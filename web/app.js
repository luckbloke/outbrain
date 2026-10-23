/* ==========================================================================
   外脑 · Cordis Brain —— 前端逻辑
   零依赖，纯原生 JS。所有网络请求都走同源 API。
   ========================================================================== */
'use strict';

const $ = (id) => document.getElementById(id);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const TYPE_LABEL = { inbox: '收件箱', task: '任务', event: '日程', idea: '灵感', reference: '资料', note: '笔记' };
const STATUS_LABEL = { open: '进行中', in_progress: '进行中', waiting: '等待中', done: '已完成', canceled: '已取消' };
const TOKEN_KEY = 'cb_token';
const THEME_KEY = 'cb_theme';

const getToken = () => localStorage.getItem(TOKEN_KEY) || '';
const setToken = (t) => { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); };

/* ------------------------------- 工具 ------------------------------- */
const esc = (s) => (s == null ? '' : String(s)).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(msg, kind, ms) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast show' + (kind ? ' ' + kind : '');
  clearTimeout(window._toastT);
  window._toastT = setTimeout(() => { t.className = 'toast' + (kind ? ' ' + kind : ''); }, ms || 2400);
}

function pad2(n) { return String(n).padStart(2, '0'); }

/** 解析后端 ISO 时间（可能带 +08:00）。 */
function toDate(s) {
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** 人性化时间：今天 15:00 / 明天 09:00 / 周三 14:30 / 10月8日 15:00 */
function fmtDue(s, withTime) {
  const d = toDate(s);
  if (!d) return '';
  const now = new Date();
  const hm = withTime === false ? '' : ` ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  const t = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const dd = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = Math.round((dd - t) / 86400000);
  if (diff === 0) return '今天' + hm;
  if (diff === 1) return '明天' + hm;
  if (diff === 2) return '后天' + hm;
  if (diff === -1) return '昨天' + hm;
  if (diff > 2 && diff < 7) return '周' + '日一二三四五六'[d.getDay()] + hm;
  return `${d.getMonth() + 1}月${d.getDate()}日` + hm;
}

/** 逾期/临期标记 */
function dueClass(s) {
  const d = toDate(s);
  if (!d) return '';
  const diff = (d - Date.now()) / 3600000;
  if (diff < 0) return 'late';
  if (diff <= 24) return 'soon';
  return '';
}

function greeting() {
  const h = new Date().getHours();
  if (h < 6) return '夜深了';
  if (h < 11) return '早上好';
  if (h < 13) return '中午好';
  if (h < 18) return '下午好';
  return '晚上好';
}

/* ------------------------------- API ------------------------------- */
async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers);
  const tok = getToken();
  if (tok) headers['Authorization'] = 'Bearer ' + tok;
  if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  opts.headers = headers;

  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    setOnline(false);
    throw new Error('无法连接服务，请确认应用仍在运行');
  }

  let data = {};
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch (e) { data = { error: text.slice(0, 200) }; } }

  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    showAuth();
    throw new Error('登录已失效，请重新登录');
  }
  if (!res.ok) throw new Error(data.error || ('请求失败（HTTP ' + res.status + '）'));
  setOnline(true);
  return data;
}

const jsonBody = (obj) => ({ method: 'POST', body: JSON.stringify(obj) });

/* --------------------------- 连接状态 --------------------------- */
function setOnline(ok) {
  const chip = $('chipHealth');
  if (!chip) return;
  chip.className = 'status-dot ' + (ok ? 'ok' : 'bad');
  chip.querySelector('span').textContent = ok ? '已连接' : '未连接';
}

async function health() {
  try {
    const r = await api('/api/health');
    setOnline(true);
    return r;
  } catch (e) {
    setOnline(false);
    return null;
  }
}

/* --------------------------- 弹层 / 对话框 --------------------------- */
let sheetOnClose = null;
/* 当前详情弹层对应的条目 id（不是条目弹层时为 null），
   条目被删除/完成后弹层要跟着关掉，否则上面的「保存」会 404。 */
let sheetItemId = null;

function openSheet(title, body, foot, onClose) {
  $('sheetTitle').textContent = title;
  $('sheetBody').innerHTML = body || '';
  $('sheetFoot').innerHTML = foot || '';
  sheetOnClose = onClose || null;
  $('sheetMask').classList.add('show');
  $('sheet').scrollTop = 0;
}

function closeSheet() {
  $('sheetMask').classList.remove('show');
  sheetItemId = null;
  const cb = sheetOnClose;
  sheetOnClose = null;
  if (cb) cb();
}

/** 自定义确认框（Android WebView 里原生 confirm() 不可靠） */
function askConfirm(message, opts) {
  opts = opts || {};
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; resolve(v); };
    openSheet(opts.title || '请确认', `<div style="font-size:14.5px;line-height:1.7">${esc(message)}</div>`,
      `<button class="btn ghost" id="_cCancel">取消</button>
       <button class="btn ${opts.danger ? 'danger' : 'primary'}" id="_cOk">${esc(opts.okText || '确定')}</button>`,
      () => done(false));
    $('_cCancel').onclick = () => { done(false); closeSheet(); };
    $('_cOk').onclick = () => { done(true); closeSheet(); };
  });
}

/** 自定义输入框 */
function askPrompt(title, placeholder, value) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; resolve(v); };
    openSheet(title, `<textarea id="_pInput" placeholder="${esc(placeholder || '')}">${esc(value || '')}</textarea>
      <div class="faint" style="margin-top:6px">多个条目可用逗号、分号或换行分隔</div>`,
      `<button class="btn ghost" id="_pCancel">取消</button><button class="btn primary" id="_pOk">确定</button>`,
      () => done(null));
    $('_pInput').focus();
    $('_pCancel').onclick = () => { done(null); closeSheet(); };
    $('_pOk').onclick = () => { const v = $('_pInput').value.trim(); done(v || null); closeSheet(); };
  });
}

/* ------------------------------- 主题 ------------------------------- */
function applyTheme(name) {
  document.documentElement.setAttribute('data-theme', name);
  localStorage.setItem(THEME_KEY, name);
  const btn = $('btnTheme');
  if (btn) btn.textContent = name === 'dark' ? '🌙' : '☀️';
  const meta = document.querySelector('meta[name=theme-color]');
  if (meta) meta.setAttribute('content', name === 'dark' ? '#0b0f14' : '#f4f6fb');
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
  applyTheme(saved || (prefersLight ? 'light' : 'dark'));
  $('btnTheme').onclick = () => {
    applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  };
}

/* ------------------------------- 认证 ------------------------------- */
let authMode = 'login';

function showAuth() {
  $('authScreen').classList.add('show');
  $('btnLogout').style.display = 'none';
  $('btnSettings').style.display = 'none';
  $('chipBell').style.display = 'none';
}

function showApp() {
  $('authScreen').classList.remove('show');
  $('btnLogout').style.display = '';
  $('chipBell').style.display = '';
}

function setAuthMode(mode) {
  authMode = mode;
  $$('#authTabs button').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
  $('authSubmit').textContent = mode === 'login' ? '登录' : '注册并登录';
  $('authPass').setAttribute('autocomplete', mode === 'login' ? 'current-password' : 'new-password');
  $('authTip').textContent = mode === 'login' ? '还没有账号？点上方「注册」' : '首个注册的用户为管理员';
  authMsg('');
}

function authMsg(msg) {
  const m = $('authMsg');
  if (!msg) { m.style.display = 'none'; m.textContent = ''; return; }
  m.textContent = msg;
  m.style.display = 'block';
}

async function submitAuth(ev) {
  if (ev) ev.preventDefault();
  const u = $('authUser').value.trim();
  const p = $('authPass').value;
  if (!u || !p) { authMsg('请输入用户名和密码'); return; }
  if (authMode === 'register' && (u.length < 2 || p.length < 6)) {
    authMsg('用户名至少 2 个字符，密码至少 6 位');
    return;
  }
  const btn = $('authSubmit');
  btn.disabled = true;
  const old = btn.textContent;
  btn.innerHTML = '<span class="spinner"></span> 请稍候…';
  try {
    const r = await api('/api/auth/' + (authMode === 'login' ? 'login' : 'register'), jsonBody({ username: u, password: p }));
    onAuthed(r);
  } catch (e) {
    authMsg(e.message);
  }
  btn.disabled = false;
  btn.textContent = old;
}

function onAuthed(r) {
  setToken(r.token);
  localStorage.setItem('cb_user', r.username || '');
  showApp();
  showSettingsBtn(r.user_id);
  startApp();
  toast('欢迎回来，' + (r.username || ''), 'ok');
}

async function doLogout() {
  const ok = await askConfirm('确定要退出登录吗？本机数据仍会保留。', { okText: '退出' });
  if (!ok) return;
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (e) { /* 忽略 */ }
  setToken('');
  location.reload();
}

function showSettingsBtn(uid) {
  $('btnSettings').style.display = uid === 1 ? '' : 'none';
}

async function initAuth() {
  if (!getToken()) { showAuth(); return; }
  try {
    const me = await api('/api/auth/me');
    localStorage.setItem('cb_user', me.username || '');
    showApp();
    showSettingsBtn(me.user_id);
    startApp();
  } catch (e) {
    showAuth();
  }
}

/* ------------------------------- 导航 ------------------------------- */
let currentTab = 'today';
const TAB_RENDERERS = {
  today: () => renderToday(),
  capture: () => renderCapture(),
  review: () => renderReview(),
  graph: () => renderGraph(),
  suggest: () => renderSuggestions(),
  data: () => renderData(),
};

function switchTab(tab) {
  if (tab === 'more') { openMoreSheet(); return; }
  currentTab = tab;
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + tab));
  $$('.navbar .tab, .tabbar .tb').forEach((t) => {
    const active = t.dataset.tab === tab || (tab !== 'more' && t.dataset.tab === 'more');
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  $('notifPanel').classList.remove('show');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  const fn = TAB_RENDERERS[tab];
  if (fn) fn();
}

function openMoreSheet() {
  const rows = [
    ['search', '🔍', '搜索', '关键词 + 语义混合检索'],
    ['graph', '🕸️', '图谱', '人物 / 项目 / 事件关系'],
    ['suggest', '💡', '建议', '主动提醒该处理什么'],
    ['data', '🗄️', '数据', '备份 / 导出 / 导入'],
  ];
  const body = rows.map(([tab, ic, name, desc]) => `
    <button class="btn block" style="justify-content:flex-start;height:auto;padding:12px 14px;margin-bottom:8px"
      onclick="closeSheet();switchTab('${tab}')">
      <span style="font-size:18px">${ic}</span>
      <span style="text-align:left;flex:1">
        <span style="display:block;font-weight:650">${name}</span>
        <span class="faint" style="display:block;font-weight:400">${desc}</span>
      </span>
      <span class="faint">›</span>
    </button>`).join('');
  const admin = $('btnSettings').style.display !== 'none';
  openSheet('更多功能', body + `
    <hr class="sep">
    <button class="btn ghost block" style="justify-content:flex-start;margin-bottom:8px" onclick="closeSheet();toggleThemeFromSheet()">
      <span style="font-size:16px">🌗</span>&nbsp;切换深浅色
    </button>
    ${admin ? `<button class="btn ghost block" style="justify-content:flex-start;margin-bottom:8px" onclick="closeSheet();openSettings()">
      <span style="font-size:16px">⚙️</span>&nbsp;设置</button>` : ''}
    <button class="btn ghost block" style="justify-content:flex-start;color:var(--bad)" onclick="closeSheet();doLogout()">
      <span style="font-size:16px">⏏</span>&nbsp;退出登录
    </button>`);
}

function toggleThemeFromSheet() {
  applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
}

/* ------------------------------- 条目卡片 ------------------------------- */
function itemCard(it, opt) {
  opt = opt || {};
  const done = it.status === 'done' || it.status === 'canceled';
  const tags = (it.tags || []).slice(0, 5).map((t) => `<span>${esc(t)}</span>`).join('');
  const due = it.due_at
    ? `<span class="due ${dueClass(it.due_at)}">⏰ ${esc(dueLabel(it.due_at))}</span>`
    : '';
  const proj = it.project_name ? `<span>· ${esc(it.project_name)}</span>` : '';
  const score = opt.score != null ? `<span class="score" style="color:var(--accent)">匹配 ${Math.round(opt.score * 100) / 100}</span>` : '';
  const body = it.snippet || (it.summary && it.summary !== it.title ? it.summary : '');
  return `
  <div class="item ${done ? 'done' : ''}" data-id="${it.id}">
    ${opt.checkbox === false ? '' : `<button class="checkbox ${done ? 'done' : ''}" aria-label="完成" onclick="completeItem(${it.id}, event)">✓</button>`}
    <div class="body" onclick="openItem(${it.id})">
      <div class="title">${esc(it.title) || esc((it.body || '').slice(0, 60)) || '（无标题）'}</div>
      <div class="meta">
        <span class="badge ${esc(it.type)}">${TYPE_LABEL[it.type] || esc(it.type)}</span>
        ${it.priority ? `<span class="pr ${esc(it.priority)}">${esc(it.priority)}</span>` : ''}
        ${due}${proj}
        ${it.status === 'waiting' ? '<span class="badge">等待中</span>' : ''}
        ${it.parent_id ? '<span class="badge">子任务</span>' : ''}
        ${score}
      </div>
      ${body ? `<div class="snippet">${esc(body)}</div>` : ''}
      ${tags ? `<div class="tags">${tags}</div>` : ''}
    </div>
    <div class="actions">
      <button class="iconbtn" style="width:34px;height:34px;min-width:34px" title="更多" onclick="openItem(${it.id}, event)">⋯</button>
    </div>
  </div>`;
}

function emptyBox(icon, text, action) {
  return `<div class="empty"><span class="big">${icon}</span>${esc(text)}${action ? `<div class="act">${action}</div>` : ''}</div>`;
}

function section(title, list, cls, opt) {
  if (!list || !list.length) return '';
  return `<section class="sec">
    <div class="section-title ${cls || ''}">${esc(title)}<span class="count">${list.length}</span></div>
    <div class="card" style="padding:4px 15px">${list.map((i) => itemCard(i, opt)).join('')}</div>
  </section>`;
}

/* ------------------------------- 今日 ------------------------------- */
let lastToday = null;

async function renderToday() {
  try {
    const v = await api('/api/today');
    lastToday = v;
    renderTodayHero(v);
    renderTodayList(v);
  } catch (e) {
    $('todayBody').innerHTML = `<div class="card"><div class="error-box">${esc(e.message)}</div>
      <div class="row" style="margin-top:10px"><button class="btn" onclick="renderToday()">重试</button></div></div>`;
    $('headline').textContent = '暂时无法加载';
    $('todayStats').innerHTML = '';
  }
}

function renderTodayHero(v) {
  const c = v.counts || {};
  const overdue = (v.overdue || []).length;
  const today = (v.due_today || []).length;
  const upcoming = (v.upcoming || []).length;
  const waiting = (v.waiting || []).length;
  const within7 = c.within7 != null ? c.within7 : today + upcoming; // 当天也算 7 天内
  const open = overdue + today + upcoming + waiting;

  const user = localStorage.getItem('cb_user') || '';
  $('helloLine').textContent = `${greeting()}${user ? '，' + user : ''} · ${v.date || ''}`;

  let headline;
  if (!open) headline = '今天没有待办，享受空白';
  else if (overdue) headline = `${overdue} 项已逾期，先处理它们`;
  else if (today) headline = `今天有 ${today} 项要做`;
  else headline = `未来 7 天有 ${within7} 项安排`;

  $('headline').textContent = headline;
  const stat = (n, label, key, cls) =>
    `<div class="stat ${cls || ''}" role="button" tabindex="0" style="cursor:pointer"
        onclick="openStatSheet('${key}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openStatSheet('${key}')}">
       <b>${n}</b><span>${label}</span></div>`;
  $('todayStats').innerHTML = `
    ${stat(overdue, '逾期', 'overdue', overdue ? 'alert' : '')}
    ${stat(today, '今天', 'due_today')}
    ${stat(within7, '7 天内', 'within7')}
    ${stat((v.added_today || []).length, '今日新增', 'added_today', 'ok')}`;
}

/* 通用「点数字 → 看清单」抽屉：字段缺失（没拉到数据）时才重取 */
async function openListSheet(title, list) {
  list = list || [];
  const body = list.length
    ? `<div class="card" style="padding:4px 15px">${list.map((i) => itemCard(i)).join('')}</div>`
    : emptyBox('✅', '这一类现在没有条目');
  openSheet(`${title} · ${list.length}`, body, '');
}

/* 点首页的大数字，直接看这一类条目（数据来自 /api/today，现取现显） */
const STAT_SHEETS = {
  overdue: { title: '已逾期', build: (v) => v.overdue },
  due_today: { title: '今天到期', build: (v) => v.due_today },
  within7: { title: '未来 7 天（含今天）', build: (v) => [].concat(v.due_today || [], v.upcoming || []) },
  added_today: { title: '今日新增', build: (v) => v.added_today },
  waiting: { title: '等待中', build: (v) => v.waiting },
};

async function openStatSheet(key) {
  const m = STAT_SHEETS[key];
  if (!m) return;
  try {
    const v = await api('/api/today');
    lastToday = v;
    openListSheet(m.title, m.build(v));
  } catch (e) {
    toast(e.message, 'bad', 3200);
  }
}

function renderTodayList(v) {
  const parts = [];
  parts.push(section('已逾期', v.overdue, 'overdue'));
  parts.push(section('今天到期', v.due_today, 'today'));
  parts.push(section('未来 7 天', v.upcoming));
  parts.push(section('等待中', v.waiting));
  parts.push(section('今日新增', v.added_today));
  const html = parts.filter(Boolean).join('');
  $('todayBody').innerHTML = html || `<div class="card">${emptyBox('🌤️', '还没有任何记录，先记一笔吧', '<button class="btn primary" onclick="switchTab(\'capture\')">去捕获</button>')}</div>`;
}

/* 解析来源文案 + 后台 AI 解析轮询
   捕获采用「规则先出结果 → LLM 后台重解析」：提交瞬间返回，AI 完成后再刷新卡片。
   AI 太慢 / 失败 / 网关不可用都不能让回执一直转圈——以此停止：条目有了 AI 解析、
   meta.refine.state=failed（后端会写）、或轮询到 refining=false / 上限 2 分钟。 */
const PROV_LABEL = { openai: 'AI 解析', ollama: 'AI 解析（本地）', heuristic: '规则解析' };
function provLabel(p) { return PROV_LABEL[p] || p || '规则解析'; }

function refineFailed(it) {
  const m = it && it.meta;
  return !!(m && m.refine && m.refine.state === 'failed');
}

function watchRefine(id, onDone, onFail, onGiveUp) {
  let n = 0;
  const tick = async () => {
    n += 1;
    try {
      const r = await api('/api/items/' + id);
      const it = (r && r.item) || r;
      const ai = it && it.parse_provider && it.parse_provider !== 'heuristic';
      if (ai) { onDone(it); return; }
      if (refineFailed(it)) { onFail ? onFail(it) : onDone(it); return; }
      if (it && it.refining === false && n > 2) {
        // 上一轮解析已收尾但没有 AI 结果（如条目在解析中被完成）
        if (onFail) onFail(it); return;
      }
    } catch (e) {
      if (/not found|404/i.test(e.message || '')) return;  // 条目被删，停止轮询
    }
    if (n < 40) setTimeout(tick, 3000);
    else if (onGiveUp) onGiveUp();
  };
  setTimeout(tick, 2500);
}

async function quickCapture() {
  const input = $('quickInput');
  const text = input.value.trim();
  if (!text) { input.focus(); return; }
  const btn = $('quickBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 整理中';
  try {
    const r = await api('/api/capture', jsonBody({ text, source: 'text' }));
    input.value = '';
    const it = r.item || {};
    const tell = (x) => {
      const byAi = x.parse_provider && x.parse_provider !== 'heuristic';
      toast(`已记下 · ${TYPE_LABEL[x.type] || x.type}${x.due_at ? ' · ' + fmtDue(x.due_at) : ''}${byAi ? ' · AI' : ''}`, 'ok');
    };
    tell(it);
    renderToday();
    if (r.refining) watchRefine(it.id, (x) => { tell(x); renderToday(); }, () => {});
  } catch (e) {
    toast(e.message, 'bad', 3200);
  }
  btn.disabled = false;
  btn.textContent = '记下';
}

/* ------------------------------- 捕获 ------------------------------- */
async function renderCapture() {
  const box = $('recentList');
  box.innerHTML = '<div class="skeleton" style="width:60%"></div><div class="skeleton"></div><div class="skeleton" style="width:80%"></div>';
  try {
    const r = await api('/api/items?limit=100');
    const open = (r.items || []).filter((i) => i.status !== 'done' && i.status !== 'canceled').slice(0, 30);
    box.innerHTML = open.length
      ? open.map((i) => itemCard(i)).join('')
      : emptyBox('📝', '没有进行中的记录');
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

function showCapResult(it, refining, refFail) {
  $('capResult').dataset.itemId = String(it.id);
  const refMeta = (it.meta && it.meta.refine) || {};
  const line = refining
    ? '<span class="spinner"></span> AI 解析中…（结果会自动刷新）'
    : (refFail || refineFailed(it)
      ? `规则解析 · AI 暂未完成${refMeta.error ? `<span class="faint">（${esc(String(refMeta.error).slice(0, 80))}）</span>` : ''}
         <button class="btn ghost sm" style="margin-left:8px" onclick="reparseCap(${it.id})">重试 AI</button>`
      : esc(provLabel(it.parse_provider)));
  $('capResult').innerHTML = `
    <div class="card" style="margin:14px 0 0;background:var(--panel2)">
      <div class="row" style="gap:8px">
        <span class="badge ${esc(it.type)}">${TYPE_LABEL[it.type] || esc(it.type)}</span>
        ${it.priority ? `<span class="pr ${esc(it.priority)}">${esc(it.priority)}</span>` : ''}
        ${it.due_at ? `<span class="due ${dueClass(it.due_at)}">⏰ ${esc(fmtDue(it.due_at))}</span>` : ''}
        <div class="spacer"></div>
        <button class="btn ghost sm" onclick="openItem(${it.id})">编辑</button>
      </div>
      <div style="font-weight:650;margin-top:8px">${esc(it.title)}</div>
      <div class="faint" style="margin-top:6px">
        置信度 ${(it.confidence || 0).toFixed(2)} · ${line}
        ${(it.tags || []).length ? ' · ' + (it.tags || []).map(esc).join(' / ') : ''}
      </div>
      ${(it.meta && it.meta.auto_project && it.project_name) ? `
        <div class="faint" style="margin-top:4px">📁 自动关联「${esc(it.project_name)}」 · ${esc(it.meta.auto_project.reason || '')}</div>` : ''}
    </div>`;
}

async function reparseCap(id) {
  try {
    const r = await api('/api/items/' + id + '/reparse', { method: 'POST', body: jsonBody({}) });
    const it = (r && r.item) || r;
    showCapResult(it, true);
    renderCapture();
    renderToday();
    watchRefine(id, (x) => {
      showCapResult(x, false);
      toast('AI 已解析意图', 'ok');
      renderCapture();
      renderToday();
    }, (x) => {
      showCapResult(x, false, true);
      renderCapture();
      renderToday();
    }, () => showCapResult(it, false, true));
  } catch (e) {
    toast(e.message, 'bad', 3200);
  }
}

async function doCapture() {
  const text = $('capText').value.trim();
  if (!text) { toast('先输入点内容'); $('capText').focus(); return; }
  const btn = $('capBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 整理中';
  try {
    const r = await api('/api/capture', jsonBody({ text, source: $('capSource').value }));
    const it = r.item || {};
    showCapResult(it, !!r.refining);
    $('capText').value = '';
    toast('已捕获并自动整理', 'ok');
    renderCapture();
    renderToday();
    if (r.refining) {
      const noteFail = (x) => { showCapResult(x, false, true); renderCapture(); renderToday(); };
      watchRefine(it.id, (x) => {
        showCapResult(x, false);
        toast('AI 已解析意图', 'ok');
        renderCapture();
        renderToday();
      }, noteFail, () => noteFail(it));
    }
  } catch (e) {
    toast(e.message, 'bad', 3200);
  }
  btn.disabled = false;
  btn.textContent = '捕获';
}
function dueLabel(s) {
  const d = toDate(s);
  if (!d) return '';
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days >= 1) return '逾期 ' + days + ' 天';
  return fmtDue(s);
}

/* ------------------------------- 条目详情 ------------------------------- */
async function openItem(id, ev) {
  if (ev && ev.stopPropagation) ev.stopPropagation();
  try {
    const it = await api('/api/items/' + id);
    renderItemSheet(it);
  } catch (e) {
    if ((e.message || '').toLowerCase().indexOf('not found') >= 0) {
      clearCapResult(id);
      refreshAll();
      toast('这条记录已经被删除', 'bad', 3200);
    } else {
      toast(e.message, 'bad');
    }
  }
}

function toLocalInput(iso) {
  const d = toDate(iso);
  if (!d) return '';
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function renderItemSheet(it) {
  sheetItemId = String(it.id);
  const done = it.status === 'done';
  const body = `
    <div class="row" style="gap:6px;margin-bottom:12px">
      <span class="badge ${esc(it.type)}">${TYPE_LABEL[it.type] || esc(it.type)}</span>
      <span class="pr ${esc(it.priority)}">${esc(it.priority)}</span>
      <span class="badge">${STATUS_LABEL[it.status] || esc(it.status)}</span>
      ${it.parent_id ? '<span class="badge">子任务</span>' : ''}
      <div class="spacer"></div>
      <span class="faint">#${it.id}</span>
    </div>

    <div class="field">
      <label>标题</label>
      <input id="edTitle" type="text" value="${esc(it.title || '')}">
    </div>

    <div class="row" style="gap:8px">
      <div class="field grow">
        <label>类型</label>
        <select id="edType">
          ${Object.keys(TYPE_LABEL).map((k) => `<option value="${k}" ${it.type === k ? 'selected' : ''}>${TYPE_LABEL[k]}</option>`).join('')}
        </select>
      </div>
      <div class="field" style="flex:0 0 92px">
        <label>优先级</label>
        <select id="edPriority">
          ${['P0', 'P1', 'P2', 'P3'].map((p) => `<option value="${p}" ${it.priority === p ? 'selected' : ''}>${p}</option>`).join('')}
        </select>
      </div>
      <div class="field grow">
        <label>状态</label>
        <select id="edStatus">
          ${['open', 'in_progress', 'waiting', 'done', 'canceled'].map((s) => `<option value="${s}" ${it.status === s ? 'selected' : ''}>${STATUS_LABEL[s]}</option>`).join('')}
        </select>
      </div>
    </div>

    <div class="field">
      <label>截止时间</label>
      <input id="edDue" type="datetime-local" value="${toLocalInput(it.due_at)}">
      <div class="row" style="margin-top:6px;gap:6px">
        <button class="btn ghost sm" onclick="setDuePreset(1)">今天 18:00</button>
        <button class="btn ghost sm" onclick="setDuePreset(2)">明天 09:00</button>
        <button class="btn ghost sm" onclick="setDuePreset(3)">下周一 09:00</button>
        <button class="btn ghost sm" onclick="$('edDue').value=''">清除</button>
      </div>
    </div>

    <div class="field">
      <label>标签（逗号或空格分隔）</label>
      <input id="edTags" type="text" value="${esc((it.tags || []).join(', '))}">
    </div>

    <div class="field">
      <label>备注</label>
      <textarea id="edBody" style="min-height:74px">${esc(it.body || '')}</textarea>
    </div>

    ${it.raw && it.raw !== it.body ? `<div class="faint" style="margin-top:4px">原文：${esc(String(it.raw).slice(0, 300))}</div>` : ''}
    <div class="faint" style="margin-top:8px">
      创建 ${esc(fmtDue(it.created_at))}${it.updated_at ? ' · 更新 ' + esc(fmtDue(it.updated_at)) : ''}
      ${it.parse_provider ? ' · 解析 ' + esc(provLabel(it.parse_provider)) : ''}
      ${it.confidence ? ' · 置信 ' + Number(it.confidence).toFixed(2) : ''}
    </div>
    ${(it.meta && it.meta.auto_project) ? `
      <div class="faint" style="margin-top:4px">📁 自动关联：${esc(it.meta.auto_project.reason || '')}
        （${esc(it.meta.auto_project.source || '')}，置信 ${Number(it.meta.auto_project.confidence || 0).toFixed(2)}）</div>` : ''}`;

  const foot = `
    <button class="btn danger sm" onclick="deleteItem(${it.id})">删除</button>
    <button class="btn ghost sm" onclick="splitItem(${it.id})">拆分</button>
    <div class="spacer"></div>
    ${done ? '' : `<button class="btn ghost sm" onclick="completeItem(${it.id})">完成</button>`}
    <button class="btn primary sm" onclick="saveItem(${it.id})">保存</button>`;

  openSheet(it.title || '条目详情', body, foot);
}

function setDuePreset(kind) {
  const d = new Date();
  if (kind === 1) { d.setHours(18, 0, 0, 0); }
  else if (kind === 2) { d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); }
  else {
    const add = ((8 - d.getDay()) % 7) || 7;
    d.setDate(d.getDate() + add); d.setHours(9, 0, 0, 0);
  }
  $('edDue').value = toLocalInput(d.toISOString());
}

async function saveItem(id) {
  const tags = $('edTags').value.split(/[,，;；\s]+/).map((s) => s.trim()).filter(Boolean);
  const dueVal = $('edDue').value;
  const payload = {
    title: $('edTitle').value.trim(),
    type: $('edType').value,
    priority: $('edPriority').value,
    status: $('edStatus').value,
    tags,
    body: $('edBody').value,
    due_at: dueVal ? new Date(dueVal).toISOString() : '',
  };
  if (!payload.title) { toast('标题不能为空'); return; }
  try {
    await api('/api/items/' + id, { method: 'PATCH', body: JSON.stringify(payload) });
    toast('已保存', 'ok');
    closeSheet();
    clearCapResult(id);
    refreshAll();
  } catch (e) {
    toast(e.message, 'bad', 3200);
  }
}

async function completeItem(id, ev) {
  if (ev && ev.stopPropagation) ev.stopPropagation();
  try {
    await api('/api/items/' + id + '/complete', { method: 'POST' });
    toast('已完成 ✓', 'ok');
    clearCapResult(id);
    if (sheetItemId === String(id)) closeSheet();
    refreshAll();
  } catch (e) {
    toast(e.message, 'bad');
  }
}

async function deleteItem(id) {
  const ok = await askConfirm('删除后不可恢复，确定删除这条记录吗？', { danger: true, okText: '删除' });
  if (!ok) return;
  try {
    await api('/api/items/' + id, { method: 'DELETE' });
    toast('已删除', 'ok');
    clearCapResult(id);
    if (sheetItemId === String(id)) closeSheet();
    refreshAll();
  } catch (e) {
    toast(e.message, 'bad');
  }
}

/* 「快速捕获」下面的结果卡只是一次性回执：条目被删除/改状态后要同步清掉，
   否则还会显示旧记录，点「编辑」就会 404。 */
function clearCapResult(id) {
  const box = $('capResult');
  if (!box) return;
  if (id == null || box.dataset.itemId === String(id)) {
    box.innerHTML = '';
    delete box.dataset.itemId;
  }
}

async function splitItem(id) {
  const s = await askPrompt('拆分成子任务', '例如：写提纲，找资料，定稿');
  if (!s) return;
  const tasks = s.split(/[、；;，,\n]+/).map((x) => x.trim()).filter(Boolean);
  if (!tasks.length) return;
  try {
    const r = await api('/api/items/' + id + '/split', jsonBody({ tasks }));
    toast(`已拆成 ${(r.children || []).length} 个子任务`, 'ok');
    refreshAll();
  } catch (e) {
    toast(e.message, 'bad', 3200);
  }
}

/* ------------------------------- 搜索 ------------------------------- */
async function doSearch() {
  const q = $('searchQ').value.trim();
  const type = $('searchType').value;
  const box = $('searchResults');
  if (!q) { box.innerHTML = emptyBox('🔍', '输入关键词开始搜索'); return; }
  box.innerHTML = '<div class="skeleton" style="width:50%"></div><div class="skeleton"></div><div class="skeleton" style="width:75%"></div>';
  try {
    const r = await api('/api/search?q=' + encodeURIComponent(q) + (type ? '&type=' + type : '') + '&limit=30');
    const items = r.items || [];
    box.innerHTML = items.length
      ? `<div class="faint" style="margin-bottom:6px">${items.length} 条结果</div>` + items.map((i) => itemCard(i, { score: i.score })).join('')
      : emptyBox('🫥', `没有找到「${q}」`);
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

/* ------------------------------- 问答 ------------------------------- */
async function doAsk() {
  const q = $('askQ').value.trim();
  if (!q) { $('askQ').focus(); return; }
  const box = $('askResults');
  const btn = $('askBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 思考中';
  box.innerHTML = '<div class="skeleton" style="width:40%"></div><div class="skeleton"></div><div class="skeleton" style="width:85%"></div>';
  try {
    const r = await api('/api/ask?q=' + encodeURIComponent(q));
    const srcs = (r.sources || []).map((s) => {
      const pre = r.mode === 'llm' ? '' : (esc(s.title || '') + ' — ');
      return `<div class="src">${pre}${esc(s.snippet || '')}</div>`;
    }).join('');
    box.innerHTML = `<div class="answer">${esc(r.answer)}</div>
      <div class="faint">来源 ${(r.sources || []).length} 条 · 模式 ${esc(r.mode || 'keyword')}</div>
      ${srcs}`;
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
  btn.disabled = false;
  btn.textContent = '提问';
}

/* ------------------------------- 复盘 ------------------------------- */
let reviewKind = 'daily';
let lastReview = null;

async function renderReview() {
  if (!$('reviewDate').value) {
    const d = new Date();
    $('reviewDate').value = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  }
  await doReview('daily');
}

function renderReviewData(r, kind) {
  lastReview = r;
  const c = r.counts || {};
  const when = kind === 'daily' ? '这一天' : '这一周';
  const stat = (k, v, cls, key, hint) =>
    `<div class="${cls || ''}" role="button" tabindex="0" title="${esc(hint || '点开看清单')}" style="cursor:pointer"
        onclick="openReviewSheet('${key}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openReviewSheet('${key}')}">
       <b>${v != null ? v : '—'}</b><span>${k}</span></div>`;
  $('reviewOut').innerHTML = `
    <div class="kstat">
      ${stat('新增', c.added, '', 'added', '点开看' + when + '记下的条目')}
      ${stat('完成', c.completed, '', 'completed', '点开看完成的条目')}
      ${stat('逾期', c.overdue, c.overdue ? 'late' : '', 'overdue', '点开看仍逾期的条目')}
      ${kind === 'daily' ? stat('今日到期', c.due_today, '', 'due_today', '点开看今天到期的条目') : ''}
      ${stat('未结', c.open, '', 'open', '点开看还没做完的条目')}
    </div>
    ${r.polish_error ? `<div class="faint" style="font-size:12px;margin-bottom:8px">AI 润色未完成：${esc(r.polish_error)}</div>` : ''}
    <div class="summary">${esc(r.summary || '（暂无内容）')}${r.polished ? `<span style="margin-left:10px;font-size:11px;color:var(--accent)">✨ AI 润色</span>` : ''}</div>`;
}

/* 复盘页的数字也能点开：直接用刚拿到的数据切清单 */
const REV_SHEETS = {
  added: { title: '新增', build: (r) => r.added },
  completed: { title: '完成', build: (r) => r.completed },
  overdue: { title: '逾期', build: (r) => r.overdue },
  due_today: { title: '今日到期', build: (r) => r.due_today },
  open: { title: '未结', build: (r) => r.open },
};

function openReviewSheet(key) {
  const m = REV_SHEETS[key];
  if (!m || !lastReview) return;
  openListSheet(m.title, m.build(lastReview) || []);
}

async function doReview(kind) {
  reviewKind = kind;
  const date = $('reviewDate').value;
  $('reviewOut').innerHTML = '<div class="skeleton" style="width:55%"></div><div class="skeleton"></div>';
  try {
    const r = await api('/api/reviews/' + kind + (date ? '?date=' + encodeURIComponent(date) : ''));
    renderReviewData(r, kind);
  } catch (e) {
    $('reviewOut').innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

async function doReviewAI() {
  const btn = $('reviewPolishBtn');
  const old = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> AI 润色中…（约 30 秒）';
  try {
    const date = $('reviewDate').value;
    const r = await api('/api/reviews/' + reviewKind + (date ? '?date=' + encodeURIComponent(date) : ''), { method: 'POST' });
    renderReviewData(r, reviewKind);
    if (r.polished) toast('已用 AI 润色', 'ok');
    else toast('AI 暂未完成：' + (r.polish_error || '网关没返回'), 'bad', 5000);
  } catch (e) {
    toast(e.message, 'bad', 3200);
    renderReview();
  }
  btn.disabled = false;
  btn.textContent = old;
}

/* ------------------------------- 图谱 ------------------------------- */
const ENTITY_COLOR = { person: '#6ea8fe', project: '#56d364', event: '#e3b341', concept: '#bc8cff' };

async function renderGraph() {
  const box = $('graphOut');
  box.innerHTML = '<div class="skeleton" style="width:50%"></div><div class="skeleton"></div>';
  try {
    const g = await api('/api/graph');
    const es = g.entities || [];
    const rs = g.relations || [];
    if (!es.length) {
      box.innerHTML = emptyBox('🕸️', '暂无实体，记录中提到的人物/项目会自动抽取');
      return;
    }
    const W = 420, cx = W / 2, cy = W / 2;
    const R = Math.min(165, 90 + es.length * 3);
    const pos = {};
    es.forEach((e, i) => {
      const a = (2 * Math.PI * i) / es.length - Math.PI / 2;
      pos[e.id] = [cx + R * Math.cos(a), cy + R * Math.sin(a)];
    });
    const edges = rs.map((rl) => {
      const a = pos[rl.from_id], b = pos[rl.to_id];
      if (!a || !b) return '';
      return `<line x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}" stroke="var(--border)" stroke-width="1.5"/>
        <text x="${((a[0] + b[0]) / 2).toFixed(1)}" y="${((a[1] + b[1]) / 2).toFixed(1)}" fill="var(--faint)" font-size="9" text-anchor="middle">${esc(rl.rel)}</text>`;
    }).join('');
    const nodes = es.map((e) => {
      const p = pos[e.id];
      return `<g><circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="6.5" fill="${ENTITY_COLOR[e.kind] || 'var(--faint)'}"/>
        <text x="${p[0].toFixed(1)}" y="${(p[1] - 10).toFixed(1)}" fill="var(--text)" font-size="10.5" text-anchor="middle">${esc(e.name)}</text></g>`;
    }).join('');
    const legend = Object.entries({ person: '人物', project: '项目', event: '事件', concept: '概念' })
      .map(([k, v]) => `<span><span style="color:${ENTITY_COLOR[k]}">●</span> ${v}</span>`).join('');
    box.innerHTML = `<svg id="graphSvg" viewBox="0 0 ${W} ${W}">${edges}${nodes}</svg>
      <div class="legend">${legend}</div>
      <div class="faint" style="text-align:center;margin-top:6px">实体 ${es.length} · 关系 ${rs.length}</div>`;
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

/* ------------------------------- 建议 ------------------------------- */
const SUG_ACTION_LABEL = {
  reschedule_next_day: '改到明天',
  reschedule_today: '排到今天',
  complete: '全部完成',
  delete: '删除',
  merge: '合并',
};

async function renderSuggestions() {
  const box = $('sugOut');
  box.innerHTML = '<div class="skeleton" style="width:60%"></div><div class="skeleton"></div>';
  try {
    const r = await api('/api/suggestions');
    const list = r.suggestions || [];
    box.innerHTML = list.length
      ? list.map((s, idx) => `
        <div class="card" style="margin:0 0 10px;padding:13px;background:var(--panel2)">
          <div style="font-weight:650;font-size:14px">${esc(s.title)}</div>
          <div class="muted" style="margin-top:3px">${esc(s.body)} · 涉及 ${(s.item_ids || []).length} 条</div>
          <div class="row" style="margin-top:9px">
            ${(s.actions || []).map((a) => `<button class="btn ghost sm" onclick="sugAct('${esc(a)}',${idx})">${SUG_ACTION_LABEL[a] || esc(a)}</button>`).join('')}
          </div>
        </div>`).join('')
      : emptyBox('✨', '没有需要处理的建议，保持现状');
    window._sugList = list;
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

async function sugAct(action, idx) {
  const list = window._sugList || [];
  const s = list[idx];
  if (!s) return;
  try {
    await api('/api/suggestions/action', jsonBody({ action, item_ids: s.item_ids }));
    toast('已处理', 'ok');
    renderSuggestions();
    renderToday();
  } catch (e) {
    toast(e.message, 'bad');
  }
}

/* ------------------------------- 通知 / 提醒 ------------------------------- */
const REMIND_LABEL = { scheduled: '计划中', snoozed: '稍后', fired: '已提醒', canceled: '已取消' };
let seenNotif = new Set();
let notifPrimed = false;
let lastUnread = 0;
const BASE_TITLE = document.title;

/* 提醒送达方式（本地偏好，按设备保存）：
   - 提示音：WebAudio 合成，零素材、离线可用（浏览器 / 安卓 WebView / 桌面 Qt 都支持）
   - 系统通知：浏览器 Notification API（安卓 WebView 一般没有，自动跳过，仍有横幅+声音） */
const PREF_SOUND = 'cb_notif_sound';
const PREF_SYS = 'cb_notif_system';
const prefGet = (k, dflt) => { const v = localStorage.getItem(k); return v === null ? dflt : v === '1'; };
const prefSet = (k, on) => localStorage.setItem(k, on ? '1' : '0');
const sysNotifySupported = () => (typeof window.Notification === 'function');

let audioCtx = null;
function chime() {
  if (!prefGet(PREF_SOUND, true)) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    audioCtx = audioCtx || new AC();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const t = audioCtx.currentTime;
    [880, 1174.7].forEach((f, i) => {
      const o = audioCtx.createOscillator();
      const g = audioCtx.createGain();
      o.type = 'sine';
      o.frequency.value = f;
      const t0 = t + i * 0.16;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(0.22, t0 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.14);
      o.connect(g);
      g.connect(audioCtx.destination);
      o.start(t0);
      o.stop(t0 + 0.16);
    });
  } catch (e) { /* 静默：声音只是锦上添花 */ }
}

function sysNotify(title, body) {
  if (!prefGet(PREF_SYS, true) || !sysNotifySupported()) return;
  if (Notification.permission !== 'granted') return;
  try {
    const n = new Notification(title || '提醒', { body: body || '', tag: 'cordis-reminder' });
    n.onclick = () => { try { window.focus(); } catch (e) { /* 忽略 */ } n.close(); };
  } catch (e) { /* WebView 里可能不支持构造 */ }
}

async function requestSysNotify() {
  if (!sysNotifySupported()) return false;
  try {
    if (Notification.permission === 'granted') return true;
    return (await Notification.requestPermission()) === 'granted';
  } catch (e) { return false; }
}

function syncTitleBadge(n) {
  try {
    document.title = (n > 0 && document.hidden) ? `(${n}) ${BASE_TITLE}` : BASE_TITLE;
  } catch (e) { /* 忽略 */ }
}

document.addEventListener('visibilitychange', () => syncTitleBadge(lastUnread));

async function togglePref(which) {
  if (which === 'sound') {
    prefSet(PREF_SOUND, !prefGet(PREF_SOUND, true));
    const on = prefGet(PREF_SOUND, true);
    if (on) chime();
    toast('提示音：' + (on ? '开' : '关'), 'ok', 2000);
  } else {
    const on = !prefGet(PREF_SYS, true);
    prefSet(PREF_SYS, on);
    if (on) {
      const ok = await requestSysNotify();
      toast(ok ? '系统通知：开' : '系统通知未授权，仍会用应用内横幅 + 声音提醒', ok ? 'ok' : 'bad', ok ? 2000 : 5000);
    } else {
      toast('系统通知：关', 'ok', 2000);
    }
  }
  toggleNotifPanel();
  toggleNotifPanel();     // 重绘面板，刷新开关状态
}

async function pollNotif() {
  try {
    const r = await api('/api/notifications?unread=1');
    const n = r.unread || 0;
    lastUnread = n;
    const badge = $('bellCount');
    badge.textContent = n > 9 ? '9+' : String(n);
    badge.style.display = n > 0 ? 'grid' : 'none';
    syncTitleBadge(n);

    const fresh = (r.notifications || []).filter((x) => !seenNotif.has(x.id));
    if (!notifPrimed) {
      fresh.forEach((x) => seenNotif.add(x.id));
      notifPrimed = true;
    } else if (fresh.length) {
      fresh.forEach((x) => seenNotif.add(x.id));
      const f = fresh[0];
      showAlert(f.title || '提醒', f.body || '');
      chime();
      sysNotify(f.title || '提醒', f.body || '');
    }
  } catch (e) { /* 静默 */ }
}

function showAlert(title, body) {
  const b = $('alertBanner');
  b.innerHTML = `
    <div style="font-size:20px;line-height:1.1">🔔</div>
    <div style="flex:1;min-width:0">
      <div style="font-weight:650;font-size:14.5px">${esc(title)}</div>
      ${body ? `<div class="muted" style="margin-top:2px;word-break:break-word">${esc(body)}</div>` : ''}
    </div>
    <button class="btn sm" onclick="$('alertBanner').classList.remove('show')">知道了</button>`;
  b.classList.add('show');
  clearTimeout(window._alertT);
  window._alertT = setTimeout(() => b.classList.remove('show'), 12000);
}

async function toggleNotifPanel() {
  const p = $('notifPanel');
  if (p.classList.contains('show')) { p.classList.remove('show'); return; }
  p.innerHTML = '<div class="pop-head">加载中…</div>';
  p.classList.add('show');
  try {
    const [nr, rr] = await Promise.all([api('/api/notifications'), api('/api/reminders')]);
    const list = nr.notifications || [];
    const rems = (rr.reminders || []).filter((r) => r.state !== 'canceled');
    p.innerHTML =
      `<div class="pop-head">🔔 通知</div>` +
      (list.length
        ? list.slice(0, 12).map((x) => `
          <div class="rowline ${x.read ? '' : 'unread'}">
            <div style="flex:1;min-width:0">
              <div style="font-size:13.5px;font-weight:600">${esc(x.title || '通知')}</div>
              <div class="faint" style="word-break:break-word">${esc(x.body || '')}</div>
              <div class="faint" style="margin-top:2px">${esc(fmtDue(x.created_at))}</div>
            </div>
            ${x.read ? '' : `<button class="btn ghost sm" onclick="markReadNotif(${x.id})">已读</button>`}
          </div>`).join('')
        : '<div class="empty" style="padding:14px">暂无通知</div>') +
      `<div class="pop-head" style="border-top:1px solid var(--border);margin-top:6px;padding-top:10px">⏰ 提醒</div>` +
      (rems.length
        ? rems.slice(0, 12).map((r) => `
          <div class="rowline">
            <div style="flex:1;min-width:0">
              <div style="font-size:13.5px;font-weight:600">${esc(r.item_title || ('条目 #' + r.item_id))}</div>
              <div class="faint">
                <span class="badge">${REMIND_LABEL[r.state] || esc(r.state)}</span>
                ${r.due_at ? ' ⏰ ' + esc(fmtDue(r.due_at)) : ''}
                ${r.snooze_until ? ' · 稍后至 ' + esc(fmtDue(r.snooze_until)) : ''}
              </div>
            </div>
            ${(r.state === 'scheduled' || r.state === 'snoozed')
              ? `<button class="btn ghost sm" onclick="remindAct(${r.id},'snooze')">稍后</button>`
              : ''}
          </div>`).join('')
        : '<div class="empty" style="padding:14px">暂无提醒</div>') +
      `<div class="pop-head" style="border-top:1px solid var(--border);margin-top:6px;padding-top:10px">🔔 提醒方式</div>` +
      `<div class="rowline">
        <div style="flex:1;min-width:0">
          <div style="font-size:13px">提示音 <span class="badge">${prefGet(PREF_SOUND, true) ? '开' : '关'}</span></div>
          <div class="faint" style="margin-top:2px">系统通知 <span class="badge">${
            sysNotifySupported()
              ? (Notification.permission === 'granted' ? '已授权'
                 : Notification.permission === 'denied' ? '被拒绝' : '未授权')
              : '此环境不支持'}</span></div>
        </div>
        <button class="btn ghost sm" onclick="togglePref('sound')">声音</button>
        <button class="btn ghost sm" onclick="togglePref('sys')">系统通知</button>
      </div>`;;
  } catch (e) {
    p.innerHTML = `<div class="error-box" style="margin:8px">${esc(e.message)}</div>`;
  }
}

async function remindAct(id, action, minutes) {
  try {
    await api('/api/reminders/' + id, { method: 'PATCH', body: JSON.stringify({ action, minutes: minutes || 30 }) });
    toast(action === 'snooze' ? '已推迟 30 分钟' : '已更新', 'ok');
    toggleNotifPanel();
    toggleNotifPanel();
  } catch (e) {
    toast(e.message, 'bad');
  }
}

async function markReadNotif(id) {
  try {
    await api('/api/notifications/' + id + '/read', { method: 'POST' });
    toggleNotifPanel();
    toggleNotifPanel();
    pollNotif();
  } catch (e) { /* 静默 */ }
}

/* ------------------------------- 数据 ------------------------------- */
async function renderData() {
  await Promise.all([loadBackups(), renderHabits(false)]);
}

/* 习惯画像：近 N 天的常用时段 / 项目 / 标签 / 类型分布（本地统计，可手动重算）。 */
async function renderHabits(refresh) {
  const out = $('habitsOut');
  if (!out) return;
  if (refresh) out.innerHTML = '<div class="empty" style="padding:14px">重新统计中…</div>';
  try {
    const r = await api('/api/habits' + (refresh ? '?refresh=1' : ''));
    const p = (r && r.profile) || {};
    const total = p.total || 0;
    if (!total) {
      out.innerHTML = '<div class="empty" style="padding:14px">还没有足够的记录，多记几条就能看到你的习惯。</div>';
      return;
    }
    const tagChips = (arr) => (arr && arr.length)
      ? arr.map((t) => `<span class="badge">${esc(t)}</span>`).join(' ')
      : '<span class="faint">—</span>';
    const projChips = (arr) => (arr && arr.length)
      ? arr.map((x) => `<span class="badge">${esc(x.name)} ×${x.count}</span>`).join(' ')
      : '<span class="faint">—</span>';
    const typeChips = Object.entries(p.types || {})
      .map(([k, v]) => `<span class="badge ${esc(k)}">${esc(TYPE_LABEL[k] || k)} ${v}</span>`).join(' ');
    const hour = String(p.default_hour == null ? 9 : p.default_hour).padStart(2, '0');
    const modes = (r && r.modes) || {};
    const autoMode = String(modes.auto_project || 'auto');
    const boostMode = String(modes.suggest_boost || 'auto');
    const rules = Object.entries(p.tag_projects || {}).slice(0, 4);
    const dom = p.dominant_project;
    const autoTag = autoMode === 'off' ? '已关闭'
      : (autoMode === 'tags' ? '仅标签规则' : '标签规则 + 常记项目');
    out.innerHTML =
      `<div class="rowline">
        <div style="flex:1;min-width:0">
          <div style="font-size:13.5px">近 ${p.window_days || 120} 天共 <b>${total}</b> 条捕获${
            p.peak_weekday ? '，' + esc(p.peak_weekday) + '最多' : ''}</div>
          <div class="faint" style="margin-top:2px;word-break:break-word">
            常用时段：${p.peak_hour_range ? esc(p.peak_hour_range) : '记录还太少'}　·　
            只写日期时会补到 <b>${hour}:00</b>
          </div>
        </div>
      </div>
      <div class="rowline"><div style="flex:1;min-width:0">
        <div class="faint">常用项目</div><div style="margin-top:4px">${projChips(p.top_projects)}</div>
      </div></div>
      <div class="rowline"><div style="flex:1;min-width:0">
        <div class="faint">常用标签</div><div style="margin-top:4px">${tagChips(p.top_tags)}</div>
      </div></div>
      <div class="rowline"><div style="flex:1;min-width:0">
        <div class="faint">类型分布</div><div style="margin-top:4px">${typeChips || '—'}</div>
      </div></div>
      <div class="rowline"><div style="flex:1;min-width:0">
        <div style="font-size:13px">📁 自动关联项目 <span class="badge">${esc(autoTag)}</span>
          <span class="badge">建议加权 ${boostMode === 'off' ? '关' : '开'}</span></div>
        <div class="faint" style="margin-top:3px;word-break:break-word">
          ${dom ? `最常记在「${esc(dom.name)}」（${dom.count} 条，占 ${Math.round((dom.share || 0) * 100)}%）`
                 : '项目样本还不够，暂不猜（先手动挂几次项目就学会了）'}
        </div>
        ${rules.length ? `<div class="faint" style="margin-top:3px;word-break:break-word">学到的规则：${
          rules.map(([t, m]) => `${esc(t)} → ${esc(m.project)}`).join('　·　')}</div>` : ''}
      </div></div>
      <div class="faint" style="padding:4px 10px 8px">统计于 ${esc(fmtDue(p.built_at))}，每 30 分钟自动更新</div>`;
  } catch (e) {
    out.innerHTML = `<div class="error-box" style="margin:8px">${esc(e.message)}</div>`;
  }
}

/* ---- 文件保存：安卓走 CordisFiles 原生桥（直接写入系统「下载」目录），
        其他端走浏览器下载。避免 WebView 把下载交给外部浏览器后连不上 127.0.0.1。 ---- */
function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).replace(/^data:[^,]*,/, ''));
    fr.onerror = () => reject(fr.error || new Error('读取失败'));
    fr.readAsDataURL(blob);
  });
}

function fileStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`;
}

