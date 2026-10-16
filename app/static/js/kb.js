'use strict';
/* `kb` 页（**知识入库**）的**纯逻辑** —— ⛔ 不碰 DOM、不发请求。
 *
 * ## 这一页有两件事
 *
 * ① **入库**（`insert` / `insert_batch` / `upload_document` / `DELETE`）——
 *    ⚠️ 那是**写接口**：会**花 embedding 的钱**、会**真改库**。
 * ② 🆕 **「库里有什么」**（`2026-10-12`）——
 *    作者定的目的（逐字）：「让大部分不想自己提交文档的访客，能知道**已有哪些是已有的，
 *    是可以直接搜索，我该搜索什么，会出现内容**」⇒ 即**可发现性**。
 *
 * ## 🔴 两个作用域（他原话：「自己的 + 公用的」）
 *
 * `scope=mine`（默认）· `scope=public`（公用语料）。
 * ⚠️ **隔离**：后端只管一个作用域；⛔ **别人的东西一条都不会回来**。
 *
 * ## ⚠️ 分层为什么是「五类」而不是「文件夹」
 *
 * 作者原话里说的是「**按文件夹逐级分层**」—— 🔴 **但数据里没有目录**：
 * `documents.source` 存的只是**文件名**（`B-01-售后服务与保修制度.md`）。
 * ⇒ 硬做"文件夹"就是**编一层结构出来**（违反他立的那条「⛔ 不许编」）。
 * ⇒ 用**语料方自己定的五类**（`testdata/corpus/README.md`）—— ⛔ 不是我编的。
 */

