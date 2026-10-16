'use strict';
/* `agent.js` 的用例 —— ⛔ 零 npm 依赖（只 node:test + node:assert）。
   跑法：node --test app/static/js/*.test.js（已进 ci.yml 的 glob） */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, 'agent.js'), 'utf8');
const A = require('./agent.js');

/* ══════════════ 1 · 🔴 URL 前缀（本文件的【主要守卫】之一） ══════════════ */

test('🔴 本页所有端点常量都必须带 `/api/v1` 前缀', () => {
  // ⚠️ 为什么单列一条：`app/tests/test_web_pages.py` 只扫**页面里的字面量**，
  //    而本页走 helper 拼路径 ⇒ 对它**空过**（那份文件自己写着这条）
  //    ⇒ 前缀保护**只落在本文件**。同族的 `rag.test.js` / `kb.test.js` 都有这一条。
  // ⚠️ 轨迹那两条**没有顶层常量**（前缀不是端点 → 见 `agent.js` 里 `buildTracePath` 上面那段），
  //    它们的前缀由下面那条"拼出来的整串也都带前缀"来守。
  for (const k of ['TOOLS_PATH', 'COST_PATH', 'BUDGET_PATH', 'INTERCEPTS_PATH',
                   'PENDING_PATH', 'PENDING_CTX_PATH', 'APPROVE_PATH', 'HISTORY_PATH',
                   'MEM_SEARCH_PATH', 'MEM_ADD_PATH']) {
    assert.match(A[k], /^\/api\/v1\//, `${k} 丢了前缀：${A[k]}`);
  }
  for (const c of A.CHAINS) {
    assert.match(c.path, /^\/api\/v1\//, `${c.id}.path 丢了前缀`);
    assert.match(c.stream, /^\/api\/v1\//, `${c.id}.stream 丢了前缀`);
  }
});

test('拼出来的整串也都带前缀', () => {
  const c = A.chainOf('langgraph');
  assert.ok(A.buildPath(c, '问一句', 'tid', false).startsWith('/api/v1/'));
  assert.ok(A.buildTraceAxisPath('tid').startsWith('/api/v1/'));
  assert.ok(A.buildTracePath('tid').startsWith('/api/v1/'));
  assert.ok(A.buildApprovePath('tid', true).startsWith('/api/v1/'));
  assert.ok(A.buildContextPath('tid').startsWith('/api/v1/'));
  assert.ok(A.buildPendingPath().startsWith('/api/v1/'));
});

/* ══════════════ 2 · 🔴 参数名随链不同（`plan_execute` 收 `goal`） ══════════════ */

test('🔴🔴 `plan_execute` 那条必须用 `goal` —— 传 `question` 会 422（真调撞出来的）', () => {
  // ⚠️ 实测原话：`POST /agent/plan_execute?question=…`
  //    `{"detail":[{"type":"missing","loc":["query","goal"],"msg":"Field required"}]}`
  //    ⇒ 页面上就是"点了没反应 + 一条 422"。
  // ⚠️ 反证：把这条链的 param 改成 'question' ⇒ 本条当场红。
  const plan = A.chainOf('plan');
  assert.strictEqual(plan.param, 'goal');
  const u = A.buildPath(plan, '算三个月用量', 't1', false);
  assert.match(u, /[?&]goal=/);
  assert.ok(!/[?&]question=/.test(u), 'plan 那条⛔ 不许出现 question 参数');

  // 另外两条反过来：它们收 `question`，⛔ 不收 `goal`。
  for (const id of ['langgraph', 'mcp']) {
    const c = A.chainOf(id);
    assert.strictEqual(c.param, 'question');
    assert.match(A.buildPath(c, 'x', 't1', false), /[?&]question=/);
  }
});

test('两个参数都要编码 · 流式那条只是路径多一段 `/stream`', () => {
  const c = A.chainOf('langgraph');
  const u = A.buildPath(c, 'a b/c', 'tid x', false);
  assert.ok(!u.includes('a b/c'), '文本没编码 —— 带空格会把 query 拆坏');
  assert.ok(!u.includes('tid x'), 'thread_id 没编码');
  const s = A.buildPath(c, 'q', 't', true);
  assert.ok(s.indexOf('/agent/langgraph_chat/stream?') !== -1, s);
});

/* ══════════════ 3 · 🔴 批 / 拒 / 代填（三种只在 query 上的差别） ══════════════ */

test('🔴 批 / 拒 / 代填 —— 三种写法，⛔ 一条都不能错', () => {
  // 后端口径（`api_v1_agent.py:592-599`）：
  //   **批** = `approved=true` · **拒** = `approved=false`
  //   **代填** = `approved=true` **且** `edited_answer=<串>`
  const batch = A.buildApprovePath('t1', true);
  assert.match(batch, /approved=true/);
  assert.ok(!batch.includes('edited_answer'), '普通"批"不该带 edited_answer');

  const reject = A.buildApprovePath('t1', false);
  assert.match(reject, /approved=false/);

  const edited = A.buildApprovePath('t1', true, '按我说的办');
  assert.match(edited, /approved=true/);
  assert.ok(edited.includes('edited_answer=' + encodeURIComponent('按我说的办')), edited);
});

test('⚠️ 「拒」的时候⛔ 不许把 edited_answer 带上 —— 后端会忽略它，带上就是骗人', () => {
  // 后端 `:463` 明确：拒绝时给 `edited_answer` **会被忽略**。
  // ⇒ 页面上那个框在"拒"的时候本就不该有内容；这里再兜一道，防止拼接时混进去。
  const reject = A.buildApprovePath('t1', false, '这段不该出现');
  assert.ok(!reject.includes('edited_answer'), `拒绝却带了代填内容：${reject}`);
});

/* ══════════════ 4 · `has` 表 = 页面哪些块有意义 ══════════════ */

test('🔴 `has` 说清了每条链产不产出那几样（页面据此决定哪些块有意义）', () => {
  assert.deepStrictEqual(A.chainOf('langgraph').has,
    { approval: true, trace: true, plan: false, memory: true });
  // ⚠️ 这条实测来的：`mcp_chat` 那张图**没有审批节点** ⇒ 一次问句连发 5 次联网检索、
  //    `/agent/pending` 计数 0 ⇒ 页面上"等人批"那一块**整块去掉**。
  assert.strictEqual(A.chainOf('mcp').has.approval, false);
  // ⚠️ `plan_execute` **不写轨迹**（它这条端点上没有 `start_trace`）⇒ 页面不摆"执行明细"。
  assert.strictEqual(A.chainOf('plan').has.trace, false);
  assert.strictEqual(A.chainOf('plan').has.plan, true);
});

test('第一条链是**默认选中**的那条（LangGraph）· 认不出的 id 回第一条而不是抛', () => {
  assert.strictEqual(A.CHAINS[0].id, 'langgraph');
  assert.strictEqual(A.chainOf('不存在的链').id, 'langgraph');
});

/* ══════════════ 5 · 🔴 缺值一律 `—`，⛔ 不编 0 ══════════════ */

test('🔴 阶段轨：只有真数据才填，取不到就 `—`（⛔ 不许倒推出一段假耗时）', () => {
  const c = A.chainOf('langgraph');
  // 一次真实调用的形状（2026-10-13 真调核过）。
  const trace = {
    duration_ms: 2399.44,
    tool_calls: [{ tool_name: 'calculator', duration_ms: 6.98, status: 'success' }],
    agent_decisions: [{ type: 'tool_decision' }, { type: 'final' }],
  };
  const cost = [{ purpose: 'answer_generation', total_tokens: 195, cost: 0.00027 }];
  const rows = A.railRows(c, trace, cost);
  assert.strictEqual(rows.length, 5);
  assert.strictEqual(rows[1].value, '2 轮', '模型决策那一格应当是决策条数');
  assert.match(rows[2].value, /^1 次 · /, rows[2].value);
  assert.match(rows[3].value, /195 tokens/);
  assert.match(rows[3].value, /¥0\.0003/, '费用要保 4 位（⛔ 不许印成 0）');
  assert.match(rows[4].value, /2\.40 s/);
});

test('🔴 空轨迹 ⇒ 每一格都是 `—`，⛔ 一个 0 都不许出现', () => {
  // ⚠️ 反证：把 `_n` / `fmtMs` 里的 `—` 改成 `0` ⇒ 本条当场红。
  //    为什么较真：0 会被读成"这一段没花时间 / 没调工具"，而实际是**没数据**。
  const rows = A.railRows(A.chainOf('langgraph'), null, null);
  const joined = rows.map((r) => r.value).join(' | ');
  assert.ok(!/(^|\D)0(\D|$)/.test(joined.replace('通过', '')), `出现了 0：${joined}`);
  assert.match(joined, /—/);
});

test('`fmtMs` / `fmtCost`：非数 ⇒ `—`（不给 `NaN`、不给 `0`）', () => {
  assert.strictEqual(A.fmtMs(null), '—');
  assert.strictEqual(A.fmtMs(undefined), '—');
  assert.strictEqual(A.fmtMs(0), '0 ms');
  assert.strictEqual(A.fmtCost(null), '—');
  assert.strictEqual(A.fmtCost(NaN), '—');
});

/* ══════════════ 6 · 执行明细 / 计划表 / 待接管 / 历史 / 工具表 ══════════════ */

test('执行明细：一步一步，带状态与耗时', () => {
  const rows = A.toolRows({ tool_calls: [
    { tool_name: 'web_search', status: 'success', duration_ms: 566.34, arguments: { query: 'x' }, result: '搜索结果…' },
    { tool_name: 'unknown', status: 'error', duration_ms: 1.2, arguments: {}, result: '' },
  ] });
  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows[0].ok, true);
  assert.strictEqual(rows[1].ok, false);
  assert.strictEqual(rows[0].ms, '566 ms');
  assert.ok(rows[0].args.includes('x'));
});

test('计划表：`plan[]` 一步一行（`plan_execute` 那条链特有）', () => {
  const rows = A.planRows({ plan: [{ step: 1, action: '计算 12*6', tool: 'calculator', input: '12*6' }] });
  assert.deepStrictEqual(rows[0], { n: '1', action: '计算 12*6', tool: 'calculator', input: '12*6' });
  assert.deepStrictEqual(A.planRows({}), []);
});

test('待接管表 / 裁决历史：字段逐条对得上真返回', () => {
  const p = A.pendingRows({ items: [{
    raw_thread_id: 'probe-1', user_name: 'admin', graph: 'agent_graph', rounds: 2,
    tool_calls: [{ name: 'web_search', args: {} }, { name: 'web_search', args: {} }],
  }] });
  assert.strictEqual(p[0].raw, 'probe-1');
  assert.strictEqual(p[0].graph, 'agent_graph');
  assert.strictEqual(p[0].tools, 'web_search、web_search');

  const h = A.historyRows({ events: [{
    owner: 'admin', actor: 'admin', decision: 'approved', edited: false, rounds: 1, reason: 'web_search×2',
  }] });
  assert.strictEqual(h[0].decision, 'approved');
  assert.strictEqual(h[0].edited, '否');
  assert.strictEqual(h[0].reason, 'web_search×2');
});

test('🔴 工具表**从真返回数出来**（⛔ 页面不写死那 7 行）', () => {
  const c = A.toolCatalog({ healthy_tools: [{ name: 'calculator', description: 'd', version: '1.0.0' }],
                            unhealthy_tools: [], total: 7 });
  assert.strictEqual(c.total, 7, 'total 取服务端给的数');
  assert.strictEqual(c.healthy[0].name, 'calculator');
  // ⚠️ 真返回里没有 total 时（不该发生，但别崩）⇒ 退回数出来的条数。
  assert.strictEqual(A.toolCatalog({ healthy_tools: [{ name: 'a' }] }).total, 1);
});

test('🔴 花费：一行都没有 ⇒ 全 `—`（⛔ 不画 0）· 有行 ⇒ 按用途分 LLM / embedding', () => {
  const empty = A.costOf({ items: [], total: { count: 0, total_tokens: 0, total_cost: 0 } });
  assert.strictEqual(empty.calls, '—');
  assert.strictEqual(empty.cost, '—');
  assert.strictEqual(empty.llm, '—');

  const c = A.costOf({
    items: [{ purpose: 'agent_decision', total_tokens: 1408 },
            { purpose: 'answer_generation', total_tokens: 195 },
            { purpose: 'embedding', total_tokens: 9 }],
    total: { count: 3, total_tokens: 1612, total_cost: 0.0031 },
  });
  assert.strictEqual(c.calls, '3');
  assert.strictEqual(c.llm, '1603');
  assert.strictEqual(c.embedding, '9');
  assert.strictEqual(c.cost, '¥0.0031');
});

/* ══════════════ 7 · 🔴 SSE 分帧（半帧必须缓冲，⛔ 别当场抛） ══════════════ */

test('🔴 `parseFrames`：一次 read 只拿到【半帧】时，剩下那段必须回给下一次', () => {
  // ⚠️ 这是流式实现最容易写错的一处：一次 `read()` 的边界**不保证**落在帧边界上。
  const p1 = A.parseFrames('data: {"content": "你');
  assert.deepStrictEqual(p1.events, []);
  assert.strictEqual(p1.rest, 'data: {"content": "你');

  const p2 = A.parseFrames(p1.rest + '好"}\n\ndata: {"content": "呀"}\n\n');
  assert.strictEqual(p2.events.length, 2);
  assert.strictEqual(p2.events[0].json.content, '你好', '跨两次 read 的那一帧要拼回来');
  assert.strictEqual(p2.events[1].json.content, '呀', '第二帧（同一次 read 里完整的）也要认出来');
  assert.strictEqual(p2.rest, '');
});

test('`parseFrames`：`[DONE]` 认得出 · 认不出的帧跳过（⛔ 不打断整条流）', () => {
  const p = A.parseFrames('data: {"content": "a"}\n\ndata: 这不是 JSON\n\ndata: [DONE]\n\n');
  assert.strictEqual(p.events.length, 2, '坏帧要**跳过**（⛔ 不是也塞一个事件进去）：' + JSON.stringify(p.events));
  assert.strictEqual(p.events[0].json.content, 'a');
  assert.strictEqual(p.events[1].done, true);
});

test('`parseFrames`：收尾那帧（带 `status`）认得出 —— 停在审批点就靠它说', () => {
  const summary = { thread_id: 't', answer: '我先查一下。', status: 'pending_approval' };
  const p = A.parseFrames('data: ' + JSON.stringify(summary) + '\n\ndata: [DONE]\n\n');
  assert.strictEqual(p.events[0].json.status, 'pending_approval');
});

/* ══════════════ 8 · 🔴 markdown 记号（数据带进来的那一类，门扫不到） ══════════════ */

test('🔴 模型吐回来的 `**` 必须去掉 —— 页面上会原样印出来', () => {
  // ⚠️ 真调实测：answer 就是 `8 × 8 = **64**`。
  //    而本仓那条 markdown 门**只扫页面 HTML**（还剥掉 `<script>`）⇒ 扫不到数据里带进来的。
  assert.strictEqual(A.stripMd('8 × 8 = **64**'), '8 × 8 = 64');
  assert.strictEqual(A.stripMd(null), '');
  const rows = A.toolRows({ tool_calls: [{ tool_name: 'x', result: '**召回了**', status: 'success' }] });
  assert.strictEqual(rows[0].result, '召回了');
});

/* ══════════════ 9 · 🔴 只许漏一个全局键（IIFE） ══════════════ */

test('🔴🔴 `agent.js` 对外只许留【一个】全局键（`RagAgent`）', () => {
  // ⚠️ 这条是拿一个真事故换来的：经典脚本的顶层 `function`/`const` **全是全局的**，
  //    与页面自己的顶层绑定（每页都有 `const key = …` / `function el(…)`）撞上
  //    `SyntaxError: Identifier 'key' has already been declared`
  //    ⇒ **整段内联脚本一行都不执行**，而当时 982 条 pytest + 283 条 node **全绿**。
  //    📄 复盘
  const sandbox = { module: undefined, window: {}, console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  vm.runInContext(SRC, sandbox);
  const added = Object.keys(sandbox).filter((k) => !before.has(k));
  assert.deepStrictEqual(added, ['RagAgent'],
    `\`agent.js\` 往 window 上多挂了：${added.join(', ')} ⇒ 与页面自己的顶层绑定撞车的面又回来了`);
});

test('`thread_id` 带 `agent-page-` 前缀（方便在账本里一眼认出来）', () => {
  assert.match(A.newThreadId(), /^agent-page-/);
});

/* ══════════════ 10 · 🔴 markdown 那条门【看不见的一条缝】（本页自己补一道） ══════════════ */

test('🔴🔴 页面内联脚本里【会变成页面文字】的字符串，⛔ 不许带 markdown 记号', () => {
  // ⚠️ 为什么自己补一道：`app/tests/test_web_pages.py` 那条 markdown 门**剥掉了 `<script>`**
  //    （那是对的 —— 脚本里的反引号常是**故意的**）⇒ 🔴 **缝就在这**：
  //    内联脚本里那些**会被 `textContent` 原样印到页面上**的字符串，它扫不到。
  //    实测栽过两次（2026-10-12）：`stream.html` 的流式正文印出 `**非人为损坏不收备件费**`、
  //    `kb.html` 的 `insert-status` 印出 `**召回了**`。
  //    ⇒ 本页接线时**又踩了一次**（`rail-note` 那两句）⇒ 就地立一道，⛔ 别等那份通用门。
  //    📄 纪律 ⇒ `frontend/finally-定稿/页面-Agent自验收标准.md` §五之四（它自己标着"⬜ 还没做"）。
  // ⚠️ 反证：往任意一句里加一个 `**` ⇒ 本条当场红。
  const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'agent.html'), 'utf8');
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
    '这些字符串会原样印在页面上（markdown 记号不会被渲染，就是两个星号/反引号）：\n  ' + bad.join('\n  '));
});
