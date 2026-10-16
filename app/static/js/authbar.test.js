'use strict';
/* `authbar.js` 的用例 —— ⛔ 零 npm 依赖（只 node:test + node:assert）。
   跑法：`node --test app/static/js/authbar.test.js`

🔴 **全程用 `vm` 造一个假浏览器**（`document` / `location` / `RagAuth` / `RagLogin` 都是假的）——
   因为这块控件的价值**全在"没登录时怎么办"**，而那只有**把登录态弄成"没有"**才测得到。
   ⚠️ 那一族最容易写成**单向**的（只测"该露的露了"）⇒ 下面每组都**两头都测**。
*/
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, 'authbar.js'), 'utf8');

/** 一个假元素：够 `authbar.js` 用（`hidden` / `textContent` / 属性 / 一个监听）。 */
function mkEl() {
  return {
    hidden: true,
    textContent: '',
    attrs: {},
    on: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(t, f) { this.on[t] = f; },
  };
}

/**
 * 造一个假浏览器跑 `authbar.js`。
 *
 * @param {{ids?: string[], loggedIn?: boolean, budget?: object|null, here?: string,
 *          headerH?: number|(() => number), noHeader?: boolean}} opts
 *   `ids` ⇒ 页面上有哪些挂点（用来测"缺一块"那条）；`loggedIn` ⇒ `RagAuth.isLoggedIn()` 回什么；
 *   `budget` ⇒ `/agent/token/budget` 回什么（`null` = 回 500）；`here` ⇒ 假装当前在哪一页；
 *   `headerH` ⇒ 假页头量出来的高度（**收函数** ⇒ 可以在跑的过程中改，用来测"额度落地后再量一次"）；
 *   `noHeader` ⇒ 这一页根本没有 `.site-header`
 */
function makeSandbox(opts) {
  const o = opts || {};
  const ids = o.ids || ['auth-btn', 'auth-strip', 'auth-quota', 'auth-logout'];
  const els = {};
  ids.forEach((i) => { els[i] = mkEl(); });

  const calls = [];
  const navigated = [];
  const cssVars = {};                 // `--header-h` 写进去的那些（见 §7）
  const resized = [];                 // 注册在 `resize` 上的回调
  const headerH = (typeof o.headerH === 'function') ? o.headerH
    : () => (o.headerH === undefined ? 46 : o.headerH);

  const sandbox = {
    window: {},
    console,
    URLSearchParams,
    addEventListener: (t, f) => { if (t === 'resize') resized.push(f); },
    document: {
      getElementById: (i) => (i in els ? els[i] : null),
      documentElement: { style: { setProperty: (k, v) => { cssVars[k] = v; } } },
      querySelector: (sel) => {
        if (sel !== '.site-header' || o.noHeader) return null;
        return { getBoundingClientRect: () => ({ height: headerH() }) };
      },
    },
    location: {
      pathname: o.here === undefined ? '/ops' : o.here,
      search: '',
      assign: (u) => { navigated.push(u); },
    },
    /** ⚠️ 真的 `RagAuth` 是个对象；这里只用它那几个被调到的函数。 */
    RagAuth: {
      isLoggedIn: () => !!o.loggedIn,
      logout: () => { calls.push('logout'); },
      apiFetch: (url) => {
        calls.push(url);
        if (url !== '/api/v1/agent/token/budget') return Promise.reject(new Error('unexpected'));
        if (!o.budget) return Promise.resolve({ ok: false, json: () => Promise.resolve(null) });
        return Promise.resolve({ ok: true, json: () => Promise.resolve(o.budget) });
      },
    },
    /** ⚠️ 真的那个有 13 条单测打底；这里的编法**与它保持一致**（`/login?next=<编码后的路径>`）。 */
    RagLogin: {
      loginHref: (here) => '/login?next=' + encodeURIComponent(here),
    },
  };

  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  vm.runInContext(SRC, sandbox);
  const added = Object.keys(sandbox).filter((k) => !before.has(k));
  return { B: sandbox.RagAuthBar, els, calls, navigated, added, cssVars, resized };
}

/* ══════════════ 1 · 没登录（约定 ②：**不能**跑页面自己的起法） ══════════════ */

