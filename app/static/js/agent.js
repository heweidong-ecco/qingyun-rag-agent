'use strict';
/* `agent` 页（**外部检索与工具Agent编排**）的**纯逻辑** —— ⛔ 不碰 DOM、不发请求。
 *
 * ## 这一页的形态（作者 2026-10-13 定）
 *
 * 🔴 **一个「链」列表 + 左右两块面板**：点行首那个键 ⇒ **左边表单和右边结果块一起换**。
 *    形态照内部RAG 页的接口切换器（`rag.js` 的 `ENDPOINTS` + `has`），
 *    ⛔ **不是**每条链一个面板全摆出来。
 *
 * ⚠️ **每条链收的参数不同、产出的东西也不同** —— 这就是 `has` 那张表的意思：
 *    左边不适用控件**收起并写明**，右边不产出的块**整块去掉**（⛔ 不留空壳装能看，
 *    `rag.html` 上那条「切到 pg_search ⇒ 阶段和 answer 整块去掉」是同一条口径）。
 *
 * ## 🔴 三条链的家底（2026-10-13 读码 + 真调核出来的，⛔ 不是照文档抄）
 *
 * | 链 | 图 | 调工具 | **停下来等人批** | 写轨迹 | 注入记忆 | 参数名 |
 * |---|---|---|---|---|---|---|
 * | `langgraph_chat`（默认） | `agent_graph` | ✅ | ✅ | ✅ | ✅ | `question` |
 * | `mcp_chat` | `mcp_agent` | ✅ | ❌ | ✅ | ✅ | `question` |
 * | `plan_execute` | 手写（非 LangGraph） | ✅ | ❌ | ❌ | ❌ | **`goal`** |
 *
 * ⚠️ **`plan_execute` 的参数叫 `goal`**，⛔ 不是 `question` —— 真调撞出来的：
 *    传 `question` 会 **422**（`{"loc":["query","goal"],"msg":"Field required"}`）。
 * ⚠️ **`mcp_chat` 不会停下来等人批** —— 实测一次问句连着发了 **5 次**联网检索、
 *    `/agent/pending` 计数 **0**（那条图的路由里根本没有审批节点）。
 *    ⇒ 页面上那一块**整块去掉**（⛔ 不摆一个永远空的"等人批"）。
 * ⚠️ **`plan_execute` 不写轨迹**（`start_trace` 只有 `mcp_chat` / `mcp_chat/stream` /
 *    `rag/stream_search` / **本页补上的 `langgraph_chat`** 在调）⇒ 它上面**⛔ 不摆「执行明细」**。
 */

