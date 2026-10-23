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


SETTINGS_KEYS = ('size', 'steps', 'sampler', 'layout', 'history')

# Measured cost of one sequential request at 4 steps, on this box, with the model cache disabled. The cost is flat
# above 384 px - the per-request overhead dominates, not the pixel count - so area-based formulas are wrong here.
SIZE_SECONDS = {256: 6.5, 320: 10.0, 384: 14.0, 448: 13.9, 512: 13.7, 640: 14.0, 768: 13.7, 1024: 14.1}
BATCH_SECONDS_PER_TILE = 0.67      # measured: 16-up at 384 px in one batch took 10.75 s


def settings_path():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home())
    return base / 'zimage-ui' / 'settings.json'


def load_settings():
    try:
        data = json.loads(settings_path().read_text(encoding='utf-8'))
        return {k: v for k, v in data.items() if k in SETTINGS_KEYS}
    except Exception:
        return {}


def save_settings(data):
    try:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({k: data[k] for k in SETTINGS_KEYS if k in data}, indent=1), encoding='utf-8')
        return True
    except Exception:
        return False


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


STARTER_PROMPTS = [('icon', 'flat vector icon of a fox, simple shapes, white background'), ('diagram', 'clean flowchart: START, VALIDATE, STORE, DONE - vertical, thin black lines, white background'), ('photo', 'photo of a ceramic mug on a wooden table, soft window light')]


