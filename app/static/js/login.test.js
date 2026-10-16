'use strict';
/* `login.js` 的用例 —— ⛔ 零 npm 依赖（只 node:test + node:assert）。
   跑法：`node --test app/static/js/login.test.js`

🔴 **重点全在 `safeNext`**（开放重定向那条）。⚠️ 那一族的判据**必须成对写**：
   「`//evil.example` 要被拦」写完，还得有「`/ops` 要放行」——
   否则把实现改成 **`return HOME`** 恒返回，被拦的那些照样绿（本仓最恨的"用一把没在测的尺子"）。
*/
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, 'login.js'), 'utf8');

/** 造一个最小环境跑 `login.js`，把 `window.RagLogin` 与"漏出来的全局键"交出来。
 *
 * 🔴 **必须把 `URLSearchParams` 放进去**（2026-10-15 实测栽过一次）：
 *    第一版沙箱只有 `window` 与 `console` ⇒ `login.js` 里那句 `new URLSearchParams(…)`
 *    **抛** ⇒ 走 `catch` ⇒ **每次都回 `/`**。
 *    ⇒ 于是那 10 条「**该拦下来**」的用例**全绿**，而它们是**因为错的原因**绿的
 *      （不是"拦住了"，是"压根没解析"）。
 *    ⇒ **是「放行」那一半抓出来的** —— 少了那一半，这个错会一直躺着。
 *    ⚠️ 教训：**只写"该拒绝"的用例 = 用一把没在测的尺子**（本仓：防空跑那一族）。
 */
function makeSandbox() {
  const sandbox = { window: {}, console, URLSearchParams };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  vm.runInContext(SRC, sandbox);
  return {
    L: sandbox.RagLogin,
    added: Object.keys(sandbox).filter((k) => !before.has(k)),
  };
}

const { L } = makeSandbox();

/* ══════════════ 1 · 放行的那一半（⛔ 少了这一半，把实现改成恒返回 `/` 也照样绿） ══════════════ */

test('站内路径 ⇒ **原样放行**', () => {
  assert.strictEqual(L.safeNext('?next=%2Fops'), '/ops');
  assert.strictEqual(L.safeNext('?next=/rag'), '/rag');
});

test('带查询串的站内路径 ⇒ 放行（查询串不该被吃掉）', () => {
  assert.strictEqual(L.safeNext('?next=%2Frag%3Fa%3D1'), '/rag?a=1');
});

test('别的参数一起出现 ⇒ 只取 `next`', () => {
  assert.strictEqual(L.safeNext('?a=1&next=%2Fkb&b=2'), '/kb');
});

/* ══════════════ 2 · 拦下来的那一半 ══════════════ */

test('🔴 **绝对地址** ⇒ 拦下来（开放重定向的正身）', () => {
  assert.strictEqual(L.safeNext('?next=https%3A%2F%2Fevil.example%2F'), '/');
  assert.strictEqual(L.safeNext('?next=http%3A%2F%2Fevil.example%2F'), '/');
});

test('🔴 **协议相对地址** `//host` ⇒ 拦下来（最容易被漏掉的一条）', () => {
  assert.strictEqual(L.safeNext('?next=%2F%2Fevil.example%2F'), '/');
});

test('🔴 `javascript:` 一类 ⇒ 拦下来', () => {
  assert.strictEqual(L.safeNext('?next=javascript%3Aalert(1)'), '/');
});

test('🔴 `\\/host`（反斜杠）⇒ 也要拦 —— 一部分浏览器把它当协议相对', () => {
  assert.strictEqual(L.safeNext('?next=%2F%5Cevil.example'), '/');
});

test('🔴 含控制字符（换行 / 制表）⇒ 拦下来', () => {
  assert.strictEqual(L.safeNext('?next=%2Fops%0A'), '/');
  assert.strictEqual(L.safeNext('?next=%2Fops%09x'), '/');
});

test('相对路径（不以 `/` 开头）⇒ 拦下来（⛔ 只收站内绝对路径）', () => {
  assert.strictEqual(L.safeNext('?next=ops'), '/');
  assert.strictEqual(L.safeNext('?next=..%2F..%2Fetc'), '/');
});

/* ══════════════ 3 · 没有 / 坏掉的时候 ══════════════ */

test('没有 `next` ⇒ 回默认首页', () => {
  assert.strictEqual(L.safeNext(''), '/');
  assert.strictEqual(L.safeNext('?'), '/');
  assert.strictEqual(L.safeNext('?other=1'), '/');
  assert.strictEqual(L.safeNext(null), '/');
  assert.strictEqual(L.safeNext(undefined), '/');
});

test('`next` 是空串 ⇒ 回默认首页（⛔ 别回一个空跳转）', () => {
  assert.strictEqual(L.safeNext('?next='), '/');
});

test('`HOME` 就是站内根', () => {
  assert.strictEqual(L.HOME, '/');
});

/* ══════════════ 3.5 · `loginHref` —— `safeNext` 的【另一半】 ══════════════ */

test('`loginHref` 把当前路径编成去登录页的链接', () => {
  assert.strictEqual(L.loginHref('/ops'), '/login?next=%2Fops');
  assert.strictEqual(L.loginHref('/rag?x=1'), '/login?next=%2Frag%3Fx%3D1');
});

test('🔴 **编出来的那个，解回去必须是它自己**（两个方向成对 —— 这是本文件最要紧的一条）', () => {
  for (const here of ['/', '/ops', '/rag?x=1', '/agent']) {
    const back = L.safeNext('?' + L.loginHref(here).split('?')[1]);
    assert.strictEqual(back, here, `\`${here}\` 编了再解不是它自己 ⇒ 登完会跑错地方`);
  }
});

test('🔴 传进来【不是】站内路径 ⇒ 退回首页（⛔ 别把外来串编进去）', () => {
  assert.strictEqual(L.loginHref('//evil.example/x'), '/login?next=%2F');
  assert.strictEqual(L.loginHref('https://evil.example/'), '/login?next=%2F');
  assert.strictEqual(L.loginHref(''), '/login?next=%2F');
  assert.strictEqual(L.loginHref(null), '/login?next=%2F');
});

/* ══════════════ 4 · 结构守卫：只许漏【一个】全局键 ══════════════ */

test('🔴 `login.js` 对外**只许漏一个全局键**（`RagLogin`）', () => {
  const { added } = makeSandbox();
  assert.deepStrictEqual(
    added, ['RagLogin'],
    '漏了别的全局键 ⇒ 会与页面自己的顶层绑定撞上 ⇒ `SyntaxError` ⇒ 整段内联脚本一行都不执行'
  );
});
