from datetime import date, timedelta

import pytest

from services.weight_trend import (
    compute_trend,
    format_weight,
    kg_to_stone_lb,
    parse_backfill_rows,
    parse_log_date,
    parse_weight_value,
    stone_lb_to_kg,
    stone_milestones,
)


# ── Unit conversion ──────────────────────────────────────────────────────

def test_stone_lb_roundtrip():
    kg = stone_lb_to_kg(16, 4)
    stone, lb = kg_to_stone_lb(kg)
    assert stone == 16
    assert lb == pytest.approx(4, abs=0.01)


def test_kg_to_stone_lb_known_value():
    # 100kg = 220.462lb = 15st 10.462lb
    stone, lb = kg_to_stone_lb(100)
    assert stone == 15
    assert lb == pytest.approx(10.462, abs=0.01)


def test_format_weight_stlb_one_decimal():
    kg = stone_lb_to_kg(11, 6.44)
    assert format_weight(kg, 'stlb') == '11st 6.4lb'


def test_format_weight_stlb_rounds_up_lb_carries_stone():
    # 13.96 rounds to 14.0 -> should carry to next stone
    kg = stone_lb_to_kg(10, 13.96)
    assert format_weight(kg, 'stlb') == '11st 0.0lb'


def test_format_weight_kg():
    assert format_weight(82.345, 'kg') == '82.3 kg'


# ── parse_weight_value ───────────────────────────────────────────────────

@pytest.mark.parametrize('text,expected_kg', [
    ('103.4', 103.4),
    ('103.4kg', 103.4),
    ('103.4 kg', 103.4),
    ('228lb', 228 / 2.2046226218488),
    ('16st 4lb', stone_lb_to_kg(16, 4)),
    ('16st4lb', stone_lb_to_kg(16, 4)),
    ('16 st 4 lb', stone_lb_to_kg(16, 4)),
    ('16st', stone_lb_to_kg(16, 0)),
])
def test_parse_weight_value_valid(text, expected_kg):
    assert parse_weight_value(text) == pytest.approx(expected_kg, abs=0.01)


@pytest.mark.parametrize('text', ['', 'banana', 'st lb', '16 stone'])
def test_parse_weight_value_invalid(text):
    with pytest.raises(ValueError):
        parse_weight_value(text)


# ── parse_log_date ───────────────────────────────────────────────────────

def test_parse_log_date_valid():
    assert parse_log_date('2026-08-01') == date(2026, 8, 1)


@pytest.mark.parametrize('text', ['01/08/2026', '2026/08/01', 'yesterday', ''])
def test_parse_log_date_invalid(text):
    with pytest.raises(ValueError):
        parse_log_date(text)


# ── parse_backfill_rows ──────────────────────────────────────────────────

def test_parse_backfill_rows_valid_mixed_units():
    text = '2026-08-01, 16st 4lb\n2026-08-02, 103.4\n'
    rows = parse_backfill_rows(text)
    assert len(rows) == 2
    assert all('error' not in r for r in rows)
    assert rows[0]['date'] == '2026-08-01'
    assert rows[0]['weightKg'] == pytest.approx(stone_lb_to_kg(16, 4), abs=0.01)
    assert rows[1]['weightKg'] == pytest.approx(103.4, abs=0.01)


def test_parse_backfill_rows_invalid_date():
    rows = parse_backfill_rows('not-a-date, 103.4')
    assert rows[0]['error']


def test_parse_backfill_rows_invalid_weight():
    rows = parse_backfill_rows('2026-08-01, not-a-weight')
    assert rows[0]['error']


def test_parse_backfill_rows_out_of_range_weight():
    rows = parse_backfill_rows('2026-08-01, 900')
    assert rows[0]['error']


def test_parse_backfill_rows_duplicate_dates_flagged():
    text = '2026-08-01, 100\n2026-08-01, 101\n'
    rows = parse_backfill_rows(text)
    assert 'error' not in rows[0]
    assert 'Duplicate' in rows[1]['error']


def test_parse_backfill_rows_skips_blank_lines():
    rows = parse_backfill_rows('2026-08-01, 100\n\n\n2026-08-02, 101\n')
    assert len(rows) == 2


def test_parse_backfill_rows_malformed_line():
    rows = parse_backfill_rows('just one field')
    assert rows[0]['error']


# ── compute_trend (EMA) ──────────────────────────────────────────────────

def test_compute_trend_single_entry_seeds_flat():
    trend = compute_trend([(date(2026, 1, 1), 100.0)])
    assert trend == {date(2026, 1, 1): 100.0}


def test_compute_trend_daily_matches_formula():
    entries = [
        (date(2026, 1, 1), 100.0),
        (date(2026, 1, 2), 99.0),
        (date(2026, 1, 3), 101.0),
    ]
    trend = compute_trend(entries, alpha=0.1)
    t0 = 100.0
    t1 = 0.1 * 99.0 + 0.9 * t0
    t2 = 0.1 * 101.0 + 0.9 * t1
    assert trend[date(2026, 1, 1)] == pytest.approx(t0)
    assert trend[date(2026, 1, 2)] == pytest.approx(t1)
    assert trend[date(2026, 1, 3)] == pytest.approx(t2)


def test_compute_trend_interpolates_over_gap():
    # A week between readings: each missing day uses a linearly interpolated raw weight (Hacker's Diet /
    # TrendWeight method). Holding the previous trend instead left sparse weighers ~3 kg behind reality.
    entries = [(date(2026, 1, 1), 100.0), (date(2026, 1, 8), 98.0)]
    trend = compute_trend(entries, alpha=0.1)
    assert len(trend) == 8
    prev = 100.0
    for i in range(1, 8):
        raw = 100.0 + (98.0 - 100.0) * i / 7          # interpolated (the 8th day is the real reading)
        prev = 0.1 * raw + 0.9 * prev
        assert trend[date(2026, 1, 1 + i)] == pytest.approx(prev)


def test_compute_trend_sparse_data_tracks_real_loss():
    # Weighing every 5 days while losing steadily: the trend must stay close to the readings, not ~3 kg behind.
    entries = [(date(2026, 1, 1) + timedelta(days=5 * i), 120.0 - 1.0 * i) for i in range(10)]
    trend = compute_trend(entries, alpha=0.1)
    last = entries[-1]
    assert trend[last[0]] - last[1] < 2.0


def test_compute_trend_holds_last_reading_beyond_last_entry():
    # After the latest weigh-in there is nothing to interpolate to, so the last reading is held and the
    # trend keeps converging on it rather than freezing where it was.
    entries = [(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 90.0)]
    trend = compute_trend(entries, alpha=0.1, end_date=date(2026, 1, 5))
    t2 = trend[date(2026, 1, 2)]
    assert trend[date(2026, 1, 3)] == pytest.approx(0.1 * 90.0 + 0.9 * t2)
    assert trend[date(2026, 1, 5)] < trend[date(2026, 1, 4)] < t2


def test_compute_trend_empty():
    assert compute_trend([]) == {}


# ── stone_milestones ─────────────────────────────────────────────────────

def test_stone_milestones_basic():
    start = stone_lb_to_kg(16, 0)
    low = stone_lb_to_kg(14, 0)
    lines = stone_milestones(start, low)
    # Should include 15st and 14st marks, not 13st
    assert len(lines) == 2
    assert lines[0] == pytest.approx(stone_lb_to_kg(15, 0), abs=0.01)
    assert lines[1] == pytest.approx(stone_lb_to_kg(14, 0), abs=0.01)


def test_stone_milestones_none_start():
    assert stone_milestones(None, 50) == []
