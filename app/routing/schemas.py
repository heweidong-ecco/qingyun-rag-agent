"""
Pydantic 数据模型集中定义
所有请求体、响应体、路径参数、查询参数的模型在此管理。
"""
from typing import List
from pydantic import BaseModel, Field
# ==================== Pydantic模型 ====================
# ==================== 检索相关 ====================
class QuestionRequest(BaseModel):
    """RAG 检索请求，包含用户问题、是否生成答案等参数，以及当前输入的这条的对话历史用于真停止按钮的调用使它（支持历史补偿）。

    **删掉了 `mode` 字段。**
    它是个**死字段** —— 收下的值**没有任何人读**：`/rag/search` 用的 `mode` 是
    **query 参数**（`SearchMode = Literal[...]`，`:583`），body 里那个**被静默忽略**。
    ⇒ 后果是：调用方传 `{"mode":"full"}`、以为跑的是 full，**实跑的是 query 参数的默认值**
      （`accurate_norerank`）—— 而**页面上看不出来**。
    改前核过两条：① 全仓**无人构造** `QuestionRequest(mode=…)`；
      ② 该模型**没** forbid 额外字段 ⇒ **删它是安全的**（老调用方多传一个键，照样被忽略）。
    契约里那条对不上的默认值（`docs/契约/接口契约.md`）**同批改掉了**。
    """
    question: str = Field(..., description="用户问题")
    top_k: int = Field(3, ge=1, le=20)
    generate_answer: bool = Field(False, description="是否直接生成答案")
    strict_mode: bool = Field(False, description=(
        "严格模式。开了：模型只照资料答，资料里没有就说「无法回答」（不许拿自己的常识补）；"
        "关着：允许它补。"
        "⚠️ 只在 generate_answer=True【且】citations=False 时才起作用 —— "
        "citations=True 走的是另一条实现（带引用的生成），那条路径根本不读它。"
    ))
    citations: bool = Field(False, description="是否在答案中标注引用来源")
    conversation_history: list[dict] = Field(
        default=[],
        description="之前的对话历史，格式为 [{'role': 'user'/'assistant', 'content': '...'}]"
    )


class RagSearchResponse(BaseModel):
    """`POST /api/v1/rag/search` 的**响应形状**（**真调一次**量出来的，不是照文档抄）。

    **这是本仓第一个 `response_model`** —— 它**真会裁字段**（不是"只改文档"）：
       实测把返回声明成只含 `a` 的模型 ⇒ 实返 `{'a': 1}`，多出来的键**被静默吃掉**。
       ⇒ 所以这里**只列真返回里有的**，一个不多。

    **`answer` 与 `sources` 是【可选】的 —— 这条是拿 500 换来的**：
       第一版把它们声明成**必填**，然后 `generate_answer=False` 真调一次
       **`ResponseValidationError: 2 validation errors: missing 'answer' / missing 'sources'`**
       ⇒ **整个端点 500**。
       根因：这个返回是**条件性**的 —— `generate_answer=False` 时管线**根本不产出**这两个键
       （「不要成文就只回检索结果」）。
       ⇒ 声明成可选**带默认值**之后，形状反而**稳定**了（缺的时候补 `null` / `[]`），
         前端不用再猜"这次有没有这两个键"。
       **代价（已知并接受）**：`generate_answer=False` 的返回会**多出**
         `answer: null` 与 `sources: []` 两个键（原先没有）。这是**纯新增**，没删任何东西。

    **顶层钉死、嵌套留 `dict`/`list[dict]`** —— 这是**有意**的：
       顶层是前端真正消费的那一层（少一个键页面就空一格）；
       嵌套那一层（`pipeline` / `timing` / `docs[]` / `sources[]`）若也钉死，
       **上游哪天多给一个键就会被这里静默吃掉**，而那正是本仓最反对的"静默"。
       ⇒ 要钉嵌套，**先给它配一条"字段集合"的门**，别只靠 Pydantic 的默认行为。

    判据（可打印）：`grep -rn "response_model=" app/routing/ | wc -l` ⇒ **不为 0**。
    """
    query: str
    pipeline: dict
    timing: dict
    docs: list[dict]
    # 这两条**只在 `generate_answer=true` 时才真的有** —— 见上面那段。
    answer: str | None = None
    sources: list[dict] = Field(default_factory=list)
    requested_by: str
    mode: str


