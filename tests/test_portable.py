"""The Windows build must run the same code as Linux: no strftime directives that only Linux understands."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAD = re.compile(r"%-[dmHIMSjyYe]")           # glibc-only "no padding" flags: ValueError ('Invalid format string') on Windows


def test_no_linux_only_strftime_in_the_app():
    offenders = []
    for f in list(ROOT.glob('*.py')) + list((ROOT / 'routes').glob('*.py')) + list((ROOT / 'services').glob('*.py')) + list((ROOT / 'templates').glob('*.html')):
        for n, line in enumerate(f.read_text(encoding='utf-8', errors='ignore').splitlines(), 1):
            if BAD.search(line) and 'no strftime' not in line and 'never use' not in line:
                offenders.append(f'{f.relative_to(ROOT)}:{n}: {line.strip()[:100]}')
    assert not offenders, 'use services/dates.py instead:\n' + '\n'.join(offenders)


def test_day_month_helpers():
    from datetime import date
    from services.dates import day_month, day_month_year
    assert day_month(date(2026, 5, 5)) == '5 May' and day_month_year(date(2026, 10, 15)) == '15 Oct 2026'
