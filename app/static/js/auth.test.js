'use strict';
/* `auth.js` 的用例 —— ⛔ 零 npm 依赖（只 node:test + node:assert）。
   跑法：`node --test app/static/js/auth.test.js`（已进 `ci.yml` 的 run 块，那条 glob）

🔴 **全程用 `vm` 造一个假浏览器**（`localStorage` / `fetch` 都是假的）——
   因为 `auth.js` 的价值**大半在"失败时怎么办"**，而那几条只有**把 fetch 弄坏**才测得到。
   ⚠️ 那一族的判据**几乎全是反证型的**（「401 要登出」＋「网络错【不】登出」），
      只写前一半是**假通过** —— 把实现改成"任何失败都登出"，前半条照样绿。
*/
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, 'auth.js'), 'utf8');

/** 造一个假浏览器，跑一遍 `auth.js`，把 `window.RagAuth` 交出来。
 *
 * @param {{stored?: object, fetchImpl?: Function, storageThrow?: boolean}} opts
 *   `stored` ⇒ 假的 localStorage 初始内容；`fetchImpl` ⇒ 假的 fetch；
 *   `storageThrow` ⇒ 让 localStorage 每个方法都抛（隐私模式那种）
 */
function makeSandbox(opts) {
  const o = opts || {};
  const store = Object.assign({}, o.stored || {});
  const calls = [];
  const boom = () => { throw new Error('storage disabled'); };

  const sandbox = {
    window: {},
    console,
    localStorage: o.storageThrow ? {
      getItem: boom, setItem: boom, removeItem: boom,
    } : {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    fetch: (url, init) => {
      calls.push({ url, init });
      if (!o.fetchImpl) return Promise.reject(new Error('测试里没给 fetchImpl'));
      return o.fetchImpl(url, init);
    },
  };

  // 🔴 `window` **就是全局对象本身** —— 真浏览器里如此（`window === globalThis`）。
  //    ⚠️ 不能用两个对象假装：`auth.js` 是**经典脚本**，它只往 `window` 上挂。
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  vm.runInContext(SRC, sandbox);
  const added = Object.keys(sandbox).filter((k) => !before.has(k));
  return { A: sandbox.RagAuth, store, calls, sandbox, added };
}

/** 🔴 **跨 realm 比对象**：`vm` 里造出来的对象，原型与用例这边**不是同一个 `Object.prototype`**
 *  ⇒ `assert.deepStrictEqual` 会报「**same structure but not reference-equal**」（内容其实一样）。
 *  ⇒ 过一遍 `JSON`，把它拉回本 realm —— 比**内容**，不比原型。
 *  ⚠️ 只对**纯数据**用（`authHeader()` 的返回值就是）；带函数的对象过不了。
 */
function plain(o) { return JSON.parse(JSON.stringify(o)); }

/** 一个假响应。`{status, json}`。 */
function resp(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

/** 一个"坏掉的 json" 响应（2xx 但体不是 JSON）。 */
function respBadJson(status) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.reject(new Error('not json')),
  });
}

/* ══════════════ 1 · 没登录时的那几条（同步 · 一个请求都不该发） ══════════════ */

test('没登录 ⇒ `token()` 是空串 · `isLoggedIn()` 假 · `authHeader()` 是**空对象**', () => {
  const { A, calls } = makeSandbox({});
  assert.strictEqual(A.token(), '');
  assert.strictEqual(A.isLoggedIn(), false);
  assert.deepStrictEqual(plain(A.authHeader()), {});
  assert.strictEqual(calls.length, 0, '读一下登录态居然发了请求 ⇒ 那是"每次开页面都打一次服务端"');
});

test('🔴 存储被禁（隐私模式）⇒ **不崩**，按"没登录"处理', () => {
  const { A } = makeSandbox({ storageThrow: true });
  assert.strictEqual(A.token(), '');
  assert.strictEqual(A.isLoggedIn(), false);
  assert.deepStrictEqual(plain(A.authHeader()), {});
});

/* ══════════════ 2 · 登入：成功那条路 ══════════════ */

