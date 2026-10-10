"""Builds the "Rob" Home Assistant dashboard (nutrition + body + riding) as a Lovelace config dict.

Look: Headwind's "dark ocean" palette on every hero/tile card (so it reads the same in light or dark HA themes),
button-card for the bespoke cards, mini-graph-card for trends, mushroom for section titles.
"""

SC = 'sensor.etekcity_smart_fitness_scale_d0_4d_00_5f_81_c1_esf_551_robert_s_'
N = 'sensor.headwind_nutrition_'
B = 'sensor.bike_tracker_'
MEALS = [('breakfast', 'Breakfast', 'mdi:coffee'), ('lunch', 'Lunch', 'mdi:food'), ('dinner', 'Dinner', 'mdi:silverware-fork-knife'),
         ('snacks', 'Snacks', 'mdi:cookie'), ('ride_fuel', 'Ride fuel', 'mdi:bike-fast'), ('recovery', 'Recovery', 'mdi:blender')]
HEX = {'protein': '#facc15', 'carbs': '#38bdf8', 'fat': '#f87171', 'fibre': '#fb923c', 'kcal': '#fc4c02', 'water': '#60a5fa'}

BG = 'linear-gradient(150deg,rgba(255,255,255,.16) 0%,rgba(255,255,255,.05) 55%,rgba(8,20,34,.35) 100%)'
THEME = 'Frosted Glass Dark'
MUTED = '#8fb1c4'

# JS shared by every button-card: safe state readers + a ring renderer + a tiny stat renderer
PRELUDE = r"""
const fdate = d => new Date(d + 'T12:00:00').toLocaleDateString('en-GB', {day: 'numeric', month: 'short'});
const n = e => parseFloat((states[e] || {}).state) || 0;
const s = e => (states[e] || {}).state;
const fmt = (v, d=0) => Number(v).toLocaleString('en-GB', {maximumFractionDigits: d, minimumFractionDigits: d});
const MUTED = '#8fb1c4';
const ring = (pct, col, big, small, size=84) => `
  <div style="position:relative;width:${size}px;height:${size}px;margin:0 auto">
    <div style="position:absolute;inset:0;border-radius:50%;background:conic-gradient(${col} ${Math.min(100,Math.max(0,pct))}%, rgba(255,255,255,.14) 0);-webkit-mask:radial-gradient(farthest-side, transparent calc(100% - 9px), #000 calc(100% - 8px));mask:radial-gradient(farthest-side, transparent calc(100% - 9px), #000 calc(100% - 8px))"></div>
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1.05">
      <span style="font-size:${Math.round(size*.26)}px;font-weight:800">${big}</span>
      <span style="font-size:10px;color:${MUTED}">${small}</span></div></div>`;
const panel = (title, right, legend, inner) => `
  <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
    <div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${MUTED}">${title}</div>
    <div style="font-size:15px;font-weight:800">${right || ''}</div></div>
  ${legend && legend.length ? `<div style="display:flex;gap:12px;margin:6px 0 0;font-size:11px;color:${MUTED}">${legend.map(l => `<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${l[1]};margin-right:5px"></span>${l[0]}</span>`).join('')}</div>` : ''}
  <div style="margin-top:8px">${inner}</div>`;
const shortDate = d => new Date(d + 'T12:00:00').toLocaleDateString('en-GB', {day: 'numeric', month: 'short'});
const chart = o => {
  const W = 320, H = o.h || 132, pl = 4, pr = 38, pt = 12, pb = 22;
  const n = o.labels.length;
  if (!n) return `<div style="color:${MUTED};font-size:13px;padding:16px 0">No data yet</div>`;
  const vals = [];
  o.series.forEach(s => s.v.forEach(x => { if (x != null && !isNaN(x)) vals.push(+x); }));
  if (o.goal) vals.push(+o.goal);
  (o.refs || []).forEach(r => vals.push(+r.v));
  if (!vals.length) return `<div style="color:${MUTED};font-size:13px;padding:16px 0">No data yet</div>`;
  let mn = Math.min(...vals), mx = Math.max(...vals);
  if (o.zero) mn = 0;
  if (o.diverge) { mn = Math.min(mn, 0); mx = Math.max(mx, 0); }
  if (mn === mx) { mn -= 1; mx += 1; }
  const span = mx - mn;
  if (!o.zero && !o.diverge) mn -= span * .12;
  if (o.diverge) mn -= span * .12;
  mx += span * .10;
  const slot = (W - pl - pr) / n;
  const x = i => pl + slot * (i + .5);
  // xdates: place points by real calendar date (each series may carry its own `xs`), so a 5-day gap looks like 5 days
  const xd = !!o.xdates;
  let t0 = 0, t1 = 0;
  if (xd) {
    const ts = [].concat(...o.series.map(s => (s.xs || o.labels).map(d => Date.parse(d + 'T12:00:00'))));
    t0 = Math.min(...ts); t1 = Math.max(...ts);
  }
  const xt = d => pl + 6 + (t1 === t0 ? (W - pl - pr - 12) / 2 : (Date.parse(d + 'T12:00:00') - t0) / (t1 - t0) * (W - pl - pr - 12));
  const y = v => pt + (1 - (v - mn) / (mx - mn)) * (H - pt - pb);
  const dec = o.dec == null ? 0 : o.dec;
  const gid = 'g' + Math.random().toString(36).slice(2, 8);
  let g = '';
  for (let k = 0; k <= 3; k++) {
    const v = mn + (mx - mn) * k / 3, yy = y(v);
    g += `<line x1="${pl}" y1="${yy}" x2="${W - pr}" y2="${yy}" stroke="rgba(255,255,255,.10)" stroke-dasharray="3 4"/>` +
         `<text x="${W - pr + 5}" y="${yy + 3.5}" font-size="9.5" fill="${MUTED}">${Number(v).toFixed(dec)}</text>`;
  }
  o.series.forEach((s, si) => {
    const X = i => xd ? xt((s.xs || o.labels)[i]) : x(i);
    if (s.type === 'bar') {
      const bw = Math.max(3, slot * .62 * (s.bw || 1));
      const base = o.diverge ? y(0) : (H - pb);
      s.v.forEach((v, i) => {
        if (v == null || isNaN(v)) return;
        const yy = y(v), top = Math.min(yy, base), h = Math.max(1.5, Math.abs(base - yy));
        g += `<rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${h}" rx="3" fill="${(s.colors && s.colors[i]) || s.color}" opacity="${(v !== 0 || o.diverge) ? .92 : .25}"/>`;
      });
      if (o.diverge) g += `<line x1="${pl}" y1="${base}" x2="${W - pr}" y2="${base}" stroke="rgba(255,255,255,.55)" stroke-width="1.2"/>`;
    } else {
      let d = '', area = '', first = null, lastx = null;
      s.v.forEach((v, i) => {
        if (v == null || isNaN(v)) { first = null; return; }
        d += (first === null ? 'M' : 'L') + X(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
        if (first === null) first = i;
        lastx = i;
      });
      if (s.area && d) {
        const i0 = s.v.findIndex(v => v != null && !isNaN(v));
        g += `<defs><linearGradient id="${gid}${si}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity=".38"/><stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient></defs>` +
             `<path d="${d} L${X(lastx).toFixed(1)} ${H - pb} L${X(i0).toFixed(1)} ${H - pb} Z" fill="url(#${gid}${si})"/>`;
      }
      if (d && s.line !== false) g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.w || 2.5}" stroke-linejoin="round" stroke-linecap="round"${s.dash ? ' stroke-dasharray="6 5"' : ''}/>`;
      if (s.dots) s.v.forEach((v, i) => { if (v != null && !isNaN(v)) g += `<circle cx="${X(i)}" cy="${y(v)}" r="${s.r || 3.4}" fill="${(s.dotColors && s.dotColors[i]) || s.color}" stroke="rgba(8,20,34,.7)" stroke-width="1.2"/>`; });
    }
  });
  if (o.goal) {
    const yy = y(o.goal);
    g += `<line x1="${pl}" y1="${yy}" x2="${W - pr}" y2="${yy}" stroke="rgba(255,255,255,.6)" stroke-width="1.2" stroke-dasharray="5 4"/>` +
         `<text x="${pl + 2}" y="${yy - 4}" font-size="9.5" fill="rgba(255,255,255,.75)">goal ${Number(o.goal).toFixed(dec)}</text>`;
  }
  (o.refs || []).forEach(r => {
    const yy = y(r.v);
    g += `<line x1="${pl}" y1="${yy}" x2="${W - pr}" y2="${yy}" stroke="${r.color || 'rgba(255,255,255,.55)'}" stroke-width="1.2" stroke-dasharray="2 4"/>` +
         `<text x="${pl + 2}" y="${yy - 4}" font-size="9.5" fill="${r.color || 'rgba(255,255,255,.75)'}">${r.label}</text>`;
  });
  const lab = (i, a) => `<text x="${a === 'start' ? pl : (a === 'end' ? W - pr : x(i))}" y="${H - 5}" font-size="9.5" fill="${MUTED}" text-anchor="${a}">${shortDate(o.labels[i])}</text>`;
  if (o.yearly) {
    for (let i = 0; i < n; i++) g += `<text x="${x(i)}" y="${H - 5}" font-size="9" fill="${MUTED}" text-anchor="middle">'${o.labels[i].slice(2, 4)}</text>`;
  } else if (xd) {
    const fmtT = t => shortDate(new Date(t).toISOString().slice(0, 10));
    [[t0, 'start'], [(t0 + t1) / 2, 'middle'], [t1, 'end']].forEach(([t, a]) => {
      if (a === 'middle' && (t1 - t0) < 5 * 86400000) return;
      const xx = a === 'start' ? pl : (a === 'end' ? W - pr : pl + 6 + (W - pl - pr - 12) / 2);
      g += `<text x="${xx}" y="${H - 5}" font-size="9.5" fill="${MUTED}" text-anchor="${a}">${fmtT(t)}</text>`;
    });
  } else if (o.monthly) {
    for (let i = 0; i < n; i++) g += `<text x="${x(i)}" y="${H - 5}" font-size="9.5" fill="${MUTED}" text-anchor="middle">${new Date(o.labels[i] + 'T12:00:00').toLocaleDateString('en-GB', {month: 'short'})}</text>`;
  } else {
    g += lab(0, 'start');
    if (n > 2) g += lab(Math.floor(n / 2), 'middle');
    if (n > 1) g += lab(n - 1, 'end');
  }
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;overflow:visible">${g}</svg>`;
};
"""


def js(body):
    return '[[[ ' + PRELUDE + body + ' ]]]'


