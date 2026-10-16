'use strict';
/* `ops` 页（**系统观测**）的**纯逻辑** —— ⛔ 不碰 DOM、不发请求。
 *
 * ## 这一页的身份（作者 2026-10-12 定的那两半 · `过程定义 §四`）
 *
 * 同一个系统、**两种人看两种东西**：
 *   · **使用者本人** ⇒ 我花了多少 / 还剩多少 ⇒ 产出是【一组结果】⇒ 在每条链页的"花费"块里
 *   · **系统方（本页）** ⇒ 服务此刻正常吗 ⇒ 产出是【一个判断】（正常/异常 + 下一步）
 * ⚠️ 所以本页的**主产出是一句话**，⛔ 不是一堆数；数只是那句话的**依据**。
 * ⚠️ 它**给访客能看，但必须标清「这是系统方视角」**（页面头部那块标注）——
 *    与 S7 那条链的"独有约束"（界限=系统方）之间的口径差。
 *
 * ## 🔴 这一页真正的价值：**把"看着正常、其实取不到"分辨出来**
 *
 * 2026-10-13 逐条读码核出来的**七处**（⛔ 不是猜的，逐条有 `文件:行`）：
 *
 * | # | 哪里 | 会怎么骗人 |
 * |---|---|---|
 * | 1 | `/debug/quota` | 查库失败 ⇒ **fail-open 回 0** ⇒ `remaining` = 满额度 ⇒ **读成"没花、很健康"** |
 * | 2 | `/debug/rate_limit` | Redis 不通 ⇒ **报满桶**（`capacity`）⇒ 看着正常，而**限流其实已失效** |
 * | 3 | `/debug/cache_stats` | `count==0` **≠ 缓存坏了**（冷启动就是 0） |
 * | 4 | `/debug/count` | `total_documents==0` 是**合法值**（空库） |
 * | 5 | `/agent/tool_health` | 启动时是**空 `{}`**；且只有登记过的工具才体检（没登记的**静默跳过**） |
 * | 6 | `/agent/available_tools` | **没体检过（UNKNOWN）被算作 healthy** ⇒ "没人验过也说它健康" |
 * | 7 | `/health` | `checks.embedding_api` **恒为一句字符串**（"deferred to external monitoring"）⇒ **Embedding 根本没被检查** |
 *
 * ⇒ **页面上的处置**：能分辨的**当场分清**（如"没检查"≠"通过"、空列表≠全好）；
 *    **分辨不了的**（1 / 2 那两条 fail-open）**在那一格如实写明"这可能是兜底值"** —— ⛔ 不许假装它是真值。
 */

