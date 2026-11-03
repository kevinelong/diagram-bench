"""usage.py - what was clicked, when, and how often, so the UI can be argued about with evidence.

One JSON line per interaction, appended to `usage-YYYYMMDD.jsonl` in the settings directory. Deliberately dumb: no schema
migration, no aggregation at write time, no failure that can break a click - if the file cannot be written, the app does not
care. Reading it later is a per-line count:

    python tools/usage_report.py            # counts by action, by day, and the top pairs

Why it exists: every UI argument in this repo so far ended in "I think this is used more" or "that feels rare". A week of
lines settles it - and the main path (Select all, Gallery) versus the alternates (A/B, Caption, Recover) is exactly the
question the pane's one-row layout was guessed at.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

MAX_DAYS = 30          # keep a month; older files are ignored by the report rather than deleted silently


def source():
    """Who wrote this line: `user` by default, `test` when the suite (or any harness) asks for it.

    Test traffic must never be counted with real use - the whole point of the log is to answer "what does the reader do", and
    a suite that opens 200 folders would drown that. `ZIMAGE_USAGE_SOURCE` sets it; the suite also points the log at a temp
    directory so the real file is not even touched.
    """
    return (os.environ.get('ZIMAGE_USAGE_SOURCE') or 'user').strip().lower()[:20] or 'user'


def log_dir():
    base = os.environ.get('ZIMAGE_USAGE_DIR')
    if base:
        return Path(base)
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'zimage-ui'


def record(action, **fields):
    """Append one interaction. Never raises: a usage log must not be able to break a click."""
    try:
        stamp = datetime.now()
        line = {'t': stamp.isoformat(timespec='seconds'), 'action': str(action)[:60], 'src': source()}
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, (int, float, bool, str)):
                line[str(key)[:30]] = value if not isinstance(value, str) else value[:200]
            else:
                line[str(key)[:30]] = str(value)[:200]
        path = log_dir() / ('usage-%s.jsonl' % stamp.strftime('%Y%m%d'))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + '\n')
        return path
    except Exception:
        return None


def lines(days=MAX_DAYS, source_wanted=None):
    """Every recorded interaction from the last `days` files, oldest first. `source_wanted` filters by writer."""
    try:
        files = sorted(log_dir().glob('usage-*.jsonl'))[-days:]
    except OSError:
        return []
    out = []
    for path in files:
        try:
            for raw in path.read_text(encoding='utf-8').splitlines():
                if raw.strip():
                    row = json.loads(raw)
                    if source_wanted and (row.get('src') or 'user') != source_wanted:
                        continue
                    out.append(row)
        except (OSError, ValueError):
            continue
    return out