test('🔴 没登录 ⇒ 页头那颗按钮写「登录」（带 next=）· 下面那一行**整块藏起来** · **`onReady` 一次都不许调**', () => {
  const { B, els } = makeSandbox({ loggedIn: false, here: '/rag' });
  let ready = 0;
  const got = B.mount(() => { ready += 1; });

  assert.strictEqual(got, false);
  assert.strictEqual(els['auth-btn'].textContent, '登录');
  assert.strictEqual(els['auth-btn'].attrs.href, '/login?next=%2Frag');
  assert.strictEqual(
    els['auth-strip'].hidden, true,
    '没登录还露着额度那一行 ⇒ 它**一个数都没有**（本仓：⛔ 不许印没有数据源的数）'
  );
  assert.strictEqual(
    ready, 0,
    '没登录还把页面起起来了 ⇒ 它会拿空令牌打一串请求、收一串 401，而用户什么都没做错'
  );
});

test('「登录」那颗按钮的 next **跟着当前页走**（换一页就换一个）', () => {
  const a = makeSandbox({ loggedIn: false, here: '/agent' });
  a.B.mount();
  assert.strictEqual(a.els['auth-btn'].attrs.href, '/login?next=%2Fagent');
});

/* ══════════════ 2 · 已登录 ══════════════ */

test('🔴 已登录 ⇒ 页头那颗按钮**换成「已登录」**· 下面那一行露出来 · **`onReady` 调一次**', () => {
  const { B, els } = makeSandbox({ loggedIn: true, budget: null });
  let ready = 0;
  const got = B.mount(() => { ready += 1; });

  assert.strictEqual(got, true);
  assert.strictEqual(els['auth-btn'].textContent, '已登录');
  assert.strictEqual(
    els['auth-strip'].hidden, false,
    '登录了却藏着额度那一行 ⇒ 需求方要的"单独一行放额度"根本看不见'
  );
  assert.strictEqual(ready, 1);
});

test('🔴 已登录 ⇒ 那颗按钮**去掉 `href`**（⛔ 点「已登录」不该跳到登录页 —— 他已经登录了）', () => {
  const { B, els } = makeSandbox({ loggedIn: true, budget: null });
  els['auth-btn'].attrs.href = '/login';       // 假装 HTML 里那个初始值还在
  B.mount();
  assert.strictEqual(
    els['auth-btn'].attrs.href, undefined,
    '界面说反话：按钮写着「已登录」，点下去却去了登录页'
  );
});

/* ══════════════ 3 · 退出 ══════════════ */

test('🔴 点「退出」⇒ 清登录态 + 跳 `/login`（⛔ **不带 next** —— 那是"我不想再进来了"）', () => {
  const { B, els, calls, navigated } = makeSandbox({ loggedIn: true, budget: null });
  B.mount();
  assert.ok(els['auth-logout'].on.click, '「退出」那颗按钮没接上');
  els['auth-logout'].on.click();

  assert.deepStrictEqual(calls.filter((c) => c === 'logout'), ['logout']);
  assert.deepStrictEqual([...navigated], ['/login']);
});

test('`mount()` 调两次**不会**给「退出」挂两个监听（幂等）', () => {
  const { B, els, navigated } = makeSandbox({ loggedIn: true, budget: null });
  B.mount();
  const first = els['auth-logout'].on.click;
  B.mount();
  assert.strictEqual(els['auth-logout'].on.click, first, '第二次 mount 把监听换了 ⇒ 前一个还在，会点一下跳两次');
});

/* ══════════════ 4 · 额度（约定 ③：⛔ 不印没有数据源的数） ══════════════ */

test('登录后读一次额度 ⇒ 写进 `#auth-quota`', async () => {
  const { B, els, calls } = makeSandbox({
    loggedIn: true,
    budget: {
      remaining: 100, llm_tokens_today: 1, llm_remaining_today: 2,
      embedding_tokens_today: 3, embedding_remaining_today: 4,
    },
  });
  B.mount();
  await new Promise((r) => setImmediate(r));   // 放 `.then` 那一串跑完
  await new Promise((r) => setImmediate(r));

  assert.ok(calls.includes('/api/v1/agent/token/budget'));
  assert.match(els['auth-quota'].textContent, /LLM-tokens 1 · 今日还剩 2 tokens/);
  assert.match(els['auth-quota'].textContent, /Embedding-tokens 3 · 今日还剩 4 tokens/);
});

test('🔴 额度取不到 ⇒ `#auth-quota` **保持空**（⛔ 不许印 `NaN` / `undefined`）', async () => {
  const { B, els } = makeSandbox({ loggedIn: true, budget: null });
  B.mount();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(els['auth-quota'].textContent, '');
});

test('额度回体里**缺字段** ⇒ 那个数画「—」，⛔ 不画 0', async () => {
  const { B, els } = makeSandbox({ loggedIn: true, budget: { remaining: 1 } });
  B.mount();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.match(els['auth-quota'].textContent, /LLM-tokens —/);
  assert.doesNotMatch(els['auth-quota'].textContent, /LLM-tokens 0/);
});