(function () {

/**
 * 🔴 **根路径那三条**（`/health` · `/ready` · `/metrics`）—— ⚠️ **不带前导斜杠**，用名字拼。
 *
 * 为什么写成这样（这是踩过的）：本仓那条页面守卫（`app/tests/test_web_pages.py`）要求
 * **页面里的 URL 字面量必须以 `/api/v1` 开头**，否则红 —— 而这三条**天生就在根路径上**。
 * ⛔ 硬把 `'/health'` 写进页面，会被那条门拦下，**而那条红会把人引向错误的修法**
 * （曾有人去改门，而门是对的）。⇒ **一个字面量都不写**，由名字拼出来。
 * 📄 同款处置的先例 ⇒ （旧「系统与执行器」页那 3 条根路径）。
 */
const ROOT_NAMES = ['health', 'ready', 'metrics'];

/** 把根路径的名字拼成路径。⚠️ 只接受表里的名字（⛔ 别拿它拼任意串）。 */
function rootPath(name) {
  return ROOT_NAMES.indexOf(name) === -1 ? '' : ('/' + name);
}

/**
 * 本页要打的那几条（⚠️ 全部必须以 `/api/v1` 开头 · 根路径那三条走 `rootPath()`）。
 *
 * 🔴 **故意写成【顶层大写常量】**（⛔ 不是直接塞进下面那个对象里）——
 *    让 `app/tests/test_response_model_declared.py` 的尺子②**扫得到**它们。
 *    ⚠️ 写成对象属性的话那道门**看不见**，这几条就**长期在门外**（"漏扫 ≠ 通过"）。
 */
const CACHE_STATS_PATH = '/api/v1/debug/cache_stats';
const COUNT_PATH = '/api/v1/debug/count';
const QUOTA_PATH = '/api/v1/debug/quota';
const RATE_LIMIT_PATH = '/api/v1/debug/rate_limit';
const TOOL_HEALTH_PATH = '/api/v1/agent/tool_health';
const TOOL_VERSIONS_PATH = '/api/v1/agent/tool_versions';
const PENDING_PATH = '/api/v1/agent/pending';

const PATHS = {
  cache: CACHE_STATS_PATH,
  count: COUNT_PATH,
  quota: QUOTA_PATH,
  rate: RATE_LIMIT_PATH,
  toolHealth: TOOL_HEALTH_PATH,
  toolVersions: TOOL_VERSIONS_PATH,
  pending: PENDING_PATH,
};

/**
 * 🔴 **分项探针**（页面右栏那张表，一行一件事）。
 *
 * 每行三样（照形态稿）：`看什么` · `打谁` · **`正常长什么样`** ——
 * ⚠️ 第三列是**给人读的判据**，⛔ 不是"数据"；数据在页面上的「现在」那一列。
 * `note` = 这一条**特有的坑**（上面那张表里的一条），有坑的行**必须**写清，
 * 否则访客会把"看着正常"当成"正常"。
 */
const PROBES = [
  {
    id: 'alive', what: '服务活着吗',
    who: ['/health', '/ready'],
    norm: '两个都通（health 里 database 与 redis 都是 ok）',
    note: '⚠️ /health 里的 embedding_api **恒为一句「交给外部监控」** —— 它【根本没检查】Embedding。'
        + '看到 status=healthy **不代表 embedding 可用**。',
  },
  {
    id: 'backlog', what: '有没有人在等',
    who: ['/api/v1/agent/pending'],
    norm: '条数**随事而定** —— 看它是不是在涨，⛔ 不是"必须是 0"',
    note: '⚠️ 这张表是**进程内存** —— 服务重启就空（页面上的 0 可能是"重启过"，不是"没人等"）。',
  },
  {
    id: 'cache', what: '缓存对不对',
    who: ['/api/v1/debug/cache_stats', '/api/v1/debug/count'],
    norm: '缓存键有条目、库里有文档 —— 但**两个都是 0 也不等于坏了**',
    note: '⚠️ 冷启动的缓存**就是 0**；空库的文档数**也是 0**。这两个 0 都是**合法值**。',
  },
  {
    id: 'limits', what: '我这个身份的限',
    who: ['/api/v1/debug/quota', '/api/v1/debug/rate_limit'],
    norm: '额度与桶都在生效（两个都取得到）',
    note: '⚠️ 这两条**都会 fail-open**：额度那条查库失败会**回 0**（⇒ 看着"没花过"），'
        + '桶那条 Redis 不通会**报满桶**（⇒ 看着"没限过"）。'
        + '🔴 **页面分辨不出**它给的是真值还是兜底值 ⇒ 这一格只能**如实标明这一点**。',
  },
  {
    id: 'tools', what: '工具健康',
    who: ['/api/v1/agent/tool_health', '/api/v1/agent/tool_versions'],
    norm: '每个工具自己的状态（healthy / unhealthy）',
    note: '⚠️ 三件事：① 服务刚起时它是**空表**（**空 ≠ 全好**）；'
        + '② 只有**登记过**的工具才体检，没登记的**静默跳过**；'
        + '③ ⛔ 探活**不是免费的** —— 它会**真去打那个工具**（含联网搜索与沙箱跑代码）。',
  },
  {
    id: 'metrics', what: '接口在不在动',
    who: ['/metrics'],
    norm: '指标在动（请求数、耗时、在飞数、被取消的流）',
    note: '⚠️ /metrics 回的是 **Prometheus 文本**（`text/plain`）—— ⛔ **不是 JSON**，'
        + '页面**不许 JSON.parse**，只按文本摘出关键几个。',
  },
];

function probeOf(id) {
  for (let i = 0; i < PROBES.length; i++) { if (PROBES[i].id === id) { return PROBES[i]; } }
  return PROBES[0];
}

/* ══════════════ 取值与印法（⚠️ 缺值一律 `—`，⛔ 不编 0） ══════════════ */

function _num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
function fmtNum(v) { const n = _num(v); return n === null ? '—' : String(n); }

/**
 * 🔴 **只给"合法的非数"用**：值不是数、但**也不是缺值** ⇒ **原样透出**。
 *
 * 为什么要有它：`/debug/quota` 的 `daily_limit` / `remaining` 在预算为 `inf` 时，
 * 后端回的是**字符串「无限」**（不是数字）。用 `fmtNum` 会把它印成 `—`，
 * 而 `—` 在这一页的意思是**「取不到」** ⇒ 那就把"没上限"谎报成"探针挂了"。
 * ⚠️ `undefined` / `null` 仍然回 `—`（那才是真取不到）。
 */
function fmtVal(v) {
  if (v === null || v === undefined) { return '—'; }
  if (typeof v === 'number' && !isFinite(v)) { return '—'; }
  return String(v);
}

/** epoch 秒 ⇒ 「刚刚 / N 分钟前 / N 小时前」。⚠️ 取不到 ⇒ `—`（⛔ 不印"从未"那种像结论的词）。 */
function fmtAgo(sec, nowSec) {
  const s = _num(sec);
  if (s === null) { return '—'; }
  const now = _num(nowSec) === null ? Date.now() / 1000 : nowSec;
  const d = Math.max(0, now - s);
  if (d < 60) { return '刚刚'; }
  if (d < 3600) { return Math.floor(d / 60) + ' 分钟前'; }
  if (d < 86400) { return Math.floor(d / 3600) + ' 小时前'; }
  return Math.floor(d / 86400) + ' 天前';
}

/**
 * `/health` 的三个 checks ⇒ 三行（⚠️ **`deferred…` 那一条不许画成通过**）。
 *
 * 🔴 判据取自真返回（2026-10-13 实测）：
 * `{"status":"healthy","checks":{"database":"ok","redis":"ok","embedding_api":"deferred to external monitoring"}}`
 * ⇒ `embedding_api` 的值**不是** ok/不 ok，而是一句**说明它没被检查**的话。
 */
function healthChecks(resp) {
  const c = (resp || {}).checks || {};
  const names = { database: '数据库', redis: '缓存（Redis）', embedding_api: '向量化接口' };
  return Object.keys(names).map(function (k) {
    const v = c[k];
    let state;
    if (v === undefined || v === null) { state = '取不到'; }
    else if (v === 'ok') { state = '通'; }
    // 🔴 **这一支是这个函数存在的理由**：值不是 ok、也不是失败，而是**一句"没检查"**。
    else if (String(v).indexOf('deferred') !== -1) { state = '没检查'; }
    else { state = String(v); }
    return { name: names[k], key: k, state: state };
  });
}

/** 配额那一格 —— 🔴 **必须把 fail-open 那句一起带出去**，⛔ 不只是印个数。 */
function quotaRow(resp, caution) {
  const r = resp || {};
  return {
    role: r.role == null ? '—' : String(r.role),
    used: fmtVal(r.used_today),
    limit: fmtVal(r.daily_limit),
    left: fmtVal(r.remaining),
    // ⚠️ 「没花过」与「查库失败兜出来的 0」在返回里**长得一模一样** ⇒ 这句必须跟着数字一起出现。
    caution: caution == null ? '' : String(caution),
  };
}

/** 限流那一格 —— ⚠️ `capacity` / `rate` 是**代码常量**（后端注释明写要让前端标出来）。 */
function rateRow(resp) {
  const r = resp || {};
  return {
    left: fmtVal(r.remaining_tokens),
    capacity: fmtVal(r.capacity),
    rate: _num(r.rate) === null ? (fmtVal(r.rate) === '—' ? '—' : fmtVal(r.rate)) : (String(r.rate) + ' /秒'),
    forWho: r.user_name == null ? '—' : String(r.user_name),
  };
}

/** 工具健康 ⇒ 一行一个工具（⚠️ 空表要**明说空**，⛔ 不许渲染成"没有异常"）。 */
function toolRows(healthResp, versionsResp, nowSec) {
  const t = (healthResp || {}).tools || {};
  const v = (versionsResp || {}).tool_versions || {};
  const names = Object.keys(t);
  return {
    empty: names.length === 0,
    rows: names.map(function (n) {
      const rec = t[n] || {};
      return {
        name: n,
        status: rec.status == null ? '—' : String(rec.status),
        ok: String(rec.status) === 'healthy',
        version: v[n] == null ? '—' : String(v[n]),
        ago: fmtAgo(rec.last_checked, nowSec),
      };
    }),
  };
}

/**
 * 🔴 **`/metrics` 是 Prometheus 文本** —— ⛔ 不许 `JSON.parse`。
 *
 * @returns `{ok, lines, counts, plain}` —— `counts` 只摘我们**真的懂**的那几个
 *   （`api_requests_total` / `api_requests_in_progress` / `stream_cancelled_total`）；
 *   ⛔ **别去解析整份**：那不是我们的格式，解析器一升级就会静默读错。
 */
function promSummary(text) {
  const s = String(text == null ? '' : text);
  const lines = s.split('\n');
  const want = { api_requests_total: null, api_requests_in_progress: null, stream_cancelled_total: null };
  const keys = Object.keys(want);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line || line.charAt(0) === '#') { continue; }
    for (let k = 0; k < keys.length; k++) {
      const name = keys[k];
      if (line.indexOf(name) === 0) {
        const parts = line.split(' ');
        const val = parts[parts.length - 1];
        if (want[name] === null && val !== '' && isFinite(Number(val))) { want[name] = Number(val); }
      }
    }
  }
  return {
    ok: lines.length > 1,
    lines: lines.filter(function (l) { return l.trim() !== ''; }).length,
    //: `# TYPE` 的行数 —— ⚠️ 它数的是**暴露出来的指标名个数**，⛔ 不是"有多少个值"
    //: （一个指标名下可以有**很多条带标签的序列** ⇒ ⛔ 别把任何一条当成"总数"）。
    types: lines.filter(function (l) { return l.indexOf('# TYPE ') === 0; }).length,
    counts: want,
    plain: 'Prometheus 文本（text/plain）—— ⛔ 不是 JSON',
  };
}

