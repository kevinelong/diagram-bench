"""test_web.py - the browser gallery, end to end, against a temporary gallery.

    python web/test_web.py

No model, no network, no GPU. The gallery is 31 generated pictures built to have a known answer:

  * 18 newest  - the plan's `DEFAULT_RECENT` cut, so they must come back as the "recent" folder and must not be
                 filed among the prompt families (this is `group_prompts.plan`'s rule, and the server must not fight it);
  * 8 identical prompts - a family big enough to need ROUNDS (log2 8 = 3), which is where `next_round` lives;
  * 3 identical prompts - small enough that the schedule is EXHAUSTIVE (6 or fewer), so every pair is asked once;
  * 2 with no prompt and no caption - the "unfiled" bucket, and what Describe all exists for.

The vision model is a stdlib stub on a loopback port that answers `POST /v1/chat/completions` the way the local model
does, so the describe path (request shape, sidecar fields, polling) is tested rather than skipped. The review state
goes to a temporary folder, never to `~/.local/zimage-ui/review`; nothing is written to the real gallery.
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import server as web                                        # noqa: E402

FAILS = []
BASE = '2026-10-01T09:00:00'
MOUNTAIN = 'three women standing on a mountain top at sunrise, wind in their coats'
POOL = 'an olympic swimming pool seen from above, lane ropes, blue tiles, empty'


def check(name, cond, detail=''):
    print('%-64s %s%s' % (name, 'PASS' if cond else 'FAIL', ('  ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def stamp(minutes):
    return (datetime.fromisoformat(BASE) + timedelta(minutes=minutes)).isoformat(timespec='seconds')


def make_gallery(root):
    """Write the 31 pictures and their sidecars. Returns {label: [paths]} for the assertions that need names."""
    made = {'recent': [], 'mountain': [], 'pool': [], 'blank': []}

    def one(name, prompt, minutes, colour):
        path = root / name
        Image.new('RGB', (96, 96), colour).save(path)
        meta = {'created': stamp(minutes)}
        if prompt:
            meta['prompt'] = prompt
        (root / (path.stem + '.json')).write_text(json.dumps(meta, indent=1), encoding='utf-8')
        return str(path)

    for i in range(3):                                  # the exhaustive family
        made['pool'].append(one('pool%d.png' % i, POOL, i, (40, 90, 200)))
    for i in range(8):                                  # the family that needs rounds
        made['mountain'].append(one('mount%d.png' % i, MOUNTAIN, 10 + i, (60, 160 + i, 70)))
    for i in range(18):                                 # the newest pictures: the recent cut
        made['recent'].append(one('rec%02d.png' % i, 'a still life of a bowl of fruit, study %d' % i,
                                  100 + i, (200 - i, 120, 60)))
    for i in range(2):                                  # no prompt, no caption: describe-all's job
        made['blank'].append(one('blank%d.png' % i, '', -20 - i, (120, 120, 120)))
    return made


# --- the stub vision model ---------------------------------------------------------------------------------------

class StubLLM(BaseHTTPRequestHandler):
    """Answers the two calls the Tk app makes, and remembers the last body it was sent."""
    seen = []
    delay = 0.0                    # a slow model, so a cancel can land in the middle of a batch rather than after it

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith('/models'):
            return self._send(200, {'data': [{'id': 'local'}]})
        return self._send(404, {'error': 'no such route'})

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n) or b'{}')
        StubLLM.seen.append(body)
        if StubLLM.delay:
            time.sleep(StubLLM.delay)
        return self._send(200, {'choices': [{'message': {'content': 'a described picture'}}]})


# --- HTTP helpers -------------------------------------------------------------------------------------------------

def call(base, path, method='GET', body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def jcall(base, path, method='GET', body=None):
    status, headers, raw = call(base, path, method, body)
    try:
        return status, json.loads(raw)
    except Exception:
        return status, {'raw': raw[:300].decode('utf-8', 'replace')}


def photo(path):
    return urllib.parse.quote(path, safe='')


def read_sidecar(path):
    return json.loads(Path(path).with_suffix('.json').read_text(encoding='utf-8'))


def wins_sum(paths):
    total = 0
    for path in paths:
        meta = read_sidecar(path)
        total += int(meta.get('wins') or 0)
    return total


def main():
    tmp = Path(tempfile.mkdtemp(prefix='web-gallery-'))
    state_dir = Path(tempfile.mkdtemp(prefix='web-state-'))
    stub = ThreadingHTTPServer(('127.0.0.1', 0), StubLLM)
    stub_thread = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_thread.start()
    llm_url = 'http://127.0.0.1:%d/v1' % stub.server_address[1]

    httpd = cfg = None
    try:
        gallery_dir = tmp / 'gallery'
        gallery_dir.mkdir(parents=True, exist_ok=True)
        made = make_gallery(gallery_dir)

        httpd, cfg = web.serve(gallery_dir, port=0, state_dir=state_dir, llm_url=llm_url)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % httpd.server_address[1]

        # --- the page and its assets ---------------------------------------------------------------------------
        status, headers, raw = call(base, '/')
        page = raw.decode('utf-8')
        check('GET / serves the page', status == 200 and 'text/html' in headers.get('Content-Type', ''), status)
        check('the page has the two result actions', 'Keep and close' in page and 'Reset and start over' in page)
        check('the page is keyboard-shaped: live regions and a skip link',
              'aria-live' in page and 'class="skip"' in page, page[:80])
        status, headers, css = call(base, '/app.css')
        css = css.decode('utf-8')
        check('GET /app.css serves the stylesheet', status == 200 and 'text/css' in headers.get('Content-Type', ''))
        check('the stylesheet honours prefers-color-scheme', 'prefers-color-scheme' in css)
        check('the stylesheet rings focus on actionable elements', ':focus' in css and 'outline' in css)
        status, js_headers, js = call(base, '/app.js')
        js = js.decode('utf-8')
        check('GET /app.js serves the script',
              status == 200 and 'javascript' in js_headers.get('Content-Type', ''), js_headers.get('Content-Type'))
        check('the keys the contract names are bound',
              all(tok in js for tok in ('ArrowLeft', 'ArrowRight', "'Escape'", "'Enter'", "' '", "'1'", "'3'")))
        grid_px = 0
        done_px = 0
        for line in css.splitlines():
            if line.strip().startswith('--grid-tile:'):
                grid_px = int(line.split(':')[1].split('px')[0].strip())
            if line.strip().startswith('--done-tile:'):
                done_px = int(line.split(':')[1].split('px')[0].strip())
        check('the finished screen tiles are larger than the grid tiles',
              done_px > grid_px > 0, 'grid %s, done %s' % (grid_px, done_px))

        # --- folders -------------------------------------------------------------------------------------------
        status, data = jcall(base, '/api/folders')
        check('GET /api/folders is JSON', status == 200 and 'folders' in data, (status, data))
        folders = data.get('folders') or []
        ids = [f['id'] for f in folders]
        check('every folder has id, label, count, series',
              all(set(('id', 'label', 'count', 'series', 'kind')) <= set(f) for f in folders), folders[:1])
        check('folder ids are unique', len(ids) == len(set(ids)), ids)
        check('the folder counts add up to every picture',
              sum(f['count'] for f in folders) == data.get('pictures') == 31,
              (sum(f['count'] for f in folders), data.get('pictures')))
        recent = [f for f in folders if f['kind'] == 'recent']
        check('the plan\'s recent cut comes back as its own folder',
              len(recent) == 1 and recent[0]['count'] == 18, recent)
        big = [f for f in folders if f['kind'] == 'group' and f['count'] == 8]
        small = [f for f in folders if f['kind'] == 'group' and f['count'] == 3]
        check('the eight-prompt family is one folder', len(big) == 1, [(f['kind'], f['count']) for f in folders])
        check('the three-prompt family is another', len(small) == 1, [(f['kind'], f['count']) for f in folders])
        unfiled = [f for f in folders if f['kind'] == 'unfiled']
        check('the pictures with no prompt land in "unfiled"', len(unfiled) == 1 and unfiled[0]['count'] == 2, unfiled)
        check('a folder reports its series count', all(isinstance(f['series'], int) and f['series'] >= 1 for f in folders))

        # --- one folder ----------------------------------------------------------------------------------------
        status, folder = jcall(base, '/api/folder/' + big[0]['id'])
        check('GET /api/folder/<id> returns the folder', status == 200 and folder['count'] == 8, status)
        items = folder['items']
        check('items carry path, prompt, created, has_prompt',
              all({'path', 'prompt', 'created', 'has_prompt'} <= set(i) for i in items), items[:1])
        check('the items are this family', sorted(i['name'] for i in items) == sorted(
            'mount%d.png' % i for i in range(8)))
        check('every member has its prompt', all(i['has_prompt'] and i['prompt'] == MOUNTAIN for i in items))
        check('the folder says nothing needs describing', folder['undescribed'] == 0)
        status, missing = jcall(base, '/api/folder/deadbeefdeadbeef')
        check('an unknown folder id is a 404', status == 404, status)

        # --- thumbnails ----------------------------------------------------------------------------------------
        status, headers, thumb = call(base, '/thumb?path=' + photo(items[0]['path']))
        check('GET /thumb returns a JPEG', status == 200 and headers.get('Content-Type') == 'image/jpeg'
              and thumb[:2] == b'\xff\xd8', (status, headers.get('Content-Type'), thumb[:4]))
        cache = gallery_dir / '.thumbs' / (Path(items[0]['path']).stem + '.jpg')
        check('the thumbnail is cached in .thumbs beside the picture', cache.is_file())
        first_mtime = cache.stat().st_mtime
        time.sleep(0.02)
        call(base, '/thumb?path=' + photo(items[0]['path']))
        check('a second request reuses the cached thumbnail', cache.stat().st_mtime == first_mtime)
        status, _h, _b = call(base, '/thumb?path=' + photo(str(Path(tempfile.gettempdir()) / 'not-a-picture.png')))
        check('a path outside the gallery is refused', status in (403, 404), status)
        status, _h, _b = call(base, '/image?path=' + photo(items[0]['path']))
        check('GET /image serves the picture itself', status == 200 and _b[:4] == b'\x89PNG', status)

        # --- the exhaustive family: every pair once --------------------------------------------------------------
        pool = small[0]
        status, review = jcall(base, '/api/review/' + pool['id'])
        check('GET /api/review starts a session', status == 200 and review['done'] is False, status)
        check('three pictures means every pair once', review['exhaustive'] is True and review['total'] == 3,
              (review['exhaustive'], review['total']))
        check('a pair is offered', isinstance(review['pair'], list) and len(review['pair']) == 2, review['pair'])
        check('the review lists the pairs it has queued',
              review['upcoming'] and review['upcoming'][0] == review['pair'], review['upcoming'][:2])
        check('the session state is kept in the given folder, not the Tk one',
              any(state_dir.glob('*.json')), list(state_dir.glob('*.json'))[:3])

        first_pair = list(review['pair'])
        pair_order = []
        seen_rounds = set()
        left_wins = 0
        steps = 0
        while review['pair'] and steps < 12:
            steps += 1
            a, b = review['pair']
            pair_order.append((a, b))
            seen_rounds.add(review['round'])
            status, review = jcall(base, '/api/decide', 'POST',
                                   {'id': pool['id'], 'a': a, 'b': b, 'choice': 'left_wins'})
            check('POST /api/decide answers with the next pair', status == 200 and 'pair' in review, (status, review))
            left_wins += 1
        check('the deciding sequence walks the schedule', len(pair_order) == 3 and len(set(pair_order)) == 3,
              pair_order)
        check('the last decision finishes the review', review['done'] is True and review['pair'] is None)
        check('the progress counts the decisions', review['decided'] == 3 and review['total'] == 3,
              (review['decided'], review['total']))
        check('wins were written into the pictures\' own sidecars', wins_sum(made['pool']) == 3,
              wins_sum(made['pool']))
        status, again = jcall(base, '/api/decide', 'POST',
                              {'id': pool['id'], 'a': first_pair[0], 'b': first_pair[1], 'choice': 'right_wins'})
        check('a pair cannot be decided twice', status == 409, (status, again))
        status, bad = jcall(base, '/api/decide', 'POST',
                            {'id': pool['id'], 'a': first_pair[0], 'b': first_pair[1], 'choice': 'delete_left'})
        check('ranking refuses to delete', status == 400, (status, bad))

        # --- the ranking ---------------------------------------------------------------------------------------
        status, rank = jcall(base, '/api/rank/' + pool['id'])
        check('GET /api/rank returns an order', status == 200 and len(rank['order']) == 3, status)
        check('the order is paths and the wins agree', set(rank['order']) == set(made['pool'])
              and sum(rank['wins'].values()) == 3, rank['wins'])
        entries = rank['entries']
        check('entries are ranked and sorted by wins',
              [e['rank'] for e in entries] == [1, 2, 3]
              and all(entries[i]['wins'] >= entries[i + 1]['wins'] for i in range(len(entries) - 1)), entries)
        check('the winner of the decided pairs is first', entries[0]['wins'] == 2, entries)
        check('the rank view knows the review is finished', rank['done'] is True, rank['done'])

        # --- reset ----------------------------------------------------------------------------------------------
        status, fresh = jcall(base, '/api/reset/' + pool['id'], 'POST')
        check('POST /api/reset clears the record', status == 200 and fresh['decided'] == 0
              and fresh['pair'] is not None, (status, fresh.get('decided')))
        check('reset zeroes the sidecar counters', wins_sum(made['pool']) == 0, wins_sum(made['pool']))
        status, rank = jcall(base, '/api/rank/' + pool['id'])
        check('after a reset the order is the neutral one', rank['wins'] == {p: 0 for p in rank['order']}
              and [Path(p).name for p in rank['order']] == sorted(Path(p).name for p in made['pool']), rank['order'])

        # --- the family that needs rounds -----------------------------------------------------------------------
        mount = big[0]
        status, review = jcall(base, '/api/review/' + mount['id'])
        check('eight pictures means rounds, not every pair',
              review['exhaustive'] is False and review['target_rounds'] == 3, (review['exhaustive'], review['target_rounds']))
        rounds = [review['round']]
        steps = 0
        while review['pair'] and steps < 40:
            steps += 1
            a, b = review['pair']
            status, review = jcall(base, '/api/decide', 'POST', {'id': mount['id'], 'a': a, 'b': b, 'choice': 'tie'})
            rounds.append(review['round'])
        check('the schedule advances a round when one is done', max(rounds) > min(rounds), rounds)
        check('the review ends when the order settles', review['done'] is True and review['settled'] is True,
              (review['done'], review['settled'], review['round']))
        check('every scheduled pair was decided', review['decided'] == review['total'] == 8,
              (review['decided'], review['total']))

        # --- describe-all ----------------------------------------------------------------------------------------
        status, llm = jcall(base, '/api/llm')
        check('GET /api/llm finds the stub vision model', status == 200 and llm['ok'] is True and llm['undescribed'] == 2,
              llm)
        good_url = cfg.llm_url
        cfg.llm_url = 'http://127.0.0.1:9/v1'                  # nothing listens on the discard port
        status, bad = jcall(base, '/api/describe-all', 'POST', {'id': unfiled[0]['id']})
        check('describe-all refuses when the model is down', status == 503 and 'not reachable' in bad.get('error', ''),
              (status, bad))
        cfg.llm_url = 'http://example.com/v1'                  # off the machine: refused without opening a connection
        status, offbox = jcall(base, '/api/describe-all', 'POST', {'id': unfiled[0]['id']})
        check('describe-all refuses a non-local endpoint', status == 503 and 'local-only' in offbox.get('error', ''),
              (status, offbox))
        cfg.llm_url = good_url

        created_before = [read_sidecar(p).get('created') for p in made['blank']]
        # A cancel stops after the picture in hand, which is what Esc does in the Tk window. The stub is slowed down
        # so the cancel lands in the middle of the batch instead of racing a model that answers in microseconds.
        StubLLM.delay = 0.4
        status, cancelled = jcall(base, '/api/describe-all', 'POST', {'id': unfiled[0]['id']})
        check('POST /api/describe-all starts a job', status == 202 and cancelled.get('job')
              and cancelled['total'] == 2, (status, cancelled))
        stop_id = cancelled.get('job') or 'none'
        time.sleep(0.15)
        jcall(base, '/api/job/%s/cancel' % stop_id, 'POST')
        progress = {}
        deadline = time.time() + 30
        while time.time() < deadline:
            status, progress = jcall(base, '/api/job/' + stop_id)
            if progress.get('state') not in ('running', 'cancelling'):
                break
            time.sleep(0.05)
        check('cancelling stops the batch after the picture in hand',
              progress.get('state') == 'cancelled' and progress.get('done', 2) < 2, progress)
        StubLLM.delay = 0.0

        left = [p for p in made['blank'] if not read_sidecar(p).get('prompt')]
        check('a cancel leaves the rest of the folder alone', len(left) >= 1, left)

        status, job = jcall(base, '/api/describe-all', 'POST', {'id': unfiled[0]['id']})
        check('the pictures the cancel left can be described', status == 202 and job.get('job')
              and job['total'] == len(left), (status, job))
        job_id = job.get('job') or 'none'
        progress = {}
        deadline = time.time() + 30
        while time.time() < deadline:
            status, progress = jcall(base, '/api/job/' + job_id)
            if progress.get('state') not in ('running', 'cancelling'):
                break
            time.sleep(0.05)
        check('the job reports progress and finishes', status == 200 and progress.get('state') == 'done'
              and progress['done'] == len(left) and progress['failed'] == 0, progress)
        metas = [read_sidecar(p) for p in made['blank']]
        check('the caption becomes the prompt', all(m.get('prompt') == 'a described picture' for m in metas), metas)
        check('the caption is kept as the caption', all(m.get('caption') == 'a described picture' for m in metas))
        check('the sidecar says where the prompt came from',
              all(m.get('prompt_from') == 'describe' for m in metas), [m.get('prompt_from') for m in metas])
        check('the describe is timestamped', all(m.get('described_at') for m in metas))
        check('an existing created date is kept, not overwritten',
              [m.get('created') for m in metas] == created_before, ([m.get('created') for m in metas], created_before))
        check('the request matches the Tk app\'s (model, temperature, tokens, inline png)',
              StubLLM.seen and StubLLM.seen[-1]['model'] == 'local'
              and StubLLM.seen[-1]['temperature'] == 0.4 and StubLLM.seen[-1]['max_tokens'] == 160
              and StubLLM.seen[-1]['messages'][0]['content'][1]['image_url']['url'].startswith(
                  'data:image/png;base64,'), StubLLM.seen[-1] if StubLLM.seen else None)
        status, after = jcall(base, '/api/folders')
        labels = [(f['kind'], f['label'], f['count']) for f in after['folders']]
        check('the described pictures are grouped now, not unfiled',
              all(f['kind'] != 'unfiled' for f in after['folders'])
              and any(f['kind'] == 'group' and f['count'] == 2 for f in after['folders']), labels)
        check('the grouping still accounts for every picture',
              sum(f['count'] for f in after['folders']) == after['pictures'] == 31,
              (sum(f['count'] for f in after['folders']), after['pictures']))
        check('a finished job is still pollable', jcall(base, '/api/job/' + job_id)[0] == 200)
        check('an unknown job is a 404', jcall(base, '/api/job/nope')[0] == 404)

        # --- routes ---------------------------------------------------------------------------------------------
        check('an unknown route is a 404', call(base, '/api/nothing')[0] == 404)
        status, bad = jcall(base, '/api/decide', 'POST', {'id': pool['id'], 'a': 'x', 'b': 'y', 'choice': 'tie'})
        check('deciding a pair from outside the folder is refused', status == 400, (status, bad))
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        stub.shutdown()
        stub.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(state_dir, ignore_errors=True)

    print()
    if FAILS:
        print('%d FAILED: %s' % (len(FAILS), ', '.join(FAILS)))
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