# ====================：把 `PENDING` 那三条欠账补上====================


class ChatHistoryResponse(BaseModel):
    """`GET /api/v1/rag/chat_history` 的响应形状（真调量出来的 5 个键）。

    `messages` 是**最近 5 轮**（`window` 那句说明它）—— 页面别把"更早的没回来"读成"丢了"。
    """
    thread_id: str
    messages: list[dict] = Field(default_factory=list)
    count: int
    window: str
    requested_by: str


class CostRecordItem(BaseModel):
    """`/agent/cost/records` 里 `records[]` 的**一条**（真调量出来的 10 个键）。

    **全都可选** —— 这一条的键在**失败分支**里一个都不会出现（见下一个模型）。
    """
    user_name: str | None = None
    thread_id: str | None = None
    model: str | None = None
    purpose: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    total_cost: float | None = None
    tool_name: str | None = None
    created_at: str | None = None


class CostRecordsResponse(BaseModel):
    """`GET /api/v1/agent/cost/records` 的响应形状（真调量出来的 7 个键）。

    **所有字段都【可选】** —— 这条端点**有一个 `{"error": …}` 的失败分支**
    （查库异常时直接 `return {"error": …}`）。声明成必填 ⇒ 那条分支会被 Pydantic 判不合法
    ⇒ **500**，而不是它本来要回的那句人话。
    **这就是 `RagSearchResponse` 当初那个坑的同款**（`generate_answer=false` 时它不产出
    `answer`/`sources` ⇒ 声明成必填 ⇒ 整条端点 500）⇒ **声明之前先想"它有没有别的出口"**。
    """
    records: list[CostRecordItem] = Field(default_factory=list)
    count: int | None = None
    truncated: bool | None = None
    has_more: bool | None = None
    offset: int | None = None
    limit: int | None = None
    requested_by: str | None = None
    error: str | None = None


class TokenBudgetResponse(BaseModel):
    """`GET /api/v1/agent/token/budget` 的响应形状（真调量出来的 15 个键）。

    **`*_daily_limit` 写成 `float | str`**：额度可以是**字符串「无限」**
    （本仓 `/debug/quota` 那一族确实会回中文串）。写死 `float` ⇒ 那种角色一访问就 **500**。
    这不是"类型不严"，是**把已知的一个出口留在声明里**。
    """
    user_name: str
    daily_budget: float | str
    used_today: float | str
    remaining: float | str
    llm_tokens_today: float
    embedding_tokens_today: float
    other_tokens_today: float
    llm_daily_limit: float | str
    llm_remaining_today: float
    embedding_daily_limit: float | str
    embedding_remaining_today: float
    global_daily_limit: float | str
    global_used_today: float
    global_remaining: float
    requested_by: str


# ==================== 文档管理相关 ====================
class DocumentInsert(BaseModel):
    """单条文档插入请求"""
    content: str = Field(
        ...,
        description="要入库的文档文本内容",
        example="Python是一种广泛使用的编程语言，常用于数据分析和AI开发。"
    )
    source: str = Field(
        "manual",
        description="文档来源标识，用于分类筛选。例如 tech_docs, life_docs, culture_docs",
        example="tech_docs"
    )

class BatchDocumentInsert(BaseModel):
    """批量文档插入请求"""
    documents: List[DocumentInsert] = Field(
        description="要批量插入的文档列表",
        examples=[
            [
                {"content": "Python是一种广泛使用的编程语言，常用于数据分析和AI开发。", "source": "tech_docs"},
                {"content": "今天中午吃什么呢？这是一个让人纠结的哲学问题。", "source": "life_docs"},
                {"content": "FastAPI是一个高性能的Python Web框架，支持异步处理。", "source": "tech_docs"},
            ]
        ]
    )

    # 也可以在模型级别添加多个完整示例（会覆盖字段级示例）
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "documents": [
                        {"content": "Python是一种广泛使用的编程语言，常用于数据分析和AI开发。", "source": "tech_docs"},
                        {"content": "今天中午吃什么呢？这是一个让人纠结的哲学问题。", "source": "life_docs"},
                        {"content": "FastAPI是一个高性能的Python Web框架，支持异步处理。", "source": "tech_docs"},
                    ]
                }
            ]
        }
    }