/* ══════════════ 🔴 判决：本页的主产出 ══════════════ */

/**
 * 🔴 **这一页要探的那几样**（键名 = `verdictOf` 收的那个对象里的键）。
 *
 * ⚠️ 它是一张**表**而不是散在各处的 `if` —— 好让「一条都没探回来」这种判据**跟着它走**
 *    （⛔ 别手数一个阈值：加一条探针就会**静默失准**，而没有任何东西会红）。
 */
const PROBE_KEYS = ['health', 'ready', 'pending', 'cache', 'count', 'quota',
                    'rate', 'toolHealth', 'metrics'];

/** 每一个键**展示时写什么**（页面上的点名、判决句里那串）。⚠️ 与上面那张表**同序同长**。 */
const PROBE_LABEL = {
  health: '/health', ready: '/ready', pending: '/agent/pending',
  cache: '/debug/cache_stats', count: '/debug/count', quota: '/debug/quota',
  rate: '/debug/rate_limit', toolHealth: '/agent/tool_health', metrics: '/metrics',
};

/**
 * 把各条探针的真实结果拼成**一句话判决**（正常 / 哪里不对劲 + 下一步）。
 *
 * 🔴 **三态，⛔ 不是两态**：
 *   · `ok`      —— 该看的都看到了，而且都好
 *   · `warn`    —— **有的东西取不到 / 有的事在等人** ⇒ 明说，⛔ 不许算成正常
 *   · `bad`     —— 明确不对（服务不健康 / 不就绪 / 有工具不健康）
 *   · `unknown` —— **连判决都做不出**（探针一条都没回来）
 *
 * ⚠️ **取不到 ≠ 正常**：这一条是本页存在的理由（上面那张七条坑的表）。
 *   实现在这里就是：**任何一条取不到都会让 level 掉出 `ok`**。
 *
 * @param {object} p 每条探针的结果（取不到时是 `null`）：
 *   `{health, ready, pending, cache, count, quota, rate, toolHealth, metrics}`
 * @returns `{{level:string, sentence:string, next:string, missing:string[]}}`
 */
