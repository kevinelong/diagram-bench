#!/usr/bin/env python
"""Caption a picture with a **local** vision model, for folders that arrive without prompts.

The endpoint is the same OpenAI-compatible one the window already uses for `describe()` and `enhance`
(`127.0.0.1:8080/v1` by default, `ZIMAGE_LLM_URL` overrides it), so nothing new has to be installed to *speak* to it -
only a vision model has to be served there.

Why a caption is not a prompt, and is stored apart: a prompt is what was *asked for* ("a wooden mannequin in the same
seated pose"), a caption is what the picture *looks like* ("a woman sitting in a chair"). Filing by caption organises
appearance; filing by prompt organises intent. The app keeps them in different fields and labels caption groups.
"""

import base64
import json
import os
import urllib.request
from pathlib import Path

DEFAULT_URL = 'http://127.0.0.1:8080/v1'
# A caption does not need the full picture: vision tokens dominate the cost, and measured on this box a 1024 px image
# took ~16 s while the answer never mentioned anything smaller than 512 px could not show.
MAX_PX = 512
# Measured on this box with Qwen2.5-VL-3B Q4 + f16 mmproj at 512 px - used for the time estimate the user is shown.
SECONDS_PER_PICTURE = 3.8
QUESTION = ('Describe this image as a topic and a one-line caption: reply with JSON only, '
            '{"topic": "<2-4 words>", "caption": "<under 30 words>"}.')


class NoModel(RuntimeError):
    """No vision model is answering - the caller must say so, not fall back to a cloud model on its own."""


def base_url():
    return (os.environ.get('ZIMAGE_LLM_URL') or DEFAULT_URL).rstrip('/')


def available(timeout=2.0):
    try:
        with urllib.request.urlopen(base_url() + '/models', timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _parse(text):
    """(topic, caption) from a JSON reply, tolerating a code fence or chatter around it."""
    import re
    match = re.search(r'\{.*\}', text or '', re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            topic = str(data.get('topic') or '').strip().strip('.').lower()
            caption = str(data.get('caption') or '').strip()
            if caption or topic:      # a topic on its own still files the picture; an empty pair does not
                return topic, caption
        except Exception:
            pass
    # an empty reply has no first line: `splitlines()[0]` on '' raised IndexError, found by the test
    first = ((text or '').strip().splitlines() or [''])[0]
    plain = first[:200]
    return '', plain


def _payload_bytes(path):
    """The picture, downscaled to MAX_PX and re-encoded as JPEG. Falls back to the file itself if PIL cannot read it."""
    try:
        import io
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert('RGB')
            if max(im.size) > MAX_PX:
                im.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=88, optimize=True)
        return buf.getvalue(), 'jpeg'
    except Exception:
        return Path(path).read_bytes(), (Path(path).suffix.lower().lstrip('.') or 'png')


def caption(path, timeout=90):
    """(topic, caption) for one picture from the local vision model. Raises NoModel when nothing is serving."""
    payload, suffix = _payload_bytes(path)
    raw = base64.b64encode(payload).decode('ascii')
    body = json.dumps({'model': 'local', 'temperature': 0.2, 'max_tokens': 120, 'messages': [
        {'role': 'user', 'content': [
            {'type': 'text', 'text': QUESTION},
            {'type': 'image_url', 'image_url': {'url': 'data:image/%s;base64,%s' % (suffix, raw)}}]}]}).encode()
    req = urllib.request.Request(base_url() + '/chat/completions', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
    except Exception as e:
        raise NoModel('no vision model at %s (%s)' % (base_url(), type(e).__name__))
    try:
        text = payload['choices'][0]['message'].get('content') or ''
    except Exception:
        raise NoModel('the endpoint answered without a message')
    return _parse(text)
