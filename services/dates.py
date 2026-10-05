"""Portable date formatting. The glibc-only "no leading zero" strftime flag works on Linux/macOS but raises 'Invalid format string' on Windows,
so it must never be used (tests/test_portable.py enforces that): these helpers give the same UK-style output everywhere."""


def day_month(d):
    """15 May"""
    return f"{d.day} {d:%b}"


def day_month_year(d):
    """15 May 2026"""
    return f"{d.day} {d:%b %Y}"