async function saveBlobToDevice(name, blob) {
  const bridge = window.CordisFiles;
  if (bridge && typeof bridge.save === 'function') {
    try {
      const b64 = await blobToBase64(blob);
      const r = bridge.save(name, blob.type || 'application/octet-stream', b64);
      if (typeof r === 'string' && r.indexOf('OK') === 0) {
        toast('已保存到「' + (r.slice(3) || '下载') + '」', 'ok', 4500);
        return true;
      }
      toast('保存失败：' + (r || '未知错误'), 'bad', 4200);
    } catch (e) {
      toast('保存失败：' + e.message, 'bad', 4200);
    }
    return false;
  }
  const pyapi = window.pywebview && window.pywebview.api;
  if (pyapi && typeof pyapi.save_file === 'function') {
    try {
      const b64 = await blobToBase64(blob);
      const p = await pyapi.save_file(name, b64, blob.type || 'application/octet-stream');
      if (typeof p === 'string' && p.indexOf('ERR:') !== 0) {
        toast('已保存到 ' + p, 'ok', 4500);
        return true;
      }
      toast('保存失败：' + String(p || '').replace(/^ERR:/, ''), 'bad', 4200);
    } catch (e) {
      toast('保存失败：' + e.message, 'bad', 4200);
    }
    return false;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  toast('已开始下载 ' + name, 'ok', 3500);
  return true;
}

async function downloadFromApi(url, name) {
  const res = await fetch(url);
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return saveBlobToDevice(name, await res.blob());
}

async function loadBackups() {
  const box = $('backupList');
  box.innerHTML = '<div class="skeleton" style="width:55%"></div><div class="skeleton"></div>';
  try {
    const r = await api('/api/backup');
    const list = r.backups || [];
    box.innerHTML = list.length
      ? list.map((b) => `
        <div class="item" style="align-items:center">
          <div class="body" style="cursor:default">
            <div class="title" style="font-size:13.5px">${esc(b.name)}</div>
            <div class="faint">${(b.size / 1024).toFixed(1)} KB · ${esc(fmtDue(b.mtime))}</div>
          </div>
          <div class="actions">
            <button class="btn ghost sm" data-backup="${esc(b.name)}" data-act="download">下载</button>
            <button class="btn ghost sm" data-backup="${esc(b.name)}" data-act="restore">恢复</button>
          </div>
        </div>`).join('')
      : emptyBox('🗄️', '暂无备份');
    box.querySelectorAll('[data-backup]').forEach((btn) => {
      btn.onclick = () => {
        const name = btn.dataset.backup;
        if (btn.dataset.act === 'download') downloadBackup(name);
        else restoreBackup(name);
      };
    });
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

async function doBackup() {
  const btn = $('btnBackup');
  if (btn) { btn.disabled = true; btn.textContent = '备份中…'; }
  try {
    const r = await api('/api/backup', jsonBody({ label: 'manual' }));
    toast('已创建备份 ' + r.name, 'ok', 4000);
    await loadBackups();
  } catch (e) {
    toast('备份失败：' + e.message, 'bad', 3800);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '创建备份'; }
  }
}

async function downloadBackup(name) {
  try {
    await downloadFromApi(
      '/api/backup/' + encodeURIComponent(name) + '/download?token=' + encodeURIComponent(getToken()),
      name
    );
  } catch (e) {
    toast('下载失败：' + e.message, 'bad', 4000);
  }
}

async function restoreBackup(name) {
  const ok = await askConfirm('恢复到该备份？当前数据会先自动备份一次。', { okText: '恢复', danger: true });
  if (!ok) return;
  try {
    await api('/api/backup/' + encodeURIComponent(name) + '/restore', { method: 'POST' });
    toast('已恢复', 'ok');
    setTimeout(() => location.reload(), 600);
  } catch (e) {
    toast(e.message, 'bad', 3200);
  }
}

async function doExport() {
  toast('正在导出…', 'ok', 1800);
  try {
    await downloadFromApi(
      '/api/export.json?token=' + encodeURIComponent(getToken()),
      `cordis-export-${fileStamp()}.json`
    );
  } catch (e) {
    toast('导出失败：' + e.message, 'bad', 4000);
  }
}

function doImportFile(file) {
  const fr = new FileReader();
  fr.onload = async () => {
    let data;
    try {
      data = JSON.parse(fr.result);
    } catch (e) {
      toast('不是有效的 JSON 文件', 'bad', 4000);
      return;
    }
    const items = (data && data.items) || [];
    if (!Array.isArray(items)) {
      toast('文件里没有 items 列表，可能不是外脑导出的文件', 'bad', 4200);
      return;
    }
    try {
      const r = await api('/api/import.json', jsonBody(data));
      toast(`导入完成：新增 ${r.imported} 条，跳过重复 ${Math.max(0, items.length - r.imported)} 条`, 'ok', 4200);
      setTimeout(() => location.reload(), 900);
    } catch (e) {
      toast('导入失败：' + e.message, 'bad', 4200);
    }
  };
  fr.onerror = () => toast('读取文件失败', 'bad', 4000);
  fr.readAsText(file);
}

/* ------------------------------- 设置 ------------------------------- */
async function openSettings() {
  try {
    const r = await api('/api/settings');
    $('settingsPath').textContent = '写入 ' + (r.config_path || 'config.toml') + '（保存会重写该文件）';
    $('settingsFields').innerHTML = (r.fields || []).map((f) => {
      const val = f.value == null ? '' : String(f.value);
      let input;
      if (f.type === 'select') {
        input = `<select data-path="${esc(f.path)}">` +
          (f.options || []).map((o) => `<option value="${esc(o)}" ${o === f.value ? 'selected' : ''}>${esc(o)}</option>`).join('') +
          '</select>';
      } else {
        input = `<input type="${f.type === 'password' ? 'password' : 'text'}" data-path="${esc(f.path)}" value="${esc(val)}">`;
      }
      return `<div class="field">
        <label>${esc(f.label)} <span class="faint" style="font-weight:400">${esc(f.path)}</span></label>
        ${input}
        ${f.hint ? `<div class="faint" style="margin-top:3px">${esc(f.hint)}</div>` : ''}
      </div>`;
    }).join('');
    $('settingsMsg').textContent = '';
    $('settingsMask').classList.add('show');
  } catch (e) {
    toast(e.message, 'bad', 3200);
  }
}

function closeSettings() { $('settingsMask').classList.remove('show'); }

async function saveSettings() {
  const payload = {};
  $$('#settingsFields [data-path]').forEach((el) => { payload[el.dataset.path] = el.value; });
  const btn = $('saveSettingsBtn');
  btn.disabled = true;
  $('settingsMsg').textContent = '保存中…';
  try {
    const r = await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
    const extra = (r.reloaded && r.reloaded.length) ? '（已即时生效：' + r.reloaded.join('、') + '）' : '';
    toast('已保存' + extra, 'ok', 3000);
    closeSettings();
    health();
  } catch (e) {
    $('settingsMsg').textContent = e.message;
  }
  btn.disabled = false;
}

/* ------------------------------- 文件 / 语音 ------------------------------- */
function doUploadFile(file) {
  if (file.size > 20 * 1024 * 1024) { toast('文件过大（上限 20MB）', 'bad'); return; }
  const fr = new FileReader();
  fr.onload = async () => {
    try {
      toast('解析文件中…');
      const r = await api('/api/capture/upload', jsonBody({
        filename: file.name,
        content_base64: String(fr.result).split(',')[1],
        source: 'file',
      }));
      const it = r.item || {};
      toast(`已捕获 · ${TYPE_LABEL[it.type] || it.type}`, 'ok');
      renderCapture();
      renderToday();
    } catch (e) {
      toast('上传失败：' + e.message, 'bad', 4000);
    }
  };
  fr.readAsDataURL(file);
}

let mediaRec = null;
let mediaChunks = [];
let recTimer = null;
let recStart = 0;
let recNative = false;
let permNagged = false;

function pickAudioMime() {
  if (!window.MediaRecorder) return '';
  const cands = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/ogg'];
  for (const m of cands) {
    try { if (MediaRecorder.isTypeSupported(m)) return m; } catch (e) { /* 忽略 */ }
  }
  return '';
}

// 部分设备/机型上，带 AEC / 降噪的采集会走 VOICE_COMMUNICATION 音频源，
// AudioRecord 起不来 → 报 "can't start audio source"。这里按「最简 → 默认」逐级重试。
const MIC_TRIES = [
  { audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } },
  { audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } },
  { audio: true },
];

