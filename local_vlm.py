"""local_vlm.py - ask a vision model on this machine a yes/no question about an image.

The cloud judge (`vlm_score.py`) is better at reading small text; this one keeps the images on the box, which is the
only way a figure study can be scored at all. Measured 2026-10-26 with `qwen3-vl:4b`:

  * yes/no questions about content: 2/2 correct, 6-12 s each;
  * **exact transcription is not reliable**: on a render whose second box reads `VAILDATE` it answered `VAIDDATE`. It
    correctly refused to report `VALIDATE`, but a single letter moved - so label scoring stays with deterministic OCR
    (`score_arm.py`) and this model is used for semantics only.

Two operational facts learned the hard way: the model is a *thinking* model whose reasoning shares the token budget, so
`num_predict` below ~120 returns an empty `response` with the answer stuck in `thinking`; and a warm call is 2-12 s
against 60-70 s cold, so the first call of a session should be treated as a load.
"""
import base64
import json
import time
import urllib.request
from pathlib import Path

HOST = 'http://127.0.0.1:11434'
MODEL = 'qwen3-vl:4b'


def available(host=HOST):
    try:
        urllib.request.urlopen(host + '/api/tags', timeout=5)
        return True
    except Exception:
        return False


def ask(image, question, model=MODEL, num_predict=220, host=HOST, timeout=300):
    """Return (answer_text, seconds, thinking_text). Raises on transport failure."""
    body = {'model': model, 'prompt': question,
            'images': [base64.b64encode(Path(image).read_bytes()).decode()],
            'stream': False,
            'options': {'temperature': 0, 'num_predict': num_predict}}
    req = urllib.request.Request(host + '/api/generate', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    out = json.load(urllib.request.urlopen(req, timeout=timeout))
    return (out.get('response') or '').strip(), round(time.monotonic() - started, 1), (out.get('thinking') or '')


def yes_no(image, question, model=MODEL, host=HOST):
    """Return (True/False/None, seconds, raw answer). None means the model did not commit either way."""
    answer, secs, _ = ask(image, question.rstrip() + ' Answer with one word: yes or no.', model=model, host=host)
    low = answer.lower()
    yes = low.startswith('yes') or ' yes' in low[:12]
    no = low.startswith('no') or ' no' in low[:12]
    if yes and not no:
        return True, secs, answer
    if no and not yes:
        return False, secs, answer
    return None, secs, answer


def unload(model=MODEL, host=HOST):
    """Free the VRAM the judge holds, so an image or video model can have it back."""
    body = json.dumps({'model': model, 'keep_alive': 0}).encode()
    req = urllib.request.Request(host + '/api/generate', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return 'unloaded'
    except Exception as e:
        return '%s: %s' % (type(e).__name__, e)
