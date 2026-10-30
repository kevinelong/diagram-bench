#!/usr/bin/env python3
"""web/server.py - the gallery in a browser, on the standard library alone.

    python web/server.py --gallery C:/Users/kevin/Pictures/zimage-ui --port 8123

The Tk window is the reference for what "organising" and "review" mean: prompt-similarity folders, an A/B ranking
that never deletes anything, and a batch describe for a folder that arrived from another pipeline. Everything that
decides those things lives in the repo root already - `group_prompts.plan` for the folders, `pair_review` for the
pair schedule and session state, `pair_rank` for the wins written into the pictures' own sidecars - so this server is
a transport over those modules and deliberately reimplements none of them. Only two pieces are local: reading the
sidecars for the plan's rows (the same row shape `zimage_ui._compute_groups` builds) and the thumbnail builder.

Why an HTTP server at all: the picture that has to move is a JPEG, the unit of state is a JSON file, and the browser
already has a keyboard, a focus ring and a colour-scheme preference. There is nothing here that Flask would make
shorter.

Endpoints (all JSON unless said otherwise)

    GET  /                       the page
    GET  /api/folders            the plan: [{id, label, title, count, series, kind, undescribed}]
    GET  /api/folder/<id>        the folder's pictures: path, prompt, created, has_prompt, record
    GET  /thumb?path=...         JPEG thumbnail (`.thumbs/<stem>.jpg`, built once with PIL when absent)
    GET  /image?path=...         the picture itself, for the A/B view and the large result tiles
    GET  /api/review/<id>        the session state and the next pair
    POST /api/decide             {id, a, b, choice} with choice in left_wins|right_wins|tie
    GET  /api/rank/<id>          the rank order and each picture's wins
    POST /api/reset/<id>         zero this folder's record and rebuild the schedule
    POST /api/describe-all       {id} - caption every picture in the folder that has no prompt (pollable job)
    GET  /api/job/<job>          the describe job's progress; POST /api/job/<job>/cancel stops it
    GET  /api/llm                whether the local vision model is up, and how many pictures still need a prompt

Nothing is deleted by any endpoint, no model is loaded, and every write goes to the gallery's own sidecars (the
describe job) or to the review state folder (the decisions) - the same two places the Tk app writes.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import group_prompts                                    # noqa: E402  (the folders - not reimplemented)
import pair_rank                                         # noqa: E402  (the wins in the sidecars)
import pair_review                                       # noqa: E402  (the pair schedule and the session)

HERE = Path(__file__).resolve().parent
IMAGE_SUFFIXES = ('.png', '.webp', '.jpg', '.jpeg')
THUMB_DIR = '.thumbs'
THUMB_PX = 336                       # zimage_ui.THUMB_PX: one cache, shared with the Tk window
LLM_DEFAULT = 'http://127.0.0.1:8080/v1'
CAPTION_PROMPT = ('Describe this image as a single image-generation prompt, under 40 words. '
                  'Reply with the prompt only.')
CAPTION_TIMEOUT = 90
MAX_BODY = 1 << 21

STATIC = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css'}
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.jpg': 'image/jpeg', '.png': 'image/png',
        '.webp': 'image/webp', '.ico': 'image/x-icon'}


class HttpError(Exception):
    """An error with the status code the browser should see."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --- the two pieces of the Tk app this file does own --------------------------------------------------------------

def thumb_for(png, cache_root=None):
    """Return a cached thumbnail for `png`, building it once with PIL when missing or older than the picture.

    Same contract as `zimage_ui.thumb_for` with `cache_root=None`: `<picture folder>/.thumbs/<stem>.jpg`, validated
    by mtime, and `.thumbs/.px` records the size so a change to THUMB_PX rebuilds instead of serving a blurrier file.
    It is re-declared here (rather than imported) because importing zimage_ui pulls in tkinter; the gallery never needs
    a display, and a cache key that matches the Tk app's is the whole of the compatibility that matters.
    """
    png = Path(png)
    root = Path(cache_root) if cache_root else png.parent
    cache = root / THUMB_DIR
    try:
        cache.mkdir(parents=True, exist_ok=True)
        out = cache / (png.stem + '.jpg')
        marker = cache / '.px'
        note = str(int(THUMB_PX))
        try:
            recorded = marker.read_text(encoding='utf-8').strip()
        except OSError:
            recorded = ''
        if recorded != note:
            marker.write_text(note, encoding='utf-8')
            try:
                out.unlink()
            except OSError:
                pass
        if out.exists() and out.stat().st_mtime >= png.stat().st_mtime:
            return out
        from PIL import Image
        with Image.open(png) as im:
            im.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
            im.convert('RGB').save(out, 'JPEG', quality=82, optimize=True)
        return out
    except Exception:
        return None