async function openMic() {
  let last = null;
  for (let i = 0; i < MIC_TRIES.length; i++) {
    try {
      const s = await navigator.mediaDevices.getUserMedia(MIC_TRIES[i]);
      if (s && s.getAudioTracks().length) return s;
      last = last || new Error('没有获取到音轨');
    } catch (e) {
      last = e;
      const nm = (e && e.name) || '';
      if (nm === 'NotAllowedError' || nm === 'PermissionDeniedError' || nm === 'SecurityError' ||
          nm === 'NotFoundError' || nm === 'DevicesNotFoundError') throw e;
    }
  }
  throw last || new Error('无法打开麦克风');
}

/* ---- 原生录音（Android APK 专用）-------------------------------------
   WebView 的 getUserMedia 在部分机型上会被系统的音频栈挡住（NotReadableError /
   can't start audio source）。Java 侧通过 CordisAudio 桥直接调用 MediaRecorder，
   录音结果以 base64 返回，再走原有的 /api/capture/upload 上传。 */
function nativeRecorder() {
  try {
    const b = window.CordisAudio;
    return (b && typeof b.available === 'function' && b.available() === '1') ? b : null;
  } catch (e) {
    return null;
  }
}

function setRecUI(on) {
  $('capMic').textContent = on ? '⏹ 停止' : '🎤 语音';
  $('capMic').classList.toggle('primary', on);
  $('recPill').classList.toggle('show', on);
  $('recText').textContent = '录音中 00:00';
}