def bc(entities, body, pad=16, extra_card=None, tap=None):
    card_style = [{'background': BG}, {'border': '1px solid rgba(255,255,255,.22)'}, {'border-radius': '22px'}, {'padding': f'{pad}px'},
                  {'backdrop-filter': 'blur(18px) saturate(150%)'}, {'-webkit-backdrop-filter': 'blur(18px) saturate(150%)'},
                  {'box-shadow': '0 8px 28px rgba(0,0,0,.28), inset 0 1px 0 rgba(255,255,255,.25)'}, {'color': '#f1f7fb'}]
    card_style += extra_card or []
    return {
        'type': 'custom:button-card', 'entity': entities[0], 'triggers_update': entities,
        'show_icon': False, 'show_name': False, 'show_state': False, 'tap_action': tap or {'action': 'more-info'},
        'custom_fields': {'c': js(body)},
        'styles': {'card': card_style,
                   'grid': [{'grid-template-areas': '"c"'}, {'grid-template-columns': '1fr'}],
                   'custom_fields': {'c': [{'width': '100%'}, {'text-align': 'left'}]}},
    }


def cols(card, c):
    card['grid_options'] = {'columns': c, 'rows': 'auto'}
    return card


def heading(text, icon=None):
    c = {'type': 'heading', 'heading': text, 'heading_style': 'title'}
    if icon:
        c['icon'] = icon
    return c


def title(text, sub):
    return {'type': 'custom:mushroom-title-card', 'title': text, 'subtitle': sub}


W_ = N + 'weight_history'
D_ = N + 'diary_history'
R_ = N + 'recovery_history'


def chart_card(entity, body, h=None):
    """Glass card holding an SVG chart; `A` is the history sensor's attribute dict."""
    pre = "const A = ((states['%s'] || {}).attributes) || {}; const last = a => (a && a.length) ? a[a.length - 1] : null; const tail = (a, k) => (a || []).slice(-k);\n" % entity
    return cols(bc([entity], pre + body), 12)


def graph(entities, name, hours, **kw):
    c = {'type': 'custom:mini-graph-card', 'name': name, 'entities': entities, 'hours_to_show': hours,
         'line_width': 3, 'font_size': 70, 'animate': True,
         'show': {'icon': False, 'name': True, 'legend': len(entities) > 1, 'fill': 'fade', 'points': False, 'labels': True, 'extrema': False}}
    c.update(kw)
    return cols(c, 12)


HAVE = set()    # entity ids that exist in this Home Assistant, filled in by the deploy script, so optional cards can be left out when their sensors do not exist


def outlook_cards():
    """The cycling-outlook card needs sensor.cycling_conditions / sensor.cycling_verdict, which are the author's own HA template sensors, not
    part of Headwind. It is only included when they exist, so a standard install is not asked for entities it does not have."""
    return [ride_outlook()] if {'sensor.cycling_conditions', 'sensor.cycling_verdict'} <= HAVE else []


def ride_outlook():
    """Cycling outlook from HA's own sensors: next few hours + tomorrow."""
    return cols(bc(['sensor.cycling_conditions', 'sensor.cycling_verdict'], """
const x = e => states[e] || {state: 'unavailable', attributes: {}};
const col = v => v === 'Good' ? '#4ade80' : v === 'Poor' ? '#f87171' : '#fbbf24';
const ok = o => !['unavailable', 'unknown', ''].includes(o.state);
const now = x('sensor.cycling_conditions'), tom = x('sensor.cycling_verdict');
const block = (label, o) => ok(o)
  ? `<div style="margin-top:10px"><div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font-size:12px;color:${MUTED}">${label}</span><b style="font-size:17px;color:${col(o.state)}">${o.state}</b></div>
     <div style="font-size:13px;line-height:1.4;color:#d6e6ef;margin-top:3px;white-space:normal">${(o.attributes.reason || '').replace(/ Best 2 hours.*$/, '')}</div>${o.attributes.best_window ? `<div style="font-size:12px;color:${MUTED};margin-top:4px">Best window: ${o.attributes.best_window}</div>` : ''}</div>`
  : `<div style="margin-top:10px;font-size:13px;color:${MUTED}">${label}: no forecast available right now</div>`;
return `<div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${MUTED}">Ride outlook</div>${block('Next few hours', now)}${block('Tomorrow', tom)}`;"""), 12)


def map_card(entity, heading_txt, empty_txt, unit_fn):
    """A route over map tiles (Esri dark gray, keyless, same source as Headwind's heatmap) with the segments it crossed on top."""
    return cols(bc([entity], """
const A = ((states['@@E@@'] || {}).attributes) || {};
const pts = A.pts || [];
if (pts.length < 2) return `<div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${MUTED}">@@H@@</div><div style="margin-top:8px;font-size:13px;color:${MUTED}">@@EMPTY@@</div>`;
const W = 340, H = 300, PAD = 28;
const lat = p => p[0], lon = p => p[1];
const mx = (lo, hi, z) => (hi - lo);
const lats = pts.map(lat), lons = pts.map(lon);
const la0 = Math.min(...lats), la1 = Math.max(...lats), lo0 = Math.min(...lons), lo1 = Math.max(...lons);
const wx = (lo, z) => (lo + 180) / 360 * 256 * Math.pow(2, z);
const wy = (la, z) => { const r = la * Math.PI / 180; return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 256 * Math.pow(2, z); };
let z = 16;
while (z > 3 && (wx(lo1, z) - wx(lo0, z) > W - 2 * PAD || wy(la0, z) - wy(la1, z) > H - 2 * PAD)) z--;
const cx = (wx(lo0, z) + wx(lo1, z)) / 2, cy = (wy(la0, z) + wy(la1, z)) / 2;
const left = cx - W / 2, top = cy - H / 2;
const X = lo => (wx(lo, z) - left).toFixed(1), Y = la => (wy(la, z) - top).toFixed(1);
const nt = Math.pow(2, z);
let tiles = '';
for (let tx = Math.floor(left / 256); tx <= Math.floor((left + W) / 256); tx++) for (let ty = Math.floor(top / 256); ty <= Math.floor((top + H) / 256); ty++) {
  if (ty < 0 || ty >= nt) continue;
  const txw = ((tx % nt) + nt) % nt;
  tiles += `<img src="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/${z}/${ty}/${txw}" style="position:absolute;left:${tx * 256 - left}px;top:${ty * 256 - top}px;width:256px;height:256px" loading="lazy" referrerpolicy="no-referrer">`;
}
const path = a => a.map((p, i) => (i ? 'L' : 'M') + X(lon(p)) + ' ' + Y(lat(p))).join(' ');
const segs = (A.segs || []).map(g => `<path d="${path(g.pts)}" fill="none" stroke="${g.pr ? '#fbbf24' : '#f472b6'}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" opacity=".95"/>`).join('');
const a = pts[0], b = pts[pts.length - 1];
const svg = `<svg width="${W}" height="${H}" style="position:absolute;left:0;top:0"><path d="${path(pts)}" fill="none" stroke="rgba(0,0,0,.55)" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="${path(pts)}" fill="none" stroke="#38bdf8" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>${segs}
  <circle cx="${X(lon(a))}" cy="${Y(lat(a))}" r="6" fill="#4ade80" stroke="#fff" stroke-width="2"/><circle cx="${X(lon(b))}" cy="${Y(lat(b))}" r="6" fill="#f87171" stroke="#fff" stroke-width="2"/></svg>`;
const nPR = (A.segs || []).filter(g => g.pr).length;
return `<div style="display:flex;justify-content:space-between;align-items:baseline"><div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${MUTED}">@@H@@</div><div style="font-size:12px;color:${MUTED}">${A.date ? fdate(A.date) : ''}${@@DIST@@}</div></div>
<div style="font-size:17px;font-weight:800;margin:3px 0 10px">${A.name || ''}</div>
<div style="display:flex;justify-content:center"><div style="position:relative;width:${W}px;height:${H}px;max-width:100%;overflow:hidden;border-radius:14px;background:#1b2229">${tiles}${svg}</div></div>
<div style="display:flex;gap:14px;flex-wrap:wrap;justify-content:center;margin-top:9px;font-size:11px;color:${MUTED}"><span><span style="color:#4ade80">●</span> start</span><span><span style="color:#f87171">●</span> finish</span>
${(A.segs || []).length ? `<span><span style="color:#f472b6">━</span> segment</span>${nPR ? '<span><span style="color:#fbbf24">━</span> PR</span>' : ''}` : ''}<span>Map © Esri</span></div>`;""".replace('@@E@@', entity).replace('@@H@@', heading_txt).replace('@@EMPTY@@', empty_txt).replace('@@DIST@@', unit_fn)), 12)


def ride_map():
    return map_card(N + 'ride_route', 'Last ride route', 'No route recorded for the latest ride yet.', "(A.mi ? ' · ' + A.mi + ' mi' : '')")


def run_map():
    return map_card(N + 'run_route', 'Last run route', 'No run recorded yet.', "(A.m ? ' · ' + (A.m / 1609.344).toFixed(1) + ' mi' : '')")



def segments_card(entity=None, run=False):
    """The latest ride's segment efforts (time, gap to your best, rank) + a table of every segment's personal best."""
    return cols(bc([entity or (N + 'ride_segments')], """
const A = ((states['@@E@@'] || {}).attributes) || {};
const E = A.efforts || [], ALL = A.all || [], RUN = @@RUN@@;
const t = v => v == null ? '–' : Math.floor(v / 60) + ':' + String(Math.round(v) % 60).padStart(2, '0');
const sig = v => (v > 0 ? '+' : v < 0 ? '−' : '±') + t(Math.abs(v));
if (!E.length && RUN && !A.id) return '';
if (!E.length) return `<div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${MUTED}">Segments</div><div style="margin-top:8px;font-size:13px;color:${MUTED}">${RUN ? 'No run segments crossed on the latest run. Create one from a run under Workouts in Headwind.' : 'No segments crossed on the latest ride.'}</div>`;
const rows = E.slice().sort((a, b) => ((a.secs - a.best) / a.best) - ((b.secs - b.best) / b.best)).map(e => {
  const gap = e.secs - e.best, pct = e.best ? gap / e.best : 0;
  const tag = e.pr ? '<b style="color:#fbbf24">🏆 PR</b>' : `<span style="color:${pct <= .05 ? '#4ade80' : pct <= .2 ? '#fbbf24' : '#f87171'}">${sig(gap)} vs best</span>`;
  const vs = e.last != null ? (e.secs - e.last < 0 ? `<span style="color:#4ade80">▲ ${t(Math.abs(e.secs - e.last))} faster than last time</span>` : e.secs - e.last > 0 ? `<span style="color:#f87171">▼ ${t(e.secs - e.last)} slower than last time</span>` : 'same as last time') : 'first time';
  return `<div style="padding:8px 0;border-top:1px solid rgba(255,255,255,.08)"><div style="display:flex;justify-content:space-between;gap:8px"><b style="font-size:14px">${e.n}</b><b style="font-size:15px">${t(e.secs)}</b></div>
   <div style="display:flex;justify-content:space-between;gap:8px;font-size:12px;color:${MUTED};margin-top:2px"><span>${(e.m / 1609.344).toFixed(2)} mi${RUN ? ' · ' + t(e.secs / (e.m / 1609.344)) + '/mi' : ''} · #${e.rank} of ${e.tries} · best ${t(e.best)}</span><span>${tag}</span></div>
   <div style="font-size:11.5px;color:${MUTED};margin-top:1px">${vs}</div></div>`; }).join('');
const nPR = E.filter(e => e.pr).length;
return `<div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${MUTED}">${RUN ? 'Run segments on the last run' : 'Segments on the last ride'}</div>
<div style="font-size:13px;color:${MUTED};margin:3px 0 4px">${E.length} crossed${nPR ? ' · <b style="color:#fbbf24">' + nPR + ' PR' + (nPR > 1 ? 's' : '') + '</b>' : ' · no PRs this time'}</div>${rows || ''}
`;""".replace('@@E@@', entity or (N + 'ride_segments')).replace('@@RUN@@', 'true' if run else 'false')), 12)


