"""搜索工具：**真正抓取**必应中文的搜索结果页（重写 · N19）
"""
import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from tools.tool_cache import cached_tool

#    与 `execute_python` 的 5 秒**不是一回事**：那是**沙箱执行**的上限（子进程硬杀），
#       这是**网络往返**的上限。也没上限的话，一次卡住会把整条调用链挂住。
SEARCH_TIMEOUT_SECONDS = 20

# 用**必应中文**（`cn.bing.com`）—— 实测，本机网络下它是唯一直接可达的：
#   `cn.bing.com` HTTP 200 ／ `google.com`、`duckduckgo.com` **完全不通**
BING_SEARCH_URL = "https://cn.bing.com/search"

# 必应结果条目的 class。**这是逆向来的** —— 必应改版这里就会失效，
# 届时 `_parse_bing_results` 会返回空，`web_search` **如实报失败**（不会静默变瞎）。
BING_RESULT_SELECTOR = "li.b_algo"

# 带一个正常的浏览器 UA —— 裸请求容易被判定为机器人
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 交给 LLM 的条数上限（多了会冲上下文）
MAX_RESULTS = 8


def _parse_bing_results(html: str) -> list:
    """从必应结果页 HTML 解析出 `[{title, url, snippet}]`。

    **解析不到就返回空列表** —— **不猜、不编**。调用方据此**如实报失败**。
    """
    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.select(BING_RESULT_SELECTOR):
        h2 = li.find("h2")
        a = (h2.find("a") if h2 else None) or li.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        url = a.get("href", "")
        caption = li.select_one(".b_caption")
        snippet = caption.get_text(" ", strip=True) if caption else ""
        if title and url:
            out.append({"title": title, "url": url, "snippet": snippet})
    return out


def _web_search_impl(query: str) -> str:
    """抓必应中文并解析成文本（**纯逻辑** —— 不含 langchain 外壳，也不含缓存）。

    **从 `web_search` 的 `@tool` 体内【原样搬出来】** ——
       为的是能在外面包一层缓存。搬的时候**一行逻辑都没改**。
    **那段给 LLM 读的 docstring 留在 `@tool` 那一层**（`web_search`），没搬过来。
    """
    try:
        resp = httpx.get(
            BING_SEARCH_URL,
            params={"q": query},
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=SEARCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        return (
            f"搜索失败：无法访问搜索引擎（{type(e).__name__}: {e}）。\n"
            f"⛔ 这是**如实报告**：本次【没有】搜到任何东西，"
            f"也**没有**用模型知识替代 —— 请勿把这段当成搜索结果。"
        )

    items = _parse_bing_results(resp.text)
    if not items:
        return (
            "搜索失败：结果页里【解析不到任何条目】—— 可能是必应页面结构变了，或被反爬拦截。\n"
            "⛔ 这是**如实报告**：本次【没有】搜到任何东西，"
            "也**没有**用模型知识替代 —— 请勿把这段当成搜索结果。"
        )

    lines = [f"搜索「{query}」的结果（共 {len(items[:MAX_RESULTS])} 条）："]
    for i, it in enumerate(items[:MAX_RESULTS], 1):
        lines.append(f"{i}. {it['title']}")
        lines.append(f"   {it['url']}")
        if it["snippet"]:
            lines.append(f"   {it['snippet'][:300]}")
    return "\n".join(lines)


# 失败判定**只认 `搜索失败` 这一个前缀** —— `_web_search_impl` 里有 **2 处**返回它，
#    **改前缀就改这里**。别写"含『失败』就跳过"那种猜法（会误伤正常搜索结果）。
# 「搜不到」是**返回值**、**不是异常** ⇒ **只能靠 `should_cache` 挡**，
#    否则一次网络抖动会被缓存 300 秒（那 5 分钟里谁都搜不到东西）。
_cacheable_search = cached_tool(
    name="web_search",
    should_cache=lambda r: not str(r).startswith("搜索失败"),
)(_web_search_impl)


@tool
def web_search(query: str) -> str:
    """搜索互联网上的实时信息（必应中文）。

    适用于需要获取实时信息、新闻、资料等场景。输入是搜索关键词或问题。

    返回的是**网页搜索结果的标题 / 链接 / 摘要**（**不是**直接答案）——
    请据此再判断、或再抓取具体页面。

    **如果返回以「搜索失败」开头**，说明这次**没搜到**（网络不通 / 页面结构变了 / 被反爬拦住）。
    那种情况下请**如实告诉用户"暂时搜不到"**，
    **不要拿你自己的知识假装成搜索结果** —— 那会让人以为这是查到的。
    """
    # 上面这段 docstring 是**给 LLM 读的工具描述**，故留在本层（别搬进 impl）。
    return _cacheable_search(query)
