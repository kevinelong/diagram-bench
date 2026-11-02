"""sessions.py - divide a folder by *when the work happened*, using the breaks that are already there.

Asked 2026-11-02: a 500-picture folder is not one subject, it is a stretch of work, and the honest axis is the session,
not the calendar day. The measurement that settled it, on the real gallery (2,925 pictures over 11 days):

    sessions = runs with no gap >= 4 h            20 over 11 days, median 3 h 28, longest 14 h 59
    sessions that cross midnight                  4 of 20 (20 %) - a calendar cut splits one night's work in two
    gaps >= 8 h                                  9 -> 10 periods, about one per day
    gaps >= 10 h                                 5 -> 6, too coarse: it skips days
    largest gaps end                              07:40 - 12:36, because the night ends when he wakes

So: **cut in the gap, not on the clock.** A candidate seam is the midpoint of every gap at or above a threshold
(default 8 h); nothing is applied until the reader accepts it, and what is stored is a timestamp, never an index.

That distinction is the whole design. A timestamp cut is stable - a picture added later with an earlier time lands in the
right session, where an index cut would shift every later segment down one. And a cut carries the picture it was drawn at,
so a stale rule can be read by a human instead of obeyed silently.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_GAP_HOURS = 8.0
STORE = '.zimage-sessions.json'


def _when(item):
    """The generation time of a picture, which is the axis; mtime is a fallback and is not comparable across machines."""
    for key in ('created', 'mtime'):
        raw = item.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            continue
    return None


def seams(items, gap_hours=DEFAULT_GAP_HOURS):
    """Candidate cuts: the midpoint of every break at least `gap_hours` long, oldest first.

    Midpoint, not the edges: a boundary that sits exactly at 23:05 or at 09:13 looks like it belongs to the session on
    one side. The middle of an empty night belongs to nobody, which is what a boundary should be.
    """
    stamped = sorted((t, it) for t, it in ((_when(i), i) for i in items) if t is not None)
    out = []
    for (a, _), (b, _) in zip(stamped, stamped[1:]):
        if b - a >= timedelta(hours=gap_hours):
            out.append((a + (b - a) / 2, a, b))
    return out


def split(items, cuts, gap_hours=DEFAULT_GAP_HOURS):
    """The segments produced by the accepted `cuts` (datetimes), oldest first.

    Every item lands in exactly one segment: the interval containing its own time. The last segment is open-ended, so new
    work joins "now" without the reader editing anything - which is the reason the partition stays usable as a folder grows.
    """
    cuts = sorted(cuts)
    # sorted by time, not by whatever order the caller happened to pass: a segment that lists its pictures
    # oldest-first is the only order that stays comparable between two folders.
    stamped = sorted((t, it) for t, it in ((_when(i), i) for i in items) if t is not None)
    loose = [i for i in items if _when(i) is None]
    segments = [[] for _ in range(len(cuts) + 1)]
    for t, it in stamped:
        index = 0
        while index < len(cuts) and t >= cuts[index]:
            index += 1
        segments[index].append(it)
    if loose:                                   # no time at all: kept, never silently dropped
        segments[-1].extend(loose)
    out = []
    for i, seg in enumerate(segments):
        if not seg:
            continue
        times = sorted(t for t in (_when(x) for x in seg) if t is not None)
        start = times[0] if times else None
        end = times[-1] if times else None
        out.append({'items': seg, 'start': start, 'end': end, 'index': i,
                    'label': label_for(start, end)})
    return out


def label_for(start, end=None):
    """A session is named by when it *started*, because that is how it would be described aloud.

    "Sat 22:38" rather than "Fri 22:38 - Sat 01:06": a night that runs into the next morning is one evening's work, and a
    label that names both days reads as if it were two sessions.
    """
    if start is None:
        return 'undated'
    return start.strftime('%a %d %b %H:%M')


def store_path(gallery):
    return Path(gallery) / STORE


def load_cuts(gallery, key):
    """Accepted cuts for one folder key, as datetimes. Missing file or key means "no cuts", never an error."""
    path = store_path(gallery)
    try:
        blob = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return []
    out = []
    for raw in (blob.get(key) or []):
        try:
            out.append(datetime.fromisoformat(str(raw)))
        except ValueError:
            continue
    return sorted(out)


def save_cuts(gallery, key, cuts):
    """Write the cuts for one folder key, leaving every other folder's alone. Returns the path."""
    path = store_path(gallery)
    try:
        blob = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(blob, dict):
            blob = {}
    except (FileNotFoundError, ValueError):
        blob = {}
    blob[key] = [c.isoformat(timespec='seconds') for c in sorted(cuts)]
    if not blob[key]:
        blob.pop(key, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob, indent=1, sort_keys=True), encoding='utf-8')
    return path


def key_for(group):
    """A folder's identity for storage: its root prompt when it has one, else its label.

    The label is a medoid and therefore moves when the content changes; the root prompt does not. So a family keeps its
    cuts through a rename, which is the case that matters - a 500-picture folder is exactly the one being renamed.
    """
    roots = [str(it.get('root') or '').strip() for it in group.get('items') or []]
    roots = [r for r in roots if r]
    if roots:
        return 'root:' + min(roots, key=len)[:160]
    return 'label:' + (group.get('label') or '')[:160]


def apply(plan, gallery, gap_hours=DEFAULT_GAP_HOURS):
    """Expand every folder that has accepted cuts into sibling folders, one per session.

    Siblings, not a new level: the folder tiles, the pane, paging, ranking and the A/B review all keep working unchanged,
    and there is no second way to look at the pictures.
    """
    groups = (plan or {}).get('groups') or []
    out = []
    for group in groups:
        cuts = load_cuts(gallery, key_for(group))
        if not cuts:
            out.append(group)
            continue
        parts = split(group.get('items') or [], cuts, gap_hours)
        if len(parts) <= 1:
            out.append(group)
            continue
        base = (group.get('label') or '').strip()
        for part in parts:
            out.append({'label': ('%s · %s' % (base, part['label']))[:160] if base else part['label'],
                        'items': part['items'],
                        'oldest': part['start'].isoformat(timespec='seconds') if part['start'] else group.get('oldest'),
                        'newest': part['end'].isoformat(timespec='seconds') if part['end'] else group.get('newest'),
                        'session_of': base, 'session_label': part['label']})
    return dict(plan or {}, groups=out)
