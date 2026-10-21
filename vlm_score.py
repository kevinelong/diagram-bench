"""vlm_score.py - structure checks and didactic questions, answered by a vision model that sees only the image.

Frozen inputs: prompts-diagrams.json (structure checks) and didactic-questions.json (learner questions).
One scorer for every arm, so its bias cancels between arms; the scorer's identity is recorded in the output.

Usage (cmd.exe):
  .venv\\Scripts\\python.exe vlm_score.py --dir render --out result-vlm-scores.json [--only flow-1]
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = 'https://api.deepseek.com/v1/chat/completions'
MODEL = 'deepseek-flash'
KEY = os.environ.get('DEEPSEEK_API_KEY')

SYSTEM = ('You are grading a diagram. Answer with a JSON object only: {"answer": "yes" or "no", '
          '"reason": "<12 words or fewer>"}. No other text.')


SHORT = ('You are answering a comprehension question about a diagram. Answer in six words or fewer, '
         'plain text, no JSON, no explanation. If the diagram does not show it, answer "unclear".')

JUDGE = ('Compare a reader\'s answer with the reference answer for the same diagram question. Decide whether '
         'the reader answer is semantically correct. Reply with JSON only: {"answer": "yes" or "no"}.')


def ask_short(image_b64, question):
    body = {'model': MODEL, 'messages': [
        {'role': 'system', 'content': SHORT},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': question},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image_b64}}]}],
        'max_tokens': 400, 'temperature': 0}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEY})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.load(r)
    return (payload['choices'][0]['message'].get('content') or '').strip()


def judge_answer(question, reference, candidate):
    body = {'model': MODEL, 'messages': [
        {'role': 'system', 'content': JUDGE},
        {'role': 'user', 'content': 'Question: %s\nReference answer: %s\nReader answer: %s'
                                    % (question, reference, candidate)}],
        'max_tokens': 400, 'temperature': 0}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEY})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.load(r)
    text = payload['choices'][0]['message'].get('content') or ''
    return 'yes' if '"yes"' in text.lower() else 'no'


def ask(image_b64, question):
    body = {'model': MODEL, 'messages': [
        {'role': 'system', 'content': SYSTEM},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': question},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image_b64}}]}],
        'max_tokens': 400, 'temperature': 0}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEY})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.load(r)
    text = payload['choices'][0]['message'].get('content') or ''
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1]).get('answer', '').strip().lower(), len(payload.get('usage', {}))
        except Exception:
            pass
    low = text.lower()
    for word in ('yes', 'no'):
        if word in low:
            return word, None
    return 'unparsed', None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='render')
    ap.add_argument('--out', default='result-vlm-scores.json')
    ap.add_argument('--only', default=None)
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args()

    corpus = json.loads((ROOT / 'prompts-diagrams.json').read_text(encoding='utf-8'))
    didactic = json.loads((ROOT / 'didactic-questions.json').read_text(encoding='utf-8'))['questions']
    answers = json.loads((ROOT / 'didactic-answers.json').read_text(encoding='utf-8'))['answers']
    by_id = {p['id']: p for p in corpus['prompts']}

    images = []
    for png in sorted(Path(args.dir).glob('*.png')):
        pid = png.stem.rsplit('-s', 1)[0]
        if pid not in by_id:
            continue
        if args.only and pid != args.only:
            continue
        images.append((png, pid))

    jobs = []
    for png, pid in images:
        b64 = base64.b64encode(png.read_bytes()).decode()
        for chk in (by_id[pid].get('structure') or by_id[pid].get('checks') or []):
            jobs.append((png.name, pid, 'structure', chk['id'], chk['q'], b64))
        for i, q in enumerate(didactic.get(pid, [])):
            jobs.append((png.name, pid, 'didactic', '%s-d%d' % (pid, i + 1), q, b64))

    results = []
    started = time.monotonic()

    def work(job):
        name, pid, kind, cid, question, b64 = job
        if kind == 'structure':
            answer, _ = ask(b64, question)
            return {'image': name, 'id': pid, 'kind': kind, 'check': cid, 'q': question,
                    'answer': answer, 'correct': 1 if answer == 'yes' else 0}
        candidate = ask_short(b64, question)
        idx = int(cid.rsplit('-d', 1)[1]) - 1
        reference = answers[pid][idx]
        verdict = judge_answer(question, reference, candidate)
        return {'image': name, 'id': pid, 'kind': kind, 'check': cid, 'q': question,
                'reader_answer': candidate, 'reference': reference,
                'answer': verdict, 'correct': 1 if verdict == 'yes' else 0}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(work, jobs):
            results.append(res)
            print('  %-22s %-9s %-8s %s' % (res['image'], res['kind'], res['check'], res['answer']), flush=True)

    # structure: a check passes when the answer is yes; didactic: needs a reference answer, so it is stored raw
    per_image = {}
    for r in results:
        per_image.setdefault(r['image'], {'structure_pass': 0, 'structure_total': 0, 'didactic': {}})
        if r['kind'] == 'structure':
            per_image[r['image']]['structure_total'] += 1
            per_image[r['image']]['structure_pass'] += r['correct']
        else:
            per_image[r['image']].setdefault('didactic_pass', 0)
            per_image[r['image']].setdefault('didactic_total', 0)
            per_image[r['image']]['didactic_total'] += 1
            per_image[r['image']]['didactic_pass'] += r['correct']
            per_image[r['image']]['didactic'][r['check']] = {'reader': r['reader_answer'],
                                                             'reference': r['reference'], 'correct': r['correct']}

    payload = {'scorer': MODEL, 'api': 'deepseek', 'images': len(images), 'questions': len(jobs),
               'elapsed_s': round(time.monotonic() - started, 1),
               'structure_pass_total': sum(v['structure_pass'] for v in per_image.values()),
               'structure_checks_total': sum(v['structure_total'] for v in per_image.values()),
               'didactic_pass_total': sum(v.get('didactic_pass', 0) for v in per_image.values()),
               'didactic_questions_total': sum(v.get('didactic_total', 0) for v in per_image.values()),
               'per_image': per_image, 'raw': results}
    (ROOT / args.out).write_text(json.dumps(payload, indent=1), encoding='utf-8')
    print('\nstructure: %d/%d checks; didactic: %d/%d answers correct; across %d images in %.0fs -> %s'
          % (payload['structure_pass_total'], payload['structure_checks_total'],
             payload['didactic_pass_total'], payload['didactic_questions_total'], len(images),
             payload['elapsed_s'], args.out))


if __name__ == '__main__':
    sys.exit(main())
