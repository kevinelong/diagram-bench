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
import os
import queue
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
from tkinter import ttk
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


def read_meta(png):
    """The sidecar written at save time: prompt, seed, size, base image, strength."""
    j = Path(png).with_suffix('.json')
    if not j.exists():
        return None
    try:
        return json.loads(j.read_text(encoding='utf-8'))
    except Exception:
        return None


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
    def generate(self, prompt, width, height, steps, cfg, seed, out_dir, base_path=None, denoise=1.0):
        def work():
            try:
                client_id = self._listen_progress() if self.progress else None
                base_name = None
                if base_path:
                    base_name = zg.upload_image('http://%s:%d' % (self.host, self.port), base_path)
                    self.events.put(('status', 'base image uploaded: %s' % base_name))
                wf = zg.workflow(prompt, width, height, steps, cfg, seed,
                                 'zimage-ui-%d' % int(time.time() * 1000),
                                 base_image=base_name, denoise=(1.0 if not base_name else denoise))
                self.events.put(('status', 'queued'))
                pid = self._post('/prompt', {'prompt': wf, 'client_id': client_id})['prompt_id']
                self.events.put(('queued', pid))
                started = time.monotonic()
                while time.monotonic() - started < 900:
                    h = self._get('/history/' + pid, timeout=30)
                    if pid in h:
                        entry = h[pid]
                        images = [i for out in (entry.get('outputs') or {}).values() for i in out.get('images', [])]
                        if entry.get('status', {}).get('status_str') == 'error':
                            self.events.put(('error', 'workflow failed'))
                            return
                        if images:
                            self._fetch(images[0], out_dir, prompt, seed, width, height,
                                        meta={'steps': steps, 'cfg': cfg,
                                              'base_image': (str(base_path) if base_path else None),
                                              'denoise': (denoise if base_path else 1.0)})
                            return
                    time.sleep(0.4)
                self.events.put(('error', 'timed out waiting for the image'))
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', 'replace')
                hint = ''
                if 'not in []' in body or 'value_not_in_list' in body:
                    hint = '  (server has no model paths - restart it with start-comfy.ps1)'
                self.events.put(('error', 'rejected: %s%s' % (body[:200], hint)))
            except Exception as e:
                self.events.put(('error', '%s: %s' % (type(e).__name__, e)))
        threading.Thread(target=work, daemon=True).start()

    def _fetch(self, img, out_dir, prompt, seed, width, height, meta=None):
        q = urllib.parse.urlencode({'filename': img['filename'], 'subfolder': img.get('subfolder', ''),
                                    'type': img.get('type', 'output')})
        raw = urllib.request.urlopen('http://%s:%d/view?%s' % (self.host, self.port, q), timeout=120).read()
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = '%s_%dx%d_seed%d.png' % (datetime.now().strftime('%Y%m%d-%H%M%S'), width, height, seed)
        path = out_dir / name
        path.write_bytes(raw)
        (out_dir / 'last-prompt.txt').write_text(prompt, encoding='utf-8')
        meta = meta or {}
        record = {'image': str(path), 'prompt': prompt, 'seed': seed, 'width': width, 'height': height,
                  'created': datetime.now().isoformat(timespec='seconds'), 'model': zg.UNET,
                  'steps': meta.get('steps'), 'cfg': meta.get('cfg'),
                  'base_image': meta.get('base_image'), 'denoise': meta.get('denoise')}
        path.with_suffix('.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
        self.events.put(('image', (raw, str(path))))


# --------------------------------------------------------------------------- the window


class App:
    def __init__(self, root, engine, args):
        self.root, self.engine, self.args = root, engine, args
        self.busy = True
        self._img = None
        self._raw = None
        self.base_path = None
        self._current = None
        self._files = []

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
        # ---- far left: previous images
        gal = ttk.Frame(root, padding=(10, 10, 4, 10))
        gal.grid(row=0, column=0, sticky='nsew')
        gal.columnconfigure(0, weight=1)
        gal.rowconfigure(1, weight=1)
        ttk.Label(gal, text='Previous').grid(row=0, column=0, sticky='w')
        self.gallery = tk.Listbox(gal, exportselection=False, activestyle='dotbox', font=('Segoe UI', 9))
        self.gallery.grid(row=1, column=0, sticky='nsew', pady=(4, 6))
        self.gallery.bind('<<ListboxSelect>>', lambda e: self._on_pick())
        self.gallery.bind('<Double-Button-1>', lambda e: self.generate())
        btns = ttk.Frame(gal)
        btns.grid(row=2, column=0, sticky='ew')
        ttk.Button(btns, text='Use as base', command=self.use_as_base).pack(fill='x')
        ttk.Button(btns, text='Clear base', command=self.clear_base).pack(fill='x', pady=(3, 0))
        ttk.Button(btns, text='Refresh', command=self.refresh_gallery).pack(fill='x', pady=(3, 0))
        ttk.Button(btns, text='Open folder', command=self.open_folder).pack(fill='x', pady=(3, 0))

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
        ttk.Label(row, text='seed').grid(row=0, column=1, padx=(12, 4))
        self.seed = tk.StringVar(value=str(args.seed))
        ttk.Entry(row, textvariable=self.seed, width=10).grid(row=0, column=2, sticky='w')

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

        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._poll()

    # -- gallery -----------------------------------------------------------
    def refresh_gallery(self, select=None):
        out = Path(self.args.out)
        files = sorted(out.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True) if out.exists() else []
        self._files = files
        if self.base_path is not None and not any(f.name == self.base_path.name for f in files):
            self.clear_base()
        self.gallery.delete(0, 'end')
        for f in files:
            self.gallery.insert('end', f.name)
        if files:
            idx = 0
            if select:
                for i, f in enumerate(files):
                    if f.name == Path(select).name:
                        idx = i
                        break
            self.gallery.selection_clear(0, 'end')
            self.gallery.selection_set(idx)
            self.gallery.see(idx)

    def _picked(self):
        sel = self.gallery.curselection()
        if not sel:
            return None
        return self._files[sel[0]]

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

    def _on_pick(self):
        """Selecting a row shows it AND makes it the base; deselecting clears both."""
        f = self._picked()
        if not f:
            self.clear_base()
            return
        self._display(f)
        self._set_base(f)
        meta = read_meta(f)
        if meta:
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', meta.get('prompt') or '')
            if meta.get('seed') is not None:
                self.seed.set(str(meta['seed']))
            self.hint.configure(text='restored prompt and seed %s from %s%s'
                                % (meta.get('seed'), f.name,
                                   ('; itself edited %s at strength %s'
                                    % (Path(meta['base_image']).name, meta.get('denoise')))
                                   if meta.get('base_image') else ''))
        else:
            self.hint.configure(text='no prompt recorded for %s (saved before metadata existed)' % f.name)
        self.status.configure(text='base: %s  (Enter to edit it, strength %.2f)'
                              % (f.name, self.denoise.get()))

    def use_as_base(self):
        f = self._picked()
        if not f:
            self.hint.configure(text='select an image in the list first')
            return
        self._set_base(f)
        self._display(f)

    def clear_base(self):
        self.base_path = None
        self.base_label.configure(text='(none - text to image)')
        self.hint.configure(text='')
        self.gallery.selection_clear(0, 'end')
        self._clear_picture()
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
        self.busy = True
        self.gen_btn.state(['disabled'])
        self.hint.configure(text='')
        self.bar.configure(value=0.0)
        self.bar.grid()
        self.status.configure(text='queued…')
        self.hint.configure(text='starting from %s (strength %.2f)' % (self.base_path.name, self.denoise.get())
                            if self.base_path else '')
        self.engine.generate(prompt, self.args.size, self.args.size, self.args.steps, self.args.cfg,
                             seed, self.args.out, base_path=(str(self.base_path) if self.base_path else None),
                             denoise=self.denoise.get())

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
                elif kind == 'image':
                    raw, path = payload
                    self._raw = raw
                    self._show(raw)
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
                    self.bar.grid_remove()
                    self.status.configure(text='wrote %s' % path)
                    self.refresh_gallery(select=path)
                    self._set_base(path)
                    self._current = Path(path)
                    self.copy_btn.state(['!disabled'])
                elif kind == 'error':
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
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
        self.canvas.create_image(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                 image=self._tk, tags='img')

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
