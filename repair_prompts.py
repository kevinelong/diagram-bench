"""repair_prompts.py - collapse style suffixes that were appended more than once.

The window applies a style by appending its words to the prompt. Rerendering a prompt that already carried the suffix
(Vary, hires, or clicking the image in the gallery) appended it again, and editing the text in between could leave the
suffix stranded mid-sentence. The app no longer does this; this tool repairs the sidecars it already wrote.

Dry run by default. `--apply` writes, after copying every file it is about to change into `.prompt-backups/`.

    python repair_prompts.py                       # report only
    python repair_prompts.py --apply               # repair, with backups
    python repair_prompts.py --dir PATH --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from zimage_ui import STYLES

SUFFIXES = [s for s in STYLES.values() if s]


def collapse(prompt, suffix):
    """Keep the last occurrence of `suffix`, drop the earlier ones, and tidy the punctuation left behind.

    Text edited between two occurrences is kept - that is where a user's own words landed:
        "X, S, S resting inside case, S"  ->  "X, resting inside case, S"
    """
    if not prompt or prompt.count(suffix) < 2:
        return prompt
    parts = prompt.split(suffix)
    body = parts[0] + ''.join(parts[1:-1]) + suffix + parts[-1]
    body = re.sub(r'\s*,\s*,+', ', ', body)      # ", ," left by a removed occurrence
    body = re.sub(r'\s{2,}', ' ', body)
    body = re.sub(r'\s+([,.;])', r'\1', body)
    return body.strip().lstrip(',; ').strip()


def repair(prompt):
    """Apply the collapse for every known suffix. Returns (new_prompt, list_of_suffixes_collapsed)."""
    out, hit = prompt, []
    for suffix in SUFFIXES:
        if out.count(suffix) > 1:
            out = collapse(out, suffix)
            hit.append(suffix)
    return out, hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=r'C:/Users/kevin/Pictures/zimage-ui')
    ap.add_argument('--apply', action='store_true', help='write the repairs (dry run without this)')
    a = ap.parse_args()
    root = Path(a.dir)
    if not root.exists():
        raise SystemExit('no such folder: %s' % root)

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = root / '.prompt-backups' / stamp
    changed, skipped = [], 0
    for sidecar in sorted(root.glob('*.json')):
        try:
            data = json.loads(sidecar.read_text(encoding='utf-8'))
        except Exception:
            skipped += 1
            continue
        prompt = data.get('prompt') or ''
        fixed, hits = repair(prompt)
        if not hits or fixed == prompt:
            continue
        changed.append((sidecar, prompt, fixed, hits))

    for sidecar, before, after, hits in changed[:10]:
        print('%-52s %s' % (sidecar.name, ', '.join(h.split(',')[0] for h in hits)))
        print('   before: %s' % before[:160])
        print('   after : %s' % after[:160])
    print('\n%d sidecar%s need repair, %d unreadable' % (len(changed), '' if len(changed) == 1 else 's', skipped))

    if not a.apply:
        print('dry run - pass --apply to write (a backup is taken first)')
        return 0
    if not changed:
        return 0

    backup.mkdir(parents=True, exist_ok=True)
    for sidecar, before, after, _hits in changed:
        shutil.copy2(sidecar, backup / sidecar.name)
        data = json.loads(sidecar.read_text(encoding='utf-8'))
        data['prompt'] = after
        sidecar.write_text(json.dumps(data, indent=1), encoding='utf-8')
    print('repaired %d sidecar%s; originals in %s' % (len(changed), '' if len(changed) == 1 else 's', backup))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
