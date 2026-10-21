"""rescore_angles.py - cue angle for all four configurations, honouring transform="rotate(theta cx cy)".

My earlier detector read only untransformed <rect> and <line>, so a rect rotated with a transform scored 0 degrees.
"""
import json
import math
import re
from pathlib import Path

FILES = [
    ('1 bare spec', 'hard/pooltable-qwen3-coder_30b.svg'),
    ('2 + worked example', 'hard/pooltable-qwen3-coder_30b-example.svg'),
    ('3 + kit line_at', 'hard/pooltable-kit-try1.svg'),
    ('4 + rects banned (try2)', 'hard/pooltable-kit-lines-try2.svg'),
]


def cue_angle(text):
    best = None
    for m in re.finditer(r'<(rect|line)([^>]*)>', text):
        tag, attrs = m.group(1), m.group(2)
        g = lambda k: float(re.search(k + r'="(-?[\d.]+)"', attrs).group(1)) if re.search(k + r'="(-?[\d.]+)"', attrs) else None
        rot = re.search(r'rotate\(\s*(-?[\d.]+)', attrs)
        rot = float(rot.group(1)) if rot else 0.0
        if tag == 'rect':
            w, h = g('width'), g('height')
            if w is None or h is None or w < 80 or h > 24:
                continue
            length, angle = max(w, h), (rot if w >= h else rot + 90)
        else:
            x1, y1, x2, y2 = g('x1'), g('y1'), g('x2'), g('y2')
            if None in (x1, y1, x2, y2):
                continue
            length = math.hypot(x2 - x1, y2 - y1)
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) + rot
            if length < 80:
                continue
        off = abs(((angle + 45) % 90) - 45)
        if best is None or (length > best[1] and off > 2) or (best and off > 2 and best[2] <= 2):
            if off > 2:
                best = (angle, length, off)
    return None if not best else round(best[0], 1)


rows = []
for label, path in FILES:
    text = Path(path).read_text(encoding='utf-8')
    a = cue_angle(text)
    rows.append({'config': label, 'cue_deg': a, 'angled': a is not None})
    print('%-24s cue %s  ->  %s' % (label, ('%+.1f deg' % a) if a is not None else 'none', 'ANGLED' if a is not None else 'horizontal'))
Path('result-hard-angles-rescored.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
print('\nconfigurations producing an angled cue: %d/%d' % (sum(r['angled'] for r in rows), len(rows)))