test('登入成功 ⇒ 两个令牌都存下 · `isLoggedIn()` 真 · 头带 `Bearer`', async () => {
  const { A, store, calls } = makeSandbox({
    fetchImpl: () => resp(200, {
      access_token: 'AT-1', refresh_token: 'RT-1', token_type: 'bearer', expires_in: 900,
    }),
  });
  const res = await A.login('admin', 'pw');
  // ⚠️ 比**字段**，不比整个对象 —— `res` 是 `vm` 那个 realm 里造的，
  //    它的原型与用例这边的 `Object.prototype` **不是同一个** ⇒ `deepStrictEqual` 会报
  //    「same structure but not reference-equal」（跨 realm 的老问题）。
  assert.strictEqual(res.ok, true);
  assert.strictEqual(store.rag_access_token, 'AT-1');
  assert.strictEqual(store.rag_refresh_token, 'RT-1');
  assert.strictEqual(A.isLoggedIn(), true);
  assert.deepStrictEqual(plain(A.authHeader()), { Authorization: 'Bearer AT-1' });

  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].url, '/api/v1/auth/login');
  assert.strictEqual(calls[0].init.method, 'POST');
  assert.deepStrictEqual(JSON.parse(calls[0].init.body), { user_name: 'admin', password: 'pw' });
});

test('⛔ 登录请求**不带任何认证头**（本来就是要来换令牌的）', async () => {
  const { A, calls } = makeSandbox({
    stored: { rag_access_token: 'OLD' },
    fetchImpl: () => resp(200, { access_token: 'AT-2', refresh_token: 'RT-2' }),
  });
  await A.login('admin', 'pw');
  const h = calls[0].init.headers;
  assert.strictEqual(h.Authorization, undefined);
  assert.strictEqual(h['X-API-Key'], undefined);
});

/* ══════════════ 3 · 登入：失败那两条【必须分开】（本文件最要紧的一段） ══════════════ */

test('🔴 密码错（401）⇒ 回**后端那句原话**，⛔ 不自己重编', async () => {
  const { A, store } = makeSandbox({
    fetchImpl: () => resp(401, { error: '用户名或密码错误', code: 'AUTH_INVALID', status_code: 401 }),
  });
  const res = await A.login('admin', 'wrong');
  assert.strictEqual(res.ok, false);
  assert.strictEqual(res.error, '用户名或密码错误', '⛔ 前端自己重编 = 同一条信息两个来源，两边会漂移');
  assert.deepStrictEqual(store, {}, '登入失败了却把令牌写进去了');
});

test('🔴 后端没给 `error` 时，401 才回退到本地那句', async () => {
  const { A } = makeSandbox({ fetchImpl: () => resp(401, {}) });
  const res = await A.login('admin', 'wrong');
  assert.strictEqual(res.ok, false);
  assert.match(res.error, /用户名或密码不对/);
});

test('🔴 网络错 ⇒ 那句话与"密码错"**不是同一句**（否则用户会一直去核对密码）', async () => {
  const { A } = makeSandbox({ fetchImpl: () => Promise.reject(new Error('boom')) });
  const res = await A.login('admin', 'pw');
  assert.strictEqual(res.ok, false);
  assert.match(res.error, /连不上服务/);
  assert.doesNotMatch(res.error, /密码/);
});

test('🔴 2xx 但**返回体里没有 access_token** ⇒ 判失败（⛔ 别拿"动作成功"当"结果正确"）', async () => {
  const { A, store } = makeSandbox({ fetchImpl: () => resp(200, { token_type: 'bearer' }) });
  const res = await A.login('admin', 'pw');
  assert.strictEqual(res.ok, false);
  assert.match(res.error, /没有 access_token/);
  assert.deepStrictEqual(store, {});
});

test('2xx 但不是 JSON ⇒ 判失败，且**不抛**', async () => {
  const { A } = makeSandbox({ fetchImpl: () => respBadJson(200) });
  const res = await A.login('admin', 'pw');
  assert.strictEqual(res.ok, false);
});

test('非 401 的失败（比如 500）⇒ 也不抛，回一句带状态码的', async () => {
  const { A } = makeSandbox({ fetchImpl: () => resp(500, {}) });
  const res = await A.login('admin', 'pw');
  assert.strictEqual(res.ok, false);
  assert.match(res.error, /500/);
});

/* ══════════════ 4 · 登出 ══════════════ */

test('`logout()` 把两个令牌都清掉，且**一个请求都不发**', () => {
  const { A, store, calls } = makeSandbox({
    stored: { rag_access_token: 'AT', rag_refresh_token: 'RT' },
  });
  A.logout();
  assert.deepStrictEqual(store, {});
  assert.strictEqual(A.isLoggedIn(), false);
  assert.deepStrictEqual(calls, [], '登出发请求了 —— 服务端那边令牌是无状态的，没什么可通知的');
});