class Tooltip:
    """A one-line explanation shown under a control on hover.

    Delayed so that sweeping the mouse across the window does not flash a dozen bubbles, and destroyed on leave so no
    orphaned toplevels accumulate (tests assert on _tips being empty after a leave).
    """

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self._id = None
        self._tip = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')

    def _schedule(self, _=None):
        self._cancel()
        self._id = self.widget.after(self.delay, self.show)

    def _cancel(self):
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except Exception:
                pass
            self._id = None

    def show(self):
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry('+%d+%d' % (x, y))
        tk.Label(tip, text=self.text, background='#ffffe0', foreground='#222', relief='solid', bd=1,
                 font=('Segoe UI', 9), justify='left', wraplength=340, padx=7, pady=5).pack()
        self._tip = tip

    def hide(self, _=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class App:
    def __init__(self, root, engine, args):
        self.root, self.engine, self.args = root, engine, args
        self.busy = True
        self._img = None
        self._raw = None
        self._tips = []                   # populated as widgets appear, not in one later pass
        self._poll_id = None
        self._settings = load_settings()
        self._history = list(self._settings.get('history') or [])
        self._hist_pos = None
        self._run_start = None
        self._run_total = 0
        self.base_path = None
        self._base_from_gallery = False   # True only when a click in the gallery chose the base
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
        self.gal_canvas.configure(takefocus=1)
        for seq, fn in (('<Left>', lambda e: self._nav(-1, 0)), ('<Right>', lambda e: self._nav(1, 0)),
                        ('<Up>', lambda e: self._nav(0, -1)), ('<Down>', lambda e: self._nav(0, 1)),
                        ('<space>', self._nav_toggle), ('<Return>', self._nav_open)):
            self.gal_canvas.bind(seq, fn)
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
        self.more_btn = ttk.Button(btns, text='More…', command=self.open_all)
        self.more_btn.pack(fill='x')
        self.clear_base_btn = ttk.Button(btns, text='Clear base', command=self.clear_base)
        self.clear_base_btn.pack(fill='x', pady=(3, 0))
        self.refresh_btn = ttk.Button(btns, text='Refresh', command=self.refresh_gallery)
        self.refresh_btn.pack(fill='x', pady=(3, 0))
        self.folder_btn = ttk.Button(btns, text='Open folder', command=self.open_folder)
        self.folder_btn.pack(fill='x', pady=(3, 0))

        selrow = ttk.Frame(btns)
        selrow.pack(fill='x', pady=(6, 0))
        self.sel_all_btn = ttk.Button(selrow, text='Select all', command=self.select_all)
        self.sel_all_btn.pack(side='left', fill='x', expand=True)
        self.del_btn = ttk.Button(selrow, text='Delete', command=self.delete_selected, state='disabled')
        self.del_btn.pack(side='left', fill='x', expand=True, padx=(3, 0))
        self.sel_label = ttk.Label(btns, text='', foreground='#2a6df4')
        self.sel_label.pack(anchor='w', pady=(3, 0))
        ttk.Label(btns, text='ctrl+click adds · shift+click ranges · Del / Ctrl+A / Esc',
                  foreground='#666', font=('Segoe UI', 8), justify='left').pack(anchor='w')
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
        self.text.bind('<Up>', self._on_hist_up)
        self.text.bind('<Down>', self._on_hist_down)
        self.text.focus_set()

        row = ttk.Frame(left)
        row.grid(row=2, column=0, sticky='ew')
        row.columnconfigure(3, weight=1)
        self.gen_btn = ttk.Button(row, text='Generate  ⏎', command=self.generate)
        self.gen_btn.grid(row=0, column=0, sticky='w')
        self.stop_btn = ttk.Button(row, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.grid(row=0, column=1, sticky='w', padx=(6, 0))
        self.retry_btn = ttk.Button(row, text='Retry', command=self.retry)
        self.retry_btn.grid(row=0, column=1, sticky='w', padx=(6, 0))
        self.retry_btn.grid_remove()
        ttk.Label(row, text='size').grid(row=0, column=1, padx=(12, 4))
        self.size_var = tk.StringVar(value=str(self._settings.get('size') or args.size))
        self.size_cb = ttk.Combobox(row, textvariable=self.size_var, width=6, state='readonly',
                                    values=['256', '320', '384', '448', '512', '640', '768', '1024'])
        self.size_cb.grid(row=0, column=2, sticky='w')
        ttk.Label(row, text='layout').grid(row=0, column=3, padx=(10, 4))
        self.layout_var = tk.StringVar(value=self._settings.get('layout') or 'single')
        self.layout_cb = ttk.Combobox(row, textvariable=self.layout_var, width=9, state='readonly',
                                      values=['single', '4-up (2x2)', '9-up (3x3)', '16-up (4x4)'])
        self.layout_cb.grid(row=0, column=4, sticky='w')
        # one at a time by default: an N-up is N separate requests with seeds seed..seed+N-1, so every tile is
        # individually reproducible and the VRAM peak stays that of a single image
        self.seq_var = tk.BooleanVar(value=True)
        self.seq_chk = ttk.Checkbutton(row, text='one image at a time', variable=self.seq_var)
        self.seq_chk.grid(row=1, column=0, columnspan=3, sticky='w', pady=(4, 0))
        # off by default: a generation is text to image unless an image was picked from the gallery
        self.chain_var = tk.BooleanVar(value=False)
        self.chain_chk = ttk.Checkbutton(row, text='reuse last image as base', variable=self.chain_var)
        self.chain_chk.grid(row=2, column=0, columnspan=4, sticky='w', pady=(2, 0))
        seedbox = ttk.Frame(row)
        seedbox.grid(row=1, column=3, sticky='w', pady=(4, 0))
        ttk.Label(seedbox, text='seed').pack(side='left', padx=(0, 4))
        self.seed = tk.StringVar(value=str(args.seed))
        self.seed_entry = ttk.Entry(seedbox, textvariable=self.seed, width=10)
        self.seed_entry.pack(side='left')
        self.seed_minus = ttk.Button(seedbox, text='\u2212', width=2, command=lambda: self.bump_seed(-1))
        self.seed_minus.pack(side='left', padx=(3, 0))
        self.seed_plus = ttk.Button(seedbox, text='+', width=2, command=lambda: self.bump_seed(1))
        self.seed_plus.pack(side='left', padx=(2, 0))
        self.seed_rnd = ttk.Button(seedbox, text='Rnd', width=4, command=self.random_seed)
        self.seed_rnd.pack(side='left', padx=(4, 0))

        self.base_row = ttk.Frame(left)
        self.base_row.grid(row=8, column=0, sticky='ew', pady=(8, 0))
        ttk.Label(self.base_row, text='base:').pack(side='left')
        self.base_label = ttk.Label(self.base_row, text='(none - text to image)', foreground='#555')
        self._base_tip = None
        self.base_label.pack(side='left', padx=(4, 10))
        ttk.Label(self.base_row, text='strength').pack(side='left')
        self.denoise = tk.DoubleVar(value=0.6)
        self.strength_scale = ttk.Scale(self.base_row, from_=0.2, to=0.95, variable=self.denoise, length=130)
        self.strength_scale.pack(side='left', padx=4)
        self.denoise_text = ttk.Label(self.base_row, text='0.60')
        self.denoise_text.pack(side='left')
        self.denoise.trace_add('write', lambda *a: self.denoise_text.configure(text='%.2f' % self.denoise.get()))

        guide_row = ttk.Frame(left)
        guide_row.grid(row=9, column=0, sticky='ew', pady=(4, 0))
        ttk.Label(guide_row, text='steps').pack(side='left')
        self.steps_var = tk.StringVar(value=str(self._settings.get('steps') or self.args.steps))
        self.steps_cb = ttk.Combobox(guide_row, textvariable=self.steps_var, width=4, state='readonly',
                                     values=['2', '4', '6', '8', '12'])
        self.steps_cb.pack(side='left', padx=4)
        ttk.Label(guide_row, text='sampler').pack(side='left', padx=(12, 4))
        self.sampler_var = tk.StringVar(value=self._settings.get('sampler') or 'euler')
        self.sampler_cb = ttk.Combobox(guide_row, textvariable=self.sampler_var, width=15, state='readonly',
                                       values=['euler', 'euler_ancestral'])
        self.sampler_cb.pack(side='left')
        ttk.Label(guide_row, text='turbo is 4 steps, euler',
                  foreground='#777').pack(side='left', padx=(8, 0))

        # ---- middle: progress (sits under the button, above the status line)
        # the banner answers the only question that matters before pressing Enter
        # wraplength caps the requested width, so a long file name can never widen this column
        self.banner = ttk.Label(left, text='', font=('Segoe UI', 10, 'bold'), foreground='#1a1a1a',
                                wraplength=430, justify='left')
        self.banner.grid(row=3, column=0, sticky='w', pady=(10, 0))
        self._tip(self.banner, 'What the next Generate will do, with the base file name in full.')
        self.ticks = ttk.Label(left, text='', foreground='#2a6df4', font=('Consolas', 10))
        self.ticks.grid(row=4, column=0, sticky='w')

        self.bar = ttk.Progressbar(left, mode='determinate', maximum=1.0)
        self.bar.grid(row=5, column=0, sticky='ew', pady=(4, 2))
        self.bar.grid_remove()
        self.status = ttk.Label(left, text='starting…', foreground='#555')
        self.status.grid(row=6, column=0, sticky='w')
        self.hint = ttk.Label(left, text='', foreground='#555', wraplength=430, justify='left',
                              font=('Segoe UI', 9))
        self.hint.grid(row=7, column=0, sticky='w', pady=(4, 0))

        self.starters = ttk.Frame(left)
        self.starters.grid(row=7, column=0, sticky='w', pady=(4, 0))
        ttk.Label(self.starters, text='Nothing generated yet. Try one of these:',
                  foreground='#555').pack(anchor='w')
        for label, prompt in STARTER_PROMPTS:
            b = ttk.Button(self.starters, text=label, width=9,
                           command=lambda p=prompt: self.use_starter(p))
            b.pack(side='left', padx=(0, 6), pady=(4, 0))
            self._tip(b, prompt)
        self.starters.grid_remove()

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
        self.info_btn = ttk.Button(tools, text='\u24d8 metadata', command=self.show_metadata, state='disabled')
        self.info_btn.pack(side='left', padx=(6, 0))
        self.copy_status = ttk.Label(tools, text='', foreground='#555')
        self.copy_status.pack(side='left', padx=(8, 0))
        self.placeholder = self.canvas.create_text(14, 14, anchor='nw', fill='#888', tags='placeholder',
                                                   text='generated image appears here')

        prefill = self.args.initial
        if getattr(self.args, 'initial_file', ''):
            prefill = Path(self.args.initial_file).read_text(encoding='utf-8').strip()
        if prefill:
            self.text.insert('1.0', prefill)

        for v in (self.size_var, self.layout_var, self.steps_var, self.sampler_var, self.seed, self.denoise):
            v.trace_add('write', lambda *a: self._update_banner())
        self.canvas.bind('<Configure>', self._fit_placeholder, add='+')
        self.layout_var.trace_add('write', self._update_estimate)
        self._add_tooltips()
        self._update_banner()

        self.refresh_gallery()
        for seq, fn in (('<Delete>', self._on_delete_key), ('<Control-a>', self._on_ctrl_a),
                        ('<Escape>', self._on_escape)):
            root.bind(seq, fn)

        root.bind('<Destroy>', self._on_destroy, add='+')
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
        if files:
            self.starters.grid_remove()
        else:
            self.starters.grid()
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

    def _nav(self, dx, dy):
        """Move the selection one tile, so the strip is usable without the mouse."""
        order = [p for p in self._files[:RECENT_N] if p in self._tile_widgets]
        if not order:
            return 'break'
        cur = self.selected[-1] if self.selected else None
        i = order.index(cur) if cur in order else -1
        new = 0 if i < 0 else max(0, min(len(order) - 1, i + dx + dy * RECENT_COLS))
        self.selected = [order[new]]
        self._anchor = order[new]
        self._paint()
        return 'break'

    def _nav_toggle(self, event=None):
        cur = self.selected[-1] if self.selected else None
        if cur is None:
            return self._nav(0, 0)
        if cur in self.selected:
            self.selected.remove(cur)
        else:
            self.selected.append(cur)
        self._paint()
        return 'break'

    def _nav_open(self, event=None):
        if self.selected:
            self.select_file(self.selected[-1])
        return 'break'

    def retry(self):
        """Try the last request again after a transport failure."""
        self.retry_btn.grid_remove()
        self.hint.configure(text='')
        self.generate()

    def show_metadata(self):
        """Show the sidecar of the displayed image, selectable and copyable."""
        path = self._current
        if path is None:
            self.hint.configure(text='nothing displayed yet')
            return
        meta = read_meta(path)
        if not meta:
            self.hint.configure(text='no metadata beside %s' % Path(path).name)
            return
        lines = ['file: %s' % Path(path).name]
        for key in ('created', 'model', 'prompt', 'seed', 'size', 'steps', 'cfg', 'batch', 'seeds',
                    'base_image', 'denoise'):
            if meta.get(key) not in (None, ''):
                val = meta[key]
                if key == 'prompt':
                    lines.append('prompt: %s' % val)
                else:
                    lines.append('%s: %s' % (key, val))
        if meta.get('tiles'):
            lines.append('tiles: %d separate files' % len(meta['tiles']))
        body = '\n'.join(lines)

        win = tk.Toplevel(self.root)
        win.title('Metadata - %s' % Path(path).name)
        win.geometry('560x320')
        txt = tk.Text(win, wrap='word', font=('Consolas', 9))
        txt.pack(fill='both', expand=True, padx=8, pady=8)
        txt.insert('1.0', body)
        txt.configure(state='disabled')

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(body)
            self.copy_status.configure(text='metadata copied')
        ttk.Button(win, text='Copy metadata', command=copy).pack(pady=(0, 8))

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
        try:
            self.gal_canvas.focus_set()      # so arrows and Space drive the strip after a click
        except Exception:
            pass
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
    def _recycle_files(self, files):
        """Move files to the Windows Recycle Bin. True if the shell accepted every one of them.

        A delete that cannot be undone is the only action in this window that can destroy work, so the default path
        is a reversible one. Returns False (caller falls back to unlink) if the shell refuses, e.g. for a path on a
        volume without a recycle bin.
        """
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return False

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', wintypes.LPCWSTR),
                        ('pTo', wintypes.LPCWSTR), ('fFlags', ctypes.c_ushort),
                        ('fAnyOperationsAborted', wintypes.BOOL), ('hNameMappings', wintypes.LPVOID),
                        ('lpszProgressTitle', wintypes.LPCWSTR)]

        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x0004, 0x0010, 0x0040, 0x0400
        names = [str(f) for f in files if Path(f).exists()]
        if not names:
            return True
        try:
            buf = ctypes.create_unicode_buffer('\0'.join(names) + '\0\0')
            op = SHFILEOPSTRUCTW(None, FO_DELETE, ctypes.cast(buf, wintypes.LPCWSTR), None,
                                 FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                                 False, None, None)
            rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception:
            return False
        return rc == 0 and not op.fAnyOperationsAborted

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
                'Move %d image%s, with the metadata and thumbnail beside each, to the Recycle Bin?\n\n'
                'You can restore %s from there if it was a mistake.'
                % (n, '' if n == 1 else 's', 'it' if n == 1 else 'them'),
                parent=self.root):
            return 0
        files = []
        for p in paths:
            files += [p, p.with_suffix('.json'), p.parent / THUMB_DIR / (p.stem + '.jpg')]
        recycled = self._recycle_files(files)
        gone = [p for p in paths if not p.exists()]
        if len(gone) < len(paths):
            # the shell would not take them (off-volume path, or no recycle bin): hard delete what is left
            gone += [p for p in paths if p.exists() and self._delete_one(p)]
            recycled = False
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
            self.info_btn.state(['disabled'])
            self._img = None
            self._current = None
        self.selected = [p for p in self.selected if p not in paths]
        self.refresh_gallery()
        self.status.configure(text='%s %d image%s%s'
                              % ('moved to the Recycle Bin' if recycled else 'deleted',
                                 len(gone), '' if len(gone) == 1 else 's',
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
        self._set_base(path, from_gallery=True)
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
        self.info_btn.state(['!disabled'] if self._current else ['disabled'])
        self.copy_status.configure(text='')
        self._img = Image.open(io.BytesIO(raw))
        self.canvas.delete('placeholder')
        self.root.after_idle(self._redraw)

    def _display(self, path):
        self._current = Path(path)
        self.copy_btn.state(['!disabled'])
        self.info_btn.state(['!disabled'])
        self.copy_status.configure(text='')
        try:
            self._img = Image.open(path)
        except Exception as e:
            self.hint.configure(text='cannot open %s: %s' % (Path(path).name, type(e).__name__))
            return
        self.canvas.delete('placeholder')
        self.root.after_idle(self._redraw)

    def _set_base(self, path, from_gallery=False):
        self._base_from_gallery = from_gallery
        self.base_path = Path(path)
        self.base_label.configure(text=self._short_name(self.base_path.name))
        if self._base_tip is None:
            self._base_tip = self._tip(self.base_label, '')
        self._base_tip.text = self.base_path.name
        self.hint.configure(text='next generation starts from %s at strength %.2f'
                            % (self.base_path.name, self.denoise.get()))
        self._update_banner()

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
        if not self._history or self._history[-1] != prompt:
            self._history.append(prompt)
            self._hist_pos = None
        self._run_start = time.time()
        self._run_total = batch

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
        self._base_from_gallery = False
        self.base_label.configure(text='(none - text to image)')
        self.hint.configure(text='')
        self._clear_picture()          # (there is no separate listbox here; repaint drops the base ring)
        self._paint()
        self._update_banner()
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
        """A fresh seed that is never the value already in the box, so the change is always visible."""
        cur = self.seed.get()
        v = random.randint(0, 999_999_999)
        while str(v) == cur:
            v = random.randint(0, 999_999_999)
        self.seed.set(str(v))

    # -- self-documentation ---------------------------------------------------
    def _tip(self, widget, text):
        tip = Tooltip(widget, text)
        self._tips.append(tip)
        return tip

    def _add_tooltips(self):
        """One line under every control, so nothing has to be guessed or read in a manual."""
        t = self._tip
        t(self.gen_btn, 'Generate with the current prompt (Enter).')
        t(self.stop_btn, 'Interrupt this run. Tiles already written are kept.')
        t(self.size_cb, 'Image size in pixels. Bigger is slower and uses more VRAM.')
        t(self.layout_cb, 'How many images one press makes: 1, 4, 9 or 16, saved as separate tiles on one sheet.')
        t(self.steps_cb, 'Sampling steps. This model is distilled for 4; extra steps cost time for little change.')
        t(self.sampler_cb, 'euler is clean. euler_ancestral injects noise every step: grain and more variation.')
        t(self.seed_entry, 'The seed picks the starting noise. Same seed and prompt give the same picture.')
        t(self.seed_minus, 'Seed - 1')
        t(self.seed_plus, 'Seed + 1')
        t(self.seed_rnd, 'Draw a new random seed.')
        t(self.seq_chk, 'On: each tile is its own request with its own seed - reproducible, and only one image '
                        'in VRAM at a time.\nOff: one batch, about 8x faster, much higher VRAM peak.')
        t(self.chain_chk, 'On: the image just made becomes the base for the next run.\n'
                          'Off: every run is text to image unless you pick an image in the gallery.')
        t(self.strength_scale, 'How much of the base image survives.\n0.2 loose sketch - 0.60 keeps the '
                               'composition - 0.95 replaces almost everything.')
        t(self.copy_btn, 'Copy the current image to the Windows clipboard.')
        t(self.more_btn, 'Browse every saved image. The selection is shared with the strip.')
        t(self.clear_base_btn, 'Forget the base image; the next run is text to image.')
        t(self.refresh_btn, 'Re-read the folder, for example after files changed outside the window.')
        t(self.folder_btn, 'Open the output folder in Explorer.')
        t(self.sel_all_btn, 'Select every tile in the strip; press again to clear.')
        t(self.del_btn, 'Send the selected images, their metadata and thumbnails to the Recycle Bin.')

    @staticmethod
    def _short_name(name, keep=26):
        """Elide a long filename from the front, keeping the tail - that is where seed and sheet size live."""
        name = str(name)
        return name if len(name) <= keep else '\u2026' + name[-(keep - 1):]

    def _update_banner(self):
        """Say what the next Generate will do, in one line, before it is pressed."""
        if not hasattr(self, 'banner'):
            return
        if self.base_path is None:
            mode, colour = 'TEXT → IMAGE', '#1a1a1a'
        else:
            mode = 'EDIT %s at %.2f' % (self._short_name(self.base_path.name), self.denoise.get())
            colour = '#2a6df4'
        n = self._batch()
        what = 'one image' if n == 1 else '%d tiles' % n
        self.banner.configure(text='%s   ·   %s steps · %s · %s px · %s   ·   seed %s'
                                   % (mode, self.steps_var.get(), self.sampler_var.get(), self.size_var.get(),
                                      what, self.seed.get()),
                              foreground=colour)

    # -- persistence, starters, prompt history -------------------------------
    def _persist(self):
        self._settings = {'size': self.size_var.get(), 'steps': self.steps_var.get(),
                          'sampler': self.sampler_var.get(), 'layout': self.layout_var.get(),
                          'history': self._history[-20:]}
        return save_settings(self._settings)

    def use_starter(self, prompt):
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', prompt)
        self.text.focus_set()
        self.hint.configure(text='prompt loaded - press Enter, or edit it first')

    def _set_prompt(self, text):
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.mark_set('insert', 'end-1c')

    def _on_hist_up(self, event=None):
        """Up recalls the previous prompt, but only from the first line, so normal editing still works."""
        if not self._history or self.text.index('insert').split('.')[0] != '1':
            return None
        if self._hist_pos is None:
            self._hist_pos = len(self._history) - 1
        elif self._hist_pos > 0:
            self._hist_pos -= 1
        self._set_prompt(self._history[self._hist_pos])
        return 'break'

    def _on_hist_down(self, event=None):
        if self._hist_pos is None or not self._history:
            return None
        if self._hist_pos < len(self._history) - 1:
            self._hist_pos += 1
            self._set_prompt(self._history[self._hist_pos])
        else:
            self._hist_pos = None
            self._set_prompt('')
        return 'break'

    # -- progress and cost ---------------------------------------------------
    @staticmethod
    def _short_time(sec):
        sec = int(round(max(0, sec)))
        return '%d s' % sec if sec < 60 else '%d m %02d s' % (sec // 60, sec % 60)

    def _show_tile_progress(self, idx, total):
        self.ticks.configure(text='\u25cf' * idx + '\u25cb' * max(0, total - idx))
        eta = ''
        if self._run_start and idx:
            per = (time.time() - self._run_start) / idx
            eta = ' \u00b7 \u2248 %s left' % self._short_time(per * (total - idx))
        self.status.configure(text='tile %d of %d%s' % (idx, total, eta))

    def _update_estimate(self, *_):
        """Estimate the cost of the chosen layout from the two configurations that were actually measured."""
        n = self._batch()
        if n <= 1 or self.busy:
            return
        try:
            steps, size = int(self.steps_var.get()), int(self.size_var.get())
        except (TypeError, ValueError):
            return
        per = SIZE_SECONDS.get(size, 14.0)
        if steps != 4:
            per *= steps / 4.0          # only the 4-step points were measured; say so in the text
        seq = per * n
        bat = max(1.0, BATCH_SECONDS_PER_TILE * n) * (steps / 4.0)
        tail = '' if steps == 4 else ' (scaled from 4-step measurements)'
        self.hint.configure(
            text='%d tiles \u00b7 \u2248 %s one at a time (%.1f s/tile measured at %d px), '
                 'or \u2248 %s as one batch (0.67 s/tile measured at 384 px)%s.'
                 % (n, self._short_time(seq), per, size, self._short_time(bat), tail))

    def _fit_placeholder(self, event=None):
        """Keep the placeholder text inside the canvas however narrow the window gets."""
        w = event.width if event is not None else self.canvas.winfo_width()
        try:
            self.canvas.itemconfigure('placeholder', width=max(90, w - 28))
        except Exception:
            pass

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

        if not self._history or self._history[-1] != prompt:
            self._history.append(prompt)
            self._hist_pos = None
        self._run_start = time.time()
        self._run_total = batch

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
        if not self.root.winfo_exists():     # the window is gone: stop rescheduling
            return
        if self._poll_id is not None:        # never leave a second timer pending behind this one
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        try:
            while True:
                kind, payload = self.engine.events.get_nowait()
                if kind == 'status':
                    self.status.configure(text=payload)
                elif kind == 'ready':
                    self.retry_btn.grid_remove()
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
                    self.ticks.configure(text='')
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
                    self._run_total = total
                    self._show_tile_progress(idx, total)
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
                    self.ticks.configure(text='')
                    self.status.configure(text='wrote %s' % path)
                    self.refresh_gallery(select=path)
                    if self.chain_var.get():
                        self._set_base(path)
                        base_note = 'chained from %s' % Path(path).name
                    else:
                        base_note = 'base unchanged (%s)' % (
                            'none - next run is text to image' if self.base_path is None
                            else 'still %s' % self.base_path.name)
                    if self._base_from_gallery:
                        note = 'seed %s kept from the gallery image' % self.seed.get()
                    else:
                        self.random_seed()
                        note = 'next seed %s' % self.seed.get()
                    self.hint.configure(text='saved; %s; %s' % (base_note, note))
                    self._current = Path(path)
                    self.copy_btn.state(['!disabled'])
                elif kind == 'error':
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
                    self.stop_btn.state(['disabled'])
                    self.bar.grid_remove()
                    self.ticks.configure(text='')
                    self.status.configure(text='failed - nothing was saved')
                    self.hint.configure(
                        text='%s\n\nThe request went to ComfyUI on 127.0.0.1:%d. If the server is not running, '
                             'start it and press Retry.' % (payload, self.args.port))
                    self.retry_btn.grid()
        except queue.Empty:
            pass
        self._poll_id = self.root.after(60, self._poll)

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

    def _on_destroy(self, event=None):
        """Cancel the poll loop however the window goes away, not just via the close button."""
        if event is not None and event.widget is not self.root:
            return
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

    def on_close(self):
        self._persist()
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
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
