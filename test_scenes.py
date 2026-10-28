#!/usr/bin/env python
"""Unit tests for the primitive scene split: no window, no GPU, no model."""

import scenes


def check(label, ok, detail=''):
    print('%-62s %s %s' % (label, 'PASS' if ok else 'FAIL', '' if ok else detail))
    return ok


def main():
    ok = True
    # the ordinary case must not change behaviour at all
    one = scenes.detect('a black poodle on a leather couch, studio light')
    ok &= check('an ordinary prompt is one scene', len(one) == 1, one)
    ok &= check('and keeps its text unchanged', one[0]['text'] == 'a black poodle on a leather couch, studio light')
    ok &= check('a prompt that merely mentions a scene stays one scene',
                scenes.count('a poster for scene 3 of a play') == 1)
    ok &= check('the banner line for one scene reads plainly', scenes.summary(one) == 'one scene')

    # screenplay: slugs are the pictures, headers are titles
    screenplay = ('SCENE ONE - OPENING\n\n1. INT. THE LAST POCKET - NIGHT\nCAMERA: rain on glass.\n\n'
                  'SCENE TWO - NO SIDE BETS\n\n2. INT. THE LAST POCKET - UNDER THE RAIL\nCAMERA: a coin passing.\n\n'
                  'SCENE THREE - THE ENTRY\n\n3. INT. THE LAST POCKET - DRAWING\nCAMERA: the scoreboard.')
    found = scenes.detect(screenplay)
    ok &= check('three set-ups are three scenes', len(found) == 3, [s['title'] for s in found])
    ok &= check('each takes its SCENE header as the title',
                [s['title'] for s in found][:2] == ['SCENE ONE - OPENING', 'SCENE TWO - NO SIDE BETS'],
                [s['title'] for s in found])
    ok &= check('a scene does not swallow the next scene\'s text',
                'SCENE TWO' not in found[0]['text'] and 'SCENE THREE' not in found[1]['text'],
                found[0]['text'])
    ok &= check('each scene keeps its own camera line',
                all('CAMERA:' in s['text'] for s in found))
    ok &= check('the banner counts them', scenes.summary(found) == '3 scenes, one image each', scenes.summary(found))

    # a combined slug, and unnumbered slugs
    combined = ('1. EXT. PORT MERIDIAN / INT. THE LAST POCKET - NIGHT\nrain\n\n'
                '2. INT./EXT. THE LAST POCKET - DOOR\nwet footsteps')
    ok &= check('a combined INT./EXT. slug is its own scene', scenes.count(combined) == 2,
                [s['title'] for s in scenes.detect(combined)])
    unnumbered = 'INT. KITCHEN - MORNING\nkettle\n\nEXT. GARDEN - LATER\nrain'
    ok &= check('unnumbered slugs split too', scenes.count(unnumbered) == 2)

    # other ways of marking a unit
    shots = 'SHOT 1: a wide of the hall\n\nSHOT 2: close on the cue\n\nSHOT 3: the pocket'
    ok &= check('SHOT n: markers split', scenes.count(shots) == 3, scenes.count(shots))
    panels = '[scene 1] the hall\n\n[scene 2] the table'
    ok &= check('[scene n] markers split', scenes.count(panels) == 2)

    # a single marker is not enough to switch behaviour
    ok &= check('one marker alone stays one scene',
                scenes.count('SHOT 1: a wide of the hall, then a close on the cue') == 1)

    print('\n%s' % ('ALL PASS' if ok else 'FAILURES'))
    return 0 if ok else 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