RUN_JS = """
const R = ((states['@@E@@'] || {}).attributes) || {};
const IMP = (R.units || 'imperial') !== 'metric', U = IMP ? 1609.344 : 1000, DU = IMP ? 'mi' : 'km', PU = IMP ? '/mi' : '/km';
const dist = m => (m / U).toFixed(m / U >= 10 ? 1 : 2);
const dur = sec => { sec = Math.round(sec); const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), x = sec % 60; return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0'); };
const pace = (sec, m) => m > 0 ? dur(sec / (m / U)) : '–';
const tile = (v, l, c) => `<div style="background:rgba(255,255,255,.07);border-radius:14px;padding:10px 6px;text-align:center"><div style="font-size:19px;font-weight:800;color:${c || '#f1f7fb'}">${v}</div><div style="font-size:11px;color:${MUTED};margin-top:2px">${l}</div></div>`;
const head = (t, r) => `<div style="display:flex;justify-content:space-between;align-items:baseline"><div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${MUTED}">${t}</div><div style="font-size:12px;color:${MUTED}">${r || ''}</div></div>`;
const none = `<div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${MUTED}">Running</div><div style="margin-top:8px;font-size:14px;line-height:1.4;white-space:normal">No runs recorded yet. Record one in the Headwind app (Record, then Run) and your best times, pace and totals appear here.</div>`;
if (!R.runs) return none;
"""


def _run_card(entity, body, pad=16):
    return cols(bc([entity], RUN_JS.replace('@@E@@', entity) + body, pad=pad), 12)


def run_hero():
    return _run_card(N + 'run_stats', """
const r = (R.recent || [])[0];
if (!r) return none;
return head('Latest run', fdate(r.date)) + `<div style="font-size:17px;font-weight:800;margin:3px 0 10px">${r.name}</div>
<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:12px"><span style="font-size:46px;font-weight:800;line-height:1;color:#38bdf8">${dist(r.m)}</span><span style="font-size:15px;color:${MUTED}">${DU}</span></div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">${tile(dur(r.s), 'Time')}${tile(pace(r.s, r.m), 'Pace ' + PU, '#34d399')}${tile(r.gain ? Math.round(r.gain * 3.28084).toLocaleString('en-GB') : '–', 'Climb ft', '#fbbf24')}</div>
${r.hr ? `<div style="text-align:center;margin-top:10px;font-size:13px;color:${MUTED}">Average heart rate <b style="color:#f87171">${r.hr} bpm</b></div>` : ''}`;""")


def run_bests():
    return _run_card(N + 'run_stats', """
const B = R.bests || [];
if (!B.length) return head('Best times', '') + `<div style="margin-top:8px;font-size:13px;color:${MUTED}">Best times appear once a run covers 1 km.</div>`;
const today = Date.now();
const rows = B.map(b => { const fresh = (today - new Date(b.date + 'T12:00:00').getTime()) / 864e5 <= 14;
  const gap = b.next ? ` · ${dur(b.next - b.secs)} quicker than next best` : ' · only one run this far so far';
  return `<div style="padding:9px 0;border-top:1px solid rgba(255,255,255,.08)"><div style="display:flex;justify-content:space-between;align-items:baseline"><b style="font-size:14px">${b.label}</b><b style="font-size:19px;color:#38bdf8">${dur(b.secs)}</b></div>
   <div style="display:flex;justify-content:space-between;font-size:12px;color:${MUTED};margin-top:2px"><span>${pace(b.secs, b.m)} ${PU}${gap}</span><span>${fresh ? '🏆 ' : ''}${fdate(b.date)}</span></div></div>`; }).join('');
return head('Best times', 'from your GPS runs') + `<div style="margin-top:6px">${rows}</div><div style="font-size:11px;color:${MUTED};margin-top:8px;white-space:normal">Fastest stretch of that distance inside any one run, so a 10 km run also gives you a 5 km, 3 km and 1 km time.</div>`;""")


def run_totals():
    return _run_card(N + 'run_stats', """
const cell = (t, d) => `<div style="background:rgba(255,255,255,.07);border-radius:14px;padding:10px 12px"><div style="font-size:11px;color:${MUTED}">${t}</div>
  <div style="font-size:20px;font-weight:800;color:#38bdf8">${d && d.n ? dist(d.m) : '0'}<span style="font-size:12px;color:${MUTED};font-weight:600"> ${DU}</span></div>
  <div style="font-size:12px;color:${MUTED}">${d && d.n ? d.n + (d.n === 1 ? ' run' : ' runs') + ' · ' + pace(d.s, d.m) + PU : 'no runs'}</div></div>`;
const L = R.longest;
return head('Totals', '') + `<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">${cell('This week', R.week)}${cell('This month', R.month)}${cell('This year', R.year)}${cell('All time', R.all)}</div>
${L ? `<div style="display:flex;justify-content:space-between;margin-top:12px;font-size:13px"><span style="color:${MUTED}">Longest run</span><b>${dist(L.m)} ${DU} · ${fdate(L.date)}</b></div>` : ''}
${R.fastest ? `<div style="display:flex;justify-content:space-between;margin-top:6px;font-size:13px"><span style="color:${MUTED}">Fastest average pace</span><b>${pace(R.fastest.s, R.fastest.m)} ${PU} · ${fdate(R.fastest.date)}</b></div>` : ''}`;""")


def run_weekly():
    return _run_card(N + 'run_stats', """
const W = R.weeks || [];
const mx = Math.max(U, ...W.map(w => w.m));
const dt = w => new Date(w.d + 'T12:00:00');
const bars = W.map((w, i) => { const mo = dt(w).toLocaleDateString('en-GB', {month: 'short'}); const first = i === 0 || mo !== dt(W[i - 1]).toLocaleDateString('en-GB', {month: 'short'});
  return `<div style="flex:1 1 0;min-width:0;text-align:center"><div style="height:84px;display:flex;align-items:flex-end;justify-content:center"><div style="width:70%;height:${w.m ? Math.max(5, Math.round(w.m / mx * 84)) : 2}px;border-radius:5px 5px 2px 2px;background:${w.m ? (i === W.length - 1 ? '#38bdf8' : '#2b7da6') : 'rgba(255,255,255,.14)'}"></div></div>
  <div style="font-size:10px;color:${MUTED};margin-top:3px">${dt(w).getDate()}</div><div style="font-size:9.5px;color:${MUTED};height:11px">${first ? mo : ''}</div><div style="font-size:10.5px;font-weight:700;height:13px">${w.m ? dist(w.m) : ''}</div></div>`; }).join('');
const tot = W.reduce((a, w) => a + w.m, 0), act = W.filter(w => w.m).length;
return head('Weekly distance (' + DU + ')', 'last 12 weeks') + `<div style="display:flex;gap:2px;margin-top:10px;width:100%">${bars}</div>
<div style="font-size:12px;color:${MUTED};margin-top:8px">${act ? dist(tot / act) + ' ' + DU + ' per week on the weeks you ran · ' + act + ' of 12 weeks active' : 'No runs in the last 12 weeks'}</div>`;""")


def run_recent():
    return _run_card(N + 'run_stats', """
const rows = (R.recent || []).map(r => `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:8px 0;border-top:1px solid rgba(255,255,255,.08)"><div><div style="font-size:14px;font-weight:700">${fdate(r.date)} · ${dist(r.m)} ${DU}</div>
  <div style="font-size:12px;color:${MUTED}">${r.name}${r.hr ? ' · ' + r.hr + ' bpm' : ''}</div></div><div style="text-align:right"><div style="font-size:14px;font-weight:700">${dur(r.s)}</div><div style="font-size:12px;color:#34d399">${pace(r.s, r.m)} ${PU}</div></div></div>`).join('');
return head('Recent runs', '') + `<div style="margin-top:4px">${rows}</div>`;""")


def view_running():
    s1 = {'type': 'grid', 'cards': [title('Running', 'Worked out from your recorded runs'), run_hero(), run_map(), run_bests(), segments_card(N + 'run_segments', run=True)]}
    s2 = {'type': 'grid', 'cards': [heading('Volume', 'mdi:chart-bar'), run_totals(), run_weekly(), run_recent()]}
    return {'title': 'Running', 'path': 'running', 'icon': 'mdi:run-fast', 'type': 'sections', 'max_columns': 3, 'sections': [s1, s2]}