function startRecTimer() {
  recStart = Date.now();
  clearInterval(recTimer);
  recTimer = setInterval(() => {
    const s = Math.floor((Date.now() - recStart) / 1000);
    $('recText').textContent = `录音中 ${pad2(Math.floor(s / 60))}:${pad2(s % 60)}`;
    if (s >= 300) {
      toast('已达 5 分钟上限，自动停止');
      if (recNative) stopNativeRec();
      else if (mediaRec) mediaRec.stop();
    }
  }, 500);
}

function uploadVoiceBlob(blob, filename) {
  if (!blob || !blob.size) { toast('没有录到声音', 'bad', 4000); return; }
  const fr = new FileReader();
  fr.onload = async () => {
    try {
      toast('正在转写…');
      const r = await api('/api/capture/upload', jsonBody({
        filename,
        content_base64: String(fr.result).split(',')[1],
        source: 'voice',
      }));
      if (r.asr) {
        toast(`语音已转写（${r.asr_provider || 'ASR'}）`, 'ok', 3000);
      } else if (r.asr_error) {
        toast('语音已保存，转写未成功：' + String(r.asr_error).slice(0, 90), 'bad', 6000);
      } else {
        toast('语音已保存（未配置语音转写）', 'ok', 4000);
      }
      renderCapture();
      renderToday();
    } catch (e) {
      toast('上传失败：' + e.message, 'bad', 4000);
    }
  };
  fr.readAsDataURL(blob);
}