(function () {

/** 本页要打的端点（⚠️ 字面量必须以 `/api/v1` 开头 —— 页面守卫会扫）。 */
const TOOLS_PATH = '/api/v1/agent/available_tools';
const COST_PATH = '/api/v1/agent/cost/records';
const BUDGET_PATH = '/api/v1/agent/token/budget';
const INTERCEPTS_PATH = '/api/v1/agent/budget/intercepts';
const PENDING_PATH = '/api/v1/agent/pending';
const PENDING_CTX_PATH = '/api/v1/agent/pending/context';
const APPROVE_PATH = '/api/v1/agent/approve';
const HISTORY_PATH = '/api/v1/agent/approvals/history';
const MEM_SEARCH_PATH = '/api/v1/agent/memory/search';
const MEM_ADD_PATH = '/api/v1/agent/memory/add';

/**
 * 🔴 **三条【流式】端点也写成顶层常量** —— 这是**故意**让它们被
 * `app/tests/test_response_model_declared.py` 的尺子②扫到。
 *
 * 理由：SSE 端点的 200 **不是 JSON 体**（一帧一帧走）⇒ `response_model` 对它**不适用**，
 * 那道门给它们留了 **`NOT_JSON`** 一栏（⛔ 不是"还没补"）。
 * ⚠️ 若这里只写成 `CHAINS` 里一个对象属性，**那道门扫不到**（它的尺子只认顶层常量）
 * ⇒ 这三条就**长期在门外**，而**没有任何东西会红**（本仓最恨的那种"漏扫 ≠ 通过"）。
 */
const STREAM_LANGGRAPH_PATH = '/api/v1/agent/langgraph_chat/stream';
const STREAM_MCP_PATH = '/api/v1/agent/mcp_chat/stream';
const STREAM_PLAN_PATH = '/api/v1/agent/plan_execute/stream';

/**
 * 🔴 **三条链**（顺序 = 页面上的顺序，第一条是**默认选中**的那条）。
 *
 * `has` 说的是**这条链产不产出那几样** —— 页面据此决定哪些块有意义：
 *   · `approval` 有人工接管（会停下来等人批）
 *   · `trace`    写轨迹（有"每一步打了谁 · 花了多久"）
 *   · `plan`     产出的是**一张计划表**（⛔ 不是一段 answer）
 *   · `memory`   注入长期记忆
 */
const CHAINS = [
  {
    id: 'langgraph',
    sub: 'LangGraph 那条 —— 会停下来等人批',
    path: '/api/v1/agent/langgraph_chat',
    stream: STREAM_LANGGRAPH_PATH,
    param: 'question',
    paramLabel: 'question',
    placeholder: '一句话，或者一个任务 —— 例如「查一下这个型号的公开报价，再算三台的毛利」',
    has: { approval: true, trace: true, plan: false, memory: true },
  },
  {
    id: 'mcp',
    sub: 'MCP 工具那条 —— ⚠️ 不会停下来等人批（敏感工具直接执行）',
    path: '/api/v1/agent/mcp_chat',
    stream: STREAM_MCP_PATH,
    param: 'question',
    paramLabel: 'question',
    placeholder: '同上 —— 例如「今天几号，30 天后呢」',
    has: { approval: false, trace: true, plan: false, memory: true },
  },
  {
    id: 'plan',
    sub: '先规划、再执行那条 —— 产出是一张计划表（⚠️ 它不写执行轨迹）',
    path: '/api/v1/agent/plan_execute',
    stream: STREAM_PLAN_PATH,
    // 🔴 参数名是 `goal`，⛔ 不是 `question`（传错会 422）。
    param: 'goal',
    paramLabel: 'goal',
    placeholder: '要办成的事 —— 例如「算三个月的用量与费用」',
    has: { approval: false, trace: false, plan: true, memory: false },
  },
];

/**
 * 链列表**那一行上写的字** —— 与内部RAG 页那一块**同形**：`POST <路径>：<一句说明>`。
 *
 * ⚠️ 写死的只有"POST"与我方路径；⛔ 别把说明也写进 HTML —— 那样改一条链要改两处。
 */
function rowTextOf(chain) {
  return "POST " + chain.path + "：" + chain.sub;
}

/** 按 id 取链；认不出就回第一条（⛔ 不抛 —— 页面不该因为一个值坏了就整页白）。 */
function chainOf(id) {
  for (let i = 0; i < CHAINS.length; i++) { if (CHAINS[i].id === id) { return CHAINS[i]; } }
  return CHAINS[0];
}

/** 建请求路径。⚠️ **参数名随链不同**（`question` / `goal`），且**全走 query**（⛔ 没有 body）。 */
function buildPath(chain, text, threadId, useStream) {
  const p = chain.path + (useStream ? '/stream' : '');
  return p + '?' + chain.param + '=' + encodeURIComponent(text)
       + '&thread_id=' + encodeURIComponent(threadId);
}

/**
 * 🔴 **轨迹那两条的整串都在这里现拼**（⚠️ 别把它抽成顶层 `const XXX_PATH = '/api/v1/agent/trace/'`）。
 *
 * 理由：`/api/v1/agent/trace/` 是**前缀**，⛔ **不是端点** —— 而
 * `app/tests/test_response_model_declared.py` 的尺子②把「顶层大写的 `const XXX = '/api/v1/…'`」
 * **一律当成"前端调用的端点"**（它没法知道那是个前缀）⇒ 写成常量会让那道门
 * 报一条**根本不存在的端点** `/api/v1/agent/trace`（这是本页接线时真发生过的）。
 * ⚠️ 而那道门**不改** —— 它的规则在承重（它挡的是"调了端点却没声明返回形状"）。
 * ⇒ 前缀保护改由 `agent.test.js` 的前缀断言兜（那份门的文档自己写着："改用 helper 拼路径的，
 *    那条门对它空过"）。
 */
function buildTracePath(threadId) {
  return '/api/v1/agent/trace/' + encodeURIComponent(threadId) + '/cost';
}

/** 建轨迹路径（追踪轴）。⚠️ 与上面那条**不是同一条** —— 那条是成本轴。 */
function buildTraceAxisPath(threadId) {
  return '/api/v1/agent/trace/' + encodeURIComponent(threadId);
}

/**
 * 🔴 **批 / 拒 / 代填**的路径（全在 query）。
 *   · **批** ⇒ `approved=true`
 *   · **拒** ⇒ `approved=false`
 *   · **代填** ⇒ `approved=true` **且** `edited_answer=<串>`
 *     （⚠️ 拒绝时给 `edited_answer` 会被**忽略** —— 后端只认 `approved=false`）
 */
function buildApprovePath(threadId, approved, editedAnswer) {
  let u = APPROVE_PATH + '?thread_id=' + encodeURIComponent(threadId)
        + '&approved=' + (approved ? 'true' : 'false');
  if (approved && editedAnswer) { u += '&edited_answer=' + encodeURIComponent(editedAnswer); }
  return u;
}

/** 取完整上下文的路径。⚠️ `thread_id` 是**必填** query。 */
function buildContextPath(threadId) {
  return PENDING_CTX_PATH + '?thread_id=' + encodeURIComponent(threadId);
}

/** 建"列出待接管"的路径。 */
function buildPendingPath() {
  return PENDING_PATH;
}

/**
 * 🔴 **语料/模型吐回来的文本自带 markdown 记号** —— 渲染前要去掉。
 *
 * ⚠️ 实测（真调）：模型答的就是 `8 × 8 = **64**` —— 页面上原样印出来就是两个星号。
 *    而本仓那条 markdown 门**只扫页面 HTML**（还剥掉 `<script>`）⇒ **扫不到数据里带进来的**。
 *
 * 🔴 **只去掉记号，⛔ 不动一个字**（本仓硬约束①：真数据，内容不许改）。
 * ⚠️ 用 `textContent` 塞进 DOM ⇒ 天然不过 HTML，不存在 XSS 面。
 */
function stripMd(s) {
  return String(s == null ? '' : s).replace(/\*\*/g, '');
}

/** 一条耗时怎么印。⚠️ `null` ⇒ `—`（⛔ 不印 0ms，那会被读成"这一段没花时间"）。 */
function fmtMs(ms) {
  if (typeof ms !== 'number' || !isFinite(ms)) { return '—'; }
  return ms >= 1000 ? (ms / 1000).toFixed(2) + ' s' : ms.toFixed(0) + ' ms';
}

/** 钱的印法。⚠️ `null` ⇒ `—`；很小的数**保 4 位**（⛔ 不许把它印成 0）。 */
function fmtCost(v) {
  if (typeof v !== 'number' || !isFinite(v)) { return '—'; }
  return '¥' + v.toFixed(4);
}

function _n(v) { return (typeof v === 'number' && isFinite(v)) ? String(v) : '—'; }
function _arr(v) { return Array.isArray(v) ? v : []; }

/**
 * 🔴 **阶段轨** —— 每一格的值都来自**真返回**，⛔ 一格都不编。
 *
 * | 格 | 值从哪来 | 取不到时 |
 * |---|---|---|
 * | 预算检查 | 这次请求**过没过**（被挡 ⇒ 那个请求是 429，页面另有说明） | — |
 * | 模型决策 | `trace.agent_decisions[]` 的**条数**（真数） | — |
 * | 调用工具 | `trace.tool_calls[]` 的条数 + **合计耗时**（每步 `duration_ms`） | — |
 * | 汇总生成 | 成本轴里 `purpose=answer_generation` 那笔的 token/费用 | — |
 * | 整条链 | `trace.duration_ms`（墙钟） | — |
 *
 * ⚠️ **"汇总生成"这一段没有单独的耗时** —— 后端只记了它的 token 与费用，
 *    ⛔ 别拿"总时长 − 前三段"倒推（那会印出一个**假的数**，`rag.js` 栽过同款）。
 * ⚠️ **整条链的时长在不收表时是"到现在为止"**（`tool_visualizer` 用
 *    `end_time or time.time()`）：停在审批点的那条**还没收表**，批准后重取会更大。
 *    这一格的口径**由页面写明**，⛔ 这里不假装它是"这一轮"。
 */
function railRows(chain, trace, costItems) {
  const t = trace || {};
  const calls = _arr(t.tool_calls);
  const decisions = _arr(t.agent_decisions);
  const items = _arr(costItems);
  let toolMs = 0, hasToolMs = false;
  for (const c of calls) {
    if (typeof c.duration_ms === 'number' && isFinite(c.duration_ms)) {
      toolMs += c.duration_ms; hasToolMs = true;
    }
  }
  const gen = items.filter((x) => String(x.purpose || '') === 'answer_generation')[0];
  const rows = [];
  rows.push({ name: '预算检查 · 会话级 + 全站级', value: '通过', dim: false });
  rows.push({
    name: '模型决策 · 调不调工具、调哪个',
    value: decisions.length ? String(decisions.length) + ' 轮' : '—',
  });
  rows.push({
    name: '调用工具' + (chain && chain.has.approval ? ' · 会对外发数据的那一步' : ''),
    value: calls.length ? (String(calls.length) + ' 次 · ' + (hasToolMs ? fmtMs(toolMs) : '—')) : '—',
  });
  rows.push({
    name: '汇总生成',
    value: gen ? ((gen.total_tokens == null ? '—' : String(gen.total_tokens) + ' tokens')
                 + ' · ' + fmtCost(gen.cost)) : '—',
  });
  rows.push({ name: '整条链（墙钟）', value: fmtMs(t.duration_ms) });
  return rows;
}

/**
 * **本次执行明细** —— 每一步打了谁（**只有写轨迹的链有这个**）。
 * ⚠️ 结果串可能很长（后端截到 500 字）⇒ 由页面折叠显示，这里只切短标签用。
 */
function toolRows(trace) {
  return _arr((trace || {}).tool_calls).map((c, i) => ({
    n: String(i + 1),
    name: stripMd(c.tool_name || '—'),
    ok: String(c.status || '') === 'success',
    status: String(c.status || '—'),
    ms: fmtMs(c.duration_ms),
    args: stripMd(JSON.stringify(c.arguments == null ? {} : c.arguments)),
    result: stripMd(c.result || ''),
  }));
}

/** **计划表**（`plan_execute` 那条链特有）—— `plan[]` 一步一行。 */
function planRows(resp) {
  return _arr((resp || {}).plan).map((p, i) => ({
    n: String(p.step == null ? i + 1 : p.step),
    action: stripMd(p.action || ''),
    tool: stripMd(p.tool || '—'),
    input: stripMd(p.input == null ? '' : String(p.input)),
  }));
}

/** **待接管表** —— 一行一条等着人处理的会话。 */
function pendingRows(resp) {
  return _arr((resp || {}).items).map((it) => ({
    raw: it.raw_thread_id == null ? '' : String(it.raw_thread_id),
    owner: stripMd(it.user_name || ''),
    graph: stripMd(it.graph || ''),
    rounds: _n(it.rounds),
    tools: _arr(it.tool_calls).map((tc) => stripMd(tc.name || '')).join('、'),
  }));
}

/** **裁决历史** —— 一行一次真裁决。 */
function historyRows(resp) {
  return _arr((resp || {}).events).map((e) => ({
    owner: stripMd(e.owner || ''),
    actor: stripMd(e.actor || ''),
    decision: String(e.decision || '—'),
    edited: e.edited ? '是' : '否',
    rounds: _n(e.rounds),
    reason: stripMd(e.reason || ''),
  }));
}

/** **工具清单** —— 从真表数出来（⛔ 页面不写死那 7 行）。 */
function toolCatalog(resp) {
  const r = resp || {};
  const healthy = _arr(r.healthy_tools).map((t) => ({
    name: stripMd(t.name || ''), desc: stripMd(t.description || ''), version: stripMd(t.version || ''),
  }));
  return { healthy: healthy, unhealthy: _arr(r.unhealthy_tools).map(stripMd),
           total: typeof r.total === 'number' ? r.total : healthy.length };
}

/**
 * 成本轴那批逐笔明细 → 三个格 + 分用途。
 * ⚠️ 它本来就是**这条线程**的（`/agent/trace/{id}/cost` 按 `thread_id` 筛）——
 *    与 `rag` 页那三格**口径不同**（那边是"本用户累计"，因为 `/rag/search` 没记对 thread）。
 */
function costOf(costResp) {
  const r = costResp || {};
  const total = r.total || {};
  const items = _arr(r.items);
  let llm = 0, emb = 0;
  for (const x of items) {
    const t = (typeof x.total_tokens === 'number' && isFinite(x.total_tokens)) ? x.total_tokens : 0;
    if (String(x.purpose || '').toLowerCase().indexOf('embed') !== -1) { emb += t; } else { llm += t; }
  }
  const has = items.length > 0;
  return {
    calls: has ? String(total.count == null ? items.length : total.count) : '—',
    llm: has ? String(llm) : '—',
    embedding: has ? String(emb) : '—',
    cost: has ? fmtCost(total.total_cost) : '—',
    truncated: !!r.truncated,
  };
}

/**
 * 🔴 **SSE 分帧**（页面那只读取器每次拿到的是一段**不保证完整**的文本块）。
 *
 * 帧格式实测（`app/routing/specs/sse.md` · 与另几条 agent 流式链同一套）：
 *   `data: {"content": "…"}` 每 token 一帧 · `data: {…汇总…}` 收尾一帧 · `data: [DONE]` 哨兵
 *   ⚠️ **没有 `event:` 行**（只有 `data:`）。
 *
 * ⚠️ **必须缓冲**：一次 `read()` 可能切在帧中间 ⇒ 只处理到**最后一个 `\n\n`** 为止，
 *    剩下的回给下一次。⛔ 别用"按行 split 然后逐行 JSON.parse"那种写法（半帧会当场抛）。
 *
 * @returns {{events: Array<object|[DONE] 标记>, rest: string}}
 */
function parseFrames(buffer) {
  const text = String(buffer == null ? '' : buffer);
  const parts = text.split('\n\n');
  const rest = parts.pop();                 // ⚠️ 最后一段可能不完整 ⇒ 留着
  const events = [];
  for (const raw of parts) {
    const line = raw.trim();
    if (line.indexOf('data:') !== 0) { continue; }
    const payload = line.slice(5).trim();
    if (payload === '[DONE]') { events.push({ done: true }); continue; }
    try { events.push({ json: JSON.parse(payload) }); }
    catch (e) { /* ⚠️ 认不出的帧**跳过**，⛔ 不让它把整条流打断 */ }
  }
  return { events: events, rest: rest };
}

/** 本页的 `thread_id` —— 一次会话一个（后端那条会话预算闸按它算）。 */
function newThreadId() {
  const r = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID().slice(0, 8) : String(Math.random()).slice(2, 10);
  return 'agent-page-' + r;
}

const RagAgent = {
  TOOLS_PATH, COST_PATH, BUDGET_PATH, INTERCEPTS_PATH, PENDING_PATH,
  PENDING_CTX_PATH, APPROVE_PATH, HISTORY_PATH, MEM_SEARCH_PATH, MEM_ADD_PATH,
  CHAINS, chainOf, rowTextOf, buildPath, buildTracePath, buildTraceAxisPath, buildApprovePath,
  buildContextPath, buildPendingPath, railRows, toolRows, planRows, pendingRows,
  historyRows, toolCatalog, costOf, parseFrames, stripMd, fmtMs, fmtCost, newThreadId,
};

if (typeof module !== 'undefined' && module.exports) { module.exports = RagAgent; }
if (typeof window !== 'undefined') { window.RagAgent = RagAgent; }

})();   // ⛔ 别去掉 —— 去掉它，顶层绑定会与页面自己的 `el` / `key` 撞车，而且**不报错**
