"""zimage_ui.py - a small prompt-and-picture window for Z-Image, with the server under its control.

Layout: prompt box filling the left side, the generated image on the right, a Generate button under the box, and a
progress bar between them that moves while the model works. Enter generates; Shift+Enter inserts a newline.

Lifecycle: the window starts ComfyUI if nothing is already answering, and stops it on exit - but only if it started it.
If a server was already running, it is left alone (same rule as `zimage_gen.py --with-server`).

Progress: ComfyUI reports step progress over its websocket at /ws as {"type": "progress", "data": {"value", "max"}}.
Neither venv on this box has a websocket package, so a minimal RFC6455 client is included below - it speaks only what
ComfyUI needs (text frames in, masked pong out).

    python zimage_ui.py                 # server on 8006, auto-started and auto-stopped
    python zimage_ui.py --port 8007 --size 768 --steps 4 --seed 1234
    python zimage_ui.py --no-progress   # if the websocket misbehaves
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import queue
import random
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk

import zimage_gen as zg


# --------------------------------------------------------------------------- websocket (text frames only)


class WSClient(threading.Thread):
    """Minimal RFC6455 client: enough for ComfyUI's /ws progress feed."""

    def __init__(self, host, port, client_id, sink, stop_event):
        super().__init__(daemon=True)
        self.host, self.port, self.client_id = host, port, client_id
        self.sink, self.stop_event = sink, stop_event
        self.sock = None

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        req = ('GET /ws?clientId=%s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
               'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n' % (self.client_id, self.host, self.port, key))
        self.sock.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('handshake closed')
            buf += chunk
        head, _, rest = buf.partition(b'\r\n\r\n')
        if b'101' not in head.split(b'\r\n')[0]:
            raise ConnectionError('handshake refused: %r' % head.split(b'\r\n')[0])
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if accept not in head:
            raise ConnectionError('bad Sec-WebSocket-Accept')
        return rest

    def _send_pong(self, payload=b''):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask + masked)

    def run(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=5)
            self.sock.settimeout(1.0)
            pending = self._handshake()
        except Exception as e:
            self.sink.put(('ws_error', str(e)))
            return
        buf = bytearray(pending)
        while not self.stop_event.is_set():
            try:
                data = self.sock.recv(65536)
                if not data:
                    break
                buf += data
            except socket.timeout:
                continue
            except OSError:
                break
            while True:
                if len(buf) < 2:
                    break
                b1, b2 = buf[0], buf[1]
                opcode = b1 & 0x0F
                masked = b2 & 0x80
                ln = b2 & 0x7F
                idx = 2
                if ln == 126:
                    if len(buf) < 4:
                        break
                    ln = struct.unpack('>H', bytes(buf[2:4]))[0]
                    idx = 4
                elif ln == 127:
                    if len(buf) < 10:
                        break
                    ln = struct.unpack('>Q', bytes(buf[2:10]))[0]
                    idx = 10
                mask = b''
                if masked:
                    if len(buf) < idx + 4:
                        break
                    mask = bytes(buf[idx:idx + 4])
                    idx += 4
                if len(buf) < idx + ln:
                    break
                payload = bytes(buf[idx:idx + ln])
                del buf[:idx + ln]
                if masked:
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                if opcode == 0x1:
                    try:
                        self.sink.put(('ws', json.loads(payload.decode('utf-8', 'replace'))))
                    except Exception:
                        pass
                elif opcode == 0x9:
                    self._send_pong(payload)
                elif opcode == 0x8:
                    return
        try:
            self.sock.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- generation plumbing


def copy_png_to_clipboard(path):
    """Put an image on the Windows clipboard. Must run in an STA apartment, hence -STA and WinForms."""
    ps = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
          "$i=[System.Drawing.Image]::FromFile('%s'); "
          "[System.Windows.Forms.Clipboard]::SetImage($i); $i.Dispose(); Write-Output 'ok'"
          % str(path).replace("'", "''"))
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-STA', '-Command', ps],
                           capture_output=True, text=True, timeout=90)
        return (r.returncode == 0 and 'ok' in r.stdout), (r.stdout + r.stderr).strip()[:180]
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e)


THUMB_DIR = '.thumbs'
THUMB_PX = 168
RECENT_N = 18        # thumbnails in the sidebar strip (3 columns x 6 rows)
RECENT_COLS = 3
RECENT_PX = 92       # on-screen size of a strip tile; the cache keeps 168 px


def thumb_for(png, cache_root=None):
    """Return a cached thumbnail path for `png`, creating it if missing or stale.

    The cache lives in a `.thumbs` folder beside the images and is keyed by the image's name, so the gallery never
    re-decodes a PNG it has already seen - and the cache is derived data, safe to delete at any time.
    """
    png = Path(png)
    root = Path(cache_root) if cache_root else png.parent
    cache = root / THUMB_DIR
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / (png.stem + '.jpg')
    try:
        if out.exists() and out.stat().st_mtime >= png.stat().st_mtime:
            return out
        im = Image.open(png)
        im.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        im.convert('RGB').save(out, 'JPEG', quality=82, optimize=True)
    except Exception:
        return None
    return out