(function () {

/** 本页要打的端点。⚠️ 字面量必须以 `/api/v1` 开头（页面守卫会扫）。 */
const LIST_PATH = '/api/v1/rag/documents';
const FULL_PATH = '/api/v1/rag/documents/';

/**
 * 🔴 **切块档位表** —— `doc_type` 的可选值。
 *
 * ⚠️ **必须逐字等于后端 `app/rag/chunker.py` 的 `CHUNK_CONFIGS` 的键**，
 *    因为端点对表外的值**当场 400**（`api_v1_rag.py` 那条 `if doc_type not in CHUNK_CONFIGS`），
 *    而**两边各自都"自洽"** ⇒ 这是**跨语言**的，Python 与 JS 谁也管不着谁。
 * 🔴 **只有门能拦**：`app/tests/test_doc_types_crosslang.py` 拿这个数组去比后端的键
 *    （这就是  说的"那条守卫现在没有落点"—— 2026-10-12 落在这儿）。
 * ⛔ **页面不许在 HTML 里另写一份**（那就又成两份真相了）。
 */
const DOC_TYPES = ['default', 'technical', 'legal', 'report', 'article', 'faq'];

/** 三种入库方式 —— 一行一种，各自一条端点。 */
const METHODS = [
  { id: 'single', label: '单条文本', path: '/api/v1/rag/insert', note: '整篇当一条 · ⛔ 不切块 · ⛔ 不收 doc_type' },
  { id: 'batch',  label: '批量',     path: '/api/v1/rag/insert_batch', note: '一段一条 · ⛔ 同样不切块' },
  { id: 'upload', label: '上传文件', path: '/api/v1/rag/upload_document', note: '解析 → 预处理 → 【切块】→ 入库 · 这一条才收 doc_type' },
];

/** 批量：把一片文本切成"一段一条"。空行 / `---` 都算分隔。⛔ 全空 ⇒ 回空数组（不发空请求）。 */
function splitBatch(text) {
  return String(text == null ? '' : text)
    .split(/\n\s*---\s*\n|\n\s*\n/)
    .map((x) => x.trim())
    .filter((x) => x.length > 0);
}

/** 批量请求体。⚠️ 与后端 `BatchDocumentInsert{documents:[DocumentInsert]}` 对齐。 */
function buildBatchBody(text, source) {
  return { documents: splitBatch(text).map((c) => ({ content: c, source: source })) };
}

/** 作用域 —— ⛔ 只许这两个值（与后端 `scope` 的 pattern 一致）。 */
const SCOPES = [
  { id: 'public', label: '公用库', note: '语料方交的那批 —— 谁都能查' },
  { id: 'mine', label: '我的库', note: '你自己入库的 —— 只有你看得到' },
];

/** 建列表路径。⚠️ `scope` 与 `limit` 都走 **query**。 */
function buildListPath(scope, limit) {
  return LIST_PATH + '?scope=' + encodeURIComponent(scope)
       + '&limit=' + encodeURIComponent(String(limit || 200));
}

/** 建"取整篇"路径。⚠️ `firstId` 传**该篇的任意一个 chunk id** —— 返回的是**整篇**。 */
function buildFullPath(firstId) {
  return FULL_PATH + encodeURIComponent(String(firstId));
}

/**
 * 建"删一篇"路径（`DELETE /api/v1/rag/documents/{id}`）。
 *
 * 🔴 **路径与"取整篇"是同一个 url，只差 `method`** —— 后端就这么设计的
 *    （`documents` 表里存的是**切块**，一篇 = 很多行；`{doc_id}` 传**任一块的 id**，
 *     删的是**整篇**）。
 * ⚠️ **那也正是不给它写成一个 `buildFullPath` 别名就完的原因**：调用点上写
 *    `DELETE` + 一个叫 `buildFullPath` 的函数 = **名字在撒谎**（本仓最恨的那类）。
 * 🔴 **权限在后端**：只许删**自己传的**（管理员除外）—— 前端这里只管点得到。
 */
function buildDeletePath(chunkId) {
  return FULL_PATH + encodeURIComponent(String(chunkId));
}

/**
 * 按搜索词过滤分组。
 *
 * 🔴 **搜索框是这个功能的【一半】，⛔ 不是附加** —— 作者那句目的里就写着
 *    「**我该搜索什么，会出现内容**」。74 篇靠点太慢。
 *
 * 匹配面：`source`（篇名）· `preview`（首块前 120 字）。
 * ⚠️ 命中后**分组还在**（⛔ 不摊平成一坨 —— 那样类别那一层就白做了）。
 * @returns 与入参同形状的 groups（空分组**去掉**；全不中 ⇒ `[]`）
 */
function filterGroups(groups, q) {
  const kw = String(q == null ? '' : q).trim().toLowerCase();
  if (!kw) { return groups || []; }
  const out = [];
  for (const g of (groups || [])) {
    const hit = (g.docs || []).filter((d) =>
      String(d.source || '').toLowerCase().includes(kw) ||
      String(d.preview || '').toLowerCase().includes(kw));
    if (hit.length) { out.push({ name: g.name, count: hit.length, docs: hit }); }
  }
  return out;
}

/** 列表的汇总行：`公用库 · 12 篇 · 3 类`（⛔ 缺值不印 `undefined`）。 */
function summaryOf(scope, resp) {
  const label = (SCOPES.find((s) => s.id === scope) || {}).label || scope;
  const r = resp || {};
  const n = (typeof r.total === 'number' && isFinite(r.total)) ? String(r.total) : '—';
  const g = Array.isArray(r.groups) ? String(r.groups.length) : '—';
  return label + ' · ' + n + ' 篇 · ' + g + ' 类' + (r.truncated ? '（只显示了一部分）' : '');
}

const RagKb = {
  LIST_PATH, FULL_PATH, SCOPES, DOC_TYPES, METHODS,
  buildListPath, buildFullPath, buildDeletePath, filterGroups, summaryOf, splitBatch, buildBatchBody,
};

if (typeof module !== 'undefined' && module.exports) module.exports = RagKb;
if (typeof window !== 'undefined') window.RagKb = RagKb;

})();   // ⛔ 别去掉 —— 去掉它，顶层绑定会与页面自己的绑定撞车，而且**不报错**