def caption_picture(path, llm_url):
    """Ask the local vision model for a prompt describing this picture. Returns text, or raises.

    The request body is byte-for-byte the one `zimage_ui.caption_picture` sends (model 'local', temperature 0.4,
    max_tokens 160, the picture inline as a data URL), because the point of the button is to produce the *same*
    prompts the Tk app produces - not a second flavour of caption that would group differently.
    """
    raw = Path(path).read_bytes()
    b64 = base64.b64encode(raw).decode('ascii')
    body = json.dumps({'model': 'local', 'temperature': 0.4, 'max_tokens': 160, 'messages': [
        {'role': 'user', 'content': [
            {'type': 'text', 'text': CAPTION_PROMPT},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}}]}]}).encode()
    req = urllib.request.Request(llm_url.rstrip('/') + '/chat/completions', data=body,
                                 headers={'Content-Type': 'application/json'})
    data = json.loads(urllib.request.urlopen(req, timeout=CAPTION_TIMEOUT).read())
    return data['choices'][0]['message']['content'].strip()


def llm_state(url, timeout=2.0):
    """(ok, why) for the configured endpoint. Local-only, and a 2 s probe so a dead one fails fast.

    The two failures are kept apart because they need different fixes: a URL pointing off the machine is refused on
    principle (nothing is uploaded from this app), while a loopback endpoint that does not answer is simply not started.
    """
    host = urlparse(url).hostname or ''
    if host not in ('127.0.0.1', 'localhost', '::1'):
        return False, 'this is local-only: --llm points at %s' % host
    try:
        urllib.request.urlopen(url.rstrip('/') + '/models', timeout=timeout).read()
        return True, None
    except Exception as e:
        return False, ('the local model at %s is not reachable (%s) - start it, or pass --llm'
                       % (url, type(e).__name__))


def sidecar_paths(png):
    return Path(png).with_suffix('.json')


def has_words(png):
    """Does this picture already say what it is? The Tk app's `_uncaptioned`, on one picture."""
    meta = pair_rank.read_meta(png)
    return bool((meta.get('prompt') or '').strip() or (meta.get('caption') or '').strip())


def session_record(state):
    """{name: (wins, losses, ties)} for *this* session. The schedule module owns the definition; do not restate it."""
    fn = getattr(pair_review, '_session_record', None)
    if fn is None:                                   # a renamed private helper must not take the whole server down
        return {}
    try:
        return fn(state)
    except Exception:
        return {}


def pair_id(a, b):
    """The identity of a bout: the two file names, sorted - `pair_review._pair_id`, which `pair_rank` also keys on."""
    return pair_review._pair_id(a, b)


# --- the gallery, its plan, and the review sessions ---------------------------------------------------------------

