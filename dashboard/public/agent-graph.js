/* Карта агентов — модуль отрисовки.
   mountAgentGraph(root, G, { footer }) рисует граф внутри root.
   Нужен d3 v7 (глобальный d3). Данные G собирает build.py. */
(function () {
const COLOR = { center:'#dfe0e2', job:'#7f9dbf', calls:'#a98bb8', git:'#8aab8f',
                kiwi:'#6fb0b3', solo:'#b89b7f', learn:'#b88a9a', infra:'#9a9da3', la:'#3fb8ac' };
const ANGLE = { job:180, kiwi:255, infra:325, solo:35, learn:105, git:345, calls:38, la:142 };
const KIND_RU = { center:'центр', branch:'направление', agent:'агент', skill:'навык',
                  task:'задача по расписанию', script:'скрипт', doc:'документ', sched:'планировщик' };
const STATUS_RU = { active:['работает','ok'], dead:['не запускается','dead'], legacy:['старое поколение','old'],
                    stub:['заглушка','old'], idle:['не запущен','old'], archived:['в архиве','old'], off:['выключена','old'],
                    missing:['файла нет','dead'] };
const OLDISH = new Set(['legacy','stub','archived','off','idle']);
const LVL = { red:0, amber:1, grey:2 };
const STORE_KEY = 'agent-graph-filters';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c]));

const MARKUP = `
<div class="ag-in">
  <header class="ag-bar">
    <h1>Карта агентов</h1>
    <span class="ag-gen" data-r="gen"></span>
    <div class="ag-stats" data-r="stats"></div>
    <span class="ag-spacer"></span>
    <div class="ag-tools">
      <input class="ag-search" data-r="q" id="ag-q" type="search" placeholder="Найти: Малина, cron, ledger…" aria-label="Поиск по карте">
      <button class="ag-tog" data-t="old" aria-pressed="true">Выключенное</button>
      <button class="ag-tog" data-t="scripts" aria-pressed="true">Скрипты</button>
      <button class="ag-tog" data-t="docs" aria-pressed="false">Документы</button>
      <button class="ag-tog" data-r="fit" aria-pressed="false">Вписать</button>
    </div>
  </header>
  <div class="ag-main">
    <div class="ag-stage" data-r="stage">
      <canvas data-r="cv" aria-label="Граф связей агентов"></canvas>
      <div class="ag-legend" data-r="legend"></div>
      <div class="ag-hint">колесо — масштаб · тянуть фон — сдвиг<br>тянуть точку — переставить · клик — подробности</div>
    </div>
    <aside class="ag-panel" data-r="panel"></aside>
  </div>
</div>`;

