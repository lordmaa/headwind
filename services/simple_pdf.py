"""Tiny dependency-free PDF writer for plain monospaced text (the diary and activity exports).

No PDF library is installed on a Headwind box, and a text export needs nothing more than Courier on A4 pages, so this writes the
PDF 1.4 structure by hand. Lines starting with '\\x01' are drawn in bold; long lines are wrapped; text outside Windows-1252 becomes '?'.
"""
import textwrap

PAGE_W, PAGE_H = 595, 842
MARGIN = 40
FONT = 8.5
LEAD = 11
COLS = int((PAGE_W - 2 * MARGIN) / (FONT * 0.6))          # Courier is 0.6 em wide
ROWS = int((PAGE_H - 2 * MARGIN) / LEAD)


def _esc(s):
    s = s.encode('cp1252', 'replace').decode('latin-1')
    return s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _wrap(text):
    lines = []
    for raw in text.splitlines():
        bold = raw.startswith('\x01')
        raw = raw.lstrip('\x01').replace('\t', '    ').rstrip()
        indent = len(raw) - len(raw.lstrip(' '))
        parts = textwrap.wrap(raw, COLS, subsequent_indent=' ' * min(indent + 2, 12), drop_whitespace=False, replace_whitespace=False) if raw else ['']
        lines += [(p.rstrip(), bold) for p in parts or ['']]
    return lines


def text_to_pdf(text, title='Headwind export'):
    lines = _wrap(text)
    pages = [lines[i:i + ROWS] for i in range(0, len(lines), ROWS)] or [[('', False)]]
    objs = []                                                    # object bodies (bytes), ids start at 1

    def add(body):
        objs.append(body if isinstance(body, bytes) else body.encode('latin-1'))
        return len(objs)

    add('<< /Type /Catalog /Pages 2 0 R >>')                      # 1
    add('')                                                       # 2 placeholder for the page tree
    add('<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>')        # 3
    add('<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>')    # 4
    kids = []
    n = len(pages)
    for i, pg in enumerate(pages, 1):
        y = PAGE_H - MARGIN - FONT
        out = ['BT']
        cur = None
        for txt, bold in pg:
            f = '/F2' if bold else '/F1'
            if f != cur:
                out.append('%s %.1f Tf' % (f, FONT)); cur = f
            out.append('1 0 0 1 %d %.1f Tm (%s) Tj' % (MARGIN, y, _esc(txt)))
            y -= LEAD
        out.append('ET')
        out.append('BT /F1 7 Tf 1 0 0 1 %d 22 Tm (%s) Tj ET' % (MARGIN, _esc('%s   page %d of %d' % (title, i, n))))
        stream = '\n'.join(out).encode('latin-1')
        cid = add(b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream')
        pid = add('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>' % (PAGE_W, PAGE_H, cid))
        kids.append('%d 0 R' % pid)
    objs[1] = ('<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join(kids), len(kids))).encode('latin-1')
    info = add('<< /Title (%s) /Producer (Headwind) >>' % _esc(title))
    buf = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(len(buf))
        buf += b'%d 0 obj\n' % i + body + b'\nendobj\n'
    xref = len(buf)
    buf += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    for o in offs:
        buf += b'%010d 00000 n \n' % o
    buf += b'trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, info, xref)
    return bytes(buf)