/* ══════════════ 5 · 刷新令牌（约定 ③：两条失败【处置相反】） ══════════════ */

test('刷新成功 ⇒ **只换 access_token**，`refresh_token` 不动', async () => {
  const { A, store } = makeSandbox({
    stored: { rag_access_token: 'AT-OLD', rag_refresh_token: 'RT-1' },
    fetchImpl: () => resp(200, { access_token: 'AT-NEW', token_type: 'bearer', expires_in: 900 }),
  });
  assert.strictEqual(await A.refresh(), true);
  assert.strictEqual(store.rag_access_token, 'AT-NEW');
  assert.strictEqual(store.rag_refresh_token, 'RT-1', '它回体里没有 refresh_token ⇒ ⛔ 别把它清掉');
});

test('🔴 刷新时服务端说 401（没资格）⇒ **干净登出**', async () => {
  const { A, store } = makeSandbox({
    stored: { rag_access_token: 'AT', rag_refresh_token: 'RT' },
    fetchImpl: () => resp(401, { error: '刷新令牌已过期' }),
  });
  assert.strictEqual(await A.refresh(), false);
  assert.deepStrictEqual(store, {}, '401 之后还留着令牌 ⇒ 页面会一直拿着废令牌去打');
});

test('🔴 **反证的另一半**：刷新时**网络错** ⇒ ⛔ **不许登出**（那是"暂时问不到"）', async () => {
  const { A, store } = makeSandbox({
    stored: { rag_access_token: 'AT', rag_refresh_token: 'RT' },
    fetchImpl: () => Promise.reject(new Error('boom')),
  });
  assert.strictEqual(await A.refresh(), false);
  assert.strictEqual(store.rag_access_token, 'AT', '网络抖一下就把登录态清了 —— 那是把能用的登录弄丢');
  assert.strictEqual(store.rag_refresh_token, 'RT');
});

test('刷新时服务端 500 ⇒ 同样**不登出**（它不是"你没资格"）', async () => {
  const { A, store } = makeSandbox({
    stored: { rag_access_token: 'AT', rag_refresh_token: 'RT' },
    fetchImpl: () => resp(500, {}),
  });
  assert.strictEqual(await A.refresh(), false);
  assert.strictEqual(store.rag_access_token, 'AT');
});

test('手里**没有**长期令牌 ⇒ 一个请求都不发就回 false', async () => {
  const { A, calls } = makeSandbox({});
  assert.strictEqual(await A.refresh(), false);
  assert.deepStrictEqual(calls, []);
});

test('刷新回 2xx 但没有 access_token ⇒ 回 false，且**保持原样**', async () => {
  const { A, store } = makeSandbox({
    stored: { rag_access_token: 'AT-OLD', rag_refresh_token: 'RT' },
    fetchImpl: () => resp(200, { token_type: 'bearer' }),
  });
  assert.strictEqual(await A.refresh(), false);
  assert.strictEqual(store.rag_access_token, 'AT-OLD');
});

/* ══════════════ 6 · `apiFetch`：贴头 + 401 换令牌重试（**只重试一次**） ══════════════ */

test('`apiFetch` 会把 `Authorization` 贴上，且**保留调用方自己的 header**', async () => {
  const { A, calls } = makeSandbox({
    stored: { rag_access_token: 'AT-1' },
    fetchImpl: () => resp(200, {}),
  });
  await A.apiFetch('/api/v1/rag/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{"q":1}',
  });
  const h = calls[0].init.headers;
  assert.strictEqual(h.Authorization, 'Bearer AT-1');
  // 🔴 反证的另一半：覆盖掉 Content-Type 会让 POST 变 422，而错看起来像"后端坏了"
  assert.strictEqual(h['Content-Type'], 'application/json');
  assert.strictEqual(calls[0].init.body, '{"q":1}');
});

test('非 401 ⇒ **一个刷新请求都不发**', async () => {
  const { A, calls } = makeSandbox({
    stored: { rag_access_token: 'AT-1', rag_refresh_token: 'RT-1' },
    fetchImpl: () => resp(500, {}),
  });
  const r = await A.apiFetch('/api/v1/x');
  assert.strictEqual(r.status, 500);
  assert.strictEqual(calls.length, 1, '不是 401 也去刷新了');
});

