'use strict';
/* 页头那一小块**登录态控件**的唯一实现（§七·1）。
 *
 * ⛔ 本文件只做两件网外的事：**读一次额度**与**退出**。
 *    「登没登录」那个判定**不在这里** —— 在 `auth.js` 里。
 *    ⇒ 分工：`auth.js` 管**登录态本身**，`authbar.js` 管**页头怎么显示它**
 *      （与它替掉的那一对 `session.js` / `cred.js` **同一种分工**）。
 *
 * ## 🔴 它替掉了什么
 *
 * 原先是「访客凭据」那套：首访静默领一把 API Key，页头显示「已自动获得」；
 * 领不到就退回「自己填 API Key」；发号闸满了显示第三块。
 * 需求方 2026-10-15 裁「**改成正规登录**」⇒ **那三块全没了**，换成两块；
 * **同一天晚些时候他又改了一次形态** ⇒ 现在是这样：
 *   · `#auth-btn`   —— **页头那颗按钮，两个状态都在，只换字**：
 *                      没登录写「登录」（去 `/login?next=回本页`）；登录了写「已登录」，
 *                      ⛔ 并**去掉 `href`** —— 点一颗写着「已登录」的按钮跳到登录页会把人绕晕。
 *   · `#auth-strip` —— **登录之后、页头【下面】那一行**（额度 + 「退出」）。原话：
 *                      「**单独一行…不要和页头放在一个块里**」⇒ **它不在 `<header>` 里**，
 *                      跟着页面滚、⛔ 不吸顶；`--header-h` 也**不算它**。
 *                      ⛔ 「已登录」三个字**不在这里** —— 那是上面那颗按钮的事。
 * 📄  §七·1（含两条没采纳的备选 · 以及为什么存 `localStorage`）。
 *
 * ## 🔴 三条约定
 *
 * ① **⛔ 一个字节的令牌都不进 DOM。** 只显示「登没登录」与「还剩多少」
 *    （沿  那条）。显示令牌只有风险：会被复制、会被转贴。
 *
 * ② **没登录 ⇒ ⛔ 不跑页面自己的起法。** `mount(onReady)` 只在**已登录**时回调 `onReady`
 *    —— 否则页面会拿空令牌打一串请求、收一串 401，而用户什么都没做错。
 *    ⚠️ **但页头必须说清"为什么什么都没有"**（就那一句「没登录 · 去登录」）——
 *    空白页 + 空白页头是最坏的一种（本仓：**界面在说反话**）。
 *
 * ③ **取不到额度就不显示数字。** 本仓：「**不许印没有数据源的数**」——
 *    宁可只显示「已登录」，⛔ 也不把 `undefined` 拼上去（那是 `NaN`，而页面上看不出哪里错了）。
 *
 * 🔴 **它还得管一件"在页面上看不出来"的事**：把 `--header-h` 同步成页头的**真实高度**
 *    —— 登录后页头是**两行**（那一串额度比导航还长），而 `--header-h` 还被
 *    `html` 的 `scroll-padding-top` 与 `.side` / `.aside` / `.form` 的 sticky `top` 用着
 *    ⇒ 不同步，那几处会**短掉一整行**。见 `syncHeaderHeight()`。
 *
 * ⚠️ 形状说明：本仓【没有 package.json】⇒ 经典脚本（浏览器 `<script src>` 直接用，
 *    必须在 `auth.js` **之后**加载），末尾条件导出给 Node 的 `require`。
 *
 * 🔴🔴 **整体包在一个 IIFE 里** —— ⛔ 别把这层括号去掉。理由与 `session.js` / `cred.js` 同一条：
 *    经典脚本的顶层绑定**全是全局的**，而页面自己有 `function el(…)` / `const $ = …`
 *    ⇒ 撞上就是 `SyntaxError` ⇒ **整段内联脚本一行都不执行，页面是死的**，
 *    而**没有任何门会红**（2026-10-10 实测过一次）。
 *    ⇒ 本文件对外**只留 `window.RagAuthBar` 一个键**（守卫：`authbar.test.js` 末条）。
 */

