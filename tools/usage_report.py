"""usage_report.py - count what was actually clicked. `python tools/usage_report.py [--days 14]`"""
import argparse
import collections
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=usage.MAX_DAYS)
    ap.add_argument('--top', type=int, default=25)
    a = ap.parse_args()
    rows = usage.lines(a.days)
    if not rows:
        print('no usage recorded yet')
        return 0
    by_action = collections.Counter(r.get('action') for r in rows)
    by_day = collections.Counter((r.get('t') or '')[:10] for r in rows)
    print('%d interactions over %d day(s), %s .. %s\n' % (len(rows), len(by_day), min(by_day), max(by_day)))
    print('%-26s %6s' % ('action', 'count'))
    for action, count in by_action.most_common(a.top):
        print('%-26s %6d' % (action, count))
    return 0


if __name__ == '__main__':
    sys.exit(main())
