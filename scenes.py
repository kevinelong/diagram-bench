#!/usr/bin/env python
"""Primitive scene detection: one prompt in, the several pictures it describes out.

No model and no LLM on purpose - this is the cheap version, so it can be trusted and tested. A prompt marks its scene
starts with a line that *begins* with one of `MARKERS` (a screenplay heading, a numbered INT./EXT. slug, `SHOT 3:` and
friends). Fewer than two markers means the prompt is one scene, so an ordinary prompt behaves exactly as it always did.

The point is the count and the split, not a shot design: the fork (zimage-scenes) is where camera blocks, elements and
environments get parsed properly.
"""

import re

MAX_SCENES = 200   # a truncation would silently merge the rest into the last scene

# A *scene title* line ("SCENE ONE - OPENING") carries no picture by itself: it names the run of set-ups under it.
HEADER = re.compile(r"^\s*SCENE\s+(?:[0-9]{1,3}|[IVXLC]+|[A-Z][A-Z' ]{0,24})\s*(?:[-–—:]\s*.*)?\s*$", re.I)
# A *set-up* is the unit that gets an image: a numbered INT./EXT. slug, or an unnumbered one.
SLUG = re.compile(r'^\s*(?:[0-9]{1,3}\.\s+)?(?:INT|EXT)\.(?:\s*/\s*(?:INT|EXT)\.)?\s+[A-Z]')
# An explicit shot marker, for prompts that are not screenplays at all.
SHOT = re.compile(r'^\s*(?:\[(?:scene|shot|panel)\s*[0-9]{1,3}\]|(?:SHOT|PANEL|FRAME|BEAT)\s+[0-9]{1,3}\s*[:.\-–—])', re.I)

MARKERS = (SLUG, HEADER, SHOT)


def is_marker(line):
    """True when a line reads as the start of a scene, rather than prose that mentions one."""
    return any(pattern.match(line) for pattern in MARKERS)


def _starts(lines):
    """Which lines begin a unit, and by which rule.

    Slugs win when there are two or more of them: in a screenplay every numbered set-up is its own picture, and the
    `SCENE ONE - OPENING` header above it is a title for the run, not a picture of its own.
    """
    for pattern, name in ((SLUG, 'slug'), (HEADER, 'header'), (SHOT, 'shot')):
        found = [i for i, line in enumerate(lines) if pattern.match(line)]
        if len(found) >= 2:
            return found, name
    return [], None


def detect(text):
    """Split a prompt into scenes.

    Returns a list of `{'title': ..., 'text': ...}`. One entry when the prompt has fewer than two units, which is the
    ordinary case. A unit's `text` starts at a preceding `SCENE` header when one sits directly above it, so each scene
    carries its own name into the prompt.
    """
    text = text or ''
    lines = text.splitlines()
    starts, how = _starts(lines)
    if not starts:
        first = lines[0].strip() if lines else ''
        return [{'title': first, 'text': text.strip()}]

    starts = starts[:MAX_SCENES]
    # A unit begins at its header when one sits directly above it, so the *boundaries* must be those begins: slicing at
    # the next slug would leave the next scene's header stuck to the tail of this one (measured: 101 chars where 60).
    begins = []
    for start in starts:
        begin = start
        if how == 'slug':
            for back in range(start - 1, max(start - 4, -1), -1):
                if not lines[back].strip():
                    continue
                if HEADER.match(lines[back]):
                    begin = back
                break
        begins.append(begin)
    begins = sorted(set(begins))

    scenes = []
    for position, begin in enumerate(begins):
        end = begins[position + 1] if position + 1 < len(begins) else len(lines)
        body = '\n'.join(lines[begin:end]).strip()
        if not body:
            continue
        title = next((lines[i].strip() for i in range(begin, min(begin + 3, len(lines))) if HEADER.match(lines[i])),
                     lines[begin].strip())
        scenes.append({'title': title, 'text': body})
    return scenes or [{'title': '', 'text': text.strip()}]


def count(text):
    """How many pictures this prompt asks for: 1 unless it marks two or more scenes."""
    return len(detect(text))


def summary(scenes):
    """One line for the banner: '6 scenes, one image each', or 'one scene' for the ordinary case."""
    return 'one scene' if len(scenes) <= 1 else '%d scenes, one image each' % len(scenes)