test('🔴 401 ⇒ 换一把令牌 + **重试一次**，返回重试的结果', async () => {
  let n = 0;
  const { A, calls } = makeSandbox({
    stored: { rag_access_token: 'AT-OLD', rag_refresh_token: 'RT-1' },
    fetchImpl: (url) => {
      if (url === '/api/v1/auth/refresh') {
        return resp(200, { access_token: 'AT-NEW', token_type: 'bearer', expires_in: 900 });
      }
      n += 1;
      return n === 1 ? resp(401, {}) : resp(200, { ok: 1 });
    },
  });
  const r = await A.apiFetch('/api/v1/x');
  assert.strictEqual(r.status, 200);
  assert.strictEqual(calls.length, 3, '应当正好三次：原请求 → 刷新 → 重试');
  // 🔴 重试那次带的是**新**令牌（⛔ 别拿旧的再打一次）
  assert.strictEqual(calls[0].init.headers.Authorization, 'Bearer AT-OLD');
  assert.strictEqual(calls[1].url, '/api/v1/auth/refresh');
  assert.strictEqual(calls[2].init.headers.Authorization, 'Bearer AT-NEW');
});

test('🔴 401 而**刷新也失败** ⇒ 把原来那个 401 交回去（⛔ 不抛、不无限重试）', async () => {
  const { A, calls } = makeSandbox({
    stored: { rag_access_token: 'AT-OLD', rag_refresh_token: 'RT-1' },
    fetchImpl: (url) => (url === '/api/v1/auth/refresh' ? resp(401, {}) : resp(401, {})),
  });
  const r = await A.apiFetch('/api/v1/x');
  assert.strictEqual(r.status, 401);
  assert.strictEqual(calls.length, 2, '刷新失败之后不该再打第三次');
});

test('🔴 **重试之后还是 401** ⇒ 直接交回去（⛔ 只重试一次，别递归）', async () => {
  const { A, calls } = makeSandbox({
    stored: { rag_access_token: 'AT', rag_refresh_token: 'RT' },
    fetchImpl: (url) => (url === '/api/v1/auth/refresh'
      ? resp(200, { access_token: 'AT-2', token_type: 'bearer', expires_in: 900 })
      : resp(401, {})),
  });
  const r = await A.apiFetch('/api/v1/x');
  assert.strictEqual(r.status, 401);
  assert.strictEqual(calls.length, 3, '应当正好三次；第四次说明它在递归');
});

test('🔴 **并发 401 ⇒ 只刷新一次**（惊群防护）—— 真浏览器上实测过"3 个请求发 3 次刷新"', async () => {
  let refreshes = 0;
  const { A } = makeSandbox({
    stored: { rag_access_token: 'AT-OLD', rag_refresh_token: 'RT-1' },
    fetchImpl: (url) => {
      if (url === '/api/v1/auth/refresh') {
        refreshes += 1;
        // ⚠️ **故意慢一点**：不然第一个还没刷完、后面两个就各自又刷一次 —— 那正是要防的形态。
        return new Promise((res) => setTimeout(() => res({
          ok: true, status: 200,
          json: () => Promise.resolve({ access_token: 'AT-NEW', token_type: 'bearer', expires_in: 900 }),
        }), 5));
      }
      return resp(401, {});
    },
  });

  // 页面一进来同时发三个（真浏览器上 `/rag` 就是这样：chat_history / token-budget / cost-records）
  await Promise.all([A.apiFetch('/x'), A.apiFetch('/y'), A.apiFetch('/z')]);

  assert.strictEqual(
    refreshes, 1,
    `三个并发 401 发了 ${refreshes} 次刷新 ⇒ 那是按并发数线性增长的浪费（本页单管理员只是浪费，页面越重越糟）`
  );
});

test('没登录时调 `apiFetch` ⇒ **不发请求**（没有令牌可贴）', async () => {
  const { A, calls } = makeSandbox({ fetchImpl: () => resp(200, {}) });
  await A.apiFetch('/api/v1/x');
  const h = calls[0].init.headers || {};
  assert.strictEqual(h.Authorization, undefined, '没登录却贴了个 Authorization 上去');
});

/* ══════════════ 7 · 结构守卫：只许漏【一个】全局键 ══════════════ */

test('🔴 `auth.js` 对外**只许漏一个全局键**（`RagAuth`）', () => {
  const { added } = makeSandbox({});
  assert.deepStrictEqual(
    added, ['RagAuth'],
    '漏了别的全局键 ⇒ 会与 8 个页面各自的顶层绑定撞上。'
    + '2026-10-10 就是这么栽的：`SyntaxError` ⇒ 整段内联脚本一行都不执行，'
    + '而当时 982 条 pytest + 283 条 node 用例全绿。'
  );
});
