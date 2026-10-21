// ── Firebase config ──
const FIREBASE_CONFIG = window.AGENT_DASHBOARD_FIREBASE_CONFIG;

firebase.initializeApp(FIREBASE_CONFIG);
const auth = firebase.auth();
const db   = firebase.firestore();

const ALLOWED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml'];
const ALLOWED_IMAGE_EXT   = ['svg', 'png', 'jpg', 'jpeg', 'gif', 'webp'];
const MAX_IMG_PX  = 1200;  // max side after resize
const IMG_QUALITY = 0.82;  // JPEG compression

// ─────────────────────────────────────────
// Auth
// ─────────────────────────────────────────
auth.onAuthStateChanged(user => {
  if (user) {
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    document.getElementById('user-name').textContent = user.displayName?.split(' ')[0] || user.email;
    document.getElementById('user-avatar').src = user.photoURL || '';
    initRouter();
  } else {
    document.getElementById('login-screen').classList.remove('hidden');
    document.getElementById('app').classList.add('hidden');
  }
});

document.getElementById('btn-google-login').addEventListener('click', () => {
  auth.signInWithPopup(new firebase.auth.GoogleAuthProvider());
});
document.getElementById('btn-logout').addEventListener('click', () => auth.signOut());

// ─────────────────────────────────────────
// Router
// ─────────────────────────────────────────
let currentPage = 'graph';

function initRouter() {
  document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      navigateTo(link.dataset.page);
    });
  });
  navigateTo(new URLSearchParams(location.search).has('task') || location.hash === '#kanban' ? 'kanban' : 'graph');
}

function navigateTo(page) {
  currentPage = page;
  document.querySelectorAll('.nav-link').forEach(l => l.classList.toggle('active', l.dataset.page === page));
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === `page-${page}`));
  document.querySelector('.main-content').classList.toggle('full-bleed', page === 'graph');
  PAGES[page]?.();
}

// ─────────────────────────────────────────
// Pages
// ─────────────────────────────────────────
const PAGES = {
  graph:     renderGraph,
  dashboard: renderDashboard,
  kanban:    renderKanban,
  projects:  renderProjects,
  plans:     renderPlans,
  skills:    renderSkills,
};

// ── ГРАФ АГЕНТОВ ─────────────────────────
// Данные кладёт firebase_sync.py (agent_graph/current) каждые 30 минут,
// отрисовка — agent-graph.js (общий модуль с артефактом, копирует build.py).
let graphGenerated = null;

async function renderGraph() {
  const el = document.getElementById('page-graph');
  try {
    const doc = await db.collection('agent_graph').doc('current').get();
    if (!doc.exists) {
      el.innerHTML = `<p class="graph-empty">Карты ещё нет — её выгружает firebase_sync.py раз в 30 минут.</p>`;
      return;
    }
    const d = doc.data();
    if (d.generated === graphGenerated && el.querySelector('.ag-in')) return;
    graphGenerated = d.generated;
    mountAgentGraph(el, JSON.parse(d.json), {
      footer: 'Обновляется сама раз в 30 минут (firebase_sync.py). Собрать сейчас:<br><code>python3 /path/to/workspace/ClaudeAIAgent/agent-graph/build.py</code>'
    });
  } catch (e) {
    el.innerHTML = `<p class="graph-empty">Не удалось загрузить карту: ${e.message}</p>`;
  }
}

// ── DASHBOARD ─────────────────────────────
const JOB_DESCRIPTIONS = {
  auto_linter:       'Запускает линтер на коде в GitLab-репозиториях. Проверяет стиль, форматирование и типичные ошибки.',
  deadline_monitor:  'Проверяет дедлайны по задачам и проектам. Отправляет уведомление в Telegram если срок близко.',
  gitlab_sync:       'Синхронизирует issues, MR и коммиты из GitLab в базу знаний (Obsidian). Обновляет каждые несколько часов.',
  gitlab_full_clone: 'Полное клонирование всех репозиториев GitLab на локальную машину. Запускается раз в неделю.',
  health_check:      'Проверяет состояние системы: диск, память, CPU, доступность сервисов. Результат — в Telegram.',
  morning_briefing:  'Утренний дайджест: что запланировано на день, дедлайны, статус задач. Приходит в Telegram.',
  weekly_digest:     'Еженедельный отчёт по активности: задачи, коммиты, изменения за неделю. Отправляется в Telegram.',
  weekly_changes:    'Собирает список изменений в кодовой базе за неделю и формирует changelog.',
  wiki_sync:         'Синхронизация базы знаний с Яндекс.Вики. Сейчас отключена (Папайя выключена 29.07.2026).',
  fetch_telemost:    'Забирает транскрипции созвонов из Яндекс.Телемост для последующей обработки.',
};

let toastTimer = null;

function showToast(text, x, y) {
  hideToast();
  const toast = document.createElement('div');
  toast.id = 'job-toast';
  toast.className = 'job-toast';
  toast.textContent = text;
  document.body.appendChild(toast);

  const tw = 280;
  const left = Math.min(x + 12, window.innerWidth - tw - 16);
  toast.style.left = left + 'px';
  toast.style.top  = (y + 16) + 'px';
  requestAnimationFrame(() => toast.classList.add('job-toast-visible'));
}

function hideToast() {
  clearTimeout(toastTimer);
  toastTimer = null;
  document.getElementById('job-toast')?.remove();
}

function attachRowTooltips() {
  document.querySelectorAll('tr[data-job]').forEach(row => {
    const desc = JOB_DESCRIPTIONS[row.dataset.job];
    if (!desc) return;
    row.addEventListener('mouseenter', e => {
      toastTimer = setTimeout(() => showToast(desc, e.clientX, e.clientY), 1000);
    });
    row.addEventListener('mousemove', e => {
      const toast = document.getElementById('job-toast');
      if (toast) {
        const left = Math.min(e.clientX + 12, window.innerWidth - 296);
        toast.style.left = left + 'px';
        toast.style.top  = (e.clientY + 16) + 'px';
      }
    });
    row.addEventListener('mouseleave', hideToast);
  });
}

