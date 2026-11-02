"""test_deployments.py - the measured-cards table stays evidence instead of folklore.

The table is only worth having if a row cannot be added from memory: every run needs a wall time, a date and the command
that produced it, an unfit profile is never proposed, and an unknown card says "measure one" rather than guessing.
"""

import sys

import deployments as d

FAILS = []


def check(name, ok, detail=''):
    print('%-72s %s%s' % (name, 'PASS' if ok else 'FAIL', '' if ok else '  ' + str(detail)))
    if not ok:
        FAILS.append(name)


def main():
    print('-- every card carries what a deployment decision needs')
    for key, card in d.CARDS.items():
        check('%s knows its compute capability and memory' % key,
              isinstance(card.get('compute'), tuple) and len(card['compute']) == 2 and card.get('vram_mib'),
              card)
        check('%s records when and where it was measured' % key,
              bool(card.get('measured_on')) and bool(card.get('source')), card)

    print('\n-- every run is a measurement, not a recollection')
    for run in d.RUNS:
        label = '%s / %s @ %d' % (run['card'], run['profile'], run['width'])
        check('%s has a size and a step count' % label,
              run['width'] == run['height'] and run['steps'] >= 1, run)
        check('%s cites the command that produced it' % label, bool(run.get('provenance')), run)
        check('%s says whether it fits the card' % label, isinstance(run.get('fits'), bool), run)
        if run['fits'] and run['wall_s'] is None:
            check('%s: a profile that fits and has no wall time is not evidence' % label, False, run)

    print('\n-- unfit profiles are never proposed')
    for card in d.CARDS:
        limit = d.CARDS[card]['vram_mib']
        picked = d.best_for(card)
        check('%s: the recommendation fits its memory' % card,
              picked is None or (picked['peak_mib'] or 0) <= limit, picked)
    over = [r for r in d.RUNS if not r['fits']]
    check('and one of the rows really is over its card, which is why the rule exists',
          bool(over) and all(r['profile'] not in [x['profile'] for x in d.runs_for(r['card'])] for r in over),
          over)

    print('\n-- size dominates speed, and each card gets its own answer')
    laptop = d.best_for('laptop-4060')
    check('the 8 GiB card starts at 1024 px with z-image turbo, not a 512 px run',
          (laptop['profile'], laptop['width']) == ('z-image turbo', 1024), laptop)
    desk = d.best_for('desktop-5070')
    check('the 12 GiB card starts with the same shape of run', (desk['profile'], desk['width']) == ('z-image turbo', 1024),
          desk)
    check('and it is faster than the laptop at the same job, which is the whole point of a per-card table',
          desk['wall_s'] < laptop['wall_s'], (desk['wall_s'], laptop['wall_s']))

    print('\n-- an unknown card says so')
    check('an unmeasured card returns no profile', d.best_for('nvidia-unknown') is None)
    text = d.advise('NVIDIA GeForce RTX 3090', 24576, (8, 6))
    check('and the advice asks for a measurement rather than inventing a default',
          'no measurement' in text and 'measure one render' in text, text)
    check('a known card is matched by name', d.card_key('NVIDIA GeForce RTX 5070', None, None) == 'desktop-5070')
    check('and by compute + memory when the name is a variant', d.card_key('Some 4060 variant', 8188, (8, 9)) == 'laptop-4060',
          d.card_key('Some 4060 variant', 8188, (8, 9)))

    print('\nFAILURES:', FAILS if FAILS else 'none')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