function startNativeRec(bridge) {
  let r = '';
  try { r = String(bridge.start() || ''); } catch (e) { r = 'ERR:' + ((e && e.message) || e); }
  if (r === 'NOPERM') {
    try { bridge.requestPermission(); } catch (e) { /* 忽略 */ }
    if (permNagged && typeof bridge.openSettings === 'function') {
      toast('请在系统设置中允许「录音 / 麦克风」权限', 'bad', 6000);
      try { bridge.openSettings(); } catch (e) { /* 忽略 */ }
    } else {
      permNagged = true;
      toast('请允许「录音」权限后再点一次 🎤', 'bad', 6000);
    }
    return true;          // 已处理：等系统弹窗
  }
  if (r !== 'OK') {
    toast('原生录音启动失败：' + r, 'bad', 6000);
    return false;         // 交给 WebView 的 getUserMedia 再试
  }
  recNative = true;
  setRecUI(true);
  startRecTimer();
  return true;
}

function stopNativeRec() {
  clearInterval(recTimer);
  setRecUI(false);
  recNative = false;
  let b64 = '';
  try { b64 = String(window.CordisAudio.stop() || ''); } catch (e) { b64 = 'ERR:' + ((e && e.message) || e); }
  if (!b64 || b64.slice(0, 4) === 'ERR:') {
    toast('录音失败：' + (b64 || '无数据'), 'bad', 6000);
    return;
  }
  let bin;
  try {
    bin = atob(b64);
  } catch (e) {
    toast('录音数据解码失败', 'bad', 5000);
    return;
  }
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  uploadVoiceBlob(new Blob([arr], { type: 'audio/mp4' }), 'voice.m4a');
}

