import gzip
import io

import pytest

from services.limits import LimitExceeded, bounded_lines, safe_gunzip


def test_gunzip_bomb_rejected():
    bomb = gzip.compress(b'\0' * (5 * 1024 * 1024))
    assert len(safe_gunzip(bomb, limit=10 * 1024 * 1024)) == 5 * 1024 * 1024
    with pytest.raises(LimitExceeded):
        safe_gunzip(bomb, limit=1024 * 1024)


def test_feed_budget_enforced():
    lines = io.BytesIO(b'{"a":1}\n' * 100)
    assert len(list(bounded_lines(lines, 10_000, 1000, 60))) == 100
    with pytest.raises(LimitExceeded):
        list(bounded_lines(io.BytesIO(b'{"a":1}\n' * 100), 10_000, 50, 60))   # line budget
    with pytest.raises(LimitExceeded):
        list(bounded_lines(io.BytesIO(b'x' * 50_000), 10_000, 50, 60, max_line=100))  # one giant line