async function renderDashboard() {
  const el = document.getElementById('page-dashboard');
  el.innerHTML = `<div class="spinner">Загрузка...</div>`;

  const [cronSnap, tasksSnap] = await Promise.all([
    db.collection('cron_jobs').get(),
    db.collection('tasks').where('status', '==', 'in-progress').get()
  ]);

  const jobs = cronSnap.docs.map(d => ({ id: d.id, ...d.data() }));
  const inProgress = tasksSnap.size;
  const totalOk  = jobs.filter(j => j.lastStatus === 0).length;
  const totalErr = jobs.filter(j => j.lastStatus !== 0 && j.lastStatus != null).length;
  const healthy  = totalErr === 0 && jobs.length > 0;

  const lastRun = jobs.reduce((acc, j) => {
    if (!j.lastRun) return acc;
    const t = j.lastRun.toDate ? j.lastRun.toDate() : new Date(j.lastRun);
    return (!acc || t > acc) ? t : acc;
  }, null);

  el.innerHTML = `
    <div class="page-header">
      <h2 class="page-title">
        <span class="dot ${healthy ? 'dot-green' : totalErr > 0 ? 'dot-red' : 'dot-yellow'}"></span>
        &nbsp;Приборная панель
      </h2>
      <span style="font-size:12px;color:var(--text-dim)">
        Обновлено: ${lastRun ? lastRun.toLocaleTimeString('ru') : '—'}
      </span>
    </div>

    <div class="cards-grid">
      ${statCard('Задачи агента', jobs.length, 'cron-задач', '🤖')}
      ${statCard('Успешных', totalOk, 'последних запусков', '✅')}
      ${statCard('Ошибок', totalErr, 'требуют внимания', totalErr > 0 ? '❌' : '🟢')}
      ${statCard('В работе', inProgress, 'канбан-задач', '⚡')}
    </div>

    <div class="table-wrap">
      <div class="section-subtitle">Cron-задачи агента</div>
      <table>
        <thead><tr>
          <th>Задача</th><th>Последний запуск</th><th>Статус</th><th>Следующий запуск</th>
        </tr></thead>
        <tbody>
          ${jobs.length === 0
            ? '<tr><td colspan="4" style="color:var(--text-dim);text-align:center">Нет данных — запусти sync-скрипт</td></tr>'
            : jobs.map(j => {
                const t = j.lastRun?.toDate ? j.lastRun.toDate() : (j.lastRun ? new Date(j.lastRun) : null);
                const n = j.nextRun?.toDate ? j.nextRun.toDate() : (j.nextRun ? new Date(j.nextRun) : null);
                const ok = j.lastStatus === 0;
                return `<tr data-job="${j.id}" style="cursor:default">
                  <td><strong>${j.id}</strong></td>
                  <td style="color:var(--text-dim)">${t ? t.toLocaleString('ru') : '—'}</td>
                  <td><span class="badge ${j.lastStatus == null ? 'badge-gray' : ok ? 'badge-green' : 'badge-red'}">
                    ${j.lastStatus == null ? 'нет данных' : ok ? 'OK' : `rc=${j.lastStatus}`}
                  </span></td>
                  <td style="color:var(--text-dim)">${n ? n.toLocaleString('ru') : '—'}</td>
                </tr>`;
              }).join('')
          }
        </tbody>
      </table>
    </div>`;

  attachRowTooltips();
}

function statCard(label, value, sub, icon) {
  return `<div class="stat-card">
    <div class="stat-label">${label}</div>
    <div class="stat-value">${value}</div>
    <div class="stat-sub">${sub}</div>
    <div class="stat-icon">${icon}</div>
  </div>`;
}

// ── KANBAN ────────────────────────────────
const COLUMNS = [
  { id: 'backlog',     label: 'Backlog',      color: 'var(--text-dim)' },
  { id: 'todo',        label: 'To do',        color: 'var(--accent)' },
  { id: 'in-progress', label: 'In progress',  color: 'var(--yellow)' },
  { id: 'issue',       label: 'Issue',        color: 'var(--red)' },
  { id: 'test',        label: 'Test',         color: 'var(--purple)' },
  { id: 'done',        label: 'Done',         color: 'var(--green)' },
];

let kanbanTasks = [];
let draggedId   = null;
let boardDirty  = false;
let kanbanUnsub = null;
let pendingTaskId = new URLSearchParams(location.search).get('task');

function renderKanban() {
  const el = document.getElementById('page-kanban');
  el.innerHTML = `
    <div class="page-header">
      <h2 class="page-title">📋 Канбан</h2>
      <span id="kanban-sync" style="font-size:12px;margin-left:auto;margin-right:16px;color:var(--text-dim)">● подключаюсь…</span>
      <button class="btn btn-primary" id="btn-add-task">+ Задача</button>
    </div>
    <div class="kanban-scroll">
      <div class="kanban-board" id="kanban-board"></div>
    </div>`;

  document.getElementById('btn-add-task').addEventListener('click', () => openTaskModal(null));
  subscribeKanban();
}

// The listener used to die silently (no error handler) and a stream left over from laptop sleep
// stayed stale until a manual reload. Now it reconnects on error, on wake and on network return.
let kanbanSyncAt = null, kanbanSyncFailed = false, kanbanRetry = null;
function subscribeKanban() {
  if (kanbanUnsub) kanbanUnsub();
  clearTimeout(kanbanRetry);
  kanbanUnsub = db.collection('tasks').onSnapshot(snap => {
    kanbanSyncFailed = false;
    if (!snap.metadata.fromCache) kanbanSyncAt = new Date();
    kanbanTasks = snap.docs.map(d => ({ id: d.id, ...d.data() }));
    paintBoard();
    refreshAgentDetails();
    paintSync(snap.metadata.fromCache);
    if (pendingTaskId) { const task = kanbanTasks.find(t => t.id === pendingTaskId); if (task) { pendingTaskId = null; openTaskModal(task); } }
  }, err => {
    console.error('Kanban listener failed', err);
    kanbanUnsub = null; kanbanSyncFailed = true; paintSync(true);
    kanbanRetry = setTimeout(subscribeKanban, 5000);
  });
}
function paintSync(fromCache) {
  const el = document.getElementById('kanban-sync');
  if (!el) return;
  const time = kanbanSyncAt ? kanbanSyncAt.toLocaleTimeString('ru-RU') : '—';
  const offline = kanbanSyncFailed || fromCache;
  el.textContent = offline ? `● нет связи, переподключаюсь · данные на ${time}` : `● в реальном времени · ${time}`;
  el.style.color = offline ? 'var(--yellow)' : 'var(--text-dim)';
}
function resubscribeIfKanban() { if (currentPage === 'kanban' && auth.currentUser) subscribeKanban(); }
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') resubscribeIfKanban(); });
window.addEventListener('online', resubscribeIfKanban);
// Timers stop while the laptop sleeps: a long gap between ticks means we just woke up.
let wakeTick = Date.now();
setInterval(() => { if (Date.now() - wakeTick > 90000) resubscribeIfKanban(); wakeTick = Date.now(); }, 30000);

function sortedCards(colId) {
  return kanbanTasks
    .filter(t => t.status === colId)
    .sort((a, b) => {
      const ao = a.order ?? a.createdAt?.toMillis?.() ?? 0;
      const bo = b.order ?? b.createdAt?.toMillis?.() ?? 0;
      return ao - bo;
    });
}

