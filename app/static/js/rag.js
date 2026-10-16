'use strict';
/* `rag` 页（**内部RAG知识库检索及问答**）的**纯逻辑** —— ⛔ 不碰 DOM、不发请求。
 *
 * ## 这一页打什么
 *
 * `POST /api/v1/rag/search` —— 🔴 **`mode` 是【query 参数】**，⛔ 不在 body 里。
 * ⚠️ 这一点是踩过的：`QuestionRequest` 上那个 `mode` 字段是**死的**（没人读），
 *    传它会被**静默忽略**，实际跑的是 query 参数的默认值（2026-10-12 已删）。
 *
 * ## 返回体（2026-10-12 **真调一次**量出来的，⛔ 不是照文档抄）
 *
 * ```
 * query  pipeline{original_query, rewrite_enabled, expand_enabled, bm25_enabled,
 *                 rerank_enabled, rewritten_query}
 * timing{rewrite_ms, search_ms, rerank_ms, total_ms}
 * docs[] {id, content, source, from, rrf_score}
 * answer            ← 「根据现有资料，无法回答。」也是一种**正常**返回
 * sources[]{index, id, source, content, content_preview, similarity}
 * requested_by  mode
 * ```
 * 🔴 **`answer` 里带 `[来源 n]`**，`sources[].index` 就是那个 n —— 两者靠它对上。
 *
 * 🔴 **「花费」不在这个返回里** —— 它得另查 `/api/v1/agent/cost/records`
 *    （逐次调用的记录，带 `purpose` / `total_tokens` / `total_cost`）。
 *    ⚠️ **那个端点本来就只回【当前用户】的** ⇒ 直接加总即可。
 *    🔴 **⛔ 别按 `thread_id` 筛** —— 实测调用者传的那个 thread **没进账本**（见 `costOf` 的说明）。
 */

