import os
import core.config as config
from dotenv import load_dotenv
from openai import OpenAI
from core.cache import get_cached_embedding, set_cached_embedding
from billing.token_tracker import record_usage  # 新增导入

load_dotenv()

#   改前是模块级 `OpenAI(api_key=DASHSCOPE_API_KEY, ...)` —— 缺 key（`os.getenv` 无默认 ⇒ `None`）
#   时在 **import 期**就抛 SDK 那句通用 `OpenAIError`，**炸掉整条 import 链**
#   （`rag_pipeline` / `api_v1` / `hybrid_search` 全都起不来），
#   而那句话提的是 `OPENAI_API_KEY` —— 本仓根本不用那个名字。
_client = None


def _get_client() -> OpenAI:
    """惰性单例。缺 key ⇒ 抛**点名 `DASHSCOPE_API_KEY`** 的错（与 `config.validate_config` 同族）。

    `os.getenv(...) or config.…` 这个写法与 `llm_factory._resolve` **同源**：
    先看 env（调用时读得到，测试改得动），回落 `config`（默认值只有一处）。
    """
    global _client
    if _client is None:
        api_key = os.getenv("DASHSCOPE_API_KEY") or config.DASHSCOPE_API_KEY
        if not api_key:
            raise EnvironmentError(
                "DASHSCOPE_API_KEY 未设置 —— embedding 固定走阿里云百炼，缺它取不到向量。"
                "检查 .env 或环境变量（见 docs/契约/环境变量.md §4）。"
            )
        _client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return _client


def get_embedding(
    text: str,
    model: str | None = None,
    *,
    user_name: str = "system",
    thread_id: str = "system",
) -> list:
    """带缓存的Embedding调用。

    （§二·2）：`model` 的**默认值不再写死** —— 走
        `EMBEDDING_MODEL_MAIN`（先 env 后 `config`，与 `_get_client` 取 key **同一种写法**）。
        **别把默认值改回字面量** —— 换模型要改代码，而那正是本次要消掉的。
    **形参 `model` 必须保留**：`test_isolation.py` 有 4 处按 `lambda text, model=None:` 打桩。

    **新增 `user_name`（关键字限定）—— 修一个真缺陷**。

    **改前**：这里**写死** `user_name="system"`（原注释还自认「可后续改为动态获取当前用户」）
    ⇒ **全仓每一次 embedding 都记在一个叫 `system` 的假用户名下**
    ⇒ **任何真实用户的「Embedding 用量」恒为 0**（实测：库里 `system/embedding` 一路，
    而各真实用户那栏只有 `answer_generation`）。
    **它把「两个额度池」里的 Embedding 池整个废掉** —— 那个池判的是「额度 − 已用」，
    而"已用"恒 0 ⇒ **永远不会 429**（那次照出来的）。

    **默认值仍是 `"system"`，不要改它** —— 有**三条路本来就没有用户**，
    "system" 对它们是**对的**答案：① `core/cache.warmup_cache`（**启动时**预热热点查询）
    ② `assert_model_dimension`（启动自检）③ 本文件的 `__main__`。
    ⇒ **判据是"调用方有没有用户"，不是"默认值改了没"**：谁手里有用户，谁就得传。

    **`thread_id` 同理 —— 【已经接了】**：
        有会话的调用点（`rag_pipeline` / `hybrid_search` / `api_v1_rag`）**都得传**；
        不传就落进 `"system"`，而**会话闸查的是调用者传的那个桶**
        **闸恒看到 0**（实测：`get_session_token_usage(user, 调用者的 thread)` = 0）
        ⇒ 那条路上的会话上限**永远不会触发**。
    **默认值仍留 `"system"`，理由与 `user_name` 逐字同款** —— 那三条路
        （启动预热 / 维度自检 / `__main__`）**本来就没有用户、也没有会话**，
        把它们算到某个会话头上同样是记错账。
    ⚠️ **有结构守卫钉着** ⇒ `app/tests/test_embedding_attribution.py`：
        活代码里每个 `get_embedding(` 都得带 `user_name=` **和** `thread_id=`，白名单要写理由。
    """
    if model is None:
        model = os.getenv("EMBEDDING_MODEL_MAIN") or config.EMBEDDING_MODEL_MAIN
    # 0. 先把客户端取到手—— 缺 key 在这里就**响亮地断**，
    #    不要等拼到 API 调用那一步。这比改前多管一件事：**缓存命中时也要 key**
    #    （否则「同一句有时报错、有时不报」取决于缓存状态，比一律报错更难查）。
    client = _get_client()

    # 1. 先查缓存（键带**模型名** —— 不带的时代换模型会命中旧向量且不报错， §2.1）
    cached = get_cached_embedding(text, model)
    if cached:
        return cached
    
    # 2. 缓存未命中，调用API
    text = text.replace("\n", " ")
    response = client.embeddings.create(input=[text], model=model)
    embedding = response.data[0].embedding
    
    # 3. 记录Token消耗和成本
    if hasattr(response, 'usage'):
        usage = response.usage
        # Embedding 调用没有 completion tokens，全部是 prompt tokens
        record_usage(
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=0,
            purpose="embedding",
            #    改前那一行是「Embedding 池永远拦不住」的根因。
            user_name=user_name,
            thread_id=thread_id,
        )

    # 4. 存入缓存
    set_cached_embedding(text, embedding, model)
    
    return embedding

