'use strict';
/* 登录态（JWT）的**唯一一份**（§七·1）。
 *
 * ⛔ 本文件**不碰 DOM** —— 它只做三件事：**登入 / 登出 / 拿令牌**。
 *    「页头怎么显示登录态」是 `cred.js` 那一层的事（照旧那份分工）。
 *
 * ## 🔴 它替掉了什么
 *
 * 首访静默领一把 API Key 存在 `localStorage` 里，页头显示「已自动获得」。
 * 作者 2026-10-15 裁「**改成正规登录**」⇒ **没有访客了**，改成用户名 + 密码换 JWT。
 * 📄  §七·1（含两条没采纳的备选）。
 *
 * ## 🔴 三条约定（⛔ 改这个文件之前先读）
 *
 * ① **⛔ 一个字节的令牌都不进 DOM。** 页面只显示「登没登录」，⛔ 不显示令牌本身。
 *    与前任那条同一条道理：显示只有风险 —— 会被复制、会被转贴。
 *
 * ② **登入失败 ⇒ 回一句话，⛔ 不抛。** 页面拿 `res.error` 原样显示。
 *    ⚠️ **优先用后端回的那句 `error`**（本仓错误体是 `{error, code, status_code}`）——
 *    前端自己重编一句 = **同一条信息两个来源**，而两边会漂移（那条纪律）。
 *
 * ③ **刷新令牌真失效（401/403）⇒ 干净登出。** ⚠️ 但**网络错 / 5xx 不登出** ——
 *    那两种是"暂时问不到"，不是"你没资格"。混成一个会把**能用的登录**弄丢。
 *
 * ## ⚠️ 为什么存 `localStorage`（⛔ 不是 `sessionStorage`）
 *
 * 我最初提的是 `sessionStorage`，**写完前改掉了**，理由两条：
 * ① `sessionStorage` **按标签页隔离** ⇒ 同一个站**新开一个标签页就是"未登录"**，
 *    而本仓是个**企业内部工具**（单管理员），那是天天会撞上的事；
 * ② `refresh_token` 的生命周期是 **30 天**（`api_v1.py` 的 `response_description`）——
 *    把它放进"关掉标签页就没"的盒子，等于**这个长效令牌白给**。
 * ⇒ 取 `localStorage` + 一个显式的**退出**按钮（退出走 `logout()`）。
 * ⚠️ 代价已认：**登录态会留在磁盘上**，直到过期或点退出 —— 本仓前任那把也是这么存的。
 *
 * ⚠️ 形状说明：本仓【没有 package.json】⇒ 经典脚本（浏览器 `<script src>` 直接用），
 *    末尾条件导出给 Node 的 `require`。
 *
 * 🔴🔴 **整体包在一个 IIFE 里** —— ⛔ 别把这层括号去掉。
 *    经典脚本的顶层 `function` / `const` **全是全局的**，而 8 个页面各自也有自己的顶层绑定
 *    （每页都有 `const key = …`、`function el(…)`）。
 *    2026-10-10 实测过一次同型事故：顶层写 `function key()` 与页面的 `const key` 撞上
 *    ⇒ `SyntaxError` ⇒ **整段内联脚本一行都不执行，页面是死的**，
 *    而当时 982 条 pytest + 283 条 node 用例**全绿**（它们只读文本，不解析 JS）。
 *    ⇒ 本文件对外**只留 `window.RagAuth` 一个键**（守卫：`auth.test.js` 末条）。
 */