/* ══════════════ 5 · 缺挂点时⛔ 不许抛（照 `cred.js` 那条规矩） ══════════════ */

test('页面上没有那几个挂点 ⇒ 照样不抛（只是什么都不做）', () => {
  const { B, els } = makeSandbox({ loggedIn: true, ids: [], budget: null });
  assert.doesNotThrow(() => B.mount());
  assert.deepStrictEqual(Object.keys(els), []);
});

test('页面少了「退出」那颗按钮 ⇒ 不抛（⛔ 别把缺挂点变成页面整体挂掉）', () => {
  const { B } = makeSandbox({ loggedIn: true, ids: ['auth-btn', 'auth-strip'], budget: null });
  assert.doesNotThrow(() => B.mount());
});

/* ══════════════ 7 · `--header-h` 必须跟着页头的【真实高度】走 ══════════════
   🔴 为什么这不是"顺手加的一条"：`--header-h` **不只是页头自己的高度** ——
      它还被 `html` 的 `scroll-padding-top` 与 `.side` / `.aside` / `.form` 的 sticky `top` 用着。
      登录后页头多出一行额度（比导航还长）⇒ 不跟着改，那些 sticky 块会**钻进页头底下**。
      ⚠️ 这一族**页面上看不出来**（不是报错，是"贴得有点高"）⇒ 只能靠这里钉住。 */

test('🔴 登录后页头是两行 ⇒ `--header-h` 要写成【量出来的】92px（⛔ 不是那个写死的 46）', () => {
  const { B, cssVars } = makeSandbox({ loggedIn: true, budget: null, headerH: 92 });
  B.mount();
  assert.strictEqual(
    cssVars['--header-h'], '92px',
    '还留着 46px ⇒ 那 4 处 sticky 定位全部**短掉一整行**'
  );
});

test('🔴 没登录也要量一次（那时页头是一行 ⇒ 回到 46px）', () => {
  const { B, cssVars } = makeSandbox({ loggedIn: false, headerH: 46 });
  B.mount();
  assert.strictEqual(cssVars['--header-h'], '46px');
});

test('🔴 额度那一串【落地之后】再量一次 —— 页头是**它进来之后**才变成两行的', async () => {
  let h = 46;                       // 刚 mount 时还只有「已登录」三个字 ⇒ 一行
  const { B, cssVars } = makeSandbox({
    loggedIn: true,
    headerH: () => h,
    budget: {
      remaining: 1, llm_tokens_today: 1, llm_remaining_today: 2,
      embedding_tokens_today: 3, embedding_remaining_today: 4,
    },
  });
  B.mount();
  assert.strictEqual(cssVars['--header-h'], '46px');
  h = 92;                            // 额度文字填进去 ⇒ 页头撑到两行
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(
    cssVars['--header-h'], '92px',
    '只在 mount 时量一次 ⇒ 量到的是"额度还没来"的那一版高度'
  );
});

test('⚠️ 量到 0 ⇒ **什么都不写**（⛔ 别把 `--header-h` 写成 0 —— 那几处 sticky 会全贴到顶上）', () => {
  const { B, cssVars } = makeSandbox({ loggedIn: true, budget: null, headerH: 0 });
  B.mount();
  assert.strictEqual(cssVars['--header-h'], undefined);
});

test('窗口 resize ⇒ 重量（窄屏会把页头挤成两行/三行）', () => {
  let h = 46;
  const { B, cssVars, resized } = makeSandbox({ loggedIn: true, budget: null, headerH: () => h });
  B.mount();
  assert.strictEqual(resized.length, 1, '没注册 resize ⇒ 窗口一变高度就漂了');
  h = 138;
  resized[0]();
  assert.strictEqual(cssVars['--header-h'], '138px');
});

test('这一页没有 `.site-header`（或没有 DOM）⇒ 不抛、也不写', () => {
  const { B, cssVars } = makeSandbox({ loggedIn: true, budget: null, noHeader: true });
  assert.doesNotThrow(() => B.mount());
  assert.deepStrictEqual(Object.keys(cssVars), []);
});

/* ══════════════ 6 · 结构守卫：只许漏【一个】全局键 ══════════════ */

test('🔴 `authbar.js` 对外**只许漏一个全局键**（`RagAuthBar`）', () => {
  const { added } = makeSandbox({});
  assert.deepStrictEqual(
    added, ['RagAuthBar'],
    '漏了别的全局键 ⇒ 会与 6 个页面各自的顶层绑定撞上（那些页面都有 `function el(…)`）'
    + ' ⇒ `SyntaxError` ⇒ 整段内联脚本一行都不执行，而当时全世界的门都是绿的'
  );
});
