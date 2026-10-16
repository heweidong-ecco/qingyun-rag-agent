"""
RAGAS 自动化评估脚本（基于 FastAPI 接口），含 context_precision）
"""
import json
import time
import httpx
import os
from dotenv import load_dotenv
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    Faithfulness,       # ← 2026-09-21 加：本脚本要用【修正过的子类】替掉它（见下方 ZhFaithfulness）
    answer_relevancy,
    context_recall,
    context_precision  # 新增：上下文精确率
)
from langchain_openai import OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

# **必须在 `load_dotenv` 之后导入**：`token_config` 是在 **import 时**读 env 的
#    （见其模块 docstring「不做热加载」）。放在 `load_dotenv()` 之前 ⇒ `.env` 里的
#    `TOKEN_MAX_*` 会被**静默忽略**、用了代码默认值。本仓目前两者相等（没有这条 env），
#    所以现在**看不出来** —— 正因为看不出来才要写死在正确的一侧。
#
#    2026-10-02：原来的 `from token_config import MAX_TOKENS_ANSWER` 换成
#    `from llm_factory import make_llm`。**次序约束仍在，但落点变了** ——
#    现在它管的是**调用** `make_llm(...)` 的时机（`llm_factory` 自己**不**在 import 时读
#    `token_config`，是 `make_llm()` **在函数内**才 import 它）。下面的 `eval_llm`
#    在 `load_dotenv()` **之后** ⇒ **同一条保证照旧成立**。
from core.llm_factory import make_llm   # noqa: E402

# ==================== 配置 ====================
# 凭据一律从环境变量读（`load_dotenv` 已在上方调用，会向上找到仓根 `.env`）。
#    此前这里是硬编码的默认口令，且该字面量在 `.secret-denylist` 黑名单里、本仓是 PUBLIC，
#    故入库时改为读环境变量，不设可用默认值。
#    （规则见本仓「凭据门」：报告命中时只写名字、不写字面量 —— 本行即遵守该规则。）
EVAL_DATASET_PATH = "eval_dataset.json"
API_BASE_URL = os.getenv("EVAL_API_BASE_URL", "http://localhost:8000/api/v1")
API_USERNAME = os.getenv("LOGIN_USER_NAME", "admin")
API_PASSWORD = os.getenv("LOGIN_PASSWORD", "")
TOP_K = 3

# 用于生成答案和 RAGAS 评估的 LLM
# 2026-09-21 改（需求方裁 · ）：**judge LLM 从【硬编码 DashScope】改为【读 `.env`】。**
#
#    **为什么必须改**：DashScope 的 **chat 免费额度已耗尽（403）**
#    这个脚本**装好包也跑不动**。而更别扭的是 —— 它**硬编码走 DashScope**，
#    **绕开了项目早就切过去的 DeepSeek**（本仓 `.env` 的 `LLM_*` 三项就是 DeepSeek）。
#
#    顺带修掉一个隐患：原写法把 `qwen-plus` 和 DashScope 的 base_url **写死在脚本里**，
#       ⇒ 项目换模型时**这个脚本不会跟着换**，而它看起来"还在正常工作"。
#
#    2026-10-02**又修掉一个**：本处的 `os.getenv("LLM_MODEL_CHAT", "deepseek-chat")`
#       **兜底值和 `config.py:55` 的 `qwen-plus` 不一致** —— 一旦 env 缺失，脚本和应用会
#       静默用上**两个不同的模型**。现在两者都走 `make_llm()` ⇒ **同一个默认值、同一个来源**。
eval_llm = make_llm(
    # 角色 = 「模型轴 chat」+「长度轴 answer(2000)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
    #    为什么是 answer 档：本脚本 :144 生成"被评的答案"（`answer_chain`）、:289 又当 RAGAS judge
    #       —— **两处都是长输出**。给 1024 的话 judge 输出可能**被截断 ⇒ 评分静默失真**，
    #       那比没有上限更坏（错数据看不出来）。
    "chat", "answer",
)