function paintBoard() {
  const board = document.getElementById('kanban-board');
  if (!board) return;
  // Agents update cards every few seconds; repainting mid-drag dropped the card being dragged.
  if (draggedId) { boardDirty = true; return; }
  boardDirty = false;
  const scroll = {};
  board.querySelectorAll('.kanban-cards').forEach(c => { scroll[c.id] = c.scrollTop; });

  board.innerHTML = COLUMNS.map(col => {
    const cards = sortedCards(col.id);
    return `
      <div class="kanban-col" data-col="${col.id}">
        <div class="kanban-col-header" style="color:${col.color}">
          ${col.label} <span class="col-count">${cards.length}</span>
        </div>
        <div class="kanban-cards" id="col-${col.id}">
          ${cards.map(t => cardHtml(t)).join('')}
          <button class="add-card-btn" onclick="openTaskModal(null,'${col.id}')">+ Добавить</button>
        </div>
      </div>`;
  }).join('');
  board.querySelectorAll('.kanban-cards').forEach(c => { if (scroll[c.id]) c.scrollTop = scroll[c.id]; });

  // Attach drag events to cards
  board.querySelectorAll('.kanban-card').forEach(card => {
    card.addEventListener('dragstart', e => {
      draggedId = card.dataset.id;
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });
    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      draggedId = null;
      if (boardDirty) paintBoard();
    });
    card.addEventListener('dragover', e => {
      e.preventDefault();
      e.stopPropagation();
      if (card.dataset.id === draggedId) return;
      const dragEl = board.querySelector('.kanban-card.dragging');
      if (!dragEl) return;
      const rect = card.getBoundingClientRect();
      const isBefore = e.clientY < rect.top + rect.height / 2;
      moveDraggedCardTo(dragEl, card, isBefore);
    });
    card.addEventListener('click', () => openTaskModal(kanbanTasks.find(t => t.id === card.dataset.id)));
  });

  // Attach drop events to columns
  board.querySelectorAll('.kanban-cards').forEach(col => {
    col.addEventListener('dragover', e => {
      e.preventDefault();
      col.classList.add('drag-over');
      if (!e.target.closest('.kanban-card')) {
        const dragEl = board.querySelector('.kanban-card.dragging');
        if (dragEl && dragEl.parentElement !== col) {
          col.insertBefore(dragEl, col.querySelector('.add-card-btn'));
        }
      }
    });
    col.addEventListener('dragleave', e => {
      if (!col.contains(e.relatedTarget)) col.classList.remove('drag-over');
    });
    col.addEventListener('drop', e => dropCard(e, col.closest('.kanban-col').dataset.col));
  });
}

// Moves the dragged card element next to `card` (FLIP-animated), so the rest
// of the column visibly slides aside to show where the card will land.
function moveDraggedCardTo(dragEl, card, isBefore) {
  const cards = Array.from(card.parentElement.querySelectorAll('.kanban-card'));
  const firstRects = new Map(cards.map(c => [c, c.getBoundingClientRect()]));

  card.parentElement.insertBefore(dragEl, isBefore ? card : card.nextSibling);

  cards.forEach(c => {
    if (c === dragEl) return;
    const before = firstRects.get(c);
    const after = c.getBoundingClientRect();
    const dy = before.top - after.top;
    if (dy) {
      c.style.transition = 'none';
      c.style.transform = `translateY(${dy}px)`;
      requestAnimationFrame(() => {
        c.style.transition = 'transform .15s ease';
        c.style.transform = '';
      });
    }
  });
}