# ==================== 认证与用户管理相关 ====================
#  access_token 和 refresh_token 登录和刷新接口
class LoginRequest(BaseModel):
    """JWT 登录请求"""
    user_name: str = Field(..., description="用户名", example="admin")
    password: str = Field(..., description="密码（取值见环境变量 LOGIN_PASSWORD）",
                          example="<your-login-password>")

class RefreshRequest(BaseModel):
    """JWT Token 刷新请求"""
    refresh_token: str = Field(
        description="用于获取新访问令牌的刷新令牌,之前登录获取的 refresh_token",
        examples=["<your-refresh-token>"]  # 别写成真 JWT 形状:凭据门的 JWT 模式会命中占位符
    )
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "refresh_token": "<your-refresh-token>"
                }
            ]
        }
    }

#    在这之前它们的 200 schema 是**空的 `{}`** ⇒ `test_response_model_declared.py` 那条门
#    一旦扫到「前端会打这两个端点」就会红。⚠️ 门没错 —— **空 schema 就是没承诺返回什么**。
class TokenResponse(BaseModel):
    """`POST /auth/login` 的返回（登录成功才有）。

    ⚠️ **`response_model` 真会裁字段** —— 这几个键是照着 `api_v1.py` 里那个 `return` 逐字核过的，
        ⛔ 别凭印象加/减（多写的键永远回不来，少写的键会被**静默吃掉**）。
    """
    access_token: str = Field(..., description="短期令牌，默认 15 分钟")
    refresh_token: str = Field(..., description="长期令牌，默认 30 天，用来换新的 access_token")
    token_type: str = Field(..., description="固定为 `bearer`（`Authorization: Bearer <token>`）")
    expires_in: int = Field(..., description="`access_token` 的有效秒数")


class AccessTokenResponse(BaseModel):
    """`POST /auth/refresh` 的返回。

    ⚠️ **它【不回 `refresh_token`】** —— 用手里那把旧的继续换（本仓实现如此）。
    """
    access_token: str = Field(..., description="新的短期令牌")
    token_type: str = Field(..., description="固定为 `bearer`")
    expires_in: int = Field(..., description="`access_token` 的有效秒数")


# 管理接口（**强制管理员**）：
#    实际 `app/routing/api_v1.py:156` 用的是 `Depends(require_admin)`，非管理员直接 403。
class UserCreate(BaseModel):
    """管理员创建用户请求"""
    user_name: str = Field(
        ...,
        description="用户名，用于标识调用者，创建后不可修改",
        example="test_user",
        min_length=3,
        max_length=50
    )
    expire_days: int = Field(
        30,
        description="API Key 的有效天数，从创建时刻开始计算",
        ge=1,
        le=365,
        example=90
    )
    #    **默认 `None`（不传）= 写 `NULL`** ⇒ 读侧走**回退**（探针身份 + admin + 其余 FREE）。
    #    **别给它默认值 `"free"`** —— "**没写**"与"**写了 free**"不是一回事，
    #    而 B1 整套设计正是靠这条区分活着（见 `app/access/permission.py` 的 docstring）。
    role: str | None = Field(
        None,
        description="角色（free / premium / admin）。**不传 = 不裁决**，按用户名回退到内置规则",
        example="premium",
    )


# ==================== 列出文档 / 取整篇====================
# 这一组补的是：**全仓此前没有任何"列出文档"的端点**。
#    作者 定的目的（逐字）：「让大部分不想自己提交文档的访客，
#    能知道已有哪些是已有的，是可以直接搜索，我该搜索什么，会出现内容」—— 即**可发现性**。


class DocListItem(BaseModel):
    """列表里**一行 = 一篇**（按 `source` 聚过的，不是一条 chunk 一行）。"""
    source: str
    category: str        # 从文件名前缀派生（A–E）—— 表里**没有目录**这一层
    chunks: int
    first_id: int        # 点开看全文时拿它去取（取的是**整篇**，见下面的 docstring）
    preview: str


class DocListResponse(BaseModel):
    scope: str           # `mine` | `public`
    total: int
    groups: list[dict]
    truncated: bool      # 收了 `limit` 就必须回这两个（有专门的门钉着）
    has_more: bool


class DocFullResponse(BaseModel):
    source: str
    category: str
    chunks: int
    content: str         # 该篇的**全部 chunk 按 id 序拼起来**（走 PostgreSQL，不经向量库）
    requested_by: str