async function doMic() {
  // 正在录音 → 停止
  if (recNative) { stopNativeRec(); return; }
  if (mediaRec && mediaRec.state === 'recording') { mediaRec.stop(); return; }

  // Android（p4a WebView）：优先用原生 MediaRecorder，绕开 WebView 的音频栈
  const bridge = nativeRecorder();
  if (bridge && startNativeRec(bridge)) return;

  const hasGUM = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  if (!hasGUM) {
    toast('当前环境不支持录音（需要 WebView 授权麦克风）', 'bad', 5000);
    return;
  }
  if (!window.isSecureContext) {
    toast('麦克风需要安全上下文：请通过 HTTPS 或 127.0.0.1 访问', 'bad', 5000);
    return;
  }

  try {
    const stream = await openMic();
    let rec;
    try {
      const mime = pickAudioMime();
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch (e1) {
      try {
        rec = new MediaRecorder(stream);       // 退回到平台默认编码
      } catch (e2) {
        stream.getTracks().forEach((t) => t.stop());
        throw e2;
      }
    }
    mediaRec = rec;
    const recType = rec.mimeType || 'audio/webm';
    const recExt = /mp4|aac/.test(recType) ? 'm4a' : (/ogg/.test(recType) ? 'ogg' : 'webm');
    mediaChunks = [];
    mediaRec.ondataavailable = (e) => { if (e.data && e.data.size) mediaChunks.push(e.data); };
    mediaRec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(recTimer);
      $('recPill').classList.remove('show');
      $('capMic').textContent = '🎤 语音';
      $('capMic').classList.remove('primary');

      const blob = new Blob(mediaChunks, { type: recType });
      mediaRec = null;
      uploadVoiceBlob(blob, 'voice.' + recExt);
    };

    try {
      mediaRec.start(250);
    } catch (e3) {
      stream.getTracks().forEach((t) => t.stop());
      mediaRec = null;
      throw e3;
    }
    recStart = Date.now();
    $('capMic').textContent = '⏹ 停止';
    $('capMic').classList.add('primary');
    $('recPill').classList.add('show');
    $('recText').textContent = '录音中 00:00';
    recTimer = setInterval(() => {
      const s = Math.floor((Date.now() - recStart) / 1000);
      $('recText').textContent = `录音中 ${pad2(Math.floor(s / 60))}:${pad2(s % 60)}`;
      if (s >= 300) { toast('已达 5 分钟上限，自动停止'); mediaRec && mediaRec.stop(); }
    }, 500);
  } catch (e) {
    const nm = (e && e.name) || '';
    const msg = (e && e.message) || String(e || '');
    if (nm === 'NotAllowedError' || nm === 'PermissionDeniedError' || nm === 'SecurityError') {
      toast('麦克风权限被拒绝：请在系统设置 → 应用 → 外脑 → 权限中允许麦克风', 'bad', 6000);
    } else if (nm === 'NotFoundError' || nm === 'DevicesNotFoundError') {
      toast('没有检测到麦克风设备', 'bad');
    } else if (nm === 'NotReadableError' || nm === 'TrackStartError' || nm === 'AbortError' ||
               /audio source|start.*audio/i.test(msg)) {
      toast('麦克风无法启动（可能被其它应用占用，或系统限制了录音）：' + (nm || msg), 'bad', 7000);
    } else {
      toast('无法录音：' + (nm ? nm + '：' : '') + msg, 'bad', 6000);
    }
    $('recPill').classList.remove('show');
    $('capMic').textContent = '🎤 语音';
    $('capMic').classList.remove('primary');
    clearInterval(recTimer);
  }
}