# **embedding 保持 DashScope**（需求方 2026-09-21 明确）——
#    chat 额度耗尽了，但 **embedding 那边仍可用**（`EMBEDDING_MODEL_MAIN`，2026-10-14 起 1024 维）。
#    与 `app/core/config.py` 的 embedding 配置**同源**（同一个模型名 / base_url），
#       差别只在这里显式取 `DASHSCOPE_API_KEY`（本脚本独立于应用运行）。
# 2026-10-11：**模型名也改成同源了** —— 见下面 `OpenAIEmbeddings(model=...)` 那几行注释。
#
# 2026-09-21 加 `check_embedding_ctx_length=False` —— **必须加，否则必炸**。
#    实测）：
#      `True`（**默认值**）→ 400 `InvalidParameter: Value error,
#                            contents is neither str nor list of str.: input.contents`
#      `False`            → 维度 1024（2026-10-14 B 段前为 1536）
#    原因：`OpenAIEmbeddings` 默认**先把文本 tokenize 成 token-id 数组**再发出去
#    （`input` 是 list[int]）。**OpenAI 官方接口接受这种形式，DashScope 的不接受。**
#    连 `embed_query("一句普通中文")` 都会挂 —— 与问题长短、中英文**都无关**。
eval_embeddings = OpenAIEmbeddings(
    # 2026-10-11：改前这里**写死** `"text-embedding-v2"` —— 施工单只记了两个站点，
    #    实测**这是第三处**（`grep -rn 'text-embedding-v2' app/ --include='*.py' | grep -v /tests/`）。
    #    需求方的要求是「**全局**的 embedding 模型要全部改成从 .env 加载」⇒ 这一处也算。
    # **不改的后果是静默的**：切完模型后评测用的 embedding 与被评测的系统**不是同一个**
    #    ⇒ 分数看着正常，但量的不是那个系统。
    # 本脚本【不 import 任何本仓模块】（见 `app/eval/CLAUDE.md`）⇒ 这里**只读 env**，
    #    字面默认值与 `app/core/config.py:63` 对齐（**同一个模型名**）。
    model=os.getenv("EMBEDDING_MODEL_MAIN", "text-embedding-v2"),
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    check_embedding_ctx_length=False,
)

# ============================================================================
# RAGAS 与【中文 + DeepSeek】的三处不兼容 —— 均在 2026-09-21 实测定位并修掉
# ============================================================================
# 三处**都不是本仓 RAG 系统的错**，是评估链三方之间的不兼容。。

# ① `answer_relevancy` 的 `strictness`（默认 3）⇒ 向 judge 要 **n=3** 个补全。
#    **DeepSeek 明确拒绝**：`Invalid n value (currently only n = 1 is supported)` ⇒ 400 ⇒ nan。
#    ⇒ 收到 1。（`strictness` 是 RAGAS 官方参数，不是我们发明的开关。）
answer_relevancy.strictness = 1


# ② `faithfulness` 对**中文答案恒为 nan** —— RAGAS 自己的 bug。
#    `ragas/metrics/_faithfulness.py:216` 把"什么算一句话"写死成 **ASCII 句点**：
#        sentences = [s for s in sentences if s.strip().endswith(".")]
#    而中文答案以 `。` 结尾 ⇒ **全部被滤掉** ⇒ 提示里 0 条语句 ⇒ `No statements were
#    generated from the answer.` ⇒ `score = np.nan`（**静默 nan，连
#    `raise_exceptions=True` 都不抛**）。
#
#    决定性实验（对照）：
#        中文答案 "…Web 开发。它也是…领域。"  → 切 2 句 → 过滤后 **0 条** → nan
#        英文答案 "Python is…. It is…."     → 切 2 句 → 过滤后 **2 条** → 正常
#
#    ⇒ 只放宽"句末标点"这一条判断，**其余逻辑一个字不动**。实测 nan → **0.857**。
class ZhFaithfulness(Faithfulness):
    """`Faithfulness` 的中文修正版 —— 句末标点不只认 ASCII `.`。"""

    _SENTENCE_END = "。．.！？!?；;"

    def _create_statements_prompt(self, row):
        answer, question = row["answer"], row["question"]
        sentences = self.sentence_segmenter.segment(answer)
        # 原版是 `sentence.strip.endswith(".")` —— 中文句号一个都过不去。
        sentences = [
            s for s in sentences if s.strip() and s.strip()[-1] in self._SENTENCE_END
        ]
        sentences = "\n".join([f"{i}:{x}" for i, x in enumerate(sentences)])
        return self.statement_prompt.format(
            question=question, answer=answer, sentences=sentences
        )


zh_faithfulness = ZhFaithfulness()

# 生成最终答案的简单链
answer_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "你是一个严格基于事实的问答助手。\n\n"
     "请按照以下规则回答：\n"
     "1. 首先判断提供的上下文是否与用户问题相关。\n"
     "2. 如果上下文完全不相关，直接回答：\"根据现有资料，无法回答此问题。\"\n"
     "3. 如果上下文部分相关，只基于相关部分回答，并忽略无关内容。\n"
     "4. 不要编造任何信息，不要引入你自己的知识。\n\n"
     "上下文：\n{context}"
    ),
    ("user", "{question}")
])
answer_chain = answer_prompt | eval_llm | StrOutputParser()