class DocDeleteResponse(BaseModel):
    """`DELETE /api/v1/rag/documents/{doc_id}` 的返回（补）。

    ⚠️ **`id` 是前端真在用的字段** —— `app/static/web/kb.html` 的「撤销」那格
    拿它回显「✅ 删掉了（id=…）」。⇒ 它**必须在模型里**：
    `response_model` **真会裁字段**（`schemas.py` 上面那条纪律），
    漏了就是把那个回显**静默**变成 `undefined`，而**没有任何门会红**。

    字段与 `api_v1_rag.delete_document` 的 `return` **逐字对齐**（三个键）。
    """
    status: str
    id: int
    requested_by: str


class DocInsertResponse(BaseModel):
    """`POST /api/v1/rag/insert` 的返回（真调一次量出来的）。

    `content` 是**截断过的前 100 字**（回执用），**不是全文** —— 别拿它当原文。
    """
    status: str
    id: int
    content: str
    source: str
    requested_by: str


class BatchInsertResponse(BaseModel):
    """`POST /api/v1/rag/insert_batch` 的返回（真调核的）。"""
    status: str
    count: int
    requested_by: str


class UploadDocResponse(BaseModel):
    """`POST /api/v1/rag/upload_document` 的返回（真调核的）。

    `preview` 是**清洗后正文的前 500 字**（不是全文）——
    `kb` 页的「入库完当场验一次」**就是拿它当问题去检索的**。
    """
    filename: str
    format: str
    domain: str
    chunks_inserted: int
    text_length: int
    preview: str
    requested_by: str


# ==================== Agent 线（「外部检索与工具Agent编排」那一页打的那些端点）====================


class AgentChatResponse(BaseModel):
    """`POST /api/v1/agent/langgraph_chat`（本页**主链**）的返回。

    **两态共用一条模型**：
      · `status="answered"`    ⇒ `answer` 有值，`pending_tool_calls` 缺省空
      · `status="pending_approval"` ⇒ **工具还没执行**，`answer` 是模型已写出的那半句
        （可能为空串），`pending_tool_calls` 是**等人批的那几个**。

    两态**都必须带上**（声明成必填会 500，见本段上面的口径 2）——
       它们是同一个端点的两种真实返回，不是"多给的前端用不上"。
    """
    question: str
    thread_id: str
    requested_by: str
    status: str | None = None
    answer: str | None = None
    pending_tool_calls: list[dict] | None = None


class AgentMcpChatResponse(BaseModel):
    """`POST /api/v1/agent/mcp_chat`（链列表第 2 条）的返回。

    它**没有 `status`** —— 那条链**不会停在审批点**（实测：
       一次问句连着发了 5 次联网检索，`/agent/pending` 计数 **0**）。
       ⇒ 页面据此把「停下来等人批」那一块**整块去掉**（不摆一个永远空的壳）。
    """
    question: str
    answer: str
    thread_id: str
    requested_by: str
    #: 预算提醒 —— 没警告时是 `null`（真调核过）。
    budget_warning: str | None = None


class AgentPlanExecuteResponse(BaseModel):
    """`POST /api/v1/agent/plan_execute`（链列表第 3 条）的返回。

    **它与另两条【形状完全不同】**：没有 `answer`，也没有 `thread_id` ——
       它给的是**一张计划表**（`plan[]`：第几步 / 干什么 / 用哪个工具 / 输入）+ 执行结果。
       ⇒ 页面按它**换掉右边那两块**（这是"每个选择对应的面板不同"的第一个实例）。

    参数名也**不同**：它收 `goal`，不是 `question`（真调撞出来的：
       传 `question` 会 422 `Field required: goal`）。
    **它不写轨迹**（`start_trace` 只有 `mcp_chat` / `mcp_chat/stream` / `rag/stream_search` 调）
       ⇒ 页面在这一条上**不摆「执行明细」**。
    """
    goal: str
    plan: list[dict]
    execution_result: str
    requested_by: str


class AgentTraceResponse(BaseModel):
    """`GET /api/v1/agent/trace/{thread_id}`（**追踪轴** · 进程内存）的返回。

    **两支**：找得到 ⇒ `trace` + `requested_by`；找不到 ⇒ **只有 `error`**
       （`api_v1_agent.py` 那两行 return）⇒ `requested_by` 必须可选。
    **它和 `/trace/{id}/cost` 是两条轴**（这一条是内存里的每一步、那一条是 PG 里的每笔钱），
       **没有共同的步 id ⇒ 对不上**，页面上并排画、不合并。
    """
    trace: dict | None = None
    error: str | None = None
    requested_by: str | None = None