# ───────────────────────────── TODAY ─────────────────────────────
def view_today():
    eaten, rem, pct = N + 'calories_eaten', N + 'calories_remaining', N + 'calorie_progress'
    burned, net = N + 'calories_burned', N + 'calories_net'
    hero = cols(bc([eaten, rem, pct, burned, net, N + 'calories_goal'], f"""
const eaten = n('{eaten}'), rem = n('{rem}'), pct = n('{pct}'), burned = n('{burned}'), net = n('{net}');
const goal = n('{N}calories_goal') || (rem > 0 ? eaten + rem : 0);
const col = pct > 100 ? '#ef4444' : (pct > 85 ? '#f59e0b' : '#fc4c02');
return `
<div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${{MUTED}}">Calories today</div>
<div style="display:flex;align-items:baseline;gap:8px;margin:4px 0 12px">
  <span style="font-size:48px;font-weight:800;line-height:1">${{fmt(eaten)}}</span>
  <span style="font-size:15px;color:${{MUTED}}">of ${{fmt(goal)}} kcal</span></div>
<div style="height:12px;background:rgba(255,255,255,.10);border-radius:8px;overflow:hidden">
  <div style="width:${{Math.min(100, pct)}}%;height:100%;border-radius:8px;background:linear-gradient(90deg,${{col}},#ffb37a)"></div></div>
<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:14px">
  <div><b style="font-size:26px">${{fmt(Math.max(0, rem))}}</b><br><span style="font-size:12px;color:${{MUTED}}">kcal left today</span></div>
  <div style="text-align:right;opacity:.75"><span style="font-size:14px;font-weight:700;color:${{net <= 0 ? '#34d399' : '#f87171'}}">~${{fmt(Math.abs(net), 0).replace(/\d(?=(\d{3})+$)/g, '$&,')}} ${{net <= 0 ? 'deficit' : 'surplus'}}</span><br>
    <span style="font-size:11px;color:${{MUTED}}">rough · full-day estimate</span></div>
</div>`;"""), 12)

    def macro(name, ent, colour, key):
        return cols(bc([ent, N + key + '_goal'], f"""
const val = n('{ent}'), goal = n('{N}{key}_goal');
const pct = goal ? val / goal * 100 : 0;
const over = goal && val > goal;
const sub = !goal ? 'set a goal in Headwind' : (over ? `<span style="color:#f87171">${{fmt(val - goal)}}g over</span>` : `${{fmt(goal - val)}}g to go`);
return ring(pct, over ? '#f87171' : '{colour}', Math.round(val * 10) / 10 % 1 === 0 ? Math.round(val) : val.toFixed(1), goal ? 'of ' + fmt(goal) + 'g' : 'g') +
  `<div style="text-align:center;margin-top:10px;font-size:14px;font-weight:700;color:{colour}">{name}</div>` +
  `<div style="text-align:center;font-size:11px;color:${{MUTED}}">${{sub}}</div>`;""", pad=14), 6)

    macros = [macro('Protein', N + 'protein', HEX['protein'], 'protein'),
              macro('Carbs', N + 'carbs', HEX['carbs'], 'carbs'),
              macro('Fat', N + 'fat', HEX['fat'], 'fat'),
              macro('Fibre', N + 'fibre', HEX['fibre'], 'fibre')]

    water = cols(bc([N + 'water', N + 'water_progress', N + 'water_goal'], f"""
const ml = n('{N}water'), pct = n('{N}water_progress');
const goal = n('{N}water_goal') || 2500;
return `
<div style="display:flex;align-items:center;gap:14px">
  <ha-icon icon="mdi:cup-water" style="--mdc-icon-size:32px;color:#60a5fa"></ha-icon>
  <div style="flex:1"><div style="display:flex;justify-content:space-between"><b style="font-size:17px">${{fmt(ml)}} ml</b><span style="font-size:12px;color:${{MUTED}}">goal ${{fmt(goal)}}</span></div>
  <div style="height:8px;background:rgba(255,255,255,.10);border-radius:6px;margin-top:8px;overflow:hidden"><div style="width:${{Math.min(100, pct)}}%;height:100%;background:linear-gradient(90deg,#3b82f6,#7dd3fc)"></div></div></div>
</div>`;""", pad=14), 12)

    steps = cols(bc([N + 'steps_today', N + 'steps_goal', N + 'steps_7_day_average', N + 'steps_source', N + 'garmin_watch_sync', N + 'garmin_status'], f"""
const today = n('{N}steps_today'), goal = n('{N}steps_goal') || 8000, avg = n('{N}steps_7_day_average');
const src = s('{N}steps_source'), ok = avg >= goal;
const ws = s('{N}garmin_watch_sync');
const wm = (ws && ws !== 'unknown' && ws !== 'unavailable') ? Math.round((Date.now() - new Date(ws).getTime()) / 60000) : null;
const wAgo = wm === null ? null : (wm < 60 ? wm + ' min' : Math.round(wm / 60) + ' h');
const wStale = wm !== null && wm > 240;
return `
<div style="display:flex;align-items:center;gap:14px">
  <ha-icon icon="mdi:shoe-print" style="--mdc-icon-size:32px;color:#34d399"></ha-icon>
  <div style="flex:1"><div style="display:flex;justify-content:space-between;align-items:baseline"><b style="font-size:17px">${{fmt(today)}} steps today</b>
    <span style="font-size:12px;color:${{MUTED}}">goal ${{fmt(goal)}}</span></div>
  <div style="height:8px;background:rgba(255,255,255,.10);border-radius:6px;margin-top:8px;overflow:hidden"><div style="width:${{Math.min(100, today / goal * 100)}}%;height:100%;background:linear-gradient(90deg,#10b981,#6ee7b7)"></div></div>
  <div style="font-size:12px;margin-top:8px;color:${{ok ? '#34d399' : '#fbbf24'}}">7-day average <b>${{fmt(avg)}}</b> ${{ok ? '— on target' : '— ' + fmt(goal - avg) + ' short'}}</div>
  <div style="font-size:11px;margin-top:4px;color:${{wStale ? '#fbbf24' : MUTED}}">${{src && src !== 'unknown' ? 'From ' + src : ''}}${{wAgo ? ' · Garmin watch last synced ' + wAgo + ' ago' + (wStale ? ' — open Garmin Connect' : '') : ''}}</div></div>
</div>`;""", pad=14), 12)

    def water_btn(ml):
        return cols(bc([N + 'water'], f"""
return `<div style="text-align:center;font-weight:800;font-size:16px;line-height:1.2">
  <span style="font-size:20px">💧</span><br>+{ml}<span style="font-size:11px;color:${{MUTED}}"> ml</span></div>`;""", pad=10,
            tap={'action': 'call-service', 'service': 'script.turn_on', 'target': {'entity_id': f'script.headwind_add_water_{ml}'}}), 4)

    goals_ents = [N + k for k in ('steps_today', 'steps_goal', 'water', 'water_goal', 'protein', 'protein_goal', 'calories_eaten', 'calories_goal', 'activity_weekly')]
    todays_goals = cols(bc(goals_ents, f"""
const AW = (states['{N}activity_weekly'] || {{}}).attributes || {{}};
const steps = n('{N}steps_today'), stepGoal = n('{N}steps_goal') || 8000;
const water = n('{N}water'), waterGoal = n('{N}water_goal') || 2000;
const prot = n('{N}protein'), protGoal = n('{N}protein_goal');
const eaten = n('{N}calories_eaten'), kGoal = n('{N}calories_goal');
const rideGoal = AW.ride_goal || 2, rides = (AW.rides && AW.rides.length) ? AW.rides[AW.rides.length - 1] : 0;
const dow = (new Date().getDay() + 6) % 7, daysLeft = 7 - dow;          // Mon = 0; days left this week including today
const need = Math.max(0, rideGoal - rides);
const late = new Date().getHours() >= 20;
const box = (done, bad) => `<span style="flex:0 0 22px;width:22px;min-width:22px;box-sizing:border-box;height:22px;border-radius:7px;display:inline-flex;align-items:center;justify-content:center;font-size:14px;font-weight:800;${{done ? 'background:#34d399;color:#062b1f' : bad ? 'background:rgba(248,113,113,.25);color:#f87171;border:1.5px solid #f87171' : 'border:1.5px solid rgba(255,255,255,.4)'}}">${{done ? '✓' : bad ? '!' : ''}}</span>`;
const bar = (v, g, c) => `<div style="height:6px;background:rgba(255,255,255,.10);border-radius:5px;margin-top:6px;overflow:hidden"><div style="width:${{Math.min(100, g ? v / g * 100 : 0)}}%;height:100%;background:${{c}}"></div></div>`;
const row = (done, bad, label, val, sub, v, g, c) => `<div style="padding:9px 0;border-top:1px solid rgba(255,255,255,.08)"><div style="display:flex;align-items:center;gap:10px">${{box(done, bad)}}
  <div style="flex:1;min-width:0"><div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px"><span style="font-size:14px;font-weight:600">${{label}}</span><span style="font-size:14px;font-weight:700;white-space:nowrap">${{val}}</span></div>
  ${{v == null ? '' : bar(v, g, c)}}${{sub ? `<div style="font-size:11.5px;color:${{MUTED}};margin-top:4px;white-space:normal;line-height:1.3">${{sub}}</div>` : ''}}</div></div></div>`;
const f = x => Math.round(x).toLocaleString('en-GB');
const dSteps = steps >= stepGoal, dWater = water >= waterGoal, dProt = protGoal ? prot >= protGoal : false, dRides = rides >= rideGoal;
const over = kGoal && eaten > kGoal, dCal = kGoal && !over && late;
const ridePips = Array.from({{length: rideGoal}}, (_, i) => i < rides ? '●' : '○').join(' ');
const rideBad = !dRides && daysLeft <= need;
const rideSub = dRides ? 'Done for the week — anything extra is a bonus' : need + ' more to go · ' + daysLeft + ' day' + (daysLeft === 1 ? '' : 's') + ' left this week' + (rideBad ? ' — you need to ride ' + (daysLeft === need ? 'every day left' : 'soon') : '');
const done = [dSteps, dWater, dProt, dRides, dCal].filter(Boolean).length;
return `<div style="display:flex;justify-content:space-between;align-items:baseline"><div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${{MUTED}}">Today's goals</div><b style="font-size:13px;color:${{done === 5 ? '#34d399' : '#fff'}}">${{done}} of 5 done</b></div>
${{row(dSteps, false, 'Walk ' + f(stepGoal) + ' steps', f(steps) + ' / ' + f(stepGoal), dSteps ? '' : f(stepGoal - steps) + ' to go', steps, stepGoal, '#34d399')}}
${{row(dRides, rideBad, 'Ride ' + rideGoal + '× this week', ridePips + '  ' + rides + ' / ' + rideGoal, rideSub, null)}}
${{row(dWater, false, 'Drink ' + f(waterGoal) + ' ml', f(water) + ' / ' + f(waterGoal), '', water, waterGoal, '#38bdf8')}}
${{row(dProt, false, 'Hit ' + f(protGoal) + ' g protein', f(prot) + ' / ' + f(protGoal) + ' g', '', prot, protGoal, '#fbbf24')}}
${{row(dCal, over, 'Stay within ' + f(kGoal) + ' kcal', f(eaten) + ' / ' + f(kGoal), over ? f(eaten - kGoal) + ' over' : dCal ? '' : f(kGoal - eaten) + ' left · ticks off after 8 pm if you are under', eaten, kGoal, over ? '#f87171' : '#fc4c02')}}`;""", pad=16), 12)

    PLAN = N + 'plan'
    my_plan = chart_card(PLAN, """
if (A.status !== 'ok') return panel('My plan', '', null, `<div style="color:${MUTED};font-size:13px;padding:8px 0">Fill in your profile in Headwind → Nutrition → Goals → Smart goals.</div>`);
const row = (l, v, sub) => `<div style="display:flex;justify-content:space-between;align-items:baseline;padding:7px 0;border-top:1px solid rgba(255,255,255,.08)"><span style="font-size:13px;color:${MUTED}">${l}</span><span style="font-size:14px;font-weight:700">${v}<span style="font-size:11px;font-weight:400;color:${MUTED}">${sub || ''}</span></span></div>`;
const k = x => Math.round(x).toLocaleString('en-GB');
const drift = A.plan_goal !== A.saved_goal ? `<div style="white-space:normal;overflow-wrap:break-word;line-height:1.35;font-size:12px;color:#fbbf24;margin-top:8px">Plan currently works out at ${k(A.plan_goal)} — the goal only moves once it is ${A.drift}+ kcal away.</div>` : '';
return panel('My plan', A.auto ? 'auto-adjusting' : 'manual', null, `
  <div style="display:flex;align-items:baseline;gap:8px"><span style="font-size:40px;font-weight:800;color:#fc4c02">${k(A.saved_goal)}</span><span style="font-size:14px;color:${MUTED}">kcal / day</span></div>
  <div style="font-size:13px;color:${MUTED};margin:2px 0 8px">P ${A.protein} g · C ${A.carbs} g · F ${A.fat} g · fibre 30 g</div>
  ${row('Steps', k(A.step_goal), ' / day')}
  ${row('Riding allowance', k(A.rides_week), ' kcal / week (about 2 rides)')}
  ${row('Loss target', A.loss_lb_week + ' lb', ' / week')}
  <div style="font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:${MUTED};margin-top:12px">How it adds up (at ${A.weight_lb} lb)</div>
  ${row('Resting burn (BMR)', k(A.bmr))}
  ${row('× everyday movement', '+' + k(A.neat - A.bmr), ' (×' + A.activity + ' from ' + k(A.step_goal) + ' steps)')}
  ${row('+ riding allowance', '+' + k(A.rides_per_day), ' / day')}
  ${row('= daily burn', k(A.tdee))}
  ${row('− deficit', '−' + k(A.deficit), ' (' + A.loss_lb_week + ' lb/week)')}
  ${row('Never below', k(A.floor), ' (1.1 × BMR)' + (A.floored ? ' — floor is active' : ''))}
  ${drift}
  <div style="font-size:11px;color:${MUTED};margin-top:10px;white-space:normal;line-height:1.35">Riding never changes the goal — only your weight does. Rides are a fixed allowance, so a big ride is extra room, not a reason to eat more.</div>`);""")

    next_cut = chart_card(PLAN, """
if (A.status !== 'ok') return panel('Next calorie change', '', null, `<div style="color:${MUTED};font-size:13px;padding:8px 0">Needs your profile and a weigh-in.</div>`);
const k = x => Math.round(x).toLocaleString('en-GB');
const rate = A.rate_lb_wk;
const pace = rate && rate > 0.05 ? `At your current pace (${rate.toFixed(1)} lb/week)` : `<span style="color:#fbbf24">Not losing over the recent window, so no dates yet</span>`;
const eta = d => d == null ? '' : d < 1 ? ' · any day' : ' · about ' + (d < 14 ? d + ' days' : Math.round(d / 7) + ' weeks');
const rows = (A.next_down || []).map((d, i) => `<div style="display:flex;justify-content:space-between;align-items:baseline;padding:9px 12px;margin-top:6px;background:rgba(255,255,255,.06);border-radius:12px">
  <span><b style="font-size:16px">${k(d.goal)}</b> <span style="font-size:12px;color:${MUTED}">kcal</span></span>
  <span style="font-size:13px;text-align:right">at ${d.lb} lb<span style="color:${MUTED}"> · ${d.away_lb} lb to go${eta(d.days)}</span></span></div>`).join('');
const up = A.next_up ? `<div style="font-size:12px;color:${MUTED};margin-top:12px;white-space:normal;line-height:1.35">Goes the other way too: if your 7-day average weight climbs to ${A.next_up.lb} lb (${A.next_up.away_lb} lb up) the goal would rise to ${k(A.next_up.goal)}.</div>` : '';
return panel('Next calorie change', k(A.saved_goal) + ' now', null, `
  <div style="white-space:normal;overflow-wrap:break-word;line-height:1.35;font-size:13px;color:${MUTED}">Checked daily${A.last_check ? ' (last ' + fdate(A.last_check) + ')' : ''}. The goal follows your <b>7-day average weight</b> — when it falls far enough, the goal steps down by ${A.drift}+ kcal. ${pace}.</div>
  <div style="margin-top:8px">${rows}</div>${up}
  <div style="font-size:11px;color:${MUTED};margin-top:10px;white-space:normal;line-height:1.35">Not a schedule — it is triggered by weight, not the calendar. These are estimates; the daily check makes the real call.</div>`);""")

    s1 = {'type': 'grid', 'cards': [title('Nutrition', "{{ now().strftime('%A %-d %B') }}"), *outlook_cards(), hero, todays_goals, my_plan, next_cut, *macros, water,
                                    water_btn(150), water_btn(250), water_btn(500), steps]}

    meal_cards = []
    for key, label, icon in MEALS:
        k = f'{N}{key}_'
        ents = [k + 'calories', k + 'protein', k + 'carbs', k + 'fat', k + 'fibre']
        meal_cards.append({
            'type': 'conditional', 'conditions': [{'condition': 'numeric_state', 'entity': k + 'calories', 'above': 0}],
            'card': cols(bc(ents, f"""
const kc = n('{k}calories'), p = n('{k}protein'), c = n('{k}carbs'), f = n('{k}fat'), fi = n('{k}fibre');
const tot = (p * 4 + c * 4 + f * 9) || 1;
const seg = (v, col) => `<div style="width:${{v / tot * 100}}%;background:${{col}}"></div>`;
return `
<div style="display:flex;align-items:center;gap:14px">
  <div style="width:46px;height:46px;border-radius:15px;background:rgba(252,76,2,.18);display:flex;align-items:center;justify-content:center;flex-shrink:0">
    <ha-icon icon="{icon}" style="--mdc-icon-size:25px;color:#fc7a3c"></ha-icon></div>
  <div style="flex:1;min-width:0">
    <div style="font-weight:700;font-size:16px">{label}</div>
    <div style="display:flex;height:6px;border-radius:4px;overflow:hidden;margin:7px 0 6px;background:rgba(255,255,255,.08)">${{seg(p * 4, '{HEX['protein']}')}}${{seg(c * 4, '{HEX['carbs']}')}}${{seg(f * 9, '{HEX['fat']}')}}</div>
    <div style="font-size:12px;color:${{MUTED}}">P ${{fmt(p)}}g · C ${{fmt(c)}}g · F ${{fmt(f)}}g · Fibre ${{fmt(fi, 1)}}g</div></div>
  <div style="text-align:right"><div style="font-size:24px;font-weight:800;line-height:1">${{fmt(kc)}}</div><div style="font-size:11px;color:${{MUTED}}">kcal</div></div>
</div>`;""", pad=14), 12)})
    empty = {'type': 'conditional', 'conditions': [{'condition': 'numeric_state', 'entity': eaten, 'below': 1}],
             'card': cols({'type': 'markdown', 'content': '_Nothing logged yet today — open Headwind and add breakfast._'}, 12)}

    RC = N + 'reality_check'
    reality = chart_card(RC, """
if (A.status !== 'ok') {
  return panel('Reality check', '', null, `<div style="color:${MUTED};font-size:13px;padding:10px 0">Needs about 7 logged days and 5 weigh-ins over that period to judge — ${A.days_logged || 0} days and ${A.weigh_ins || 0} weigh-ins so far. Weigh in a few more times and this fills itself in.</div>`);
}
const target = A.target_lb_wk, rate = -A.rate_lb_wk;                       // rate > 0 means losing
const pace = rate / target;
const verdict = pace > 1.15 ? ['Losing faster than your ' + target + ' lb/week plan', '#34d399']
              : pace >= 0.85 ? ['Right on your ' + target + ' lb/week plan', '#34d399']
              : pace > 0 ? ['Losing, but slower than your ' + target + ' lb/week plan', '#fbbf24']
              : ['Not losing over this period', '#f87171'];
const box = (v, l, c) => `<div style="text-align:center;background:rgba(255,255,255,.06);border-radius:14px;padding:9px 4px">
  <div style="font-size:19px;font-weight:800;color:${c}">${v}</div><div style="font-size:11px;color:${MUTED};margin-top:2px">${l}</div></div>`;
const diff = A.model_burn ? A.observed_burn - A.model_burn : null;
return panel('Reality check · last ' + A.win + ' days', A.confidence === 'good' ? '' : A.confidence + ' confidence', null, `
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
    ${box(fmt(A.intake), 'ate / day', '#fc7a3c')}
    ${box(fmt(A.observed_burn), 'burned / day', '#38bdf8')}
    ${box((A.change_lb > 0 ? '+' : '') + A.change_lb.toFixed(1) + ' lb', fmt(rate, 1) + ' lb/wk lost', '#34d399')}
  </div>
  <div style="margin:10px 0 2px;font-size:14px;font-weight:700;color:${verdict[1]}">${verdict[0]}</div>
  ${A.stale_days > 2 ? `<div style="font-size:11px;color:#fbbf24;margin-bottom:4px">As of ${fdate(A.as_of)} — the last day you logged food.</div>` : ''}
  <div style="font-size:11px;color:${MUTED};margin-bottom:6px;white-space:normal;overflow-wrap:break-word;line-height:1.35">Burn is measured from what you ate and how your weight moved (${A.days_logged} logged days, ${A.weigh_ins} weigh-ins)${diff != null ? '; the model estimates ' + fmt(A.model_burn) + ' (' + (diff >= 0 ? '+' : '') + fmt(diff) + ')' : ''}.</div>
  ${chart({labels: (A.w || []).map(p => p[0]), xdates: true, dec: 0, h: 118, series: [{v: (A.plan || []).map(p => p[1]), xs: (A.plan || []).map(p => p[0]), color: 'rgba(255,255,255,.6)', dash: true, w: 1.6}, {v: (A.w || []).map(p => p[1]), xs: (A.w || []).map(p => p[0]), color: '#fc4c02', dots: true, r: 3.6, w: 2.2}]})}
  <div style="display:flex;gap:14px;font-size:11px;color:${MUTED};margin-top:4px"><span>● your weigh-ins (lb)</span><span>┅ plan at ${target} lb/week</span></div>`);""")

    meal_keys = [('breakfast', 'Breakfast'), ('lunch', 'Lunch'), ('dinner', 'Dinner'), ('snacks', 'Snacks'), ('ride_fuel', 'Ride fuel'), ('recovery', 'Recovery')]
    prot_ents = [f'{N}{k}_protein' for k, _ in meal_keys] + [N + 'protein', N + 'protein_goal', N + 'protein_remaining', N + 'calories_remaining']
    meals_js = ', '.join(f"['{lbl}', n('{N}{k}_protein')]" for k, lbl in meal_keys)
    protein_by_meal = cols(bc(prot_ents, f"""
const rows = [{meals_js}];
const total = n('{N}protein'), goal = n('{N}protein_goal'), left = n('{N}protein_remaining'), kleft = n('{N}calories_remaining');
const mx = Math.max(1, ...rows.map(r => r[1]));
const shown = rows.filter((r, i) => r[1] > 0 || i < 4);
return `
<div style="display:flex;justify-content:space-between;align-items:baseline">
  <div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${{MUTED}}">Protein by meal</div>
  <div style="font-size:15px;font-weight:800;color:#facc15">${{fmt(total)}}<span style="font-size:12px;color:${{MUTED}}"> / ${{fmt(goal)}} g</span></div></div>
<div style="margin-top:10px">${{shown.map(r => `
  <div style="display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px">
    <span style="width:74px;color:${{MUTED}}">${{r[0]}}</span>
    <div style="flex:1;height:9px;background:rgba(255,255,255,.08);border-radius:6px;overflow:hidden"><div style="width:${{r[1] / mx * 100}}%;height:100%;background:linear-gradient(90deg,#eab308,#fde047)"></div></div>
    <b style="width:42px;text-align:right">${{fmt(r[1])}} g</b></div>`).join('')}}</div>
<div style="font-size:12px;margin-top:8px;color:${{left > 0 ? '#fbbf24' : '#34d399'}}">${{left > 0 ? fmt(left) + ' g to go' + (kleft > 0 ? ' · ' + fmt(kleft) + ' kcal available' : '') : 'Protein target reached'}}</div>`;""", pad=14), 12)

    s2 = {'type': 'grid', 'cards': [heading('By meal', 'mdi:silverware-fork-knife'), protein_by_meal, *meal_cards, empty]}

    kcal_hist = chart_card(D_, """
const L = 21, k = tail(A.k, L);
return panel('Calories · last 3 weeks', fmt(last(k) || 0) + ' today', null,
  chart({labels: tail(A.d, L), series: [{v: k, color: '#fc4c02', type: 'bar'}], goal: (A.goal || {}).k, zero: true, dec: 0}));""")
    macro_hist = chart_card(D_, """
const L = 21, gap = arr => arr.map(v => v ? v : null);   // unlogged days are gaps, not zeros
return panel('Macros · last 3 weeks (g)', '', [['Protein', '#facc15'], ['Carbs', '#38bdf8'], ['Fat', '#f87171']],
  chart({labels: tail(A.d, L), zero: true, dec: 0, series: [
    {v: gap(tail(A.p, L)), color: '#facc15', dots: true, r: 2.6, w: 2.2}, {v: gap(tail(A.c, L)), color: '#38bdf8', dots: true, r: 2.6, w: 2.2}, {v: gap(tail(A.f, L)), color: '#f87171', dots: true, r: 2.6, w: 2.2}]}));""")
    fibre_hist = chart_card(D_, """
const L = 21, fi = tail(A.fi, L);
return panel('Fibre · last 3 weeks', fmt(last(fi) || 0, 1) + ' g today', null,
  chart({labels: tail(A.d, L), series: [{v: fi, color: '#fb923c', type: 'bar'}], goal: 30, zero: true, dec: 0}));""")
    activity_trend = chart_card(N + 'activity_weekly', """
const d = A.d || [];
if (d.length < 2) return panel('Activity trend', '', null, `<div style="color:${MUTED};font-size:13px;padding:8px 0">Needs a few weeks of data.</div>`);
const cur = d.length - 1;                                   // last week is still in progress: drawn dim, left out of the averages
const roll = v => v.map((_, i) => { if (i >= cur) return null; const w = v.slice(0, i + 1).filter(x => x != null); const t = w.slice(-4); return t.length >= 2 ? t.reduce((a, b) => a + b, 0) / t.length : null; });
const cmp = v => { const done = v.slice(0, cur).filter(x => x != null); if (done.length < 8) return null;
  const a = done.slice(-4).reduce((x, y) => x + y, 0) / 4, b = done.slice(-8, -4).reduce((x, y) => x + y, 0) / 4; return b ? (a - b) / b * 100 : null; };
const pill = (label, pct) => { if (pct == null) return `<span style="background:rgba(255,255,255,.08);border-radius:999px;padding:5px 11px;font-size:12px;color:${MUTED}">${label} · not enough weeks</span>`;
  const flat = Math.abs(pct) < 5, up = pct >= 5, c = flat ? '#fbbf24' : up ? '#34d399' : '#f87171';
  return `<span style="background:${c}22;color:${c};border-radius:999px;padding:5px 11px;font-size:12px;font-weight:700">${label} · ${flat ? 'flat' : up ? '▲ up ' + Math.round(pct) + '%' : '▼ down ' + Math.round(-pct) + '%'}</span>`; };
const mi = A.mi, st = A.steps, goal = A.step_goal;
const dim = arr => arr.map((_, i) => i === cur ? 'rgba(255,255,255,.28)' : null);
const miCol = mi.map((_, i) => i === cur ? 'rgba(252,76,2,.35)' : '#fc4c02'), stCol = st.map((_, i) => i === cur ? 'rgba(125,211,252,.35)' : '#7dd3fc');
const sIdx = st.findIndex(x => x != null), sl = i => i < 0 ? 0 : i;
const pm = cmp(mi), ps = cmp(st);
const flatBoth = pm != null && ps != null && Math.abs(pm) < 5 && Math.abs(ps) < 5;
const flatMsg = flatBoth ? `<div style="font-size:12px;color:#fbbf24;margin-top:10px;white-space:normal;line-height:1.35">Riding and steps have both been level for the last 4 weeks vs the 4 before — a plateau in activity, which is when losses usually stall.</div>` : '';
return panel('Activity trend · 26 weeks', '', [['Week', 'rgba(255,255,255,.6)'], ['4-week average', '#ffffff']], `
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">${pill('Riding', pm)}${pill('Steps', ps)}</div>
  <div style="font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:${MUTED}">Riding · miles per week</div>
  ${chart({labels: d, xdates: false, dec: 0, zero: true, h: 120, series: [{v: mi, color: '#fc4c02', colors: miCol, type: 'bar'}, {v: roll(mi), color: '#ffffff', w: 2.2}]})}
  <div style="font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:${MUTED};margin-top:12px">Steps · daily average per week</div>
  ${chart({labels: d, dec: 0, zero: true, h: 120, goal: goal, series: [{v: st, color: '#7dd3fc', colors: stCol, type: 'bar'}, {v: roll(st), color: '#ffffff', w: 2.2}]})}
  ${flatMsg}
  <div style="font-size:11px;color:${MUTED};margin-top:10px;white-space:normal;line-height:1.35">Raw weekly values are the bars; the line is a 4-week average of completed weeks (this week, dimmed, is still in progress). Trend verdict compares the last 4 weeks with the 4 before, ±5% counts as flat. Steps only go back to late June.</div>`);""")

    s3 = {'type': 'grid', 'cards': [heading('Trends', 'mdi:chart-line'), reality, activity_trend, kcal_hist, macro_hist, fibre_hist]}
    return {'title': 'Today', 'path': 'today', 'icon': 'mdi:food-apple', 'type': 'sections', 'max_columns': 3, 'sections': [s1, s2, s3]}


