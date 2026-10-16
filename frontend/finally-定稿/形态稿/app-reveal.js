/* 形态稿 · 只从 `/3` 的 `app.js` 里搬【两块】：
 *
 *   · initReveal —— 滚动揭示（作者 2026-10-12 裁定「滚动揭示：乙」⇒ 照搬）
 *   · syncHeader —— 把页头**实际**高度写回 `--header-h`（吸顶定位与锚点避让要用）
 *
 * ⛔ 不搬 initCalc —— 它算的是 `/3` 那个**虚构产品**的定价（`¥38 / 百万 token` …），
 *    搬进来就是把编的数带进我们仓 ⇒ 违反硬约束①。
 * ⛔ 不搬 initSpy  —— 它服务"左栏跟随阅读"，本页没有那种侧栏。
 *
 * 🔴 阈值与兜底数字【逐字照抄 `/3`】（`rootMargin` / `threshold` / `1.2s`），⛔ 不是我自己定的。
 */
(function () {
  'use strict';

  var root = document.documentElement;
  var doc = document;

  /* ── 1 · 滚动揭示（照抄 /3）── */
  function initReveal() {
    if (!root.classList.contains('reveal') || !('IntersectionObserver' in window)) return;
    var items = doc.querySelectorAll('.rv');
    if (!items.length) return;

    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add('in');
        io.unobserve(e.target);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.03 });

    Array.prototype.forEach.call(items, function (el) { io.observe(el); });
    // 兜底：观察器失灵时全部显示，绝不留白
    window.setTimeout(function () {
      Array.prototype.forEach.call(items, function (el) { el.classList.add('in'); });
    }, 1200);
  }

  /* ── 2 · 页头高度回写（照抄 /3）── */
  function syncHeader() {
    var h = doc.querySelector('.site-header');
    if (!h) return;
    var v = Math.round(h.getBoundingClientRect().height);
    if (v > 0) root.style.setProperty('--header-h', v + 'px');
  }

  function init() {
    initReveal();
    syncHeader();
    window.addEventListener('resize', syncHeader);
  }

  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', init);
  else init();
})();