(function () {

/** 两个挂点。⚠️ **6 个页面都得有这两个**（守卫：`app/tests/test_web_pages.py`）。 */
const IDS = {
  btn: 'auth-btn',       // 页头那颗按钮（**两个状态都在，只换字**：登录 ⇄ 已登录）
  strip: 'auth-strip',   // 页头【下面】那一行（额度 span + 退出按钮）
  quota: 'auth-quota',   // 上面那一行里的额度 span
  logout: 'auth-logout', // 上面那一行里的「退出」按钮
};

/** 取元素；**没有就返回 null**（⛔ 别抛 —— 与 `cred.js` 同一条）。 */
function el(id) {
  return (typeof document !== 'undefined' && document) ? document.getElementById(id) : null;
}

/** 切显隐；元素不存在 ⇒ **跳过**（见上）。 */
function setHidden(id, hidden) {
  const n = el(id);
  if (n) n.hidden = !!hidden;
}

/** 去属性；元素/方法不在 ⇒ **跳过**（同 `setHidden` 那条：⛔ 别抛）。 */
function removeAttr(node, name) {
  if (node && typeof node.removeAttribute === 'function') node.removeAttribute(name);
}

/**
 * 🔴 页头那两处的**唯一**一处切换。
 *
 * ⚠️ **别在别处再切一次** —— 两处切就会不一致（表现是「已登录」和「登录」同时出现）。
 *
 * 🔴 **页头那颗按钮⛔ 不跟着 `hidden` 走** —— 它两个状态**都要在**，只换字（需求方 2026-10-15 定）。
 *    切的是：**下面那一行**的显隐 + **那颗按钮的字**。
 *
 * @param {boolean} loggedIn
 */
function applyMode(loggedIn) {
  setHidden(IDS.strip, !loggedIn);

  const btn = el(IDS.btn);
  if (!btn) return;
  btn.textContent = loggedIn ? '已登录' : '登录';
  if (loggedIn) {
    // 🔴 已登录 ⇒ 它只是个**状态牌**。⚠️ 留着 `href` 的话，点「已登录」会跳到登录页，
    //    而点它的人**已经登录了** —— 本仓：**界面⛔ 不说反话**。
    removeAttr(btn, 'href');
  }
}

/**
 * 把**页头那颗按钮**（没登录时写着「登录」）指到**带 `next=` 的登录页**（登完回到本页）。
 *
 * ⚠️ 那个 `next=` 的**编法**在 `login.js` 的 `loginHref` —— ⛔ **别在这里手拼**：
 *    它与登录页那边的解码（`safeNext`）是**一对**，分居两个文件就是在赌"改了这头记得改那头"。
 * ⚠️ 拿不到 `location`（Node 里跑测试）⇒ **原样不动**，⛔ 不抛。
 */
function pointLoginLink() {
  const a = el(IDS.btn);
  if (!a) return;
  if (typeof location === 'undefined' || !location) return;
  const here = String(location.pathname || '/') + String(location.search || '');
  if (typeof RagLogin !== 'undefined' && RagLogin && typeof RagLogin.loginHref === 'function') {
    a.setAttribute('href', RagLogin.loginHref(here));
  }
}

/**
 * 页头那把额度的**当场读数**（`/api/v1/agent/token/budget`）。
 *
 * 🔴 **走 `RagAuth.apiFetch`**（⛔ 不是裸 `fetch`）—— 它负责贴认证头、
 *    以及"令牌过期就换一把再试"（access_token 只活 **15 分钟**）。
 * ⚠️ **取不到就保持原样**（只显示「已登录」）—— 见文件头约定 ③。
 * ⚠️ 单位是 **token**（：额度全套统一到 token 口径，⛔ 不是"次"）。
 */
function refreshQuota() {
  const n = el(IDS.quota);
  if (!n) return;
  if (typeof RagAuth === 'undefined' || !RagAuth || typeof RagAuth.apiFetch !== 'function') return;
  RagAuth.apiFetch('/api/v1/agent/token/budget')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (b) {
      // 🔴 判据是 **"它得是个有限的数"**，⛔ 不是 `!== undefined` ——
      //    后者挡不住 `null`/`NaN`（拼进字符串会印成 `NaN`，而页面上看不出哪里错了）。
      if (!b || typeof b.remaining !== 'number' || !isFinite(b.remaining)) return;
      const num = function (v) { return (typeof v === 'number' && isFinite(v)) ? String(v) : '—'; };
      const seg = [];
      seg.push('LLM-tokens ' + num(b.llm_tokens_today)
               + ' · 今日还剩 ' + num(b.llm_remaining_today) + ' tokens');
      seg.push('Embedding-tokens ' + num(b.embedding_tokens_today)
               + ' · 今日还剩 ' + num(b.embedding_remaining_today) + ' tokens');
      // ⚠️「其它」**只在真的 > 0 时才出现** —— 平时那一格是 0，摆出来只是噪音；
      //    而**真的非零时不摆**会让两个数加起来对不上总数（不许那样）。
      if (typeof b.other_tokens_today === 'number' && b.other_tokens_today > 0) {
        seg.push('其它 ' + num(b.other_tokens_today));
      }
      n.textContent = ' · ' + seg.join(' · ');
      syncHeaderHeight();     // 🔴 这一串比导航还长 ⇒ 它一进来页头就可能是两行了（见下）
    })
    .catch(function () { /* 取不到 ⇒ 就不显示数字，页头仍写着「已登录」 */ });
}

/** 「退出」按钮的接线。⚠️ **幂等**（`mount()` 调两次不会挂两个监听）· 没这颗按钮 ⇒ 跳过。 */
let _logoutWired = false;
function wireLogout() {
  if (_logoutWired) return;
  const b = el(IDS.logout);
  if (!b) return;
  b.addEventListener('click', function () {
    RagAuth.logout();
    // ⚠️ 退出去登录页**不带 `next`** —— 那是"我不想再进来了"，不是"登完接着干"。
    location.assign('/login');
  });
  _logoutWired = true;
}

/**
 * 🔴 让 `--header-h` **跟着页头的真实高度走**。
 *
 * ⚠️ **它不只是"页头自己的高度"** —— `--header-h` 还被这几处用着：
 *    `html` 的 `scroll-padding-top` · `.side` / `.aside` / `.form` 的 sticky `top`
 *    `.ep` 的 `scroll-margin-top`。
 * 🔴 登录之后页头会变成**两行**（那一串额度比导航还长 ⇒ `styles.css` 的 `.hi` 允许换行）
 *    ⇒ 不跟着改，那几处会**短掉一整行**：实测 sticky 的 `.form` 会**钻到页头底下**。
 *    （`.form` 正好就在有登录控件的 `agent` / `stream` / `rag` / `kb` 四页上。）
 * ⚠️ 拿不到 DOM（Node 里跑测试 / 元素不在）⇒ **什么都不做**，⛔ 不抛。
 */
function syncHeaderHeight() {
  if (typeof document === 'undefined' || !document || !document.documentElement) return;
  const h = (typeof document.querySelector === 'function')
    ? document.querySelector('.site-header') : null;
  if (!h || typeof h.getBoundingClientRect !== 'function') return;
  const px = Math.round(h.getBoundingClientRect().height);
  if (!(px > 0)) return;        // 🔴 量到 0（元素没布局 / 页面隐藏）⇒ 宁可**不动**，
                                //    否则会把 `--header-h` 写成 0，那几处 sticky 全贴到顶上去
  document.documentElement.style.setProperty('--header-h', px + 'px');
}

/** 接线：量一次 + **窗口一变就重量一次**（窗口窄了会把页头挤成两行或三行）。⚠️ 幂等。 */
let _heightWired = false;
function wireHeaderHeight() {
  if (_heightWired) return;
  _heightWired = true;
  syncHeaderHeight();
  if (typeof window !== 'undefined' && window && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', syncHeaderHeight);
  }
}

/**
 * ⭐ 页面装配的唯一入口：**登录态就位之后再起页面**。
 *
 * 🔴 为什么必须"先就位再起"：没登录时页面若照旧发请求，第一批全是 401 ——
 *    而用户什么都没做错（见文件头约定 ②）。
 *
 * @param {() => void} [onReady] 已登录之后跑页面自己的起法。⚠️ **同步回调**。
 * @returns {boolean} 现在是不是已登录
 */
function mount(onReady) {
  const on = !!(typeof RagAuth !== 'undefined' && RagAuth && RagAuth.isLoggedIn
                && RagAuth.isLoggedIn());
  applyMode(on);
  // 🔴 **两条路都要接** —— 页头的高度**两种状态不一样**（登录后多一行额度），
  //    所以不是"只在登录时才量"。放在这里 = `applyMode` 之后、分支之前。
  wireHeaderHeight();
  if (!on) {
    pointLoginLink();
    return false;                       // 约定 ②：⛔ 不跑 `onReady`
  }
  refreshQuota();
  wireLogout();
  if (typeof onReady === 'function') onReady();
  return true;
}

const RagAuthBar = { IDS, applyMode, pointLoginLink, refreshQuota, syncHeaderHeight, mount };

if (typeof module !== 'undefined' && module.exports) {
  module.exports = RagAuthBar;               // Node（`node --test` / `require`）
}
if (typeof window !== 'undefined') {
  window.RagAuthBar = RagAuthBar;            // 浏览器（`<script src>` 之后就是全局）
}

})();   // ⛔ 别去掉 —— 见文件头：去掉它，`el` 会与页面自己那个互相顶掉，且**不报错**