class Gallery:
    """One folder of pictures, the prompt plan over it, and the A/B sessions kept in the review state folder."""

    def __init__(self, root, state_dir=None, llm_url=LLM_DEFAULT):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise SystemExit('no such gallery folder: %s' % self.root)
        self.state_dir = (Path(state_dir).expanduser().resolve() if state_dir
                          else Path(pair_review.STATE_DIR))
        self.llm_url = (llm_url or LLM_DEFAULT).rstrip('/')
        self.verbose = False
        self._lock = threading.RLock()
        self._jobs_lock = threading.Lock()
        self._jobs = {}
        self._job_order = []
        self._signature = None
        self._folders = None
        self._pictures = 0
        self._seconds = 0.0

    # ---- reading the gallery

    def scan(self):
        """(path, mtime) newest first, plus the sidecar mtimes. The same walk `zimage_ui._scan` does, uncached."""
        rows, sidecars = [], {}
        try:
            with os.scandir(self.root) as it:
                for entry in it:
                    low = entry.name.lower()
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            continue
                        if low.endswith('.json'):
                            sidecars[Path(entry.name).stem] = entry.stat().st_mtime
                        elif low.endswith(IMAGE_SUFFIXES) and entry.is_file():
                            rows.append((Path(entry.path), entry.stat().st_mtime))
                    except OSError:
                        continue
        except OSError:
            return [], {}
        rows.sort(key=lambda r: -r[1])
        return rows, sidecars

    def refresh(self):
        """The plan folders, rebuilt only when a picture or a sidecar changed.

        Grouping 1,000 prompts costs about a second; the folder list is fetched on every visit, so the scan signature
        (names and mtimes of the pictures *and* of the sidecars) decides whether last time's answer still holds. A
        describe run therefore re-groups on the next look without an explicit invalidation call.
        """
        with self._lock:
            rows, sidecars = self.scan()
            signature = (tuple((str(p), round(m, 3)) for p, m in rows),
                         tuple(sorted((k, round(v, 3)) for k, v in sidecars.items())))
            if signature == self._signature and self._folders is not None:
                return self._folders
            started = time.time()
            self._folders = self._build(rows)
            self._pictures = len(rows)
            self._seconds = round(time.time() - started, 2)
            self._signature = signature
            return self._folders

    def _build(self, rows):
        """The rows `group_prompts.plan` wants, then the plan split into addressable folders.

        Row shape, field for field, from `zimage_ui._compute_groups`: a picture with no prompt but a caption is filed
        by what it looks like (and the folder says so), and the lineage root travels with the row so a family follows
        where the series started rather than the last instruction.
        """
        items = []
        for path, stamp in rows:
            meta = pair_rank.read_meta(path)
            prompt = (meta.get('prompt') or '').strip()
            caption = (meta.get('caption') or '').strip()
            topic = (meta.get('topic') or '').strip()
            filed = prompt
            if not filed and (caption or topic):
                filed = ' '.join(x for x in (topic, caption) if x)
            created = meta.get('created') or datetime.fromtimestamp(stamp).isoformat(timespec='seconds')
            items.append({
                'path': str(path), 'name': path.name, 'prompt': filed, 'own_prompt': prompt,
                'caption': caption, 'created': created,
                'root': meta.get('lineage_root'), 'lineage': meta.get('lineage'), 'topic': topic,
                'has_prompt': bool(prompt), 'caption_only': bool(not prompt and (caption or topic)),
                'wins': int(meta.get('wins') or 0), 'losses': int(meta.get('losses') or 0),
                'ties': int(meta.get('ties') or 0), 'bouts': int(meta.get('bouts') or 0),
            })
        plan = group_prompts.plan(items)
        folders = []

        def series_of(group_items):
            return len({tuple(r.get('lineage') or [r.get('prompt') or '']) for r in group_items})

        # The newest DEFAULT_RECENT pictures are held out of the folders by `plan` (the Tk strip). A browser has no
        # strip to put them in, so they become the first folder - otherwise the newest renders could be seen nowhere
        # and could never be reviewed.
        head = plan.get('recent') or []
        if head:
            folders.append({'kind': 'recent', 'label': 'recent (%d)' % len(head),
                            'title': 'the %d newest pictures, kept out of the folders' % len(head),
                            'items': head, 'oldest': head[0].get('created'), 'newest': head[-1].get('created'),
                            'series': series_of(head)})
        for group in plan.get('groups') or []:
            members = group.get('items') or []
            if not members:
                continue
            label = group.get('label') or 'folder'
            if all(row.get('caption_only') for row in members):
                label = 'caption: ' + label.strip()          # the folder is what the pictures look like, not a prompt
            folders.append({'kind': 'group', 'label': label, 'title': group.get('title') or label,
                            'items': members, 'oldest': group.get('oldest'), 'newest': group.get('newest'),
                            'series': int(group.get('series') or series_of(members))})
        leftovers = [row for row in (plan.get('ungrouped') or []) if row.get('path')]
        if leftovers:
            folders.append({'kind': 'unfiled', 'label': 'unfiled (%d)' % len(leftovers),
                            'title': 'pictures no folder can hold - a prompt is what a folder is made of',
                            'items': leftovers, 'oldest': None, 'newest': None,
                            'series': series_of(leftovers)})
        for folder in folders:
            # The id is the review session's key (`pair_review.state_key`), so a folder and its A/B session are the
            # same identity: add a picture and both move together, which is exactly the Tk app's rule.
            folder['id'] = pair_review.state_key([row['path'] for row in folder['items']])
        return folders

    # ---- the folder payloads

    def folder(self, fid):
        for folder in self.refresh():
            if folder['id'] == fid:
                return folder
        raise HttpError(404, 'no folder %s in this gallery - it was regrouped, so reload the folder list' % fid)

    def folders_payload(self):
        folders = self.refresh()
        out = []
        for folder in folders:
            items = folder['items']
            out.append({
                'id': folder['id'], 'label': folder['label'], 'title': folder.get('title') or folder['label'],
                'kind': folder['kind'], 'count': len(items),
                'series': int(folder.get('series') or 1),
                'newest': folder.get('newest'), 'oldest': folder.get('oldest'),
                'undescribed': sum(1 for row in items
                                   if not row['has_prompt'] and not (row.get('caption') or '').strip()),
            })
        return {'gallery': str(self.root), 'pictures': self._pictures, 'folders': out,
                'seconds': self._seconds, 'recent_cut': group_prompts.DEFAULT_RECENT,
                'review_state_dir': str(self.state_dir)}

    def folder_payload(self, fid):
        folder = self.folder(fid)
        items = sorted(folder['items'], key=lambda r: r.get('created') or '', reverse=True)
        out = [{
            'path': row['path'], 'name': row['name'], 'prompt': row.get('prompt') or '',
            'created': row.get('created') or '', 'has_prompt': bool(row.get('has_prompt')),
            'caption_only': bool(row.get('caption_only')), 'caption': row.get('caption') or '',
            'wins': int(row.get('wins') or 0), 'losses': int(row.get('losses') or 0),
            'ties': int(row.get('ties') or 0), 'bouts': int(row.get('bouts') or 0),
        } for row in items]
        return {'id': fid, 'label': folder['label'], 'title': folder.get('title') or folder['label'],
                'kind': folder['kind'], 'count': len(out), 'series': int(folder.get('series') or 1),
                'items': out, 'undescribed': sum(1 for row in out if not row['has_prompt'] and not row['caption']),
                'thumb_px': THUMB_PX}

    # ---- the review session

    def state_for(self, paths):
        key = pair_review.state_key(paths)
        state = pair_review.load_state(key, state_dir=self.state_dir)
        state['key'] = key
        pair_review.build(paths, state, hash_store=Path(self.state_dir) / ('%s-hashes.json' % key))
        return key, state

    def _advance(self, state):
        """The next pair, filling a new round when the last one is decided - the rule `_review_show_next` uses."""
        queue = pair_review.remaining(state)
        added = 0
        if not queue:
            more = pair_review.next_round(state)
            if more:
                added = len(more)
                queue = pair_review.remaining(state)
        return queue, added

    def _payload(self, fid, folder, key, state, queue, added=0):
        # The pair count grows as rounds are added, so it is read from the schedule rather than from `state['total']`
        # (which is frozen at round one and would print "8 of 4" after a second round).
        decided = len(state.get('decisions') or {})
        total = len(state.get('pairs') or [])
        record = session_record(state)
        pair = [queue[0][0], queue[0][1]] if queue else None
        ordered = pair_review.order(state)
        items = []
        for row in sorted(folder['items'], key=lambda r: r.get('created') or '', reverse=True):
            wins, losses, ties = record.get(row['name'], (0, 0, 0))
            items.append({'path': row['path'], 'name': row['name'], 'prompt': row.get('prompt') or '',
                          'has_prompt': bool(row.get('has_prompt')), 'created': row.get('created') or '',
                          'wins': wins, 'losses': losses, 'ties': ties})
        round_now = int(state.get('round') or 1)
        target = int(state.get('target_rounds') or 1)
        return {
            'id': fid, 'key': key, 'label': folder['label'], 'kind': folder['kind'],
            'count': len(folder['items']), 'pair': pair, 'queue': len(queue), 'advanced': added,
            'upcoming': [[a, b] for a, b, _d in queue[:8]],
            'decided': decided, 'total': total,
            'round': round_now, 'target_rounds': target,
            'exhaustive': bool(state.get('exhaustive')), 'settled': bool(state.get('settled')),
            'done': pair is None,
            'stage': 'every pair' if state.get('exhaustive') else 'round %d of %d' % (round_now, target),
            'minutes_left': round(pair_review.estimate_minutes(queue), 2),
            'paths': list(state.get('paths') or []),
            'items': items,
            'order': [{'path': p, 'name': Path(p).name,
                       'wins': record.get(Path(p).name, (0, 0, 0))[0],
                       'losses': record.get(Path(p).name, (0, 0, 0))[1],
                       'ties': record.get(Path(p).name, (0, 0, 0))[2]} for p in ordered],
        }

    def review_payload(self, fid):
        folder = self.folder(fid)
        paths = [row['path'] for row in folder['items']]
        if len(paths) < 2:
            raise HttpError(400, 'that folder has %d picture - nothing to pair' % len(paths))
        key, state = self.state_for(paths)
        queue, added = self._advance(state)
        pair_review.save_state(state, state_dir=self.state_dir)
        return self._payload(fid, folder, key, state, queue, added)

    def decide(self, fid, a, b, choice):
        """Record one bout from the rank review. Deletion choices are refused: this view ranks, it never removes."""
        folder = self.folder(fid)
        paths = [row['path'] for row in folder['items']]
        if choice not in pair_rank.WIN_CHOICES:
            raise HttpError(400, 'choice must be one of %s - ranking never deletes a picture'
                            % ', '.join(pair_rank.WIN_CHOICES))
        if not a or not b or a == b:
            raise HttpError(400, 'a bout needs two different pictures')
        key, state = self.state_for(paths)
        allowed = set(state.get('paths') or [])
        if a not in allowed or b not in allowed:
            raise HttpError(400, 'that pair is not in this folder')
        if pair_id(a, b) in (state.get('decisions') or {}):
            raise HttpError(409, 'that pair has already been decided')
        pair_rank.record(state, a, b, choice)            # writes wins/losses/ties into both sidecars
        queue, added = self._advance(state)              # ... and a finished round adds the next one here
        pair_review.save_state(state, state_dir=self.state_dir)
        return self._payload(fid, folder, key, state, queue, added)

    def rank_payload(self, fid):
        folder = self.folder(fid)
        paths = [row['path'] for row in folder['items']]
        key, state = self.state_for(paths)
        record = session_record(state)
        ordered = pair_review.order(state)
        entries = []
        for rank, path in enumerate(ordered, 1):
            wins, losses, ties = record.get(Path(path).name, (0, 0, 0))
            entries.append({'rank': rank, 'path': path, 'name': Path(path).name,
                            'wins': wins, 'losses': losses, 'ties': ties,
                            'lifetime': pair_rank.record_of(path)})
        decided = len(state.get('decisions') or {})
        total = len(state.get('pairs') or [])
        return {'id': fid, 'key': key, 'label': folder['label'], 'count': len(paths),
                'order': ordered, 'entries': entries,
                'wins': {entry['path']: entry['wins'] for entry in entries},
                'decided': decided, 'total': total,
                'round': int(state.get('round') or 1),
                'target_rounds': int(state.get('target_rounds') or 1),
                'settled': bool(state.get('settled')),
                'done': not pair_review.remaining(state)}

    def reset(self, fid):
        """Zero this folder's record and start the schedule again, which is what "Reset and start over" promises.

        The Tk reset keeps the old pair list and the round counter, so a restarted multi-round review would ask round
        one again and then declare itself finished. The counters here are cleared through `pair_rank.reset_wins` (the
        sidecars, same function) and the schedule is rebuilt from the paths, so the rounds run again too.
        """
        folder = self.folder(fid)
        paths = [row['path'] for row in folder['items']]
        if len(paths) < 2:
            raise HttpError(400, 'that folder has %d picture - nothing to pair' % len(paths))
        touched = pair_rank.reset_wins(paths)
        key = pair_review.state_key(paths)
        state = {'key': key, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'decisions': {}}
        pair_review.build(paths, state, hash_store=Path(self.state_dir) / ('%s-hashes.json' % key))
        pair_review.save_state(state, state_dir=self.state_dir)
        queue, added = self._advance(state)
        payload = self._payload(fid, folder, key, state, queue, added)
        payload['cleared'] = touched
        return payload

    # ---- describe-all: the local vision model, one picture at a time

    def llm_payload(self):
        folders = self.refresh()
        pending = sum(1 for folder in folders for row in folder['items']
                      if not row['has_prompt'] and not (row.get('caption') or '').strip())
        ok, why = llm_state(self.llm_url)
        return {'url': self.llm_url, 'ok': ok, 'problem': why, 'undescribed': pending}

    def describe_all(self, fid=None):
        """Start captioning every picture here that has no prompt or caption. Returns the job to poll."""
        if fid:
            folder = self.folder(fid)
            paths = [row['path'] for row in folder['items']]
            label = folder['label']
        else:
            paths = [str(path) for path, _m in self.scan()[0]]
            label = self.root.name or str(self.root)
        todo = [path for path in paths if not has_words(path)]
        if not todo:
            return {'job': None, 'total': 0, 'label': label,
                    'message': 'every picture here already has a prompt or a caption'}
        ok, why = llm_state(self.llm_url)
        if not ok:
            raise HttpError(503, why)
        job = self._start_job(todo, label)
        return {'job': job['id'], 'total': len(todo), 'label': label, 'llm': self.llm_url,
                'poll': '/api/job/' + job['id'],
                'message': 'describing %d picture%s - about %d seconds'
                           % (len(todo), '' if len(todo) == 1 else 's', 2 * len(todo))}

    def _start_job(self, todo, label=None):
        job = {'id': uuid.uuid4().hex[:12], 'label': label, 'total': len(todo), 'done': 0, 'failed': 0,
               'current': None, 'state': 'running', 'error': None, 'errors': [],
               'started': datetime.now().isoformat(timespec='seconds'), 'finished': None,
               'llm': self.llm_url, 'cancel': False, 'todo': list(todo)}
        with self._jobs_lock:
            self._jobs[job['id']] = job
            self._job_order.append(job['id'])
            while len(self._job_order) > 32:              # a long evening must not keep every finished job in memory
                stale = self._job_order.pop(0)
                self._jobs.pop(stale, None)
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return job

    def _run_job(self, job):
        """`zimage_ui.describe_missing`'s loop: the caption becomes the prompt, marked where it came from."""
        for path in job['todo']:
            if job['cancel']:
                job['state'] = 'cancelled'
                break
            job['current'] = Path(path).name
            try:
                text = caption_picture(path, self.llm_url)
            except Exception as e:
                job['failed'] += 1
                job['errors'] = (job['errors'] + ['%s: %s' % (Path(path).name, e)])[-5:]
                continue
            if not text:
                job['failed'] += 1
                continue
            try:
                meta = pair_rank.read_meta(path)
                if not meta:
                    meta = {'image': str(path)}
                meta.setdefault('created', datetime.fromtimestamp(
                    Path(path).stat().st_mtime).isoformat(timespec='seconds'))
                meta['caption'] = text
                meta['prompt'] = text
                meta['prompt_from'] = 'describe'
                meta['described_at'] = datetime.now().isoformat(timespec='seconds')
                if not pair_rank.write_meta(path, meta):
                    raise OSError('could not write the sidecar')
                job['done'] += 1
            except Exception as e:
                job['failed'] += 1
                job['errors'] = (job['errors'] + ['%s: %s' % (sidecar_paths(path).name, e)])[-5:]
        job['current'] = None
        # A cancel that arrives after the last picture has no iteration left to notice it: the state is resolved here
        # so a job can never sit in 'cancelling' for the client to poll forever.
        if job['state'] == 'cancelling':
            job['state'] = 'cancelled'
        elif job['state'] == 'running':
            job['state'] = 'done'
        job['finished'] = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            self._signature = None                        # the prompts changed: the next look re-groups
        if self.verbose:
            print('describe job %s: %d done, %d failed' % (job['id'], job['done'], job['failed']))

    def job_payload(self, jid):
        with self._jobs_lock:
            job = self._jobs.get(jid)
            if job is None:
                raise HttpError(404, 'no such job: %s' % jid)
            return {'id': job['id'], 'state': job['state'], 'total': job['total'], 'done': job['done'],
                    'failed': job['failed'], 'current': job['current'], 'error': job['error'],
                    'errors': job['errors'], 'started': job['started'], 'finished': job['finished'],
                    'llm': job['llm'], 'label': job['label']}

    def cancel_job(self, jid):
        with self._jobs_lock:
            job = self._jobs.get(jid)
            if job is None:
                raise HttpError(404, 'no such job: %s' % jid)
            if job['state'] == 'running':
                job['cancel'] = True
                job['state'] = 'cancelling'
            return {'id': jid, 'state': job['state'], 'done': job['done'], 'total': job['total']}