const PHASE_LABELS = {awaiting_approval:'Ждёт решения в Telegram',queued:'В очереди',routing:'Выбор исполнителя',executing:'Выполнение',reviewing:'Приёмка',done:'Готово',test:'Ждёт приёмки',issue:'Нужен Антон',interrupted:'Прервано'};
let modalTaskId = null;
const PROVIDER_LABELS = {codex:'Codex', claude:'Claude'};
// Who works on the card now, or who worked last. Earlier version mixed the new route's provider with
// the previous run's model ("Claude · gpt-6.1-sol") and ignored the live executor after a reroute.
function executorName(t) {
  const a=t.agentDesk || {}, c=a.currentExecutor || {}, e=a.executor, r=a.route || {};
  const label=(p,m)=>`${PROVIDER_LABELS[p] || p}${m ? ' · '+m : ''}`;
  if (c.status==='running' && c.phase==='routing') return 'Claude · Sonnet выбирает исполнителя';
  if (c.status==='running' && c.phase==='reviewing') return 'Claude · Sonnet принимает работу';
  if (a.phase==='queued' && a.nextRoute?.provider) return 'Далее: '+label(a.nextRoute.provider, a.nextRoute.model);
  if (e?.provider) return label(e.provider, e.actual_model || e.model)+(e.state==='failed' ? ' — сбой' : '');
  if (c.status==='running' && c.provider) return label(c.provider, c.model || (c.provider===r.provider ? r.model : null));
  return r.provider ? label(r.provider, r.model) : 'Исполнитель не записан';
}
function phaseOf(t) {
  const a=t.agentDesk || {}, c=a.currentExecutor || {};
  return c.status==='running' ? c.phase : a.phase;
}
function authActions(t) {
  let request=t.agentDesk?.authRequest;
  if (!request && /b24u/i.test(t.agentNotes || '') && /авториз|вход|браузер|доступ/i.test(t.agentNotes || '')) request={url:'https://crm.b24u.ru/auth/',browser:'codex'};
  if (!request || t.status!=='issue') return '';
  try { const u=new URL(request.url); if(u.protocol!=='https:' || u.username || u.password || !['codex','chrome'].includes(request.browser)) return ''; } catch { return ''; }
  const url='agentdesk://authorize?'+new URLSearchParams({url:request.url,browser:request.browser}).toString();
  return `<div class="meta-block" style="margin:12px 0"><a class="btn btn-primary" style="display:inline-flex;text-decoration:none" href="${esc(url)}">Открыть вход · ${request.browser==='codex'?'встроенный браузер':'Chrome'}</a><p style="font-size:12px;overflow-wrap:anywhere">${esc(request.url)}</p><p><a href="${esc(request.url)}" target="_blank" rel="noopener noreferrer">Открыть страницу здесь</a></p>${/подключ.{0,30}браузер|браузер.{0,30}не.{0,5}подключ/i.test(t.agentNotes || '')?'<p style="font-size:12px">Агенту также нужен подключённый браузер. Вход в CRM не подключает его автоматически.</p>':''}<small>После входа верни задачу в To do. Вход не разрешает внешнюю отправку.</small></div>`;
}
function agentDetails(t) {
 const a=t.agentDesk || {}, r=a.route || {}, u=a.usage, c=a.currentExecutor || {};
 const count=u?.known ? new Intl.NumberFormat('ru-RU').format(u.total) : null;
 const phase=phaseOf(t);
 const consent=a.approvalRequest; const consentLabel={pending:'Ждёт ответа в Telegram',authorized:'Разрешено Антоном',denied:'Антон запретил',used:'Разрешённое действие выполнено',uncertain:'Исход действия требует проверки'};
 return `${consent?`<div class="meta-block"><div class="meta-label">Согласование</div><strong>${esc(consentLabel[consent.status] || consent.status)}</strong><p style="font-size:12px;overflow-wrap:anywhere">${esc(consent.action?.target || '')}</p><small>Ответь кнопкой Можно или Нельзя в Telegram. Одно разрешение — одно действие.</small></div>`:''}<div class="meta-block"><div class="meta-label">Исполнитель</div><strong>${esc(executorName(t))}</strong><p>${esc(PHASE_LABELS[phase] || phase || 'Нет данных о запуске')}${r.effort?' · '+esc(r.effort):''}</p>${c.agents?.length?`<small>Агенты: ${esc(c.agents.join(', '))}</small>`:''}${r.reason?`<p style="font-size:12px">${esc(r.reason)}</p>`:''}${a.runtime?.actual_model?`<small>Фактически: ${esc(a.runtime.actual_model)}</small>`:''}${a.reviewRuntime?.actual_model?`<p style="font-size:12px">Приёмка: Claude · ${esc(a.reviewRuntime.actual_model)}</p>`:''}</div><div class="meta-block"><div class="meta-label">Токены по задаче</div><strong>${count || (phase==='executing' || phase==='routing' ? 'Ожидаем данные' : 'Нет данных')}</strong>${count?`<p style="font-size:12px">Вход: ${u.input.toLocaleString('ru-RU')}<br>Выход: ${u.output.toLocaleString('ru-RU')}<br>Кэш: ${(u.cache_read+u.cache_write).toLocaleString('ru-RU')}</p><small>Включая роутер, выполнение и приёмку. Это объём токенов, не стоимость подписки.</small>`:'<small>Старые запуски не содержат статистики. В новом запуске данные появятся по мере ответа исполнителя.</small>'}</div>`;
}
// Card chat: the agent's questions and Антон's answers. An Issue card from before the chat shows its
// agentNotes as the agent's first message, so the dialogue starts where the agent stopped.
function chatMessages(t) {
  const chat = t.chat || [];
  if (!chat.length && t.agentNotes && t.status === 'issue') return [{id:'notes', from:'agent', text:t.agentNotes, virtual:true}];
  return chat;
}
function chatStateLine(t) {
  const last = chatMessages(t).slice(-1)[0];
  if (t.status === 'issue' && last?.from === 'anton') return 'Ответ получен — агент забирает задачу в работу…';
  if (t.status === 'issue' && t.agentDesk?.phase === 'awaiting_approval') return 'Агент ждёт кнопку Можно / Нельзя в Telegram.';
  if (t.status === 'issue') return 'Агент ждёт твоего ответа. Как только ответишь, задача сама вернётся в работу.';
  if (t.status === 'in-progress') return 'Агент работает. Сообщение он увидит при следующем запуске.';
  return 'Сообщения передаются агенту вместе с задачей.';
}
function chatListHtml(t) {
  const msgs = chatMessages(t);
  if (!msgs.length) return '<div class="chat-empty">Пока пусто. Если агенту что-то понадобится, он спросит здесь.</div>';
  return msgs.map(m => {
    const at = m.at ? new Date(m.at).toLocaleString('ru-RU', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'}) : '';
    return `<div class="chat-msg ${m.from === 'anton' ? 'chat-me' : 'chat-agent'}"><div class="chat-who">${m.from === 'anton' ? 'Ты' : 'Агент'}${at ? ' · '+esc(at) : ''}</div><div class="chat-text">${esc(m.text)}</div></div>`;
  }).join('');
}
function chatHtml(t) {
  return `<div class="task-chat">
    <div class="task-section-label">Чат с агентом</div>
    <div id="chat-list" class="chat-list">${chatListHtml(t)}</div>
    <div id="chat-state" class="chat-state">${esc(chatStateLine(t))}</div>
    <div class="chat-input-row">
      <textarea id="chat-input" class="chat-input" rows="2" placeholder="Ответ агенту… Enter — отправить, Shift+Enter — новая строка"></textarea>
      <button id="chat-send" class="btn btn-primary" type="button">Отправить</button>
    </div>
  </div>`;
}
let chatShown = '';
function paintChat(t) {
  const list = document.getElementById('chat-list'); if (!list) return;
  const html = chatListHtml(t);
  if (html !== chatShown) {
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    list.innerHTML = html; chatShown = html;
    if (atBottom) list.scrollTop = list.scrollHeight;
  }
  const state = document.getElementById('chat-state'); if (state) state.textContent = chatStateLine(t);
}
function setupChat(taskId) {
  const input = document.getElementById('chat-input'), button = document.getElementById('chat-send');
  const list = document.getElementById('chat-list'); list.scrollTop = list.scrollHeight;
  async function send() {
    const text = input.value.trim(); if (!text) return;
    button.disabled = true;
    try {
      const fresh = kanbanTasks.find(t => t.id === taskId) || {};
      const add = [];
      // Keep the old agentNotes question in the history the agent will read.
      if (!(fresh.chat || []).length && fresh.agentNotes && fresh.status === 'issue') add.push({id:crypto.randomUUID(), from:'agent', text:fresh.agentNotes, at:new Date().toISOString()});
      add.push({id:crypto.randomUUID(), from:'anton', text, at:new Date().toISOString()});
      await db.collection('tasks').doc(taskId).set({chat: firebase.firestore.FieldValue.arrayUnion(...add), updatedAt: firebase.firestore.FieldValue.serverTimestamp()}, {merge:true});
      input.value = '';
    } catch (e) { alert('Не отправилось: ' + e.message); }
    finally { button.disabled = false; input.focus(); }
  }
  button.onclick = send;
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) { e.preventDefault(); e.stopPropagation(); send(); }
  });
}

// Manual executor picker. Efforts are the ones each executor accepts (MANUAL_EFFORTS in AgentDesk app.py).
const MANUAL_EXECUTORS = {
  sonnet: {label:'Claude Sonnet', efforts:['low','medium','high','max']},
  opus:   {label:'Claude Opus',   efforts:['low','medium','high','xhigh','max']},
  codex:  {label:'GPT-6.1 Sol',   efforts:['low','medium']},
};
const EFFORT_LABELS = {low:'low — лёгкий', medium:'medium — средний', high:'high — высокий', xhigh:'xhigh — очень высокий', max:'max — максимальный'};
function effortOptions(key, current) {
  const list = MANUAL_EXECUTORS[key]?.efforts || [];
  const pick = list.includes(current) ? current : (key === 'codex' ? 'medium' : 'high');
  return list.map(e => `<option value="${e}" ${e === pick ? 'selected' : ''}>${EFFORT_LABELS[e]}</option>`).join('');
}
function executorPickerHtml(t) {
  const m = t.manualExecutor || {};
  return `<div class="meta-block">
    <div class="meta-label">Назначить исполнителя</div>
    <select id="f-exec" class="form-select">
      <option value="">Авто (по правилам)</option>
      ${Object.entries(MANUAL_EXECUTORS).map(([k, v]) => `<option value="${k}" ${m.model_key === k ? 'selected' : ''}>${v.label}</option>`).join('')}
    </select>
    <select id="f-effort" class="form-select ${m.model_key ? '' : 'hidden'}" style="margin-top:8px">${m.model_key ? effortOptions(m.model_key, m.effort) : ''}</select>
    <small id="f-exec-hint" style="display:block;margin-top:6px"></small>
  </div>`;
}
function setupExecutorPicker(t) {
  const exec = document.getElementById('f-exec'), effort = document.getElementById('f-effort'), hint = document.getElementById('f-exec-hint');
  const initial = t?.manualExecutor?.model_key || '';
  function paint() {
    const key = exec.value;
    effort.classList.toggle('hidden', !key);
    if (key && !MANUAL_EXECUTORS[key].efforts.includes(effort.value)) effort.innerHTML = effortOptions(key, effort.value);
    const status = (kanbanTasks.find(x => x.id === modalTaskId) || t || {}).status;
    const changed = key !== initial || (key && effort.value !== (t?.manualExecutor?.effort || ''));
    hint.textContent = !changed ? (key ? 'Выбрано вручную: роутер и эскалации не меняют исполнителя.' : '')
      : ['in-progress', 'todo', 'issue'].includes(status) ? 'После сохранения текущий запуск остановится, задача начнётся заново с этим исполнителем.'
      : 'Применится при следующем запуске задачи.';
  }
  exec.onchange = paint; effort.onchange = paint; paint();
}