(function () {

/** 本页要打的两条端点。⚠️ **字面量必须以 `/api/v1` 开头**（页面守卫会扫）。 */
const SEARCH_PATH = '/api/v1/rag/search';
const COST_PATH = '/api/v1/agent/cost/records';

/**
 * 四档 mode。🔴 `ok:false` 那两档**在 线上点了会 500**（镜像不装重排依赖）
 * ⇒ 页面上做成**不可点 + 写明原因**，⛔ 不留一个"点了必红"的按钮。
 */
const MODES = [
  { id: 'accurate_norerank', ok: true,  note: '默认：改写 + 向量 + BM25 + RRF，⛔ 不含重排（不依赖 torch）' },
  { id: 'fast',              ok: true,  note: '只向量 + BM25 + RRF，最快' },
  { id: 'accurate',          ok: false, note: '需要重排模型 —— 本服务镜像不装' },
  { id: 'full',              ok: false, note: '需要重排模型 —— 本服务镜像不装' },
];

/** 阶段轨 —— 🔴 **只有接口真测了的那三段**，⛔ 不自己凑第四段。
 *
 *  ⚠️ 形态稿上写的是四段：「查询改写 / 召回 / **融合·RRF** / 生成答案」。实测下来**两处都不成立**：
 *   ① **"融合"没有单独的一段** —— RRF 含在 `search_ms` 里；
 *   ② 🔴 **`total_ms` 不含"生成答案"** —— 实测 `rewrite 1896.81 + search 1950.01 = 3846.82`
 *      而 `total_ms = 3847.8`（≈ 前三段之和）⇒ 它是**检索管线**的总时长。
 *      ⚠️ 我第一版按"总 − 前三段"倒推第四段 ⇒ 页面上会印出「**生成答案 0 ms**」，
 *         而生成明明花了几秒 —— **那是个假的数**（本仓：⛔ 不许印没有数据源的数）。
 *    ⇒ 现在**只列真有的三段**，并在页面说明里写清"生成本接口不计时"。
 *      🔴 要让访客看见生成耗时，得**另取**（端到端的墙钟时间）—— 那是另一件事，⛔ 别拿 `total_ms` 冒充。 */
const STAGES = [
  { key: 'rewrite_ms', name: '查询改写' },
  { key: 'search_ms',  name: '检索（向量 + BM25 + RRF）' },
  { key: 'rerank_ms',  name: '重排' },
];

/**
 * 🔴 **这一页能打哪些接口**（作者 2026-10-12 定 · 判据见 `页面-Agent自验收标准.md` §五之一）。
 *
 * ⛔ **不列** `hybrid_search` / `rewrite_search` / `rerank_search` ——
 *   它们**被 `/rag/search` 的 mode 覆盖**（实测读代码核过）：
 *   `hybrid_search` = `mode=fast` · `rewrite_search` = `mode=accurate_norerank`
 *   `rerank_search` = `mode=accurate` / `full`。
 * ✅ **列** `pg_search` —— 它是 `fast` 的**真子集**（少 BM25 + RRF），是**消融对照**，没被覆盖。
 * ⚠️ **`stream_search` 不在这里** —— 它单独一页（这里放不下流式输出）。
 *
 * `has` 说的是**这条接口产不产出那几样** —— 页面据此决定哪些块有意义（⛔ 不摆空的装能看）。
 */
const ENDPOINTS = [
  {
    id: 'search', path: '/api/v1/rag/search', title: 'POST /api/v1/rag/search',
    has: { mode: true, switches: true, timing: true, answer: true },
  },
  {
    id: 'pg', path: '/api/v1/rag/pg_search', title: 'POST /api/v1/rag/pg_search',
    // ⚠️ `pg_search` 的签名是 `(req, user_name)` —— **不收** mode / thread_id / 那几个开关，
    //    也**不产出** timing / answer / sources（它只回一个 `docs[]`）。
    has: { mode: false, switches: false, timing: false, answer: false },
  },
];

/** 按 id 取端点；认不出就回第一条（⛔ 不抛 —— 页面不该因为一个值坏了就整页白）。 */
function endpointOf(id) {
  for (let i = 0; i < ENDPOINTS.length; i++) { if (ENDPOINTS[i].id === id) { return ENDPOINTS[i]; } }
  return ENDPOINTS[0];
}

/** 建这条端点的请求路径。⚠️ `mode` 与 `thread_id` **只有 `/rag/search` 收**。 */
function pathFor(ep, mode, threadId) {
  return ep.has.mode
    ? ep.path + '?mode=' + encodeURIComponent(mode) + '&thread_id=' + encodeURIComponent(threadId)
    : ep.path;
}

/** 建查询串。⚠️ `mode` 与 `thread_id` 都走 **query**。 */
function buildSearchPath(mode, threadId) {
  return SEARCH_PATH + '?mode=' + encodeURIComponent(mode)
       + '&thread_id=' + encodeURIComponent(threadId);
}

/** 建 body。⛔ **不放 `mode`**（那是死字段）。 */
function buildBody(opts) {
  const o = opts || {};
  return {
    question: String(o.question == null ? '' : o.question).trim(),
    top_k: Number(o.topK) || 4,
    citations: !!o.citations,
    strict_mode: !!o.strictMode,
    generate_answer: !!o.generateAnswer,
  };
}

/**
 * 三段耗时（**接口真测了的那些** —— 见 `STAGES` 上面那段：`total_ms` 里⛔ 不含生成）。
 * ⚠️ 任一字段不是数 ⇒ 回 `null`（⛔ **不编 0** —— 本仓：「不许印没有数据源的数」）。
 */
function stagesOf(timing) {
  const t = timing || {};
  const num = (v) => (typeof v === 'number' && isFinite(v)) ? v : null;
  const val = { rewrite_ms: num(t.rewrite_ms), search_ms: num(t.search_ms), rerank_ms: num(t.rerank_ms) };
  return STAGES.map((s) => ({ name: s.name, ms: val[s.key] }));
}

/** 一条耗时怎么印。⚠️ `null` ⇒ `—`（⛔ 不印 0ms，那会被读成"这一段没花时间"）。 */
function fmtMs(ms) {
  if (typeof ms !== 'number' || !isFinite(ms)) return '—';
  return ms >= 1000 ? (ms / 1000).toFixed(2) + ' s' : ms.toFixed(0) + ' ms';
}

/** 三个指标。⚠️ 缺值一律 `—`。 */
function metricsOf(resp) {
  const r = resp || {};
  const n = (v) => (typeof v === 'number' && isFinite(v)) ? String(v) : '—';
  const docs = Array.isArray(r.docs) ? r.docs.length : null;
  const src = Array.isArray(r.sources) ? r.sources.length : null;
  return {
    hits: docs === null ? '—' : n(docs),
    cited: src === null ? '—' : n(src),
    total: fmtMs(r.timing && r.timing.total_ms),
  };
}

/**
 * 把 `cost/records` 那批（**它本来就只回当前用户的**）加总。
 *
 * 🔴 **为什么不按 `thread_id` 筛**（2026-10-12 实测撞出来的）：
 *   本页传的 `?thread_id=rag-page-<uuid>` **⛔ 没有进账本** —— 实测同一批里写进去的是
 *   `answer_generation → default` · `embedding → system` · `query_rewrite → unknown`
 *   （**三个不同的 thread，一个都不是调用者传的那个**）。
 *   ⇒ 按 `thread_id` 筛**永远筛不到**，那三格会恒为 `—`。
 *   🔴 已登记为待修： 的 （⚠️ 它影响的**不只是这个页面** ——
 *      「按会话的额度」与「按会话的账」现在算的是**不同的桶**）。
 * ⚠️ 所以这里**只按端点已经做过的用户过滤**去加总 ⇒ 对访客（一人一个新用户）
 *    就等于"他这次进来花的"；对一个**老用户**它是"他的累计"。
 *    ⇒ 页面上的标签写的是「**本用户累计**」，⛔ 不是"本次"。
 * @returns {{llm:number|null, embedding:number|null, cost:number|null, calls:number}}
 *   ⚠️ 一条都没有 ⇒ 三个都是 `null`（页面画 `—`，⛔ 不画 0）
 */
function costOf(records) {
  const rows = (Array.isArray(records) ? records : []).filter(Boolean);
  if (!rows.length) return { llm: null, embedding: null, cost: null, calls: 0 };
  let llm = 0, emb = 0, cost = 0;
  for (const r of rows) {
    const t = (typeof r.total_tokens === 'number' && isFinite(r.total_tokens)) ? r.total_tokens : 0;
    // 🔴 按 `purpose` 分：含 embed 的算 embedding，其余算 LLM。
    //    ⚠️ 这是**按名字分**，不是后端标的类型 —— 见下面 `PURPOSE_IS_EMBEDDING` 的说明。
    if (String(r.purpose || '').toLowerCase().indexOf('embed') !== -1) emb += t; else llm += t;
    if (typeof r.total_cost === 'number' && isFinite(r.total_cost)) cost += r.total_cost;
  }
  return { llm: llm, embedding: emb, cost: cost, calls: rows.length };
}

/**
 * 🔴 「引用」还是「检索到的资料」？—— **同一个格子，两种内容**（2026-10-12 作者裁）。
 *
 * 作者原话（他举的用法）：「**当我只打开 citations、没有打开 generate_answer，我点击提问，
 * 右侧的"引用"没有内容显示。这个是错的**，因为当不需要 llm，用户只想知道本地知识库
 * 通过 RAG 检索找资料的时候会用到。用户动作示例：**先不开 llm，RAG 知识库搜资料；
 * 需要 llm 辅助，再开 generate_answer**。」
 *
 * 🔴 根因：这是**两个不同的键**，我一开始混成了一格 ——
 *   · `sources[]` = **引用**（答案里绑定的那几条，带 `[来源 n]` 序号）⇒ ⚠️ **只有成文时才有**
 *   · `docs[]`    = **检索到的资料**（每条带 `content` / `source` / `rrf_score`）
 *                   ⇒ 🔴 **只要召回到东西就有，跟开不开 LLM 无关**
 *
 * ⇒ 判据：**有成文 ⇒ 显示引用；没成文 ⇒ 显示检索到的资料**（标题也跟着变，⛔ 不叫"引用"却摆检索结果）。
 *
 * @returns {{kind:'citation'|'docs', title:string, label:string, rows:Array<{head:string, body:string}>}}
 *   ⚠️ 两边都空 ⇒ `rows` 是 `[]`，由页面去画空态
 */
/**
 * 🔴 **语料原文自带 markdown 记号** —— 渲染前要去掉（2026-10-12 真浏览器上看见的）。
 *
 * ⚠️ 实测：检索回来的 `docs[].content` 里印着 `**谁在什么时候做什么保养**` ——
 *    那是**语料文件自己的** markdown，页面上原样印出来就是**两个星号**。
 *    而本仓那条门（`test_web_pages.py` 的 markdown 门）**只扫页面 HTML**，
 *    **扫不到数据里带进来的** ⇒ 这一条只能在这里拦。
 *
 * 🔴 **只去掉记号，⛔ 不动一个字** —— 硬约束①：真数据，内容不许改（⛔ 不是"清洗语料"）。
 *    ⚠️ 也**不做 markdown 解析**（那就变成"渲染"了，而本仓⛔ 不引 md 渲染器）⇒ 就是抹掉那对星号。
 *    ⚠️ 用 `textContent` 塞进 DOM ⇒ **天然不过 HTML**，不存在 XSS 面。
 */
function stripMd(s) {
  return String(s == null ? '' : s).replace(/\*\*/g, '');
}

function referenceRows(resp) {
  const r = resp || {};
  const num = (v) => (typeof v === 'number' && isFinite(v)) ? String(v) : '—';
  const src = Array.isArray(r.sources) ? r.sources : [];
  if (src.length) {
    return {
      kind: 'citation', title: '引用', label: src.length + ' 条',
      rows: src.map((x) => ({
        head: stripMd('[' + num(x.index) + '] ' + (x.source || '—') + ' · 相似度 ' + num(x.similarity)),
        body: stripMd(x.content_preview || x.content || ''),
      })),
    };
  }
  const docs = Array.isArray(r.docs) ? r.docs : [];
  return {
    kind: 'docs', title: '检索到的资料', label: docs.length + ' 条',
    rows: docs.map((x) => ({
      // 🔴 两种 docs 的字段不一样（2026-10-12 真浏览器上照出来的）——
      //    /rag/search 给 rrf_score + from；pg_search 给的是 **similarity**（纯向量）。
      //    ⛔ 只认一种 ⇒ 另一种永远是「—」（而页面上看不出来是「没有」还是「我没取」）。
      head: stripMd((x.source || '—')
        + ' · ' + (x.rrf_score != null ? '相关度 ' + num(x.rrf_score)
                                      : '相似度 ' + num(x.similarity))
        + (x.from ? ' · 来源 ' + x.from : '')),
      body: stripMd(x.content || ''),
    })),
  };
}

/** 钱的印法。⚠️ `null` ⇒ `—`；很小的数**保 4 位**（本仓：⛔ 不许把它印成 0）。 */
function fmtCost(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—';
  return '¥' + v.toFixed(4);
}

/** 本页的 `thread_id` —— 一次会话一个，用来把"本次花费"和这次提问对上。 */
function newThreadId() {
  const rnd = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID().slice(0, 8)
    : String(Math.random()).slice(2, 10);
  return 'rag-page-' + rnd;
}

const RagRag = {
  SEARCH_PATH, COST_PATH, MODES, STAGES,
  ENDPOINTS, endpointOf, pathFor,
  buildSearchPath, buildBody, stagesOf, fmtMs, metricsOf, costOf, fmtCost, newThreadId,
  referenceRows, stripMd,
};

if (typeof module !== 'undefined' && module.exports) module.exports = RagRag;
if (typeof window !== 'undefined') window.RagRag = RagRag;

})();   // ⛔ 别去掉 —— 去掉它，顶层绑定会与页面自己的 `el` / `key` 撞车，而且**不报错**