function verdictOf(p) {
  const x = p || {};
  const missing = [];
  const bad = [];
  const warn = [];
  // ⚠️ 「一条都没探回来」这条判据按**表**算，⛔ 不写一个手数的阈值
  //    （手数的那种，加一条探针就会**静默失准** —— 本仓最恨的一类）。
  const got = PROBE_KEYS.filter(function (k) { return !!x[k]; });

  // ① 活着吗 —— ⚠️ /health 与 /ready 都在【根路径】上，两条都取不到就说明"连探都探不动"。
  if (!x.health) { missing.push(PROBE_LABEL.health); }
  else if (String(x.health.status) !== 'healthy') { bad.push('服务自报不健康（health 的 status=' + x.health.status + '）'); }
  if (!x.ready) { missing.push(PROBE_LABEL.ready); }
  else if (String(x.ready.status) !== 'ready') { bad.push('/ready 说还没就绪'); }

  // ② 有没有人在等
  if (!x.pending) { missing.push(PROBE_LABEL.pending); }
  else if (_num(x.pending.count) > 0) { warn.push('有 ' + x.pending.count + ' 条在等人批'); }

  // ③ 缓存 / 文档数（⚠️ 0 是合法值 ⇒ 只当"读过"，⛔ 不当异常）
  if (!x.cache) { missing.push(PROBE_LABEL.cache); }
  if (!x.count) { missing.push(PROBE_LABEL.count); }

  // ④ 我这个身份的限（⚠️ 这两条会 fail-open ⇒ 取到了也**不等于**是真值）
  if (!x.quota) { missing.push(PROBE_LABEL.quota); }
  if (!x.rate) { missing.push(PROBE_LABEL.rate); }

  // ⑤ 工具健康（⚠️ 空表 ≠ 全好）
  if (!x.toolHealth) { missing.push(PROBE_LABEL.toolHealth); }
  else {
    const t = x.toolHealth.tools || {};
    const names = Object.keys(t);
    const sick = names.filter(function (n) { return String((t[n] || {}).status) === 'unhealthy'; });
    if (names.length === 0) { warn.push('工具健康表是空的（⚠️ 空 ≠ 全好：服务刚起时它就是空的）'); }
    else if (sick.length) { bad.push('有工具不健康：' + sick.join('、')); }
  }

  // ⑥ Prometheus 文本（⚠️ 不算"异常"，它只是指标）
  if (!x.metrics) { missing.push(PROBE_LABEL.metrics); }

  let level, sentence, next;
  if (got.length === 0) {
    level = 'unknown';
    sentence = '一条都没探回来 —— 连判决都做不出。';
    next = '先确认服务在跑、凭据有效；再看下面每一行标了什么。';
  } else if (bad.length) {
    level = 'bad';
    sentence = '不对劲：' + bad.join('；') + '。';
    next = '按右栏那几行的「打谁」逐条往下查。';
  } else if (warn.length || missing.length) {
    level = 'warn';
    const bits = [];
    if (warn.length) { bits.push(warn.join('；')); }
    if (missing.length) { bits.push('有 ' + missing.length + ' 条取不到（' + missing.join('、') + '）'); }
    sentence = '没看到明确故障，但也不能说正常：' + bits.join('；') + '。';
    next = missing.length
      ? '取不到的那几条先看右栏标了什么 —— ⚠️ 取不到⛔ 不等于正常。'
      : '按需要处置（例如去外部检索那页把待接管批了）。';
  } else {
    level = 'ok';
    sentence = '该看的都看到了，而且都好。';
    next = '⚠️ 但这不是"全系统健康证明"：上面那几条取到的值里，有的可能是兜底值（见右栏的坑）。';
  }
  // ⚠️ `missingKeys` 是**按表**算出来的那一份（页面据此知道该给哪几行打标）；
  //    `missing` 是给人读的名字。两样都给，⛔ 谁也别从谁推。
  return { level: level, sentence: sentence, next: next, missing: missing,
           missingKeys: PROBE_KEYS.filter(function (k) { return !x[k]; }) };
}

