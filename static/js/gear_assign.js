/* Calendar for assigning bikes to rides. Dates are plain 'YYYY-MM-DD' strings built by hand (never toISOString: that shifts the day across timezones). */
(function () {
  'use strict';
  const G = window.GEAR_ASSIGN, $ = (id) => document.getElementById(id);
  const COLOURS = ['#38bdf8', '#a78bfa', '#34d399', '#f472b6', '#fbbf24', '#60a5fa', '#f87171', '#2dd4bf'];
  const colourOf = Object.fromEntries(G.bikes.map((id, i) => [String(id), COLOURS[i % COLOURS.length]]));
  const nameOf = Object.fromEntries(G.bikes.map((id, i) => [String(id), G.names[i]]));
  const pad = (n) => String(n).padStart(2, '0'), iso = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;
  const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
  const fromDate = (dt) => iso(dt.getFullYear(), dt.getMonth() + 1, dt.getDate());
  const MONTHS = Array.from({ length: 12 }, (_, i) => new Intl.DateTimeFormat('en-GB', { month: 'long' }).format(new Date(2000, i, 1)));
  const rider = G.rider ? `&rider=${G.rider}` : '';

  const t = parse(G.today);
  const start = G.span.first_unassigned || G.span.last || G.today;
  let view = { y: +start.slice(0, 4), m: +start.slice(5, 7) };
  let days = {}, selected = new Set(), lastClicked = null, previewTimer = null, previewSeq = 0;

  // ---- header selects ----
  const firstYear = +(G.span.first || G.today).slice(0, 4), lastYear = Math.max(t.getFullYear(), +(G.span.last || G.today).slice(0, 4));
  MONTHS.forEach((n, i) => $('monthSel').add(new Option(n, i + 1)));
  for (let y = lastYear; y >= Math.min(firstYear, lastYear); y--) $('yearSel').add(new Option(y, y));
  $('legend').innerHTML = G.bikes.map((id) => `<span><i style="background:${colourOf[String(id)]}"></i>${nameOf[String(id)].replace(/</g, '&lt;')}</span>`).join('') + '<span><i style="box-shadow:inset 0 0 0 2px rgba(252,76,2,.8);background:transparent"></i>has rides with no bike</span>';

  // ---- range helpers ----
  function range() {
    const f = $('from').value, to = $('toPresent').checked ? G.today : $('to').value;
    return { from: f || null, to: to || null };
  }
  function inRange(d) { const r = range(); return !!((r.from || r.to) && (!r.from || d >= r.from) && (!r.to || d <= r.to)); }
  function datesBetween(a, b) { if (a > b) [a, b] = [b, a]; const out = [], d = parse(a), end = parse(b); for (; d <= end; d.setDate(d.getDate() + 1)) out.push(fromDate(d)); return out; }

  // ---- calendar ----
  async function load() {
    $('monthSel').value = view.m; $('yearSel').value = view.y;
    const r = await fetch(`/gear/api/calendar?year=${view.y}&month=${view.m}${rider}`); days = (await r.json()).days || {};
    render();
  }
  function render() {
    const first = new Date(view.y, view.m - 1, 1), lead = (first.getDay() + 6) % 7, n = new Date(view.y, view.m, 0).getDate();
    let h = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => `<div class="cal-dow">${d}</div>`).join('');
    for (let i = 0; i < lead; i++) h += '<div class="cal-day empty"></div>';
    let rides = 0, un = 0;
    for (let d = 1; d <= n; d++) {
      const key = iso(view.y, view.m, d), info = days[key], cls = ['cal-day'];
      if (info) { cls.push('has'); rides += info.n; un += info.unassigned; if (info.unassigned) cls.push('need'); }
      if (selected.has(key)) cls.push('sel'); else if (inRange(key)) cls.push('inrange');
      if (key === G.today) cls.push('today');
      let stripes = '';
      if (info) { const total = info.n; stripes = '<div class="stripes">' + Object.entries(info.bikes).map(([b, c]) => `<i style="flex:${c};background:${colourOf[b] || '#888'}" title="${(nameOf[b] || 'bike')}"></i>`).join('') + (info.unassigned ? `<i style="flex:${info.unassigned}"></i>` : '') + '</div>'; }
      h += `<div class="${cls.join(' ')}" data-d="${key}"><span class="n">${d}</span>${info ? `<span class="c">${info.n}</span>` : ''}${stripes}</div>`;
    }
    $('calGrid').innerHTML = h;
    $('monthInfo').textContent = rides ? `${rides} ride${rides === 1 ? '' : 's'}${un ? `, ${un} with no bike` : ''}` : 'no rides this month';
  }
  $('calGrid').addEventListener('click', (e) => {
    const el = e.target.closest('.cal-day[data-d]'); if (!el) return;
    const key = el.dataset.d;
    if (e.shiftKey && lastClicked) datesBetween(lastClicked, key).forEach((d) => selected.add(d));
    else if (selected.has(key)) selected.delete(key); else selected.add(key);
    lastClicked = key; render(); schedulePreview();
  });
  const go = (dy, dm) => { let y = view.y, m = view.m + dm; if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; } view = { y, m }; load(); };
  $('prev').onclick = () => go(0, -1); $('next').onclick = () => go(0, 1);
  $('monthSel').onchange = () => { view.m = +$('monthSel').value; load(); }; $('yearSel').onchange = () => { view.y = +$('yearSel').value; load(); };
  $('goToday').onclick = () => { view = { y: t.getFullYear(), m: t.getMonth() + 1 }; load(); };
  $('goUn').onclick = () => { const s = G.span.first_unassigned || G.span.first || G.today; view = { y: +s.slice(0, 4), m: +s.slice(5, 7) }; load(); };

  // ---- range controls ----
  function setRange(f, to, present) { $('from').value = f || ''; $('toPresent').checked = !!present; $('to').value = present ? '' : (to || ''); $('to').disabled = !!present; selected.clear(); render(); schedulePreview(); }
  $('from').onchange = $('to').onchange = () => { render(); schedulePreview(); };
  $('toPresent').onchange = () => { $('to').disabled = $('toPresent').checked; render(); schedulePreview(); };
  $('qFromDay').onclick = () => { const d = selected.size ? [...selected].sort()[0] : (lastClicked || null); if (!d) { $('result').textContent = 'Click a day on the calendar first.'; return; } setRange(d, null, true); };
  $('qMonth').onclick = () => setRange(iso(view.y, view.m, 1), iso(view.y, view.m, new Date(view.y, view.m, 0).getDate()), false);
  $('qYear').onclick = () => setRange(iso(view.y, 1, 1), iso(view.y, 12, 31), false);
  $('qAllUn').onclick = () => { $('onlyUn').checked = true; setRange(G.span.first_unassigned || G.span.first, null, true); };
  $('qClear').onclick = () => { setRange('', '', false); lastClicked = null; $('result').textContent = ''; };

  // ---- preview + apply ----
  function payload(dry) { const r = range(); return { bike: $('bikeSel').value ? +$('bikeSel').value : null, dates: [...selected], from: r.from, to: r.to, only_unassigned: $('onlyUn').checked, dry_run: dry }; }
  const hasSel = () => selected.size > 0 || !!(range().from || range().to);
  function describe() {
    const r = range(), bits = [];
    if (selected.size) bits.push(`${selected.size} picked day${selected.size === 1 ? '' : 's'}`);
    if (r.from || r.to) bits.push(`${r.from ? r.from : 'the start'} to ${r.to ? r.to : 'the end'}`);
    $('selInfo').textContent = bits.length ? bits.join(' and ') : 'Nothing picked yet.';
  }
  function schedulePreview() { describe(); clearTimeout(previewTimer); $('apply').disabled = true; if (!hasSel()) { $('preview').textContent = 'Pick some days or a range to see what will change.'; return; } previewTimer = setTimeout(preview, 250); }
  async function preview() {
    const seq = ++previewSeq;
    const r = await fetch('/gear/api/assign' + (rider ? '?' + rider.slice(1) : ''), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload(true)) });
    const j = await r.json(); if (seq !== previewSeq) return;
    if (!r.ok) { $('preview').textContent = j.error || 'Could not work that out.'; return; }
    const target = $('bikeSel').value ? nameOf[$('bikeSel').value] : 'no bike';
    const skipped = j.matched - j.changed;
    $('preview').innerHTML = j.matched === 0 ? 'No rides in that selection.' : (j.changed === 0 ? `All ${j.matched} ride${j.matched === 1 ? ' is' : 's are'} already as you want them.` :
      `<strong>${j.changed.toLocaleString()}</strong> ride${j.changed === 1 ? '' : 's'} will be put on <strong>${target.replace(/</g, '&lt;')}</strong>.` + (skipped > 0 ? `<div class="hint">${skipped.toLocaleString()} left as they are.</div>` : ''));
    $('apply').disabled = j.changed === 0; $('apply').dataset.n = j.changed;
  }
  $('bikeSel').onchange = $('onlyUn').onchange = schedulePreview;
  $('apply').onclick = async () => {
    const n = +$('apply').dataset.n || 0, target = $('bikeSel').value ? nameOf[$('bikeSel').value] : 'no bike';
    if (n > 50 && !confirm(`Put ${n.toLocaleString()} rides on ${target}?`)) return;
    $('apply').disabled = true;
    const r = await fetch('/gear/api/assign' + (rider ? '?' + rider.slice(1) : ''), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload(false)) });
    const j = await r.json();
    if (!r.ok) { $('result').textContent = j.error || 'Something went wrong.'; return; }
    $('result').textContent = `Done: ${j.changed.toLocaleString()} ride${j.changed === 1 ? '' : 's'} updated.`;
    const wasNone = (j.previous && j.previous.none) || 0;
    G.span.unassigned = Math.max(0, G.span.unassigned + ($('bikeSel').value ? -wasNone : j.changed - wasNone));
    selected.clear(); lastClicked = null; setRange('', '', false); await load();
  };
  load();
})();