# --- HTTP ---------------------------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = 'zimage-web/1.0'

    # -- plumbing

    @property
    def gallery(self):
        return self.server.gallery

    def log_message(self, fmt, *args):
        if getattr(self.server.gallery, 'verbose', False):
            sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _send(self, code, ctype, body, cache='no-cache'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command == 'HEAD':
            return
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, 'application/json; charset=utf-8',
                   json.dumps(obj, ensure_ascii=False).encode('utf-8'), cache='no-store')

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise HttpError(413, 'request body too large')
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode('utf-8'))
        except Exception:
            raise HttpError(400, 'body is not JSON')
        if not isinstance(data, dict):
            raise HttpError(400, 'body must be a JSON object')
        return data

    def _static(self, name):
        path = HERE / name
        try:
            body = path.read_bytes()
        except OSError:
            raise HttpError(404, 'missing static file %s' % name)
        self._send(200, MIME.get(path.suffix, 'application/octet-stream'), body)

    def _local_path(self, query, must_exist=True):
        """`path` from the query string, resolved, and refused unless it is a picture inside the gallery."""
        raw = (query.get('path') or [''])[0]
        if not raw:
            raise HttpError(400, 'path is required')
        target = Path(raw)
        if not target.is_absolute():
            target = self.gallery.root / raw
        try:
            resolved = target.resolve()
        except OSError:
            raise HttpError(400, 'unreadable path')
        if resolved != self.gallery.root and self.gallery.root not in resolved.parents:
            raise HttpError(403, 'that file is outside the gallery')
        if resolved.suffix.lower() not in IMAGE_SUFFIXES:
            raise HttpError(403, 'not a picture: %s' % resolved.name)
        if must_exist and not resolved.is_file():
            raise HttpError(404, 'no such picture: %s' % resolved.name)
        return resolved

    def _id(self, path, prefix):
        return unquote(path[len(prefix):]).strip('/')

    # -- GET

    def do_GET(self):
        self._route('GET')

    def do_HEAD(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if method == 'GET':
                self._get(path, query)
            else:
                self._post(path, query)
        except HttpError as e:
            self._json({'error': e.message}, e.code)
        except KeyError as e:
            self._json({'error': str(e).strip("'")}, 404)
        except ValueError as e:
            self._json({'error': str(e)}, 400)
        except Exception as e:                                  # a broken request must not take the server down
            traceback.print_exc()
            self._json({'error': '%s: %s' % (type(e).__name__, e)}, 500)

    def _get(self, path, query):
        g = self.gallery
        if path in STATIC:
            return self._static(STATIC[path])
        if path == '/favicon.ico':
            return self._send(204, 'image/x-icon', b'')
        if path == '/thumb':
            target = self._local_path(query)
            got = thumb_for(target)
            if got is None:
                raise HttpError(500, 'could not build a thumbnail for %s' % target.name)
            return self._send(200, 'image/jpeg', Path(got).read_bytes(), cache='public, max-age=600')
        if path == '/image':
            target = self._local_path(query)
            ctype = MIME.get(target.suffix.lower(), 'application/octet-stream')
            return self._send(200, ctype, target.read_bytes(), cache='public, max-age=60')
        if path == '/api/folders':
            return self._json(g.folders_payload())
        if path == '/api/llm':
            return self._json(g.llm_payload())
        if path.startswith('/api/folder/'):
            return self._json(g.folder_payload(self._id(path, '/api/folder/')))
        if path.startswith('/api/review/'):
            return self._json(g.review_payload(self._id(path, '/api/review/')))
        if path.startswith('/api/rank/'):
            return self._json(g.rank_payload(self._id(path, '/api/rank/')))
        if path.startswith('/api/job/'):
            return self._json(g.job_payload(self._id(path, '/api/job/')))
        raise HttpError(404, 'no such route: %s' % path)

    def _post(self, path, query):
        g = self.gallery
        if path == '/api/decide':
            body = self._body()
            fid = (body.get('id') or '').strip()
            if not fid:
                raise HttpError(400, 'id is required')
            return self._json(g.decide(fid, body.get('a') or '', body.get('b') or '',
                                       (body.get('choice') or '').strip()))
        if path.startswith('/api/reset/'):
            return self._json(g.reset(self._id(path, '/api/reset/')))
        if path == '/api/describe-all':
            body = self._body()
            return self._json(g.describe_all((body.get('id') or '').strip() or None), 202)
        if path.startswith('/api/job/') and path.endswith('/cancel'):
            return self._json(g.cancel_job(self._id(path[:-len('/cancel')], '/api/job/')))
        raise HttpError(404, 'no such route: %s' % path)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, gallery):
        super().__init__(address, Handler)
        self.gallery = gallery


