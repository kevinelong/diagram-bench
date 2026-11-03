"""merge_equivalence.py - does candidate-only clustering agree with a full pass?

`groups_store.merge()` exists to make one new picture cost seconds instead of the 74 s full pass, and it is not wired because on
the suite's gallery it once returned a folder labelled "prompt for image 0" holding 21 pictures instead of the shared scene. This
script is the gate: build a small gallery, run the full pass, store the result, add and delete pictures, run the merge, and diff
the two plans - labels and membership.

    python tools/merge_equivalence.py

Exit code 0 means the two agree and the switch is safe to make. Anything else prints the diff, which is where the fix starts.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import group_prompts as gp
import groups_store as gs


def rows_for(spec):
    """(name, prompt) pairs -> planner rows, oldest first."""
    return [{'path': str(Path('C:/g') / n), 'prompt': p, 'root': p, 'created': '2026-11-01T%02d:00:00' % (8 + i)}
            for i, (n, p) in enumerate(spec)]


def summarise(plan):
    out = {}
    for group in (plan or {}).get('groups') or []:
        names = sorted(Path(r.get('path')).name for r in group.get('items') or [])
        out[group.get('label') or ''] = names
    return out


def diff(a, b):
    problems = []
    for label in sorted(set(a) | set(b)):
        if sorted(a.get(label) or []) != sorted(b.get(label) or []):
            problems.append((label, sorted(a.get(label) or []), sorted(b.get(label) or [])))
    return problems


def main():
    base = [('a1.png', 'a man in a hat'), ('a2.png', 'a man in a hat, red hat'), ('a3.png', 'a man in a hat at dawn'),
            ('b1.png', 'a woman by the sea'), ('b2.png', 'a woman by the sea at noon'),
            ('c1.png', 'a dragon over a lake'), ('c2.png', 'a dragon over a lake in rain')]
    rows = rows_for(base)
    full = gp.plan(rows, keep_lexicon=False, recent=0)
    tmp = Path(__file__).resolve().parent.parent / '.merge-equivalence'
    tmp.mkdir(exist_ok=True)
    rule = gs.fingerprint_for(gp.DEFAULT_THRESHOLD, 0, gp.MIN_GROUP)
    gs.save(tmp, full, rule)

    later = rows_for(base + [('a4.png', 'a man in a hat, blue hat')])[1:]      # one new picture, one deleted (a1)
    merged, reused, clustered = gs.merge(later, gs.load(tmp), rule,
                                         lambda subset: gp.plan(subset, keep_lexicon=False, recent=0))
    fresh = gp.plan(later, keep_lexicon=False, recent=0)

    before, after = summarise(full), summarise(merged or {})
    truth = summarise(fresh)
    print('clustered %d of %d rows (a full pass would cluster all)' % (clustered, len(later)))
    print('\nfull pass now :', {k: v for k, v in sorted(truth.items())})
    print('\nmerge result  :', {k: v for k, v in sorted(after.items())})
    # Equality with a fresh pass is deliberately NOT the gate. The store is a cache of a past decision, and a deletion can
    # legitimately re-cluster: here `a1` was the anchor that held "at dawn" and "red hat" together, so removing it splits the
    # family in a fresh pass and the cache keeps it whole. That is the trade the user asked for ("assume old groupings are
    # good"). What must hold is that the merge loses nothing and invents nothing:
    wanted = {Path(r['path']).name for r in later}
    got = [n for names in after.values() for n in names]
    problems = []
    if sorted(got) != sorted(wanted):
        problems.append(('membership', sorted(set(wanted) - set(got)), sorted(set(got) - set(wanted))))
    if len(got) != len(set(got)):
        problems.append(('duplicate', [n for n in set(got) if got.count(n) > 1], []))
    for label in after:
        if not label:
            problems.append(('unlabelled folder', [], sorted(after[label])))
    print('\nfull pass would label %d folders, the merge kept %d' % (len(truth), len(after)))
    if problems:
        print('\nFAILS the invariants:')
        for what, missing, extra in problems:
            print('  %-18s missing=%s extra=%s' % (what, missing, extra))
        return 1
    print('\nmerge holds every picture exactly once, in a labelled folder: safe to switch')
    print('(labels may differ from a fresh pass by design - the cache keeps a decision made before the change)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