function notesHtml(t) {
  if (!t.agentNotes || chatMessages(t).length) return '';
  return `<div class="task-agent-notes-block">
          <div class="task-section-label" style="color:var(--yellow)">⚠️ Проблема от агента</div>
          <textarea id="f-agent-notes" class="task-desc-textarea" style="min-height:80px;border-color:rgba(255,199,119,.4);color:var(--yellow)">${esc(t.agentNotes)}</textarea>
        </div>`;
}
function resultHtml(t) {
  const a=t.agentDesk || {};
  if (!a.result) return '';
  return `<div style="padding:16px;margin:16px 0;border:1px solid var(--border);border-radius:8px"><div class="task-section-label">Результат агента</div><p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(a.result.summary)}</p><small style="white-space:pre-wrap;overflow-wrap:anywhere">${esc((a.result.evidence || []).join('\n'))}</small>${a.review ? `<p>Приёмка Claude: ${esc(a.review.summary)}</p>` : ''}</div>`;
}
// What the modal showed when opened or last refreshed; fields the user did not touch follow the agent.
let modalShown = {status:null, agentNotes:null};
function refreshAgentDetails() {
 const overlay=document.getElementById('modal-overlay');
 if (!modalTaskId || overlay?.classList.contains('hidden')) return;
 const task=kanbanTasks.find(t=>t.id===modalTaskId); if(!task)return;
 const live=document.getElementById('agent-live');if(live)live.innerHTML=agentDetails(task);
 const auth=document.getElementById('auth-actions');if(auth)auth.innerHTML=authActions(task);
 const result=document.getElementById('agent-result-live');if(result)result.innerHTML=resultHtml(task);
 paintChat(task);
 const notes=document.getElementById('agent-notes-live'), notesInput=document.getElementById('f-agent-notes');
 if (notes && (!notesInput || notesInput.value===(modalShown.agentNotes || '')) && (task.agentNotes || null)!==modalShown.agentNotes) {
   notes.innerHTML=notesHtml(task); modalShown.agentNotes=task.agentNotes || null;
 }
 const status=document.getElementById('f-status');
 if (status && status.value===modalShown.status && task.status!==modalShown.status) { status.value=task.status; modalShown.status=task.status; }
}

function cardHtml(t) {
  const pColor = { high: 'var(--red)', medium: 'var(--yellow)', low: 'var(--text-dim)' }[t.priority] || 'var(--text-dim)';
  const tags   = t.tags || [];
  const isHumanOnly = tags.some(tag => tag.toLowerCase() === 'only with human');
  const otherTags   = tags.filter(tag => tag.toLowerCase() !== 'only with human');

  return `<div class="kanban-card" data-id="${t.id}" draggable="true">
    <div class="card-title-row">
      <div class="card-title">${esc(t.title)}</div>
      ${isHumanOnly ? `<span class="tag-human-only" title="Only with human">🔒</span>` : ''}
    </div>
    ${t.description ? `<div class="card-desc">${esc(t.description).slice(0, 100)}${t.description.length > 100 ? '…' : ''}</div>` : ''}
    ${t.agentDesk ? `<div class="card-agent-notes">${esc(executorName(t))} · ${esc(PHASE_LABELS[phaseOf(t)] || phaseOf(t) || '')}${t.agentDesk.usage?.known?' · '+t.agentDesk.usage.total.toLocaleString('ru-RU')+' ток.':''}</div>` : ''}
    ${t.manualExecutor ? `<div class="card-agent-notes">👤 ${esc(MANUAL_EXECUTORS[t.manualExecutor.model_key]?.label || '')} · ${esc(t.manualExecutor.effort || '')}</div>` : ''}
    ${t.agentNotes ? `<div class="card-agent-notes">${t.status === 'issue' && chatMessages(t).slice(-1)[0]?.from === 'anton' ? '💬 Ответ отправлен' : '⚠️ '+esc(t.agentNotes).slice(0, 80)}</div>` : ''}
    <div class="card-footer">
      ${t.priority ? `<span style="font-size:10px;color:${pColor}">● ${t.priority}</span>` : '<span></span>'}
      <div class="card-tags">
        ${(t.images?.length ? `<span class="card-tag" title="${t.images.length} картинок">🖼 ${t.images.length}</span>` : '')}
        ${otherTags.map(tag => `<span class="card-tag">${esc(tag)}</span>`).join('')}
      </div>
    </div>
  </div>`;
}

async function dropCard(e, colId) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  if (!draggedId) return;

  // The dragged card's DOM position (already live-updated on dragover)
  // reflects exactly where it should land, so read the order from its neighbors.
  const colEl   = document.getElementById(`col-${colId}`);
  const domIds  = Array.from(colEl.querySelectorAll('.kanban-card')).map(c => c.dataset.id);
  const dragIdx = domIds.indexOf(draggedId);

  // Cards in target column excluding the dragged one, sorted by current order
  const colCards = sortedCards(colId).filter(t => t.id !== draggedId);
  const prevId = domIds[dragIdx - 1];
  const nextId = domIds[dragIdx + 1];
  const prev = colCards.find(t => t.id === prevId);
  const next = colCards.find(t => t.id === nextId);
  const orderOf = t => t?.order ?? t?.createdAt?.toMillis?.() ?? 0;

  let newOrder;
  if (prev && next) {
    newOrder = (orderOf(prev) + orderOf(next)) / 2;
  } else if (next) {
    newOrder = orderOf(next) - 1000;
  } else if (prev) {
    newOrder = orderOf(prev) + 1000;
  } else {
    newOrder = Date.now();
  }

  const originalTask = kanbanTasks.find(t => t.id === draggedId);
  await db.collection('tasks').doc(draggedId).update({
    ...(colId === 'todo' && originalTask?.status !== 'todo' ? {routeNonce: crypto.randomUUID()} : {}),
    ...(colId === 'todo' && originalTask?.status === 'test' && originalTask?.agentDesk?.route?.provider === 'codex' ? {'agentDesk.codexFailures': firebase.firestore.FieldValue.increment(1)} : {}),
    status:    colId,
    order:     newOrder,
    updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
  });
  draggedId = null;
  if (boardDirty) paintBoard();
}

