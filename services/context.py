from database import query_db
from routes.riders import _compute_badges


def _tags(dist_mi, elev_ft, avg_power, avg_hr):
    tags = []
    if dist_mi >= 30:       tags.append('long_ride')
    elif dist_mi <= 10:     tags.append('short_ride')
    if elev_ft >= 3000:     tags.append('big_climb')
    elif elev_ft >= 1500:   tags.append('hilly')
    if avg_power and avg_power >= 210: tags.append('hard_power')
    if avg_hr and avg_hr >= 160:       tags.append('high_hr')
    return ','.join(tags)


def save_ride_memory(activity, ai_summary):
    from database import get_db
    import json

    dist_mi  = float(activity['distance'] or 0) / 1609.344
    elev_ft  = float(activity['totalElevationGain'] or 0) * 3.28084
    spd_mph  = float(activity['averageSpeed'] or 0) * 2.23694
    power    = float(activity['averageWatts'] or 0) or None
    norm_pw  = float(activity['weightedAvgWatts'] or 0) or None
    hr       = float(activity['averageHeartrate'] or 0) or None
    cals     = float(activity['calories'] or 0) or None

    tags = _tags(dist_mi, elev_ft, power, hr)

    db = get_db()
    db.execute('''
        INSERT INTO RideMemory
            (rideId, rideDate, rideName, distanceMi, movingTime, elevationFt,
             avgSpeedMph, avgPower, normPower, avgHR, calories, aiSummary, tags, updatedAt)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(rideId) DO UPDATE SET
            aiSummary=excluded.aiSummary,
            tags=excluded.tags,
            updatedAt=datetime('now')
    ''', [
        str(activity['id']),
        str(activity['startDateLocal'])[:10],
        activity['name'],
        round(dist_mi, 2),
        activity['movingTime'],
        round(elev_ft),
        round(spd_mph, 2),
        round(power) if power else None,
        round(norm_pw) if norm_pw else None,
        round(hr, 1) if hr else None,
        round(cals) if cals else None,
        ai_summary,
        tags,
    ])
    db.commit()


def _fmt_dur(secs):
    m, s = divmod(int(abs(secs)), 60)
    h, m = divmod(m, 60)
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


def _fmt_date(d):
    try:
        from datetime import datetime
        dt = datetime.strptime(str(d)[:10], '%Y-%m-%d')
        return dt.strftime('%-d %b %Y')
    except Exception:
        return str(d)[:10]


