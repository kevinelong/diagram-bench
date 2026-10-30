"""test_lineage.py - the prompt chain is recorded, copied forward, and can be recovered once.

The chain exists because grouping cannot recover it: one scene described as a tag list and as prose scores 0.07 cosine
(measured, 2026-10-30), so the pictures scatter into folders no threshold can merge. If the app records the chain while it
renders, the information is never lost; if it did not, `base_image` is enough to recover it once.
"""

import json
import sys
import tempfile
from pathlib import Path

import lineage

FAILS = []


def check(name, ok, detail=''):
    print('%-64s %s%s' % (name, 'PASS' if ok else 'FAIL', '' if ok else '  ' + str(detail)))
    if not ok:
        FAILS.append(name)


def picture(folder, name, prompt, base=None, **extra):
    """A picture and its sidecar, which is all lineage ever reads."""
    path = Path(folder) / ('%s.png' % name)
    path.write_bytes(b'\x89PNG\r\n\x1a\n')
    meta = {'created': '2026-10-30T10:00:00', 'width': 64, 'height': 64}
    if prompt is not None:
        meta['prompt'] = prompt
    if base is not None:
        meta['base_image'] = str(base)
    meta.update(extra)
    path.with_suffix('.json').write_text(json.dumps(meta), encoding='utf-8')
    return path


def main():
    tmp = Path(tempfile.mkdtemp(prefix='lin-test-'))
    root = picture(tmp, 'root', 'single candle, attic, ritual, sigil')
    step2 = picture(tmp, 'step2', 'seated inside the larger blood red sigil circle', base=root)
    step3 = picture(tmp, 'step3', 'add smoke curling from the floor', base=step2)

    steps, partial = lineage.chain_for(step3)
    check('a chain is the prompts that produced the picture, oldest first',
          steps == ['single candle, attic, ritual, sigil', 'seated inside the larger blood red sigil circle',
                    'add smoke curling from the floor'], steps)
    check('and a chain with its base present is not partial', partial is False, partial)

    steps1, partial1 = lineage.chain_for(root)
    check('a picture with no base is a chain of one', steps1 == ['single candle, attic, ritual, sigil'], steps1)
    check('and it is the root of its own chain, not partial', partial1 is False, partial1)

    orphan = picture(tmp, 'orphan', 'a picture whose base has been deleted', base=tmp / 'gone.png')
    steps2, partial2 = lineage.chain_for(orphan)
    check('a deleted base marks the chain partial instead of inventing a root',
          steps2 == [orphan.with_suffix('.json').read_text(encoding='utf-8') and 'a picture whose base has been deleted']
          and partial2 is True, (steps2, partial2))

    loop = picture(tmp, 'loop-a', 'first half of a loop', base=tmp / 'loop-b.png')
    picture(tmp, 'loop-b', 'second half of a loop', base=loop)
    _steps, loop_partial = lineage.chain_for(loop)
    check('a cycle terminates instead of recursing forever', loop_partial is True)

    stats = lineage.backfill(tmp, apply=True, verbose=False)
    check('the pass writes a chain for every picture that has a prompt', stats['written'] >= 4, stats)
    after = json.loads(step3.with_suffix('.json').read_text(encoding='utf-8'))
    check('the sidecar now carries the chain', after.get('lineage') == steps, after.get('lineage'))
    check('and names the root', after.get('lineage_root') == 'single candle, attic, ritual, sigil', after.get('lineage_root'))
    check('the orphan is marked partial', json.loads(orphan.with_suffix('.json').read_text(encoding='utf-8')).get('lineage_partial') is True)
    again = lineage.backfill(tmp, apply=True, verbose=False)
    check('running it twice changes nothing', again['written'] == 0 and again['already'] >= 4, again)

    picture(tmp, 'blank', None)                                   # a picture whose sidecar has no prompt
    empty = lineage.backfill(tmp, apply=True, verbose=False)
    check('a picture with no prompt is left alone', empty['no_prompt'] >= 1, empty)

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