function openTaskModal(task, defaultCol = 'backlog') {
  const isNew  = !task;
  const taskId = task?.id || db.collection('tasks').doc().id;
  modalTaskId=taskId;
  // Filter out any broken/stuck-uploading images from previous sessions
  let modalImages = (task?.images || []).filter(img => img.url && !img.uploading);

  const tags        = task?.tags || [];
  const isHumanOnly = tags.some(t => t.toLowerCase() === 'only with human');
  const otherTags   = tags.filter(t => t.toLowerCase() !== 'only with human').join(', ');

  const pOpts = ['', 'high', 'medium', 'low'];
  const pLabels = { '': '— Приоритет', high: '🔴 High', medium: '🟡 Medium', low: '🔵 Low' };

  document.getElementById('modal-title').textContent = isNew ? 'Новая задача' : 'Задача';
  document.getElementById('modal-delete').classList.toggle('hidden', isNew);

  document.getElementById('modal-body').innerHTML = `
    <div class="task-modal-layout">

      <!-- LEFT: content -->
      <div class="task-modal-left">
        <input id="f-title" class="task-title-input" value="${esc(task?.title || '')}" placeholder="Название задачи...">

        <div class="task-section-label">Описание / задание для агента</div>
        <textarea id="f-desc" class="task-desc-textarea" placeholder="Опиши ожидаемый результат и критерий готовности. To do запускает выполнение автоматически.">${esc(task?.description || '')}</textarea>

        ${isNew ? '' : chatHtml(task)}
        <div id="agent-notes-live">${notesHtml(task || {})}</div>

        <div id="auth-actions">${authActions(task || {})}</div>
        <div id="agent-result-live">${resultHtml(task || {})}</div>
        <div class="task-section-label" style="margin-top:16px">Вложения</div>
        <div class="image-drop-zone" id="image-drop-zone">
          📎 Нажми, вставь скриншот (Ctrl+V) или перетащи файл — svg, png, jpg, jpeg
        </div>
        <input type="file" id="f-image-input" accept="${ALLOWED_IMAGE_TYPES.join(',')}" multiple class="hidden">
        <div class="image-grid" id="image-grid"></div>
      </div>

      <!-- RIGHT: meta -->
      <div class="task-modal-right">
        <div id="agent-live">${agentDetails(task || {})}</div>
        ${executorPickerHtml(task || {})}
        <div class="meta-block"><div class="meta-label">Рабочая папка (необязательно)</div><input id="f-workspace" class="form-input" value="${esc(task?.workspace || '')}" placeholder="/путь/к/проекту"></div>
        <div class="meta-block">
          <div class="meta-label">Колонка</div>
          <select id="f-status" class="form-select">
            ${COLUMNS.map(c => `<option value="${c.id}" ${(task?.status || defaultCol) === c.id ? 'selected' : ''}>${c.label}</option>`).join('')}
          </select>
        </div>
        <div class="meta-block">
          <div class="meta-label">Приоритет</div>
          <select id="f-priority" class="form-select">
            ${pOpts.map(p => `<option value="${p}" ${(task?.priority || '') === p ? 'selected' : ''}>${pLabels[p]}</option>`).join('')}
          </select>
        </div>
        <div class="meta-block">
          <div class="meta-label">Теги</div>
          <input id="f-tags" class="form-input" value="${esc(otherTags)}" placeholder="срочно, backend">
        </div>
        <div class="meta-block">
          <label class="human-only-label">
            <input type="checkbox" id="f-human-only" ${isHumanOnly ? 'checked' : ''}>
            <span>🔒 Only with human</span>
          </label>
        </div>
      </div>
    </div>`;

  modalShown = {status: document.getElementById('f-status').value, agentNotes: task?.agentNotes || null};
  setupImageAttachments(taskId, () => modalImages, imgs => { modalImages = imgs; });
  setupExecutorPicker(task);
  chatShown = '';
  if (!isNew) { setupChat(taskId); paintChat(task); }

  document.getElementById('modal-body').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      document.getElementById('modal-save').click();
    }
  });

  document.getElementById('modal-save').onclick = async () => {
    const title = document.getElementById('f-title').value.trim();
    if (!title) return;

    const humanOnly = document.getElementById('f-human-only').checked;
    const tagsRaw   = document.getElementById('f-tags').value.split(',').map(s => s.trim()).filter(Boolean);
    const allTags   = humanOnly ? [...tagsRaw, 'only with human'] : tagsRaw;

    const data = {
      title,
      workspace: document.getElementById('f-workspace').value.trim(),
      description: document.getElementById('f-desc').value.trim(),
      status:      document.getElementById('f-status').value,
      priority:    document.getElementById('f-priority').value,
      tags:        allTags,
      images:      modalImages.filter(img => img.url && !img.uploading),
      updatedAt:   firebase.firestore.FieldValue.serverTimestamp(),
    };

    // Compare with the card as it is now, not as it was when the modal opened: the agent may have
    // moved it meanwhile. Saving used to write the stale column back, which threw away the
    // agent's result and could start the same task twice.
    const fresh = kanbanTasks.find(t => t.id === taskId) || task;
    const notesInput = document.getElementById('f-agent-notes');
    if (notesInput && notesInput.value !== (modalShown.agentNotes || '')) data.agentNotes = notesInput.value;
    if (!isNew && data.status === modalShown.status) data.status = fresh?.status || data.status;

    if (data.status === 'todo' && (fresh?.status !== 'todo' || fresh?.title !== title || fresh?.description !== data.description || (fresh?.workspace || '') !== data.workspace || JSON.stringify(fresh?.tags || []) !== JSON.stringify(allTags) || JSON.stringify(fresh?.images || []) !== JSON.stringify(data.images))) data.routeNonce = crypto.randomUUID();
    // Manual executor. Replacing it on a running, queued or waiting card restarts the card: the worker
    // sees the new routeNonce, stops the old CLI run and discards its result.
    const execKey = document.getElementById('f-exec').value;
    const manual = execKey ? {model_key: execKey, effort: document.getElementById('f-effort').value} : null;
    if (JSON.stringify(manual) !== JSON.stringify(fresh?.manualExecutor || null)) {
      data.manualExecutor = manual;
      if (!isNew && ['in-progress', 'todo', 'issue'].includes(fresh?.status) && data.status === fresh.status) { data.status = 'todo'; data.routeNonce = crypto.randomUUID(); }
    }
    if (data.status === 'todo' && fresh?.status === 'test' && fresh?.agentDesk?.route?.provider === 'codex') data.agentDesk = {codexFailures: firebase.firestore.FieldValue.increment(1)};  // merge: only this field
    if (isNew) {
      data.createdAt = firebase.firestore.FieldValue.serverTimestamp();
      data.order     = Date.now();
    }
    await db.collection('tasks').doc(taskId).set(data, { merge: true });
    closeModal();
  };

  document.getElementById('modal-delete').onclick = async () => {
    if (!confirm('Удалить задачу?')) return;
    await db.collection('tasks').doc(task.id).delete();
    closeModal();
  };

  openModal();
}

