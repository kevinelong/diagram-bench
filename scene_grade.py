#!/usr/bin/env python
"""Ask the vision model about a picture, so continuity is measured rather than asserted.

Two jobs:

* **grade a scene** - after a scene render, ask whether the image shows the scene it was asked for, and record the answer
  beside the picture (`scene_check` in the sidecar).
* **gather information** - over a sample of the gallery, ask a question with a checkable answer (the usual one: how many
  people are in the image, against the number the prompt stated) and report the agreement rate. That is the baseline any
  consistency work has to beat.

The model, endpoint and key are the ones `vlm_score.py` already uses - one scorer for every question, so its bias cancels
between comparisons. The frozen inputs of that instrument are untouched.

Usage:
  python scene_grade.py --gallery C:/Users/kevin/Pictures/zimage-ui --mode counts --sample 30
  python scene_grade.py --image out/x.png --scene "1. INT. THE LAST POCKET - NIGHT\\nrain on glass, three green lamps"
"""

import argparse
import base64
import json
import os
import re
import statistics
import urllib.request
from pathlib import Path

import vlm_score

COUNT_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
               'ten': 10, 'eleven': 11, 'twelve': 12}
PEOPLE = ('adult', 'woman', 'women', 'man', 'men', 'people', 'person', 'persons', 'figure', 'figures', 'character',
          'characters', 'player', 'players', 'girl', 'girls', 'boy', 'boys', 'face', 'faces')

SCENE_SYSTEM = ('You are checking a storyboard frame against the scene it was made from. Reply with JSON only: '
                 '{"answer": "yes" or "no", "reason": "<12 words or fewer>"}.')

COUNT_SYSTEM = ('Count the people visible in the image. Reply with JSON only: {"answer": "<a number>", "reason": '
                '"<8 words or fewer>"}. If you cannot tell, answer "unclear".')


# A reasoning model can spend the whole budget thinking: measured, not assumed. 300 lost 2 of 5 answers, 900 recovered
# one of those, and the hardest question in the consistency experiment ("how many arms does the figure in the middle
# have") exhausted 2400 twice independently and answered at 6000. Four rungs, so a lost answer is rare rather than silent.
BUDGETS = (300, 900, 2400, 6000)


def _post(body, timeout=180):
    """One call, returning (content, finish_reason).

    `finish_reason` matters: with a reasoning model the content can come back **empty** because the reasoning used the
    whole token budget. That is not the same as "unclear", and reporting it as an abstention is how an instrument lies -
    measured on 5 pictures: 2 returned nothing at 300 tokens, and 1 of those answered at 900.
    """
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise RuntimeError('DEEPSEEK_API_KEY is not set: the grader cannot run')
    req = urllib.request.Request(vlm_score.API, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    choice = payload['choices'][0]
    return (choice['message'].get('content') or '').strip(), str(choice.get('finish_reason') or '')


def b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def _json_reply(text):
    """Pull {answer, reason} out of a reply, tolerating a code fence or chatter around it."""
    match = re.search(r'\{.*\}', text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            return str(data.get('answer', '')).strip().lower(), str(data.get('reason', '')).strip()
        except Exception:
            pass
    return text.strip().lower(), ''


def ask(image_path, question, system):
    """Ask, retrying with a larger budget while the reply is being cut off. Returns (answer, reason).

    `answer` is 'unclear' when the model genuinely would not answer, and 'no-answer' when every budget was consumed by
    the model's own reasoning - the two are reported separately so a lost answer never looks like an abstention.
    """
    payload_image = 'data:image/png;base64,' + b64(image_path)
    last_finish = ''
    for budget in BUDGETS:
        body = {'model': vlm_score.MODEL, 'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': question},
                {'type': 'image_url', 'image_url': {'url': payload_image}}]}],
            'max_tokens': budget, 'temperature': 0}
        content, last_finish = _post(body)
        if content:
            return _json_reply(content)
    return ('no-answer', 'reasoning used every budget (last finish_reason: %s)' % (last_finish or '?'))


def strip_directions(text):
    """A scene's CAMERA/LIGHT/SOUND/MOOD lines are directions, not things a still can show; keep the action prose."""
    keep = []
    for line in (text or '').splitlines():
        stripped = line.strip()
        if re.match(r'^(CAMERA|LIGHT|SOUND|MOOD|CUT TO|FADE)\b', stripped, re.I):
            continue
        keep.append(stripped)
    return ' '.join(' '.join(keep).split())


def grade_scene(image_path, scene_text, title=''):
    """Would this image read as the scene it came from? Returns {'answer', 'reason', 'question'}."""
    body = strip_directions(scene_text)[:900]
    question = ('The scene to show:\n%s\n\nDoes this image show that scene - the place, the people and the action? '
                'Answer yes or no, and name the one thing that is most clearly missing if not.' % body)
    answer, reason = ask(image_path, question, SCENE_SYSTEM)
    return {'answer': answer, 'reason': reason, 'title': title, 'question': question}