# ───────────────────────────── BODY ─────────────────────────────
def view_body():
    w = SC + 'weight'
    hero = cols(bc([w, N + 'weight_history', N + 'weight_change_per_week'], f"""
const lb = n('{w}'), rate = n('{N}weight_change_per_week') * 2.20462;
// average of the real weigh-ins in the last 7 days up to your latest one (no smoothing)
const H = (states['{N}weight_history'] || {{}}).attributes || {{}}, hd = H.d || [], hw = (H.w || []).map(v => v * 2.20462);
const lastT = hd.length ? Date.parse(hd[hd.length - 1] + 'T12:00:00') : 0;
const wk = hw.filter((_, i) => (lastT - Date.parse(hd[i] + 'T12:00:00')) / 86400000 <= 7);
const trend = wk.length ? wk.reduce((a, b) => a + b, 0) / wk.length : 0;
const st = x => x.toFixed(1) + ' lb';
const flat = Math.abs(rate) < 0.05, down = rate < 0;
// biggest recent high (last 21 days, at least 2 days before the latest weigh-in) and how far you have come down from it
let pk = null;
hd.forEach((dd, i) => {{ const age = (lastT - Date.parse(dd + 'T12:00:00')) / 86400000; if (age >= 2 && age <= 21 && (!pk || hw[i] > pk.w)) pk = {{d: dd, w: hw[i]}}; }});
const fromPk = pk ? pk.w - hw[hw.length - 1] : 0;
const peakPill = fromPk >= 1 ? `<span style="background:rgba(52,211,153,.16);color:#34d399;border-radius:999px;padding:6px 12px;font-size:13px;font-weight:700">▼ ${{fromPk.toFixed(1)}} lb since ${{fdate(pk.d)}}</span>` : '';
return `
<div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${{MUTED}}">Weight</div>
<div style="font-size:44px;font-weight:800;line-height:1.05;margin:4px 0 10px">${{lb > 0 ? lb.toFixed(1) : '—'}}<span style="font-size:20px;color:${{MUTED}}"> lb</span></div>
<div style="display:flex;gap:10px;flex-wrap:wrap">
  <span style="background:rgba(255,255,255,.08);border-radius:999px;padding:6px 12px;font-size:13px">Last weighed <b>${{hd.length ? fdate(hd[hd.length - 1]) : '—'}}</b></span>
  <span style="background:${{flat ? 'rgba(255,255,255,.08)' : down ? 'rgba(52,211,153,.16)' : 'rgba(248,113,113,.16)'}};color:${{flat ? MUTED : down ? '#34d399' : '#f87171'}};border-radius:999px;padding:6px 12px;font-size:13px;font-weight:700">
    ${{flat ? '● steady' : (down ? '▼ ' : '▲ ') + Math.abs(rate).toFixed(1) + ' lb / week'}}</span>${{peakPill}}</div>`;"""), 12)

    def tile(name, ent, unit, colour, dec=1, cols_=6):
        return cols(bc([ent], f"""
return `<div style="text-align:center">
  <div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:${{MUTED}}">{name}</div>
  <div style="font-size:30px;font-weight:800;margin-top:4px;color:{colour}">${{fmt(n('{ent}'), {dec})}}<span style="font-size:14px;color:${{MUTED}}">{unit}</span></div></div>`;""", pad=14), cols_)

    GPJ = N + 'goal_projection'
    goal = chart_card(GPJ, """
if (A.status === 'no_goal') return panel('Road to goal', '', null, `<div style="color:${MUTED};font-size:13px;padding:8px 0">Set a goal weight in Headwind → Nutrition → Goals.</div>`);
if (A.status === 'need_more_data') return panel('Road to goal', '', null, `<div style="color:${MUTED};font-size:13px;padding:8px 0">Needs a few more weigh-ins.</div>`);
const st = lb => (Math.abs(lb - Math.round(lb)) < 0.05 ? Math.round(lb) : lb.toFixed(1)) + ' lb';
if (A.status === 'reached') return panel('Road to ' + st(A.goal_lb), '', null, `<div style="font-size:20px;font-weight:800;color:#34d399;padding:10px 0">Goal reached 🎉</div>`);
const pct = Math.round((A.progress || 0) * 100);
const dl = A.rate_lb_wk;
const eta = (d) => d ? fdate(d) : '—';
const brk = A.stale ? `<div style="font-size:12px;color:#fbbf24;margin-top:10px">⚠ Last weigh-in was ${A.days_since} days ago (${fdate(A.last_weigh_in)}) — weigh in to refresh this.</div>` : '';
return panel('Road to ' + st(A.goal_lb), pct + '%', null, `
  <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px"><span style="font-size:32px;font-weight:800">${A.remaining_lb.toFixed(1)}</span><span style="font-size:14px;color:${MUTED}">lb to go · now ${st(A.current_lb)}</span></div>
  <div style="height:10px;background:rgba(255,255,255,.10);border-radius:8px;margin:10px 0 4px;overflow:hidden"><div style="width:${pct}%;height:100%;background:linear-gradient(90deg,#34d399,#6ee7b7)"></div></div>
  <div style="display:flex;justify-content:space-between;font-size:11px;color:${MUTED}"><span>${st(A.start_lb)}</span><span>${st(A.goal_lb)}</span></div>
  ${A.eta_now ? `<div style="margin-top:14px;font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:${MUTED}">At your current pace (${dl.toFixed(1)} lb/week)</div>
    <div style="font-size:26px;font-weight:800;color:#34d399;margin-top:2px">${eta(A.eta_now)}<span style="font-size:14px;color:${MUTED}"> · ${A.weeks_now} weeks</span></div>
    <div style="font-size:12px;color:${MUTED};margin-top:4px">If it slows to 70% of that: ${eta(A.eta_slow)} · at your ${A.target_lb_wk} lb/week plan: ${eta(A.eta_plan)}</div>`
   : `<div style="margin-top:12px;font-size:14px;color:#fbbf24">Not losing over the recent window, so there's no date to project yet.</div>`}
  ${(A.milestones || []).length ? `<div style="margin-top:14px;display:flex;flex-direction:column;gap:6px">${A.milestones.map(m => `
    <div style="display:flex;justify-content:space-between;font-size:13px;padding:6px 10px;background:rgba(255,255,255,.06);border-radius:10px"><span>${m.label}</span><span style="color:${MUTED}">${eta(m.eta)}</span></div>`).join('')}</div>` : ''}
  ${brk}
  <div style="font-size:11px;color:${MUTED};margin-top:10px;white-space:normal;overflow-wrap:break-word;line-height:1.35">A guide from your recent weigh-ins, not a promise: weight loss rarely stays this fast, and a few days away or a gain will move it.</div>`);""")

    s1 = {'type': 'grid', 'cards': [
        title('Body', "Etekcity scale · last weigh-in {{ relative_time(states.%s.last_changed) }} ago" % w),
        hero,
        goal,
        tile('Body fat', SC + 'body_fat_percentage', '%', '#f472b6'),
        tile('BMI', SC + 'body_mass_index', '', '#c4b5fd'),
    ]}

    g_w = chart_card(W_, """
const LB = 2.20462, L = 90;
const d = tail(A.d, L), w = tail(A.w, L).map(v => v * LB), src = tail(A.src, L);
const dots = src.map(x => x === 'homeassistant' ? '#7dd3fc' : '#ffffff');
const change = w.length > 1 ? (w[w.length - 1] - w[0]) : null;
const right = w.length ? fmt(w[w.length - 1], 1) + ' lb' : '';
return panel('Weight · ' + (d.length > 1 ? 'since ' + shortDate(d[0]) : 'latest') + (change != null ? ' (' + (change > 0 ? '+' : '') + change.toFixed(1) + ' lb)' : ''), right,
  [['Scale (last of the day)', '#7dd3fc'], ['Typed / imported', '#ffffff']],
  chart({labels: d, xdates: true, dec: 0, h: 150, series: [{v: w, xs: d, color: 'rgba(255,255,255,.45)', dots: true, dotColors: dots, r: 4.2, w: 1.6}]}));""")
    g_wk = chart_card(W_, """
const LB = 2.20462, d = A.d || [], w = A.w || [];
const monday = iso => { const x = new Date(iso + 'T12:00:00'); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x.toISOString().slice(0, 10); };
const wk = {};
d.forEach((iso, i) => { (wk[monday(iso)] = wk[monday(iso)] || []).push(w[i] * LB); });
const keys = Object.keys(wk).sort();
const avg = keys.map(k => wk[k].reduce((a, b) => a + b, 0) / wk[k].length);
const labels = [], delta = [], colors = [];
for (let i = 1; i < keys.length; i++) {
  const gap = Math.max(1, Math.round((new Date(keys[i]) - new Date(keys[i - 1])) / 604800000));
  const dv = (avg[i] - avg[i - 1]) / gap;          // per week, even if a week was skipped
  labels.push(keys[i]); delta.push(Math.round(dv * 10) / 10); colors.push(dv > 0.05 ? '#f87171' : '#34d399');
}
const latest = delta.length ? delta[delta.length - 1] : null;
return panel('Weekly weight change', latest != null ? (latest > 0 ? '+' : '') + latest.toFixed(1) + ' lb' : '', [['Loss', '#34d399'], ['Gain', '#f87171']],
  delta.length ? chart({labels, dec: 1, diverge: true, h: 120, series: [{v: delta, color: '#34d399', colors, type: 'bar'}]})
               : `<div style="color:${MUTED};font-size:13px;padding:12px 0">Needs two weeks of weigh-ins.</div>`);""")
    g_f = chart_card(W_, """
const idx = (A.bf || []).map((v, i) => v == null ? -1 : i).filter(i => i >= 0);
const d = idx.map(i => A.d[i]), v = idx.map(i => A.bf[i]);
return panel('Body fat', v.length ? fmt(v[v.length - 1], 1) + ' %' : '', null,
  chart({labels: d, dec: 1, series: [{v: v, color: '#f472b6', area: true, dots: true}]}));""")
    g_b = chart_card(W_, """
const bmi = tail(A.bmi, 90), d = tail(A.d, 90);
const change = bmi.length > 1 ? bmi[bmi.length - 1] - bmi[0] : null;
return panel('BMI' + (change != null ? ' · ' + (change > 0 ? '+' : '') + change.toFixed(1) + ' since ' + shortDate(d[0]) : ''),
  bmi.length ? fmt(bmi[bmi.length - 1], 1) : '', [['BMI', '#c4b5fd'], ['Class I obesity starts at 30', 'rgba(255,255,255,.6)']],
  chart({labels: d, dec: 1, refs: [{v: 30, label: 'BMI 30', color: '#fbbf24'}], series: [{v: bmi, color: '#c4b5fd', area: true, dots: true}]}));""")
    s2 = {'type': 'grid', 'cards': [heading('Trends', 'mdi:chart-line'), g_w, g_wk, g_b, g_f]}

    s3 = {'type': 'grid', 'cards': [
        heading('Scale readings', 'mdi:heart-pulse'),
        cols({'type': 'markdown', 'content': '<small>Bio-impedance scales estimate these — trust the direction over time, not the exact number.</small>'}, 12),
        tile('Body water', SC + 'body_water_percentage', '%', '#60a5fa', 1, 4),
        tile('Muscle', SC + 'muscle_mass', ' lb', '#fbbf24', 1, 4),
        tile('Fat-free', SC + 'fat_free_weight', ' lb', '#34d399', 1, 4),
        tile('Visceral', SC + 'visceral_fat_value', '', '#fb923c', 0, 4),
        tile('BMR', SC + 'basal_metabolic_rate', ' kcal', '#f87171', 0, 4),
        tile('Metabolic age', SC + 'metabolic_age', '', '#a78bfa', 0, 4),
    ]}
    return {'title': 'Body', 'path': 'body', 'icon': 'mdi:human-handsup', 'type': 'sections', 'max_columns': 3, 'sections': [s1, s2, s3]}


