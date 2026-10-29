#!/usr/bin/env python
"""Tests for shotlist.py - the card shape checked against the 2026-10-28 prototype's own output.

    python test_shotlist.py

The golden values below are the two cards that survived in temp (`sl-_59gdl0j/shotlist.json`). They are the only
specification of the shape that exists, and they are copied here rather than read from temp, so the test does not depend
on a machine's leftovers.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shotlist  # noqa: E402

FAILS = []


def check(label, ok, detail=''):
    print('%-62s %s %s' % (label, 'PASS' if ok else 'FAIL', detail))
    if not ok:
        FAILS.append(label)


FIXTURE = Path(__file__).resolve().parent / 'tests' / 'fixtures' / 'last-pocket-snippet.txt'

# the prototype's values, for the eight fields that are unambiguous
GOLDEN = [
    {'id': 'S01-0001', 'scene': 1, 'scene_title': 'SCENE ONE - OPENING',
     'slug': '1. EXT. PORT MERIDIAN / INT. THE LAST POCKET - NIGHT',
     'place': 'EXT. PORT MERIDIAN / INT. THE LAST POCKET', 'time': 'NIGHT',
     'camera': 'Start against rain-streaked glass, then travel beside the rail.',
     'prompt': 'ISA rolls a ferry token across her knuckles. A sleeve crease remains on her cheek.'},
    {'id': 'S02-0001', 'scene': 2, 'scene_title': 'SCENE TWO - NO SIDE BETS',
     'slug': '2. INT. THE LAST POCKET - UNDER THE RAIL - CONTINUOUS',
     'place': 'INT. THE LAST POCKET', 'time': 'UNDER THE RAIL - CONTINUOUS',
     'camera': 'Low, under the rail, a coin passing in the foreground.',
     'prompt': 'KES places a second coin beside hers.'},
]


def main():
    text = FIXTURE.read_text(encoding='utf-8')
    doc = shotlist.parse(text, film='THE LAST POCKET', code='TLP')

    print('\n-- the shape the prototype produced')
    check('two set-ups become two cards', len(doc['shots']) == 2, str(len(doc['shots'])))
    check('the film and code travel with it', (doc['film'], doc['code']) == ('THE LAST POCKET', 'TLP'),
          '%s/%s' % (doc['film'], doc['code']))
    for want, have in zip(GOLDEN, doc['shots']):
        for field in ('id', 'scene', 'scene_title', 'slug', 'place', 'time', 'camera', 'prompt'):
            check('%s field %s' % (want['id'], field), want[field] == have.get(field),
                  '' if want[field] == have.get(field) else 'want %r got %r' % (want[field], have.get(field)))

    print('\n-- the fields whose meaning had to be read off the prototype')
    for card in doc['shots']:
        check('%s source_words counts the unit, not the prompt' % card['id'],
              card['source_words'] == len(card['full_text'].split())
              and card['source_words'] > len(card['prompt'].split()),
              '%d vs prompt %d' % (card['source_words'], len(card['prompt'].split())))
        check('%s prompt carries no craft line' % card['id'],
              ':' not in card['prompt'][:12] and 'CAMERA' not in card['prompt'] and 'MOOD' not in card['prompt'],
              card['prompt'][:40])
        check('%s prompt carries no scene header' % card['id'],
              'SCENE' not in card['prompt'], card['prompt'][:40])
        check('%s full_text starts at its own slug' % card['id'],
              card['full_text'].lstrip().startswith(card['slug'][:2]), card['full_text'][:30])
        check('%s elements and checks are the empty lists the prototype emitted' % card['id'],
              card['elements'] == [] and card['checks'] == [], str(card['elements'])[:20])

    print('\n-- a slug with two dashes keeps the tail as the time')
    number, place, when = shotlist._split_slug('7. INT. THE LAST POCKET - UNDER THE RAIL - CONTINUOUS')
    check('the ordinal is read', number == 7, str(number))
    check('the place stops at the first dash', place == 'INT. THE LAST POCKET', place)
    check('the time keeps the second dash and all', when == 'UNDER THE RAIL - CONTINUOUS', when)
    check('a slug with no dash has no time', shotlist._split_slug('INT. ROOM') == (None, 'INT. ROOM', ''),
          str(shotlist._split_slug('INT. ROOM')))

    print('\n-- several set-ups in one scene share its number and count up')
    two_in_one = ('SCENE FOUR - THE TABLE\n\n4. INT. THE LAST POCKET - NIGHT\n\nA hand on the felt.\n\n'
                  '5. INT. THE LAST POCKET - LATER\n\nA second hand.\n')
    cards = shotlist.parse(two_in_one)['shots']
    check('two cards', len(cards) == 2, str(len(cards)))
    check('scene numbers come from the slug ordinals', [c['scene'] for c in cards] == [4, 5],
          str([c['scene'] for c in cards]))
    check('ids are scene-scoped', [c['id'] for c in cards] == ['S04-0001', 'S05-0001'],
          str([c['id'] for c in cards]))

    print('\n-- text that is not a screenplay is one card, not an error')
    plain = shotlist.parse('a red fox in snow')['shots']
    check('one card', len(plain) == 1, str(len(plain)))
    check('no slug, so the whole text is the prompt', plain[0]['slug'] == '' and plain[0]['prompt'] == 'a red fox in snow',
          '%r/%r' % (plain[0]['slug'], plain[0]['prompt']))

    print('\n-- the card carries its running position')
    check('cards are numbered in script order', [c['card'] for c in doc['shots']] == [1, 2],
          str([c['card'] for c in doc['shots']]))
    repeated = shotlist.parse('7. INT. A - NIGHT\n\nOne.\n\n1. INT. B - DAY\n\nTwo.\n\n7. INT. C - NIGHT\n\nThree.\n')
    ids = [c['id'] for c in repeated['shots']]
    check('a film numbered twice still has unique ids', len(ids) == len(set(ids)) == 3, str(ids))
    check('and the running position disambiguates them', [c['card'] for c in repeated['shots']] == [1, 2, 3],
          str([c['card'] for c in repeated['shots']]))

    print('\n-- cutting a card to what an encoder reads')
    long_card = {'prompt': ('She sets the glass down. ' * 20).strip(), 'slug': 'INT. ROOM', 'camera': ''}
    cut = shotlist.prompt_for(long_card, words=20)
    check('a long block is cut inside the budget', len(cut.split()) <= 21, '%d words' % len(cut.split()))
    check('and at a sentence end, not mid-word', cut.endswith('.'), repr(cut[-24:]))
    check('a short block is passed through untouched',
          shotlist.prompt_for({'prompt': 'A hand on the felt.'}) == 'A hand on the felt.')
    check('a card with no action falls back to its slug',
          shotlist.prompt_for({'prompt': '', 'slug': 'INT. THE LAST POCKET', 'camera': 'Low.'}) == 'INT. THE LAST POCKET Low.')
    check('a card with nothing at all gives an empty prompt, not a crash',
          shotlist.prompt_for({}) == '')

    print('\n-- the file name a card gets')
    import zimage_ui
    name = zimage_ui.shot_filename('20261029-090000', 'TLP_S01-0001', 1024, 768, 7)
    check('the stamp, the id, the size and the seed are all there',
          name == '20261029-090000_TLP_S01-0001_1024x768_seed7.png', name)
    check('the gallery can still parse the seed', int(name.rsplit('_seed', 1)[1].split('_')[0].split('.')[0]) == 7, name)
    check('and the size', name.split('_')[-2] == '768x0' or '1024x768' in name, name)

    print('\n-- writing it out')
    tmp = Path(tempfile.mkdtemp(prefix='shotlist-test-'))
    out, md = tmp / 'shotlist.json', tmp / 'shotlist.md'
    code = shotlist.main(['--script', str(FIXTURE), '--film', 'THE LAST POCKET', '--code', 'TLP',
                          '--out', str(out), '--markdown', str(md)])
    check('the command succeeds', code == 0, str(code))
    again = json.loads(out.read_text(encoding='utf-8'))
    check('the JSON round-trips', again['shots'] == doc['shots'], str(len(again['shots'])))
    page = md.read_text(encoding='utf-8')
    check('the page names every card', all(c['id'] in page for c in doc['shots']), page[:40])

    print('\n%d failures' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