def serve(gallery, port=8123, host='127.0.0.1', state_dir=None, llm_url=LLM_DEFAULT, verbose=False,
          warm=True):
    """Build (but do not start) the server. Returns (httpd, gallery); `httpd.serve_forever()` runs it.

    Port 0 asks the OS for a free port, which is what the tests use - they must never collide with a real server.
    `warm` groups the gallery in a background thread straight away: the first /api/folders on the real 832-picture
    folder measures 3.1 s, which is a wait nobody should meet with an empty page if it can be spent while they read
    the tab title instead. The lock in `refresh` means a request that arrives first simply waits for the same build.
    """
    cfg = Gallery(gallery, state_dir=state_dir, llm_url=llm_url)
    cfg.verbose = verbose
    server = Server((host, port), cfg)
    if warm:
        def build():
            try:
                cfg.refresh()
                if cfg.verbose:
                    print('grouped %d pictures into %d folders in %.2f s'
                          % (cfg._pictures, len(cfg._folders or []), cfg._seconds))
            except Exception:
                pass
        threading.Thread(target=build, daemon=True).start()
    return server, cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description='the gallery, in a browser - folders, A/B ranking, describe-all')
    ap.add_argument('--gallery', default=str(Path.home() / 'Pictures' / 'zimage-ui'),
                    help='the folder of pictures (and their .json sidecars) to show')
    ap.add_argument('--port', type=int, default=8123)
    ap.add_argument('--host', default='127.0.0.1', help='keep it on the loopback: there is no auth on this server')
    ap.add_argument('--state', default=None,
                    help="where A/B sessions are kept (default: the Tk app's folder, so one session is shared)")
    ap.add_argument('--llm', default=LLM_DEFAULT, help='the local vision model used by Describe all')
    ap.add_argument('--verbose', action='store_true', help='log every request')
    args = ap.parse_args(argv)
    httpd, cfg = serve(args.gallery, args.port, args.host, args.state, args.llm, args.verbose)
    host, port = httpd.server_address[:2]
    print('zimage web - %d pictures in %s' % (len(cfg.scan()[0]), cfg.root))
    print('  http://%s:%d     (Ctrl-C to stop)' % (host, port))
    print('  review state: %s' % cfg.state_dir)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
