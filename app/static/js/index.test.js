'use strict';
/* `index.js`（**总览 / 首页**）的用例 —— ⛔ 零 npm 依赖（只 node:test + node:assert）。
   跑法：node --test app/static/js/index.test.js（已进 ci.yml 的 run 块，glob）

⚠️ 本文件测的是**纯逻辑**；"页面画得对不对"归 `app/tests/test_index_page.py` 与真浏览器。 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const R = require('./index.js');
const SRC = fs.readFileSync(path.join(__dirname, 'index.js'), 'utf8');

/* ══════════════ 1 · 真调那一条：路径与请求体 ══════════════ */

test('🔴 真调那条路径必须以 `/api/v1` 开头（本仓那条前缀事故：`/agent/pending` 少了前缀 ⇒ 页面 404 而没人红）', () => {
  assert.ok(R.SEARCH_PATH.startsWith('/api/v1'), `实际：${R.SEARCH_PATH}`);
  assert.strictEqual(R.searchPath(), '/api/v1/rag/search?mode=accurate_norerank');
});

test('🔴 请求体里两个开关都要显式打开 —— 它们【默认是关的】', () => {
  // ⚠️ 本仓那条坑：`generate_answer` / `citations` 默认 `false` ⇒ 不给就**没有答案、没有引用**，
  //    而页面上的格子会空着 —— 看起来像"后端坏了"。
  const b = R.buildBody();
  assert.strictEqual(b.generate_answer, true, '没开 ⇒ 返回里没有 answer');
  assert.strictEqual(b.citations, true, '没开 ⇒ 答案里没有引用');
  assert.ok(b.question && typeof b.question === 'string', '没问题就没什么可检索的');
});

test('默认档是 accurate_norerank（不依赖重排模型 ⇒ 任何机器上都跑得起来）', () => {
  assert.strictEqual(R.MODE, 'accurate_norerank');
});

/* ══════════════ 2 · 🔴 真数：从 /openapi.json 现算 ══════════════ */

test('🔴🔴 `/openapi.json` 那条路径【必须拼出来】，⛔ 不许是字面量', () => {
  // ⚠️ 为什么：页面守卫要求字面量以 `/api/v1` 开头，而 `/openapi.json` 不在它下面
  //    ⇒ 硬写会被拦下，**而那条红会把人引向改门**（门是对的）。同 `ops.js` 的 `ROOT_NAMES`。
  assert.strictEqual(R.rootPath('openapi'), '/openapi.json');
  assert.ok(!SRC.includes("'/openapi.json'"), '源码里出现了字面量 —— 会被页面守卫拦下');
  assert.ok(!SRC.includes('"/openapi.json"'), '源码里出现了字面量（双引号那种）');
});

test('🔴🔴 形状不对时回 `null`，⛔ **绝不回 0** —— `0 条接口` 是一句假话', () => {
  // 反证：把 `openapiCounts` 里的 `return null` 改成 `return {paths: 0, groups: 0}`
  //       ⇒ 页面上会印「0 条接口 · 0 组」（服务明明有接口，只是这次没读到）。
  for (const bad of [null, undefined, {}, { paths: {} }, { paths: 'x' }, 'nope', 42]) {
    assert.strictEqual(R.openapiCounts(bad), null, `入参 ${JSON.stringify(bad)} 该回 null`);
  }
});

test('正常形状 ⇒ 数出「多少条接口 · 多少组」（组 = tag 去重）', () => {
  const spec = {
    paths: {
      '/a': { get: { tags: ['检索'] }, post: { tags: ['检索', '调试'] } },
      '/b': { get: { tags: ['公开'] } },
      '/c': { get: {} },                       // ⚠️ 没有 tags ⇒ 不计入组，但要计入条数
    },
  };
  // 三个不同的 tag（检索 / 调试 / 公开）⇒ 3 组；`/c` 没有 tags ⇒ 计入条数、不计入组。
  assert.deepStrictEqual(R.openapiCounts(spec), { paths: 3, groups: 3 });
});

/* ══════════════ 3 · 缺值口径：画「—」，⛔ 不画 0 ══════════════ */