/**
 * 🔴 **把 N 条探针的原始结果拼成判决与渲染用的那个对象**（`verdictOf` 收的就是它）。
 *
 * ⚠️ **这一步为什么单拎出来**：它原先写在页面的 DOM 层里，**当场出过一个真 bug** ——
 *    DOM 层拿到 `/metrics` 的正文后，**把 `metrics` 删掉**、只留摘要，
 *    而判决那一句判的恰恰是「**这条回来了没有**」⇒ 页面一边说「9/9 拿到了」、
 *    一边说「有 1 条取不到（metrics）」（2026-10-13 在真浏览器上撞出来的）。
 *    ⇒ 搬进纯逻辑 ⇒ **有 node 用例钉着**；DOM 层只剩"画"。
 *
 * 🔴 **两条不许动**：① `metrics`（原文）**必须留着**；② 摘要另存 `metricsText`。
 */
function composeProbes(vals) {
  const v = vals || {};
  return {
    health: v.health || null, ready: v.ready || null, pending: v.pending || null,
    cache: v.cache || null, count: v.count || null, quota: v.quota || null,
    rate: v.rate || null, toolHealth: v.toolHealth || null, toolVersions: v.toolVersions || null,
    // ⚠️ 这两行**一起**才有意义：原文给"取到没有"，摘要给"页面上画什么"。
    metrics: v.metrics == null ? null : v.metrics,
    metricsText: v.metrics == null ? null : promSummary(v.metrics),
  };
}

/** 本页的 `thread_id`（这一页不发对话请求，但花费块要按会话归集 ⇒ 一样给它一个）。 */
function newThreadId() {
  const r = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID().slice(0, 8) : String(Math.random()).slice(2, 10);
  return 'ops-page-' + r;
}

const RagOps = {
  ROOT_NAMES, rootPath, PATHS,
  CACHE_STATS_PATH, COUNT_PATH, QUOTA_PATH, RATE_LIMIT_PATH,
  TOOL_HEALTH_PATH, TOOL_VERSIONS_PATH, PENDING_PATH,
  PROBES, PROBE_KEYS, PROBE_LABEL, probeOf,
  fmtNum, fmtVal, fmtAgo, healthChecks, quotaRow, rateRow, toolRows, promSummary,
  composeProbes, verdictOf, newThreadId,
};

if (typeof module !== 'undefined' && module.exports) { module.exports = RagOps; }
if (typeof window !== 'undefined') { window.RagOps = RagOps; }

})();   // ⛔ 别去掉 —— 去掉它，顶层绑定会与页面自己的 `el` / `key` 撞车，而且**不报错**