def _build_segment_context(activity_id):
    efforts = query_db('''
        SELECT e.elapsedSecs, e.isPR, e.activityDate,
               s.id AS seg_id, s.name AS seg_name, s.distanceM
        FROM SegmentEffort e
        JOIN Segment s ON s.id = e.segmentId
        WHERE e.activityId = ?
        ORDER BY s.name
    ''', [activity_id])

    if not efforts:
        return ''

    lines = ['Segments ridden today:']

    for eff in efforts:
        seg_id   = eff['seg_id']
        seg_name = eff['seg_name']
        this_secs = eff['elapsedSecs']
        dist_m   = eff['distanceM'] or 0

        # Full leaderboard for this segment
        board = query_db('''
            SELECT elapsedSecs, activityDate
            FROM SegmentEffort
            WHERE segmentId = ?
            ORDER BY elapsedSecs ASC
        ''', [seg_id])

        total     = len(board)
        rank      = next((i + 1 for i, r in enumerate(board) if r['elapsedSecs'] == this_secs), total)
        best_secs = board[0]['elapsedSecs'] if board else this_secs
        best_date = board[0]['activityDate'] if board else eff['activityDate']
        avg_secs  = sum(r['elapsedSecs'] for r in board) / total if total else this_secs

        gap_to_pr  = this_secs - best_secs  # 0 if this IS the PR
        is_pr      = gap_to_pr == 0

        # Last 5 efforts on this segment (chronological, excluding today)
        recent = query_db('''
            SELECT elapsedSecs, activityDate
            FROM SegmentEffort
            WHERE segmentId = ? AND activityId != ?
            ORDER BY activityDate DESC LIMIT 5
        ''', [seg_id, activity_id])

        if is_pr:
            status = f'PERSONAL BEST'
            if len(board) > 1:
                prev_best_secs = board[1]['elapsedSecs']
                status += (
                    f' (prev best: {_fmt_dur(prev_best_secs)} on {_fmt_date(board[1]["activityDate"])},'
                    f' improved by {_fmt_dur(prev_best_secs - this_secs)})'
                )
        else:
            status = f'#{rank} of {total} (gap to PR: {_fmt_dur(gap_to_pr)})'

        line = f'- {seg_name}: {_fmt_dur(this_secs)} — {status}'
        line += f'\n  All-time: {total} efforts, avg {_fmt_dur(avg_secs)}, best {_fmt_dur(best_secs)} ({best_date})'

        if recent:
            recent_times = [_fmt_dur(r['elapsedSecs']) for r in recent]
            recent_secs  = [r['elapsedSecs'] for r in recent]
            if len(recent_secs) >= 3:
                first_half  = sum(recent_secs[len(recent_secs)//2:]) / max(1, len(recent_secs) - len(recent_secs)//2)
                second_half = sum(recent_secs[:len(recent_secs)//2]) / max(1, len(recent_secs)//2)
                diff = second_half - first_half
                if abs(diff) < 0.03 * first_half:
                    trend = 'stable'
                elif diff < 0:
                    trend = 'getting faster'
                else:
                    trend = 'getting slower'
            else:
                trend = 'insufficient history'
            line += f'\n  Last {len(recent)} efforts (newest first): {", ".join(recent_times)} — {trend}'

        lines.append(line)

    # Broader 6-month segment trends — all segments the rider uses regularly
    six_months_ago = query_db("SELECT date('now', '-180 days') as d", one=True)['d']
    regular_segs = query_db('''
        SELECT s.id, s.name, COUNT(*) as cnt
        FROM SegmentEffort e
        JOIN Segment s ON s.id = e.segmentId
        WHERE e.activityDate >= ?
        GROUP BY s.id
        HAVING cnt >= 3
        ORDER BY cnt DESC
    ''', [six_months_ago])

    if regular_segs:
        lines.append('')
        lines.append('Training road trends (last 6 months, segments with 3+ efforts):')
        for seg in regular_segs:
            monthly = query_db('''
                SELECT strftime('%Y-%m', activityDate) as ym, AVG(elapsedSecs) as avg_secs
                FROM SegmentEffort
                WHERE segmentId = ? AND activityDate >= ?
                GROUP BY ym ORDER BY ym
            ''', [seg['id'], six_months_ago])

            if len(monthly) >= 2:
                first_avg = monthly[0]['avg_secs']
                last_avg  = monthly[-1]['avg_secs']
                delta     = last_avg - first_avg
                if abs(delta) < 0.03 * first_avg:
                    trend_str = 'consistent'
                elif delta < 0:
                    trend_str = f'improving ({abs(delta):.0f}s faster avg vs 6 months ago)'
                else:
                    trend_str = f'slower ({delta:.0f}s avg vs 6 months ago)'
                lines.append(
                    f'- {seg["name"]}: {seg["cnt"]} efforts, '
                    f'recent avg {_fmt_dur(last_avg)} — {trend_str}'
                )

    return '\n'.join(lines)


def _build_achievement_context(activity_id, activity_date):
    achievements = query_db('''
        SELECT e.distanceMi, e.elapsedSecs, e.avgSpeedMps
        FROM BestEffort e
        WHERE e.activityId = ?
    ''', [activity_id])

    if not achievements:
        return ''

    lines = ['Achievements set on this ride:']
    yr = str(activity_date)[:4]

    for ach in achievements:
        d_mi   = int(ach['distanceMi'])
        secs   = ach['elapsedSecs']
        mph    = round(float(ach['avgSpeedMps']) * 2.23694, 1) if ach['avgSpeedMps'] else 0

        # What was the previous best for this distance bracket?
        prev = query_db('''
            SELECT elapsedSecs, activityDate
            FROM BestEffort
            WHERE distanceMi = ? AND activityId != ?
            ORDER BY elapsedSecs ASC LIMIT 1
        ''', [ach['distanceMi'], activity_id], one=True)

        if prev:
            improvement = prev['elapsedSecs'] - secs
            lines.append(
                f'- Fastest {d_mi}mi: {_fmt_dur(secs)} at {mph}mph '
                f'(beat previous best of {_fmt_dur(prev["elapsedSecs"])} '
                f'from {_fmt_date(prev["activityDate"])} by {_fmt_dur(improvement)})'
            )
        else:
            lines.append(f'- Fastest {d_mi}mi: {_fmt_dur(secs)} at {mph}mph (first recorded effort at this distance)')

    return '\n'.join(lines)


def _build_weather_context(activity):
    """Return a single-line weather summary for the AI, or empty string."""
    summary = activity['weatherSummary'] if activity['weatherSummary'] else None
    if not summary:
        return ''

    parts = [summary]

    wind_kph = activity['weatherWindKph']
    gust_kph = activity['weatherGustKph']
    wind_rel = activity['weatherWindRel']
    rain_mm  = activity['weatherRainMm']
    temp_c   = activity['weatherTempC']

    # Flag notably strong wind
    if wind_kph and wind_kph >= 30:
        parts.append('strong wind conditions')
    elif wind_kph and wind_kph >= 20:
        parts.append('moderate wind')

    # Flag notable gust spike
    if gust_kph and wind_kph and gust_kph >= wind_kph * 1.5 and gust_kph >= 30:
        parts.append(f'gusty (up to {gust_kph:.0f}kph)')

    # Wind relative to route
    if wind_rel and wind_rel not in ('calm', None):
        if wind_kph and wind_kph >= 15:
            parts.append(f'mostly {wind_rel}')

    # Rain
    if rain_mm and rain_mm >= 1.0:
        parts.append('wet conditions')

    # Temperature extremes
    if temp_c is not None:
        if temp_c <= 5:
            parts.append('cold')
        elif temp_c >= 28:
            parts.append('hot')

    # Deduplicate and return
    seen = set()
    unique = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return ', '.join(unique)


def _build_other_training_context(rider_id, exclude_id):
    rows = query_db('''
        SELECT name, sportType, startDateLocal, distance, movingTime, totalElevationGain
        FROM Activity
        WHERE riderId = ?
          AND id != ?
          AND date(startDateLocal) >= date('now', '-30 days')
          AND lower(sportType) NOT LIKE '%ride%'
          AND lower(sportType) NOT LIKE '%cycling%'
          AND distance > 0
        ORDER BY startDateLocal DESC
    ''', [rider_id, exclude_id])

    if not rows:
        return ''

    lines = ['Other training (last 30 days):']
    for r in rows:
        dist_mi = float(r['distance'] or 0) / 1609.344
        dur_min = int((r['movingTime'] or 0) / 60)
        elev_ft = int(float(r['totalElevationGain'] or 0) * 3.28084)
        sport   = r['sportType'] or 'Activity'
        parts   = [f'{dist_mi:.1f}mi', f'{dur_min}min']
        if elev_ft >= 50:
            parts.append(f'{elev_ft}ft gain')
        lines.append(f'  {_fmt_date(str(r["startDateLocal"])[:10])}: {sport} — {r["name"]} — {", ".join(parts)}')

    return '\n'.join(lines)


def _build_garmin_context(ride_date):
    """Return a recovery/readiness block from GarminDaily for the 3 days up to and including ride_date."""
    rows = query_db('''
        SELECT date, restingHR, hrv, hrvBalanced, sleepHours, sleepScore, bodyBattery, steps, stressScore
        FROM GarminDaily
        WHERE date <= ? AND date >= date(?, '-3 days')
        ORDER BY date DESC
    ''', [ride_date, ride_date])

    if not rows:
        return ''

    lines = ['Garmin recovery data (up to ride day):']
    for r in rows:
        parts = []
        if r['restingHR']:
            parts.append(f'resting HR {r["restingHR"]}bpm')
        if r['hrv']:
            status = 'balanced' if r['hrvBalanced'] else 'unbalanced'
            parts.append(f'HRV {r["hrv"]} ({status})')
        if r['sleepHours']:
            parts.append(f'sleep {r["sleepHours"]}h')
            if r['sleepScore']:
                parts.append(f'score {r["sleepScore"]}')
        if r['bodyBattery']:
            parts.append(f'body battery {r["bodyBattery"]}%')
        if r['steps']:
            parts.append(f'steps {r["steps"]:,}')
        if r['stressScore']:
            parts.append(f'stress {r["stressScore"]}')
        if parts:
            lines.append(f'  {_fmt_date(r["date"])}: {", ".join(parts)}')

    return '\n'.join(lines) if len(lines) > 1 else ''


def _build_badge_context(rid, ride_id):
    totals = query_db(
        'SELECT COUNT(*) as rides, SUM(distance) as dist, SUM(totalElevationGain) as elev '
        'FROM Activity WHERE riderId=?', [rid], one=True)
    if not totals:
        return ''
    categories = _compute_badges(rid, totals)
    total_earned = sum(1 for cat in categories for b in cat['badges'] if b['earned'])
    total_badges = sum(len(cat['badges']) for cat in categories)
    unlocked = [
        f'  - {b["icon"]} {b["name"]}: {b["desc"]}'
        for cat in categories for b in cat['badges']
        if b.get('ride_id') and str(b['ride_id']) == str(ride_id)
    ]
    lines = [f'Trophy case: {total_earned}/{total_badges} badges earned']
    if unlocked:
        lines.append('Milestones unlocked on this ride:')
        lines.extend(unlocked)
    return '\n'.join(lines)


def _build_comeback_progress(ride_id, ride_date, cur_spd_mph=None, cur_pwr_w=None):
    """Monthly averages since comeback start, to surface gains clearly."""
    from datetime import datetime
    current_ym = datetime.now().strftime('%Y-%m')

    rows = query_db('''
        SELECT strftime('%Y-%m', rideDate) as ym,
               COUNT(*) as rides,
               AVG(distanceMi) as avg_dist,
               AVG(avgSpeedMph) as avg_spd,
               AVG(avgPower) as avg_pwr,
               MAX(avgSpeedMph) as best_spd,
               MAX(avgPower) as best_pwr
        FROM RideMemory
        WHERE rideId != ?
          AND rideDate >= '2026-01-01'
          AND distanceMi > 2
          AND NOT (rideName = 'Ride Activity' AND avgPower IS NULL)
        GROUP BY ym
        ORDER BY ym
    ''', [ride_id])

    if len(rows) < 2:
        return ''

    lines = ['Comeback progress by month (Jan 2026 onwards — read the OVERALL arc, not month-to-month noise):']
    for r in rows:
        pwr = f"avg {r['avg_pwr']:.0f}W, best {r['best_pwr']:.0f}W" if r['avg_pwr'] else 'no power data'
        partial_note = ' ⚠️ partial month' if r['ym'] == current_ym else ''
        lines.append(
            f"  {r['ym']}{partial_note}: {r['rides']} rides | "
            f"avg {r['avg_spd']:.1f}mph (best {r['best_spd']:.1f}mph) | {pwr}"
        )

    # Add today's ride vs current month avg for context
    cur_month_row = next((r for r in rows if r['ym'] == current_ym), None)
    if cur_month_row and (cur_spd_mph or cur_pwr_w):
        parts = []
        if cur_spd_mph:
            direction = 'above' if cur_spd_mph > cur_month_row['avg_spd'] else 'below'
            parts.append(f'{cur_spd_mph:.1f}mph ({direction} {current_ym} avg of {cur_month_row["avg_spd"]:.1f}mph)')
        if cur_pwr_w and cur_month_row['avg_pwr']:
            direction = 'above' if cur_pwr_w > cur_month_row['avg_pwr'] else 'below'
            parts.append(f'{cur_pwr_w:.0f}W ({direction} {current_ym} avg of {cur_month_row["avg_pwr"]:.0f}W)')
        if parts:
            lines.append(f"  Today's ride: {', '.join(parts)}")

    # Overall delta: first month vs most recent COMPLETE month (not partial)
    complete_rows = [r for r in rows if r['ym'] != current_ym]
    if not complete_rows:
        complete_rows = rows
    first, last_complete = rows[0], complete_rows[-1]
    spd_gain = last_complete['avg_spd'] - first['avg_spd'] if first['avg_spd'] else 0
    if spd_gain > 0.2 and first['avg_spd']:
        pct = spd_gain / first['avg_spd'] * 100
        lines.append(f'  → Overall speed up {pct:.0f}% from {first["ym"]} to {last_complete["ym"]} (ignore partial current month)')
    if first['avg_pwr'] and last_complete['avg_pwr']:
        pwr_gain = last_complete['avg_pwr'] - first['avg_pwr']
        if pwr_gain > 5:
            pct = pwr_gain / first['avg_pwr'] * 100
            lines.append(f'  → Overall power up {pct:.0f}% from {first["ym"]} to {last_complete["ym"]}')

    return '\n'.join(lines)


def build_context(activity):
    ride_id  = str(activity['id'])
    dist_mi  = float(activity['distance'] or 0) / 1609.344
    elev_ft  = float(activity['totalElevationGain'] or 0) * 3.28084

    # Last 50 meaningful rides — exclude "Ride Activity" duplicates with no power
    recent = query_db('''
        SELECT * FROM RideMemory
        WHERE rideId != ?
          AND distanceMi > 0
          AND NOT (rideName = 'Ride Activity' AND avgPower IS NULL)
        ORDER BY rideDate DESC LIMIT 20
    ''', [ride_id])

    comparable = query_db('''
        SELECT * FROM RideMemory
        WHERE rideId != ?
          AND distanceMi BETWEEN ? AND ?
          AND elevationFt BETWEEN ? AND ?
          AND distanceMi > 0
          AND NOT (rideName = 'Ride Activity' AND avgPower IS NULL)
        ORDER BY rideDate DESC LIMIT 5
    ''', [
        ride_id,
        dist_mi * 0.7, dist_mi * 1.3,
        max(0, elev_ft * 0.6), elev_ft * 1.4,
    ])

    month = query_db('''
        SELECT * FROM RideMemory
        WHERE rideId != ?
          AND rideDate >= date('now', '-30 days')
          AND distanceMi > 0
          AND NOT (rideName = 'Ride Activity' AND avgPower IS NULL)
        ORDER BY rideDate DESC
    ''', [ride_id])

    lines = []

    # Comeback progress FIRST — most important signal for a returning rider
    cur_spd = float(activity['averageSpeed'] or 0) * 2.23694 or None
    cur_pwr = float(activity['averageWatts'] or 0) or None
    comeback_ctx = _build_comeback_progress(
        ride_id, str(activity['startDateLocal'])[:10],
        round(cur_spd, 1) if cur_spd else None,
        round(cur_pwr) if cur_pwr else None,
    )
    if comeback_ctx:
        lines.append(comeback_ctx)
        lines.append('')

    if recent:
        lines.append(f'Ride history (last {len(recent)} rides):')
        lines.append('Date       | Dist   | Speed  | Elev   | Power | HR   | Name')
        for r in recent:
            pwr = f"{r['avgPower']:.0f}W" if r['avgPower'] else '—'
            hr  = f"{r['avgHR']:.0f}"     if r['avgHR']    else '—'
            lines.append(
                f"{r['rideDate']} | {r['distanceMi']:>5.1f}mi | "
                f"{r['avgSpeedMph']:>5.1f}mph | {r['elevationFt']:>5.0f}ft | "
                f"{pwr:>5} | {hr:>4} | {r['rideName']}"
            )
        lines.append('')

    if comparable:
        lines.append('Comparable rides (similar distance & elevation):')
        for r in comparable:
            pwr = f"{r['avgPower']:.0f}W" if r['avgPower'] else '—'
            lines.append(
                f"  {r['rideDate']}: {r['distanceMi']:.1f}mi, "
                f"{r['avgSpeedMph']:.1f}mph, {r['elevationFt']:.0f}ft climb, {pwr}"
            )
        lines.append('')

    if month:
        dists  = [r['distanceMi']  for r in month]
        speeds = [r['avgSpeedMph'] for r in month]
        elevs  = [r['elevationFt'] for r in month]
        powers = [r['avgPower'] for r in month if r['avgPower']]

        def trend(vals):
            if len(vals) < 3: return 'insufficient data'
            first_half = sum(vals[:len(vals)//2]) / max(1, len(vals)//2)
            second_half = sum(vals[len(vals)//2:]) / max(1, len(vals) - len(vals)//2)
            diff = second_half - first_half
            if abs(diff) < 0.05 * first_half: return 'flat'
            return 'trending up' if diff > 0 else 'trending down'

        lines.append(f'30-day summary ({len(month)} rides):')
        lines.append(f'  Distance : avg {sum(dists)/len(dists):.1f}mi, best {max(dists):.1f}mi, {trend(dists)}')
        lines.append(f'  Elevation: avg {sum(elevs)/len(elevs):.0f}ft, best {max(elevs):.0f}ft')
        lines.append(f'  Speed    : avg {sum(speeds)/len(speeds):.1f}mph, best {max(speeds):.1f}mph, {trend(speeds)}')
        if powers:
            lines.append(f'  Power    : avg {sum(powers)/len(powers):.0f}W, best {max(powers):.0f}W, {trend(powers)}')
        last3 = [r['distanceMi'] for r in month[:3]]
        lines.append(f'  Last 3 rides: {", ".join(f"{d:.1f}mi" for d in last3)}')
        lines.append('')

    if not recent and not month:
        lines.append('No previous rides in memory yet — this may be one of the first analysed rides.')

    garmin_ctx = _build_garmin_context(str(activity['startDateLocal'])[:10])
    if garmin_ctx:
        lines.append('')
        lines.append(garmin_ctx)

    other_ctx = _build_other_training_context(activity['riderId'], ride_id)
    if other_ctx:
        lines.append('')
        lines.append(other_ctx)

    seg_ctx = _build_segment_context(ride_id)
    if seg_ctx:
        lines.append('')
        lines.append(seg_ctx)

    ach_ctx = _build_achievement_context(ride_id, str(activity['startDateLocal'])[:10])
    if ach_ctx:
        lines.append('')
        lines.append(ach_ctx)

    if activity['riderId']:
        badge_ctx = _build_badge_context(activity['riderId'], ride_id)
        if badge_ctx:
            lines.append('')
            lines.append(badge_ctx)

    return '\n'.join(lines)


def get_weather_line(activity):
    """Return a concise weather line for embedding in the AI prompt, or None."""
    return _build_weather_context(activity) or None


def build_comparison_receipts(activity):
    """Return specific before/after comparison receipts for AI coaching."""
    ride_id   = str(activity['id'])
    ride_date = str(activity['startDateLocal'])[:10]
    dist_mi   = float(activity['distance'] or 0) / 1609.344
    elev_ft   = float(activity['totalElevationGain'] or 0) * 3.28084
    spd_mph   = float(activity['averageSpeed'] or 0) * 2.23694

    receipts = []

    current_efforts = query_db('''
        SELECT e.elapsedSecs, s.id AS seg_id, s.name AS seg_name
        FROM SegmentEffort e
        JOIN Segment s ON s.id = e.segmentId
        WHERE e.activityId = ?
        ORDER BY s.name
    ''', [ride_id])

    for eff in current_efforts:
        seg_id   = eff['seg_id']
        seg_name = eff['seg_name']
        cur_secs = eff['elapsedSecs']

        prev_best  = query_db('''
            SELECT elapsedSecs, activityDate FROM SegmentEffort
            WHERE segmentId = ? AND activityId != ?
            ORDER BY elapsedSecs ASC LIMIT 1
        ''', [seg_id, ride_id], one=True)

        first_eff = query_db('''
            SELECT elapsedSecs, activityDate FROM SegmentEffort
            WHERE segmentId = ? AND activityId != ?
            ORDER BY activityDate ASC LIMIT 1
        ''', [seg_id, ride_id], one=True)

        recent_eff = query_db('''
            SELECT elapsedSecs, activityDate FROM SegmentEffort
            WHERE segmentId = ? AND activityId != ?
              AND activityDate BETWEEN date(?, '-56 days') AND date(?, '-14 days')
            ORDER BY activityDate DESC LIMIT 1
        ''', [seg_id, ride_id, ride_date, ride_date], one=True)

        def _pr_age_years(date_str):
            try:
                from datetime import datetime
                d = datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
                return (datetime.now() - d).days // 365
            except Exception:
                return 0

        def _make(comp_type, row):
            if not row:
                return None
            prev_secs = row['elapsedSecs']
            delta_s   = prev_secs - cur_secs  # positive = today is faster
            delta_pct = abs(delta_s) / prev_secs * 100 if prev_secs else 0
            if delta_s > 0:
                delta_str = f'{_fmt_dur(delta_s)} faster'
            elif delta_s < 0:
                delta_str = f'{_fmt_dur(abs(delta_s))} slower'
            else:
                delta_str = 'identical time'
            age_yrs = _pr_age_years(row['activityDate'])
            historical = age_yrs >= 2
            if comp_type == 'previous_best':
                if historical:
                    hint = f'historical best from {str(row["activityDate"])[:4]} — aspirational target, not a current-form benchmark'
                else:
                    hint = 'new personal best' if delta_s > 0 else 'just off the PR'
            elif comp_type == 'first_effort':
                hint = 'progression since first attempt'
            else:
                hint = 'recent form improving' if delta_s > 0 else 'slightly off recent pace'
            return {
                'type': 'segment',
                'comparison_type': comp_type,
                'segment': seg_name,
                'current': _fmt_dur(cur_secs),
                'previous': _fmt_dur(prev_secs),
                'previous_date': _fmt_date(row['activityDate']),
                'delta': delta_str,
                'delta_percent': f'{delta_pct:.1f}%',
                'delta_seconds': delta_s,
                'hint': hint,
                'historical': historical,
            }

        r_recent = _make('recent_effort', recent_eff)
        r_best   = _make('previous_best', prev_best)
        r_first  = _make('first_effort',  first_eff)

        if r_recent:
            receipts.append(r_recent)

        if r_best:
            # Skip if same row as recent effort
            if not recent_eff or prev_best['activityDate'] != recent_eff['activityDate']:
                receipts.append(r_best)

        if r_first and r_best:
            # Only add if first effort is genuinely different from the all-time best row
            if (first_eff['activityDate'] != prev_best['activityDate'] or
                    first_eff['elapsedSecs'] != prev_best['elapsedSecs']):
                receipts.append(r_first)
        elif r_first and not r_best:
            receipts.append(r_first)

    # --- Similar ride speed comparisons ---
    similar = query_db('''
        SELECT rideId, rideDate, rideName, distanceMi, avgSpeedMph, elevationFt, avgPower
        FROM RideMemory
        WHERE rideId != ?
          AND distanceMi BETWEEN ? AND ?
          AND elevationFt BETWEEN ? AND ?
          AND distanceMi > 0
        ORDER BY rideDate DESC LIMIT 3
    ''', [
        ride_id,
        dist_mi * 0.8, dist_mi * 1.2,
        max(0, elev_ft * 0.7), elev_ft * 1.4,
    ])

    for sim in similar:
        sim_spd = float(sim['avgSpeedMph'] or 0)
        if sim_spd == 0:
            continue
        spd_delta = spd_mph - sim_spd
        if abs(spd_delta) < 0.2:
            continue

        direction = 'faster' if spd_delta > 0 else 'slower'
        hint = f'speed {"up" if spd_delta > 0 else "down"} on comparable terrain'

        pwr_note = ''
        if activity['averageWatts'] and sim['avgPower']:
            pwr_d = float(activity['averageWatts']) - float(sim['avgPower'])
            if abs(pwr_d) < 5:
                pwr_note = ', same power output'
                if spd_delta > 0:
                    hint = 'faster pace for same power — efficiency gain'
            elif pwr_d < 0 and spd_delta > 0:
                pwr_note = f', {abs(pwr_d):.0f}W less power'
                hint = 'faster with less power — significant efficiency gain'
            elif pwr_d > 0 and spd_delta > 0:
                pwr_note = f', {abs(pwr_d):.0f}W more power'

        receipts.append({
            'type': 'similar_ride',
            'comparison_type': 'similar_ride',
            'previous_name': sim['rideName'],
            'previous_date': _fmt_date(str(sim['rideDate'])),
            'current_speed': f'{spd_mph:.1f}mph',
            'previous_speed': f'{sim_spd:.1f}mph',
            'current_elev': f'{elev_ft:.0f}ft',
            'previous_elev': f'{float(sim["elevationFt"]):.0f}ft',
            'delta': f'{abs(spd_delta):.1f}mph {direction}{pwr_note}',
            'hint': hint,
        })

    # Sort: recent improvements first, non-historical PRs next, historical last
    # Within each tier, bigger delta first
    def _seg_key(r):
        if r.get('historical'):
            return (2, -abs(r['delta_seconds']))
        if r['comparison_type'] == 'recent_effort':
            return (0, -abs(r['delta_seconds']))
        return (1, -abs(r['delta_seconds']))

    seg_r  = sorted([r for r in receipts if r['type'] == 'segment'], key=_seg_key)
    ride_r = [r for r in receipts if r['type'] == 'similar_ride']
    return (seg_r + ride_r)[:10]
