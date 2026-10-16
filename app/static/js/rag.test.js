'use strict';
/* `rag.js` 的用例 —— ⛔ 零 npm 依赖（只 node:test + node:assert）。
   跑法：node --test app/static/js/rag.test.js（已进 ci.yml 的 run 块，glob） */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, 'rag.js'), 'utf8');
const R = require('./rag.js');

/* ══════════════ 1 · 🔴 URL 前缀（本文件的【主要守卫】） ══════════════ */

test('🔴 两条端点都必须带 `/api/v1` 前缀', () => {
  // ⚠️ 为什么单列一条：`app/tests/test_web_pages.py` 只扫**页面里的字面量**，
  //    而本页走 helper ⇒ 对它**空过**（那份文件自己写着这条）。
  //    ⇒ 前缀保护**只落在本文件**（同族：旧的 lab.test.js / kb.test.js 都有一条）。
  assert.match(R.SEARCH_PATH, /^\/api\/v1\//);
  assert.match(R.COST_PATH, /^\/api\/v1\//);
});

test('`buildSearchPath` 出来的整串也带前缀，且两个参数都被编码', () => {
  const p = R.buildSearchPath('accurate_norerank', 'rag-page-a b/c');
  assert.ok(p.startsWith('/api/v1/'), p);
  assert.match(p, /mode=accurate_norerank/);
  assert.ok(!p.includes('b/c'), 'thread_id 没编码 —— 带 `/` 的值会把路径拆坏');
});

/* ══════════════ 2 · 🔴 `mode` ⛔ 不许回到 body 里（的回归守卫） ══════════════ */

test('🔴 body 里⛔ 不许有 `mode` —— 它是【死字段】，传了会被静默忽略', () => {
  // 🔴 这条守的是一个**真掉过的坑**：`QuestionRequest.mode` 在后端**没有任何人读**，
  //    传 `{"mode":"full"}` 会被**静默忽略**，实跑的是 **query 参数**的默认值。
  //    ⇒ 访客选了 fast、页面显示 fast，而**实际跑的是另一档** —— 页面上看不出来。
  // ⚠️ 反证：把 mode 加回这个对象 ⇒ 本条当场红。
  const b = R.buildBody({ question: 'x', topK: 4, citations: true });
  assert.deepStrictEqual(Object.keys(b).sort(),
    ['citations', 'generate_answer', 'question', 'strict_mode', 'top_k']);
  assert.ok(!('mode' in b), 'body 里又出现 mode 了 —— 那是 N40 那个死字段');
});

test('`buildBody`：问题要 trim · `top_k` 出数字 · 开关缺省即 false', () => {
  const b = R.buildBody({ question: '  问点什么  ', topK: '8' });
  assert.strictEqual(b.question, '问点什么');
  assert.strictEqual(b.top_k, 8);
  assert.strictEqual(b.citations, false);
  assert.strictEqual(b.generate_answer, false);
});

test('`buildBody`：`question` 给 `null` ⇒ 空串（⛔ 不是 `"null"` 四个字母）', () => {
  assert.strictEqual(R.buildBody({}).question, '');
  assert.strictEqual(R.buildBody({ question: null }).question, '');
});

/* ══════════════ 3 · 四段耗时 ══════════════ */

test('🔴 三段与返回体的 `timing` 一一对应 —— ⛔ 只有接口真测了的那些', () => {
  const rows = R.stagesOf({ rewrite_ms: 100, search_ms: 200, rerank_ms: 0, total_ms: 500 });
  assert.deepStrictEqual(rows.map((r) => r.name),
    ['查询改写', '检索（向量 + BM25 + RRF）', '重排']);
  assert.deepStrictEqual(rows.map((r) => r.ms), [100, 200, 0]);
});

test('🔴🔴 ⛔ 不许拿 `total_ms` 倒推"生成答案的时间" —— 那会印出一个假的 0', () => {
  // 🔴 2026-10-12 实测撞出来的：真返回 `rewrite 1896.81 + search 1950.01 = 3846.82`，
  //    而 `total_ms = 3847.8` ⇒ **它只等于前三段之和**（是**检索管线**的总时长，**不含生成**）。
  //    ⚠️ 我第一版按"总 − 前三段"倒推第四段 ⇒ 页面上印「生成答案 0 ms」，
  //    而生成明明花了几秒 —— **那是个假数**（本仓：⛔ 不许印没有数据源的数）。
  // ⇒ 判据：`stagesOf` **读都不读** `total_ms`；传一个极大的 total 也不该多出一行。
  const rows = R.stagesOf({ rewrite_ms: 1, search_ms: 2, rerank_ms: 3, total_ms: 99999 });
  assert.strictEqual(rows.length, 3, '`total_ms` 一出现就多一行 ⇒ 又回到那个假的第四段了');
  assert.ok(!rows.some((r) => r.name.indexOf('生成') !== -1), '⛔ 不许有"生成答案"那一段');
});

test('🔴 缺字段 ⇒ `null`（⛔ 不编 0）', () => {
  assert.deepStrictEqual(R.stagesOf({}).map((r) => r.ms), [null, null, null]);
  const partial = R.stagesOf({ rewrite_ms: 10, search_ms: 20 });
  assert.deepStrictEqual(partial.map((r) => r.ms), [10, 20, null], '缺的那一段必须是 null');
});

test('🔴 `fmtMs(null)` ⇒ `—`（⛔ 绝不印 `0 ms`）', () => {
  // ⚠️ 印 0ms 会被读成"这一段没花时间"，而真相是"这个数没有数据源"。
  assert.strictEqual(R.fmtMs(null), '—');
  assert.strictEqual(R.fmtMs(undefined), '—');
  assert.strictEqual(R.fmtMs(NaN), '—');
  assert.strictEqual(R.fmtMs(0), '0 ms', '0 是【真值】的时候要照印 —— 比如重排在 线上就是 0');
  assert.strictEqual(R.fmtMs(1500), '1.50 s');
});

/* ══════════════ 4 · 三个指标 ══════════════ */

test('`metricsOf`：命中数 = `docs` 长度 · 引用数 = `sources` 长度', () => {
  const m = R.metricsOf({ docs: [1, 2, 3], sources: [{}, {}], timing: { total_ms: 1234 } });
  assert.strictEqual(m.hits, '3');
  assert.strictEqual(m.cited, '2');
  assert.strictEqual(m.total, '1.23 s');
});

test('`metricsOf`：缺值 ⇒ `—`（⛔ 不许印 `undefined`）', () => {
  const m = R.metricsOf({});
  assert.deepStrictEqual(m, { hits: '—', cited: '—', total: '—' });
});

/* ══════════════ 5 · 本次花费 ══════════════ */

const RECORDS = [
  { thread_id: 't-me', purpose: 'answer_generation', total_tokens: 300, total_cost: 0.0005 },
  { thread_id: 't-me', purpose: 'query_rewrite',     total_tokens: 100, total_cost: 0.0002 },
  { thread_id: 't-me', purpose: 'embedding_query',   total_tokens: 50,  total_cost: 0.0001 },
  { thread_id: 't-other', purpose: 'answer_generation', total_tokens: 999, total_cost: 9 },
];

test('🔴🔴⛔ 不许按 `thread_id` 筛 —— 后端【不记】调用者传的那个（实测撞出来的）', () => {
  // 🔴 2026-10-12 实测：本页传 `?thread_id=rag-page-<uuid>`，而账本里写进去的是
  //      `answer_generation → default` · `embedding → system` · `query_rewrite → unknown`
  //      —— **三个不同的 thread，一个都不是调用者传的那个**。
  //    ⇒ 按 thread_id 筛**永远筛不到**，页面那三格会恒为 `—`（就是这个坑把花费那格留空的）。
  // ⚠️ 所以现在**只按端点已做过的用户过滤**加总 —— 端点本来就只回当前用户的记录。
  // 📌 这条用例把"现在就是这个语义"钉住：**别的 thread 的行也照样算进来**。
  //    等  修好（账本记调用者的 thread），再改成按 thread 筛，并**在这条上写出反证**。
  const c = R.costOf(RECORDS);
  assert.strictEqual(c.calls, 4, '四条都要算进来（含 thread_id 不是本页那条的）');
  assert.strictEqual(c.cost, 9.0008);
});

test('🔴 按 `purpose` 把 embedding 与 LLM 分开（含 embed 的算 embedding）', () => {
  const c = R.costOf(RECORDS);
  assert.strictEqual(c.embedding, 50);
  assert.strictEqual(c.llm, 400 + 999, '1200 里那 999 是另一条 thread 的 —— 现在也先算进来（见上一条）');
});

test('🔴 一条都没筛到 ⇒ 三个都是 `null`，⛔ 不是 0', () => {
  // ⚠️ 画 0 会被读成"这次没花钱"，而真相是"还没查到"。
  assert.deepStrictEqual(R.costOf([]),
    { llm: null, embedding: null, cost: null, calls: 0 });
  assert.deepStrictEqual(R.costOf([]),
    { llm: null, embedding: null, cost: null, calls: 0 });
  assert.deepStrictEqual(R.costOf(null),
    { llm: null, embedding: null, cost: null, calls: 0 });
});

test('🔴 `fmtCost(null)` ⇒ `—`；很小的钱⛔ 不许印成 `¥0.00`', () => {
  assert.strictEqual(R.fmtCost(null), '—');
  assert.strictEqual(R.fmtCost(undefined), '—');
  assert.strictEqual(R.fmtCost(0.0001), '¥0.0001', '4 位就够它了 —— 印成 0.00 等于抹掉');
  assert.strictEqual(R.fmtCost(0.0123), '¥0.0123');
});

/* ══════════════ 6 · 档位表（跨语言那件事的另一半） ══════════════ */

test('🔴 四档里【不可用】的正好是需要重排的那两档', () => {
  // ⚠️ 为什么钉：本服务镜像**不装重排依赖**（`app/Dockerfile:73` 构建期过滤掉
  //    `sentence-transformers`）⇒ `accurate` / `full` 在 线上**点了会 500**。
  //    页面上⛔ 不许把它们做成可点的按钮。
  const off = R.MODES.filter((m) => !m.ok).map((m) => m.id).sort();
  assert.deepStrictEqual(off, ['accurate', 'full']);
  assert.deepStrictEqual(R.MODES.filter((m) => m.ok).map((m) => m.id),
    ['accurate_norerank', 'fast']);
});

test('🔴 最下面那档（默认选中的）必须是 `accurate_norerank` —— 它不依赖 torch', () => {
  // ⚠️ 接口自己的默认值就是它（`api_v1_rag.py` 的 `SearchMode = "accurate_norerank"`）
  //    ⇒ 页面若默认选 `accurate`，访客第一下就撞 500。
  assert.strictEqual(R.MODES[0].id, 'accurate_norerank');
  assert.strictEqual(R.MODES[0].ok, true);
});

/* ══════════════ 7 · 结构型守卫（同 `session` / `cred` 那两份） ══════════════ */

test('🔴🔴 本文件往 window 上只许留【一个】键 —— ⛔ 多一个都会与页面顶层绑定撞车', () => {
  // ⚠️ 2026-10-10 实测过：共用脚本顶层写 `function key()` ⇒ 与页面那句顶层 `const key`
  //    撞上 ⇒ `SyntaxError: Identifier 'key' has already been declared`
  //    ⇒ **整段内联脚本一行都不执行**，而 982 条 pytest + 283 条 node **全绿**。
  //    ⇒ 整份包在 IIFE 里，只留 `RagRag` 一个键。
  const sandbox = { module: undefined, window: {}, console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  vm.runInContext(SRC, sandbox);
  const added = Object.keys(sandbox).filter((k) => !before.has(k));
  assert.deepStrictEqual(added, ['RagRag'],
    `\`rag.js\` 往 window 上多挂了：${added.join(', ')} ⇒ 与页面自己的顶层绑定撞车的面又回来了`);
});

test('`thread_id` 带 `rag-page-` 前缀（方便在 cost/records 里一眼认出来）', () => {
  assert.match(R.newThreadId(), /^rag-page-/);
});

/* ══════════════ 8 · 引用 ↔ 检索到的资料（需求方 2026-10-12 裁的那条） ══════════════ */

test('🔴🔴 没成文（只有 docs）⇒ 显示【检索到的资料】，⛔ 不是空着', () => {
  // ⚠️ 需求原话：「当我只打开 citations、没有打开 generate_answer，我点击提问，
  //    右侧的"引用"没有内容显示。**这个是错的**…… 用户只想知道本地知识库通过 RAG 检索找资料。」
  const r = R.referenceRows({
    docs: [{ source: 'B-01.md', content: '保修制度正文', from: 'vector', rrf_score: 0.03 }],
    sources: [],
  });
  assert.strictEqual(r.kind, 'docs');
  assert.strictEqual(r.title, '检索到的资料');
  assert.strictEqual(r.label, '1 条');
  assert.strictEqual(r.rows.length, 1);
  assert.ok(r.rows[0].head.includes('B-01.md'));
  assert.strictEqual(r.rows[0].body, '保修制度正文');
});

test('🔴 成文了（有 sources）⇒ 显示【引用】，带 [来源 n]', () => {
  const r = R.referenceRows({
    docs: [{ source: 'x', content: 'y' }],
    sources: [{ index: 2, source: 'B-03.md', similarity: 0.44, content_preview: '正文' }],
  });
  assert.strictEqual(r.kind, 'citation');
  assert.strictEqual(r.title, '引用');
  assert.ok(r.rows[0].head.startsWith('[2] B-03.md'), r.rows[0].head);
});

test('⚠️ 两边都空 ⇒ rows 是空的（由页面去画空态），⛔ 不编一条假的', () => {
  const r = R.referenceRows({ docs: [], sources: [] });
  assert.deepStrictEqual(r.rows, []);
  assert.strictEqual(r.label, '0 条');
});

test('⚠️ 缺值的相似度/相关度一律 `—`（⛔ 不印 undefined）', () => {
  const a = R.referenceRows({ sources: [{ index: 1, source: 's', content_preview: 'p' }] });
  assert.ok(!/undefined|NaN/.test(a.rows[0].head), a.rows[0].head);
  const b = R.referenceRows({ docs: [{ source: 's', content: 'c' }] });
  assert.ok(!/undefined|NaN/.test(b.rows[0].head), b.rows[0].head);
});

test('🔴 语料自带的 `**` 在渲染前要去掉（⛔ 但一个字都不许改）', () => {
  // ⚠️ 实测：检索回来的正文里印着「**谁在什么时候做什么保养**」—— 那是语料自己的 markdown。
  //    本仓那条 markdown 门**只扫页面 HTML**，扫不到数据里带进来的 ⇒ 只能在这里拦。
  const raw = '它规定**谁在什么时候做什么保养**，以及不做会怎样。';
  assert.strictEqual(R.stripMd(raw), '它规定谁在什么时候做什么保养，以及不做会怎样。');
  // ⛔ 只去记号：文字一个字不动
  const r = R.referenceRows({ docs: [{ source: 'a.md', content: raw }] });
  assert.ok(!r.rows[0].body.includes('**'));
  assert.ok(r.rows[0].body.includes('谁在什么时候做什么保养'), '把内容也改掉了 ⇒ 违反「真数据」');
});

/* ══════════════ 9 · 接口切换器（需求方 2026-10-12 定） ══════════════ */

test('🔴 这一页只列 2 个可切接口 —— ⛔ 不列被 mode 覆盖的那三条', () => {
  // ⚠️ 判据（读代码核过）：hybrid_search = mode=fast · rewrite_search = mode=accurate_norerank
  //    rerank_search = mode=accurate/full ⇒ 都被 /rag/search 覆盖 ⇒ 不列。
  const ids = R.ENDPOINTS.map((e) => e.id);
  assert.deepStrictEqual(ids, ['search', 'pg']);
  const paths = R.ENDPOINTS.map((e) => e.path);
  for (const bad of ['hybrid_search', 'rewrite_search', 'rerank_search']) {
    assert.ok(!paths.some((p) => p.includes(bad)), `${bad} 被 mode 覆盖了，⛔ 不该单独列`);
  }
});

test('🔴 `mode`/`thread_id` **只有 `/rag/search` 收** —— pg_search 的路径上⛔ 不许带', () => {
  const pg = R.endpointOf('pg');
  const p2 = R.pathFor(pg, 'fast', 'th-1');
  assert.strictEqual(p2, '/api/v1/rag/pg_search', '给 pg_search 塞了它不收的参数');
  const se = R.endpointOf('search');
  const p1 = R.pathFor(se, 'fast', 'th-1');
  assert.ok(p1.includes('mode=fast') && p1.includes('thread_id=th-1'), p1);
});

test('⚠️ 认不出的 id ⇒ 回第一条，⛔ 不抛（页面不该因一个值坏了就整页白）', () => {
  assert.strictEqual(R.endpointOf('不存在').id, 'search');
});

test('🔴 `has` 说清了每条接口产不产出那几样（页面据此决定哪些块有意义）', () => {
  assert.deepStrictEqual(R.endpointOf('pg').has,
    { mode: false, switches: false, timing: false, answer: false });
  assert.deepStrictEqual(R.endpointOf('search').has,
    { mode: true, switches: true, timing: true, answer: true });
});

test('🔴 `docs` 两种字段都要认 —— `rrf_score`（rag/search）与 `similarity`（pg_search）', () => {
  // ⚠️ 真浏览器上照出来的：pg_search 返回的 docs 给的是 `similarity`，而我原来只认 `rrf_score`
  //    ⇒ 那一格永远是 `—`，而**页面上看不出来**是"没有"还是"我没取"。
  const a = R.referenceRows({ docs: [{ source: 'a.md', content: 'x', rrf_score: 0.031, from: 'both' }] });
  assert.ok(a.rows[0].head.includes('相关度 0.031'), a.rows[0].head);
  assert.ok(a.rows[0].head.includes('来源 both'), a.rows[0].head);
  const b = R.referenceRows({ docs: [{ source: 'b.md', content: 'y', similarity: 0.56 }] });
  assert.ok(b.rows[0].head.includes('相似度 0.56'), b.rows[0].head);
  assert.ok(!b.rows[0].head.includes('来源'), '没有 from 就别印「来源」那一截');
});
