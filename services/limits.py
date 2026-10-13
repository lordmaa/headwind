"""Resource bounds for untrusted input: decompression bombs, oversized zip members, runaway peer feeds."""
import gzip
import time
import zipfile

MAX_ACTIVITY_BYTES = 200 * 1024 ** 2   # one .fit/.gpx after decompression
MAX_ZIP_ENTRIES = 50_000


class LimitExceeded(Exception):
    pass


def safe_gunzip(data, limit=MAX_ACTIVITY_BYTES):
    out = gzip.GzipFile(fileobj=__import__('io').BytesIO(data)).read(limit + 1)
    if len(out) > limit:
        raise LimitExceeded(f'decompressed data exceeds {limit // 1024 // 1024} MB')
    return out


def safe_zip_read(zf, name, limit=MAX_ACTIVITY_BYTES):
    """zf.read() with a hard cap on actual decompressed bytes (not the header's claimed size)."""
    with zf.open(name) as f:
        out = f.read(limit + 1)
    if len(out) > limit:
        raise LimitExceeded(f'{name} exceeds {limit // 1024 // 1024} MB')
    return out


def check_zip(zf):
    if len(zf.infolist()) > MAX_ZIP_ENTRIES:
        raise LimitExceeded(f'archive has more than {MAX_ZIP_ENTRIES} files')


def bounded_lines(resp, max_bytes, max_lines, max_seconds, max_line=5 * 1024 ** 2):
    """Yield lines from a streaming HTTP response, raising LimitExceeded past any budget."""
    start, total, n = time.monotonic(), 0, 0
    while True:
        line = resp.readline(max_line + 1)
        if not line:
            return
        total += len(line)
        n += 1
        if len(line) > max_line or total > max_bytes or n > max_lines or time.monotonic() - start > max_seconds:
            raise LimitExceeded('feed exceeded its size/time budget')
        yield line