def thumbs_for(paths, workers=8, cache_root=None):
    """Build any missing thumbnails in parallel. Returns {path: thumb_or_None}."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        got = list(pool.map(lambda p: (p, thumb_for(p, cache_root)), paths))
    return dict(got)


def read_meta(png):
    """The sidecar written at save time: prompt, seed, size, base image, strength."""
    j = Path(png).with_suffix('.json')
    if not j.exists():
        return None
    try:
        return json.loads(j.read_text(encoding='utf-8'))
    except Exception:
        return None


class GenerationCancelled(Exception):
    """Raised inside the worker when the user pressed Stop; the partial run is discarded."""


class Engine:
    """Server lifecycle plus one-generation-at-a-time generation, all off the UI thread."""

    def __init__(self, port, autostart=True, progress=True):
        self.port = port
        self.progress = progress
        self.host = '127.0.0.1'
        self.autostart = autostart
        self.owned = False
        self.ready = False
        self.events = queue.Queue()
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self._ws = None

    # -- http helpers ------------------------------------------------------
    def _get(self, path, timeout=30):
        return json.loads(urllib.request.urlopen('http://%s:%d%s' % (self.host, self.port, path),
                                                timeout=timeout).read())

    def _post(self, path, obj, timeout=60):
        req = urllib.request.Request('http://%s:%d%s' % (self.host, self.port, path),
                                     data=json.dumps(obj).encode(),
                                     headers={'Content-Type': 'application/json'})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    def _answers(self):
        try:
            self._get('/system_stats', timeout=3)
            return True
        except Exception:
            return False

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        def work():
            if self._answers():
                self.events.put(('status', 'using the server already on port %d' % self.port))
                self.ready = True
                self.events.put(('ready', None))
                return
            if not self.autostart:
                self.events.put(('error', 'no server on port %d and --no-autostart given' % self.port))
                return
            self.events.put(('status', 'starting ComfyUI on port %d (~25 s)...' % self.port))
            try:
                zg.start_server(self.port, timeout=300)
                self.owned = True
                self.ready = True
                self.events.put(('status', 'server ready'))
                self.events.put(('ready', None))
            except Exception as e:
                self.events.put(('error', 'server did not start: %s' % e))
        threading.Thread(target=work, daemon=True).start()

    def shutdown(self):
        self._stop.set()
        if self.owned:
            return zg.stop_server(self.port)
        return 'left the pre-existing server running'

    # -- progress ----------------------------------------------------------
    def _listen_progress(self):
        client_id = 'zimage-ui-%d' % int(time.time() * 1000)
        self._ws = WSClient(self.host, self.port, client_id, self.events, self._stop)
        self._ws.start()
        return client_id

    # -- generation --------------------------------------------------------
    def _listen_progress(self):
        client_id = 'zimage-ui-%d' % int(time.time() * 1000)
        self._ws = WSClient(self.host, self.port, client_id, self.events, self._stop)
        self._ws.start()
        return client_id

    def cancel(self):
        """Halt the running generation: interrupt the sampler, drop anything queued, and stop the worker waiting."""
        self._cancel.set()
        for path, payload in (('/interrupt', {}), ('/queue', {'clear': True})):
            try:
                self._post(path, payload, timeout=15)
            except Exception:
                pass
        return 'interrupt sent'

    def _queue_wait(self, wf):
        """Queue a workflow and wait for its images to appear in history. Returns a list of output image dicts."""
        pid = self._post('/prompt', {'prompt': wf})['prompt_id']
        started = time.monotonic()
        while time.monotonic() - started < 900:
            if self._cancel.is_set():
                raise GenerationCancelled()
            h = self._get('/history/' + pid, timeout=30)
            if pid in h:
                entry = h[pid]
                if entry.get('status', {}).get('status_str') == 'error':
                    raise RuntimeError('workflow failed')
                imgs = [i for out in (entry.get('outputs') or {}).values() for i in out.get('images', [])]
                if imgs:
                    return imgs
            time.sleep(0.3)
        raise RuntimeError('timed out waiting for the image')

    def _download(self, img):
        q = urllib.parse.urlencode({'filename': img['filename'], 'subfolder': img.get('subfolder', ''),
                                    'type': img.get('type', 'output')})
        return urllib.request.urlopen('http://%s:%d/view?%s' % (self.host, self.port, q), timeout=120).read()

    def generate(self, prompt, width, height, steps, cfg, seed, out_dir, base_path=None, denoise=1.0,
                 batch=1, sequential=True, sampler='euler'):
        """One image, or a sheet of N.

        sequential=True runs the sampler N times with seeds seed, seed+1 ... so every tile is individually reproducible
        and the VRAM peak stays that of a single image. sequential=False asks ComfyUI for one batch of N latents, which
        is fewer round trips but whose tiles are not reproducible from individual seeds.
        """
        self._cancel.clear()

        def work():
            try:
                out = Path(out_dir)          # not out_dir: assigning that would shadow the parameter
                out.mkdir(parents=True, exist_ok=True)
                meta = {'steps': steps, 'cfg': cfg, 'denoise': (denoise if base_path else 1.0),
                        'base_image': (str(base_path) if base_path else None)}
                (out / 'last-prompt.txt').write_text(prompt, encoding='utf-8')
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                base_name = None
                if base_path:
                    base_name = zg.upload_image('http://%s:%d' % (self.host, self.port), base_path)
                    self.events.put(('status', 'base image uploaded: %s' % base_name))
                client_id = self._listen_progress() if self.progress else None

                total = 1 if base_name else max(1, batch)
                runs = range(total) if (sequential or base_name) else [0]
                paths, raws = [], []
                for run_i in runs:
                    if self._cancel.is_set():
                        raise GenerationCancelled()
                    this_seed = seed + run_i if (sequential and not base_name) else seed
                    if total > 1:
                        self.events.put(('status', 'tile %d of %d (seed %d)' % (run_i + 1, total, this_seed)))
                    wf = zg.workflow(prompt, width, height, steps, cfg, this_seed,
                                     'zimage-ui-%d-%d' % (int(time.time() * 1000), run_i),
                                     base_image=base_name,
                                     denoise=(1.0 if not base_name else denoise),
                                     batch=(1 if (sequential or base_name) else total),
                                     sampler=sampler)
                    outs = self._queue_wait(wf)
                    for k, img in enumerate(outs):
                        raw = self._download(img)
                        suffix = '' if total == 1 else ('_%02d' % (run_i + k + 1))
                        path = out / ('%s_%dx%d_seed%d%s.png' % (stamp, width, height, this_seed, suffix))
                        path.write_bytes(raw)
                        paths.append(path)
                        raws.append(raw)
                        rec = dict(meta, image=str(path), prompt=prompt, seed=this_seed, width=width,
                                   height=height, created=datetime.now().isoformat(timespec='seconds'),
                                   model=zg.UNET,
                                   index=(run_i + k + 1) if total > 1 else None,
                                   of=total if total > 1 else None)
                        path.with_suffix('.json').write_text(json.dumps(rec, indent=1), encoding='utf-8')
                        if total > 1:
                            self.events.put(('tile', (raw, str(path), run_i + k + 1, total)))

                if len(paths) == 1:
                    self.events.put(('image', (raws[0], str(paths[0]))))
                    return
                sheet_path = self._compose_sheet(paths, out, stamp, prompt, seed, width, height, meta)
                self.events.put(('image', (sheet_path.read_bytes(), str(sheet_path))))
            except GenerationCancelled:
                saved = len([p for p in locals().get('paths', []) if Path(p).exists()])
                self.events.put(('cancelled', saved))
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', 'replace')
                hint = ''
                if 'not in []' in body or 'value_not_in_list' in body:
                    hint = '  (server has no model paths - restart it with start-comfy.ps1)'
                self.events.put(('error', 'rejected: %s%s' % (body[:200], hint)))
            except Exception as e:
                self.events.put(('error', '%s: %s' % (type(e).__name__, e)))
        threading.Thread(target=work, daemon=True).start()

    def _compose_sheet(self, paths, out_dir, stamp, prompt, seed, width, height, meta):
        tiles = [Image.open(p).convert('RGB') for p in paths]
        cols = {4: 2, 9: 3, 16: 4}.get(len(tiles)) or max(1, int(math.ceil(math.sqrt(len(tiles)))))
        rows = int(math.ceil(len(tiles) / cols))
        tw, th = tiles[0].size
        gap = 6
        sheet = Image.new('RGB', (cols * tw + (cols - 1) * gap, rows * th + (rows - 1) * gap), 'white')
        for i, tile in enumerate(tiles):
            sheet.paste(tile, ((i % cols) * (tw + gap), (i // cols) * (th + gap)))
        path = out_dir / ('%s_%dx%d_seed%d_sheet_%dup.png' % (stamp, width, height, seed, len(tiles)))
        sheet.save(path, 'PNG')
        rec = dict(meta, image=str(path), prompt=prompt, seed=seed, width=width, height=height,
                   created=datetime.now().isoformat(timespec='seconds'), model=zg.UNET,
                   batch=len(tiles), seeds=[int(Path(p).stem.rsplit('_seed', 1)[1].split('_')[0]) for p in paths],
                   tiles=[str(p) for p in paths])
        path.with_suffix('.json').write_text(json.dumps(rec, indent=1), encoding='utf-8')
        return path


class App:
    def __init__(self, root, engine, args):
        self.root, self.engine, self.args = root, engine, args
        self.busy = True
        self._img = None
        self._raw = None
        self.base_path = None
        self._current = None
        self._files = []
        self._sheet = None            # {'cols','rows','tiles'} while a contact sheet is on screen
        self._draw = None             # (x0, y0, w, h) of the drawn image on the canvas

        root.title('Z-Image — prompt and picture')
        try:
            root.state('zoomed')   # fill the screen: prompt left, picture right
        except Exception:
            root.geometry('1180x720')
        root.minsize(760, 480)
        root.columnconfigure(0, weight=0, minsize=210)
        root.columnconfigure(1, weight=0, minsize=420)
        root.columnconfigure(2, weight=1)
        root.rowconfigure(0, weight=1)

        # ---- left: prompt + controls
        # ---- far left: the most recent images as thumbnails
        plate = ttk.Style().lookup('TFrame', 'background') or '#f0f0f0'
        gal = ttk.Frame(root, padding=(10, 10, 4, 10))
        gal.grid(row=0, column=0, sticky='nsew')
        gal.columnconfigure(0, weight=1)
        self.gal_title = ttk.Label(gal, text='Recent (%d)' % RECENT_N)
        self.gal_title.grid(row=0, column=0, columnspan=2, sticky='w')
        # the strip scrolls: 20 tiles at 92 px need ~500 px, which is more than a small window has
        holder = ttk.Frame(gal)
        holder.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(4, 6))
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        # width the canvas to the grid, or tk's default width clips the last column
        self.gal_canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0, background=plate,
                                    width=RECENT_COLS * (RECENT_PX + 8))
        self.gal_canvas.grid(row=0, column=0, sticky='nsew')
        self.gal_bar = ttk.Scrollbar(holder, orient='vertical', command=self.gal_canvas.yview)
        self.gal_bar.grid(row=0, column=1, sticky='ns')
        self.gal_canvas.configure(yscrollcommand=self.gal_bar.set)
        self.recent = ttk.Frame(self.gal_canvas)
        self._gal_item = self.gal_canvas.create_window((0, 0), window=self.recent, anchor='nw')
        self.recent.bind('<Configure>', self._on_strip_resize)
        self.gal_canvas.bind('<Configure>', self._on_strip_resize)
        self.gal_canvas.bind('<MouseWheel>', self._on_gallery_wheel)
        self.recent.bind('<MouseWheel>', self._on_gallery_wheel)
        for c in range(RECENT_COLS):
            self.recent.columnconfigure(c, weight=0)
        self._thumbs = {}                 # path -> PhotoImage, kept alive
        self._tile_widgets = {}           # path -> Label, so selection can be painted without a rebuild
        self._all_widgets = {}            # same, for the "More..." window
        self.selected = []                # ordered list of paths marked for action
        self._anchor = None               # shift-click range starts here

        btns = ttk.Frame(gal)
        btns.grid(row=2, column=0, columnspan=2, sticky='ew')
        ttk.Button(btns, text='More…', command=self.open_all).pack(fill='x')
        ttk.Button(btns, text='Clear base', command=self.clear_base).pack(fill='x', pady=(3, 0))
        ttk.Button(btns, text='Refresh', command=self.refresh_gallery).pack(fill='x', pady=(3, 0))
        ttk.Button(btns, text='Open folder', command=self.open_folder).pack(fill='x', pady=(3, 0))

        selrow = ttk.Frame(btns)
        selrow.pack(fill='x', pady=(6, 0))
        ttk.Button(selrow, text='Select all', command=self.select_all).pack(side='left', fill='x', expand=True)
        self.del_btn = ttk.Button(selrow, text='Delete', command=self.delete_selected, state='disabled')
        self.del_btn.pack(side='left', fill='x', expand=True, padx=(3, 0))
        self.sel_label = ttk.Label(btns, text='', foreground='#2a6df4')
        self.sel_label.pack(anchor='w', pady=(3, 0))
        ttk.Label(btns, text='ctrl+click adds · shift+click ranges\nDel / Ctrl+A / Esc', foreground='#777',
                  font=('Segoe UI', 7), justify='left').pack(anchor='w')
        gal.rowconfigure(1, weight=1)

        left = ttk.Frame(root, padding=(10, 10, 6, 10))
        left.grid(row=0, column=1, sticky='nsew')
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        ttk.Label(left, text='Prompt  (Enter generates · Shift+Enter adds a line)').grid(row=0, column=0, sticky='w')
        self.text = tk.Text(left, wrap='word', width=48, height=18, undo=True, font=('Segoe UI', 10))
        self.text.grid(row=1, column=0, sticky='nsew', pady=(4, 6))
        self.text.bind('<Return>', self._on_return)
        self.text.bind('<Shift-Return>', self._on_shift_return)
        self.text.focus_set()

        row = ttk.Frame(left)
        row.grid(row=2, column=0, sticky='ew')
        row.columnconfigure(3, weight=1)
        self.gen_btn = ttk.Button(row, text='Generate  ⏎', command=self.generate)
        self.gen_btn.grid(row=0, column=0, sticky='w')
        self.stop_btn = ttk.Button(row, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.grid(row=0, column=1, sticky='w', padx=(6, 0))
        ttk.Label(row, text='size').grid(row=0, column=1, padx=(12, 4))
        self.size_var = tk.StringVar(value=str(args.size))
        ttk.Combobox(row, textvariable=self.size_var, width=6, state='readonly',
                     values=['256', '320', '384', '448', '512', '640', '768', '1024']).grid(row=0, column=2, sticky='w')
        ttk.Label(row, text='layout').grid(row=0, column=3, padx=(10, 4))
        self.layout_var = tk.StringVar(value='single')
        ttk.Combobox(row, textvariable=self.layout_var, width=9, state='readonly',
                     values=['single', '4-up (2x2)', '9-up (3x3)', '16-up (4x4)']).grid(row=0, column=4, sticky='w')
        # one at a time by default: an N-up is N separate requests with seeds seed..seed+N-1, so every tile is
        # individually reproducible and the VRAM peak stays that of a single image
        self.seq_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text='one at a time (seed+1)', variable=self.seq_var).grid(row=1, column=0, columnspan=3,
                                                                                sticky='w', pady=(4, 0))
        seedbox = ttk.Frame(row)
        seedbox.grid(row=1, column=3, sticky='w', pady=(4, 0))
        ttk.Label(seedbox, text='seed').pack(side='left', padx=(0, 4))
        self.seed = tk.StringVar(value=str(args.seed))
        ttk.Entry(seedbox, textvariable=self.seed, width=10).pack(side='left')
        ttk.Button(seedbox, text='\u2212', width=2, command=lambda: self.bump_seed(-1)).pack(side='left', padx=(3, 0))
        ttk.Button(seedbox, text='+', width=2, command=lambda: self.bump_seed(1)).pack(side='left', padx=(2, 0))
        ttk.Button(seedbox, text='Rnd', width=4, command=self.random_seed).pack(side='left', padx=(4, 0))

        self.base_row = ttk.Frame(left)
        self.base_row.grid(row=6, column=0, sticky='ew', pady=(8, 0))
        ttk.Label(self.base_row, text='base:').pack(side='left')
        self.base_label = ttk.Label(self.base_row, text='(none - text to image)', foreground='#555')
        self.base_label.pack(side='left', padx=(4, 10))
        ttk.Label(self.base_row, text='strength').pack(side='left')
        self.denoise = tk.DoubleVar(value=0.6)
        ttk.Scale(self.base_row, from_=0.2, to=0.95, variable=self.denoise, length=130).pack(side='left', padx=4)
        self.denoise_text = ttk.Label(self.base_row, text='0.60')
        self.denoise_text.pack(side='left')
        self.denoise.trace_add('write', lambda *a: self.denoise_text.configure(text='%.2f' % self.denoise.get()))

        guide_row = ttk.Frame(left)
        guide_row.grid(row=7, column=0, sticky='ew', pady=(4, 0))
        ttk.Label(guide_row, text='steps').pack(side='left')
        self.steps_var = tk.StringVar(value=str(self.args.steps))
        ttk.Combobox(guide_row, textvariable=self.steps_var, width=4, state='readonly',
                     values=['2', '4', '6', '8', '12']).pack(side='left', padx=4)
        ttk.Label(guide_row, text='sampler').pack(side='left', padx=(12, 4))
        self.sampler_var = tk.StringVar(value='euler')
        ttk.Combobox(guide_row, textvariable=self.sampler_var, width=15, state='readonly',
                     values=['euler', 'euler_ancestral']).pack(side='left')
        ttk.Label(guide_row, text='turbo is 4 steps, euler',
                  foreground='#777').pack(side='left', padx=(8, 0))

        # ---- middle: progress (sits under the button, above the status line)
        self.bar = ttk.Progressbar(left, mode='determinate', maximum=1.0)
        self.bar.grid(row=3, column=0, sticky='ew', pady=(10, 2))
        self.bar.grid_remove()
        self.status = ttk.Label(left, text='starting…', foreground='#555')
        self.status.grid(row=4, column=0, sticky='w')
        self.hint = ttk.Label(left, text='', foreground='#555', wraplength=390, justify='left')
        self.hint.grid(row=5, column=0, sticky='w', pady=(4, 0))

        # ---- right: the picture
        right = ttk.Frame(root, padding=(6, 10, 10, 10))
        right.grid(row=0, column=2, sticky='nsew')
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(right, background='#f4f4f5', highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.canvas.bind('<Configure>', lambda e: self._redraw())
        self.canvas.bind('<Button-1>', self._on_canvas_click)
        tools = ttk.Frame(right)
        tools.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        self.copy_btn = ttk.Button(tools, text='Copy image', command=self.copy_image, state='disabled')
        self.copy_btn.pack(side='left')
        self.copy_status = ttk.Label(tools, text='', foreground='#555')
        self.copy_status.pack(side='left', padx=(8, 0))
        self.placeholder = self.canvas.create_text(14, 14, anchor='nw', fill='#888', tags='placeholder',
                                                   text='generated image appears here')

        prefill = self.args.initial
        if getattr(self.args, 'initial_file', ''):
            prefill = Path(self.args.initial_file).read_text(encoding='utf-8').strip()
        if prefill:
            self.text.insert('1.0', prefill)

        self.refresh_gallery()
        for seq, fn in (('<Delete>', self._on_delete_key), ('<Control-a>', self._on_ctrl_a),
                        ('<Escape>', self._on_escape)):
            root.bind(seq, fn)

        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._poll()

    # -- gallery: thumbnails -------------------------------------------------
    def _all_files(self):
        out = Path(self.args.out)
        return sorted(out.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True) if out.exists() else []

    def _tile_widget(self, parent, path, thumb, size=52):
        lab = tk.Label(parent, image=thumb, bd=1, relief='flat', cursor='hand2')
        lab.image = thumb
        lab.path = path
        lab.bind('<Button-1>', lambda e, p=path: self._on_tile_click(e, p))
        lab.bind('<Double-Button-1>', lambda e, p=path: self.generate())
        return lab

    def refresh_gallery(self, select=None):
        files = self._all_files()
        self._files = files
        if self.base_path is not None and not any(f.name == self.base_path.name for f in files):
            self.clear_base()
        # a deleted or moved file must not stay selected
        self.selected = [p for p in self.selected if any(f.name == Path(p).name for f in files)]
        recent = files[:RECENT_N]
        self.gal_title.configure(text=('Recent (%d)' % len(recent)) if len(files) <= RECENT_N
                                 else ('Recent (%d of %d)' % (RECENT_N, len(files))))
        thumbs = thumbs_for(recent) if recent else {}
        for w in self.recent.winfo_children():
            w.destroy()
        self._tile_widgets = {}
        for i, p in enumerate(recent):
            tp = thumbs.get(p)
            if not tp:
                continue
            try:
                from PIL import ImageTk
                im = Image.open(tp)
                im.thumbnail((RECENT_PX, RECENT_PX), Image.LANCZOS)
                ph = ImageTk.PhotoImage(im)
            except Exception:
                continue
            self._thumbs[p] = ph
            lab = self._tile_widget(self.recent, p, ph)
            lab.grid(row=i // RECENT_COLS, column=i % RECENT_COLS, padx=1, pady=1)
            lab.bind('<MouseWheel>', self._on_gallery_wheel)
            self._tile_widgets[Path(p)] = lab
        self._paint()

    def _on_strip_resize(self, event=None):
        """Keep the scroll region current and show the scrollbar only when the strip overflows."""
        box = self.gal_canvas.bbox('all')
        if box:
            self.gal_canvas.configure(scrollregion=box)
        self.gal_canvas.itemconfigure(self._gal_item, width=self.gal_canvas.winfo_width())
        content_h = (box[3] - box[1]) if box else 0
        need = content_h > self.gal_canvas.winfo_height() + 1
        if need:
            self.gal_bar.grid()
        else:
            self.gal_bar.grid_remove()
            self.gal_canvas.yview_moveto(0)

    def _on_gallery_wheel(self, event):
        self.gal_canvas.yview_scroll(int(-event.delta / 120), 'units')
        return 'break'

    # -- gallery: selection --------------------------------------------------
    def _click_order(self):
        """The order shift-click ranges over: every file in the window that is currently shown."""
        return [p for p in getattr(self, '_files', []) if p in self._tile_widgets or p in self._all_widgets]

    def _on_tile_click(self, event, path):
        """Plain click loads the image as the base; ctrl toggles; shift extends from the anchor."""
        path = Path(path)
        ctrl = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)

        if shift and self._anchor is not None:
            order = self._click_order()
            if self._anchor in order and path in order:
                i, j = order.index(self._anchor), order.index(path)
                if i > j:
                    i, j = j, i
                self.selected = order[i:j + 1]
            else:
                self.selected = [path]
        elif ctrl:
            if path in self.selected:
                self.selected.remove(path)
            else:
                self.selected.append(path)
            self._anchor = path
        else:
            self.selected = [path]
            self._anchor = path
            self.select_file(path)          # still the one-click path to "use this image"
            return                          # select_file rebuilds the grid, which paints it
        self._paint()

    def _paint(self):
        """Colour the selection ring and the base-image border without rebuilding thumbnails."""
        ring = '#2a6df4'
        plate = ttk.Style().lookup('TFrame', 'background') or '#f0f0f0'
        base_name = self.base_path.name if self.base_path else None
        seen = {}
        seen.update(self._tile_widgets)
        seen.update(self._all_widgets)
        for p, lab in seen.items():
            try:
                sel = any(Path(p).name == Path(s).name for s in self.selected)
                is_base = base_name is not None and Path(p).name == base_name
                lab.configure(highlightthickness=3, highlightbackground=ring if sel else plate,
                              relief='solid' if is_base else 'flat', bd=2 if is_base else 1)
            except Exception:
                pass
        n = len(self.selected)
        if hasattr(self, 'del_btn'):
            self.del_btn.configure(text='Delete (%d)' % n if n else 'Delete',
                                   state='normal' if n else 'disabled')
        if hasattr(self, 'sel_label'):
            self.sel_label.configure(text='%d selected' % n if n else '')
        for btn, lab in ((getattr(self, 'br_del', None), getattr(self, 'br_count', None)),):
            if btn is not None and btn.winfo_exists():
                btn.configure(text='Delete (%d)' % n if n else 'Delete', state='normal' if n else 'disabled')
            if lab is not None and lab.winfo_exists():
                lab.configure(text='%d selected' % n if n else '')

    def select_all(self):
        """Select every image in the window in play; pressing it again clears."""
        if self._all_widgets:
            pool = [p for p in self._click_order() if p in self._all_widgets]
        else:
            pool = [p for p in self._files[:RECENT_N] if p in self._tile_widgets]
        if pool and len(self.selected) == len(pool) and all(p in self.selected for p in pool):
            self.selected = []
        else:
            self.selected = list(pool)
        self._anchor = pool[0] if pool else None
        self._paint()

    def clear_selection(self):
        self.selected = []
        self._anchor = None
        self._paint()

    # -- gallery: delete -----------------------------------------------------
    def _delete_one(self, png):
        """Remove the PNG, its sidecar and its cached thumbnail. True if the image itself went."""
        png = Path(png)
        gone = False
        for f in (png, png.with_suffix('.json'), png.parent / THUMB_DIR / (png.stem + '.jpg')):
            try:
                if f.exists():
                    f.unlink()
                    gone = gone or f == png
            except Exception as e:
                self.hint.configure(text='could not delete %s: %s' % (f.name, e))
        return gone

    def delete_selected(self):
        paths = [Path(p) for p in self.selected if Path(p).exists()]
        if not paths:
            self.hint.configure(text='nothing selected to delete - ctrl+click thumbnails first')
            return 0
        n = len(paths)
        if not messagebox.askyesno(
                'Delete images',
                'Delete %d image%s and the metadata beside %s?\n\nThis removes the file%s from disk.'
                % (n, '' if n == 1 else 's', 'it' if n == 1 else 'them', '' if n == 1 else 's'),
                parent=self.root):
            return 0
        gone = [p for p in paths if self._delete_one(p)]
        base_gone = self.base_path is not None and any(
            p.name == self.base_path.name for p in paths)
        if base_gone:
            self.clear_base()
        if self._current is not None and any(p.name == self._current.name for p in paths):
            self.canvas.delete('img')
            self.canvas.delete('placeholder')
            self.canvas.create_text(14, 14, anchor='nw', fill='#888', tags='placeholder',
                                    text='that image was deleted')
            self.copy_btn.state(['disabled'])
            self._img = None
            self._current = None
        self.selected = [p for p in self.selected if p not in paths]
        self.refresh_gallery()
        self.status.configure(text='deleted %d image%s%s'
                              % (len(gone), '' if len(gone) == 1 else 's',
                                 '; base cleared' if base_gone else ''))
        return len(gone)

    # -- keyboard ------------------------------------------------------------
    def _typing(self):
        w = self.root.focus_get()
        return w is not None and w.winfo_class() in ('Text', 'Entry', 'TEntry', 'TCombobox', 'Spinbox')

    def _on_delete_key(self, event=None):
        if self._typing():
            return None
        self.delete_selected()
        return 'break'

    def _on_ctrl_a(self, event=None):
        if self._typing():
            return None
        self.select_all()
        return 'break'

    def _on_escape(self, event=None):
        self.clear_selection()
        return None

    def select_file(self, path):
        """One entry point for a click in the recent strip or the full browser."""
        self._display(path)
        self._set_base(path)
        meta = read_meta(path)
        if meta:
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', meta.get('prompt') or '')
            if meta.get('seed') is not None:
                self.seed.set(str(meta['seed']))
            self.hint.configure(text='restored prompt and seed %s from %s%s'
                                % (meta.get('seed'), Path(path).name,
                                   ('; itself edited %s at strength %s'
                                    % (Path(meta['base_image']).name, meta.get('denoise')))
                                   if meta.get('base_image') else ''))
        else:
            self.hint.configure(text='no prompt recorded for %s (saved before metadata existed)' % Path(path).name)
        self.status.configure(text='base: %s  (Enter to edit it, strength %.2f)'
                              % (Path(path).name, self.denoise.get()))
        self.refresh_gallery(select=path)

    def use_as_base(self):
        self.select_file(self.base_path) if self.base_path else \
            self.hint.configure(text='click a thumbnail first')

    def open_all(self):
        """Full-screen browser: every image, scrolling, thumbnails cached on disk."""
        files = self._all_files()
        if not files:
            self.hint.configure(text='no images yet')
            return
        win = tk.Toplevel(self.root)
        win.title('All images — %d' % len(files))
        try:
            win.state('zoomed')
        except Exception:
            win.geometry('1200x800')
        bar = ttk.Scrollbar(win, orient='vertical')
        bar.pack(side='right', fill='y')
        canvas = tk.Canvas(win, highlightthickness=0, background='#f4f4f5', yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)
        bar.configure(command=canvas.yview)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind_all('<MouseWheel>', lambda e: canvas.yview_scroll(int(-e.delta / 120), 'units'))

        cols = 6
        self._all_thumbs = {}
        self._all_widgets = {}
        foot = ttk.Frame(win)
        foot.pack(side='bottom', fill='x', pady=6, padx=6)
        for i, p in enumerate(files):
            tp = thumb_for(p)
            if not tp:
                continue
            try:
                from PIL import ImageTk
                ph = ImageTk.PhotoImage(Image.open(tp))
            except Exception:
                continue
            self._all_thumbs[p] = ph
            cell = tk.Frame(inner)
            cell.grid(row=i // cols, column=i % cols, padx=3, pady=3)
            lab = self._tile_widget(cell, p, ph)
            lab.configure(width=tp and 0)
            lab.pack()
            self._all_widgets[Path(p)] = lab
            tk.Label(cell, text=p.name[:24], font=('Segoe UI', 7), foreground='#555').pack()
            lab.bind('<Double-Button-1>', lambda e, q=p: (self.select_file(q), win.destroy()))

        def close_browser():
            self._all_widgets = {}
            win.destroy()

        ttk.Button(foot, text='Close', command=close_browser).pack(side='left')
        ttk.Button(foot, text='Select all', command=self.select_all).pack(side='left', padx=(6, 0))
        self.br_del = ttk.Button(foot, text='Delete', command=self.delete_selected)
        self.br_del.pack(side='left', padx=(6, 0))
        self.br_count = ttk.Label(foot, text='', foreground='#2a6df4')
        self.br_count.pack(side='left', padx=(8, 0))

        self._paint()

    def _display_raw(self, raw, path=None):
        self._current = Path(path) if path else None
        self.copy_btn.state(['!disabled'])
        self.copy_status.configure(text='')
        self._img = Image.open(io.BytesIO(raw))
        self.canvas.delete('placeholder')
        self.root.after_idle(self._redraw)

    def _display(self, path):
        self._current = Path(path)
        self.copy_btn.state(['!disabled'])
        self.copy_status.configure(text='')
        try:
            self._img = Image.open(path)
        except Exception as e:
            self.hint.configure(text='cannot open %s: %s' % (Path(path).name, type(e).__name__))
            return
        self.canvas.delete('placeholder')
        self.root.after_idle(self._redraw)

    def _set_base(self, path):
        self.base_path = Path(path)
        self.base_label.configure(text=self.base_path.name)
        self.hint.configure(text='next generation starts from %s at strength %.2f'
                            % (self.base_path.name, self.denoise.get()))

    def _tile_at(self, x, y):
        """Which tile of the displayed sheet is at canvas (x, y)? Returns a tile path or None."""
        if not self._sheet or not self._draw:
            return None
        x0, y0, w, h = self._draw
        if not (x0 <= x <= x0 + w and y0 <= y <= y0 + h):
            return None
        cols, rows = self._sheet['cols'], self._sheet['rows']
        col = min(int((x - x0) / (w / cols)), cols - 1)
        row = min(int((y - y0) / (h / rows)), rows - 1)
        idx = row * cols + col
        tiles = self._sheet['tiles']
        return tiles[idx] if idx < len(tiles) else None

    def _on_canvas_click(self, event):
        tile = self._tile_at(event.x, event.y)
        if not tile:
            return
        self.enlarge(tile)

    def enlarge(self, tile_path):
        """Regenerate one tile on its own, at the model's native 1024 px, using the prompt and seed recorded for it."""
        if self.busy:
            self.hint.configure(text='busy - wait for the current run to finish')
            return
        meta = read_meta(tile_path)
        if not meta:
            self.hint.configure(text='no metadata beside %s' % Path(tile_path).name)
            return
        seed = meta.get('seed') or self.args.seed
        prompt = meta.get('prompt') or self.text.get('1.0', 'end').strip()
        if not prompt:
            self.hint.configure(text='that tile has no recorded prompt')
            return
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', prompt)
        self.seed.set(str(seed))
        self.busy = True
        self.gen_btn.state(['disabled'])
        self.stop_btn.state(['!disabled'])
        self.bar.configure(value=0.0)
        self.bar.grid()
        base = meta.get('base_image')
        self.status.configure(text='enlarging %s at 1024 px, seed %s' % (Path(tile_path).name, seed))
        self.engine.generate(prompt, 1024, 1024, self.args.steps, 1.0, int(seed), self.args.out,
                             base_path=base, denoise=(meta.get('denoise') or 1.0), batch=1, sequential=True,
                             sampler=self.sampler_var.get())

    def copy_image(self):
        if not self._current:
            return
        ok, msg = copy_png_to_clipboard(self._current)
        self.copy_status.configure(text='copied to clipboard' if ok else 'copy failed: %s' % msg)

    def _clear_picture(self):
        self._img = None
        self.canvas.delete('img')
        self.canvas.delete('placeholder')
        self.placeholder = self.canvas.create_text(14, 14, anchor='nw', fill='#888', tags='placeholder',
                                                   text='generated image appears here')

    def clear_base(self):
        self.base_path = None
        self.base_label.configure(text='(none - text to image)')
        self.hint.configure(text='')
        self._clear_picture()          # (there is no separate listbox here; repaint drops the base ring)
        self._paint()
        self.status.configure(text='text to image — nothing selected')

    def open_folder(self):
        try:
            os.startfile(str(Path(self.args.out)))
        except Exception as e:
            self.hint.configure(text='could not open folder: %s' % type(e).__name__)

    # -- events ------------------------------------------------------------
    def _on_return(self, event):
        self.generate()
        return 'break'          # no newline inserted

    def _on_shift_return(self, event):
        self.text.insert('insert', '\n')
        return 'break'

    def stop(self):
        if not self.busy:
            return
        self.status.configure(text='stopping…')
        self.stop_btn.state(['disabled'])
        print('ui: %s' % self.engine.cancel())

    def bump_seed(self, delta):
        try:
            v = int(self.seed.get())
        except ValueError:
            v = self.args.seed
        self.seed.set(str(max(0, v + delta)))

    def random_seed(self):
        self.seed.set(str(random.randint(0, 999_999_999)))

    def _batch(self):
        return {'single': 1, '4-up (2x2)': 4, '9-up (3x3)': 9, '16-up (4x4)': 16}.get(self.layout_var.get(), 1)

    def generate(self):
        if self.busy:
            return
        prompt = self.text.get('1.0', 'end').strip()
        if not prompt:
            self.hint.configure(text='enter a prompt first')
            return
        try:
            seed = int(self.seed.get())
        except ValueError:
            seed = self.args.seed
        try:
            size = int(self.size_var.get())
        except ValueError:
            size = self.args.size

        # a base image yields exactly one result, so an N-up layout does not apply to an edit
        batch = 1 if self.base_path else self._batch()

        self.busy = True
        self.gen_btn.state(['disabled'])
        self.stop_btn.state(['!disabled'])
        self.bar.configure(value=0.0)
        self.bar.grid()
        if self.base_path:
            self.hint.configure(text='starting from %s at strength %.2f%s'
                                % (self.base_path.name, self.denoise.get(),
                                   '  (layout ignored: an edit is one image)' if self._batch() > 1 else ''))
        else:
            self.hint.configure(text='')
        if batch == 1:
            self.status.configure(text='queued… one %d px image' % size)
        else:
            if batch >= 16 and size > 448 and not self.seq_var.get():
                self.hint.configure(text='16-up above 448 px in one batch needs more than a 12 GB card: '
                                        '16 x 384 px already peaks near 11.7 GiB as a batch')
            cols = 2 if batch == 4 else 4 if batch == 16 else 3
            self.status.configure(text='queued… %d images at %d px -> %d px sheet, %s'
                                  % (batch, size, size * cols + (cols - 1) * 6,
                                     'seeds %d-%d one at a time' % (seed, seed + batch - 1)
                                     if self.seq_var.get() else 'one batch of %d' % batch))
        try:
            steps = int(self.steps_var.get())
        except (AttributeError, ValueError):
            steps = self.args.steps
        self.engine.generate(prompt, size, size, steps, 1.0,
                             seed, self.args.out, base_path=(str(self.base_path) if self.base_path else None),
                             denoise=self.denoise.get(), batch=batch, sequential=self.seq_var.get(),
                             sampler=self.sampler_var.get())

    def _poll(self):
        try:
            while True:
                kind, payload = self.engine.events.get_nowait()
                if kind == 'status':
                    self.status.configure(text=payload)
                elif kind == 'ready':
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
                    self.status.configure(text='ready — type a prompt and press Enter')
                    if self.args.auto:
                        self.generate()
                elif kind == 'queued':
                    self.status.configure(text='generating…')
                elif kind == 'ws':
                    self._on_ws(payload)
                elif kind == 'ws_error':
                    self.hint.configure(text='progress feed unavailable (%s); the image still arrives' % payload)
                elif kind == 'cancelled':
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
                    self.stop_btn.state(['disabled'])
                    self.bar.grid_remove()
                    n = payload if isinstance(payload, int) else 0
                    self.status.configure(text=('stopped - nothing was saved'
                                                if n == 0 else
                                                'stopped - %d image%s already saved; tweak and press Enter again'
                                                % (n, '' if n == 1 else 's')))
                elif kind == 'tile':
                    raw, path, idx, total = payload
                    self._sheet = None
                    self._display_raw(raw, path)
                    self.status.configure(text='tile %d of %d received' % (idx, total))
                elif kind == 'image':
                    raw, path = payload
                    self._raw = raw
                    meta = read_meta(path)
                    if meta and meta.get('tiles'):
                        n = len(meta['tiles'])
                        cols = {4: 2, 9: 3, 16: 4}.get(n) or max(1, int(math.ceil(math.sqrt(n))))
                        self._sheet = {'cols': cols, 'rows': int(math.ceil(n / cols)), 'tiles': meta['tiles']}
                    else:
                        self._sheet = None
                    self._show(raw)
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
                    self.stop_btn.state(['disabled'])
                    self.bar.grid_remove()
                    self.status.configure(text='wrote %s' % path)
                    self.refresh_gallery(select=path)
                    self._set_base(path)
                    self._current = Path(path)
                    self.copy_btn.state(['!disabled'])
                elif kind == 'error':
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
                    self.stop_btn.state(['disabled'])
                    self.bar.grid_remove()
                    self.status.configure(text='error')
                    self.hint.configure(text=payload)
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _on_ws(self, msg):
        mtype = msg.get('type')
        data = msg.get('data') or {}
        if mtype == 'progress':
            mx = data.get('max') or 1
            val = data.get('value') or 0
            self.bar.configure(maximum=mx, value=val)
            self.status.configure(text='step %d of %d' % (val, mx))
        elif mtype == 'executing' and data.get('node') is None:
            self.status.configure(text='decoding…')

    def _show(self, raw):
        self._img = Image.open(io.BytesIO(raw))
        self.canvas.delete('placeholder')
        self.root.update_idletasks()      # let the maximised window settle before measuring the canvas
        self.root.after_idle(self._redraw)

    def _redraw(self):
        self.canvas.delete('img')
        if self._img is None:
            return
        w = max(self.canvas.winfo_width() - 12, 64)
        h = max(self.canvas.winfo_height() - 12, 64)
        im = self._img.copy()
        im.thumbnail((w, h), Image.LANCZOS)   # never upscales, never overflows the canvas
        self._tk = ImageTk.PhotoImage(im)
        cx, cy = self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2
        self._draw = (cx - im.width // 2, cy - im.height // 2, im.width, im.height)
        self.canvas.create_image(cx, cy, image=self._tk, tags='img')

    def on_close(self):
        self.status.configure(text='stopping…')
        result = self.engine.shutdown()
        print('server: %s' % result)
        self.root.destroy()


def main(argv):
    ap = argparse.ArgumentParser(description='A small Z-Image prompt window with the server under its control.')
    ap.add_argument('--port', type=int, default=8006)
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--steps', type=int, default=4)
    ap.add_argument('--cfg', type=float, default=1.0)
    ap.add_argument('--seed', type=int, default=1234)
    ap.add_argument('--out', default=r'C:/Users/kevin/Pictures/zimage-ui')
    ap.add_argument('--no-autostart', action='store_true', help='never start a server; only use a running one')
    ap.add_argument('--no-progress', action='store_true', help='skip the websocket progress feed')
    ap.add_argument('--initial', default='', help='pre-fill the prompt box')
    ap.add_argument('--initial-file', default='', help='pre-fill from a text file (avoids shell quoting of a long prompt)')
    ap.add_argument('--auto', action='store_true', help='generate once as soon as the server is ready')
    args = ap.parse_args(argv)

    engine = Engine(args.port, autostart=not args.no_autostart, progress=not args.no_progress)
    engine.start()

    root = tk.Tk()
    try:
        ttk.Style().theme_use('vista')
    except Exception:
        pass
    app = App(root, engine, args)
    try:
        root.mainloop()
    finally:
        # belt and braces: if the window dies by any other route, do not leave an engine behind
        if engine.owned:
            print('server: %s (finally)' % zg.stop_server(args.port))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