test('🔴 缺值一律「—」；**真值就是 0 时照画 `0`**（两者别混）', () => {
  assert.strictEqual(R.num(null), '—');
  assert.strictEqual(R.num(undefined), '—');
  assert.strictEqual(R.num(NaN), '—');
  assert.strictEqual(R.num(Infinity), '—');
  assert.strictEqual(R.num(''), '—');
  assert.strictEqual(R.num('   '), '—');
  assert.strictEqual(R.num(0), '0', '真值是 0 的时候要照画 —— ⛔ 别把它也画成「—」');
  assert.strictEqual(R.num(62), '62');
  assert.strictEqual(R.num('7'), '7');
});

test('🔴 条数数出 0 行时画「—」，⛔ 不谎报「0 条能力」', () => {
  assert.strictEqual(R.countRows([]), '—');
  assert.strictEqual(R.countRows(null), '—');
  assert.strictEqual(R.countRows([1, 2, 3, 4, 5, 6, 7]), '7');
});

/* ══════════════ 4 · 把真返回映射到页面上那一串格子 ══════════════ */

test('把一次真返回映到格子上：字段名都是真返回里有的', () => {
  const payload = {
    query: '在保设备上门收不收工时费',
    pipeline: { original_query: '在保设备上门收不收工时费', rewrite_enabled: true, expand_enabled: false, bm25_enabled: true, rerank_enabled: false },
    timing: { rewrite_ms: 512.3, search_ms: 88.1, rerank_ms: 0, total_ms: 976.4 },
    docs: [{ id: 1 }, { id: 2 }],
    answer: '在保设备上门不收工时费[来源1]',
    sources: [{ index: 1, id: 1, source: 'B-01.md', similarity: 0.82 }],
    mode: 'accurate_norerank',
    requested_by: 'u-x',
  };
  const m = {};
  for (const f of R.respFields(payload)) { m[f.id] = f.text; }

  assert.strictEqual(m['v-query'], '在保设备上门收不收工时费');
  assert.strictEqual(m['v-rewrite'], 'true');
  assert.strictEqual(m['v-expand'], 'false', '布尔假要画 `false`，⛔ 不是「—」—— 那是真值');
  assert.strictEqual(m['v-t-total'], '976.4 ms');
  assert.strictEqual(m['v-docs'], '2 条');
  assert.strictEqual(m['v-sources'], '1 条');
  assert.strictEqual(m['v-mode'], 'accurate_norerank');
  assert.strictEqual(m['v-by'], 'u-x');
});

test('🔴 `generate_answer=false` 的返回【没有】answer / sources ⇒ 那两格画「—」，⛔ 不编', () => {
  // ⚠️ 真返回里这两个键**只在成文时才有**（`RagSearchResponse` 把它们声明成可选 —— 那是拿一个 500 换来的）。
  const m = {};
  for (const f of R.respFields({ query: 'q', pipeline: {}, timing: {}, docs: [], mode: 'fast', requested_by: 'x' })) {
    m[f.id] = f.text;
  }
  assert.strictEqual(m['v-answer'], '—');
  assert.strictEqual(m['v-sources'], '— 条');
  assert.strictEqual(m['v-t-total'], '—', 'timing 里没有那个键 ⇒ 画「—」，⛔ 别画 0 ms');
  assert.strictEqual(m['v-rewrite'], '—', 'pipeline 里没有那个键 ⇒ 画「—」');
});

/* ══════════════ 4b · 🆕 那一排「问它一句」（补） ══════════════ */

test('🔴 问题清单：**最后一条是【故意】语料里没有的**，而且**标出来了**', () => {
  // ⚠️ 为什么这条值钱：它是**一个有意的演示**（"没依据就不编"）——
  //    ⛔ **不标出来就是陷阱**（访客会以为它坏了）；标出来就是一个**能力**。
  // ⚠️ 反证：把那条的 `inCorpus: false` 改成 `true`（或把那一项删掉）⇒ 本条红。
  const qs = R.QUESTIONS;
  assert.ok(qs.length >= 2, `问题太少（${qs.length} 条）—— 这一排就没有意义了`);
  assert.strictEqual(qs[qs.length - 1].inCorpus, false,
    '最后一条该是"语料里没有"的那条（页面上明写着它会答"无法回答"）');
  const out = qs.filter((x) => !x.inCorpus);
  assert.strictEqual(out.length, 1, `"语料里没有"的该只有一条，实际 ${out.length} 条`);
  for (const x of qs) {
    assert.ok(x.q && typeof x.q === 'string' && x.q.trim(), `有一条问题名是空的：${JSON.stringify(x)}`);
    assert.strictEqual(typeof x.inCorpus, 'boolean', `每一条都要带 inCorpus：${JSON.stringify(x)}`);
  }
});

