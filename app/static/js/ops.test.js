'use strict';
/* `ops.js` 的用例 —— ⛔ 零 npm 依赖（只 node:test + node:assert）。
   跑法：node --test app/static/js/*.test.js（已进 ci.yml 的 glob） */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, 'ops.js'), 'utf8');
const O = require('./ops.js');

/* ══════════════ 1 · 🔴 URL 前缀 与【根路径那三条】 ══════════════ */

test('🔴 本页所有端点常量都必须带 `/api/v1` 前缀', () => {
  // ⚠️ 为什么单列一条：`app/tests/test_web_pages.py` 只扫**页面里的字面量**，
  //    而本页走 helper ⇒ 对它**空过**（那份文件自己写着这条）⇒ 前缀保护只落在本文件。
  for (const k of Object.keys(O.PATHS)) {
    assert.match(O.PATHS[k], /^\/api\/v1\//, `${k} 丢了前缀：${O.PATHS[k]}`);
  }
});

test('🔴🔴 根路径那三条【⛔ 不许写成字面量】—— 否则会被页面守卫拦下，而那条红会引人改错地方', () => {
  // 🔴 那条守卫要求页面里的 URL 字面量以 `/api/v1` 开头；而 `/health` `/ready` `/metrics`
  //    **天生就在根路径上** ⇒ 硬写就是红，而**门是对的**（曾经有人去改门）。
  //    📄 同款处置的先例 ⇒ （旧「系统与执行器」页那 3 条）。
  // ⚠️ 反证：把 `rootPath` 改成 `return '/' + name;` 之外直接写 `'/health'` ⇒ 本条当场红。
  // 🔴 **先把注释剥掉**（本仓的既有做法）—— 否则注释里一提那串就红，
  //    而"要提那串"恰恰是必须的（读代码的人得知道为什么不能那么写）。
  const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '')      // 块注释
                  .replace(/(^|[^:])\/\/[^\n]*/g, '$1');   // 行注释（⚠️ 别咬到 http:）
  // ⚠️ **只查"请求的实参"**（与 `test_web_pages.py` 那条前缀门同一个形状）——
  //    表里那些给人读的说明文字（"打谁"那一列、点名哪条取不到）**不算**。
  const RE = /(?:getJSON|fetch)\(\s*['"`]([^'"`]*)/g;
  const bad = [];
  let m;
  while ((m = RE.exec(code)) !== null) {
    if (m[1].indexOf('/api/v1') !== 0) { bad.push(m[1]); }
  }
  assert.deepStrictEqual(bad, [],
    '这些请求的 URL 字面量没带 /api/v1（页面守卫会红）：' + JSON.stringify(bad));
  // 根路径那三条【必须】走 rootPath 拼（⛔ 别出现 `fetch('/health')`）
  assert.ok(code.indexOf('rootPath') !== -1, 'ops.js 里没找到 rootPath —— 那三条根路径怎么拼的？');
  assert.deepStrictEqual(O.ROOT_NAMES, ['health', 'ready', 'metrics']);
});

test('`rootPath` 只认表里那三个名字（⛔ 别拿它拼任意串）', () => {
  assert.strictEqual(O.rootPath('health'), '/health');
  assert.strictEqual(O.rootPath('ready'), '/ready');
  assert.strictEqual(O.rootPath('metrics'), '/metrics');
  assert.strictEqual(O.rootPath('admin/secret'), '', '表外的名字必须回空串');
  assert.strictEqual(O.rootPath('../etc/passwd'), '');
});

/* ══════════════ 2 · 🔴 `deferred…` 那一条【不许画成通过】 ══════════════ */

test('🔴🔴 /health 里的 `embedding_api` 恒为一句「没检查」—— ⛔ 不许显示成"通"', () => {
  // 🔴 真返回（2026-10-13 实测）：
  //    {"status":"healthy","checks":{"database":"ok","redis":"ok",
  //      "embedding_api":"deferred to external monitoring"}}
  //    ⇒ 那个字段**不是** ok / 不 ok，而是一句"交给外部监控了" = **它根本没被检查**。
  //    ⛔ 把它画成 ✅ 就是对访客撒谎（"向量化接口好着呢"，而没人验过）。
  const rows = O.healthChecks({
    checks: { database: 'ok', redis: 'ok', embedding_api: 'deferred to external monitoring' },
  });
  const map = {};
  rows.forEach((r) => { map[r.key] = r.state; });
  assert.strictEqual(map.database, '通');
  assert.strictEqual(map.redis, '通');
  assert.strictEqual(map.embedding_api, '没检查', `实得 ${map.embedding_api}`);
  assert.strictEqual(rows.length, 3);
});

test('healthChecks：缺字段 ⇒ 「取不到」（⛔ 不是「通」）', () => {
  const rows = O.healthChecks({ checks: { database: 'ok' } });
  const map = {};
  rows.forEach((r) => { map[r.key] = r.state; });
  assert.strictEqual(map.database, '通');
  assert.strictEqual(map.redis, '取不到');
  assert.strictEqual(map.embedding_api, '取不到');
  assert.strictEqual(O.healthChecks(null).length, 3, '整个返回取不到也要回三行，不能崩');
});

/* ══════════════ 3 · 🔴 判决：三态 + 「取不到 ≠ 正常」 ══════════════ */

const ALL_OK = {
  health: { status: 'healthy', checks: { database: 'ok', redis: 'ok', embedding_api: 'deferred' } },
  ready: { status: 'ready' },
  pending: { count: 0, items: [] },
  cache: { cached_embeddings_count: 146, sample_keys: [] },
  count: { total_documents: 225 },
  quota: { role: 'free', daily_limit: 60000, remaining: 60000, used_today: 0, user_name: 'u-x' },
  rate: { remaining_tokens: 20, capacity: 20, rate: 3.0, user_name: 'u-x' },
  toolHealth: { tools: { calculator: { status: 'healthy', last_checked: 1791859853.37 } } },
  metrics: '# TYPE api_requests_total counter\napi_requests_total 5.0\n',
};

test('🔴 全好 ⇒ `ok`，且那句话里**不吹牛**（明说"不是健康证明"）', () => {
  const v = O.verdictOf(ALL_OK);
  assert.strictEqual(v.level, 'ok');
  assert.match(v.sentence, /都好/);
  assert.match(v.next, /兜底值/, '必须提醒"取到的值里有的可能是兜底值"');
});

test('🔴🔴 **取不到 ≠ 正常** —— 少一条就掉出 `ok`，并把它点名', () => {
  // 这条是本页存在的理由：那七处坑里最狠的就是"看着正常、其实取不到"。
  // ⚠️ 反证：把 verdictOf 里 `missing.length` 那一支删掉 ⇒ 本条红。
  const p = Object.assign({}, ALL_OK, { quota: null });
  const v = O.verdictOf(p);
  assert.notStrictEqual(v.level, 'ok', '少一条取不到却还判 ok ⇒ 这一页就白做了');
  assert.strictEqual(v.level, 'warn');
  assert.ok(v.missing.indexOf('/debug/quota') !== -1, JSON.stringify(v.missing));
  assert.match(v.sentence, /不能说正常/);
});

test('🔴 服务自报不健康 / 没就绪 ⇒ `bad`，并说清是哪一条', () => {
  const v = O.verdictOf(Object.assign({}, ALL_OK, { health: { status: 'unhealthy', checks: {} } }));
  assert.strictEqual(v.level, 'bad');
  assert.match(v.sentence, /不健康|不对劲/);

  const v2 = O.verdictOf(Object.assign({}, ALL_OK, { ready: { status: 'starting' } }));
  assert.strictEqual(v2.level, 'bad');
});

test('🔴 有工具不健康 ⇒ `bad`（而且**点它的名**）', () => {
  const v = O.verdictOf(Object.assign({}, ALL_OK, {
    toolHealth: { tools: { web_search: { status: 'unhealthy' }, calculator: { status: 'healthy' } } },
  }));
  assert.strictEqual(v.level, 'bad');
  assert.match(v.sentence, /web_search/);
});

test('🔴🔴 工具健康表是【空的】⇒ `warn`（**空 ≠ 全好**）', () => {
  // 🔴 服务刚起时 `_tool_health` 就是 `{}`（`app/tools/tool_health.py:26`）
  //    ⇒ 把它渲染成"没有异常"就是骗人：**没人验过**。
  const v = O.verdictOf(Object.assign({}, ALL_OK, { toolHealth: { tools: {} } }));
  assert.strictEqual(v.level, 'warn');
  assert.match(v.sentence, /空/);
});

test('🔴 有人在等 ⇒ `warn` 且给出下一步（去把待接管批了）', () => {
  const v = O.verdictOf(Object.assign({}, ALL_OK, { pending: { count: 3, items: [] } }));
  assert.strictEqual(v.level, 'warn');
  assert.match(v.sentence, /3 条/);
  assert.match(v.next, /批/);
});

test('🔴 一条都没探回来 ⇒ `unknown`（**连判决都做不出**），⛔ 不是 `ok`、也不是 `bad`', () => {
  const v = O.verdictOf({});
  assert.strictEqual(v.level, 'unknown');
  assert.match(v.sentence, /一条都没探回来|判决/);
  assert.deepStrictEqual(v.missingKeys.slice().sort(), O.PROBE_KEYS.slice().sort(),
    '按表算出来的那一份必须**一个不落**：' + JSON.stringify(v.missingKeys));
  assert.strictEqual(v.missing.length, O.PROBE_KEYS.length, '给人读的那份也要一样多');
});

test('⚠️ 缓存 0 / 文档 0 **不算异常**（它们是合法值，页面只是"读到了 0"）', () => {
  const v = O.verdictOf(Object.assign({}, ALL_OK, {
    cache: { cached_embeddings_count: 0, sample_keys: [] }, count: { total_documents: 0 },
  }));
  assert.strictEqual(v.level, 'ok', '冷启动的 0 不该被当成故障');
});

/* ══════════════ 4 · 🔴 `/metrics` 是 Prometheus 文本 ══════════════ */

test('🔴 `promSummary` 只按【文本】摘几个数，⛔ 不 JSON.parse', () => {
  const text = [
    '# HELP api_requests_total Total API requests',
    '# TYPE api_requests_total counter',
    'api_requests_total{method="GET",path="/health"} 12.0',
    'api_requests_total{method="POST",path="/api/v1/rag/search"} 3.0',
    '# TYPE api_requests_in_progress gauge',
    'api_requests_in_progress 1.0',
    '# TYPE stream_cancelled_total counter',
    'stream_cancelled_total 2.0',
  ].join('\n');
  const s = O.promSummary(text);
  assert.strictEqual(s.ok, true);
  assert.strictEqual(s.counts.api_requests_total, 12, '同名多条 ⇒ 取第一条（⚠️ 别求和，那是我们的假设）');
  assert.strictEqual(s.counts.api_requests_in_progress, 1);
  assert.strictEqual(s.counts.stream_cancelled_total, 2);
  assert.match(s.plain, /Prometheus|text\/plain/);
  assert.ok(s.lines >= 4, '行数要真数出来');
});

test('`promSummary`：空 / 取不到 ⇒ `ok:false`，⛔ 不编数（那几个格保持 null）', () => {
  const s = O.promSummary('');
  assert.strictEqual(s.ok, false);
  assert.strictEqual(s.counts.api_requests_total, null);
  assert.strictEqual(O.promSummary(null).ok, false);
});

/* ══════════════ 4.5 · 🔴 composeProbes：那个把自己骗了的 bug ══════════════ */

test('🔴🔴 `composeProbes`：摘要另存一格，⛔ **不许把原文那条删掉**（判决还在读它）', () => {
  // 🔴 这是 2026-10-13 在真浏览器上撞出来的真 bug：DOM 层拿到 /metrics 正文后**把它删了**、
  //    只留摘要 ⇒ 判决那一句判「这条回来了没有」⇒ 判成"取不到"
  //    ⇒ 页面一边说「9/9 拿到了」、一边说「有 1 条取不到（metrics）」，**自相矛盾**。
  // ⚠️ 反证：在 `composeProbes` 里把 `metrics` 那行改成 `null` ⇒ 本条当场红。
  const res = O.composeProbes({ metrics: '# TYPE x counter\nx 1.0\n', health: { status: 'healthy' } });
  assert.ok(res.metrics, '原文必须留着 —— 判决判的就是"它回来了没有"');
  assert.strictEqual(res.metricsText.ok, true, '摘要也要有（渲染那一格用它）');
  assert.strictEqual(res.metricsText.types, 1);
  const v = O.verdictOf(res);
  assert.ok(v.missingKeys.indexOf('metrics') === -1,
    '指标那条不该被点名"取不到"：' + JSON.stringify(v.missingKeys));
  // 取不到时两格都是 null（⛔ 不许编一个空摘要出来）
  const none = O.composeProbes({});
  assert.strictEqual(none.metrics, null);
  assert.strictEqual(none.metricsText, null);
  assert.strictEqual(none.health, null);
});

/* ══════════════ 5 · 取值与印法：缺值一律 `—` ══════════════ */

test('🔴 配额那一格**必须把 fail-open 那句一起带出来**（⛔ 只印个数会骗人）', () => {
  // 🔴 `get_daily_token_usage` 是 fail-open：查库失败 ⇒ 回 0 ⇒ `remaining` = 满额度
  //    ⇒ 看着像"没花、很健康"，而真相是**查挂了**。页面**分辨不出** ⇒ 只能如实标明。
  const r = O.quotaRow({ role: 'free', daily_limit: 60000, used_today: 0, remaining: 60000 },
                       '可能是兜底值');
  assert.strictEqual(r.used, '0');
  assert.strictEqual(r.limit, '60000');
  assert.strictEqual(r.left, '60000');
  assert.strictEqual(r.caution, '可能是兜底值');
  const empty = O.quotaRow(null);
  assert.strictEqual(empty.used, '—');
  assert.strictEqual(empty.left, '—', '⛔ 取不到不许印 0');
});

test('🔴🔴 后端回字符串「无限」时⛔ 不许印成 `—`（`—` 在本页 = 取不到）', () => {
  // 🔴 `/debug/quota` 在预算为 `inf` 时回的是【字符串】「无限」（不是数字）。
  //    用数值格式化器会把它印成 `—`，而 `—` 在这一页**专指"取不到"**
  //    ⇒ 那就把「没上限」谎报成「探针挂了」。两种，页面上必须分得开。
  const r = O.quotaRow({ role: 'admin', daily_limit: '无限', remaining: '无限', used_today: 0 });
  assert.strictEqual(r.limit, '无限');
  assert.strictEqual(r.left, '无限');
  assert.strictEqual(r.used, '0');
  assert.strictEqual(O.quotaRow(null).left, '—', '真取不到仍然是 —');
  assert.strictEqual(O.fmtVal(undefined), '—');
});

test('限流那一格：`capacity` / `rate` 是**代码常量**（页面要标出来）', () => {
  const r = O.rateRow({ remaining_tokens: 20, capacity: 20, rate: 3.0, user_name: 'u-x' });
  assert.strictEqual(r.left, '20');
  assert.strictEqual(r.capacity, '20');
  assert.strictEqual(r.rate, '3 /秒');
  assert.strictEqual(O.rateRow(null).left, '—');
});

test('工具健康逐行：名称 / 状态 / 版本 / 上次探到多久以前', () => {
  const now = 1000000;
  const out = O.toolRows(
    { tools: { calculator: { status: 'healthy', last_checked: now - 90 },
               web_search: { status: 'unhealthy', last_checked: now - 7200 } } },
    { tool_versions: { calculator: '1.0.0', web_search: '2.0.0' } }, now);
  assert.strictEqual(out.empty, false);
  assert.strictEqual(out.rows[0].name, 'calculator');
  assert.strictEqual(out.rows[0].ok, true);
  assert.strictEqual(out.rows[0].version, '1.0.0');
  assert.strictEqual(out.rows[0].ago, '1 分钟前');
  assert.strictEqual(out.rows[1].ok, false);
  assert.strictEqual(out.rows[1].ago, '2 小时前');
  // ⚠️ 空表必须被**标明是空的**（上游要据此说"空 ≠ 全好"）
  assert.strictEqual(O.toolRows({ tools: {} }, {}, now).empty, true);
  assert.strictEqual(O.toolRows(null, null, now).empty, true);
  assert.strictEqual(O.toolRows(null, null, now).rows.length, 0);
});

test('`fmtAgo`：刚刚 / 分钟 / 小时 / 天；取不到 ⇒ `—`（⛔ 不写"从未"那种像结论的词）', () => {
  const now = 1000000;
  assert.strictEqual(O.fmtAgo(now - 5, now), '刚刚');
  assert.strictEqual(O.fmtAgo(now - 300, now), '5 分钟前');
  assert.strictEqual(O.fmtAgo(now - 7200, now), '2 小时前');
  assert.strictEqual(O.fmtAgo(now - 200000, now), '2 天前');
  assert.strictEqual(O.fmtAgo(null, now), '—');
  assert.strictEqual(O.fmtAgo(undefined, now), '—');
});

/* ══════════════ 6 · 探针表本身 ══════════════ */

test('🔴 每一行探针都要写清「正常长什么样」，且**有坑的行必须写明那个坑**', () => {
  assert.ok(O.PROBES.length >= 6, '形态稿上那张表是 6 行');
  O.PROBES.forEach(function (p) {
    assert.ok(p.what && p.norm, `探针 ${p.id} 缺 what/norm`);
    assert.ok(Array.isArray(p.who) && p.who.length, `探针 ${p.id} 没写打谁`);
  });
  // 那七处坑里，**页面能分辨的**必须写在页面上；**分辨不了的**（fail-open 两条）必须如实标明
  assert.match(O.probeOf('limits').note, /fail-open|兜底/, '「我这个身份的限」那行必须写明 fail-open');
  assert.match(O.probeOf('tools').note, /空|不是免费/, '工具健康那行必须写明空表与探活有代价');
  assert.match(O.probeOf('metrics').note, /Prometheus|不是 JSON/);
  assert.strictEqual(O.probeOf('不存在').id, 'alive', '认不出的 id 回第一条，⛔ 不抛');
});

/* ══════════════ 7 · 🔴 markdown 那条门【看不见的缝】（本页自己补一道） ══════════════ */

test('🔴🔴 页面内联脚本里【会变成页面文字】的字符串，⛔ 不许带 markdown 记号', () => {
  // ⚠️ 同 `agent.test.js` 里那一条：`test_web_pages.py` 的 markdown 门**剥掉了 `<script>`**
  //    ⇒ 内联脚本里会被 `textContent` 印到页面上的字符串，它**扫不到**。
  const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'ops.html'), 'utf8');
  const blocks = html.match(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/gi) || [];
  assert.ok(blocks.length >= 2, `一段内联脚本都没扫到 ⇒ 本条空过（扫到 ${blocks.length} 段）`);
  const bad = [];
  for (const b of blocks) {
    const body = b.replace(/^<script[^>]*>/i, '');
    const strs = body.match(/'[^'\n]*'|"[^"\n]*"/g) || [];
    for (const s of strs) {
      if (s.indexOf('**') !== -1 || s.indexOf('`') !== -1) { bad.push(s.slice(0, 100)); }
    }
  }
  assert.deepStrictEqual(bad, [],
    '这些字符串会原样印在页面上：\n  ' + bad.join('\n  '));
});

/* ══════════════ 8 · 🔴 只许漏一个全局键（IIFE） ══════════════ */

test('🔴🔴 `ops.js` 对外只许留【一个】全局键（`RagOps`）', () => {
  // ⚠️ 这条是拿一个真事故换来的：经典脚本的顶层 `function`/`const` **全是全局的**，
  //    与页面自己的顶层绑定撞上 ⇒ 整段内联脚本一行都不执行，而当时所有用例全绿。
  //    📄 复盘
  const sandbox = { module: undefined, window: {}, console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  vm.runInContext(SRC, sandbox);
  const added = Object.keys(sandbox).filter((k) => !before.has(k));
  assert.deepStrictEqual(added, ['RagOps'],
    `\`ops.js\` 往 window 上多挂了：${added.join(', ')} ⇒ 与页面自己的顶层绑定撞车的面又回来了`);
});

test('`thread_id` 带 `ops-page-` 前缀', () => {
  assert.match(O.newThreadId(), /^ops-page-/);
});