class AgentTraceCostResponse(BaseModel):
    """`GET /api/v1/agent/trace/{thread_id}/cost`（**成本轴** · PG `token_usage_logs`）的返回。

    **页面上的「本次花费」只认这一条** —— 因为 `/trace/{id}` 的
       `total_tokens` / `total_cost` **恒为 0**（`finish_trace` 的调用点都没传它们，
       实测：`total_tokens=0 total_cost=0.0` 而这一条同一次调用给的是真数）。
       拿那个 0 当"没花钱"就是印了一个**假数**（本仓硬约束②）。
    一行都没有 ⇒ **200 + 空 `items`**（不是 404）。
    """
    items: list[dict]
    total: dict
    truncated: bool
    thread_id: str
    requested_by: str


class AgentPendingResponse(BaseModel):
    """`GET /api/v1/agent/pending`（待接管列表）的返回。

    **它是进程内存** ⇒ 服务重启就空 —— 页面必须**如实写这句**（不说成"持久队列"）。
    可见性：**本人默认 · admin 全量**。
    """
    count: int
    items: list[dict]
    requested_by: str


class AgentPendingContextResponse(BaseModel):
    """`GET /api/v1/agent/pending/context`（待接管会话的**完整上下文**）的返回。

    **两支**：`status="ok"` ⇒ 带上 `owner`/`graph`/`rounds`/`next`/`messages`；
      否则 ⇒ `{"status": "error", "message": …}`（四条 error 出口）。
      ⇒ **除 `status` 之外全部可选**。
    `messages[]` 是 `_serialize_messages` 的产物：`{content, type}` 两个键
       （`content` **可能是 list** —— 多模态 parts，前端要归一）。
    """
    status: str
    message: str | None = None
    owner: str | None = None
    graph: str | None = None
    rounds: int | None = None
    #: 图下一步要去哪个节点（`list(state.next)`）—— 停在审批点时是 `["approval"]`。
    next: list | None = None
    messages: list[dict] | None = None
    requested_by: str | None = None


class AgentApproveResponse(BaseModel):
    """`POST /api/v1/agent/approve`（批 / 拒 / 代填）的返回 —— **它有 5 种形状**。

    | `status` | 什么时候 | 额外的键 |
    |---|---|---|
    | `approved` / `rejected` | 图**真的走完了** | `answer` |
    | `pending_approval` | 批了，但它**又**停在审批点（第三态） | `answer` + `pending_tool_calls` + `rounds` |
    | `approved` / `rejected` + `forced_finish` | 撞上轮次上限、被**强制收口** | `forced_finish` + `rounds` |
    | `error` | 队列里没有 / 有多条撞号 / 无权审批 / 图名不认识 / 触顶仍拦不住 | `message`（+ `rounds`） |

    ⇒ **全部声明成可选**（口径 2：声明成必填必 500）—— 这是本仓**返回形状最多变**的一条。
    参数全在 **query**（`thread_id` / `approved` / `edited_answer` / `owner`）：
       **代填** = `approved=true` **且** `edited_answer=<串>`（拒绝时给 `edited_answer` 会被忽略）。
    """
    status: str
    thread_id: str | None = None
    answer: str | None = None
    message: str | None = None
    requested_by: str | None = None
    pending_tool_calls: list[dict] | None = None
    forced_finish: bool | None = None
    rounds: int | None = None


class AgentApprovalHistoryResponse(BaseModel):
    """`GET /api/v1/agent/approvals/history`（裁决历史）的返回。

    `events[]`：`{owner, actor, decision, edited, rounds, reason}`
       —— `reason` 是那一次的工具名（如 `web_search×2`）。
    可见性镜像 `/traces`：**本人默认 · admin 全量**（admin 会看到别人的 owner）。
    """
    events: list[dict]
    count: int
    has_more: bool
    limit: int
    offset: int
    requested_by: str


class AgentToolsResponse(BaseModel):
    """`GET /api/v1/agent/available_tools`（工具清单）的返回。

    `healthy_tools` 里每条是 `{name, description, version}` ——
       **页面上的工具表从它取，不写死那 7 行**（写死会漂）。
    **没体检过的工具也算 healthy**（判据是 `health == UNHEALTHY` 才进另一栏）——
       ⇒ 可能出现"没人验过也说它健康"，页面上别把它读成"验过了"。
    """
    healthy_tools: list[dict]
    unhealthy_tools: list[str]
    total: int
    requested_by: str


