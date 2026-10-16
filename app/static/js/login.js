'use strict';
/* 登录页（`/login`）的**纯逻辑**（§七·1）。
 *
 * ⛔ 本文件**不碰 DOM** —— 「表单怎么接、按钮怎么禁、错误往哪显示」是
 *    `app/static/web/login.html` 里那段内联脚本的事。这里只放**能单测的判定**。
 *    （本仓的分工惯例：`js/<页名>.js` 放纯逻辑 + `node --test`；页面里那段内联只管接线。）
 *
 * ## 🔴 为什么这个文件值得存在
 *
 * 登录页看起来"没什么逻辑" —— 但它有**一处真正的安全判定**：`?next=` 那个参数。
 * 「登完跳回你原来要去的页」是个好体验，而**它的经典错法叫开放重定向**：
 * 攻击者把 `?next=https://evil.example/`（或 `//evil.example/`）发给人，
 * 人登完**从一个真站点跳到假站点**，而地址栏在跳转前一直是真站点的。
 * ⇒ **那个判定必须有一份、有单测**，⛔ 不是"在页面里当场 `location.href = next`"。
 *
 * ⚠️ 形状说明：本仓【没有 package.json】⇒ 经典脚本（浏览器 `<script src>` 直接用），
 *    末尾条件导出给 Node 的 `require`。
 *
 * 🔴🔴 **整体包在一个 IIFE 里** —— ⛔ 别把这层括号去掉。
 *    经典脚本的顶层绑定**全是全局的**，而页面自己有 `const $ = …` / `const el = …`
 *    ⇒ 撞上就是 `SyntaxError` ⇒ **整段内联脚本一行都不执行，页面是死的**，
 *    而**没有任何门会红**（2026-10-10 实测过一次同型事故）。
 */

(function () {

/** 登完之后默认回哪。⚠️ 站内根路径 —— ⛔ 别写成完整 URL。 */
const HOME = '/';

/**
 * 🔴 从页面的 `?next=` 里取出「登完回哪去」，**只接受站内路径**。
 *
 * @param {string} search 形如 `'?next=%2Fops'`（浏览器里就是 `location.search`）
 * @returns {string} 站内路径；**任何一个条件不满足 ⇒ 回 `/`**（默认值，⛔ 不抛）
 *
 * ## 收什么 / 不收什么
 *
 * | 收 | 为什么 |
 * |---|---|
 * | `/` | 站内根 |
 * | `/ops` · `/rag?a=1` | 站内路径（含查询串） |
 *
 * | ⛔ 不收 | 为什么 |
 * |---|---|
 * | `https://evil.example/` · `javascript:…` | **绝对地址 / 别的协议** ⇒ 开放重定向 |
 * | `//evil.example/` | **协议相对地址** —— 浏览器会把它当 `https://evil.example/`。⚠️ **最容易被漏掉的一条** |
 * | `/\evil.example` | 同上：一部分浏览器把**反斜杠当正斜杠**归一化 ⇒ 同样是协议相对 |
 * | 含控制字符（`\n` 之类） | **响应头注入**那一族的老朋友；路径里本来也不该有 |
 * | 空 / 缺 | 没什么可回的 ⇒ 回 `/` |
 *
 * ⚠️ **判据是"白名单式"的**（必须以单个 `/` 开头、且第二个字符不是 `/` 或 `\`），
 *    ⛔ **不是黑名单式的**（"过滤掉 `http://` 就行"）——
 *    黑名单永远差一条，而白名单差的那条只是"回首页"。
 */
function safeNext(search) {
  let raw = '';
  try {
    // ⚠️ `URLSearchParams` 会自己解开 `%2F` ⇒ 比较的是**解码后**的值，那正是浏览器要跳的那个。
    raw = new URLSearchParams(String(search == null ? '' : search)).get('next') || '';
  } catch (e) {
    return HOME;                       // 串坏了 ⇒ 回默认，⛔ 别把坏串原样吐回去
  }

  if (!raw) return HOME;
  if (raw.charAt(0) !== '/') return HOME;          // 必须站内
  if (raw.charAt(1) === '/') return HOME;          // `//host` = 协议相对
  if (raw.charAt(1) === '\\') return HOME;         // `/\host` = 某些浏览器同上
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(raw)) return HOME;

  return raw;
}

/** 登录页的路径。⚠️ **只此一处**（另一个用到它的地方是路由 `main.py` 的 `/login`）。 */
const LOGIN_PATH = '/login';

/**
 * 🔴 **`safeNext` 的【另一半】**：把"我现在在哪"编成一个去登录页的链接。
 *
 * ⚠️ **两个方向必须住同一个文件** —— 编码在这边、解码在那边，就是在赌
 *    「改了一头还记得改另一头」（本仓立场：**同一个东西两份实现必然漂移**）。
 *
 * @param {string} here 当前位置，形如 `'/ops'` 或 `'/rag?x=1'`（浏览器里就是
 *   `location.pathname + location.search`）
 * @returns {string} 形如 `/login?next=%2Fops`
 */
function loginHref(here) {
  const h = String(here == null ? '' : here);
  // ⚠️ 传进来的要是**站内路径**（`safeNext` 的同一个白名单）——
  //    否则编出来的链接登完会把用户送到别处（那是同一个洞的另一头）。
  const ok = h.charAt(0) === '/' && h.charAt(1) !== '/' && h.charAt(1) !== '\\';
  return LOGIN_PATH + '?next=' + encodeURIComponent(ok ? h : HOME);
}

const RagLogin = { HOME, LOGIN_PATH, safeNext, loginHref };

if (typeof module !== 'undefined' && module.exports) {
  module.exports = RagLogin;                 // Node（`node --test` / `require`）
}
if (typeof window !== 'undefined') {
  window.RagLogin = RagLogin;                // 浏览器（`<script src>` 之后就是全局）
}

})();   // ⛔ 别去掉 —— 见文件头：去掉它，上面那些绑定全变全局，会撞页面自己的顶层同名绑定