test('🔴 默认那一句 = **第一条**（⇒ 访客什么都不做也能跑）', () => {
  // ⚠️ 反证：把 `DEFAULT_QUESTION` 改成 `''` ⇒ 本条红；而页面上会变成"没挑就没得跑"。
  assert.strictEqual(R.DEFAULT_QUESTION, R.QUESTIONS[0].q);
  assert.strictEqual(R.QUESTIONS[0].inCorpus, true,
    '默认那一句必须是**语料答得出**的 —— ⛔ 别让访客第一眼看到"它答不出来"');
});

test('传了哪句就问哪句；⛔ 传空/不传 ⇒ 回落到默认那句（⛔ 不发一个没问题的请求）', () => {
  assert.strictEqual(R.buildBody('设备多久保养一次').question, '设备多久保养一次');
  assert.strictEqual(R.buildBody('').question, R.DEFAULT_QUESTION);
  assert.strictEqual(R.buildBody('   ').question, R.DEFAULT_QUESTION);
  assert.strictEqual(R.buildBody(null).question, R.DEFAULT_QUESTION);
  assert.strictEqual(R.buildBody().question, R.DEFAULT_QUESTION);
});

/* ══════════════ 4c · 🆕 docs[] / sources[] 展开成真结构（填掉左下那块空白） ══════════════ */

test('🔴 `docs[]` 展开成真结构：一条 3 行 · 键名都是真返回里有的', () => {
  // 实测的真键（起真服务调出来的）：docs[0] = content / from / id / rrf_score / source
  const payload = {
    docs: [
      { id: 812, source: 'B-01-售后服务与保修制度.md', rrf_score: 0.0321, from: 'hybrid', content: '保修期内非人为损坏不收备件费……' },
      { id: 807, source: 'C-01-常见问题.md', rrf_score: 0.031, from: 'hybrid', content: '……' },
    ],
  };
  const lines = R.arrayLines('docs', payload);
  assert.strictEqual(lines.length, 2 * 3, `两条 doc 该出 6 行，实际 ${lines.length}`);
  assert.ok(lines[0].includes('"id": 812') && lines[0].includes('B-01-售后服务与保修制度.md'), `首行：${lines[0]}`);
  assert.ok(lines[1].includes('"rrf_score": 0.0321') && lines[1].includes('"from": "hybrid"'), `次行：${lines[1]}`);
  assert.ok(lines[2].includes('"content"'), `三行：${lines[2]}`);
});

test('🔴 超过 3 条 ⇒ **如实说"还有 N 条"**（⛔ 不是假装就这么多）', () => {
  // ⚠️ 为什么：页面上只放得下 3 条，而**返回里可能有更多** ——
  //    不说 = 让人以为"就召回了 3 条"（那是谎报召回量）。
  const many = { docs: Array.from({ length: 7 }, (_, i) => ({ id: i + 1, source: 's.md', rrf_score: 0.1, from: 'x', content: 'c' })) };
  const lines = R.arrayLines('docs', many);
  assert.strictEqual(lines.length, R.ARRAY_CAP * 3 + 1, `该是 3 条 × 3 行 + 1 行"还有"`);
  assert.ok(lines[lines.length - 1].includes('还有 4 条'), `末行该说还有 4 条：${lines[lines.length - 1]}`);
});

test('缺值照画「—」；一条都没有 ⇒ 出空数组（⛔ 不编假的条目）', () => {
  const lines = R.arrayLines('sources', { sources: [{ index: 1, id: 9, source: 'a.md' }] });
  assert.strictEqual(lines.length, 3);
  assert.ok(lines.join(' ').includes('"similarity": —'), '融合链上 similarity 本来就缺 ⇒ 画「—」，⛔ 别编一个分');
  assert.deepStrictEqual(R.arrayLines('docs', { docs: [] }), []);
  assert.deepStrictEqual(R.arrayLines('docs', {}), []);
  assert.deepStrictEqual(R.arrayLines('docs', null), []);
});

test('空载荷不抛（页面拿到怪东西时也只会是「—」，⛔ 不是整页崩）', () => {
  assert.doesNotThrow(() => R.respFields(null));
  assert.doesNotThrow(() => R.respFields(undefined));
  const ids = R.respFields({}).map((f) => f.id);
  assert.ok(ids.includes('v-query') && ids.includes('v-answer'), '格子还是那一串（只是值都是「—」）');
});

