#!/usr/bin/env python
"""A screenplay into a shot list: one card per set-up, with an id you can hand to an editor.

    python shotlist.py --script screenplay.txt --film "THE LAST POCKET" --code TLP --out shotlist.json

The shape is the one the 2026-10-28 prototype produced (two cards survive in temp, and they are the specification this
follows - the test compares against them):

    {"film": "...", "code": "...", "shots": [
        {"id": "S01-0001", "scene": 1, "scene_title": "SCENE ONE - OPENING",
         "slug": "1. EXT. PORT MERIDIAN / INT. THE LAST POCKET - NIGHT",
         "place": "EXT. PORT MERIDIAN / INT. THE LAST POCKET", "time": "NIGHT",
         "camera": "Start against rain-streaked glass, then travel beside the rail.",
         "prompt": "ISA rolls a ferry token across her knuckles. A sleeve crease remains on her cheek.",
         "prompt_truncated": False, "source_words": 67, "full_text": "...",
         "elements": [], "checks": []}]}

Which lines are set-ups is **not** decided here: `scenes.detect` owns that (a numbered INT./EXT. slug is its own
picture, a `SCENE ...` header is a title, `SHOT n:` marks a hand-written shot), so a storyboard has one notion of a
set-up rather than two that can disagree. This module only turns each of those units into a card.

`elements` and `checks` are emitted as empty lists because that is what the prototype emitted; they were never filled,
their meaning is not recoverable from the surviving output, and inventing a meaning would be a second specification. If
they come back, they get a definition and a test first.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenes  # noqa: E402

# "CAMERA: ...", "LIGHT: ...", "SOUND: ...", "MOOD: ..." - the craft lines a shot can carry above its action.
LABEL = re.compile(r'^\s*([A-Z][A-Z /-]{2,}):\s*(.+)$')
# "1. EXT. ..." / "12) INT. ..." - the scene ordinal in front of a slug.
ORDINAL = re.compile(r'^\s*(\d+)\s*[.)]\s*(.+)$')

# A prompt past this many words is the encoder's problem, not the writer's; the prototype left 67 words untouched, so the
# cap sits above that rather than at the ~50 the encoder actually reads. Recorded, not enforced.
WORD_CAP = 70


def _split_slug(slug):
    """'1. EXT. PORT MERIDIAN / INT. THE LAST POCKET - NIGHT' -> (1, 'EXT. ... / INT. ...', 'NIGHT').

    The time is everything after the **first** ' - ': a slug may carry a second dash for a position inside the room
    ("INT. THE LAST POCKET - UNDER THE RAIL - CONTINUOUS"), and the prototype kept all of it as the time.
    """
    number, rest = None, slug.strip()
    m = ORDINAL.match(rest)
    if m:
        number, rest = int(m.group(1)), m.group(2).strip()
    place, sep, when = rest.partition(' - ')
    return number, (place.strip() if sep else rest), (when.strip() if sep else '')


def _unit_card(text_unit, running_scene):
    """One card's fields from one set-up's text."""
    lines = [ln.rstrip() for ln in (text_unit or '').splitlines()]
    # scenes.detect returns the header *inside* the unit's text (it is the unit's title); the card's own text starts at
    # its slug, which is what the prototype recorded and what makes source_words count this card's words only.
    lines = [ln for ln in lines if not scenes.HEADER.match(ln.strip())]
    slug, labels, body = '', {}, []
    for line in lines:
        if not slug and scenes.SLUG.match(line):
            slug = line.strip()
            continue
        m = LABEL.match(line)
        if m and m.group(1) not in ('SCENE',):
            labels.setdefault(m.group(1).strip(), m.group(2).strip())
            continue
        if line.strip():
            body.append(line.strip())
    number, place, when = _split_slug(slug) if slug else (None, '', '')
    prompt = ' '.join(' '.join(body).split())
    full_text = '\n'.join(lines).strip()
    # source_words counts what the card was cut *from* (its own text, slug and craft lines included), not the action
    # line: the prototype reported 67 against a 14-word prompt, which is what showed the difference.
    source_words = len(full_text.split())
    words = len(prompt.split())
    return {
        'scene': number if number else running_scene,
        'slug': slug,
        'place': place,
        'time': when,
        'camera': labels.get('CAMERA', ''),
        'prompt': prompt,
        'prompt_truncated': words > WORD_CAP,
        'source_words': source_words,
        'full_text': full_text,
    }


def parse(text, film='', code=''):
    """Screenplay text -> {'film', 'code', 'shots'}. One card per set-up, ids S<scene>-<n>."""
    found = scenes.detect(text)
    shots, seen = [], {}
    for index, unit in enumerate(found, start=1):
        title = (unit.get('title') or '').strip()
        card = _unit_card(unit.get('text') or '', index)
        scene = card['scene']
        seen[scene] = seen.get(scene, 0) + 1
        is_title = bool(scenes.HEADER.match(title)) if title else False
        shots.append({
            'id': 'S%02d-%04d' % (scene, seen[scene]),
            'scene': scene,
            'scene_title': title if is_title else '',
            'slug': card['slug'],
            'place': card['place'],
            'time': card['time'],
            'camera': card['camera'],
            'prompt': card['prompt'],
            'prompt_truncated': card['prompt_truncated'],
            'source_words': card['source_words'],
            'full_text': card['full_text'],
            'elements': [],
            'checks': [],
        })
    return {'film': film, 'code': code, 'shots': shots}


def load(path):
    return Path(path).read_text(encoding='utf-8', errors='replace')


def to_markdown(shotlist):
    """A page a writer can read: id, place and time, then the card's own words."""
    lines = ['# %s - shot list' % (shotlist.get('film') or 'shot list'),
             '', '%d cards, `%s` ordered as the script runs.' % (len(shotlist.get('shots') or []),
                                                                 shotlist.get('code') or 'no code'), '']
    for shot in shotlist.get('shots') or []:
        head = ' · '.join(x for x in (shot.get('place'), shot.get('time')) if x)
        lines.append('## %s%s' % (shot.get('id'), ('  ·  ' + shot['scene_title']) if shot.get('scene_title') else ''))
        if head:
            lines.append('*%s*' % head)
        lines.append('')
        lines.append(shot.get('prompt') or '_(no action line)_')
        if shot.get('camera'):
            lines.append('')
            lines.append('> CAMERA: %s' % shot['camera'])
        lines.append('')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Turn a screenplay into a shot list of cards.')
    ap.add_argument('--script', help='screenplay text file')
    ap.add_argument('--text', help='screenplay text on the command line, for a quick look')
    ap.add_argument('--film', default='')
    ap.add_argument('--code', default='')
    ap.add_argument('--out', default='', help='write the JSON here (shotlist.json)')
    ap.add_argument('--markdown', default='', help='write a readable page here (shotlist.md)')
    a = ap.parse_args(argv)
    if not (a.script or a.text):
        ap.error('give --script or --text')
    source = load(a.script) if a.script else a.text
    film = a.film or (Path(a.script).stem if a.script else '')
    shotlist = parse(source, film=film, code=a.code)
    try:
        if a.out:
            Path(a.out).write_text(json.dumps(shotlist, indent=1), encoding='utf-8')
        if a.markdown:
            Path(a.markdown).write_text(to_markdown(shotlist), encoding='utf-8')
    except OSError as e:
        print('cannot write there: %s' % e, file=sys.stderr)
        return 1
    if not (a.out or a.markdown):
        print(json.dumps(shotlist, indent=1))
    else:
        print('%d cards from %s' % (len(shotlist['shots']), a.script or '(text)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
