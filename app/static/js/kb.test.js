'use strict';
/* `kb.js` 的用例 —— ⛔ 零 npm 依赖（只 node:test + node:assert）。 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, 'kb.js'), 'utf8');
const K = require('./kb.js');

test('🔴 两条端点都必须带 `/api/v1` 前缀', () => {
  // ⚠️ 页面走 helper ⇒ `test_web_pages.py` 对它**空过** ⇒ 前缀保护只落在本文件。
  assert.match(K.LIST_PATH, /^\/api\/v1\//);
  assert.match(K.FULL_PATH, /^\/api\/v1\//);
});

test('`buildListPath`：scope 与 limit 都进 query，且被编码', () => {
  const p = K.buildListPath('public', 50);
  assert.ok(p.startsWith('/api/v1/rag/documents?'), p);
  assert.ok(p.includes('scope=public') && p.includes('limit=50'), p);
  assert.strictEqual(K.buildListPath('mine').includes('limit=200'), true, '不传 limit ⇒ 默认 200');
});

test('🔴 只许两个 scope 值（与后端 pattern 一致）', () => {
  assert.deepStrictEqual(K.SCOPES.map((s) => s.id), ['public', 'mine']);
});

test('🔴 搜索：命中还要**留住分组**（⛔ 不摊平成一坨）', () => {
  const groups = [
    { name: '流程 / 制度', count: 2, docs: [{ source: 'B-01-保修.md', preview: '保修制度' }, { source: 'B-03-调拨.md', preview: '备件调拨' }] },
    { name: '常见问题', count: 1, docs: [{ source: 'C-04-备件.md', preview: '非常备件多久到' }] },
  ];
  const hit = K.filterGroups(groups, '备件');
  assert.strictEqual(hit.length, 2, '两个分组都该还在 —— 命中后⛔ 不许把类别那一层丢掉');
  assert.strictEqual(hit[0].docs.length, 1);
  assert.strictEqual(hit[0].count, 1, 'count 要跟着过滤后的数走');
});

test('⚠️ 搜索：空词 ⇒ 原样返回（⛔ 不是空）', () => {
  const groups = [{ name: 'x', count: 1, docs: [{ source: 'a' }] }];
  assert.deepStrictEqual(K.filterGroups(groups, '   '), groups);
  assert.deepStrictEqual(K.filterGroups(groups, null), groups);
});

test('⚠️ 搜索：全不中 ⇒ 空数组；空分组要去掉', () => {
  const groups = [
    { name: 'A', count: 1, docs: [{ source: 'aaa', preview: '' }] },
    { name: 'B', count: 1, docs: [{ source: 'zzz', preview: '' }] },
  ];
  assert.deepStrictEqual(K.filterGroups(groups, '不存在的词'), []);
  const one = K.filterGroups(groups, 'aaa');
  assert.strictEqual(one.length, 1, '没命中的那个分组要去掉');
});

test('`buildFullPath`：id 进路径且被编码', () => {
  assert.strictEqual(K.buildFullPath(149), '/api/v1/rag/documents/149');
});

test('🔴 `buildDeletePath`：**与取整篇同一个 url**（只差 method）—— 且必须存在', () => {
  // ⚠️ 这条钉两件事：① 路径对；② **这个函数真的导出着**。
  //    上一版页面里删一篇是**手拼**字符串的（`'/api/v1/rag/documents/' + id`）——
  //    那等于页面里藏着第二份路径真相，前缀改了就有一处会漂。
  assert.strictEqual(typeof K.buildDeletePath, 'function', '页面按行删要用它，⛔ 别让它在别处手拼');
  assert.strictEqual(K.buildDeletePath(149), '/api/v1/rag/documents/149');
  assert.strictEqual(
    K.buildDeletePath(149), K.buildFullPath(149),
    '后端就是同一个 url（`{doc_id}` 传任一块的 id，删的是整篇）—— 两边漂了就是有一个改了另一个没改'
  );
  assert.strictEqual(K.buildDeletePath('a/b'), '/api/v1/rag/documents/a%2Fb');
});

test('`summaryOf`：缺值画 `—`（⛔ 不印 undefined）', () => {
  const s = K.summaryOf('public', {});
  assert.ok(!/undefined|NaN/.test(s), s);
  const t = K.summaryOf('public', { total: 74, groups: [1, 2, 3, 4, 5] });
  assert.strictEqual(t, '公用库 · 74 篇 · 5 类');
  assert.ok(K.summaryOf('public', { total: 200, groups: [], truncated: true }).includes('只显示了一部分'));
});

test('🔴🔴 本文件往 window 上只许留【一个】键', () => {
  const sandbox = { module: undefined, window: {}, console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  vm.runInContext(SRC, sandbox);
  const added = Object.keys(sandbox).filter((k) => !before.has(k));
  assert.deepStrictEqual(added, ['RagKb'],
    `\`kb.js\` 往 window 上多挂了：${added.join(', ')} ⇒ 与页面自己顶层绑定撞车的面又回来了`);
});