test('🔴🔴 预览必须**折成一行**：内容里的换行⛔ 不许漏出去（那一栏是 `white-space: pre`）', () => {
  // ⚠️ 这条是拿一个真缺陷换来的（2026-10-13 真浏览器上量出来的）：取回来的 `content` / `answer`
  //    里**带换行**（语料是多段 Markdown），而那一栏是 `white-space: pre` ⇒ **一个 `\n` 就多撑一行**
  //    ⇒ 整块从 698 px 涨到 1005 px，而**没有任何门会红**。⇒ 一律折叠成单空格。
  // ⚠️ 反证：把 `clip` 里的 `.replace(/\s+/g, ' ')` 去掉 ⇒ 本条立刻红。
  const dirty = '第一段。\n\n第二段\t有制表符。   还有连续空格。';
  const one = R.clip(dirty, 200);
  assert.ok(!/[\n\r\t]/.test(one), `换行/制表符漏出来了：${JSON.stringify(one)}`);
  assert.ok(!/  /.test(one), `连续空格漏出来了：${JSON.stringify(one)}`);
  assert.strictEqual(one, '第一段。 第二段 有制表符。 还有连续空格。');

  const row = R.arrayLines('docs', { docs: [{ id: 1, source: 'a.md', rrf_score: 0.1, from: 'x', content: '甲\n\n乙' }] });
  assert.ok(!row.join('').includes('\n'), '展开出来的行里⛔ 不许带换行（那会撑破版面）');
});

test('🔴🔴 答案里的 `**` 必须去掉 —— 否则**星号会原样印在页面上**', () => {
  // ⚠️ 2026-10-13 真浏览器上看到的：答案带着 `**不收备件费**`（那是**模型自己写的 markdown**），
  //    而页面用 `textContent` 渲染 ⇒ 星号照印。
  //    🔴 而**页面那条 markdown 门扫不到它**：它扫的是 HTML 文件，这串是**运行时**才进 DOM 的
  //    （`页面-Agent自验收标准.md` §五之四 记的就是这个缝）⇒ 只能在这里钉。
  // ⚠️ 反证：把 `clip` 里的 `.replace(/\*\*/g, '')` 去掉 ⇒ 本条立刻红。
  const t = R.clip('保修期内替换的备件由青云精工提供，**不收备件费**，只收上门费。', 200);
  assert.ok(!t.includes('**'), `星号漏出去了：${t}`);
  assert.ok(t.includes('不收备件费'), '只该去掉记号，⛔ 不许把内容一起去掉');
});

test('答案那一格只放开头（整段塞进一行会把版面撑爆）', () => {
  const long = '甲'.repeat(200);
  const t = R.respFields({ answer: long }).find((f) => f.id === 'v-answer').text;
  assert.ok(t.endsWith('…'), `该带一个截断标记，实际结尾：${t.slice(-3)}`);
  assert.ok(t.length <= R.ANSWER_HEAD + 1, `该不超过 ${R.ANSWER_HEAD} 字 + 一个「…」，实际 ${t.length}`);
  const short = R.respFields({ answer: '短答案' }).find((f) => f.id === 'v-answer').text;
  assert.strictEqual(short, '短答案', '短的就原样 —— ⛔ 别给它也加个「…」');
});

/* ══════════════ 5 · 🔴🔴 全局污染守卫（本仓真栽过一次，死了 8 个页面） ══════════════ */

test('🔴🔴 本文件对外**只许留 `RagIndex` 一个全局键**（多一个都可能撞死页面）', () => {
  // ⚠️ 2026-10-10 实测：共用脚本顶层写 `function key()` ⇒ 与 8 个页面各自的 `const key` 撞上
  //    `SyntaxError: Identifier 'key' has already been declared` ⇒ **整段内联脚本一行都不执行，
  //    页面全死**，而 982 条 pytest + 283 条 node **全绿**（它们只读文本，不解析 JS）。
  const vm = require('node:vm');
  const sandbox = { window: {}, console, fetch: () => Promise.reject(new Error('不该发请求')) };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  vm.runInContext(SRC, sandbox);
  const added = Object.keys(sandbox).filter((k) => !before.has(k));
  assert.deepStrictEqual(added, ['RagIndex'],
    '本文件往 window 上多挂了东西 ⇒ 与页面自己的顶层绑定撞车的面又回来了');
});