def count_from_prompt(prompt):
    """The single head count a prompt states, or None when the prompt is ambiguous.

    Two rules, both learned the hard way. The *earliest* phrase wins (iterating words made "six women ... three layers"
    read as three). And the phrase must be unambiguous: a long compositional prompt mentions several numbers, and
    guessing which one is the cast is exactly the kind of silent error this instrument exists to avoid - so it answers
    None and the picture is simply not measured.
    """
    low = ' '.join((prompt or '').split()).lower()
    found = []
    for word, number in COUNT_WORDS.items():
        # the people noun may sit a few adjectives away ("three very strong fit toned muscled adult women") but not
        # across a preposition: "three green lamps above two women" is about the lamps
        pattern = (r'\b%s\b\s+(?:(?!(?:above|behind|near|beside|by|on|in|at|with|from|under|over|across)\b)\w+\s+)'
                   r'{0,8}(?:%s)\b' % (word, '|'.join(PEOPLE)))
        for match in re.finditer(pattern, low):
            found.append((match.start(), number))
    if not found:
        return None
    found.sort()
    numbers = {number for _pos, number in found}
    if len(numbers) > 1:
        return None            # several different counts stated: the prompt is ambiguous, so it is not measured
    return found[0][1]


def count_people(image_path):
    question = 'How many people (or human figures) are visible? Count every figure that is a person.'
    answer, reason = ask(image_path, question, COUNT_SYSTEM)
    number = None
    match = re.search(r'\d+', answer)
    if match:
        number = int(match.group(0))
    return number, answer, reason


def attach_to_sidecar(image_path, verdict):
    """Record the grader's answer beside the picture, so a disputed frame stays traceable."""
    sidecar = Path(image_path).with_suffix('.json')
    if not sidecar.is_file():
        return False
    try:
        meta = json.loads(sidecar.read_text(encoding='utf-8'))
    except Exception:
        return False
    meta['scene_check'] = {'scorer': vlm_score.MODEL, 'answer': verdict.get('answer'),
                           'reason': verdict.get('reason'), 'title': verdict.get('title')}
    sidecar.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding='utf-8')
    return True


def run_counts(gallery, sample, out):
    """Does the picture hold the number of people the prompt asked for? A baseline, not a verdict."""
    root = Path(gallery)
    candidates = []
    for sidecar in sorted(root.glob('*.json')):
        if sidecar.stem.endswith('-hashes'):
            continue
        try:
            meta = json.loads(sidecar.read_text(encoding='utf-8'))
        except Exception:
            continue
        stated = count_from_prompt(meta.get('prompt'))
        png = sidecar.with_suffix('.png')
        # a contact sheet is a composite: 16 tiles of two figures is 32 figures by construction, not a count failure
        composite = '_sheet' in sidecar.stem or bool(meta.get('tiles'))
        if stated and png.is_file() and not composite:
            candidates.append((png, stated, meta.get('prompt')))
    step = max(1, len(candidates) // sample) if sample else 1
    chosen = candidates[::step][:sample]
    print('prompts stating a head count: %d; sampling %d' % (len(candidates), len(chosen)))

    rows, errors = [], []
    for png, stated, prompt in chosen:
        try:
            got, raw, reason = count_people(png)
        except Exception as e:
            errors.append((png.name, '%s: %s' % (type(e).__name__, e)))
            continue
        rows.append((png.name, stated, got, raw, reason, prompt))
        print('  %-46s stated %2d, counted %s' % (png.name[:46], stated, got))

    lost = sum(1 for r in rows if r[2] is None and 'no-answer' in str(r[3]))
    lines = ['# Does a prompt\'s head count survive into the picture?\n',
             'Grader: `%s` via `%s`. %d pictures whose prompt states a count, one question each: '
             '"how many people are visible?".\n' % (vlm_score.MODEL, vlm_score.API, len(rows)),
             '`no-answer` rows are questions the model\'s own reasoning consumed the token budget on - a lost answer, '
             'not an abstention.\n',
             '| picture | stated | counted | grader says | prompt |', '|---|---|---|---|---|']
    for name, stated, got, raw, reason, prompt in rows:
        lines.append('| %s | %d | %s | %s | %s |' % (name[:44], stated, 'unclear' if got is None else got,
                                                     raw[:18], prompt[:34].replace('|', '/')))
    scored = [(stated, got) for _n, stated, got, _raw, _r, _p in rows if got is not None]
    if scored:
        exact = sum(1 for stated, got in scored if stated == got)
        within1 = sum(1 for stated, got in scored if abs(stated - got) <= 1)
        lines.append('')
        lines.append('**%d of %d exact (%.0f %%), %d within one (%.0f %%)); %d unreadable (of which %d lost to the '
                     'budget); %d errors.**' % (
                         exact, len(scored), 100 * exact / len(scored), within1, 100 * within1 / len(scored),
                         len(rows) - len(scored), lost, len(errors)))
        deltas = [got - stated for stated, got in scored]
        lines.append('')
        lines.append('Median error: %+.0f figures; the grader under-counts %d, over-counts %d.' % (
            statistics.median(deltas), sum(1 for d in deltas if d < 0), sum(1 for d in deltas if d > 0)))
    for name, why in errors:
        lines.append('- error on %s: %s' % (name, why))
    text = '\n'.join(lines) + '\n'
    if out:
        Path(out).write_text(text, encoding='utf-8')
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', default='')
    ap.add_argument('--scene', default='')
    ap.add_argument('--gallery', default='')
    ap.add_argument('--mode', choices=('counts', 'scene'), default='counts')
    ap.add_argument('--sample', type=int, default=25)
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    if a.mode == 'scene':
        if not a.image or not a.scene:
            raise SystemExit('--mode scene needs --image and --scene')
        verdict = grade_scene(a.image, a.scene)
        stored = attach_to_sidecar(a.image, verdict)
        print('answer: %s (%s)%s' % (verdict['answer'], verdict['reason'],
                                     '  [recorded in the sidecar]' if stored else ''))
        return
    if not a.gallery:
        raise SystemExit('--mode counts needs --gallery')
    run_counts(a.gallery, a.sample, a.out)


if __name__ == '__main__':
    main()
