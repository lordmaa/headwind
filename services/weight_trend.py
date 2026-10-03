"""Pure weight-tracking math: unit conversion, EMA trend, backfill parsing.

No Flask/DB imports here — keep this testable in isolation.
"""
import re
from datetime import date, timedelta

KG_PER_STONE = 6.35029318
LB_PER_KG = 2.2046226218488

_STONE_LB_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*st\.?\s*(\d+(?:\.\d+)?)\s*lb\.?$', re.IGNORECASE)
_STONE_ONLY_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*st\.?$', re.IGNORECASE)
_LB_ONLY_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*lb\.?$', re.IGNORECASE)
_KG_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*kg$', re.IGNORECASE)
_PLAIN_RE = re.compile(r'^(\d+(?:\.\d+)?)$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

MIN_SANE_KG = 20
MAX_SANE_KG = 300


def kg_to_lb(kg):
    return kg * LB_PER_KG


def lb_to_kg(lb):
    return lb / LB_PER_KG


def kg_to_stone_lb(kg):
    """Returns (stone:int, lb:float) — lb is the remainder, not rounded."""
    total_lb = kg_to_lb(kg)
    stone = int(total_lb // 14)
    lb = total_lb - stone * 14
    return stone, lb


def stone_lb_to_kg(stone, lb):
    return (float(stone) * 14 + float(lb)) / LB_PER_KG


def format_weight(kg, unit='stlb'):
    """One decimal place throughout: lb for stlb, kg for kg."""
    if unit == 'kg':
        return f'{kg:.1f} kg'
    stone, lb = kg_to_stone_lb(kg)
    lb_rounded = round(lb, 1)
    if lb_rounded >= 14:
        stone += 1
        lb_rounded = round(lb_rounded - 14, 1)
    return f'{stone}st {lb_rounded:.1f}lb'


def parse_weight_value(text):
    """Parse a single weight token — bare number is assumed kg."""
    t = (text or '').strip()
    m = _STONE_LB_RE.match(t)
    if m:
        return stone_lb_to_kg(float(m.group(1)), float(m.group(2)))
    m = _STONE_ONLY_RE.match(t)
    if m:
        return stone_lb_to_kg(float(m.group(1)), 0)
    m = _LB_ONLY_RE.match(t)
    if m:
        return lb_to_kg(float(m.group(1)))
    m = _KG_RE.match(t)
    if m:
        return float(m.group(1))
    m = _PLAIN_RE.match(t)
    if m:
        return float(m.group(1))
    raise ValueError(f"Can't parse weight: {text!r}")


def parse_log_date(text):
    t = (text or '').strip()
    if not _DATE_RE.match(t):
        raise ValueError(f'Date must be YYYY-MM-DD: {text!r}')
    return date.fromisoformat(t)


def parse_backfill_rows(text):
    """Parse pasted/CSV "date, weight" rows. Returns a list of dicts, one per
    non-blank input line, each either populated with date/weightKg or an error
    string. Never raises — bad rows are just flagged for the caller to show."""
    rows = []
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(',', 1)]
        row = {'line': i, 'raw': line}
        if len(parts) != 2 or not parts[0] or not parts[1]:
            row['error'] = 'Expected "date, weight"'
            rows.append(row)
            continue
        date_str, weight_str = parts
        try:
            d = parse_log_date(date_str)
            row['date'] = d.isoformat()
        except ValueError as e:
            row['error'] = str(e)
            rows.append(row)
            continue
        try:
            kg = parse_weight_value(weight_str)
            if not (MIN_SANE_KG <= kg <= MAX_SANE_KG):
                raise ValueError(f'{kg:.1f}kg is outside the expected range')
            row['weightKg'] = round(kg, 2)
        except ValueError as e:
            row['error'] = str(e)
        rows.append(row)

    seen = {}
    for row in rows:
        if row.get('error') or not row.get('date'):
            continue
        if row['date'] in seen:
            row['error'] = f"Duplicate date in this batch (also line {seen[row['date']]})"
        else:
            seen[row['date']] = row['line']
    return rows


def compute_trend(entries, alpha=0.1, end_date=None):
    """entries: iterable of (date, weightKg). Returns {date: trendKg} for every
    day from the first entry to end_date (default: last entry's date).

    Days between weigh-ins use a linearly interpolated raw weight, and days after the
    latest weigh-in hold that reading, so the trend keeps converging on sparse data."""
    entries = sorted(entries, key=lambda e: e[0])
    if not entries:
        return {}
    entry_map = dict(entries)
    first_date = entries[0][0]
    last_date = end_date or entries[-1][0]
    if last_date < first_date:
        last_date = first_date

    # Interpolate a raw weight for every day between weigh-ins (Hacker's Diet / TrendWeight method). Without this,
    # sparse weighing (one reading every few days) leaves the trend badly behind: it only moves on weigh-in days.
    days = (last_date - first_date).days
    known = [(e[0], e[1]) for e in entries if e[0] <= last_date]
    raw_by_day = {}
    for i, (d0, w0) in enumerate(known):
        raw_by_day[d0] = w0
        if i + 1 < len(known):
            d1, w1 = known[i + 1]
            span = (d1 - d0).days
            for k in range(1, span):
                raw_by_day[d0 + timedelta(days=k)] = w0 + (w1 - w0) * k / span
    last_known_date, last_known_w = known[-1]

    trend = {first_date: entry_map[first_date]}
    prev_trend = trend[first_date]
    d = first_date + timedelta(days=1)
    while d <= last_date:
        # after the last weigh-in there is nothing to interpolate to: hold the last reading so the trend keeps converging
        raw = raw_by_day.get(d, last_known_w if d > last_known_date else prev_trend)
        prev_trend = alpha * raw + (1 - alpha) * prev_trend
        trend[d] = prev_trend
        d += timedelta(days=1)
    return trend


def stone_milestones(start_kg, low_kg):
    """kg values at each whole stone below start_kg, down to low_kg."""
    if start_kg is None:
        return []
    milestones = []
    n = 1
    while True:
        val = start_kg - n * KG_PER_STONE
        if val < low_kg - 1e-9:
            break
        milestones.append(val)
        n += 1
    return milestones