def get_auth_token():
    # 快速失败：口令改从环境变量读之后，未配置时要报得清楚，
    # 而不是让 /auth/login 返回一个看不出原因的 422。
    if not API_PASSWORD:
        raise SystemExit(
            "缺少登录口令：请在仓根 .env 里设置 LOGIN_PASSWORD（本脚本不再内置默认口令）。"
        )
    resp = httpx.post(f"{API_BASE_URL}/auth/login", json={
        "user_name": API_USERNAME,
        "password": API_PASSWORD
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def call_rag_api(question: str, token: str,max_retries: int = 3) -> dict:
    """调用 /rag/search 接口，获取检索文档，并自动生成答案，带重试、并返回检索结果、生成答案以及改写后的查询（如有）"""
    last_exception = None
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                f"{API_BASE_URL}/rag/search",
                headers={"Authorization": f"Bearer {token}"},
                json={"question": question, "top_k": TOP_K, "mode": "accurate"},
                timeout=120.0  # 增加到 2 分钟
            )
            resp.raise_for_status()
            data = resp.json()
            docs = data.get("docs", [])
            # 从检索结果中提取文本上下文
            contexts = [doc["content"] for doc in docs]

            # 使用 LLM 基于检索到的上下文生成答案
            answer = answer_chain.invoke({
                "context": "\n\n".join(contexts),
                "question": question
            })
            # 提取改写后的查询（如果存在）
            pipeline_info = data.get("pipeline", {})
            rewritten_query = pipeline_info.get("rewritten_query", question)
                        # --- 关键修改：如果检索不到任何文档，直接返回拒答，不调用 LLM ---
            if not contexts:
                return {
                    "answer": "根据现有资料，无法回答此问题。",
                    "contexts": [],
                    "rewritten_query": rewritten_query
                }

            # 生成答案（只在有文档时才调用 LLM）
            answer = answer_chain.invoke({
                "context": "\n\n".join(contexts),
                "question": question
            })

            # 成功返回后，主动休息一下，避免连续冲击
            time.sleep(1.5)
            return {
                "answer": answer,
                "contexts": contexts,
                "rewritten_query": rewritten_query
            }
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError) as e:
            last_exception = e
            print(f"    ⚠️ 请求失败 (尝试 {attempt+1}/{max_retries}): {e}")
            time.sleep(2)  # 等待 2 秒后重试
        except Exception as e:
            # 其他异常直接抛出
            raise e

    # 所有重试都失败
    raise last_exception


def load_eval_dataset(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def prepare_ragas_dataset(eval_data, token):
    questions = []
    answers = []
    contexts_list = []
    ground_truths = []
    detailed_results = []  # 新增：存储每个问题的详细信息

    print(f"正在处理 {len(eval_data)} 个问题...")
    for i, item in enumerate(eval_data):
        question = item["question"]
        result = call_rag_api(question, token)
        questions.append(question)
        answers.append(result["answer"])
        contexts_list.append(result["contexts"])
        # 直接传入字符串，不再是列表
        ground_truths.append(item["answer"])

        # 保存详细结果，包含改写后的查询
        detailed_results.append({
            "question": question,
            "rewritten_query": result.get("rewritten_query", question),
            "expected_answer": item["answer"],  # 标准答案
            "actual_answer": result["answer"],  # 系统生成的答案
            "retrieved_docs": result["contexts"] # 检索到的文档列表
        })
        if (i + 1) % 10 == 0:
            print(f"  已完成 {i+1}/{len(eval_data)}")

    ragas_dataset = Dataset.from_dict({
        "question": questions,
        "answer": answers,
        "contexts": contexts_list,
        "ground_truth": ground_truths
    })
    return ragas_dataset, detailed_results  # 修改返回值

def main():
    print("=" * 60)
    print("RAGAS 自动化评估（基于 FastAPI）")
    print("=" * 60)

    # 1. 登录
    print("\n1. 登录获取 Token...")
    token = get_auth_token()
    print("   ✅ 登录成功")

    # 2. 加载数据
    print(f"\n2. 加载评估数据集: {EVAL_DATASET_PATH}")
    eval_data = load_eval_dataset(EVAL_DATASET_PATH)
    print(f"   共 {len(eval_data)} 个问答对")

    # 3. 运行 RAG 并构建数据集
    print("\n3. 通过 API 运行 RAG 并生成答案...")
    start = time.time()
    ragas_dataset, detailed_results = prepare_ragas_dataset(eval_data, token)
    elapsed = time.time() - start
    print(f"   完成，耗时 {elapsed:.1f}s")

    # 4. RAGAS 评估
    print("\n4. 运行 RAGAS 评估...")
    result = evaluate(
        ragas_dataset,
        # `zh_faithfulness`（修正过的子类），**不是** `faithfulness`（对中文恒 nan，见文件上方）。
        metrics=[zh_faithfulness, answer_relevancy, context_recall, context_precision],
        llm=eval_llm,
        embeddings=eval_embeddings,
    )

    # 5. 输出报告
    print("\n" + "=" * 60)
    print("📊 评估报告")
    print("=" * 60)
    report = {"eval_size": len(eval_data)}
    for metric in ["faithfulness", "answer_relevancy", "context_recall", "context_precision"]:
        if metric in result:
            score = round(float(result[metric]), 4)
            report[metric] = score
            print(f"  • {metric}: {score:.4f}")

    report["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open("ragas_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n✅ 报告已保存至 ragas_report.json")
    # ===== 新增：保存详细对比数据 =====
    with open("ragas_detailed_report.json", "w", encoding="utf-8") as f:
        json.dump(detailed_results, f, ensure_ascii=False, indent=2)
    print("📋 详细对比报告已保存至 ragas_detailed_report.json")
if __name__ == "__main__":
    main()