// ── Image attachments — base64 in Firestore ──
function isAllowedImageFile(file) {
  const ext = (file.name.split('.').pop() || '').toLowerCase();
  return ALLOWED_IMAGE_TYPES.includes(file.type) || ALLOWED_IMAGE_EXT.includes(ext);
}

function compressImage(file) {
  return new Promise((resolve, reject) => {
    // SVGs don't need canvas compression
    if (file.type === 'image/svg+xml') {
      const reader = new FileReader();
      reader.onload = e => resolve({ url: e.target.result, name: file.name });
      reader.onerror = reject;
      reader.readAsDataURL(file);
      return;
    }
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      let { width, height } = img;
      if (width > MAX_IMG_PX || height > MAX_IMG_PX) {
        if (width >= height) { height = Math.round(height * MAX_IMG_PX / width); width = MAX_IMG_PX; }
        else                 { width = Math.round(width * MAX_IMG_PX / height); height = MAX_IMG_PX; }
      }
      const canvas = document.createElement('canvas');
      canvas.width = width; canvas.height = height;
      canvas.getContext('2d').drawImage(img, 0, 0, width, height);
      const dataUrl = canvas.toDataURL('image/jpeg', IMG_QUALITY);
      const kb = Math.round(dataUrl.length * 0.75 / 1024);
      if (kb > 900) { reject(new Error(`Слишком большое изображение после сжатия (${kb} KB). Уменьши файл.`)); return; }
      resolve({ url: dataUrl, name: file.name });
    };
    img.onerror = reject;
    img.src = url;
  });
}

function setupImageAttachments(taskId, getImages, setImages) {
  const grid  = document.getElementById('image-grid');
  const zone  = document.getElementById('image-drop-zone');
  const input = document.getElementById('f-image-input');
  if (!grid || !zone || !input) return;

  function paint() {
    const images = getImages();
    grid.innerHTML = images.map((img, i) => `
      <div class="image-thumb" data-idx="${i}" title="${esc(img.name || '')}">
        <img src="${img.url}" alt="${esc(img.name || '')}" data-idx="${i}">
        <button class="image-remove" type="button" title="Удалить" data-idx="${i}">✕</button>
      </div>`).join('');

    grid.querySelectorAll('.image-thumb img').forEach(el => {
      el.addEventListener('click', () => openLightbox(el.src, el.alt));
    });
    grid.querySelectorAll('.image-remove').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const images = getImages();
        images.splice(Number(btn.dataset.idx), 1);
        setImages(images);
        paint();
      });
    });
  }
  paint();

  async function processFiles(files) {
    for (const file of Array.from(files)) {
      if (!isAllowedImageFile(file)) {
        alert(`Неподдерживаемый формат: ${file.name}`);
        continue;
      }
      try {
        const img = await compressImage(file);
        const images = getImages();
        images.push(img);
        setImages(images);
        paint();
      } catch (err) {
        alert(err.message || 'Ошибка обработки изображения');
      }
    }
  }

  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { processFiles(input.files); input.value = ''; });
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over'); processFiles(e.dataTransfer.files); });
  document.getElementById('modal-body').addEventListener('paste', e => {
    const files = Array.from(e.clipboardData?.items || []).filter(it => it.kind === 'file').map(it => it.getAsFile()).filter(Boolean);
    if (files.length) processFiles(files);
  });
}

// ── Lightbox ──
function openLightbox(src, alt) {
  const lb = document.createElement('div');
  lb.id = 'lightbox';
  lb.innerHTML = `<div class="lb-backdrop"></div><div class="lb-content"><img src="${src}" alt="${esc(alt)}"><div class="lb-close">✕</div></div>`;
  document.body.appendChild(lb);
  lb.querySelector('.lb-backdrop').addEventListener('click', () => lb.remove());
  lb.querySelector('.lb-close').addEventListener('click', () => lb.remove());
  document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { lb.remove(); document.removeEventListener('keydown', esc); } });
}

// ── PROJECTS ──────────────────────────────
let projectFilter = 'all';

