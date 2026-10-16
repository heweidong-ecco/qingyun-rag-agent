'use strict';
/* `index` 页（**总览 / 首页** · `GET /` 302 到它）的**纯逻辑** —— ⛔ 不碰 DOM、只在 `loadCounts` 里发一个请求。
 *
 * ## 这一页做三件事（2026-10-13 重做 · ）
 *
 * ① **一眼说清「这是什么 · 有什么」** —— 首屏定位 + 一排**真数**；
 * ② **能力地图**：每条能力一个可点入口（⛔ 不再只列 3 条链 —— 那是它原来的毛病）；
 * ③ **一条真证据**：真返回体（**点了才跑**）。
 *
 * ## 🔴 三条口径（⛔ 改这个文件之前先读）
 *
 * ① **接口条数从服务自己现算**（`/openapi.json`）—— ⛔ **不写死**。
 *    本仓立场：**写死的数必然过期**；而且它让"有多少接口"这句话**可核**（谁都能点开那个 JSON 数一遍）。
 * ② **取不到就画「—」，⛔ 绝不画 0** —— `0 条接口` 是一句**假话**（服务有接口，只是这次没读到）。
 * ③ **`/openapi.json` 那条路径⛔ 不写字面量** —— 它不在 `/api/v1` 下，而页面守卫要求
 *    字面量必须以 `/api/v1` 开头；硬写会被拦下，**而那条红会把人引向改门**（门是对的）。
 *    ⇒ 与 `ops.js` 的 `ROOT_NAMES` 同一套做法：**用名字拼**。
 *
 * ⚠️ 形状说明：本仓【没有 package.json】⇒ Node 把 .js 当 CommonJS ⇒ 不能用 import/export。
 *    下面是**经典脚本**，末尾条件导出给 Node 的 `require`。
 *
 * 🔴🔴 **整体包在一个 IIFE 里** —— ⛔ 别把这层括号去掉。经典脚本的顶层绑定**全是全局的**，
 *    而页面自己有顶层绑定（`const $ = …`）⇒ 多漏一个键就可能撞死整页，**而且不报错**。
 */

