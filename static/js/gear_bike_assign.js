/* Date-range picker on a bike's page: put every ride in a range on this bike (with a live count first). The full calendar is /gear/assign. */
(function () {
  'use strict';
  const G = window.GEAR_BIKE, $ = (id) => document.getElementById(id);
  if (!G || !$('bApply')) return;
  const pad = (n) => String(n).padStart(2, '0'), iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;   // local date, never toISOString (UTC shift)
  const url = '/gear/api/assign?rider=' + G.rider;
  let timer = null, seq = 0;
  const range = () => ({ from: $('bFrom').value || null, to: $('bPresent').checked ? G.today : ($('bTo').value || null) });
  const payload = (dry) => { const r = range(); return { bike: G.bike, dates: [], from: r.from, to: r.to, only_unassigned: $('bOnlyUn').checked, dry_run: dry }; };
  function schedule() { clearTimeout(timer); $('bApply').disabled = true; $('bResult').textContent = ''; const r = range(); if (!r.from && !r.to) { $('bPreview').textContent = `Choose a start date to see how many rides will move onto ${G.name}.`; return; } timer = setTimeout(preview, 250); }
  async function preview() {
    const my = ++seq, res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload(true)) }), j = await res.json();
    if (my !== seq) return;
    if (!res.ok) { $('bPreview').textContent = j.error || 'Could not work that out.'; return; }
    const left = j.matched - j.changed;
    $('bPreview').innerHTML = j.matched === 0 ? 'No rides in that range.' : j.changed === 0 ? `All ${j.matched} ride${j.matched === 1 ? ' is' : 's are'} already on this bike or left alone.`
      : `<strong>${j.changed.toLocaleString()}</strong> ride${j.changed === 1 ? '' : 's'} will be put on <strong>${G.name.replace(/</g, '&lt;')}</strong>.` + (left > 0 ? `<div class="hint">${left.toLocaleString()} left as they are.</div>` : '');
    $('bApply').disabled = j.changed === 0; $('bApply').dataset.n = j.changed;
  }
  ['bFrom', 'bTo', 'bPresent', 'bOnlyUn'].forEach((id) => { $(id).onchange = () => { if (id === 'bPresent') $('bTo').disabled = $('bPresent').checked; schedule(); }; });
  document.querySelectorAll('[data-q]').forEach((b) => { b.onclick = () => {
    const t = new Date(), q = b.dataset.q;
    if (q === 'year') { $('bFrom').value = `${t.getFullYear()}-01-01`; $('bTo').value = iso(t); $('bPresent').checked = false; }
    if (q === '30') { const f = new Date(t); f.setDate(f.getDate() - 30); $('bFrom').value = iso(f); $('bPresent').checked = true; }
    if (q === 'all') { $('bFrom').value = '2000-01-01'; $('bPresent').checked = true; $('bOnlyUn').checked = true; }
    $('bTo').disabled = $('bPresent').checked; schedule();
  }; });
  $('bApply').onclick = async () => {
    const n = +$('bApply').dataset.n || 0;
    if (n > 50 && !confirm(`Put ${n.toLocaleString()} rides on ${G.name}?`)) return;
    $('bApply').disabled = true;
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload(false)) }), j = await res.json();
    if (!res.ok) { $('bResult').textContent = j.error || 'Something went wrong.'; return; }
    $('bResult').textContent = `Done: ${j.changed.toLocaleString()} ride${j.changed === 1 ? '' : 's'} updated. Refreshing...`;
    setTimeout(() => location.reload(), 700);
  };
})();