class EmbeddingDimensionMismatch(EnvironmentError):
    """**维度对不上**（配置漂移）—— ⛔ 与「网络/模型不可用」**是两回事**。

    🔴 **为什么要单开一个类**（B 段接线时撞到的）：
       py3 里 **`EnvironmentError` 就是 `OSError`** ⇒ 启动那段若写 `except EnvironmentError: raise`，
       **会把 HTTP 连接失败一起当成"维度对不上"** ⇒ **一次网络抖动就把应用挡在门外**。
       ⇒ 单开一个子类：调用方**只对它**响亮地断，别的（网络类）只记 warning。
    ⚠️ **它仍然是 `EnvironmentError` 的子类** ⇒ 既有的那条用例
       （`pytest.raises(EnvironmentError, match="维度")`）**不受影响**。
    """


def assert_model_dimension(expected: int) -> None:
    """启动自检：**拿真模型实测一次**，与表定义对不上就断（§2.1）。

    **不是警告** —— 维度不匹配时检索**不是变慢、是变错**。
    它**要在 startup 里调**（`app/main.py` 的 `startup_event`，`validate_config` 那一族），
       **不在 import 期** —— 那会炸掉整条 import 链。
    ：**本函数在这里，接线留到 B 段** —— 接线需要一个 `expected` 的**来源**，
       而全仓现在**没有**那个来源（`app/core/db.py` 是**行内字面量** `vector(1536)`）。
       在 A 段硬接 = 在 `main.py` 里再写一份 1536（第三处）。
       ⇒ （**B 段**）立起「模型名 + 维度 + 表定义 = 三件套」之后，接线才有个真的落点。

    ✅ **· B 段到了，接线已做**（`main.py` 的 `startup_event`）：
       **`expected` 的来源 = 【表自己】** ⇒ `core.db.embedding_dim_in_db()`
       （PG 查 `pg_attribute.atttypmod`，对 `vector(N)` 就是 N）。
       ⇒ ⛔ **不抽常量、也不在 `main.py` 里写死一个数** —— 那正是当初不敢在 A 段硬接的原因。
       ⚠️ **SQLite 那条路回 `None`**（`embedding` 是 `TEXT`，没有向量类型）⇒ 调用方**跳过**，
          ⛔ **不是"当成通过"**。
    """
    vector = get_embedding("维度自检")
    actual = len(vector)
    if actual != expected:
        model = os.getenv("EMBEDDING_MODEL_MAIN") or config.EMBEDDING_MODEL_MAIN
        raise EmbeddingDimensionMismatch(
            f"embedding 维度对不上：模型 {model!r} 实测 {actual} 维，而表定义是 {expected} 维。"
            "⛔ 别把它降成警告 —— 维度不同源时检索**不报错、只是全错**（§2.1）。"
            "检查：① `.env` 的 EMBEDDING_MODEL_MAIN ② 表定义（`app/core/db.py` 的 vector(N)）。"
        )


if __name__ == "__main__":
    vec = get_embedding("人工智能正在改变世界")
    print(f"向量维度: {len(vec)}")
    print(f"前10个值: {vec[:10]}")