window.mountAgentGraph = function (root, G, opts = {}) {
  if (root._agDestroy) root._agDestroy();
  root.classList.add('ag');
  root.innerHTML = MARKUP;
  const $ = r => root.querySelector(`[data-r="${r}"]`);
  const css = getComputedStyle(root);
  const V = name => css.getPropertyValue(name).trim();
  const C = { bg:V('--ag-bg'), strip:V('--ag-strip'), text:V('--ag-text'), muted:V('--ag-muted'),
              dim:V('--ag-dim'), selText:V('--ag-sel-text'), red:V('--ag-red'), amber:V('--ag-amber') };
  const FONT = V('--ag-sans') || 'system-ui, sans-serif';
  const footer = opts.footer ??
    'Карта не обновляется сама. После изменений в агентах пересобрать:<br><code>python3 /path/to/workspace/ClaudeAIAgent/agent-graph/build.py</code>';

  const byId = new Map(G.nodes.map(n => [n.id, n]));
  const col = n => COLOR[n.branch] || C.muted;
  const deg = new Map(G.nodes.map(n => [n.id, 0]));
  G.links.forEach(l => { if (l.kind !== 'belongs') { deg.set(l.source, deg.get(l.source)+1); deg.set(l.target, deg.get(l.target)+1); } });
  const radius = n => {
    if (n.kind === 'center') return 15;
    if (n.kind === 'branch') return 10;
    return Math.min(n.kind === 'script' || n.kind === 'doc' ? 7 : 11, 3.4 + Math.sqrt(deg.get(n.id)) * 0.95);
  };
  const worst = n => { let w = null; for (const i of n.issues) if (w === null || LVL[i.level] < LVL[w]) w = i.level; return w; };
  const neighbors = new Map(G.nodes.map(n => [n.id, new Map()]));
  G.links.forEach(l => { neighbors.get(l.source).set(l.target, l); neighbors.get(l.target).set(l.source, l); });

  // Детерминированный разброс — картинка одна и та же при каждом открытии
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const target = n => {
    if (n.kind === 'center') return { x:0, y:0 };
    const a = (ANGLE[n.branch] ?? 0) * Math.PI / 180;
    const R = n.kind === 'branch' ? 210 : (n.branch === 'job' ? 420 : 320);
    return { x: Math.cos(a) * R, y: Math.sin(a) * R };
  };
  const simNodes = G.nodes.map(n => { const t = target(n); return Object.assign(n, { x: t.x + (rnd()-.5)*120, y: t.y + (rnd()-.5)*120 }); });
  byId.get('center').fx = 0; byId.get('center').fy = 0;

  const S = { old:true, scripts:true, docs:false, hover:null, sel:null, query:'' };
  try { const s = JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); if (s) Object.assign(S, { old:s.old, scripts:s.scripts, docs:s.docs }); } catch (e) {}
  const visible = n => !((!S.old && OLDISH.has(n.status)) || (!S.scripts && n.kind === 'script') || (!S.docs && n.kind === 'doc'));

  // ── симуляция ──
  let sim, vNodes = [], vLinks = [];
  function rebuild(heat, warm) {
    vNodes = simNodes.filter(visible);
    const ids = new Set(vNodes.map(n => n.id));
    vLinks = G.links.filter(l => ids.has(l.source) && ids.has(l.target)).map(l => ({ ...l }));
    if (sim) sim.stop();
    sim = d3.forceSimulation(vNodes)
      .force('link', d3.forceLink(vLinks).id(d => d.id)
        .distance(l => l.kind === 'belongs' ? 70 : 55)
        .strength(l => {
          if (l.kind === 'trunk') return 0;
          if (l.kind === 'belongs') return 0.07;
          return l.source.branch === l.target.branch ? 0.05 + 0.05 * Math.min(1, Math.log2(1 + l.w) / 3) : 0.006;
        }))
      .force('charge', d3.forceManyBody().distanceMax(420)
        .strength(d => d.kind === 'center' ? -600 : d.kind === 'branch' ? -320 : -55))
      .force('x', d3.forceX(d => target(d).x).strength(d => d.kind === 'branch' ? .5 : .075))
      .force('y', d3.forceY(d => target(d).y).strength(d => d.kind === 'branch' ? .5 : .075))
      .force('collide', d3.forceCollide(d => radius(d) + 4))
      .alpha(heat).alphaDecay(0.03)
      .on('tick', draw);
    if (warm) { sim.stop(); for (let i = 0; i < warm; i++) sim.tick(); sim.alpha(0.05).restart(); }
  }

  // ── холст ──
  const cv = $('cv'), ctx = cv.getContext('2d'), stage = $('stage');
  let W = 0, H = 0, T = d3.zoomIdentity;
  function resize() {
    const r = stage.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    W = r.width; H = r.height;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }
  const scaleR = () => Math.max(.7, Math.min(1.6, Math.sqrt(T.k)));
  function focusSet() {
    const f = S.hover || S.sel;
    if (!f) return null;
    const s = new Set([f.id]);
    neighbors.get(f.id).forEach((l, id) => s.add(id));
    return s;
  }
  function matches(n) {
    if (!S.query) return false;
    const q = S.query.toLowerCase();
    return n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q) || (n.sub || '').toLowerCase().includes(q);
  }
  function shape(n, x, y, r) {
    ctx.beginPath();
    if (n.kind === 'script') { const s = r * .9; ctx.rect(x - s, y - s, s * 2, s * 2); }
    else if (n.kind === 'doc') { ctx.moveTo(x, y - r*1.2); ctx.lineTo(x + r*1.2, y); ctx.lineTo(x, y + r*1.2); ctx.lineTo(x - r*1.2, y); ctx.closePath(); }
    else ctx.arc(x, y, r, 0, Math.PI * 2);
  }

  function draw() {
    if (!W) return;
    ctx.clearRect(0, 0, W, H);
    const F = focusSet(), q = !!S.query, f = S.hover || S.sel, k = T.k, sr = scaleR();

    for (const l of vLinks) {
      const a = l.source, b = l.target;
      const on = F && (a.id === f.id || b.id === f.id);
      let alpha, w;
      if (l.kind === 'trunk') { alpha = .28; w = 1.6; }
      else if (l.kind === 'belongs') { alpha = .07; w = .8; }
      else { alpha = .13; w = .6 + Math.min(1.6, Math.log2(1 + l.w) * .35); }
      if (F) alpha = on ? .85 : alpha * .25;
      else if (q) alpha *= .4;
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = on ? col(f.id === a.id ? b : a) : C.text;
      ctx.lineWidth = on ? Math.max(w, 1.2) : w;
      ctx.beginPath();
      ctx.moveTo(T.applyX(a.x), T.applyY(a.y));
      ctx.lineTo(T.applyX(b.x), T.applyY(b.y));
      ctx.stroke();
    }

    for (const n of vNodes) {
      const x = T.applyX(n.x), y = T.applyY(n.y), r = radius(n) * sr;
      let alpha = 1;
      if (F && !F.has(n.id)) alpha = .16;
      else if (!F && q && !matches(n) && n.kind !== 'center') alpha = .2;
      ctx.globalAlpha = alpha;
      const c = col(n);
      shape(n, x, y, r);
      if (OLDISH.has(n.status)) { ctx.fillStyle = C.strip; ctx.fill(); ctx.lineWidth = 1.3; ctx.strokeStyle = c; ctx.globalAlpha = alpha * .7; ctx.stroke(); ctx.globalAlpha = alpha; }
      else { ctx.fillStyle = c; ctx.fill(); }
      const w = worst(n);
      if (w === 'red' || w === 'amber') {
        ctx.beginPath(); ctx.arc(x, y, r + 3.2, 0, Math.PI * 2);
        ctx.lineWidth = 1.8; ctx.strokeStyle = w === 'red' ? C.red : C.amber; ctx.stroke();
      }
      if (S.sel && S.sel.id === n.id) {
        ctx.beginPath(); ctx.arc(x, y, r + 7, 0, Math.PI * 2);
        ctx.lineWidth = 1.2; ctx.strokeStyle = C.selText; ctx.stroke();
      }
    }

    // подписи — в экранных координатах, размер не зависит от масштаба
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (const n of vNodes) {
      const important = n.kind === 'agent' || n.kind === 'skill' || n.kind === 'task';
      const inFocus = F && F.has(n.id);
      const show = n.kind === 'center' || n.kind === 'branch' || inFocus || (q && matches(n))
                || k >= 1.5 || (k >= .85 && important && !OLDISH.has(n.status)) || (k >= 1.15 && important);
      if (!show) continue;
      if (F && !inFocus && n.kind !== 'branch' && n.kind !== 'center') continue;
      const x = T.applyX(n.x), y = T.applyY(n.y), r = radius(n) * sr;
      let size = 11.5, weight = 400, fill = C.muted;
      if (n.kind === 'center') { size = 15; weight = 600; fill = C.text; }
      else if (n.kind === 'branch') { size = 13; weight = 600; fill = col(n); }
      else if (inFocus || (q && matches(n))) fill = C.text;
      if (OLDISH.has(n.status) && !inFocus) fill = C.dim;
      ctx.globalAlpha = F && !inFocus ? .35 : 1;
      ctx.font = `${weight} ${size}px ${FONT}`;
      const yy = y + r + (worst(n) === 'red' || worst(n) === 'amber' ? 6 : 4);
      ctx.lineWidth = 3; ctx.strokeStyle = C.bg; ctx.strokeText(n.label, x, yy);
      ctx.fillStyle = fill; ctx.fillText(n.label, x, yy);
    }
    ctx.globalAlpha = 1;
  }

  // ── взаимодействие ──
  function nodeAt(px, py) {
    const [x, y] = T.invert([px, py]);
    let best = null, bd = Infinity;
    for (const n of vNodes) {
      const d = Math.hypot(n.x - x, n.y - y), r = (radius(n) * scaleR() + 5) / T.k;
      if (d < r && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  const zoom = d3.zoom().scaleExtent([.2, 5]).on('zoom', e => { T = e.transform; draw(); });
  const drag = d3.drag()
    .subject(e => { const n = nodeAt(e.x, e.y); return n ? { n, x: T.applyX(n.x), y: T.applyY(n.y) } : null; })
    .on('start', e => { if (!e.active) sim.alphaTarget(.2).restart(); const n = e.subject.n; n.fx = n.x; n.fy = n.y; })
    .on('drag', e => { const n = e.subject.n, p = T.invert([e.x, e.y]); n.fx = p[0]; n.fy = p[1]; })
    .on('end', e => { if (!e.active) sim.alphaTarget(0); const n = e.subject.n; if (n.id !== 'center') { n.fx = null; n.fy = null; } });
  d3.select(cv).call(drag).call(zoom).on('dblclick.zoom', null);
  let userMoved = false;
  const pos = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  cv.addEventListener('pointermove', e => {
    const n = nodeAt(...pos(e));
    if (n !== S.hover) { S.hover = n; cv.classList.toggle('on-node', !!n); draw(); }
  });
  cv.addEventListener('pointerleave', () => { if (S.hover) { S.hover = null; draw(); } });
  cv.addEventListener('pointerdown', () => { userMoved = true; });
  cv.addEventListener('wheel', () => { userMoved = true; }, { passive: true });
  cv.addEventListener('click', e => select(nodeAt(...pos(e))));
  cv.addEventListener('dblclick', () => fit(true));
  const onKey = e => { if (e.key === 'Escape' && root.isConnected) select(null); };
  document.addEventListener('keydown', onKey);

  function fit(anim) {
    if (!vNodes.length || !W) return;
    const xs = vNodes.map(n => n.x), ys = vNodes.map(n => n.y);
    const x0 = Math.min(...xs) - 40, x1 = Math.max(...xs) + 40, y0 = Math.min(...ys) - 40, y1 = Math.max(...ys) + 50;
    const k = Math.min(W / (x1 - x0), H / (y1 - y0), 2);
    const t = d3.zoomIdentity.translate(W / 2 - k * (x0 + x1) / 2, H / 2 - k * (y0 + y1) / 2).scale(k);
    (anim ? d3.select(cv).transition().duration(450) : d3.select(cv)).call(zoom.transform, t);
  }
  function centerOn(n) {
    const k = Math.max(T.k, 1.2);
    d3.select(cv).transition().duration(450)
      .call(zoom.transform, d3.zoomIdentity.translate(W / 2 - k * n.x, H / 2 - k * n.y).scale(k));
  }

  // ── панель ──
  const panel = $('panel');
  const branchName = b => b === 'center' ? 'центр' : (G.branches[b]?.label || b);
  function ensureVisible(n) {
    if (visible(n)) return;
    if (OLDISH.has(n.status)) S.old = true;
    if (n.kind === 'script') S.scripts = true;
    if (n.kind === 'doc') S.docs = true;
    syncToggles(); rebuild(.3, 120);
  }
  function select(n, move) {
    S.sel = n;
    if (n) { ensureVisible(n); if (move) centerOn(n); }
    renderPanel(); draw();
  }
  function renderOverview() {
    const all = [];
    G.nodes.forEach(n => n.issues.forEach(i => all.push({ n, ...i })));
    const groups = [
      ['red', 'Не работает'], ['amber', 'Разъехалось'], ['grey', 'Уборка'],
    ];
    let html = `<div><h2>Что доработать</h2><p class="ag-lead">Собрано автоматически по файлам, расписанию и логам. Клик — точка на карте и подробности.</p></div>`;
    for (const [lvl, title] of groups) {
      const items = all.filter(i => i.level === lvl);
      if (!items.length) continue;
      html += `<section class="ag-group"><h3><span class="ag-dot ${lvl}"></span>${title} · ${items.length}</h3>` +
        items.map(i => `<button class="ag-item" data-id="${esc(i.n.id)}"><span class="ag-dot" style="background:${col(i.n)}"></span>
          <span><span class="ag-who">${esc(i.n.label)}</span> <span style="color:var(--ag-dim)">· ${esc(branchName(i.n.branch))}</span>
          <span class="ag-what">${esc(i.text)}</span></span></button>`).join('') + `</section>`;
    }
    html += `<p class="ag-foot">${footer}</p>`;
    panel.innerHTML = html;
  }
  function renderNode(n) {
    const st = STATUS_RU[n.status] || [n.status, ''];
    const facts = [];
    if (n.path) facts.push(['Файл', `<span class="ag-path">${esc(n.path)}</span><button class="ag-copy" data-copy="${esc(n.path)}">копировать</button>`]);
    if (n.model) facts.push(['Модель', esc(n.model)]);
    if (n.kind === 'agent') facts.push(['Effort', n.effort ? esc(n.effort) : '<span style="color:var(--ag-amber)">не задан</span>']);
    if (n.size) facts.push(['Размер', esc(String(n.size).replace('.', ',')) + ' КБ']);
    if (n.schedule) facts.push(['Расписание', esc(n.schedule)]);
    if (n.last_run) facts.push(['Последний запуск', esc(n.last_run)]);
    if (n.mtime) facts.push(['Файл изменён', esc(n.mtime)]);
    const nb = [...neighbors.get(n.id).entries()]
      .map(([id, l]) => ({ m: byId.get(id), l }))
      .filter(x => x.l.kind !== 'belongs' || n.kind === 'branch')
      .sort((a, b) => (b.l.w || 0) - (a.l.w || 0));
    let html = `<button class="ag-back" data-back="1">← Что доработать</button>
      <div><h2>${esc(n.label)}</h2>
      <div class="ag-kind"><span>${esc(KIND_RU[n.kind] || n.kind)} · ${esc(branchName(n.branch))}</span>
      <span class="ag-pill ${st[1]}">${esc(st[0])}</span></div>
      ${n.sub ? `<p class="ag-lead">${esc(n.sub)}</p>` : ''}</div>`;
    if (n.issues.length) html += `<section class="ag-group" style="gap:6px"><h3>Замечания</h3>` +
      n.issues.map(i => `<div class="ag-issue"><span class="ag-dot ${i.level}"></span><span>${esc(i.text)}</span></div>`).join('') + `</section>`;
    if (facts.length) html += `<dl class="ag-facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;
    if (n.desc && n.desc !== n.sub) html += `<p class="ag-desc">${esc(n.desc)}</p>`;
    if (nb.length) html += `<section><h3>Связи · ${nb.length}</h3><div class="ag-nb">` +
      nb.map(({ m, l }) => `<button data-id="${esc(m.id)}"><span class="ag-sw" style="background:${OLDISH.has(m.status) ? 'transparent' : col(m)};border:1px solid ${col(m)}"></span>${esc(m.label)}${l.kind === 'link' && l.w > 1 ? `<span class="ag-w">${l.w}</span>` : ''}</button>`).join('') +
      `</div><p class="ag-foot" style="margin-top:6px">Число — сколько раз файлы упоминают друг друга.</p></section>`;
    panel.innerHTML = html;
  }
  function renderPanel() { S.sel ? renderNode(S.sel) : renderOverview(); panel.scrollTop = 0; }
  panel.addEventListener('click', e => {
    if (e.target.closest('[data-back]')) { select(null); return; }
    const b = e.target.closest('[data-id]');
    if (b) { select(byId.get(b.dataset.id), true); return; }
    const c = e.target.closest('[data-copy]');
    if (c) {
      const fallback = () => { const r = document.createRange(); r.selectNodeContents(c.previousElementSibling); getSelection().removeAllRanges(); getSelection().addRange(r); };
      if (navigator.clipboard) navigator.clipboard.writeText(c.dataset.copy).then(() => { c.textContent = 'скопировано'; }, fallback);
      else fallback();
    }
  });

  // ── верхняя полоса ──
  const agents = G.nodes.filter(n => n.kind === 'agent' || n.kind === 'skill');
  const act = agents.filter(n => n.status === 'active').length;
  const cnt = { red:0, amber:0, grey:0 };
  G.nodes.forEach(n => n.issues.forEach(i => cnt[i.level]++));
  $('gen').textContent = 'собрано ' + G.generated;
  $('stats').innerHTML =
    `<span class="ag-chip">агентов <b>${act}</b> в строю · <b>${agents.length - act}</b> старых</span>
     <span class="ag-chip"><span class="ag-dot red"></span>не работает <b>${cnt.red}</b></span>
     <span class="ag-chip"><span class="ag-dot amber"></span>разъехалось <b>${cnt.amber}</b></span>
     <span class="ag-chip"><span class="ag-dot grey"></span>уборка <b>${cnt.grey}</b></span>`;
  $('legend').innerHTML = Object.entries(G.branches).map(([k, v]) => `<span><i class="ag-sw" style="background:${COLOR[k]}"></i>${esc(v.label)}</span>`).join('') +
    `<span><i class="ag-sw" style="border:1.3px solid ${C.muted}"></i>выключено / не запущено</span>
     <span><i class="ag-sw" style="border:2px solid ${C.red}"></i>не работает</span>
     <span><i class="ag-sw" style="border-radius:1px;background:${C.muted};width:8px;height:8px"></i>скрипт</span>
     <span><i class="ag-sw" style="border:2px solid ${C.amber}"></i>разъехалось</span>
     <span><i class="ag-sw" style="background:${C.muted};transform:rotate(45deg);border-radius:1px;width:7px;height:7px"></i>документ</span>`;

  const togs = [...root.querySelectorAll('[data-t]')];
  function syncToggles() {
    togs.forEach(b => b.setAttribute('aria-pressed', String(S[b.dataset.t])));
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ old:S.old, scripts:S.scripts, docs:S.docs })); } catch (e) {}
  }
  togs.forEach(b => b.addEventListener('click', () => {
    const key = b.dataset.t;
    S[key] = !S[key];
    if (S.sel && !visible(S.sel)) S.sel = null;
    syncToggles(); rebuild(.35, 150); renderPanel(); fit(true);
  }));
  $('fit').addEventListener('click', () => fit(true));
  const qi = $('q');
  qi.addEventListener('input', () => { S.query = qi.value.trim(); draw(); });
  qi.addEventListener('keydown', e => { if (e.key === 'Enter') { const n = simNodes.find(matches); if (n) select(n, true); } });

  // ── старт: раскладка считается заранее, граф открывается уже в покое ──
  syncToggles(); renderPanel();
  rebuild(1, 400);
  const ro = new ResizeObserver(() => { resize(); if (!userMoved) fit(false); });
  ro.observe(stage);
  resize(); fit(false);

  root._agDestroy = () => { sim.stop(); ro.disconnect(); document.removeEventListener('keydown', onKey); };
};
})();