# ───────────────────────────── RIDING ─────────────────────────────
def view_riding():
    lr = [B + 'last_ride', B + 'last_ride_date', B + 'last_ride_sport', B + 'last_ride_distance', B + 'last_ride_moving_time',
          B + 'last_ride_avg_speed', B + 'last_ride_elevation', B + 'last_ride_avg_heart_rate', B + 'last_ride_avg_power', B + 'last_ride_calories']
    last = cols(bc(lr, f"""
const box = (v, u, l, c) => `<div style="text-align:center;background:rgba(255,255,255,.06);border-radius:14px;padding:10px 4px">
  <div style="font-size:20px;font-weight:800;color:${{c}}">${{v}}<span style="font-size:11px;color:${{MUTED}}"> ${{u}}</span></div>
  <div style="font-size:11px;color:${{MUTED}};margin-top:2px">${{l}}</div></div>`;
return `
<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
  <ha-icon icon="mdi:bike" style="--mdc-icon-size:34px;color:#fc4c02"></ha-icon>
  <div><div style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:${{MUTED}}">Latest ride · ${{s('{B}last_ride_date')}}</div>
  <div style="font-size:19px;font-weight:800">${{s('{B}last_ride')}}</div></div></div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
  ${{box(s('{B}last_ride_distance').replace(' mi',''), 'mi', 'Distance', '#38bdf8')}}
  ${{box(s('{B}last_ride_moving_time'), '', 'Time', '#e8f3fa')}}
  ${{box(s('{B}last_ride_avg_speed'), 'mph', 'Avg speed', '#34d399')}}
  ${{box(fmt(n('{B}last_ride_elevation')), 'ft', 'Climbing', '#fbbf24')}}
  ${{box(fmt(n('{B}last_ride_avg_heart_rate')), 'bpm', 'Avg HR', '#f87171')}}
  ${{box(fmt(n('{B}last_ride_avg_power')), 'W', 'Avg power', '#facc15')}}
</div>
<div style="text-align:center;margin-top:12px;font-size:13px;color:${{MUTED}}">Burned <b style="color:#fb923c;font-size:15px">${{fmt(n('{B}last_ride_calories'))}} kcal</b></div>`;"""), 12)
    s1 = {'type': 'grid', 'cards': [title('Riding', 'Latest ride from Headwind'), *outlook_cards(), last, ride_map(), segments_card()]}

    def gring(name, ent, mx, unit, good_high=True):
        return cols(bc([ent], f"""
const v = n('{ent}'), mx = {mx};
const pct = v / mx * 100;
const col = {'pct > 50 ? "#34d399" : (pct > 25 ? "#f59e0b" : "#ef4444")' if good_high else 'pct < 40 ? "#34d399" : (pct < 70 ? "#f59e0b" : "#ef4444")'};
return ring(pct, col, fmt(v, {1 if unit == 'h' else 0}), '{unit}', 78) +
  `<div style="text-align:center;margin-top:8px;font-size:13px;font-weight:700">{name}</div>`;""", pad=12), 4)

    def small(name, ent, unit, colour):
        return cols(bc([ent], f"""
const v = s('{ent}');
return `<div style="text-align:center"><div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:${{MUTED}}">{name}</div>
  <div style="font-size:24px;font-weight:800;color:{colour};margin-top:4px">${{isNaN(parseFloat(v)) ? '—' : fmt(parseFloat(v))}}<span style="font-size:12px;color:${{MUTED}}"> {unit}</span></div></div>`;""", pad=12), 4)

    s2 = {'type': 'grid', 'cards': [
        heading('Recovery', 'mdi:battery-heart-variant'),
        gring('Body battery', B + 'recovery_body_battery', 100, '%'),
        gring('Sleep', B + 'recovery_sleep', 10, 'h'),
        gring('Stress', B + 'recovery_stress', 100, '', good_high=False),
        small('Resting HR', B + 'recovery_resting_hr', 'bpm', '#f87171'),
        small('HRV', B + 'recovery_hrv', 'ms', '#c4b5fd'),
        chart_card(R_, """
const goal = parseFloat((states['sensor.headwind_nutrition_steps_goal'] || {}).state) || 8000, st = A.st || [];
return panel('Steps · 30 days', last(st) != null ? fmt(last(st)) + ' today' : '', null,
  chart({labels: A.d || [], dec: 0, zero: true, goal: goal, series: [{v: st, color: '#34d399', type: 'bar'}]}));"""),
        chart_card(R_, """
const hr = A.hr || [];
return panel('Resting heart rate · 30 days', last(hr) != null ? fmt(last(hr)) + ' bpm' : '', null,
  chart({labels: A.d || [], dec: 0, series: [{v: hr, color: '#f87171', area: true, dots: true, r: 2.4}]}));"""),
        chart_card(R_, """
const sl = A.sl || [];
return panel('Sleep · 30 days', last(sl) != null ? fmt(last(sl), 1) + ' h' : '', null,
  chart({labels: A.d || [], dec: 0, zero: true, goal: 8, series: [{v: sl, color: '#a78bfa', type: 'bar'}]}));"""),
        chart_card(R_, """
const bb = A.bb || [];
return panel('Body battery · 30 days', last(bb) != null ? fmt(last(bb)) + ' %' : '', null,
  chart({labels: A.d || [], dec: 0, min: 0, series: [{v: bb, color: '#34d399', area: true, dots: true, r: 2.4}]}));"""),
    ]}

    def life(name, ent, unit, colour, dec=0):
        return cols(bc([ent], f"""
return `<div style="text-align:center"><div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:${{MUTED}}">{name}</div>
  <div style="font-size:24px;font-weight:800;color:{colour};margin-top:4px">${{fmt(n('{ent}'), {dec})}}<span style="font-size:12px;color:${{MUTED}}"> {unit}</span></div></div>`;""", pad=12), 6)

    badges = cols(bc([B + 'badges_earned', B + 'total_badges'], f"""
const e = n('{B}badges_earned'), t = n('{B}total_badges') || 1;
return `<div style="display:flex;align-items:center;gap:14px"><ha-icon icon="mdi:trophy" style="--mdc-icon-size:34px;color:#fbbf24"></ha-icon>
  <div style="flex:1"><div style="display:flex;justify-content:space-between"><b style="font-size:17px">${{e}} of ${{t}} badges</b><span style="color:${{MUTED}}">${{Math.round(e / t * 100)}}%</span></div>
  <div style="height:8px;background:rgba(255,255,255,.10);border-radius:6px;margin-top:8px;overflow:hidden"><div style="width:${{e / t * 100}}%;height:100%;background:linear-gradient(90deg,#f59e0b,#fde68a)"></div></div></div></div>`;""", pad=14), 12)
    RM = N + 'ride_months'
    ytd = chart_card(RM, """
const y = A.ytd || {};
const box = (v, u, l, c) => `<div style="text-align:center;background:rgba(255,255,255,.06);border-radius:14px;padding:10px 4px">
  <div style="font-size:19px;font-weight:800;color:${c}">${v}<span style="font-size:11px;color:${MUTED}"> ${u}</span></div><div style="font-size:11px;color:${MUTED};margin-top:2px">${l}</div></div>`;
return panel((A.year || '') + ' so far', '', null, `<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px">
  ${box(fmt(y.mi || 0, 0), 'mi', 'distance', '#38bdf8')}${box(fmt(y.rides || 0), '', 'rides', '#fc7a3c')}
  ${box(fmt(y.h || 0, 0), 'h', 'in the saddle', '#2dd4bf')}${box(fmt(y.ft || 0), 'ft', 'climbing', '#fbbf24')}</div>`);""")
    months_mi = chart_card(RM, """
const mi = A.mi || [];
const best = mi.length ? Math.max(...mi) : 0;
return panel('Miles per month', best ? 'best ' + fmt(best, 0) : '', null, chart({labels: A.d || [], dec: 0, zero: true, monthly: true, series: [{v: mi, color: '#38bdf8', type: 'bar'}]}));""")
    months_tbl = chart_card(RM, """
const d = A.d || [], m = i => new Date(d[i] + 'T12:00:00').toLocaleDateString('en-GB', {month: 'long'});
const rows = d.map((_, i) => i).reverse().filter(i => (A.rides || [])[i] > 0);
const cell = 'padding:7px 4px;text-align:right';
return panel('Month by month', '', null, `<div style="display:grid;grid-template-columns:1.3fr .7fr .9fr .8fr 1fr;font-size:11px;color:${MUTED};padding:0 4px 4px"><span>Month</span><span style="text-align:right">Rides</span><span style="text-align:right">Miles</span><span style="text-align:right">Hours</span><span style="text-align:right">Climb ft</span></div>` +
  rows.map(i => `<div style="display:grid;grid-template-columns:1.3fr .7fr .9fr .8fr 1fr;font-size:13px;border-top:1px solid rgba(255,255,255,.08)">
    <span style="padding:7px 4px;font-weight:700">${m(i)}</span><span style="${cell}">${A.rides[i]}</span><span style="${cell};color:#38bdf8;font-weight:700">${fmt(A.mi[i], 0)}</span>
    <span style="${cell}">${fmt(A.h[i], 1)}</span><span style="${cell}">${fmt(A.ft[i])}</span></div>`).join(''));""")
    s3 = {'type': 'grid', 'cards': [heading('This year', 'mdi:calendar-month'), ytd, months_mi, months_tbl]}
    RY = N + 'ride_years'
    years_chart = chart_card(RY, """
const mi = A.mi || [], td = A.mi_to_date || [], cnt = mi.length;
const cur = (A.this_year || 0), i = (A.d || []).findIndex(d => d.startsWith(String(cur)));
const colors = mi.map((_, k) => k === i ? '#fc7a3c' : '#38bdf8');
const best = cnt ? Math.max(...mi) : 0, bestYear = cnt ? A.d[mi.indexOf(best)].slice(0, 4) : '';
return panel('Miles per year', best ? 'best ' + bestYear : '', [['Full year', '#38bdf8'], ['By ' + A.as_of, '#e0f2fe'], [cur + ' (year to date)', '#fc7a3c']],
  chart({labels: A.d || [], dec: 0, zero: true, yearly: true, h: 150, series: [{v: mi, color: '#38bdf8', colors, type: 'bar'}, {v: td, color: '#e0f2fe', type: 'bar', bw: .38}]}));""")
    years_tbl = chart_card(RY, """
const d = A.d || [], cur = String(A.this_year || '');
const idx = d.map((_, i) => i).reverse();
const cell = 'padding:7px 4px;text-align:right';
const g = 'grid-template-columns:1fr .8fr 1fr .9fr 1.1fr';
return panel('Year by year', 'to ' + (A.as_of || ''), null,
  `<div style="display:grid;${g};font-size:11px;color:${MUTED};padding:0 4px 4px"><span>Year</span><span style="text-align:right">Rides</span><span style="text-align:right">Miles</span><span style="text-align:right">Hours</span><span style="text-align:right">Climb ft</span></div>` +
  idx.map(i => { const y = d[i].slice(0, 4), now = y === cur;
    return `<div style="display:grid;${g};font-size:13px;border-top:1px solid rgba(255,255,255,.08);${now ? 'background:rgba(252,122,60,.10);border-radius:8px' : ''}">
    <span style="padding:7px 4px;font-weight:700">${y}${now ? ' *' : ''}</span><span style="${cell}">${fmt(A.rides[i])}</span><span style="${cell};color:#38bdf8;font-weight:700">${fmt(A.mi[i])}</span>
    <span style="${cell}">${fmt(A.h[i])}</span><span style="${cell}">${fmt(A.ft[i])}</span></div>`; }).join('') +
  `<div style="font-size:11px;color:${MUTED};margin-top:8px;white-space:normal;overflow-wrap:break-word;line-height:1.35">* ${cur} is year to date. The narrow bars show what each year had reached by ${A.as_of}.</div>`);""")
    s3b = {'type': 'grid', 'cards': [heading('Year by year', 'mdi:calendar-range'), years_chart, years_tbl]}

    s4 = {'type': 'grid', 'cards': [
        heading('All time', 'mdi:infinity'),
        life('Rides', B + 'total_rides', '', '#fc7a3c'),
        life('Distance', B + 'total_distance', 'mi', '#38bdf8'),
        life('In the saddle', B + 'total_moving_time', 'h', '#2dd4bf'),
        life('Everests', B + 'everests_climbed', '', '#34d399', 1),
        life('Laps of Earth', B + 'laps_of_earth', '', '#7dd3fc', 2),
        life('Burned', B + 'total_calories', 'kcal', '#fb923c'),
        badges,
    ]}
    return {'title': 'Riding', 'path': 'riding', 'icon': 'mdi:bike', 'type': 'sections', 'max_columns': 3, 'sections': [s1, s3, s3b, s2, s4]}


def build():
    views = [view_today(), view_body(), view_running(), view_riding()]
    for v in views:
        v['theme'] = THEME
    return {'title': 'Rob', 'views': views}


if __name__ == '__main__':
    import json
    print(json.dumps(build(), indent=1))