/* ------------------------------- 刷新 ------------------------------- */
function refreshAll() {
  if ($('sheetMask').classList.contains('show')) closeSheet();
  renderToday();
  if (currentTab === 'capture') renderCapture();
  if (currentTab === 'review') doReview(reviewKind);
  if (currentTab === 'suggest') renderSuggestions();
  if (currentTab === 'graph') renderGraph();
}

let appStarted = false;

function startApp() {
  if (appStarted) return;
  appStarted = true;
  health();
  renderToday();
  pollNotif();
  setInterval(health, 30000);
  setInterval(pollNotif, 10000);
  // 回到前台时刷新（Android WebView 从后台恢复）
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { health(); if (currentTab === 'today') renderToday(); pollNotif(); }
  });
}

/* ------------------------------- 事件绑定 ------------------------------- */
function bindEvents() {
  $$('.navbar .tab, .tabbar .tb').forEach((t) => {
    t.addEventListener('click', () => switchTab(t.dataset.tab));
  });

  $('quickBtn').onclick = quickCapture;
  $('quickInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') quickCapture(); });

  $('capBtn').onclick = doCapture;
  $('capText').addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') doCapture();
  });
  $('capFile').onclick = () => $('capFileInput').click();
  $('capFileInput').onchange = (e) => { if (e.target.files[0]) doUploadFile(e.target.files[0]); e.target.value = ''; };
  $('capMic').onclick = doMic;
  $('recentRefresh').onclick = renderCapture;

  $('searchBtn').onclick = doSearch;
  $('searchQ').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

  $('askBtn').onclick = doAsk;
  $('askQ').addEventListener('keydown', (e) => { if (e.key === 'Enter') doAsk(); });

  $('reviewDailyBtn').onclick = () => doReview('daily');
  $('reviewWeekBtn').onclick = () => doReview('weekly');
  $('reviewPolishBtn').onclick = doReviewAI;
  $('reviewDate').onchange = () => doReview(reviewKind);
  $('graphRefresh').onclick = renderGraph;
  $('sugRefresh').onclick = renderSuggestions;
  $('habitsRefresh').onclick = () => renderHabits(true);

  $('btnBackup').onclick = doBackup;
  $('btnExport').onclick = doExport;
  $('btnImport').onclick = () => $('impFile').click();
  $('impFile').onchange = (e) => { if (e.target.files[0]) doImportFile(e.target.files[0]); e.target.value = ''; };

  $('chipBell').onclick = toggleNotifPanel;
  $('btnLogout').onclick = doLogout;
  $('btnSettings').onclick = openSettings;

  $('sheetClose').onclick = closeSheet;
  $('sheetMask').addEventListener('click', (e) => { if (e.target === $('sheetMask')) closeSheet(); });
  $('settingsClose').onclick = closeSettings;
  $('settingsCancel').onclick = closeSettings;
  $('saveSettingsBtn').onclick = saveSettings;
  $('settingsMask').addEventListener('click', (e) => { if (e.target === $('settingsMask')) closeSettings(); });

  $$('#authTabs button').forEach((b) => { b.onclick = () => setAuthMode(b.dataset.mode); });
  $('authForm').addEventListener('submit', submitAuth);

  // 点击空白处收起通知面板
  document.addEventListener('click', (e) => {
    const p = $('notifPanel');
    if (!p.classList.contains('show')) return;
    if (p.contains(e.target) || $('chipBell').contains(e.target)) return;
    p.classList.remove('show');
  });

  // Esc 关闭弹层
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if ($('settingsMask').classList.contains('show')) closeSettings();
    else if ($('sheetMask').classList.contains('show')) closeSheet();
    else $('notifPanel').classList.remove('show');
  });
}

/* ------------------------------- 启动 ------------------------------- */
initTheme();
bindEvents();
setAuthMode('login');
initAuth();
