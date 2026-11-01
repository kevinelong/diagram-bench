"""perf_report.py - read what the window measured and say where the time went.

The window keeps bounded samples per timed block (panels, redraw, photo cache, thumbnails) and writes them beside the
settings: `profile-YYYYmmdd-HHMMSS.json` from the `profile…` button, or `timings.log` (JSONL) when `ZIMAGE_TIMING` was set.

    python tools/perf_report.py                          # newest profile beside the settings
    python tools/perf_report.py path/to/profile.json
    python tools/perf_report.py path/to/timings.log --top 12

Two questions it answers, which a raw log does not:

* **Where does the time go** - p50/p90/max and the total per event, biggest total first.
* **When is it slow** - the slowest samples, each with the context recorded beside it (files, folders, zoom, source and
  canvas pixels), which is how "the redraw is slow" becomes "at 400 % on a 1536 px source in a 690 px pane".
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def settings_dir():
    base = os.environ.get('ZIMAGE_SETTINGS_DIR')
    return Path(base) if base else Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'zimage-ui'


def newest_profile():
    for pattern in ('profile-*.json', 'timings.log'):
        found = sorted(glob.glob(str(settings_dir() / pattern)))
        if found:
            return Path(found[-1])
    return None


def load(path):
    """A profile file, or a JSONL log - both reduce to {event: [ms, ...]} plus the samples' contexts."""
    text = Path(path).read_text(encoding='utf-8')
    if text.lstrip().startswith('{') and '\n{' not in text:
        blob = json.loads(text)
        if 'events' in blob:                                  # the profile the button writes
            return {ev: [stats['p50']] * 1 for ev, stats in blob.get('events', {}).items()}, blob, []
    samples, contexts = {}, []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev, ms = rec.get('ev'), rec.get('ms')
        if not isinstance(ms, (int, float)):
            continue
        samples.setdefault(ev, []).append(float(ms))
        if rec.get('ctx'):
            contexts.append((float(ms), ev, rec['ctx'], rec.get('t', '')))
    return samples, {'events': {}}, contexts


def stats(values):
    ordered = sorted(values)
    def pct(q):
        return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]
    return len(ordered), pct(.5), pct(.9), ordered[-1], sum(ordered)


def report(samples, blob, contexts, top):
    events = blob.get('events') or {}
    print('profile: %s' % blob.get('started', '?'))
    if blob.get('context'):
        print('context at save: %s' % json.dumps(blob['context'], sort_keys=True))
    rows = []
    for ev, values in samples.items():
        if ev in events and len(values) <= 1:                 # from a saved profile: only p50 is kept
            s = events[ev]
            rows.append((s.get('total', 0), ev, s.get('n', 0), s.get('p50', 0), s.get('p90', 0), s.get('max', 0)))
            continue
        n, p50, p90, mx, total = stats(values)
        rows.append((total, ev, n, p50, p90, mx))
    if not rows:
        print('no samples')
        return 1
    rows.sort(reverse=True)
    print('\n%-22s %6s %8s %8s %8s %10s' % ('event', 'n', 'p50 ms', 'p90 ms', 'max ms', 'total ms'))
    for total, ev, n, p50, p90, mx in rows:
        print('%-22s %6d %8.1f %8.1f %8.1f %10.0f' % (ev, n, p50, p90, mx, total))
    if contexts:
        print('\nslowest samples, with the situation they happened in')
        for ms, ev, ctx, when in sorted(contexts, reverse=True)[:top]:
            print('  %8.1f ms  %-14s %s  %s' % (ms, ev, when, json.dumps(ctx, sort_keys=True)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('path', nargs='?', default=None, help='a profile json or timings.log (default: newest beside settings)')
    ap.add_argument('--top', type=int, default=8, help='how many slowest samples to show')
    args = ap.parse_args(argv)
    path = Path(args.path) if args.path else newest_profile()
    if path is None or not Path(path).exists():
        print('no profile found beside %s - press "profile…" in the window first' % settings_dir())
        return 2
    samples, blob, contexts = load(path)
    return report(samples, blob, contexts, args.top)


if __name__ == '__main__':
    sys.exit(main())