class AgentInterceptsResponse(BaseModel):
    """`GET /api/v1/agent/budget/intercepts`（我这几轮被挡过几次）的返回。

    **本用户 · 进程内存计数器**（重启归零）—— 页面要写明这一点，
       别写成"今天被挡了 N 次"那种听起来像持久统计的话。
    """
    user_name: str
    total_intercepts: int
    requested_by: str


class AgentMemorySearchResponse(BaseModel):
    """`GET /api/v1/agent/memory/search` 的返回（是 **GET**，且参数名就叫 `query`）。

    空库 ⇒ `memories: []`（实测：没写过的时候就是这样）。
    """
    query: str
    memories: list[str]
    memory_space: str


class AgentMemoryAddResponse(BaseModel):
    """`POST /api/v1/agent/memory/add`（显式的「记住这句」）的返回。

    **它会花钱**：mem0 内部要跑一次 LLM **抽事实** —— 实测一笔 **7992** 个 LLM token
       （`purpose="memory_extract"`，见  的）。
       那笔账把 `thread_id` 写死成 `"system"` ⇒ 按 `thread_id` 筛成本时**会漏掉它**。
    它**没有 `requested_by`**（真调核过）—— 别照别的模型顺手补一个。
    """
    status: str
    content: str
    memory_space: str


# ==================== 「系统观测」页（`/ops`）那一批 ====================
#  —— 这一页接线时打了 6 条端点，它们**当场进了那道门的
#    `called` 集合**（`app/tests/test_response_model_declared.py`）⇒ 必须当场声明。


class DebugCacheStatsResponse(BaseModel):
    """`GET /api/v1/debug/cache_stats` 的返回（真调核过）。

    **全站口径**（全站的缓存键数）· 不是"你的"。
    **`cached_embeddings_count == 0` 不等于缓存坏了** —— 冷启动就是 0。
    """
    cached_embeddings_count: int
    #: 前几个键名（**只给几个**，不是全量 —— 所以它不能当"缓存里有啥"用）。
    sample_keys: list[str]


class DebugCountResponse(BaseModel):
    """`GET /api/v1/debug/count` 的返回。**全站口径**（全站文档数）。"""
    total_documents: int


class DebugQuotaResponse(BaseModel):
    """`GET /api/v1/debug/quota` 的返回（**本人口径** · token 单位）。

    **单位是 token，不是"次"**（把口径统一到 token 一套了，
    而字段名 `daily_limit` / `remaining` 保留没改 ⇒ 名字会让人误读成次数）。

    **`daily_limit` / `remaining` 允许字符串** —— 预算为 `inf` 时后端回**「无限」**
    （`token_tracker.get_token_budget_info`）⇒ 声明成 `int` 的话**一调就 500**。

    还有一层它表达不了的：`get_daily_token_usage` 是 **fail-open** ——
    查库失败会**回 0** ⇒ `remaining` 变成满额度。**响应里分辨不出**"没花过"与"查挂了"。
    """
    user_name: str
    role: str
    daily_limit: int | float | str
    remaining: int | float | str
    used_today: int | float


class DebugRateLimitResponse(BaseModel):
    """`GET /api/v1/debug/rate_limit` 的返回（**本人口径** · 令牌桶）。

    `capacity` / `rate` 是**代码常量**（`billing/token_config.py`）—— 不是实时值。
    桶在 **Redis** 里；`get_remaining` 在 **Redis 不通时报满桶**（有意 fail-open）
    ⇒ 页面不能只看 `remaining_tokens`：那会显示"满桶=正常"，而**限流其实已失效**。
    """
    user_name: str
    remaining_tokens: int | float
    capacity: int | float
    rate: int | float


class AgentToolHealthResponse(BaseModel):
    """`GET /api/v1/agent/tool_health` 的返回。

    三件必须写进页面的：① 服务刚起时 `tools` 是**空 `{}`**（**空 ≠ 全好**）；
    ② 只有**登记过**的工具才体检（没登记的静默跳过）；③ 探活**会真打工具**（含联网）。
    """
    tools: dict[str, dict]
    requested_by: str


class AgentToolVersionsResponse(BaseModel):
    """`GET /api/v1/agent/tool_versions` 的返回。"""
    tool_versions: dict[str, str]
    requested_by: str