(function () {

/** 存令牌的两个键。⚠️ 前缀与前任那把（`rag_api_key`）**不同** —— 那是另一件事，别复用。 */
const AT_STORE = 'rag_access_token';
const RT_STORE = 'rag_refresh_token';

/** 两条端点。⚠️ **必须带 `/api/v1`** —— 页面守卫（`test_web_pages.py`）会扫这个字面量。 */
const LOGIN_PATH = '/api/v1/auth/login';
const REFRESH_PATH = '/api/v1/auth/refresh';

/**
 * 存取的三个薄封装。
 *
 * 🔴 **全部吞掉异常**：隐私模式 / 存储被禁时 `localStorage` 会**抛** ——
 *    那时页面应当"看起来像没登录"，⛔ 而不是整体挂掉（前任 `session.js` 同款处置）。
 */
function read(k) {
  try {
    const v = localStorage.getItem(k);
    return typeof v === 'string' ? v : '';
  } catch (e) { return ''; }
}

function write(k, v) {
  try { localStorage.setItem(k, String(v || '')); } catch (e) { /* 见上 */ }
}

function drop(k) {
  try { localStorage.removeItem(k); } catch (e) { /* 见上 */ }
}

/** 当前的短期令牌；**没登录就是 `''`**。⚠️ 它**只给发请求用**，⛔ 别显示到页面上（约定 ①）。 */
function token() { return read(AT_STORE); }

/** 当前的长期令牌；没有就是 `''`。 */
function refreshToken() { return read(RT_STORE); }

/** 登没登录。⚠️ 判据只到"手里有没有令牌" —— **令牌过没过期不在这里判**（那要问服务端）。 */
function isLoggedIn() { return !!token(); }

/**
 * 发给后端的认证头。
 *
 * ⚠️ 后端两种都收（`routing/deps.py` 的 `get_current_user_hybrid`：`X-API-Key` 或 `Bearer`）
 *    ⇒ 本函数**只发 `Bearer`**。返回空对象 ⇒ 调用方那个 `headers` 就是"没带头"。
 */
function authHeader() {
  const t = token();
  return t ? { Authorization: 'Bearer ' + t } : {};
}

/** 清掉登录态。⚠️ **同步**，⛔ 不发请求（服务端那边令牌是无状态的，没什么可通知的）。 */
function logout() {
  drop(AT_STORE);
  drop(RT_STORE);
}

/**
 * ⭐ 登入。**永远不抛**（约定 ②）—— 失败就回一句能直接显示的话。
 *
 * @param {string} user_name
 * @param {string} password
 * @returns {Promise<{ok: boolean, error?: string}>}
 */
async function login(user_name, password) {
  const body = {
    user_name: String(user_name == null ? '' : user_name),
    password: String(password == null ? '' : password),
  };

  let r;
  try {
    r = await fetch(LOGIN_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    // ⚠️ **网络错**（后端没起 / 地址不对 / 断网）—— 它与"密码错"是两回事，必须分开说，
    //    否则用户会一直去核对密码，而问题根本不在那儿。
    return { ok: false, error: '连不上服务 —— 确认后端在跑、地址对不对' };
  }

  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }

  if (!r.ok) {
    // 约定 ②：**优先用后端回的那句 `error`**（本仓错误体 `{error, code, status_code}`）。
    const fromServer = data && typeof data.error === 'string' ? data.error.trim() : '';
    const fallback = r.status === 401 ? '用户名或密码不对'
                                      : ('登录失败（HTTP ' + r.status + '）');
    return { ok: false, error: fromServer || fallback };
  }

  const at = data && typeof data.access_token === 'string' ? data.access_token : '';
  if (!at) {
    // ⚠️ 2xx 但**没有令牌** —— 那是"服务端坏了"，⛔ 别当成登录成功往下走。
    //    本仓立场：**拿"动作成功"当"结果正确"**是头号禁忌。
    return { ok: false, error: '服务回了成功，但返回体里没有 access_token' };
  }

  write(AT_STORE, at);
  write(RT_STORE, (data && typeof data.refresh_token === 'string') ? data.refresh_token : '');
  return { ok: true };
}

/**
 * 用长期令牌换一把新的短期令牌。
 *
 * ⚠️ **它不负责改写页面** —— 调用方拿到 `false` 之后自己去登出 / 跳登录页。
 *
 * @returns {Promise<boolean>} 换到了就是 `true`
 */
async function refresh() {
  const rt = refreshToken();
  if (!rt) return false;                 // 手里没有长期的 ⇒ 没什么可换的

  let r;
  try {
    r = await fetch(REFRESH_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    });
  } catch (e) {
    return false;                        // 约定 ③：网络错 ⇒ **不登出**（那是"暂时问不到"）
  }

  // 约定 ③：**只有服务端明说"你没资格"才登出**（401/403）。
  if (r.status === 401 || r.status === 403) { logout(); return false; }
  if (!r.ok) return false;               // 5xx / 别的 ⇒ 留着，下次再试

  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  const at = data && typeof data.access_token === 'string' ? data.access_token : '';
  if (!at) return false;

  write(AT_STORE, at);
  return true;
}

/**
 * 🔴 **同一时刻的多次 401 ⇒ 只刷新一次**（惊群防护）。
 *
 * ⚠️ **这是拿一个实测换来的**（2026-10-15 · 真浏览器上抓的）：
 *    一进 `/rag`，页面**同时**发 3 个请求（`chat_history` · `token/budget` · `cost/records`），
 *    而那时手里的 `access_token` 刚过期 ⇒ **3 个一起 401**
 *    每个 `apiFetch` 各自去刷新 ⇒ **一口气发了 3 次 `/auth/refresh`**（网络记录里看得）。
 *    ⚠️ 本仓是**单管理员**，3 次只是浪费；但那是**按并发数线性增长**的 —— 页面越重越糟。
 *    ⇒ 现在**同时在飞的那几次共用同一个 `Promise`**。
 *
 * ⚠️ **⛔ 别把它并进 `refresh()` 里** —— `refresh()` 是"换一把"这件事本身（有自己的用例），
 *    `refreshOnce` 是"同一时刻只做一次"这层调度。两件事分开，各自的判据才写得出来。
 */
let _refreshing = null;
function refreshOnce() {
  if (!_refreshing) {
    _refreshing = refresh().then(
      function (ok) { _refreshing = null; return ok; },
      function (e) { _refreshing = null; throw e; },
    );
  }
  return _refreshing;
}

/**
 * ⭐ **带认证的取数** —— 页面调后端**只走这一个入口**（§七·1 的第四段）。
 *
 * 它做两件事，而**两件都必须只有一份**：
 * ① **贴认证头**（`Authorization: Bearer …`）—— 原先 6 个页面各写一遍
 *    `function auth() { return { 'X-API-Key': key() }; }`，那是"8 份 `KEY_STORE`"那笔债的同一形状；
 * ② **401 ⇒ 换一次令牌 ⇒ 重试一次**。⚠️ 这一条**尤其不能各页自己写**：
 *    access_token 只活 **15 分钟**，而 refresh_token 活 **30 天** ——
 *    没有这一步的话，**开着页面过 15 分钟点任何东西都会失败**，而那不是用户的错。
 *
 * ## 🔴 三条（⛔ 改这个函数之前先读）
 *
 * ① **只重试一次**，⛔ 不递归 —— 新令牌又被拒（比如账号被停用）时**原样把 401 交回给调用方**，
 *    由页面自己的错误分支去显示。⚠️ 在 fetch 包装里"跳到登录页"那种副作用**不在这里做**：
 *    藏起来的跳转是最难查的一类（调用方以为自己只是取个数）。
 * ② **`init` 会被复用两次** —— 所以**⛔ 别传一次性 body**（`ReadableStream` 那种）。
 *    本仓现有调用点传的是字符串 / `FormData`，两者复用都安全。
 * ③ **调用方自己的 header 保留**（如 `Content-Type`）—— 本函数只**往里加**一个 `Authorization`，
 *    ⛔ 不覆盖（覆盖掉 `Content-Type` 会让 `POST` 变 422，而错看起来像"后端坏了"）。
 *
 * @param {string} url
 * @param {Object} [init] 与 `fetch` 的第二参同形
 * @returns {Promise<Response>} **永远是 fetch 那个 Response**（含 401），⛔ 本函数不抛
 */
async function apiFetch(url, init) {
  const base = init || {};

  /** 造一份"贴上当前令牌"的 init。⚠️ **每次都新造一份**：`fetch` 会用掉那份 headers。 */
  function withAuth() {
    const o = Object.assign({}, base);
    o.headers = Object.assign({}, base.headers || {}, authHeader());
    return o;
  }

  let r = await fetch(url, withAuth());
  if (r.status !== 401) return r;

  // 401 ⇒ 令牌多半过期了（15 分钟）。换一把再试**一次**。
  if (!(await refreshOnce())) return r;  // 换不到 ⇒ 把原来那个 401 交回去
  return fetch(url, withAuth());
}

const RagAuth = {
  AT_STORE, RT_STORE, LOGIN_PATH, REFRESH_PATH,
  token, refreshToken, isLoggedIn, authHeader, logout, login, refresh, apiFetch,
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = RagAuth;                  // Node（`node --test` / `require`）
}
if (typeof window !== 'undefined') {
  window.RagAuth = RagAuth;                  // 浏览器（`<script src>` 之后就是全局）
}

})();   // ⛔ 别去掉 —— 见文件头：去掉它，上面那些绑定全变全局，会撞页面自己的顶层同名绑定