async function renderProjects() {
  const el = document.getElementById('page-projects');
  el.innerHTML = `
    <div class="page-header">
      <h2 class="page-title">📁 Проекты</h2>
      <button class="btn btn-primary" id="btn-add-project">+ Проект</button>
    </div>
    <div class="filters" id="proj-filters">
      ${['all', 'active', 'paused', 'done'].map(f =>
        `<button class="filter-btn ${f === 'all' ? 'active' : ''}" data-filter="${f}">
          ${{ all: 'Все', active: 'Активные', paused: 'На паузе', done: 'Завершены' }[f]}
        </button>`).join('')}
    </div>
    <div class="cards-grid" id="projects-grid"><div class="spinner">Загрузка...</div></div>`;

  document.getElementById('btn-add-project').addEventListener('click', () => openItemModal('projects', null));
  document.getElementById('proj-filters').addEventListener('click', e => {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    document.querySelectorAll('#proj-filters .filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    projectFilter = btn.dataset.filter;
    loadProjects();
  });
  loadProjects();
}

async function loadProjects() {
  const grid = document.getElementById('projects-grid');
  if (!grid) return;
  const snap = await db.collection('projects').orderBy('updatedAt', 'desc').get();
  let items = snap.docs.map(d => ({ id: d.id, ...d.data() }));
  if (projectFilter !== 'all') items = items.filter(p => p.status === projectFilter);
  grid.innerHTML = items.length === 0
    ? '<p style="color:var(--text-dim)">Нет проектов. Добавь или запусти sync-скрипт.</p>'
    : items.map(p => `
        <div class="item-card" onclick="openItemModal('projects', ${JSON.stringify(p).replace(/"/g, '&quot;')})">
          <div class="item-card-header">
            <div class="item-card-title">${esc(p.name)}</div>
            <span class="badge ${statusBadge(p.status)}">${p.status || 'без статуса'}</span>
          </div>
          <div class="item-card-desc">${esc((p.description || '').slice(0, 120))}</div>
          <div class="item-card-footer">
            ${(p.tags || []).map(t => `<span class="badge badge-blue">${esc(t)}</span>`).join('')}
          </div>
        </div>`).join('');
}

// ── PLANS ─────────────────────────────────
async function renderPlans() {
  const el = document.getElementById('page-plans');
  el.innerHTML = `
    <div class="page-header">
      <h2 class="page-title">🗺️ Планы</h2>
      <button class="btn btn-primary" id="btn-add-plan">+ План</button>
    </div>
    <div class="filters" id="plan-filters">
      ${['all', 'today', 'week', 'month', 'quarter'].map(f =>
        `<button class="filter-btn ${f === 'all' ? 'active' : ''}" data-filter="${f}">
          ${{ all: 'Все', today: 'Сегодня', week: 'Неделя', month: 'Месяц', quarter: 'Квартал' }[f]}
        </button>`).join('')}
    </div>
    <div class="cards-grid" id="plans-grid"><div class="spinner">Загрузка...</div></div>`;

  document.getElementById('btn-add-plan').addEventListener('click', () => openItemModal('plans', null));
  document.getElementById('plan-filters').addEventListener('click', e => {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    document.querySelectorAll('#plan-filters .filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadPlans(btn.dataset.filter);
  });
  loadPlans('all');
}

async function loadPlans(horizon = 'all') {
  const grid = document.getElementById('plans-grid');
  if (!grid) return;
  const snap = await db.collection('plans').orderBy('updatedAt', 'desc').get();
  let items = snap.docs.map(d => ({ id: d.id, ...d.data() }));
  if (horizon !== 'all') items = items.filter(p => p.horizon === horizon);
  grid.innerHTML = items.length === 0
    ? '<p style="color:var(--text-dim)">Нет планов. Добавь или запусти sync-скрипт.</p>'
    : items.map(p => `
        <div class="item-card" onclick="openItemModal('plans', ${JSON.stringify(p).replace(/"/g, '&quot;')})">
          <div class="item-card-header">
            <div class="item-card-title">${esc(p.name)}</div>
            <div style="display:flex;gap:6px">
              ${p.horizon ? `<span class="badge badge-purple">${p.horizon}</span>` : ''}
              <span class="badge ${statusBadge(p.status)}">${p.status || '—'}</span>
            </div>
          </div>
          <div class="item-card-desc">${esc((p.content || '').slice(0, 140).replace(/[#*]/g, ''))}</div>
        </div>`).join('');
}

// ── SKILLS ────────────────────────────────
async function renderSkills() {
  const el = document.getElementById('page-skills');
  el.innerHTML = `
    <div class="page-header">
      <h2 class="page-title">⚙️ Скиллы</h2>
      <button class="btn btn-primary" id="btn-add-skill">+ Скилл</button>
    </div>
    <div class="cards-grid" id="skills-grid"><div class="spinner">Загрузка...</div></div>`;

  document.getElementById('btn-add-skill').addEventListener('click', () => openItemModal('skills', null));
  const snap = await db.collection('skills').orderBy('category').get();
  const items = snap.docs.map(d => ({ id: d.id, ...d.data() }));
  const grid  = document.getElementById('skills-grid');
  if (!grid) return;
  grid.innerHTML = items.length === 0
    ? '<p style="color:var(--text-dim)">Нет скиллов. Добавь или запусти sync-скрипт.</p>'
    : items.map(s => `
        <div class="item-card" onclick="openItemModal('skills', ${JSON.stringify(s).replace(/"/g, '&quot;')})">
          <div class="item-card-header">
            <div class="item-card-title">${esc(s.name)}</div>
            ${s.category ? `<span class="badge badge-purple">${esc(s.category)}</span>` : ''}
          </div>
          <div class="item-card-desc">${esc((s.content || '').slice(0, 120).replace(/[#*]/g, ''))}</div>
        </div>`).join('');
}

// ── Generic item modal (Projects / Plans / Skills) ──
function openItemModal(collection, item) {
  const isNew = !item;
  const labels = { projects: 'Проект', plans: 'План', skills: 'Скилл' };
  document.getElementById('modal-title').textContent = (isNew ? 'Новый ' : 'Редактировать ') + labels[collection];
  const deleteBtn = document.getElementById('modal-delete');
  deleteBtn.classList.toggle('hidden', isNew);

  const extraFields = {
    projects: `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="form-group">
          <label class="form-label">Статус</label>
          <select id="f-status" class="form-select">
            ${['active', 'paused', 'done'].map(s => `<option value="${s}" ${item?.status === s ? 'selected' : ''}>${s}</option>`).join('')}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Теги</label>
          <input id="f-tags" class="form-input" value="${esc((item?.tags || []).join(', '))}">
        </div>
      </div>`,
    plans: `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="form-group">
          <label class="form-label">Горизонт</label>
          <select id="f-horizon" class="form-select">
            ${['today', 'week', 'month', 'quarter'].map(h => `<option value="${h}" ${item?.horizon === h ? 'selected' : ''}>${h}</option>`).join('')}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Статус</label>
          <select id="f-status" class="form-select">
            ${['active', 'done'].map(s => `<option value="${s}" ${item?.status === s ? 'selected' : ''}>${s}</option>`).join('')}
          </select>
        </div>
      </div>`,
    skills: `
      <div class="form-group">
        <label class="form-label">Категория</label>
        <input id="f-category" class="form-input" value="${esc(item?.category || '')}" placeholder="python, shell, api...">
      </div>`,
  }[collection];

  document.getElementById('modal-body').innerHTML = `
    <div class="form-group">
      <label class="form-label">Название *</label>
      <input id="f-name" class="form-input" value="${esc(item?.name || '')}">
    </div>
    ${collection !== 'skills' ? `
    <div class="form-group">
      <label class="form-label">Описание</label>
      <input id="f-description" class="form-input" value="${esc(item?.description || '')}">
    </div>` : ''}
    ${extraFields}
    <div class="form-group">
      <label class="form-label">Содержимое (Markdown)</label>
      <textarea id="f-content" class="form-textarea" style="min-height:180px;font-family:'JetBrains Mono',monospace;font-size:12px">${esc(item?.content || '')}</textarea>
    </div>`;

  document.getElementById('modal-body').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      document.getElementById('modal-save').click();
    }
  });

  document.getElementById('modal-save').onclick = async () => {
    const name = document.getElementById('f-name').value.trim();
    if (!name) return;
    const data = {
      name,
      content:   document.getElementById('f-content').value,
      updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
    };
    if (collection !== 'skills') data.description = document.getElementById('f-description')?.value || '';
    if (collection === 'projects') {
      data.status = document.getElementById('f-status').value;
      data.tags   = document.getElementById('f-tags').value.split(',').map(s => s.trim()).filter(Boolean);
    }
    if (collection === 'plans') {
      data.horizon = document.getElementById('f-horizon').value;
      data.status  = document.getElementById('f-status').value;
    }
    if (collection === 'skills') {
      data.category = document.getElementById('f-category').value.trim();
    }
    if (isNew) data.createdAt = firebase.firestore.FieldValue.serverTimestamp();
    if (isNew) await db.collection(collection).add(data);
    else       await db.collection(collection).doc(item.id).update(data);
    closeModal();
    PAGES[currentPage]?.();
  };

  deleteBtn.onclick = async () => {
    if (!confirm('Удалить?')) return;
    await db.collection(collection).doc(item.id).delete();
    closeModal();
    PAGES[currentPage]?.();
  };

  openModal();
}

// ─────────────────────────────────────────
// Modal helpers
// ─────────────────────────────────────────
function openModal()  { document.getElementById('modal-overlay').classList.remove('hidden'); }
function closeModal() { modalTaskId = null; document.getElementById('modal-overlay').classList.add('hidden'); }
document.getElementById('modal-close').addEventListener('click', closeModal);
document.getElementById('modal-cancel').addEventListener('click', closeModal);
document.getElementById('modal-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('modal-overlay')) closeModal();
});

// ─────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function statusBadge(s) {
  return { active: 'badge-green', done: 'badge-gray', paused: 'badge-yellow' }[s] || 'badge-gray';
}

// expose for inline handlers
window.dropCard      = dropCard;
window.openItemModal = openItemModal;
window.openTaskModal = openTaskModal;