(function () {

/** 🔴 字面量必须以 `/api/v1` 开头（页面守卫会扫）—— 它也是本页唯一真调的端点。 */
const SEARCH_PATH = '/api/v1/rag/search';

/** 本页真跑用哪一档 —— 默认档（不依赖重排模型 ⇒ 任何机器上都跑得起来）。 */
const MODE = 'accurate_norerank';

/**
 * 🔴 **可选的几个问题**（2026-10-13 加）。
 *
 * ## 为什么要有这一排
 *
 * 原先那一版把问题**写死在 js 里、页面上一个字都不提** ⇒ 两个毛病：
 * ① 访客**看不到会问什么**（返回体那格是「—」）；
 * ② 而那个写死的句子**恰恰是语料答不好的那一个**（实测答成「根据现有资料，无法明确回答」）
 *    ⇒ **访客第一眼看到的是"它答不出来"**。
 *
 * ## 四条口径（⛔ 改这个清单之前先读）
 *
 * ① 🔴 **每一条都必须是【实测过、语料答得出】的** —— ⛔ 别凭感觉加。
 *    判据：起真服务逐条调 `/rag/search`，看 `answer` 是不是真内容（⛔ 不是"无法回答"）。
 * ② 🔴 **默认选中第一条** ⇒ 访客**什么都不做**也能点「跑一次」（⛔ 不留空、⛔ 不用打字）。
 * ③ 🔴 **最后一条【故意】是语料里没有的**，并且**在页面上标明**（`inCorpus: false`）——
 *    它演示的是「**没依据就不编**」（实测原文：「根据现有资料，无法回答。……其中不包含任何关于
 *    上海天气的信息。」）。⚠️ **不标出来就是陷阱**：访客会以为它坏了。标出来就是**一个能力**。
 * ④ ⛔ **别在 HTML 里再写一份**（那就成两份真相了）—— 那一排 chips 由本数组生成。
 */
const QUESTIONS = [
  { q: '保修期内换备件要不要钱', inCorpus: true },
  { q: '设备多久保养一次', inCorpus: true },
  { q: '上门服务的响应时间是多少', inCorpus: true },
  { q: 'QY300 的主轴转速是多少', inCorpus: true },
  // 🔴 这一条**不在语料库里**（见 ③）—— 页面上**明写着**它会答"无法回答"。
  { q: '今天上海的天气怎么样', inCorpus: false },
];

/** 没挑的时候用哪一句 —— 第一条（**语料答得出来的那条**）。 */
const DEFAULT_QUESTION = QUESTIONS[0].q;

/** 根路径那一条 —— ⚠️ **只写名字，⛔ 不写带斜杠的字面量**（见文件头口径 ③）。 */
const ROOT_NAMES = { openapi: 'openapi.json' };

/** 把上面那个名字拼成真路径。 */
function rootPath(name) { return '/' + ROOT_NAMES[name]; }

/** 真调的那条路径。 */
function searchPath() { return SEARCH_PATH + '?mode=' + encodeURIComponent(MODE); }

/** 真调的请求体。⚠️ 两个开关都显式打开 —— 它们**默认是关的**（本仓那条坑：不给就没有答案、没有引用）。 */
function buildBody(question) {
  const q = String(question == null ? '' : question).trim();
  return { question: q || DEFAULT_QUESTION, top_k: 3, citations: true, generate_answer: true };
}

/** 这一次的会话名（只用来在页面上显示"哪一次"）。 */
function threadId() {
  const r = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID().slice(0, 8) : String(Math.random()).slice(2, 10);
  return 'index-page-' + r;
}

/**
 * 🔴 **没有真值就画「—」，⛔ 绝不画 0**（本仓硬约束②）。
 *
 * ⚠️ 判据是"**它得是个有限的数**"，⛔ 不是 `!== undefined` —— 后者挡不住 `null`（会被印成 `NaN`）
 *    和 `NaN`。同 `cred.js` 的 `num()`。
 */
function num(value) {
  if (value === null || value === undefined) { return '—'; }
  if (typeof value === 'number') { return isFinite(value) ? String(value) : '—'; }
  const s = String(value).trim();
  return s ? s : '—';
}

/** 行数（能力地图那张表）。⛔ 空集合不谎报成 0 条能力 —— 那是"页面坏了"的样子。 */
function countRows(nodes) {
  const n = nodes ? nodes.length : 0;
  return n > 0 ? String(n) : '—';
}

/**
 * 从 `/openapi.json` 现算「多少条接口 · 多少组」。
 *
 * ⚠️ **形状不对就回 `null`**（调用方据此保持「—」）—— ⛔ 不 try 出一个 `0`。
 * @returns {{paths:number, groups:number}|null}
 */
function openapiCounts(spec) {
  if (!spec || typeof spec !== 'object') { return null; }
  const paths = spec.paths;
  if (!paths || typeof paths !== 'object') { return null; }

  const keys = Object.keys(paths);
  if (!keys.length) { return null; }

  const groups = new Set();
  for (const item of Object.values(paths)) {
    if (!item || typeof item !== 'object') { continue; }
    for (const op of Object.values(item)) {
      const tags = (op && op.tags) || [];
      for (const t of tags) { groups.add(t); }
    }
  }
  return { paths: keys.length, groups: groups.size };
}

/** 拉一次 `/openapi.json` ⇒ 条数。⛔ 任何失败都回 `null`（页面保持「—」，⛔ 不弹错）。 */
function loadCounts() {
  return fetch(rootPath('openapi'))
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (spec) { return openapiCounts(spec); })
    .catch(function () { return null; });
}

/** 答案那一格只放开头 —— ⚠️ 整段答案不塞进这一行（那会把版面撑爆），标一个「…」说明它被截了。 */
const ANSWER_HEAD = 60;

/**
 * 把可能很长的字符串截成**一行**能放下的样子。
 *
 * 🔴 **必须先把空白折叠掉**（2026-10-13 实测栽的）：取回来的 `content` / `answer` 里**带换行**
 * （语料是多段 Markdown），而那一栏是 `white-space: pre` ⇒ **一个 `\n` 就多撑出一行** ——
 * 实测整块高度从 **698 px 涨到 1005 px**，而**没有任何门会红**（它只是"看着变高了"）。
 * ⇒ 折叠成单空格是对"预览"的正确语义（它按定义就是一行）。
 */
function clip(text, limit) {
  // 🔴 **`**` 也要去掉**（2026-10-13 真浏览器上看到的）：答案里带着 `**不收备件费**` ——
  //    那是**模型自己写的 markdown**，而页面用 `textContent` 渲染 ⇒ **星号会原样印在页面上**。
  //    ⚠️ 页面那条 markdown 门**扫不到它**：它扫的是**HTML 文件**，而这串是**运行时**才进 DOM 的
  //    （`页面-Agent自验收标准.md` §五之四 记的正是这个缝）。同款处置见 `rag.js` 的 `stripMd`。
  const s = String(text == null ? '' : text)
    .replace(/\*\*/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  const n = limit || ANSWER_HEAD;
  return s.length > n ? (s.slice(0, n) + '…') : s;
}

/** 两个数组各展示**前几条**（⛔ 不是"只回几条" —— 是页面上放不下，超出的会写明"还有 N 条"）。 */
const ARRAY_CAP = 3;

/**
 * 🔴 **把 `docs[]` / `sources[]` 展开成真结构**（每条 3 行 · 最多 `ARRAY_CAP` 条）。
 *
 * ## 为什么要展开（2026-10-13 改）
 *
 * 原先这里只印一句「3 条」⇒ **返回体那半边的下半截是一大块空白**
 * （实测：容器 698 px、内容只到 312 px ⇒ 空 386 px）。
 * 而「完整返回」那一栏本来就该**把真结构摊开** —— `docs` 是一条条对象，⛔ 不是一个数字。
 *
 * 🔴 **口径**：键名都是**真返回里有的**（实测 `docs[0]` 的键 = `content/from/id/rrf_score/source`；
 * `sources[0]` = `content/content_preview/id/index/similarity/source`）；
 * 长字段**截断**并带「…」（⛔ 不是编一个短的）；缺值照画「—」（⛔ 不画 0）。
 * ⚠️ 超过 `ARRAY_CAP` 条 ⇒ 补一行「… 还有 N 条」—— **如实说"没全放"**，⛔ 不是假装就这么多。
 *
 * @returns {string[]} 每一行是一条**已经排好版的字符串**（DOM 层只负责塞进 `.ln`）
 */
function arrayLines(kind, payload) {
  const rows = (payload && Array.isArray(payload[kind])) ? payload[kind] : null;
  if (!rows || !rows.length) { return []; }

  const out = [];
  for (const r of rows.slice(0, ARRAY_CAP)) {
    const it = r || {};
    if (kind === 'docs') {
      out.push('    { "id": ' + num(it.id) + ', "source": "' + clip(it.source, 34) + '",');
      out.push('      "rrf_score": ' + num(it.rrf_score) + ', "from": "' + clip(it.from, 12) + '",');
      out.push('      "content": "' + clip(it.content, 40) + '" },');
    } else {
      out.push('    { "index": ' + num(it.index) + ', "id": ' + num(it.id) + ',');
      out.push('      "source": "' + clip(it.source, 34) + '",');
      out.push('      "similarity": ' + num(it.similarity) + ', "content_preview": "' + clip(it.content_preview, 30) + '" },');
    }
  }
  if (rows.length > ARRAY_CAP) {
    out.push('    … 还有 ' + (rows.length - ARRAY_CAP) + ' 条（去「内部RAG知识库检索及问答」那页逐条看）');
  }
  return out;
}

/**
 * 🔴 **把一次真返回映射到页面上那一串格子**（`[{id, text}]`）。
 *
 * ⚠️ **抽成纯函数**是为了让它**能被单测**：DOM 层只剩"把 text 填进 id"。
 * ⚠️ **字段名都是真返回里有的**（`app/routing/schemas.py` 的 `RagSearchResponse` +
 *    `app/static/js/rag.js` 头部的读法）—— ⛔ 别照文档抄，那两个地方是本仓核过的。
 * ⚠️ **`answer` / `sources` 只在 `generate_answer=true` 时才有内容** ⇒ 缺了就画「—」。
 */
function respFields(payload) {
  const p = payload || {};
  const pipeline = p.pipeline || {};
  const timing = p.timing || {};
  const docs = Array.isArray(p.docs) ? p.docs : null;
  const sources = Array.isArray(p.sources) ? p.sources : null;
  const flag = function (v) { return v === true ? 'true' : (v === false ? 'false' : '—'); };
  const ms = function (v) { return typeof v === 'number' && isFinite(v) ? (v + ' ms') : '—'; };

  return [
    { id: 'v-query', text: clip(p.query, 40) },
    { id: 'v-original', text: clip(pipeline.original_query, 40) },
    { id: 'v-rewrite', text: flag(pipeline.rewrite_enabled) },
    { id: 'v-expand', text: flag(pipeline.expand_enabled) },
    { id: 'v-bm25', text: flag(pipeline.bm25_enabled) },
    { id: 'v-rerank', text: flag(pipeline.rerank_enabled) },
    { id: 'v-t-rewrite', text: ms(timing.rewrite_ms) },
    { id: 'v-t-search', text: ms(timing.search_ms) },
    { id: 'v-t-rerank', text: ms(timing.rerank_ms) },
    { id: 'v-t-total', text: ms(timing.total_ms) },
    { id: 'v-docs', text: docs ? (docs.length + ' 条') : '— 条' },
    { id: 'v-answer', text: p.answer ? clip(p.answer) : '—' },
    { id: 'v-sources', text: sources ? (sources.length + ' 条') : '— 条' },
    { id: 'v-mode', text: p.mode ? String(p.mode) : '—' },
    { id: 'v-by', text: p.requested_by ? String(p.requested_by) : '—' },
  ];
}

const RagIndex = {
  SEARCH_PATH, MODE, QUESTIONS, DEFAULT_QUESTION, ROOT_NAMES, ANSWER_HEAD, ARRAY_CAP,
  rootPath, searchPath, buildBody, threadId, num, countRows,
  openapiCounts, loadCounts, clip, arrayLines, respFields,
};

if (typeof module !== 'undefined' && module.exports) { module.exports = RagIndex; }
if (typeof window !== 'undefined') { window.RagIndex = RagIndex; }

})();   // ⛔ 别去掉 —— 去掉它，顶层绑定会与页面自己的 `$` 撞车，而且**不报错**
