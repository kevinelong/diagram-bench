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
import re
import socket
import struct
import subprocess

from winproc import popen, run
import scenes as scene_detect
import sys
import threading
import time
import urllib.parse
import webbrowser
import urllib.request
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from PIL import Image, ImageDraw, ImageTk

import zimage_gen as zg
from rephrase import cycle_period, has_synonyms, rephrase_text


# --------------------------------------------------------------------------- the host's own colours


def _hex(rgb):
    return '#%02x%02x%02x' % tuple(int(v) for v in rgb)


def _mix(a, b, t):
    """Blend two '#rrggbb' colours; used only where Windows offers no colour of its own."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return _hex(pa[i] + (pb[i] - pa[i]) * t for i in range(3))


def _registry_value(path, name, default=None):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except Exception:
        return default


# A real bitmap face: at 8 px an antialiased UI font turns to mush, and Fixedsys is what Windows still ships for exactly
# this job. Falls back to a monospace face that exists everywhere.
PIXEL_FONT = ('Fixedsys', 8)
FALLBACK_PIXEL_FONT = ('Consolas', 7)
SIZE_BANDS = ((64 * 1024, '#ff6347', '#ffffff'),        # tomato red: 64 kB or less
              (1024 * 1024, '#ffbf00', '#1a1a1a'),      # amber yellow: in between
              (None, '#3cb371', '#0b1a10'))             # medium green: 1 MB or more


def human_size(n):
    """A file size a 3-character badge can hold: 64k, 412k, 1.2M."""
    n = int(n or 0)
    if n >= 1024 * 1024:
        return '%.1fM' % (n / (1024.0 * 1024)) if n < 10 * 1024 * 1024 else '%dM' % round(n / (1024.0 * 1024))
    return '%dk' % max(1, int(round(n / 1024.0)))


def size_band(n):
    """Background and text colour for a size: tomato red at 64 kB or less, green at 1 MB or more, amber between.

    The boundaries are inclusive on both ends as asked, so exactly 1 MB is green and exactly 64 kB is red - a chain of
    `n <= limit` tests cannot say that (1 MB would fall into the amber arm) and the test pins it.
    """
    n = int(n or 0)
    if n <= 64 * 1024:
        return '#ff6347', '#ffffff'
    if n >= 1024 * 1024:
        return '#3cb371', '#0b1a10'
    return '#ffbf00', '#1a1a1a'


def shorten_title(text, words=10):
    """A folder tile shows three lines, which is about ten words; the full sentence stays in its tooltip."""
    text = ' '.join(str(text or '').split())
    parts = text.split(' ')
    if len(parts) > words:
        return ' '.join(parts[:words]).rstrip(',.;:-') + '\u2026'
    return text


GRID_MOVES = (('Left', 'left'), ('Right', 'right'), ('Up', 'up'), ('Down', 'down'),
              ('Prior', 'pageup'), ('Next', 'pagedown'), ('Home', 'home'), ('End', 'end'),
              ('Control-Home', 'all-home'), ('Control-End', 'all-end'))


def grid_move(index, count, cols, move, rows=1):
    """Where the W3C APG layout-grid pattern takes the current cell, in a row-major grid of `count` cells.

    Three lists answer the arrow keys - the strip, View All and the folder browser - and they must answer them the same
    way, so the movement itself lives here once instead of in each of them. The pattern
    (https://www.w3.org/WAI/ARIA/apg/patterns/grid/, "Keyboard Interaction For Layout Grids") says:

    * left/right are one cell and **wrap across rows** - which for a row-major index is simply +1/-1, clamped at the two
      ends of the list, and is why the owner's "wrap to the next row" request is the same thing as the standard;
    * up/down keep the column and **never wrap**: below the last full row there is no cell in that column, so the move
      is refused rather than jumping sideways into a short final row;
    * page up/down keep the column and move by whole visible rows, clamping to the last row that still has that column;
    * home/end are the ends of the *current row*, ctrl+home/ctrl+end the ends of the whole list.

    `index` may be -1 (nothing current yet): the first press lands on the first cell, as the strip has always done, and
    ctrl+end/end from nothing land on the last cell because that is what those keys are asked for. Returns the new index.
    """
    try:
        count, cols, rows = int(count), max(1, int(cols)), max(1, int(rows))
    except Exception:
        return 0
    if count <= 0:
        return None
    if not 0 <= index < count:
        # no current cell: the edge keys keep their meaning, every other key starts the walk at the beginning
        return count - 1 if move in ('end', 'all-end') else 0
    row = index // cols
    col = index % cols
    if move == 'right':
        return min(count - 1, index + 1)
    if move == 'left':
        return max(0, index - 1)
    if move == 'down':
        below = index + cols
        return below if below < count else index
    if move == 'up':
        above = index - cols
        return above if above >= 0 else index
    if move in ('pageup', 'pagedown'):
        last_row = (count - 1) // cols
        step = rows if move == 'pagedown' else -rows
        target = min(last_row, max(0, row + step))
        # a short last row does not have this column: step back rather than slide left into whatever is there
        while target > 0 and target * cols + col >= count:
            target -= 1
        return target * cols + col
    if move == 'home':
        return row * cols
    if move == 'end':
        return min(count - 1, row * cols + cols - 1)
    if move == 'all-home':
        return 0
    if move == 'all-end':
        return count - 1
    return index


def grid_visible_rows(height, pitch):
    """How many whole rows a viewport of `height` shows, given the measured distance between two rows.

    Page down has to move "one visible set", and the set is a property of the window as it is sized right now - a
    maximised View All shows eight rows of 190 px tiles, the strip perhaps four - so this is measured, never assumed.
    """
    try:
        height, pitch = int(height), int(pitch)
    except Exception:
        return 1
    if height <= 0 or pitch <= 0:
        return 1
    return max(1, height // pitch)


def grid_bind_moves(widget, handler):
    """Bind the layout-grid movement keys - plain and shift-extended - on one widget.

    Shift is bound separately because Tk will not let a `<Left>` binding answer `<Shift-Left>`. The same table feeds all
    three lists, so none of them can quietly offer a different subset of the pattern.
    """
    for seq, move in GRID_MOVES:
        widget.bind('<%s>' % seq, lambda e, m=move: handler(e, m))
        if not seq.startswith('Control-'):
            widget.bind('<Shift-%s>' % seq, lambda e, m=move: handler(e, m))


class Theme:
    """The colours the user already chose, instead of colours invented here.

    Three sources, in order of authority:

    * **Tk's system colour names** - `SystemWindow`, `SystemButtonFace`, `SystemWindowText`, `SystemGrayText`,
      `SystemHighlight`. On Windows these are the real `GetSysColor` values, so they follow the user's classic colour
      settings *and* high-contrast themes; that is the whole reason to use them instead of hex literals.
    * **`AppsUseLightTheme`** under `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize` - dark mode
      is not a colour, it is a mode, and Windows reports it nowhere else. Without it the app is a light island on a dark
      desktop, which is the most common complaint about Win32 and Tk applications.
    * **The DWM accent** (`HKCU\\Software\\Microsoft\\Windows\\DWM\\AccentColor`, stored as AABBGGRR) for selection,
      focus and the A/B overlay.

    What Windows does not provide - a backdrop behind a photo, a hover tone - is *derived* from the window colour, so it
    tracks light/dark and contrast themes instead of becoming a second palette that drifts.
    """

    SYS = (('window', 'SystemWindow'), ('surface', 'SystemButtonFace'), ('text', 'SystemWindowText'),
           ('muted', 'SystemGrayText'), ('accent', 'SystemHighlight'), ('on_accent', 'SystemHighlightText'))

    LIGHT = {'window': '#ffffff', 'surface': '#f0f0f0', 'text': '#000000', 'muted': '#6d6d6d',
             'accent': '#0a84ff', 'on_accent': '#ffffff'}
    DARK = {'window': '#202020', 'surface': '#2b2b2b', 'text': '#f2f2f2', 'muted': '#a8a8a8',
            'accent': '#4cc2ff', 'on_accent': '#101010'}
    NEUTRAL = ('window', 'surface', 'text', 'muted')

    def __init__(self, root):
        self.root = root
        self.dark = self._dark_mode()
        base = dict(self.DARK if self.dark else self.LIGHT)
        for name, sysname in self.SYS:
            found = self._system(sysname)
            if found:
                base[name] = found
        if self.dark:
            # the system *classic* colours stay light on a dark desktop, so the mode wins for the neutrals
            for name in self.NEUTRAL:
                base[name] = self.DARK[name]
        accent = self._accent()
        if accent:
            base['accent'] = accent
        self.colours = base
        self.window = base['window']
        self.surface = base['surface']
        self.text = base['text']
        self.muted = base['muted']
        self.accent = base['accent']
        self.on_accent = base['on_accent']
        self.field = _mix(self.window, self.text, 0.06)
        self.border = _mix(self.window, self.text, 0.22)
        self.hover = _mix(self.surface, self.text, 0.10)
        self.sheet_bg = _mix(self.window, self.text, 0.10)     # behind a contact sheet's thumbnails
        self.tooltip_bg = _mix(self.window, '#3a3a3a' if self.dark else '#ffffe0', 0.9)
        self.win_mark = '#9be2b4' if self.dark else '#1a6b2a'
        self.error = '#ff7b72' if self.dark else '#c0392b'

    # -- where the values come from
    def _system(self, name):
        """Tk maps these names to GetSysColor on Windows, so they are the system's own values."""
        try:
            return _hex(v // 257 for v in self.root.winfo_rgb(name))
        except Exception:
            return None

    def _dark_mode(self):
        value = _registry_value(r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
                                'AppsUseLightTheme', 1)
        try:
            return int(value) == 0
        except Exception:
            return False

    def _accent(self):
        value = _registry_value(r'Software\Microsoft\Windows\DWM', 'AccentColor', None)
        if value is None:
            return None
        try:
            v = int(value)
        except Exception:
            return None
        b, g, r = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF      # stored as AABBGGRR
        if max(r, g, b) < 24:
            return None
        return _hex((r, g, b))

    # -- put them on the widgets
    def apply(self):
        style = ttk.Style()
        if self.dark:
            try:
                style.theme_use('clam')      # the one bundled ttk theme whose colours can actually be set
            except Exception:
                pass
            style.configure('.', background=self.window, foreground=self.text, fieldbackground=self.field,
                            bordercolor=self.border, lightcolor=self.surface, darkcolor=self.border,
                            troughcolor=self.surface, selectbackground=self.accent, selectforeground=self.on_accent)
            for name in ('TFrame', 'TLabel', 'TCheckbutton', 'TRadiobutton', 'TLabelframe'):
                style.configure(name, background=self.window, foreground=self.text)
            # clam pads buttons much wider than the vista theme does, and the action bar then needs more width than
            # the picture column has; these are the vista-ish metrics, not a new look
            style.configure('TButton', background=self.surface, foreground=self.text, bordercolor=self.border,
                            padding=(4, 2), width=-4)
            style.configure('TCombobox', padding=(2, 1))
            style.configure('TSpinbox', padding=(2, 1))
            style.map('TButton', background=[('active', self.hover)], foreground=[('disabled', self.muted)])
            for name in ('TEntry', 'TCombobox', 'TSpinbox'):
                style.configure(name, fieldbackground=self.field, foreground=self.text,
                                background=self.surface, arrowcolor=self.text)
            # a readonly combobox draws its field from the *state map*, not from fieldbackground, which is why the
            # first dark pass still showed white boxes with invisible values
            style.map('TCombobox',
                      fieldbackground=[('readonly', self.field), ('disabled', self.surface)],
                      foreground=[('readonly', self.text)],
                      background=[('readonly', self.surface), ('active', self.hover)],
                      selectbackground=[('readonly', self.field)], selectforeground=[('readonly', self.text)],
                      arrowcolor=[('readonly', self.text)])
            # the dropdown list is a classic tk Listbox, which no ttk style reaches
            for pattern, value in (('*TCombobox*Listbox.background', self.field),
                                   ('*TCombobox*Listbox.foreground', self.text),
                                   ('*TCombobox*Listbox.selectBackground', self.accent),
                                   ('*TCombobox*Listbox.selectForeground', self.on_accent)):
                self.root.option_add(pattern, value)
        else:
            try:
                style.theme_use('vista')
            except Exception:
                pass
        self.titlebar()
        try:
            # one style for the column's controls: 10 pt with real padding. The room comes from folding the options away.
            style = ttk.Style()
            style.configure('Big.TButton', padding=(8, 6), font=('Segoe UI', 10))
            style.configure('Big.TCheckbutton', font=('Segoe UI', 10))
            style.configure('Big.TCombobox', padding=(4, 4), font=('Segoe UI', 10))
        except Exception:
            pass

    def titlebar(self, window=None):
        """Ask for the Win32 caption now, and again once the window has a frame.

        `winfo_id()` is the *client* child (`TkChild`); the caption belongs to its `TkTopLevel` parent, which does not
        exist until the window is mapped - so a call made during construction lands on a handle DWM rejects
        (`E_HANDLE`, and the fallback then hits the child, which it also rejects). Hence: resolve at call time, and
        repeat on `<Map>` and once shortly after.

        """
        target = window or self.root
        applied = self._apply_titlebar(target)
        try:
            target.bind('<Map>', lambda _e: self._apply_titlebar(target), add='+')
        except Exception:
            pass
        try:
            target.after(250, lambda: self._apply_titlebar(target))
        except Exception:
            pass
        return applied

    def _apply_titlebar(self, window=None):
        """Win32 title bar, done the way Windows does it: dark mode, and the accent when the user asked for it there.

        Four DWM attributes, each best-effort because a build that does not know one must still get the others:
        `DWMWA_USE_IMMERSIVE_DARK_MODE` (20, 19 on 1809-1909), `DWMWA_BORDER_COLOR` (34), `DWMWA_CAPTION_COLOR` (35) and
        `DWMWA_TEXT_COLOR` (36). "Show accent colour on title bars and window borders" is `DWM\ColorPrevalence = 1`,
        which is the only place Windows records that choice; caption text is then black or white by luminance, because
        a caption colour without a matching text colour is worse than no accent at all. Colours here are COLORREFs -
        0x00BBGGRR - not the RRGGBB the rest of this file uses.

        Called for every window this app opens, not just the main one: a dark app with light sub-windows is the defect
        this fixes.
        """
        target = window or self.root
        try:
            import ctypes
            # GA_ROOT: the frame that owns the caption, not the client child Tk hands out from winfo_id()
            hwnd = ctypes.windll.user32.GetAncestor(target.winfo_id(), 2)
            if not hwnd:
                return False
            handle = ctypes.c_void_p(hwnd)

            def attribute(index, value):
                datum = ctypes.c_int(int(value))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, index, ctypes.byref(datum),
                                                           ctypes.sizeof(datum))

            for index in (20, 19):
                attribute(index, 1 if self.dark else 0)
            if int(_registry_value(r'Software\Microsoft\Windows\DWM', 'ColorPrevalence', 0) or 0):
                try:
                    r, g, b = (int(self.accent[i:i + 2], 16) for i in (1, 3, 5))
                    colourref = r | (g << 8) | (b << 16)
                    attribute(35, colourref)                     # caption background
                    attribute(34, colourref)                     # window border
                    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
                    attribute(36, 0x000000 if luminance > 150 else 0xFFFFFF)
                except Exception:
                    pass
            return True
        except Exception:
            return False


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
        r = run(['powershell', '-NoProfile', '-STA', '-Command', ps],
                           capture_output=True, text=True, timeout=90)
        return (r.returncode == 0 and 'ok' in r.stdout), (r.stdout + r.stderr).strip()[:180]
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e)


SETTINGS_KEYS = ('size', 'steps', 'sampler', 'layout', 'history', 'style', 'model', 'advanced', 'llm_url',
                 'favorites', 'custom_styles', 'runs', 'sidebar', 'seed_step', 'denoise', 'folders', 'motion')

# An image prompt describes a still; an image-to-video model obeys motion language. Sending the still's description
# with a clip asks for motion while describing stillness, which is exactly what "unusable" looked like.
DEFAULT_MOTION = 'the subject stays coherent, natural slow camera push in, no warping'

# Style presets are prompt suffixes, the way Fooocus exposes --style: the model is not retrained, the words are added.
# Mediums a prompt can name for itself. A style suffix that names a different one competes with these, and the prompt
# ends up asking for two things at once - "oil painting, cinematic still, anamorphic" - so Generate asks first.
STYLE_CONFLICT_WORDS = ('oil painting', 'watercolour painting', 'watercolor painting', 'acrylic painting', 'painting',
                        'photograph', 'photo', 'film still', 'film', 'sketch', 'drawing', 'illustration', 'anime',
                        '3d render', 'vector icon')

STYLES = {
    'none': '',
    'photo': 'photograph, natural light, shallow depth of field',
    'icon': 'flat vector icon, simple shapes, white background, minimal',
    'diagram': 'clean technical diagram, thin black lines, white background, labelled',
    'blueprint': 'engineering blueprint, white lines on blue, orthographic',
    'watercolour': 'watercolour painting, soft washes, paper texture',
    'cinematic': 'cinematic still, anamorphic, dramatic lighting, film grain',
}

# Measured cost of one sequential request at 4 steps, on this box, with the model cache disabled. The cost is flat
# above 384 px - the per-request overhead dominates, not the pixel count - so area-based formulas are wrong here.
SIZE_SECONDS = {256: 6.5, 320: 10.0, 384: 14.0, 448: 13.9, 512: 13.7, 640: 14.0, 768: 13.7, 1024: 14.1}
BATCH_SECONDS_PER_TILE = 0.67      # measured: 16-up at 384 px in one batch took 10.75 s


def settings_path():
    """Where the window keeps its state. ZIMAGE_SETTINGS_DIR redirects it, so tests never touch a real profile."""
    base = os.environ.get('ZIMAGE_SETTINGS_DIR')
    if base:
        return Path(base) / 'settings.json'
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'zimage-ui' / 'settings.json'


def load_settings():
    if os.environ.get('ZIMAGE_NO_SETTINGS'):     # live tests must never touch a real profile
        return {}
    try:
        data = json.loads(settings_path().read_text(encoding='utf-8'))
        return {k: v for k, v in data.items() if k in SETTINGS_KEYS}
    except Exception:
        return {}


def save_settings(data):
    if os.environ.get('ZIMAGE_NO_SETTINGS'):
        return False
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
RECENT_COLS = 3       # fallback; the strip computes its own from the width it has
RECENT_PX = 92       # on-screen size of a strip tile; the cache keeps 168 px
CONTENTS_W = 300     # requested width of the open-folder pane beside the folder list; it grows with the column
STRIP_TILE_MIN = 96  # a tile never shrinks below this: the row count follows the width, the tile follows the row
FOLDER_CELLS = 4     # pictures on a folder tile, as a 2x2 - nine-up forced one folder per row, two is the ask
# How different two pictures must be to share a tile. Raised from 8/24 after seeing sheets that were still four
# near-copies while clearly different pictures sat further down the folder - the old bar was passed by small changes.
FOLDER_DISTINCT = 16   # of 64 difference-hash bits
FOLDER_COLOUR = 40     # or a channel shift of this much between two quadrants: dHash alone cannot see colour
FOLDER_SCAN = 240      # how far back through a folder the walk may look for four that qualify
BATCH_TILES = 21     # strip pictures added per scroll to the bottom
BATCH_FOLDERS = 12   # folder tiles added per scroll
BATCH_CONTENTS = 24  # pictures added per scroll inside the open folder
FILTER_CAP = 200     # while a search is active the strip is a result list, not a recency window

#  Peaks measured on this box with nvidia-smi at 0.25 s (12,227 MiB total). Steps and hires barely move them; batch
#  size and the checkpoint dominate. Every entry below actually ran - including 16-up at 512 px, which an earlier
#  hand-written warning claimed would not fit.
VRAM_POINTS = {
    ('flux2 klein 9b uncensored', 1, 512): 8837,      # 2026-10-25 three-arm run
    ('flux2 klein 9b uncensored', 1, 1024): 9749,     # 2026-10-28 live ALL run: 24.5 s, 2,478 MiB left
    ('z-image turbo', 1, 512): 6708,
    ('z-image turbo', 1, 1024): 7193,
    ('z-image turbo', 16, 384): 8206,
    ('z-image turbo', 16, 512): 8927,
    ('flux1 dev', 1, 512): 9458,
}
VRAM_TOTAL_MIB = 12227


def estimate_vram(model, batch, size):
    """Nearest measured peak for this shape, and the point it came from. None if nothing similar was measured."""
    want_single = batch == 1
    cands = [(k, v) for k, v in VRAM_POINTS.items() if k[0] == model and (k[1] == 1) == want_single]
    if not cands:
        cands = [(k, v) for k, v in VRAM_POINTS.items() if k[0] == model]
    if not cands:
        return None, None
    key, mib = min(cands, key=lambda kv: abs(kv[0][2] - size))
    return mib, 'measured %s at %d px, batch %d' % (key[0], key[2], key[1])


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
    def make_clip(self, still, grid='1:1', frames=49, prompt='', seed=1234, end=None, model='5b'):
        """Render a few seconds from a still (or between two), off the UI thread. Frees the image model first."""
        self._cancel.clear()
        if self.progress:
            self._listen_progress()          # same websocket the image path uses, so the step counter works

        def work():
            try:
                import video
                rec = video.make_clip(still, grid=grid, frames=frames, prompt=prompt, seed=seed,
                                      host='http://%s:%d' % (self.host, self.port),
                                      end=end, model=model,
                                      on_event=lambda msg: self.events.put(('status', msg)))
                self.events.put(('clip', rec))
            except Exception as e:
                self.events.put(('error', '%s: %s' % (type(e).__name__, e)))

        threading.Thread(target=work, daemon=True).start()

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
                 batch=1, sequential=True, sampler='euler', negative='', profile=None, mask_path=None,
                 outpaint=None, style=None, hires=0.0, hires_denoise=0.45, sweep=None, seed_step=1,
                 scene=None, of=None):
        """One image, or a sheet of N.

        sequential=True runs the sampler N times with seeds seed, seed+1 ... so every tile is individually reproducible
        and the VRAM peak stays that of a single image. sequential=False asks ComfyUI for one batch of N latents, which
        is fewer round trips but whose tiles are not reproducible from individual seeds.
        """
        self._cancel.clear()
        self.last_workflow = None

        def work():
            try:
                out = Path(out_dir)          # not out_dir: assigning that would shadow the parameter
                out.mkdir(parents=True, exist_ok=True)
                meta = {'steps': steps, 'cfg': cfg, 'denoise': (denoise if base_path else 1.0),
                        'base_image': (str(base_path) if base_path else None),
                        'negative': negative or None, 'style': style or None,
                        'model': (profile or zg.DEFAULT_MODEL),
                        'mask': (Path(mask_path).name if mask_path else None),
                        'outpaint': list(outpaint) if outpaint else None,
                        'hires': (hires if hires > 1.0 else None),
                        'hires_denoise': (hires_denoise if hires > 1.0 else None),
                        'scene': scene, 'scene_of': of}
                (out / 'last-prompt.txt').write_text(prompt, encoding='utf-8')
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                base_name, scale_base = None, None
                if base_path:
                    base_name = zg.upload_image('http://%s:%d' % (self.host, self.port), base_path)
                    self.events.put(('status', 'base image uploaded: %s' % base_name))
                    try:
                        with Image.open(base_path) as _bi:
                            bw, bh = _bi.size
                        if (bw, bh) != (width, height):
                            # the size selector wins: resizing the base is the only way an edit changes scale
                            scale_base = (width, height)
                            self.events.put(('status', 'base %dx%d resized to %dx%d' % (bw, bh, width, height)))
                    except Exception:
                        pass
                mask_name = None
                if mask_path:
                    mask_name = zg.upload_image('http://%s:%d' % (self.host, self.port), mask_path)
                    self.events.put(('status', 'mask uploaded: %s' % mask_name))
                client_id = self._listen_progress() if self.progress else None

                total = 1 if base_name else max(1, batch)
                runs = range(total) if (sequential or base_name) else [0]
                paths, raws = [], []
                for run_i in runs:
                    if self._cancel.is_set():
                        raise GenerationCancelled()
                    # every tile gets its own seed: seed, seed+step, seed+2*step ... A batch request cannot do this -
                    # one latent batch is drawn from one seed - which is why sequential is the default.
                    this_seed = seed + run_i * max(1, int(seed_step)) if (sequential and not base_name) else seed
                    run_steps, run_cfg, value = steps, cfg, None
                    if sweep and sweep.get('values'):
                        value = sweep['values'][run_i % len(sweep['values'])]
                        if sweep['param'] == 'cfg':
                            run_cfg = float(value)
                        elif sweep['param'] == 'steps':
                            run_steps = int(value)
                        elif sweep['param'] == 'seed':
                            this_seed = seed + int(value)
                        self.events.put(('status', 'tile %d: %s=%s' % (run_i + 1, sweep['param'], value)))
                    if total > 1:
                        self.events.put(('status', 'tile %d of %d (seed %d)' % (run_i + 1, total, this_seed)))
                    wf = zg.workflow(prompt, width, height, run_steps, run_cfg, this_seed,
                                     'zimage-ui-%d-%d' % (int(time.time() * 1000), run_i),
                                     base_image=base_name,
                                     denoise=(1.0 if not base_name else denoise),
                                     batch=(1 if (sequential or base_name) else total),
                                     sampler=sampler, negative=negative, profile=profile,
                                     mask=mask_name, outpaint=outpaint,
                                     hires=hires, hires_denoise=hires_denoise, scale_base=scale_base)
                    self.last_workflow = wf
                    outs = self._queue_wait(wf)
                    for k, img in enumerate(outs):
                        raw = self._download(img)
                        # outpaint changes the canvas, so name and record what was actually produced
                        try:
                            real_w, real_h = Image.open(io.BytesIO(raw)).size
                        except Exception:
                            real_w, real_h = width, height
                        suffix = '' if total == 1 else ('_%02d' % (run_i + k + 1))
                        path = out / ('%s_%dx%d_seed%d%s.png' % (stamp, real_w, real_h, this_seed, suffix))
                        path.write_bytes(raw)
                        paths.append(path)
                        raws.append(raw)
                        rec = dict(meta, image=str(path), prompt=prompt, seed=this_seed, width=real_w,
                                   steps=run_steps, cfg=run_cfg,
                                   sweep=(('%s=%s' % (sweep['param'], value)) if sweep else None),
                                   height=real_h, created=datetime.now().isoformat(timespec='seconds'),
                                   model=(profile or zg.DEFAULT_MODEL),
                                   unet=zg.MODELS.get(profile or zg.DEFAULT_MODEL, {}).get('unet'),
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


class ActionButton(tk.Button):
    """A tk.Button that answers to the ttk state([...]) API, so call sites do not care which toolkit broke the line."""

    def state(self, spec=None):
        if spec is None:
            return ('disabled',) if str(self['state']) == 'disabled' else ()
        for s in spec:
            if s.startswith('!'):
                super().configure(state='normal')
            elif s in ('disabled', 'active', 'normal'):
                super().configure(state=s)
        return ()


class Tooltip:
    BG, FG = '#ffffe0', '#222'        # replaced with the window's own colours once the palette is known
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
        tk.Label(tip, text=self.text, background=Tooltip.BG, foreground=Tooltip.FG, relief='solid', bd=1,
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


class ScrollGrid(ttk.Frame):
    """A scrolling grid of square tiles that fills whatever width it is given.

    Both full-screen views need the same thing - every picture, and every prompt folder - so the grid is written once.
    What it adds over the fixed six-column list it replaces:

    * **columns from the live width**, and the tile *grows* to fill that width, so a maximised window shows big tiles
      instead of six small ones and a band of empty space;
    * **reflow on resize**, debounced, so dragging the window edge does not re-encode every thumbnail;
    * **the wheel scrolls the grid under the pointer**, bound on the tiles themselves because on Windows the wheel
      follows focus, not the pointer (the lesson from the strip, 2026-10-27);
    * **infinite scrolling**: `batch` tiles are laid out at a time and the next batch is appended at the bottom.
    """

    def __init__(self, master, make_tile, caption=None, batch=60, tile_min=STRIP_TILE_MIN, pad=6,
                 background=None, tile_target=180, muted=None, accent=None, sel_ring=True,
                 on_mark=None, on_activate=None, on_select_all=None):
        super().__init__(master)
        # colours come from the caller (the window's Theme), so a grid built by a test still works
        background = background or (ttk.Style().lookup('TFrame', 'background') or '#f4f4f5')
        self.muted = muted or (ttk.Style().lookup('TLabel', 'foreground') or '#555')
        self.accent = accent or (ttk.Style().lookup('TLabel', 'foreground') or '#0a84ff')
        self.background = background
        # Keyboard state. `focus_key` is the cell the arrow keys are on, and it is drawn as a ring around the whole cell
        # - label and caption - because a caption that swallows the keyboard would be invisible feedback. When the
        # owner keeps the selection itself (View All, whose Delete button reads `App.selected`) it passes `on_mark` and
        # `sel_ring=False`; otherwise the grid keeps its own chosen set. See `_mark`.
        self.focus_key = None
        self.chosen = set()
        self.anchor = None
        self.sel_ring = bool(sel_ring)
        self.on_mark = on_mark              # (key, 'set' | 'extend' | 'toggle') -> None, the owner applies it
        self.on_activate = on_activate      # (key) -> None, Enter
        self.on_select_all = on_select_all  # () -> None, Ctrl+A
        self.keys = []
        self.make_tile = make_tile       # (parent, item, size) -> Label, click already bound by the caller
        self.caption = caption           # (item) -> str or ''
        self.batch = batch
        self.tile_min = tile_min
        self.tile_target = tile_target   # what a tile *wants* to be; the grid grows tiles towards it before adding
        self.pad = pad
        self.items = []
        self.shown = 0
        self.tile = 0
        self.cols = 0
        self.cells = {}                  # item key -> the tile Label, so selection can be painted
        self._photos = {}                # (key, tile size) -> PhotoImage
        self._resize_job = None

        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=background)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self._on_bar)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.bar.grid(row=0, column=1, sticky='ns')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.bind('<Configure>', self._on_configure)
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        for w in (self.canvas, self.inner):
            w.bind('<MouseWheel>', self._wheel)
        self.bind('<Map>', lambda e: self.after(60, self._apply_resize))
        # The keys go on the canvas, not on a bind_all: a press in the search box or in a dialog must never move the
        # grid, and a widget-level binding cannot fire unless the grid itself holds the keyboard.
        self.canvas.configure(takefocus=1)
        grid_bind_moves(self.canvas, self._on_move)
        self.canvas.bind('<space>', lambda e: self._on_toggle())
        self.canvas.bind('<Return>', lambda e: self._on_enter())
        self.canvas.bind('<KP_Enter>', lambda e: self._on_enter())
        self.canvas.bind('<Control-a>', lambda e: self._on_all())

    # -- public
    def set_items(self, items):
        """items: [{'key', 'path', 'label', ...}] - the caller keeps whatever else it needs in the dict."""
        self.items = list(items)
        self.shown = min(self.batch, len(self.items))
        self.cells = {}
        self._photos = {}
        self._layout(rebuild=True)
        self.canvas.yview_moveto(0)
        # the first layout often runs before the window has its final width (a zoomed Toplevel reports its old size for
        # a frame or two), so ask again once the geometry settles
        self.after(80, self._apply_resize)

    def set_shown(self, n):
        if n != self.shown:
            self.shown = max(0, min(int(n), len(self.items)))
            self._layout(rebuild=True)

    def set_focus(self, key, scroll=False):
        """Park the keyboard cursor on one cell - how the folder browser follows the folder that is already open.

        Without it the ring would sit on "All pictures" while the right-hand grid showed some other folder, and the
        first arrow press would then start from the wrong place.
        """
        if key not in self.cells:
            return
        self.focus_key = key
        if self.on_mark is None:
            self.chosen = {key}
            self.anchor = key
        self._paint_focus()
        if scroll:
            self._see(key)

    # -- layout
    def _metrics(self):
        """Columns from the width, and a tile that grows to fill it.

        The first version derived the column count *and* the tile from `tile_min`, which converges: more width bought
        more columns, every column stayed at the 96 px floor, and a 1560 px window showed eight small tiles with a
        gutter. Column count now aims at `tile_target`, capped so a tile never falls below `tile_min`; the leftover
        width goes into the tile.
        """
        width = self.canvas.winfo_width()
        if width <= 1:
            width = self.winfo_toplevel().winfo_width() or 900
        widest = max(1, int((width - self.pad) // (self.tile_min + self.pad)))
        cols = min(widest, max(1, int(round(width / float(self.tile_target)))))
        tile = max(self.tile_min, (width - self.pad) // cols - self.pad)
        return cols, tile

    def _layout(self, rebuild=False):
        cols, tile = self._metrics()
        if not rebuild and (cols, tile) == (self.cols, self.tile):
            return
        self.cols, self.tile = cols, tile
        for w in self.inner.winfo_children():
            w.destroy()
        self.cells = {}
        self.keys = []
        for i, item in enumerate(self.items[:self.shown]):
            # a tk.Frame, not a ttk one, because only the classic widget draws a highlight ring - the ring is how the
            # grid says which cell the arrow keys are on, and it has to survive the owner's own selection painting
            cell = tk.Frame(self.inner, background=self.background, highlightthickness=0)
            cell.grid(row=i // cols, column=i % cols, padx=self.pad // 2, pady=self.pad // 2, sticky='n')
            lab = self.make_tile(cell, item, tile)
            lab.pack()
            self._bind_wheel(lab)
            self._bind_cell(lab, item['key'])
            text = self.caption(item) if self.caption else ''
            if text:
                cap = tk.Label(cell, text=text, font=('Segoe UI', 8), foreground=self.muted,
                               background=self.background, wraplength=max(80, tile - 8), justify='center')
                cap.pack()
                self._bind_wheel(cap)
                self._bind_cell(cap, item['key'])
            self.cells[item['key']] = lab
            self.keys.append(item['key'])
        # a rebuild keeps the cursor where it was, and puts it on the first cell when it has nowhere to be
        if self.focus_key not in self.cells:
            self.focus_key = self.keys[0] if self.keys else None
        self.canvas.itemconfigure(self._window, width=self.canvas.winfo_width())
        self.root_after_scrollregion()
        self._paint_focus()

    def root_after_scrollregion(self):
        box = self.inner.bbox('all')
        if box:
            self.canvas.configure(scrollregion=box)

    def _bind_wheel(self, widget):
        widget.bind('<MouseWheel>', self._wheel)

    # -- keyboard (W3C APG layout grid) ---------------------------------------
    def _bind_cell(self, widget, key):
        """A click anywhere in a cell moves the keyboard cursor there and gives the grid the keyboard.

        The owner's own click handler is already bound on the tile and still runs first - this one is added on top
        (`add='+'`), so a mouse click keeps its selection meaning and simply also tells the keyboard where it is.
        """
        widget.bind('<Button-1>', lambda e, k=key: self._clicked(k), add='+')

    def _clicked(self, key):
        """Pointer and keyboard agree after a click; nothing more, because the click's selection is the owner's call."""
        self.focus_key = key
        if self.on_mark is None:
            # the grid keeps its own selection here, so the click sets it the way a plain click elsewhere would
            self.chosen = {key}
            self.anchor = key
        self._paint_focus()

    def _on_move(self, event, move):
        """One movement key: move the cursor, mark the new cell, scroll it into view.

        Returns 'break' always when there is something to move over, because Tk's Canvas class binding for the arrow
        keys would otherwise scroll the viewport underneath the cursor.
        """
        if not self.keys:
            return None
        shift = bool(getattr(event, 'state', 0) & 0x0001)
        index = self.keys.index(self.focus_key) if self.focus_key in self.keys else -1
        new = grid_move(index, len(self.keys), self.cols, move, rows=self._visible_rows())
        if new is None:
            return None
        self.focus_key = self.keys[new]
        # page up/down is defined by where the visible set lands: the new current row becomes a leading visible row
        self._see(self.focus_key, 'top' if move in ('pageup', 'pagedown') else 'nearest')
        extending = shift and move not in ('home', 'end', 'all-home', 'all-end')
        self._mark(self.focus_key, 'extend' if extending else 'set')
        if new >= len(self.keys) - 1:
            self._maybe_more()          # at the last laid-out cell: pull the next batch in, as scrolling would
        return 'break'

    def _on_toggle(self):
        """Space toggles the current cell, the same gesture the strip uses."""
        if not self.keys:
            return None
        if self.focus_key not in self.keys:
            self.focus_key = self.keys[0]
        self._mark(self.focus_key, 'toggle')
        return 'break'

    def _on_enter(self):
        """Enter activates the current cell: load the picture, open the folder."""
        if not self.keys or self.on_activate is None:
            return None
        if self.focus_key not in self.keys:
            self.focus_key = self.keys[0]
        self.on_activate(self.focus_key)
        return 'break'

    def _on_all(self):
        """Ctrl+A takes everything that is laid out, which is what "visible" means in every list here."""
        if self.on_select_all is not None:
            self.on_select_all()
        elif self.sel_ring:
            self.chosen = set(self.keys)
            self.anchor = self.keys[0] if self.keys else None
            self._paint_focus()
        return 'break'

    def _mark(self, key, mode):
        """Apply one selection intent, to the owner when it owns the selection and locally when it does not."""
        if self.on_mark is not None:
            self.on_mark(key, mode)
            return
        if mode == 'toggle':
            if key in self.chosen:
                self.chosen.discard(key)
            else:
                self.chosen.add(key)
            self.anchor = key
        elif mode == 'extend' and self.anchor in self.keys and key in self.keys:
            i, j = self.keys.index(self.anchor), self.keys.index(key)
            i, j = min(i, j), max(i, j)
            self.chosen = set(self.keys[i:j + 1])
        else:
            self.chosen = {key}
            self.anchor = key
        self._paint_focus()

    def _paint_focus(self):
        """Ring the current cell (thicker) and the chosen cells (thinner), never rebuilding a thumbnail.

        The ring is on the cell frame so it surrounds the caption as well - the caption is part of the cell, and a
        keyboard cursor that stops at the picture is ambiguous when two tiles share a filename prefix.
        """
        for key, lab in self.cells.items():
            cell = lab.master
            current = (key == self.focus_key)
            chosen = self.sel_ring and key in self.chosen
            try:
                cell.configure(highlightthickness=3 if current else (2 if chosen else 0),
                               highlightbackground=self.accent)
            except Exception:
                pass

    def _visible_rows(self):
        """Rows the viewport shows, from the distance between the first cell and the one a row below it."""
        first = self.cells.get(self.keys[0]) if self.keys else None
        pitch = 0
        if first is not None and len(self.keys) > self.cols:
            below = self.cells.get(self.keys[self.cols])
            if below is not None:
                pitch = below.master.winfo_y() - first.master.winfo_y()
        if pitch <= 0 and first is not None:
            pitch = first.master.winfo_height()
        return grid_visible_rows(self.canvas.winfo_height(), pitch)

    def _see(self, key, align='nearest'):
        """Scroll so the cell under the keyboard is on screen - and never when it already is.

        `align='top'` is what page up/down needs: the APG puts the new current row among the first visible rows, so
        a whole visible set of movement shows up as a whole visible set of scroll. Everything else scrolls the least
        it can, which is what keeps the picture under the cursor from jumping around.
        """
        lab = self.cells.get(key)
        if lab is None:
            return
        cell = lab.master
        try:
            self.canvas.update_idletasks()
        except Exception:
            return
        total = max(self.inner.winfo_height(), 0)
        view_h = self.canvas.winfo_height()
        if total <= 0 or view_h <= 0:
            return
        top = cell.winfo_y()
        height = max(1, cell.winfo_height())
        view_top = self.canvas.canvasy(0)
        if align == 'top':
            target = top - self.pad
        elif top < view_top - 1:
            target = top - self.pad
        elif top + height > view_top + view_h + 1:
            target = top + height + self.pad - view_h
        else:
            return
        target = max(0.0, min(float(max(0, total - view_h)), float(target)))
        if abs(target - view_top) < 1.0:
            return
        self.canvas.yview_moveto(max(0.0, min(1.0, target / float(total))))

    # -- events
    def _on_configure(self, event=None):
        if self._resize_job:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(120, self._apply_resize)

    def _apply_resize(self):
        self._resize_job = None
        self._layout(rebuild=True)
        self.canvas.itemconfigure(self._window, width=self.canvas.winfo_width())

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), 'units')
        return 'break'

    def _on_bar(self, *args):
        self.canvas.yview(*args)
        self._maybe_more()

    def _on_scroll(self, first, last):
        self.bar.set(first, last)
        if float(last) >= 0.995:
            self._maybe_more()

    def _maybe_more(self):
        if self.shown >= len(self.items):
            return
        self.set_shown(self.shown + self.batch)


class App:
    def __init__(self, root, engine, args):
        self.root, self.engine, self.args = root, engine, args
        self.busy = True
        self._img = None
        self._raw = None
        self._tips = []                   # populated as widgets appear, not in one later pass
        self._poll_id = None
        self.ui_events = queue.Queue()    # results from worker threads (the prompt enhancer)
        self._settings = load_settings()
        self._adv_shown = bool(self._settings.get('advanced') or False)
        self._base_img = None
        self._split = 0.5
        self._runs = list(self._settings.get('runs') or [])
        self._favorites = set(self._settings.get('favorites') or [])
        self.search_var = tk.StringVar(value='')
        self.starred_only = tk.BooleanVar(value=False)
        self.run_started_at = None
        self._pending_hires = 0.0
        self._preview = False
        self._custom_styles = dict(self._settings.get('custom_styles') or {})
        self._llm_ok = False
        self._browser_win = None     # the single View All window
        self._meta_cache = {}        # name -> (mtime, searchable text), rebuilt as files change
        self._search_after = None    # debounce id, so a keystroke does not re-filter the folder
        self._mask = None            # PIL 'L' at the picture's own size
        self._mask_painting = False
        self._brush = 40
        self._pads = [0, 0, 0, 0]
        self._pinned = None          # a second picture to compare against, independent of the base
        self._sidebar = self._settings.get('sidebar', True)
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
        # the platform icon masters are vendored in assets/platform-icons (see its USAGE.md): use the Windows master's
        # 1024 px preview as the window icon, and default=True so every dialog inherits it
        try:
            _icon = Path(__file__).resolve().parent / 'assets' / 'platform-icons' / 'previews' / 'windows-gradient.png'
            if _icon.is_file():
                self._icon_image = tk.PhotoImage(file=str(_icon))
                root.iconphoto(True, self._icon_image)
        except Exception:
            pass                       # an icon is decoration: never let it stop the window opening
        # the host's colours, read before any widget is built so nothing is painted twice
        self.theme = Theme(root)
        self.theme.apply()
        Tooltip.BG, Tooltip.FG = self.theme.tooltip_bg, self.theme.text
        try:
            root.state('zoomed')   # fill the screen: prompt left, picture right
        except Exception:
            root.geometry('1180x720')
        root.after(150, self._ensure_maximized)
        # gallery + prompt column + picture: the controls need ~1200 before the picture column can hold its buttons
        root.minsize(1200, 700)
        root.columnconfigure(0, weight=0, minsize=210)
        root.columnconfigure(1, weight=0, minsize=420)
        root.columnconfigure(2, weight=1)
        root.rowconfigure(0, weight=1)

        # ---- left: prompt + controls
        # ---- far left: the most recent images as thumbnails
        plate = ttk.Style().lookup('TFrame', 'background') or self.theme.surface
        gal = ttk.Frame(root, padding=(10, 10, 4, 10))
        gal.grid(row=0, column=0, sticky='nsew')
        self.gal = gal
        gal.columnconfigure(0, weight=1)
        self.group_var = tk.BooleanVar(value=bool(self._settings.get('folders', True)))
        self._plan = None                 # the grouping plan, when computed
        self._active_folder = None        # index into self._plan['groups'], or None for everything
        self._grouping = False
        self.folder_bar = ttk.Frame(gal)
        self.folder_bar.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        self.folder_bar.grid_remove()
        # title and the folders switch share one row *and one column*, so the strip cannot gain a third column and
        # steal width from the picture pane (it did, by 70 px, and squeezed the action bar)
        self.title_row = ttk.Frame(gal)
        self.title_row.grid(row=0, column=0, columnspan=2, sticky='ew')
        self.gal_title = ttk.Label(self.title_row, text='Recent (%d)' % RECENT_N)
        self.gal_title.pack(side='left')
        self.search_box = ttk.Entry(gal, textvariable=self.search_var, width=16, font=('Segoe UI', 10))
        self._tip(self.search_box, 'Search file names, prompts, styles, models, seeds and steps.')
        self.search_box.grid(row=1, column=0, sticky='ew', pady=(3, 0))
        self.search_box.bind('<KeyRelease>', self._on_search_typing)
        self.starred_chk = ttk.Checkbutton(gal, text='starred only', variable=self.starred_only,
                                           command=self.refresh_gallery, style='Big.TCheckbutton')
        self.starred_chk.grid(row=1, column=1, sticky='w', pady=(3, 0))

        self.group_chk = ttk.Checkbutton(self.title_row, text='folders', variable=self.group_var,
                                         command=self.toggle_groups, style='Big.TCheckbutton')
        self.group_chk.pack(side='left', padx=(8, 0))
        # Everything above the strip except this first line folds away: it is configuration, not the work. Folding it by
        # default is what pays for the bigger controls inside it.
        self._options_shown = False
        self.options_btn = ttk.Button(self.title_row, text='\u25b8 options', width=11,
                                      command=self.toggle_options, style='Big.TButton')
        self.options_btn.pack(side='right')
        # the strip scrolls: 20 tiles at 92 px need ~500 px, which is more than a small window has
        holder = self.strip_holder = ttk.Frame(gal)
        holder.grid(row=4, column=0, columnspan=2, sticky='nsew', pady=(4, 6))
        # 'grouping prompts...' is a placeholder for the whole area, so it is placed in the viewport and centred in it -
        # in the content flow it sat in the top-left corner of a large empty column, which read as a caption, not a state
        self.grouping_badge = ttk.Label(holder, text='grouping prompts\u2026', foreground=self.theme.muted,
                                        font=('Segoe UI', 18), anchor='center', justify='center')
        # the open folder's pictures get their own pane beside the folder list, so the list never has to disappear
        self.contents_holder = ttk.Frame(gal)
        self.contents_holder.grid(row=4, column=2, sticky='nsew', pady=(4, 6), padx=(6, 0))
        # the pane is a second column of pictures: give it the column's spare width instead of a fixed 236 px
        gal.columnconfigure(2, weight=1)
        # The folder's own name at the top of its column. The pane had no identity at all: a folder was recognisable
        # only by the truncated label back in the first column, so nothing on screen said which folder you were in.
        self.contents_title = ttk.Label(self.contents_holder, text='', anchor='w', justify='left',
                                        wraplength=CONTENTS_W)
        self.contents_title.grid(row=0, column=0, columnspan=2, sticky='we', pady=(0, 4))
        self.contents_canvas = tk.Canvas(self.contents_holder, highlightthickness=0, borderwidth=0,
                                         background=plate, width=CONTENTS_W)
        self.contents_canvas.grid(row=1, column=0, sticky='nsew')
        self.contents_bar = ttk.Scrollbar(self.contents_holder, orient='vertical',
                                          command=self.contents_canvas.yview)
        self.contents_bar.grid(row=1, column=1, sticky='ns')
        # the pane must stretch with the row, not sit at the canvas's requested 7 cm height (the folder list did, and
        # the pictures were clipped to a two-row band under a much taller column)
        self.contents_holder.rowconfigure(1, weight=1)
        self.contents_holder.columnconfigure(0, weight=1)
        self.contents_canvas.configure(yscrollcommand=self.contents_bar.set)
        self.contents_canvas.configure(takefocus=1)
        for seq, fn in (('<Left>', lambda e: self._pane_key(e, 'left')), ('<Right>', lambda e: self._pane_key(e, 'right')),
                        ('<Up>', lambda e: self._pane_key(e, 'up')), ('<Down>', lambda e: self._pane_key(e, 'down')),
                        ('<Prior>', lambda e: self._pane_key(e, 'pageup')), ('<Next>', lambda e: self._pane_key(e, 'pagedown')),
                        ('<Home>', lambda e: self._pane_key(e, 'home')), ('<End>', lambda e: self._pane_key(e, 'end')),
                        ('<Control-Home>', lambda e: self._pane_key(e, 'all-home')),
                        ('<Control-End>', lambda e: self._pane_key(e, 'all-end')),
                        ('<space>', self._pane_toggle), ('<Return>', self._pane_open),
                        ('<Control-a>', self._pane_all)):
            self.contents_canvas.bind(seq, fn)
        self._contents_cols_seen = None
        self.contents = ttk.Frame(self.contents_canvas)
        self._contents_item = self.contents_canvas.create_window((0, 0), window=self.contents, anchor='nw')
        self.contents.bind('<Configure>', self._on_contents_resize)
        self.contents_canvas.bind('<Configure>', self._on_contents_resize)
        self.contents_holder.grid_remove()
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
        self.root.bind_all('<MouseWheel>', self._on_any_wheel, add='+')
        self.gal_bar.configure(command=self._on_strip_scrollbar)
        self.gal_canvas.configure(takefocus=1)
        # The whole layout-grid table, from the one list the three views share: arrows wrap across rows and clamp
        # vertically, page up/down move a visible set, home/end are the row and ctrl+home/end the list.
        grid_bind_moves(self.gal_canvas, self._nav_move_key)
        for seq, fn in (('<space>', self._nav_toggle_key), ('<Return>', self._nav_open_key)):
            self.gal_canvas.bind(seq, fn)
        for c in range(RECENT_COLS + 12):
            self.recent.columnconfigure(c, weight=0)
        self._shown = RECENT_N            # how many strip pictures are on screen (grows as you scroll)
        self._folder_shown = BATCH_FOLDERS
        self._content_shown = BATCH_CONTENTS
        self._sheets = {}                 # memo for composed contact sheets, keyed by the pictures in them
        self._hashes = {}                 # path -> 64-bit difference hash of its thumbnail (None when unreadable)
        self._heads = {}                  # folder -> the four pictures its tile shows
        self.sheets_all_alike = 0         # folders that could not produce four visibly different pictures
        self._thumbs = {}                 # path -> PhotoImage, kept alive
        self._tile_widgets = {}           # path -> Label, so selection can be painted without a rebuild
        self._all_widgets = {}            # same, for the "More..." window
        self.selected = []                # ordered list of paths marked for action
        self._anchor = None               # shift-click range starts here
        self._nav_cur = None              # the cell the arrow keys are on: a Path in the strip, a folder index in folders

        btns = self.btns = ttk.Frame(gal)
        btns.grid(row=3, column=0, columnspan=2, sticky='ew')     # above the strip: buttons are never clipped
        # Four rows instead of eight full-width ones. The column is barely two folder tiles wide, so one stacked button
        # per action cost ~180 px of picture height for no information; pairing them costs half of that.
        toprow = ttk.Frame(btns)
        toprow.pack(fill='x')
        self.more_btn = ttk.Button(toprow, text='View all pictures\u2026', command=self.open_all, style='Big.TButton')
        self.more_btn.pack(side='left', fill='x', expand=True)
        self.folders_btn = ttk.Button(toprow, text='Folders \u25b8', command=self._folders_clicked, style='Big.TButton')
        self.folders_btn.pack(side='left', fill='x', expand=True, padx=(3, 0))

        toolrow = ttk.Frame(btns)
        toolrow.pack(fill='x', pady=(3, 0))
        self.refresh_btn = ttk.Button(toolrow, text='\u21bb', width=3, command=self.refresh_gallery,
                                      style='Big.TButton')
        self.refresh_btn.pack(side='left')
        self.log_btn = ttk.Button(toolrow, text='Session log\u2026', command=self.show_run_log, style='Big.TButton')
        self.log_btn.pack(side='left', fill='x', expand=True, padx=(3, 0))
        self.folder_btn = ttk.Button(toolrow, text='Open pictures folder\u2026', command=self.open_folder,
                                     style='Big.TButton')
        self.folder_btn.pack(side='left', fill='x', expand=True)
        self.collapse_btn = ttk.Button(toolrow, text='\u00ab', width=3, command=lambda: self.set_sidebar(False),
                                       style='Big.TButton')
        self.collapse_btn.pack(side='left', padx=(3, 0))

        selrow = ttk.Frame(btns)
        selrow.pack(fill='x', pady=(3, 0))
        ttk.Label(selrow, text='sort by', foreground=self.theme.muted, font=('Segoe UI', 10)).pack(side='left')
        # wins travel with the picture, so "Wins" is a stable order across folders and restarts
        self.sort_var = tk.StringVar(value='Recent')
        # Sequence reads a storyboard in film order (the scene numbers a multi-scene prompt records in its sidecars);
        # it falls back to recency for pictures that have none, so an ordinary gallery is unchanged
        self.sort_cb = ttk.Combobox(selrow, state='readonly', width=8, values=['Recent', 'Wins', 'Sequence'],
                                    textvariable=self.sort_var, style='Big.TCombobox')
        self.sort_cb.pack(side='left', padx=(3, 0))
        self.sort_cb.bind('<<ComboboxSelected>>', lambda e: self._resort())
        self.star_btn = ttk.Button(selrow, text='\u2605', width=3, command=self.toggle_star, style='Big.TButton')
        self.star_btn.pack(side='left', padx=(3, 0))
        self.sel_all_btn = ttk.Button(selrow, text='Select all', width=9, command=self.select_all, style='Big.TButton')
        self.sel_all_btn.pack(side='left', fill='x', expand=True, padx=(3, 0))
        self.del_btn = ttk.Button(selrow, text='Delete\u2026', command=self.delete_selected, state='disabled',
                                  style='Big.TButton')
        self.del_btn.pack(side='left', fill='x', expand=True, padx=(3, 0))

        self.sel_label = ttk.Label(btns, text='', foreground=self.theme.accent)
        self.sel_label.pack(anchor='w', pady=(2, 0))
        self.hint_label = ttk.Label(btns, text='Ctrl+click adds to the selection \u00b7 Shift+click selects a range\n'
                                               'Delete sends pictures to the Recycle Bin \u00b7 Ctrl+A selects all',
                                    foreground=self.theme.muted, font=('Segoe UI', 9), justify='left')
        self.hint_label.pack(anchor='w')
        gal.rowconfigure(3, weight=0)   # the buttons, fixed height
        gal.rowconfigure(4, weight=1)   # the strip takes whatever is left
        self.toggle_options(False)      # the column opens folded: the strip gets the room, not the configuration
        self._strip_cols_seen = None

        self.rail = ttk.Frame(root, padding=(4, 12, 0, 10))
        self.rail.grid(row=0, column=0, sticky='ns')
        self.rail_btn = ttk.Button(self.rail, text='\u00bb', width=2, command=lambda: self.set_sidebar(True))
        self.rail_btn.pack(anchor='n')
        self.rail.grid_remove()

        left = ttk.Frame(root, padding=(10, 10, 6, 10))
        left.grid(row=0, column=1, sticky='nsew')
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        ttk.Label(left, text='Prompt  (Enter generates · Shift+Enter adds a line · Ctrl+\u2191/\u2193 recalls)'
                  ).grid(row=0, column=0, sticky='w')
        # a tall minimum height here pushes the rows below it off the bottom of the window once Advanced opens
        self.text = tk.Text(left, wrap='word', width=48, height=7, undo=True, font=('Segoe UI', 10),
                            background=self.theme.field, foreground=self.theme.text,
                            insertbackground=self.theme.text, selectbackground=self.theme.accent,
                            selectforeground=self.theme.on_accent, highlightthickness=1,
                            highlightbackground=self.theme.border, relief='flat')
        self.text.grid(row=1, column=0, sticky='nsew', pady=(4, 6))
        # the medium words the style guard lights up: a muted amber, so it reads as "look here", not as an error
        self.text.tag_configure('conflict', background='#6b4a12', foreground='#ffffff')
        self.text.bind('<Return>', self._on_return)
        self.text.bind('<Shift-Return>', self._on_shift_return)
        # Ctrl+arrow, not plain arrows: in a multi-line prompt box Up/Down have to move the caret, which is what
        # every text editor does and what a five-line prompt needs
        self.text.bind('<Control-Up>', self._on_hist_up)
        self.text.bind('<Control-Down>', self._on_hist_down)
        self.text.focus_set()

        row = ttk.Frame(left)
        row.grid(row=2, column=0, sticky='ew')
        row.columnconfigure(3, weight=1)
        # Generate itself now lives at the bottom of the column; this row holds the modifiers and second actions
        rephrase_row = ttk.Frame(row)
        rephrase_row.grid(row=0, column=0, columnspan=6, sticky='w', pady=(2, 2))
        self.rephrase_btn = ttk.Button(rephrase_row, text='\u21bb rephrase', command=self.rephrase)
        self.rephrase_btn.pack(side='left')
        # intensity: how many of the matched words move on one press. matching: how forgiving the lookup is.
        self.rephrase_intensity = tk.StringVar(value=self._settings.get('rephrase_intensity') or 'normal')
        ttk.Combobox(rephrase_row, textvariable=self.rephrase_intensity, width=7, state='readonly',
                     values=['light', 'normal', 'strong']).pack(side='left', padx=(8, 0))
        ttk.Label(rephrase_row, text='matching').pack(side='left', padx=(10, 4))
        self.rephrase_matching = tk.StringVar(value=self._settings.get('rephrase_matching') or 'strict')
        ttk.Combobox(rephrase_row, textvariable=self.rephrase_matching, width=6, state='readonly',
                     values=['off', 'strict', 'loose']).pack(side='left')

        self.stop_btn = ttk.Button(row, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.grid(row=1, column=0, sticky='w')
        self.retry_btn = ttk.Button(row, text='Retry', command=self.retry)
        self.retry_btn.grid(row=1, column=0, sticky='w')
        self.retry_btn.grid_remove()
        self.preview_btn = ttk.Button(row, text='Preview', command=self.preview)
        self.preview_btn.grid(row=1, column=1, sticky='w', padx=(6, 0))
        ttk.Label(row, text='size').grid(row=1, column=2, padx=(12, 4))
        self.size_var = tk.StringVar(value=str(self._settings.get('size') or args.size))
        self.size_cb = ttk.Combobox(row, textvariable=self.size_var, width=6, state='readonly',
                                    values=['256', '320', '384', '448', '512', '640', '704', '768', '1024'])
        self.size_cb.grid(row=1, column=3, sticky='w')
        ttk.Label(row, text='layout').grid(row=1, column=4, padx=(10, 4))
        self.layout_var = tk.StringVar(value=self._settings.get('layout') or 'single')
        self.layout_cb = ttk.Combobox(row, textvariable=self.layout_var, width=9, state='readonly',
                                      values=['single', '4-up (2x2)', '9-up (3x3)', '16-up (4x4)'])
        self.layout_cb.grid(row=1, column=5, sticky='w')

        # one at a time by default: an N-up is N separate requests with seeds seed..seed+N-1, so every tile is
        # individually reproducible and the VRAM peak stays that of a single image
        self.seq_var = tk.BooleanVar(value=True)
        self.seq_chk = ttk.Checkbutton(row, text='one image at a time', variable=self.seq_var)
        self.seq_chk.grid(row=2, column=0, columnspan=3, sticky='w', pady=(4, 0))
        # off by default: a generation is text to image unless Use as Base armed an image
        self.chain_var = tk.BooleanVar(value=False)
        self.chain_chk = ttk.Checkbutton(row, text='chain: each new image becomes the next base',
                                         variable=self.chain_var)
        self.chain_chk.grid(row=3, column=0, columnspan=3, sticky='w', pady=(2, 0))
        # off by default: after a picture the seed steps on by one, which is reproducible and visible. Ticked, the
        # seed is left exactly as it is, for when the same seed is wanted again on purpose.
        self.hold_seed_var = tk.BooleanVar(value=False)
        self.hold_seed_chk = ttk.Checkbutton(row, text='hold seed', variable=self.hold_seed_var,
                                             command=self._update_banner)
        self.hold_seed_chk.grid(row=3, column=3, columnspan=3, sticky='w', pady=(2, 0))
        seedbox = ttk.Frame(row)
        seedbox.grid(row=2, column=3, columnspan=3, sticky='w', pady=(4, 0))
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

        stylerow = ttk.Frame(row)
        stylerow.grid(row=4, column=0, columnspan=5, sticky='w', pady=(6, 0))
        ttk.Label(stylerow, text='style').pack(side='left', padx=(0, 4))
        self.style_var = tk.StringVar(value=self._settings.get('style') or 'none')
        self.style_cb = ttk.Combobox(stylerow, textvariable=self.style_var, width=12, state='readonly',
                                     values=list(STYLES) + sorted(self._custom_styles))
        self.style_cb.pack(side='left')
        self.save_style_btn = ttk.Button(stylerow, text='save style\u2026', command=self.save_style)
        self.save_style_btn.pack(side='left', padx=(4, 0))
        # the checkpoint is a first-class choice, not an Advanced secret: picking one costs a model reload
        ttk.Label(stylerow, text='model').pack(side='left', padx=(10, 4))
        self.model_var = tk.StringVar(value=self._settings.get('model') or zg.DEFAULT_MODEL)
        self.model_cb = ttk.Combobox(stylerow, textvariable=self.model_var, width=14, state='readonly',
                                     values=[name for name, prof in zg.MODELS.items()
                                             if prof.get('in_picker', True)] + [zg.ALL_MODEL])
        self.model_cb.pack(side='left')
        self.model_cb.bind('<<ComboboxSelected>>', self._on_model_change)
        self.adv_btn = ttk.Button(stylerow, text='Advanced \u25b8', command=self.toggle_advanced)
        self.adv_btn.pack(side='left', padx=(12, 0))

        self.base_row = ttk.Frame(left)
        self.base_row.grid(row=8, column=0, sticky='ew', pady=(8, 0))
        ttk.Label(self.base_row, text='base:').pack(side='left')
        # a fixed character width, so a long file name cannot change the column's width
        self.base_label = ttk.Label(self.base_row, text='(none - text to image)', foreground=self.theme.muted,
                                    width=24, anchor='w')
        self._base_tip = None
        self.base_label.pack(side='left', padx=(4, 10))
        ttk.Label(self.base_row, text='strength').pack(side='left')
        # 0.70 is the measured pose threshold for flux2 klein; below it an instruction changes the props, not the pose
        self.denoise = tk.DoubleVar(value=float(self._settings.get('denoise') or 0.70))
        self.strength_scale = ttk.Scale(self.base_row, from_=0.2, to=0.95, variable=self.denoise, length=130)
        self.strength_scale.pack(side='left', padx=4)
        # The readout must be *derived* from the variable. It was created with the literal string '0.60' and only
        # refreshed by a trace, and a trace fires on changes: nothing changed at startup, so the label said 0.60 while
        # Generate used the real value (0.70) - the one number a user reads before pressing a button that spends a minute.
        self.denoise_text = ttk.Label(self.base_row, text='%.2f' % self.denoise.get())
        self.denoise_text.pack(side='left')
        self.denoise.trace_add('write', lambda *a: self.denoise_text.configure(text='%.2f' % self.denoise.get()))

        # the two base-image actions share one row directly above Generate: use the shown picture, or forget it.
        # The deliberate gesture that arms an edit lives here, so a click in the strip only ever looks.
        self.base_actions = ttk.Frame(left)
        self.base_actions.grid(row=10, column=0, sticky='ew', pady=(10, 4))
        self.base_actions.columnconfigure(0, weight=1)
        self.base_actions.columnconfigure(1, weight=1)
        self.use_base_btn = ActionButton(self.base_actions, text='Use as Base', command=self.use_as_base,
                                         font=('Segoe UI', 13), bg='#5c6b80', fg='white',
                                         activebackground='#4a5768', activeforeground='white',
                                         disabledforeground='#d5dae2', relief='flat', bd=0,
                                         height=2, cursor='hand2', takefocus=True)
        self.use_base_btn.grid(row=0, column=0, sticky='ew', padx=(0, 3))
        self.use_base_btn.state(['disabled'])

        self.clear_base_btn = ActionButton(self.base_actions, text='Clear Base', command=self.clear_base,
                                           font=('Segoe UI', 13), bg='#ff6347', fg='white',
                                           activebackground='#e0492f', activeforeground='white',
                                           disabledforeground='#ffe0d8', relief='flat', bd=0,
                                           height=2, cursor='hand2', takefocus=True)
        self.clear_base_btn.grid(row=0, column=1, sticky='ew', padx=(3, 0))
        self.clear_base_btn.state(['disabled'])

        self.clip_row = ttk.Frame(left)
        self.clip_row.grid(row=13, column=0, sticky='ew', pady=(6, 0))
        self.clip_row.columnconfigure(0, weight=3)
        self.clip_row.columnconfigure(1, weight=2)
        self.motion_var = tk.StringVar(value=str(self._settings.get('motion') or DEFAULT_MOTION))
        self.motion_entry = ttk.Entry(self.clip_row, textvariable=self.motion_var)
        ttk.Label(self.clip_row, text='motion', foreground=self.theme.muted).grid(row=0, column=0, sticky='w')
        self.motion_entry.grid(row=0, column=1, sticky='ew', padx=(3, 3))
        self._tip(self.motion_entry, 'Motion for a clip only: what should happen between the frames. '
                                     'The prompt above describes the still and is not sent with a clip.')
        self.clip_row.columnconfigure(1, weight=1)
        self._last_clip = None

        self.gen_btn = ActionButton(left, text='\u26a1 Generate', command=self.generate,
                                    font=('Segoe UI', 13, 'bold'), bg='#1f8a45', fg='white',
                                    activebackground='#166b34', activeforeground='white',
                                    disabledforeground='#c9e6d2', relief='flat', bd=0,
                                    height=2, cursor='hand2', takefocus=True)
        self.gen_btn.grid(row=11, column=0, sticky='ew', pady=(0, 0))
        self.last_label = ttk.Label(left, text='', foreground=self.theme.muted, anchor='center')
        self.last_label.grid(row=12, column=0, sticky='ew', pady=(6, 0))

        self.advanced = ttk.Frame(left)
        self.advanced.grid(row=9, column=0, sticky='ew', pady=(4, 0))
        adv1 = ttk.Frame(self.advanced)
        adv1.pack(fill='x')

        adv_step = ttk.Frame(self.advanced)
        adv_step.pack(fill='x', pady=(4, 0))
        ttk.Label(adv_step, text='seed step').pack(side='left')
        self.seed_step_var = tk.IntVar(value=int(self._settings.get('seed_step') or 1))
        self.seed_step = ttk.Spinbox(adv_step, from_=1, to=100000, increment=1, width=8,
                                     textvariable=self.seed_step_var)
        self.seed_step.pack(side='left', padx=(4, 4))
        ttk.Label(adv_step, text='added per tile: 1 = seed, seed+1, seed+2 ...',
                  foreground=self.theme.muted).pack(side='left')

        adv_sweep = ttk.Frame(self.advanced)
        adv_sweep.pack(fill='x', pady=(4, 0))
        ttk.Label(adv_sweep, text='sweep').pack(side='left')
        self.sweep_var = tk.StringVar(value='none')
        self.sweep_cb = ttk.Combobox(adv_sweep, textvariable=self.sweep_var, width=8, state='readonly',
                                     values=['none', 'cfg', 'steps', 'seed'])
        self.sweep_cb.pack(side='left', padx=(4, 4))
        self.sweep_values = tk.StringVar(value='')
        ttk.Entry(adv_sweep, textvariable=self.sweep_values, width=14).pack(side='left')
        ttk.Label(adv_sweep, text='e.g. 1,2,3 or 1..4x6 (blank = spread over the tiles)',
                  foreground=self.theme.muted).pack(side='left', padx=(4, 0))
        ttk.Label(adv1, text='steps').pack(side='left')
        self.steps_var = tk.StringVar(value=str(self._settings.get('steps') or self.args.steps))
        self.steps_cb = ttk.Combobox(adv1, textvariable=self.steps_var, width=4, state='readonly',
                                     values=['2', '4', '6', '8', '9', '12', '20', '30', '50'])
        self.steps_cb.pack(side='left', padx=4)
        ttk.Label(adv1, text='sampler').pack(side='left', padx=(12, 4))
        self.sampler_var = tk.StringVar(value=self._settings.get('sampler') or 'euler')
        self.sampler_cb = ttk.Combobox(adv1, textvariable=self.sampler_var, width=15, state='readonly',
                                       values=['euler', 'euler_ancestral'])
        self.sampler_cb.pack(side='left')
        adv2 = ttk.Frame(self.advanced)
        adv2.pack(fill='x', pady=(4, 0))
        ttk.Label(adv2, text='negative').pack(side='left', anchor='n')
        self.negative = tk.Text(adv2, height=2, width=42, wrap='word', font=('Segoe UI', 9),
                                background=self.theme.field, foreground=self.theme.text,
                                insertbackground=self.theme.text, relief='flat', highlightthickness=1,
                                highlightbackground=self.theme.border)
        self.negative.pack(side='left', padx=(4, 0), fill='x', expand=True)
        if not (self._settings.get('advanced') or False):
            self.advanced.grid_remove()

        # ---- middle: progress (sits under the button, above the status line)
        # the banner answers the only question that matters before pressing Enter
        # wraplength caps the requested width, so a long file name can never widen this column
        self.banner = ttk.Label(left, text='', font=('Segoe UI', 10, 'bold'), foreground=self.theme.text,
                                wraplength=396, justify='left')
        self.banner.grid(row=3, column=0, sticky='w', pady=(10, 0))
        self._tip(self.banner, 'What the next Generate will do, with the base file name in full.')
        self.ticks = ttk.Label(left, text='', foreground=self.theme.accent, font=('Consolas', 10))
        self.ticks.grid(row=4, column=0, sticky='w')

        self.bar = ttk.Progressbar(left, mode='determinate', maximum=1.0)
        self.bar.grid(row=5, column=0, sticky='ew', pady=(4, 2))
        self.bar.grid_remove()
        self.status = ttk.Label(left, text='starting…', foreground=self.theme.muted)
        self.status.grid(row=6, column=0, sticky='w')
        self.hint = ttk.Label(left, text='', foreground=self.theme.muted, wraplength=430, justify='left',
                              font=('Segoe UI', 9))
        self.hint.grid(row=7, column=0, sticky='w', pady=(4, 0))

        self.starters = ttk.Frame(left)
        self.starters.grid(row=7, column=0, sticky='w', pady=(4, 0))
        ttk.Label(self.starters, text='Nothing generated yet. Try one of these:',
                  foreground=self.theme.muted).pack(anchor='w')
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
        self.canvas = tk.Canvas(right, background=self.theme.window, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')

        self.canvas.bind('<Configure>', lambda e: self._redraw())
        self.canvas.bind('<Button-1>', self._on_canvas_click)
        self.canvas.bind('<B1-Motion>', self._on_crop_drag, add='+')
        self.canvas.bind('<ButtonRelease-1>', self._on_crop_release)
        # what the picture is, directly under it in the same column: pixels, bytes, seed, steps, then model and date.
        # 'always' means always - it sits under the picture, not tucked at the end of a row of buttons
        self.res_label = ttk.Label(right, text='', foreground=self.theme.muted, font=('Segoe UI', 8),
                                   anchor='e', justify='right')
        self.res_label.grid(row=1, column=0, sticky='e', pady=(2, 0))
        tools = ttk.Frame(right)
        tools.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        self.copy_btn = ttk.Button(tools, text='Copy image', command=self.copy_image, state='disabled')
        self.copy_btn.pack(side='left')
        self.info_btn = ttk.Button(tools, text='\u24d8 metadata\u2026', command=self.show_metadata, state='disabled')
        self.info_btn.pack(side='left', padx=(6, 0))
        self.graph_btn = ttk.Button(tools, text='graph\u2026', command=self.show_graph)
        self.graph_btn.pack(side='left', padx=(6, 0))
        self.describe_btn = ttk.Button(tools, text='Describe', command=self.describe, state='disabled')
        self.describe_btn.pack(side='left', padx=(6, 0))
        self.copy_status = ttk.Label(tools, text='', foreground=self.theme.muted)
        self.copy_status.pack(side='left', padx=(8, 0))
        # two rows: the picture column is narrow, and one row of six controls was clipped at 1180 px
        # the clip controls sit at the bottom of the picture column, where the picture they act on is: Make clip first,
        # then Open clip - the order the work happens in
        self.clip_actions = ttk.Frame(right)
        self.clip_actions.grid(row=4, column=0, sticky='ew', pady=(6, 0))
        self.clip_btn = ActionButton(self.clip_actions, text='\u25b6 Make clip (2 s)', command=self.make_clip,
                                     font=('Segoe UI', 11), bg='#3f4f63', fg='white',
                                     activebackground='#33404f', activeforeground='white',
                                     disabledforeground='#c9d2de', relief='flat', bd=0,
                                     height=1, cursor='hand2', takefocus=True)
        self.clip_btn.pack(side='left', fill='x', expand=True)
        self.clip_open_btn = ActionButton(self.clip_actions, text='Open clip\u2026', command=self.open_last_clip,
                                          font=('Segoe UI', 11), bg='#7a8699', fg='white',
                                          activebackground='#68748a', activeforeground='white',
                                          disabledforeground='#d5dae2', relief='flat', bd=0,
                                          height=1, cursor='hand2', takefocus=True)
        self.clip_open_btn.pack(side='left', fill='x', expand=True, padx=(6, 0))
        self.clip_open_btn.state(['disabled'])

        self.actions = ttk.Frame(right)
        self.actions.grid(row=3, column=0, sticky='ew', pady=(6, 0))
        act1 = ttk.Frame(self.actions)
        act1.pack(fill='x')
        self.vary_btn = ttk.Button(act1, text='Vary', command=lambda: self.vary('vary'), state='disabled')
        self.vary_btn.pack(side='left')
        self.vary_soft_btn = ttk.Menubutton(act1, text='Vary\u2026', state='disabled')
        vary_menu = tk.Menu(self.vary_soft_btn, tearoff=False)
        # Measured, not guessed: at 9 steps the sampler quantises strength, and 10 renders across 0.40-0.95 produced only
        # 7 distinct pictures (0.78 == 0.80; 0.84 == 0.87 == 0.90). These five sit on distinct levels and are labelled by
        # what they measured as a share of an unrelated picture's difference - see VARY-STRENGTH-2026-10-28.md.
        for label, strength in (('subtle (0.40)', 0.40), ('partial (0.55)', 0.55), ('real change (0.72)', 0.72),
                                ('new picture (0.87)', 0.87), ('different image (0.95)', 0.95)):
            vary_menu.add_command(label=label, command=lambda s=strength: self.vary('base', s))
        self.vary_soft_btn.configure(menu=vary_menu)
        self.vary_soft_btn.pack(side='left', padx=(4, 0))
        self.upscale_btn = ttk.Button(act1, text='hires \u00d72', command=self.hires, state='disabled')
        self.upscale_btn.pack(side='left', padx=(4, 0))
        # a reference picture from outside the folder: bitmap on the clipboard, or a copied file / path
        self.paste_btn = ttk.Button(act1, text='Paste image', command=self.paste_base)
        self.paste_btn.pack(side='left', padx=(4, 0))
        act2 = ttk.Frame(self.actions)
        act2.pack(fill='x', pady=(4, 0))
        self.mask_var = tk.BooleanVar(value=False)
        self.mask_chk = ttk.Checkbutton(act2, text='paint mask', variable=self.mask_var,
                                        command=self.toggle_mask_mode)
        self.mask_chk.pack(side='left')
        ttk.Label(act2, text='brush').pack(side='left', padx=(6, 2))
        self.brush_var = tk.IntVar(value=40)
        ttk.Spinbox(act2, from_=8, to=200, increment=8, width=5, textvariable=self.brush_var,
                    command=self._read_brush).pack(side='left')
        self.clear_mask_btn = ttk.Button(act2, text='clear', command=self.clear_mask, state='disabled')
        self.clear_mask_btn.pack(side='left', padx=(4, 0))
        # crop: drag a rectangle on the picture and that rectangle becomes the base for the next run
        self.crop_var = tk.BooleanVar(value=False)
        self.crop_chk = ttk.Checkbutton(act2, text='\u2702 crop', variable=self.crop_var,
                                        command=self.toggle_crop_mode)
        self.crop_chk.pack(side='left', padx=(6, 0))
        self._crop_start = None
        self._crop_item = None

        act3 = ttk.Frame(self.actions)
        act3.pack(fill='x', pady=(4, 0))
        ttk.Label(act3, text='expand').pack(side='left')
        for label, idx in ((u'\u25c0', 0), (u'\u25b2', 1), (u'\u25b6', 2), (u'\u25bc', 3)):
            b = ttk.Button(act3, text=label, width=2, command=lambda i=idx: self.add_pad(i))
            b.pack(side='left', padx=(2, 0))
        self.pad_label = ttk.Label(act3, text='', foreground=self.theme.accent)
        self.pad_label.pack(side='left', padx=(6, 0))
        self.pin_btn = ttk.Button(act3, text='pin', command=self.pin_current, state='disabled')
        self.pin_btn.pack(side='left', padx=(8, 0))
        self.compare_var = tk.BooleanVar(value=False)
        self.compare_chk = ttk.Checkbutton(act3, text='compare', variable=self.compare_var, command=self._redraw)
        self.compare_chk.pack(side='left', padx=(4, 0))


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
        for v in (self.size_var, self.steps_var, self.layout_var):
            v.trace_add('write', lambda *a: self._refresh_generate_label())
        self._add_tooltips()
        self._update_banner()
        self._refresh_generate_label()

        # Folders are the default view, so the picture strip painted here is replaced a few hundred milliseconds later:
        # 18 thumbnails rendered only to be thrown away, and the flash of content that vanishes is exactly what was
        # reported as confusing. Paint a grouping line instead and let `_apply_plan` draw the folders.
        self._grouping = bool(self.group_var.get())      # a plan is coming, so the strip has nothing to draw yet
        self._prediction = None          # which folder the prompt in the box would join
        self._pane_cur = None            # the keyboard cursor inside the open folder's pane
        self._content_order = []
        self._content_cols = 1
        self._all_queue = None           # the ALL sweep's remaining profiles, or None
        self._all_total = 0
        self._scene_queue = None         # the remaining scenes of a multi-scene prompt, or None
        self._scene_current = None       # (scene text, title) of the scene being rendered, for the grader
        self._scene_total = 0
        self._all_saved = None
        self._see_pending = None         # a folder waiting to be scrolled into view after the next rebuild
        self.refresh_gallery()
        root.bind('<Control-v>', lambda e: None if self._typing() else self.paste_base())
        self.canvas.bind('<B1-Motion>', self._on_compare_drag)
        self.style_var.trace_add('write', lambda *a: self._update_banner())
        self.model_var.trace_add('write', lambda *a: self._update_banner())
        for seq, fn in (('<Delete>', self._on_delete_key), ('<Control-a>', self._on_ctrl_a),
                        ('<Escape>', self._on_escape)):
            root.bind(seq, fn)

        if not self._sidebar:
            self.set_sidebar(False)
        root.after(400, self.probe_llm)
        root.bind('<Destroy>', self._on_destroy, add='+')
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._poll()
        # Folders are the default view. Grouping reads a sidecar per picture, so it waits for the window to exist and
        # for the first paint instead of running during construction.
        if self.group_var.get():
            self.root.after_idle(lambda: self.toggle_groups(True) if not self._plan else None)

    def _ensure_maximized(self):
        """Ask again once the window is mapped.

        `state('zoomed')` before the window is mapped is silently ignored on some Tk/Windows combinations, and a later
        `minsize` can un-maximise it; so the state is confirmed and, failing that, the window is placed on the work area.
        """
        try:
            if self.root.state() != 'zoomed':
                self.root.state('zoomed')
        except Exception:
            pass
        try:
            self.theme.titlebar()      # the title bar is only real once the window exists
        except Exception:
            pass

    # -- gallery: thumbnails -------------------------------------------------
    def _scan(self):
        """One directory read: (path, mtime) for every png, newest first.

        The filter and the cache check both need the mtime, so scanning once beats stat-ing each file twice.
        """
        out = Path(self.args.out)
        if not out.exists():
            return []
        rows, meta_mtimes = [], {}
        try:
            with os.scandir(out) as it:
                for entry in it:
                    low = entry.name.lower()
                    try:
                        if low.endswith('.json'):
                            meta_mtimes[Path(entry.name).stem] = entry.stat().st_mtime
                        elif low.endswith('.png') and entry.is_file():
                            rows.append((Path(entry.path), entry.stat().st_mtime))
                    except OSError:
                        continue
        except OSError:
            return []
        rows.sort(key=lambda r: -r[1])
        self._meta_mtimes = meta_mtimes      # a sidecar edit must invalidate the search cache too
        return rows

    def _all_files(self):
        return [p for p, _m in self._scan()]

    def _tile_widget(self, parent, path, thumb, size=52, browser=False, pane=False):
        lab = tk.Label(parent, image=thumb, bd=1, relief='flat', cursor='hand2')
        lab.image = thumb
        lab.path = path
        lab.bind('<Button-1>', lambda e, p=path: self._on_tile_click(e, p, browser=browser, pane=pane))
        for seq, fn in (('<Left>', lambda e: self._nav_key(e, -1, 0)), ('<Right>', lambda e: self._nav_key(e, 1, 0)),
                        ('<Up>', lambda e: self._nav_key(e, 0, -1)), ('<Down>', lambda e: self._nav_key(e, 0, 1))):
            lab.bind(seq, fn)
        if browser:
            # a double click loads the picture and leaves the window open: closing it was easy to do by accident
            lab.bind('<Double-Button-1>', lambda e, p=path: self._load_from_browser(p))
        # Nothing else is bound to a double click. It used to *generate* on the strip and in the pane, so looking at a
        # picture twice in quick succession spent a minute of GPU with whatever prompt happened to be in the box - a
        # browsing gesture doing the most expensive thing in the app (reported 2026-10-28). Making a picture is the
        # Generate button, Vary, or Upscale; none of them is a click away from browsing.
        return lab

    def _load_from_browser(self, path):
        self.select_file(path)
        self.status.configure(text='loaded %s - Use as Base to edit from it' % self._short_name(Path(path).name))

    def _on_search_typing(self, _event=None):
        """One filter per pause in typing: matching 780 sidecars on every keypress costs ~200 ms."""
        if self._search_after is not None:
            try:
                self.root.after_cancel(self._search_after)
            except Exception:
                pass
        self._search_after = self.root.after(250, self._run_search)

    def _run_search(self):
        self._search_after = None
        self.refresh_gallery()

    def _searchable(self, path, mtime=None):
        """Everything a keyword can match: the file name plus the sidecar's prompt, style, model, seed and more.

        Cached per file and validated by mtime, because a keystroke re-runs the filter over the whole folder.
        """
        if mtime is None:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                return path.name.lower()
        sidecar_mtime = getattr(self, '_meta_mtimes', {}).get(path.stem)
        hit = self._meta_cache.get(path.name)
        if hit is not None and hit[0] == mtime and hit[1] == sidecar_mtime:
            return hit[2]
        parts = [path.name]
        try:
            meta = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            for key in ('prompt', 'style', 'model', 'negative', 'sweep', 'base_image', 'mask'):
                value = meta.get(key)
                if value:
                    parts.append(str(value))
            if meta.get('seed') is not None:
                parts.append(str(meta['seed']))
            if meta.get('steps') is not None:
                parts.append('%s steps' % meta['steps'])
        except Exception:
            pass
        blob = ' '.join(parts).lower()
        self._meta_cache[path.name] = (mtime, sidecar_mtime, blob)
        return blob

    def toggle_groups(self, on=None):
        """Turn the prompt folders on: compute them in the background, oldest first, and touch nothing on disk."""
        want = bool(self.group_var.get()) if on is None else bool(on)
        self.group_var.set(want)
        self._settings['folders'] = want
        save_settings(self._settings)
        if not want:
            self._plan, self._active_folder, self._grouping = None, None, False
            self.folder_bar.grid_remove()
            self.refresh_gallery()
            return
        self._grouping = True
        self.folder_bar.grid()
        self._render_folder_bar()
        threading.Thread(target=self._compute_groups, daemon=True).start()

    def _follow_prediction(self, prompt):
        """Open the folder the prompt is about to land in - but only when no folder is being read.

        Leave-one-out on the real gallery puts the right folder first for 99.0 % of 1,756 prompts, so the prediction is
        worth acting on. Acting on it *always* would be wrong: the reader may be standing in another folder, and having
        the view jump when Generate is pressed is worse than not moving it. So: predict and show it beside the folder
        button in every case, and switch the view only from the unfiltered "All folders" state, where there is no
        reading position to lose.
        """
        self._prediction = None
        if not self.group_var.get() or not self._plan:
            return
        try:
            import group_prompts as _gp
            hit = _gp.predict(prompt, self._plan)
        except Exception:
            hit = None
        if not hit:
            return
        self._prediction = hit
        if self._active_folder is None and hit['index'] < len(self._plan['groups']):
            self.select_folder(hit['index'])
            self._see_folder(hit['index'])           # bring it into view: it may be far down the list
        self._render_folder_bar()

    def _folder_holding(self, path, plan=None):
        """Index of the folder that holds `path`, or that its prompt would join; None when neither applies."""
        plan = plan or self._plan
        if path is None or not plan or not plan.get('groups'):
            return None
        name = Path(path).name
        for i, group in enumerate(plan['groups']):
            if any(Path(r['path']).name == name for r in group['items']):
                return i
        try:
            prompt = (read_meta(Path(path)) or {}).get('prompt') or ''
        except Exception:
            prompt = ''
        if not prompt.strip():
            return None
        try:
            import group_prompts as _gp
            hit = _gp.predict(prompt, plan)
        except Exception:
            hit = None
        return hit['index'] if hit else None

    def _pane_cells(self):
        """The contents pane's cells in display order, its column count, and its tile widgets."""
        keys = list(getattr(self, '_content_order', []) or [])
        widgets = getattr(self, '_content_widgets', {}) or {}
        return keys, max(1, int(getattr(self, '_content_cols', 1) or 1)), [widgets[k] for k in keys if k in widgets]

    def _pane_visible_rows(self, tiles, cols):
        pitch = 0
        if len(tiles) > cols:
            try:
                pitch = tiles[cols].winfo_y() - tiles[0].winfo_y()
            except Exception:
                pitch = 0
        if pitch <= 0 and tiles:
            try:
                pitch = tiles[0].winfo_height() + 2
            except Exception:
                pitch = 0
        return grid_visible_rows(self.contents_canvas.winfo_height(), pitch)

    def _see_pane_cell(self, tile):
        """Scroll the pane to the cell under the keyboard, and only when it is actually out of view."""
        if tile is None:
            return
        canvas = self.contents_canvas
        self.contents.update_idletasks()
        canvas.update_idletasks()
        total = max(self.contents.winfo_height(), 0)
        if total <= 0:
            return
        canvas.configure(scrollregion=(0, 0, max(1, canvas.winfo_width()), total))
        top, height = tile.winfo_y(), max(1, tile.winfo_height())
        view_top, view_h = canvas.canvasy(0), canvas.winfo_height()
        if top >= view_top - 1 and top + height <= view_top + view_h:
            return
        canvas.yview_moveto(min(1.0, max(0.0, (top - 8.0) / total)))

    def _pane_nav(self, move, extend=False):
        """One layout-grid movement inside the open folder's pane.

        The pane is a list in its own right: the arrows move its cursor and leave the strip alone, clicking a picture
        hands it the keyboard, and clicking a tile in column 0 hands it back. The movement itself is the shared
        `grid_move` the strip and both ScrollGrids use, so wrap/clamp/paging cannot drift between the lists.
        """
        keys, cols, tiles = self._pane_cells()
        if not keys:
            return 'break'
        current = getattr(self, '_pane_cur', None)
        if current not in keys:
            current = self.selected[-1] if self.selected and self.selected[-1] in keys else None
        index = keys.index(current) if current in keys else -1
        new = grid_move(index, len(keys), cols, move, rows=self._pane_visible_rows(tiles, cols))
        if new is None or not 0 <= new < len(keys):
            return 'break'
        key = keys[new]
        self._pane_cur = key
        if extend and self._anchor in keys:
            i, j = keys.index(self._anchor), new
            if i > j:
                i, j = j, i
            self.selected = list(keys[i:j + 1])
        else:
            self.selected = [key]
            self._anchor = key
        self._paint()
        self._see_pane_cell(tiles[new] if new < len(tiles) else None)
        return 'break'

    def _pane_has_focus(self):
        try:
            focused = self.root.focus_get()
        except Exception:
            return False
        return focused is self.contents_canvas or focused in (getattr(self, '_content_widgets', {}) or {}).values()

    def _pane_key(self, event, move):
        """Arrows reach the pane only while the pane has the keyboard - never while a text box does."""
        if self._typing() or not self._pane_has_focus():
            return None
        return self._pane_nav(move, extend=bool(event.state & 0x0001))

    def _pane_toggle(self, event=None):
        if not self._pane_has_focus():
            return None
        keys, _cols, _tiles = self._pane_cells()
        current = getattr(self, '_pane_cur', None)
        if current in keys:
            self.selected = ([p for p in self.selected if p != current] if current in self.selected
                             else self.selected + [current])
            self._paint()
        return 'break'

    def _pane_open(self, event=None):
        if not self._pane_has_focus():
            return None
        keys, _cols, _tiles = self._pane_cells()
        current = getattr(self, '_pane_cur', None)
        if current in keys:
            self.select_file(current)
        return 'break'

    def _pane_all(self, event=None):
        if not self._pane_has_focus():
            return None
        self.selected = list(self._pane_cells()[0])
        self._paint()
        return 'break'

    def _regroup_soon(self):
        """Recompute the folders after a render lands: a new picture changes the plan, and the open folder should see it.

        Debounced because a batch or a 16-up sheet arrives as several events; grouping 1,800 prompts costs ~2.4 s, which
        belongs on a worker, not in the event loop.
        """
        if not self.group_var.get():
            return
        if getattr(self, '_regroup_job', None):
            try:
                self.root.after_cancel(self._regroup_job)
            except Exception:
                pass
        self._regroup_job = self.root.after(1200, self._regroup_now)

    def _regroup_now(self):
        self._regroup_job = None
        if not self.group_var.get() or getattr(self, '_grouping', False):
            return
        self._grouping = True
        threading.Thread(target=self._compute_groups, daemon=True).start()

    def _folders_clicked(self):
        """Fork the one folder control: browse when a plan exists, otherwise start computing one."""
        if self._plan:
            self.open_folder_browser()
        else:
            self.toggle_groups(True)

    def _compute_groups(self):
        """Background: prompts only, no images decoded, no model, no file written."""
        started = time.time()
        try:
            import group_prompts
            rows = []
            for path, stamp in self._scan():          # (path, mtime); the prompt lives in the sidecar
                meta = read_meta(path) or {}
                rows.append({'path': str(path), 'prompt': meta.get('prompt') or '',
                             'created': meta.get('created') or datetime.fromtimestamp(stamp).isoformat(timespec='seconds')})
            plan = group_prompts.plan(rows, keep_lexicon=True)
            plan['seconds'] = round(time.time() - started, 2)
            self.ui_events.put(('groups', plan))
        except Exception as e:
            self.ui_events.put(('groups_error', '%s: %s' % (type(e).__name__, e)))

    def _apply_plan(self, plan):
        was_open = None
        if self._plan and self._active_folder is not None and self._active_folder < len(self._plan['groups']):
            was_open = self._plan['groups'][self._active_folder].get('label')
        self._folder_shown = BATCH_FOLDERS
        self._shown = RECENT_N
        self._sheets = {}
        self._plan = plan
        self._grouping = False
        # keep the open folder by name: a re-group after a generation must not close the view being read, and indexes
        # move when the plan is recomputed
        self._active_folder = None
        if was_open:
            self._active_folder = next((i for i, g in enumerate(plan['groups']) if g.get('label') == was_open), None)
        elif plan['groups']:
            # Folders have just been turned on with a picture selected in the flat list: open the folder that picture
            # belongs to, and scroll to it. The picture itself if it is a member, otherwise the folder its prompt would
            # join - the same rule as the pre-render prediction.
            target = Path(self.selected[-1]) if self.selected else (self._current or None)
            index = self._folder_holding(target, plan)
            if index is not None:
                self._active_folder = index
                self._see_folder(index)
        self._render_folder_bar()
        self.refresh_gallery()
        # The plan just changed underneath an open folder, and the pane is drawn by `_render_contents`, which only
        # `select_folder` called: a picture that had just landed in the open folder stayed invisible until the reader
        # re-selected the folder (reported 2026-10-28). Re-render here so the plan and the pane cannot disagree.
        if self._active_folder is not None:
            self._render_contents()
        self.status.configure(text='%d folders from %d pictures in %.1fs - recent %d stay ungrouped'
                              % (len(plan['groups']), len(plan['recent']) + len(plan['groups']) and
                                 sum(len(g['items']) for g in plan['groups']) + len(plan['ungrouped']) + len(plan['recent']),
                                 plan.get('seconds') or 0, len(plan['recent'])))

    def _render_folder_bar(self):
        """A folder *button*, not a dropdown: it names what is open and it opens the full-screen folder browser.

        The dropdown was the wrong shape twice over. It hid every folder but one, in a column too narrow to show the
        names even when opened, and the "..." list beside it was a Treeview of text in a picture application. The
        browser it opens is the same shape as View All - tiles that fill the window, wheel scrolling, infinite
        scrolling - so there is one interaction to learn, and the strip no longer has to double as the folder index.
        """
        for w in self.folder_bar.winfo_children():
            w.destroy()
        if self._grouping:
            ttk.Label(self.folder_bar, text='grouping prompts\u2026', foreground=self.theme.muted).pack(side='left')
            return
        if not self._plan:
            return
        self._folder_choices = [None] + list(range(len(self._plan['groups'])))
        name = 'All folders' if self._active_folder is None else self._folder_text(self._active_folder)
        self.folder_pick_btn = ttk.Button(self.folder_bar, text='%s\u2026' % name, width=17,
                                          command=self.open_folder_browser)
        self.folder_pick_btn.pack(side='left')
        ttk.Button(self.folder_bar, text='A/B\u2026', width=4, command=self.review_start).pack(side='left', padx=(3, 0))
        if getattr(self, '_prediction', None):
            label = ttk.Label(self.folder_bar, text='\u2192 %s' % shorten_title(self._prediction['label'], 5),
                              foreground=self.theme.accent, font=('Segoe UI', 8))
            label.pack(side='left', padx=(4, 0))
            self._tip(label, 'The prompt as typed would land in this folder, so the next render appears there.')

    # -- the full-screen folder browser -------------------------------------------------------------
    def open_folder_browser(self):
        """Every folder, full screen: 4-up tiles on the left, the open folder's pictures in a grid on the right.

        Same mechanism as View All (ScrollGrid), so both scale to the window and scroll the same way.
        """
        if not self._plan:
            self.hint.configure(text='turn folders on first - grouping takes a second')
            return
        existing = getattr(self, '_folder_win', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        from PIL import ImageTk

        win = tk.Toplevel(self.root)
        self.root.after(30, lambda w=win: self.theme.titlebar(w))
        self._folder_win = win
        win.title('Prompt folders (%d)' % len(self._plan['groups']))
        try:
            win.state('zoomed')
        except Exception:
            win.geometry('1200x800')

        head = ttk.Frame(win)
        head.pack(side='top', fill='x', padx=8, pady=(8, 4))
        chosen = ttk.Label(head, text='', foreground='#222')
        chosen.pack(side='left')
        count = ttk.Label(head, text='', foreground=self.theme.muted)
        count.pack(side='left', padx=(10, 0))
        ttk.Button(head, text='A/B review\u2026', command=lambda: self.review_start()).pack(side='right', padx=(6, 0))
        ttk.Button(head, text='Close', command=self._close_folder_browser).pack(side='right')

        body = ttk.Frame(win)
        body.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        body.columnconfigure(0, weight=0, minsize=300)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        def folder_tile(parent, item, size):
            photo = ImageTk.PhotoImage(self._folder_sheet(item['group'], size))
            lab = tk.Label(parent, image=photo, bd=1, relief='flat', cursor='hand2', background=self.theme.sheet_bg)
            lab.image = photo
            lab.bind('<Button-1>', lambda e, i=item['index']: pick(i))
            return lab

        def picture_tile(parent, item, size):
            path = item['path']
            tp = thumb_for(path)
            if not tp:
                return tk.Label(parent, text=path.name[:18], width=12, height=6,
                                background=self.theme.window, foreground=self.theme.muted)
            try:
                with Image.open(tp) as im:
                    im.thumbnail((size, size), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(im)
            except Exception:
                return tk.Label(parent, text='?', width=6)
            lab = self._tile_widget(parent, path, photo, browser=True)
            lab.image = photo
            return lab

        def folder_caption(item):
            group = item['group']
            return '%s\n%d pictures' % (shorten_title(group.get('title') or group['label']), len(group['items']))

        left = ScrollGrid(body, folder_tile, caption=folder_caption, batch=40, tile_target=150,
                          background=self.theme.window, muted=self.theme.muted, accent=self.theme.accent,
                          on_activate=lambda key: pick(None if key == 'all' else int(key)))
        left.grid(row=0, column=0, sticky='nsew')
        right = ScrollGrid(body, picture_tile, caption=lambda item: item['path'].name[:26], batch=80,
                           tile_target=190, background=self.theme.window, muted=self.theme.muted,
                           accent=self.theme.accent, on_activate=self._load_from_browser)
        right.grid(row=0, column=1, sticky='nsew', padx=(8, 0))

        def pick(index):
            # the grid's cursor follows the open folder, so the first arrow press starts from the folder on screen
            left.set_focus('all' if index is None else str(index))
            self.select_folder(index)
            chosen.configure(text='All pictures' if index is None else self._folder_text(index))
            paths = self._all_files() if index is None else [
                Path(r['path']) for r in self._plan['groups'][index]['items']]
            right.set_items([{'key': p, 'path': p} for p in paths])
            count.configure(text='%d picture%s' % (len(paths), '' if len(paths) == 1 else 's'))

        everything = {'label': 'All pictures', 'title': 'All pictures',
                      'items': [{'path': str(p)} for p in self._all_files()]}
        items = [{'key': 'all', 'index': None, 'group': everything}]
        for i, g in enumerate(self._plan['groups']):
            items.append({'key': str(i), 'index': i, 'group': g})
        left.set_items(items)
        pick(self._active_folder)
        win.protocol('WM_DELETE_WINDOW', self._close_folder_browser)
        # the keyboard starts on the folder column, where Up/Down walk the folders and Enter opens one; the picture
        # column takes it back with a click, since each grid's keys are bound to that grid's own canvas
        def focus_folders():
            try:
                if win.winfo_exists() and left.cells:
                    left.canvas.focus_set()
            except Exception:
                pass                                    # the window closed before the timer fired
        win.after(120, focus_folders)

    def _close_folder_browser(self):
        win = getattr(self, '_folder_win', None)
        self._folder_win = None
        if win is not None and win.winfo_exists():
            win.destroy()

    def _folder_text(self, index):
        """Short label for the selector: the folder's name and size, or All with the full picture count."""
        if index is None:
            # the real gallery total, singletons included - the plan's groups cannot know about them
            try:
                total = len(self._all_files())
            except Exception:
                total = sum(len(g['items']) for g in self._plan['groups'])
            return 'All (%d)' % total
        g = self._plan['groups'][index]
        label = g['label']
        cut = label[:16].rsplit(' ', 1)[0] if len(label) > 16 else label
        return '%s\u2026 (%d)' % (cut or label[:16], len(g['items'])) if len(label) > 16 else '%s (%d)' % (label, len(g['items']))


    def _see_folder(self, index):
        """Scroll the folder strip so the open folder is visible.

        A folder opened *automatically* - the one the prompt is predicted to join - can be anywhere in the list, and
        selecting it while it sits below the fold is invisible. Batches are lazy, so it may not even be rendered yet:
        extend the batch first, then scroll after the layout has settled.
        """
        if index is None or not self._plan:
            return
        if index >= self._folder_shown:
            self._folder_shown = index + 1
            self._render_folder_tiles()
        self._see_pending = index
        self.root.after_idle(lambda: self.refresh_gallery())

    def _scroll_folder_into_view(self, index, _retry=True):
        """Scroll the strip to `index`, refreshing the scrollregion first.

        `yview_moveto` against a stale scrollregion silently clamps to the old extent - the strip's resize handler is
        debounced by 50 ms, so straight after a rebuild the canvas still believes the list is short and the scroll lands
        at the top. One immediate pass with the region recomputed, then one retry while the layout settles.
        """
        def again():
            # one retry is not enough: the strip rebuilds after the selection, and the batch may not even exist yet
            if _retry:
                self.root.after(80, lambda: self._scroll_folder_into_view(index, False))

        tiles = [w for w in self.recent.winfo_children() if w.grid_info()]
        if index >= len(tiles):
            if index >= self._folder_shown:
                self._folder_shown = index + 1
                self._render_folder_tiles()
            again()
            return
        tile = tiles[index]
        canvas = self.gal_canvas
        # The strip rebuilds right after a selection, so at this instant the inner frame is populated but not laid out:
        # its bbox is a few tens of pixels while the canvas still holds the old, taller region. Measure a forced layout
        # instead - shrinking the region to that 60 px bbox is what made yview_moveto clamp to the top.
        self.recent.update_idletasks()
        canvas.update_idletasks()
        total = max(self.recent.winfo_height(), 0)
        if total <= 0:
            again()
            return
        canvas.configure(scrollregion=(0, 0, max(1, canvas.winfo_width()), total))
        top, height = tile.winfo_y(), max(1, tile.winfo_height())
        view_top, view_h = canvas.canvasy(0), canvas.winfo_height()
        if top >= view_top - 1 and top + height <= view_top + view_h:
            return                                   # already visible: never jump for nothing
        canvas.yview_moveto(min(1.0, max(0.0, (top - 8.0) / total)))
        canvas.update_idletasks()
        view_top = canvas.canvasy(0)
        if not (top >= view_top - 1 and top + height <= view_top + view_h):
            again()

    def select_folder(self, index):
        self._active_folder = index
        # With a folder open the gallery column is two columns of pictures, so it is allowed to claim the empty space
        # the picture pane leaves; with no folder open it goes back to hugging its content.
        try:
            self.root.columnconfigure(0, weight=1 if index is not None else 0)
        except Exception:
            pass
        self._content_shown = BATCH_CONTENTS
        self._render_contents()
        try:
            self.contents_canvas.yview_moveto(0)
        except Exception:
            pass
        if getattr(self, 'folder_pick_btn', None) is not None and self._plan:
            try:
                name = 'All folders' if index is None else self._folder_text(index)
                self.folder_pick_btn.configure(text='%s\u2026' % name)
            except Exception:
                pass
        self.refresh_gallery()

    # -- paired A/B review of one folder --------------------------------------------------------------
    def review_start(self, index=None):
        """Review the selected folder (or the biggest one) as most-similar-first A/B pairs, full screen."""
        if not self._plan:
            self.hint.configure(text='turn folders on first - A/B review works inside a prompt folder')
            return
        if index is None:
            index = self._active_folder
        if index is None:
            index = max(range(len(self._plan['groups'])), key=lambda i: len(self._plan['groups'][i]['items']))
        group = self._plan['groups'][index]
        paths = [r['path'] for r in group['items']]
        if len(paths) < 2:
            self.hint.configure(text='that folder has only one picture - nothing to pair')
            return
        self.status.configure(text='preparing %d pairs from %s\u2026' % (len(paths) // 2, group['label'][:28]))
        self.update_idletasks() if hasattr(self, 'update_idletasks') else None
        self.root.update_idletasks()

        def work():
            try:
                import pair_review
                key = pair_review.state_key(paths)
                state = pair_review.load_state(key)
                pair_review.build(paths, state, hash_store=pair_review.STATE_DIR / ('%s-hashes.json' % key))
                pair_review.save_state(state)
                self.ui_events.put(('review_ready', (group['label'], state)))
            except Exception as e:
                self.ui_events.put(('review_error', '%s: %s' % (type(e).__name__, e)))
        threading.Thread(target=work, daemon=True).start()

    def review_open(self, label, state):
        """The comparison window: left and right, three decisions, and a way out that keeps the progress."""
        import pair_review
        if getattr(self, '_review', None) is not None and self._review.get('win') and self._review['win'].winfo_exists():
            self._review['win'].lift()
            return
        win = tk.Toplevel(self.root)
        self.root.after(30, lambda w=win: self.theme.titlebar(w))
        win.title('A/B review - %s' % label[:50])
        try:
            win.state('zoomed')
        except Exception:
            win.geometry('%dx%d+0+0' % (self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        win.configure(background=_mix('#000000', self.theme.window, 0.35))
        win.columnconfigure(0, weight=1)
        win.columnconfigure(1, weight=1)
        win.rowconfigure(0, weight=1)
        left_lbl = tk.Label(win, background=_mix('#000000', self.theme.window, 0.35), foreground='#ddd', compound='center')
        right_lbl = tk.Label(win, background=_mix('#000000', self.theme.window, 0.35), foreground='#ddd', compound='center')
        left_lbl.grid(row=0, column=0, sticky='nsew', padx=(6, 3), pady=6)
        right_lbl.grid(row=0, column=1, sticky='nsew', padx=(3, 6), pady=6)
        for lbl in (left_lbl, right_lbl):
            lbl.bind('<Configure>', self._on_review_resize)
        bar = ttk.Frame(win)
        bar.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        for c in range(4):
            bar.columnconfigure(c, weight=1)
        info = ttk.Label(bar, text='', anchor='center')
        info.grid(row=0, column=0, columnspan=4, sticky='ew', pady=(0, 4))
        mode_row = ttk.Frame(bar)
        mode_row.grid(row=1, column=0, columnspan=4, sticky='ew', pady=(0, 3))
        self._review_mode = tk.StringVar(value='rank')
        ttk.Radiobutton(mode_row, text='Rank - mark a winner, delete nothing', value='rank',
                        variable=self._review_mode, command=self._review_relabel).pack(side='left')
        ttk.Radiobutton(mode_row, text='Delete - recycle the loser', value='delete',
                        variable=self._review_mode, command=self._review_relabel).pack(side='left', padx=(12, 0))
        ttk.Button(mode_row, text='Reset wins in this folder', command=self._review_reset).pack(side='right')
        b_left = ttk.Button(bar, text='Left wins  (1)', command=lambda: self.review_decide('left'))
        b_keep = ttk.Button(bar, text='Tie  (2)', command=lambda: self.review_decide('middle'))
        b_right = ttk.Button(bar, text='Right wins  (3)', command=lambda: self.review_decide('right'))
        b_stop = ttk.Button(bar, text='Stop  (Esc)', command=self.review_stop)
        for i, b in enumerate((b_left, b_keep, b_right, b_stop)):
            b.grid(row=2, column=i, sticky='ew', padx=4)
        self._review = {'win': win, 'state': state, 'left_lbl': left_lbl, 'right_lbl': right_lbl, 'info': info,
                        'pair': None, 'deleted': 0, 'queue': None, 'path': None,
                        'buttons': (b_left, b_keep, b_right)}
        for key, fn in (('1', lambda: self.review_decide('left')),
                        ('2', lambda: self.review_decide('middle')),
                        ('3', lambda: self.review_decide('right')),
                        ('<Escape>', self.review_stop),
                        ('<Left>', lambda: self.review_decide('left')),
                        ('<Right>', lambda: self.review_decide('right')),
                        ('<space>', lambda: self.review_decide('middle'))):
            win.bind(key, lambda e, f=fn: (f(), 'break')[1])
        win.protocol('WM_DELETE_WINDOW', self.review_stop)
        self._review_show_next()


    def _review_fit(self, lbl, path=None, redraw_only=False):
        """Size a picture to the label's own cell, not to the screen.

        Sizing from the screen clipped the picture whenever the window was smaller than the display or the taskbar
        offset reduced the work area; binding <Configure> means every resize refits and nothing is ever cropped.
        """
        from PIL import ImageTk
        path = path or getattr(lbl, 'source', None)
        if not path:
            return
        w = max(160, lbl.winfo_width() - 8)
        h = max(120, lbl.winfo_height() - 8)
        try:
            with Image.open(path) as im:
                im = im.convert('RGB')
                im.thumbnail((w, h), Image.LANCZOS)
                photo = ImageTk.PhotoImage(im)
            lbl.configure(image=photo, text='')
            lbl.image = photo
        except Exception as e:
            lbl.configure(image='', text='%s\n(%s)' % (Path(path).name, type(e).__name__))

    def _review_relabel(self):
        """Rank mode marks a winner; delete mode recycles a file. The buttons say which is which."""
        r = getattr(self, '_review', None)
        if not r:
            return
        left, middle, right = r['buttons']
        if self._review_mode.get() == 'rank':
            left.configure(text='Left wins  (1)')
            middle.configure(text='Tie  (2)')
            right.configure(text='Right wins  (3)')
        else:
            left.configure(text='Delete left  (1)')
            middle.configure(text='Keep both  (2)')
            right.configure(text='Delete right  (3)')
        self._review_show_next()

    def _review_reset(self):
        """Zero the record for this folder only - the one explicit way to start a comparison over."""
        import pair_rank
        r = getattr(self, '_review', None)
        if not r or not self._plan:
            return
        group = self._plan['groups'][self._active_folder] if self._active_folder is not None else None
        paths = [it['path'] for it in (group['items'] if group else self._files)]
        n = pair_rank.reset_wins(paths)
        r['state']['decisions'] = {}
        r['queue'] = None
        r['deleted'] = 0
        self.hint.configure(text='cleared the record on %d pictures - comparisons start from zero' % n)
        self._review_show_next()

    def _review_show_next(self):
        import pair_review
        from PIL import ImageTk
        r = getattr(self, '_review', None)
        if not r:
            return
        queue = pair_review.remaining(r['state'])
        if r['queue'] is None or not queue:
            r['queue'] = queue
        queue = r['queue']
        decided, total, deletes = pair_review.progress(r['state'])
        if not queue:
            r['info'].configure(text='done - %d pairs decided, %d deleted. Close this window.' % (decided, total))
            for w in (r['left_lbl'], r['right_lbl']):
                w.configure(image='', text='')
            return
        a, b, dist = queue[0]
        r['pair'] = (a, b)
        for lbl, path in ((r['left_lbl'], a), (r['right_lbl'], b)):
            lbl.source = path
            self._review_fit(lbl, path)
        r['info'].configure(text='pair %d of %d \u00b7 %d deleted \u00b7 similarity distance %d \u00b7 '
                                 '%s | %s' % (decided + 1, total, deletes, dist,
                                              Path(a).name[:34], Path(b).name[:34]))

    def _on_review_resize(self, event=None):
        r = getattr(self, '_review', None)
        if not r or r.get('_resize_job'):
            return
        r['_resize_job'] = self.root.after(120, self._review_refit)

    def _review_refit(self):
        r = getattr(self, '_review', None)
        if not r:
            return
        r['_resize_job'] = None
        for lbl in (r['left_lbl'], r['right_lbl']):
            if getattr(lbl, 'source', None):
                self._review_fit(lbl)

    def review_decide(self, choice):
        import pair_review
        import pair_rank
        r = getattr(self, '_review', None)
        if not r or not r.get('pair'):
            return
        a, b = r['pair']
        # the buttons say left/middle/right; the mode decides what that means
        if choice in ('left', 'middle', 'right'):
            rank = getattr(self, '_review_mode', None) and self._review_mode.get() == 'rank'
            choice = {('left', True): 'left_wins', ('middle', True): 'tie', ('right', True): 'right_wins',
                      ('left', False): 'delete_left', ('middle', False): 'keep_both',
                      ('right', False): 'delete_right'}[(choice, rank)]
        if choice in pair_rank.WIN_CHOICES:
            winner = {'left_wins': a, 'right_wins': b}.get(choice)
            to_delete = pair_rank.record(r['state'], a, b, choice)
            if winner:
                rec = pair_rank.record_of(winner)
                self.hint.configure(text='%s now %dW-%dL (rank %d)' % (
                    Path(winner).name, rec['wins'], rec['losses'], int(rec['wins'])))
            else:
                self.hint.configure(text='tie recorded')
            pair_review.save_state(r['state'])
            r['pair'] = None
            if r['queue']:
                r['queue'] = r['queue'][1:]
            self._review_show_next()
            return
        to_delete = pair_review.decide(r['state'], a, b, choice)
        if to_delete:
            try:
                self._recycle_files([Path(to_delete), Path(to_delete).with_suffix('.json'),
                                     Path(to_delete).parent / THUMB_DIR / (Path(to_delete).stem + '.jpg')])
                r['deleted'] = r.get('deleted', 0) + 1
            except Exception as e:
                self.hint.configure(text='could not delete %s: %s' % (Path(to_delete).name, e))
        pair_review.save_state(r['state'])
        r['pair'] = None
        if r['queue']:
            r['queue'] = r['queue'][1:]
        self._review_show_next()

    def review_stop(self, event=None):
        import pair_review
        r = getattr(self, '_review', None)
        if not r:
            return 'break'
        pair_review.save_state(r['state'])
        decided, total, deletes = pair_review.progress(r['state'])
        try:
            r['win'].destroy()
        except Exception:
            pass
        self._review = None
        self.refresh_gallery()
        self._render_folder_bar()
        self.status.configure(text='A/B review stopped: %d of %d pairs decided, %d deleted - progress saved'
                              % (decided, total, deletes))
        return 'break'

    def _scroll_pos(self, canvas):
        try:
            return canvas.yview()[0]
        except Exception:
            return 0.0

    def _restore_scroll(self, canvas, pos):
        """Put a list back where the reader left it. Selecting in one column must not scroll another."""
        if not pos:
            return
        def restore():
            try:
                canvas.yview_moveto(pos)
            except Exception:
                pass
        self.root.after_idle(restore)

    def toggle_options(self, force=None):
        """Fold the column's configuration away, or bring it back.

        The first line - what the list *is* and the folders switch - stays: it answers "what am I looking at" rather than
        "how is it configured". Everything else (search, the button rows, the sort row, the hints) is configuration and
        costs vertical room the pictures want; measured, folding it returns ~150 px to the strip.
        """
        show = (not self._options_shown) if force is None else bool(force)
        self._options_shown = show
        group = [getattr(self, 'search_box', None), getattr(self, 'starred_chk', None), getattr(self, 'btns', None)]
        for widget in group:
            if widget is None or not widget.winfo_exists():
                continue
            if show:
                widget.grid()
            else:
                widget.grid_remove()
        # the folder bar has its own rule: it exists only when a plan does
        bar = getattr(self, 'folder_bar', None)
        if bar is not None and bar.winfo_exists():
            if show and self._plan:
                bar.grid()
            else:
                bar.grid_remove()
        if hasattr(self, 'options_btn'):
            self.options_btn.configure(text='\u25be options' if show else '\u25b8 options')

    def _sort_wins(self):
        return getattr(self, 'sort_var', None) is not None and self.sort_var.get() == 'Wins'

    def _sort_sequence(self):
        return getattr(self, 'sort_var', None) is not None and self.sort_var.get() == 'Sequence'

    def _scene_of(self, path):
        """(scene, position in that scene) for a picture, or None. Memoised: the sidecars are read once per refresh."""
        cache = getattr(self, '_scene_cache', None)
        if cache is None:
            cache = self._scene_cache = {}
        if path in cache:
            return cache[path]
        meta = read_meta(path) or {}
        scene, of = meta.get('scene'), meta.get('scene_of')
        value = (int(scene), int(of or 0)) if isinstance(scene, int) or (isinstance(scene, str) and scene.isdigit()) \
            else None
        cache[path] = value
        return value

    def _sequence_order(self, paths):
        """Film order: numbered pictures by (scene, position in scene), then everything else newest-first.

        Two passes rather than one clever key: a storyboard has an order that ends, and the pictures made outside one
        (a test, a quick experiment) belong after it, not interleaved by a date they happened to be made on.
        """
        rows = [(p, self._scene_of(p), (read_meta(p) or {}).get('created') or '') for p in paths]
        numbered = sorted((r for r in rows if r[1]), key=lambda r: (r[1][0], r[1][1], r[2]))
        rest = sorted((r for r in rows if not r[1]), key=lambda r: r[2], reverse=True)
        return [p for p, _scene, _created in numbered + rest]

    def toggle_options(self, force=None):
        """Fold the column's configuration away, or bring it back.

        The first line - what the list *is* and the folders switch - stays: it answers "what am I looking at" rather than
        "how is it configured". Everything else (search, the button rows, the sort row, the hints) is configuration and
        costs vertical room the pictures want; measured, folding it returns ~150 px to the strip.
        """
        show = (not self._options_shown) if force is None else bool(force)
        self._options_shown = show
        group = [getattr(self, 'search_box', None), getattr(self, 'starred_chk', None), getattr(self, 'btns', None)]
        for widget in group:
            if widget is None or not widget.winfo_exists():
                continue
            if show:
                widget.grid()
            else:
                widget.grid_remove()
        # the folder bar has its own rule: it exists only when a plan does
        bar = getattr(self, 'folder_bar', None)
        if bar is not None and bar.winfo_exists():
            if show and self._plan:
                bar.grid()
            else:
                bar.grid_remove()
        if hasattr(self, 'options_btn'):
            self.options_btn.configure(text='\u25be options' if show else '\u25b8 options')

    def _sort_wins(self):
        return getattr(self, 'sort_var', None) is not None and self.sort_var.get() == 'Wins'

    def _sort_sequence(self):
        return getattr(self, 'sort_var', None) is not None and self.sort_var.get() == 'Sequence'

    def _scene_of(self, path):
        """(scene, position in that scene) for a picture, or None. Memoised: the sidecars are read once per refresh."""
        cache = getattr(self, '_scene_cache', None)
        if cache is None:
            cache = self._scene_cache = {}
        if path in cache:
            return cache[path]
        meta = read_meta(path) or {}
        scene, of = meta.get('scene'), meta.get('scene_of')
        value = (int(scene), int(of or 0)) if isinstance(scene, int) or (isinstance(scene, str) and scene.isdigit()) \
            else None
        cache[path] = value
        return value

    def _sequence_order(self, paths):
        """Film order: scene, then the picture's position within the scene, then recency. Unnumbered pictures go last."""
        def key(item):
            path, created = item
            scene = self._scene_of(path)
            return (0, scene[0], scene[1], '') if scene else (1, 0, 0, ''.join(chr(255 - ord(c)) for c in created[::-1]))
        ordered = [p for p, _c in sorted(
            ((p, (read_meta(p) or {}).get('created') or '') for p in paths), key=key)]
        return ordered

    def _resort(self):
        self._content_shown = BATCH_CONTENTS
        self.refresh_gallery()
        if getattr(self, '_active_folder', None) is not None:
            self._render_contents()

    def _visible_files(self):
        """The gallery as filtered: the whole folder, then search, then stars, favourites first."""
        rows = self._scan()
        files = [p for p, _m in rows]
        if self._plan and self._active_folder is not None:
            chosen = {r['path'] for r in self._plan['groups'][self._active_folder]['items']}
            files = [f for f in files if str(f) in chosen]
        needle = self.search_var.get().strip().lower()
        if needle:
            stamps = dict(rows)
            files = [f for f in files if needle in self._searchable(f, stamps.get(f))]
        if self.starred_only.get():
            files = [f for f in files if f.name in self._favorites]
        files.sort(key=lambda f: (f.name not in self._favorites, -f.stat().st_mtime))
        return files

    def _folders_view_active(self):
        """The strip lists folders when grouping is on, nothing is selected and nothing is filtered."""
        # The `_grouping` term is deliberately gone: during a re-group (1.2 s after every render) it made the strip paint
        # the 18 recent pictures and flip back to folders - the "content that vanishes" flash removed at startup,
        # reintroduced per render. While a plan exists, keep drawing folders; the placeholder covers "no plan yet".
        return (bool(self._plan)
                and not self.search_var.get().strip() and not self.starred_only.get())

    def _signature(self, path):
        """What makes two pictures different on a tile: the shape of their gradients *and* their colours.

        A difference hash alone was not enough, and the test proved it: four pictures in four different colours hashed
        identically, because a dHash compares neighbours and ignores absolute level. Collage and colour are both cheap, so
        both are kept - and the colour part is what tells a red render from a green one.
        """
        if path in self._hashes:
            return self._hashes[path]
        value = None
        thumb = thumbs_for([path]).get(path)
        if thumb:
            try:
                from PIL import Image as _I
                with _I.open(thumb) as im:
                    small = im.convert('RGB').resize((16, 16), _I.LANCZOS)
                    grey = small.convert('L').resize((9, 8), _I.LANCZOS)
                pixels = grey.tobytes()
                bits = 0
                for y in range(8):
                    for x in range(8):
                        bits = (bits << 1) | (1 if pixels[y * 9 + x] > pixels[y * 9 + x + 1] else 0)
                # four quadrant means, quantised: enough to separate a palette shift without being fooled by noise
                quadrants = []
                raw = small.tobytes()
                for qy in (0, 8):
                    for qx in (0, 8):
                        total = [0, 0, 0]
                        for y in range(qy, qy + 8):
                            for x in range(qx, qx + 8):
                                at = (y * 16 + x) * 3
                                for c in range(3):
                                    total[c] += raw[at + c]
                        quadrants.append(tuple(total[c] // 64 for c in range(3)))
                value = (bits, tuple(quadrants))
            except Exception:
                value = None
        self._hashes[path] = value
        return value

    def _different(self, a, b):
        """Are two signatures different enough to sit side by side on one tile?"""
        if a is None or b is None:
            return True                       # an unreadable thumbnail must not block the tile
        grey_bits = bin(a[0] ^ b[0]).count('1')
        colour_delta = max(max(abs(x - y) for x, y in zip(qa, qb)) for qa, qb in zip(a[1], b[1]))
        return grey_bits >= FOLDER_DISTINCT or colour_delta >= FOLDER_COLOUR

    def _sheet_paths(self, group):
        """The four pictures a folder's tile shows: the newest four that are visibly different from each other.

        Newest rather than oldest because a tile summarises what the folder *is now*; distinct because four variations of
        one render say nothing. When a folder genuinely holds fewer than four different pictures the newest four are shown
        anyway and the folder is counted in `sheets_all_alike` - a quiet fallback would hide a real property.
        Always newest-first, so a tile reads the same way whichever path produced it.
        """
        items = group['items']
        cache_key = (group['label'], len(items), items[-1]['path'] if items else '')
        cached = self._heads.get(cache_key)
        if cached is not None:
            return cached
        want = FOLDER_CELLS
        kept, signatures = [], []
        # Look past the newest handful when they are near-copies: with a higher bar the walk has to travel further into
        # the history to find four that qualify, which is the point - a spreadsheet of four one-pixel variations is not
        # a summary of the folder.
        for row in reversed(items[-FOLDER_SCAN:] if len(items) > FOLDER_SCAN else items):
            signature = self._signature(row['path'])
            if signature is not None and all(self._different(signature, other) for other in signatures):
                signatures.append(signature)
                kept.append(row['path'])
                if len(kept) == want:
                    break
        if len(kept) < want:
            kept = [r['path'] for r in reversed(items[-want:])]
            self.sheets_all_alike += 1
        self._heads[cache_key] = kept
        return kept

    def _folder_sheet(self, group, size, gutter=None):
        """A 2x2 contact sheet of a folder's newest four distinct pictures, built from the thumbnail cache.

        Four rather than nine because the tile has to sit two-up in the first column: a nine-up sheet demanded ~270 px
        and left room for one folder per row. One PhotoImage per folder instead of four keeps a 60-folder view cheap,
        and the sheet is memoised so scrolling or a redraw does not re-compose it.
        """
        from PIL import Image as _I
        head = tuple(self._sheet_paths(group))
        key = (group['label'], len(group['items']), size, head, gutter)
        hit = self._sheets.get(key)
        if hit is not None:
            return hit
        side = 2 if FOLDER_CELLS == 4 else 3
        cell = size // side
        # the gutter is what draws the 1 px line between the four pictures when the tile is selected
        sheet = _I.new('RGB', (size, size), gutter or self.theme.sheet_bg)
        paths = list(head)
        thumbs = thumbs_for(paths)
        for i, path in enumerate(paths):
            tp = thumbs.get(path)
            if not tp:
                continue
            try:
                with _I.open(tp) as im:
                    im = im.convert('RGB')
                    im.thumbnail((cell - 2, cell - 2), _I.LANCZOS)
                    sheet.paste(im, ((i % side) * cell + (cell - im.width) // 2,
                                     (i // side) * cell + (cell - im.height) // 2))
            except Exception:
                continue
        self._sheets[key] = sheet
        if len(self._sheets) > 400:
            self._sheets.clear()
        return sheet

    def _render_contents(self):
        """The open folder's pictures, in the pane beside the folder list - and nothing to "close" but another click."""
        from PIL import ImageTk
        if self._active_folder is None or not self._plan or self._active_folder >= len(self._plan['groups']):
            self._active_folder = None
            self.contents_holder.grid_remove()
            return
        group = self._plan['groups'][self._active_folder]
        pane_pos = self._scroll_pos(self.contents_canvas)
        try:
            had_focus = self.root.focus_get() is self.contents_canvas
        except Exception:
            had_focus = False
        self._rebuilding = True
        self.contents_holder.grid()
        for w in self.contents.winfo_children():
            w.destroy()
        self._content_widgets = {}
        width = self.contents_canvas.winfo_width()
        if width <= 1:
            width = CONTENTS_W
        # three per row is the floor the screen asks for; wider pane, more columns, bigger tiles either way
        cols = max(3, int((width - 8) // (STRIP_TILE_MIN + 6)))
        tile_px = max(48, (width - 8) // cols - 6)
        items = list(group['items'])
        # Live view: the newest pictures are outside every folder by design, so a render made while this folder is open
        # would otherwise never appear in it. `live_append` answers "what else would this folder accept?" from the same
        # corpus statistics, newest first, and marks them so the folder's own membership stays honest.
        try:
            import group_prompts as _gp
            fresh = _gp.live_append(group, self._plan) if self._plan else []
        except Exception:
            fresh = []
        fresh_rows = []
        for row in fresh:
            row = dict(row)
            row['fresh'] = True
            fresh_rows.append(row)
        # newest first: a fresh render belongs at the top of the folder it just joined, and appending it after 200
        # members put it past the first batch, where paging could never reach it
        items = fresh_rows + items
        if self._sort_sequence():
            order = self._sequence_order([r['path'] for r in items])
            rank = {p: i for i, p in enumerate(order)}
            items.sort(key=lambda r: rank.get(r['path'], len(items)))
        elif not self._sort_wins():
            # newest on top: a folder is read from its most recent picture down, which is also where a fresh render
            # lands. The plan hands members over oldest-first so the folder order stays stable; the pane re-sorts them.
            items.sort(key=lambda r: r.get('created') or '', reverse=True)
        self._content_items = items
        # the caption says which folder this is and how much is in it, so the column identifies itself
        title = (group.get('title') or group.get('label') or '').strip()
        # members and live-view arrivals are two different numbers: the folder *has* N, and M of the newest pictures are
        # being offered because they would join it. Adding them into one total read as if the folder held them.
        members = max(len(items) - len(fresh_rows), 0)
        caption = '%s  -  %d picture%s' % (title, members, '' if members == 1 else 's')
        if fresh_rows:
            caption += ', %d new' % len(fresh_rows)
        self.contents_title.configure(text=caption, wraplength=max(200, width - 6))
        if self._sort_wins():
            import pair_rank
            order = pair_rank.sort_by_wins([r['path'] for r in items])
            pos = {p: i for i, p in enumerate(order)}
            items.sort(key=lambda r: pos.get(r['path'], len(items)))
        thumbs = thumbs_for([r['path'] for r in items])
        order = []                                  # the pane's own display order, for its keyboard
        for i, row in enumerate(items[:self._content_shown]):
            tp = thumbs.get(row['path'])
            if not tp:
                continue
            try:
                with Image.open(tp) as im:
                    im.thumbnail((tile_px, tile_px), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(im)
            except Exception:
                continue
            cell = ttk.Frame(self.contents)
            cell.grid(row=i // cols, column=i % cols, padx=1, pady=1)
            lab = self._tile_widget(cell, row['path'], photo, pane=True)
            lab.pack()
            order.append(Path(row['path']))
            # the corner badge: how big this file is, on the three bands - tomato red 64 kB or less, amber between,
            # medium green 1 MB or more. A pixel font because at 8 px an antialiased face is unreadable.
            try:
                bytes_on_disk = Path(row['path']).stat().st_size
            except Exception:
                bytes_on_disk = 0
            if bytes_on_disk:
                background, foreground = size_band(bytes_on_disk)
                badge = tk.Label(cell, text=human_size(bytes_on_disk), font=PIXEL_FONT,
                                 background=background, foreground=foreground, bd=0, padx=2, pady=0)
                badge.place(relx=1.0, rely=1.0, anchor='se', x=-2, y=-2)
                # a badge sits over the thumbnail, so it must not swallow the click that selects the picture
                badge.bind('<Button-1>', lambda e, q=row['path']: self._on_tile_click(e, q))
                self._tip(badge, '%s\n%d bytes' % (Path(row['path']).name, bytes_on_disk))
            try:
                import pair_rank
                mark = pair_rank.badge(row['path'])
            except Exception:
                mark = ''
            if mark:
                ttk.Label(cell, text=mark, font=('Segoe UI', 7), foreground=self.theme.win_mark).pack()
            elif row.get('fresh'):
                ttk.Label(cell, text='new', font=PIXEL_FONT, foreground=self.theme.accent).pack()
            self._content_widgets[Path(row['path'])] = lab
        self._content_order = order
        self._content_cols = cols
        self._rebuilding = False
        if had_focus:
            # a re-render must not hand the keyboard to the strip: the reader was working in this pane
            self.root.after_idle(lambda: self.contents_canvas.focus_set())
        self.root.after_idle(self._on_contents_resize)
        if pane_pos:
            self._restore_scroll(self.contents_canvas, pane_pos)
        else:
            self.contents_canvas.yview_moveto(0)

    def _on_strip_scrollbar(self, *args):
        self.gal_canvas.yview(*args)
        self._maybe_more('strip')

    def _on_contents_resize(self, event=None):
        if getattr(self, '_rebuilding', False):
            return
        width = self.contents_canvas.winfo_width()
        cols = max(3, int((max(width, 1) - 8) // (STRIP_TILE_MIN + 6)))
        if cols != getattr(self, '_contents_cols_seen', None):
            self._contents_cols_seen = cols
            if getattr(self, '_content_widgets', None):
                self._render_contents()      # the pane is three-up at this width; reflow to fill it
                return
        box = self.contents_canvas.bbox('all')
        if box:
            self.contents_canvas.configure(scrollregion=box)
        self.contents_canvas.itemconfigure(self._contents_item, width=self.contents_canvas.winfo_width())
        box = self.contents_canvas.bbox('all')
        need = bool(box) and (box[3] - box[1]) > self.contents_canvas.winfo_height() + 1
        if need:
            self.contents_bar.grid()
        else:
            self.contents_bar.grid_remove()

    def _maybe_more(self, which):
        """Infinite scrolling: at the bottom of a list, append the next batch."""
        if which == 'strip':
            low, high = self.gal_canvas.yview()
            if high < 0.995:
                return
            if self._folders_view_active():
                if self._folder_shown >= len(self._plan['groups']):
                    return
                self._folder_shown += BATCH_FOLDERS
                self._render_folder_tiles()
            else:
                if self._shown >= len(self._files):
                    return
                self._shown += BATCH_TILES
                self.refresh_gallery()
        else:
            low, high = self.contents_canvas.yview()
            if high < 0.995 or self._active_folder is None:
                return
            if self._content_shown >= len(getattr(self, '_content_items', []) or []):
                return
            self._content_shown += BATCH_CONTENTS
            self._render_contents()

    def _render_folder_tiles(self):
        """Folder tiles, two per row: a 4-up (2x2) sheet, the full name wrapped, and the picture count.

        Two per row is the point of the 4-up sheet - at 3x3 one tile needed ~270 px and the column held exactly one,
        which is what the 2026-10-27 screenshot showed. The tile width is derived from the column, so both tiles always
        fill it instead of leaving a gutter.
        """
        from PIL import ImageTk
        for w in self.recent.winfo_children():
            w.destroy()
        self._tile_widgets = {}
        width = max(190, self.gal_canvas.winfo_width() - 10)
        cols = 2 if width >= 2 * (STRIP_TILE_MIN + 8) else 1
        self._folder_cols = cols          # the keyboard reads the same number the renderer just used
        sheet_px = max(STRIP_TILE_MIN, (width - 8 - 6 * cols) // cols)
        for index, group in enumerate(self._plan['groups'][:self._folder_shown]):
            # The open folder is drawn in reverse video: a 1 px line around the sheet and between its four pictures (the
            # sheet's own gutter, in the same colour), with the caption on an inverted bar. Strong enough to find at a
            # glance, quiet enough to sit in a grid of tiles.
            selected = (self._active_folder == index)
            ink = self.theme.text if selected else self.theme.border
            plate = self.theme.text if selected else self.theme.sheet_bg
            # A slide mount: one 1 px frame holds the sheet *and* its caption together, so a tile reads as one object
            # instead of a picture with loose text under it. Every tile carries the frame - the border colour when idle,
            # the text colour when open - and the tiles are spaced on both axes so they are separate objects, not a grid.
            tile = tk.Frame(self.recent, background=ink, padx=1, pady=1)
            tile.grid(row=index // cols, column=index % cols, padx=5, pady=5, sticky='n')
            body = tk.Frame(tile, background=self.theme.window, padx=3, pady=3)
            body.pack(fill='both', expand=True)
            photo = ImageTk.PhotoImage(self._folder_sheet(group, sheet_px, gutter=plate if selected else None))
            pic = tk.Label(body, image=photo, bd=0, relief='flat', cursor='hand2', background=plate)
            pic.image = photo
            pic.pack()
            # the count belongs to the image block, so it sits between the pictures and the name
            meta = tk.Label(body, text='%d pictures' % len(group['items']), anchor='w',
                            font=('Segoe UI', 7),
                            foreground=self.theme.window if selected else self.theme.muted,
                            background=self.theme.text if selected else self.theme.window)
            meta.pack(fill='x', pady=(2, 0))
            full_title = group.get('title') or group['label']
            title = tk.Label(body, text=shorten_title(full_title), wraplength=sheet_px - 8,
                             justify='left', anchor='w', font=('Segoe UI', 8),
                             foreground=self.theme.window if selected else self.theme.text,
                             background=self.theme.text if selected else self.theme.window)
            title.pack(fill='x', pady=(1, 0))
            self._tip(title, full_title)
            for w in (tile, body, pic, title, meta):
                w.bind('<Button-1>', lambda e, i=index: self._folder_clicked(i))
        self._paint_folder_cursor()
        self.root.after_idle(self._on_strip_resize)

    def _folder_clicked(self, index):
        """A click on a folder tile: open it, park the keyboard cursor on it, and hand the strip the arrows.

        `select_folder` is also called by the prediction and by the browser, so the focus belongs to the click, not to
        the selection - those other callers must not pull the keyboard out of the prompt box.
        """
        self._nav_cur = index
        self.select_folder(index)
        try:
            self.gal_canvas.focus_set()
        except Exception:
            pass

    def _strip_metrics(self):
        """Columns and tile size for the strip, from the width it actually has.

        The strip used to paint fixed 92 px tiles and take as many columns as fitted, which wasted the leftover width
        on a wide column. Now the column count follows the width and the tile *grows* into what is left, so the list
        uses the whole column instead of leaving a gutter.
        """
        try:
            w = self.gal_canvas.winfo_width()
        except Exception:
            w = 0
        if w <= 1:
            w = RECENT_COLS * (RECENT_PX + 8)
        pad = 4
        cols = max(1, int((w - pad) // (STRIP_TILE_MIN + pad)))
        tile = max(STRIP_TILE_MIN, (w - pad) // cols - pad)
        return cols, tile

    def _strip_cols(self):
        return self._strip_metrics()[0]

    def refresh_gallery(self, select=None):
        strip_pos = self._scroll_pos(self.gal_canvas)
        self._rebuilding = True          # <Configure> mid-rebuild would see an empty container and clamp the view
        try:
            self._refresh_gallery(select)
        finally:
            self._rebuilding = False
        # A folder that has just been opened asks to be brought into view, and that must beat restoring the old
        # position: the strip re-flows and rebuilds right after a selection, and the restore was undoing the scroll.
        pending = getattr(self, '_see_pending', None)
        if pending is not None:
            self._see_pending = None
            self.root.after_idle(lambda i=pending: self._scroll_folder_into_view(i))
        else:
            self._restore_scroll(self.gal_canvas, strip_pos)

    def _refresh_gallery(self, select=None):
        self._scene_cache = {}
        self._hashes = {}                 # a new or deleted picture invalidates its hash and the heads that used it
        self._heads = {}
        everything = self._all_files()
        if self.base_path is not None and not any(f.name == self.base_path.name for f in everything):
            self.clear_base()
        # a deleted or moved file must not stay selected
        self.selected = [p for p in self.selected if any(f.name == Path(p).name for f in everything)]
        files = self._visible_files()
        if self._sort_wins():
            import pair_rank
            files = pair_rank.sort_by_wins(files)
        elif self._sort_sequence():
            files = self._sequence_order(files)
        self._files = files
        # a search turns the strip into a result list, so it shows matches from the whole folder, not 18 of them
        filtering = bool(self.search_var.get().strip()) or bool(self.starred_only.get())
        cap = FILTER_CAP if filtering else max(RECENT_N, min(len(files), self._shown))
        recent = files[:cap]
        if filtering:
            label = 'Matches (%d of %d)' % (len(recent), len(files))
        else:
            label = ('Recent (%d)' % len(recent) if len(recent) >= len(files)
                     else 'Recent (%d of %d)' % (len(recent), len(files)))
        if self._favorites:
            label += '  \u00b7  %d \u2605' % len(self._favorites)
        needle = self.search_var.get().strip()
        if needle:
            label += '  \u00b7  \u201c%s\u201d' % needle
        if getattr(self, '_grouping', False) and not self._plan and files:
            # Folders are on and the plan is not in yet: say so in place of the pictures rather than painting 18
            # thumbnails that are about to be replaced.
            self.gal_title.configure(text='Grouping prompts\u2026  \u00b7  %d pictures' % len(files))
            for w in self.recent.winfo_children():
                w.destroy()
            self._tile_widgets = {}
            if hasattr(self, 'grouping_badge'):
                self.grouping_badge.configure(text='grouping %d pictures\u2026' % len(files))
                self.grouping_badge.place(relx=0.5, rely=0.5, anchor='center')
            return
        if hasattr(self, 'grouping_badge'):
            self.grouping_badge.place_forget()
        if self._folders_view_active():
            total = len(self._plan['groups'])
            shown = min(total, self._folder_shown)
            self.gal_title.configure(text='Folders (%d%s)  \u00b7  %d pictures'
                                     % (shown, '' if shown >= total else ' of %d' % total,
                                        sum(len(g['items']) for g in self._plan['groups'])))
            self._render_folder_tiles()
            self._paint()          # the folders branch used to return without painting, so a pane click left the
            return                 # action row disabled while `selected` held the path
        self.gal_title.configure(text=label)
        cols, tile_px = self._strip_metrics()
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
                im.thumbnail((tile_px, tile_px), Image.LANCZOS)
                ph = ImageTk.PhotoImage(im)
            except Exception:
                continue
            self._thumbs[p] = ph
            lab = self._tile_widget(self.recent, p, ph)
            lab.grid(row=i // cols, column=i % cols, padx=1, pady=1)
            self._tile_widgets[Path(p)] = lab
        if files:
            self.starters.grid_remove()
        else:
            self.starters.grid()
        self._paint()

    def _on_strip_resize(self, event=None):
        """Debounce: a rebuild emits a burst of <Configure> events while the container is half-built.

        Applying them immediately made the scroll region collapse to a few pixels, and Tk then clamped the view to the
        top - which is what lost the reader's place when a picture in the other column was selected.
        """
        if getattr(self, '_strip_job', None):
            try:
                self.root.after_cancel(self._strip_job)
            except Exception:
                pass
        self._strip_job = self.root.after(50, self._apply_strip_resize)

    def _apply_strip_resize(self):
        self._strip_job = None
        cols, tile = self._strip_metrics()
        if (cols, tile) != getattr(self, '_strip_cols_seen', None):
            self._strip_cols_seen = (cols, tile)
            if self._tile_widgets:
                self.root.after_idle(self.refresh_gallery)   # the tile grid follows the width
                return
        box = self.gal_canvas.bbox('all')
        if box:
            self.gal_canvas.configure(scrollregion=box)
        self.gal_canvas.itemconfigure(self._gal_item, width=self.gal_canvas.winfo_width())
        content_h = (box[3] - box[1]) if box else 0
        need = content_h > self.gal_canvas.winfo_height() + 1
        if need:
            self.gal_bar.grid()
        else:
            # Hide the bar, but never move the view: a <Configure> arrives mid-rebuild while the tile container is
            # momentarily a few pixels tall, and treating that as "everything fits" snapped the list back to the top
            # (reported 2026-10-27). Tk clamps the view anyway when the content really is shorter than the viewport.
            self.gal_bar.grid_remove()

    def _strip_has_focus(self):
        """True only when the keyboard is actually on the strip, not merely because a binding exists."""
        w = self.root.focus_get()
        if w is None:
            return False
        if w is self.gal_canvas:
            return True
        try:
            return str(w).startswith(str(self.gal_canvas)) or w in self._tile_widgets.values()
        except Exception:
            return False

    def _strip_cells(self):
        """(keys, cols, tiles): the strip's keyboard order, its column count, and one widget per cell.

        The strip has two modes and they behave differently enough to be worth naming here: with folders on the cells
        are folder indexes and the tile frames, otherwise they are Paths and the tile labels. Both report the column
        count their renderer actually used, never a recomputation that could disagree with the layout on screen.
        """
        if self._folders_view_active():
            tiles = self._folder_tiles()
            return list(range(len(tiles))), max(1, getattr(self, '_folder_cols', 2) or 1), tiles
        keys = [p for p in self._files[:RECENT_N] if p in self._tile_widgets]
        tiles = [self._tile_widgets[p] for p in keys]
        return keys, max(1, self._strip_cols()), tiles

    def _folder_tiles(self):
        """The folder tiles that are laid out right now, in index order (a grid_info-less child is not a cell)."""
        try:
            return [w for w in self.recent.winfo_children() if w.grid_info()]
        except Exception:
            return []

    def _nav_current(self):
        """The cell the keyboard is on: the cursor, else the last thing selected, else nothing."""
        keys, _cols, _tiles = self._strip_cells()
        if self._nav_cur in keys:
            return self._nav_cur
        if self.selected and self.selected[-1] in keys:
            return self.selected[-1]
        return None

    def _nav_move_key(self, event, move):
        """The bound handler: arrows only reach the strip when the strip has the keyboard."""
        if self._typing() or not self._strip_has_focus():
            return None
        return self._nav_move(move, extend=bool(getattr(event, 'state', 0) & 0x0001))

    def _nav_key(self, _event, dx, dy):
        """Arrow keys reach the gallery only from the gallery: never while the prompt box has the keyboard."""
        if self._typing() or not self._strip_has_focus():
            return None
        return self._nav(dx, dy, extend=bool(getattr(_event, 'state', 0) & 0x0001))

    def _nav_toggle_key(self, event=None):
        if self._typing() or not self._strip_has_focus():
            return None
        return self._nav_toggle()

    def _nav_open_key(self, event=None):
        if self._typing() or not self._strip_has_focus():
            return None
        return self._nav_open()

    def _nav(self, dx, dy, extend=False):
        """Move the selection one tile, so the strip is usable without the mouse.

        Kept in (dx, dy) because that is how the click paths and the suite describe a step; the movement itself is
        `_nav_move`, which is the same function the two full-screen grids run.
        """
        move = {(1, 0): 'right', (-1, 0): 'left', (0, 1): 'down', (0, -1): 'up'}.get((dx, dy), 'none')
        return self._nav_move(move, extend=extend)

    def _nav_move(self, move, extend=False):
        """One layout-grid movement in the strip, whichever mode it is showing.

        In the picture list the cursor and the selection are the same thing - that is how the strip has always shown
        where you are, and shift extends the range from the anchor the way shift-click does. In folder mode the strip
        has no multi-selection (its one selection is the open folder), so a plain arrow only rings the tile and Enter
        opens it; opening a folder on every arrow press would re-render the contents pane for movement alone.
        """
        keys, cols, tiles = self._strip_cells()
        if not keys:
            return 'break'
        cur = self._nav_current()
        index = keys.index(cur) if cur in keys else -1
        new = grid_move(index, len(keys), cols, move, rows=self._strip_visible_rows(tiles, cols))
        if new is None:
            return 'break'
        key = keys[new]
        folders = self._folders_view_active()
        if not folders and extend and self._anchor in keys:
            i, j = keys.index(self._anchor), new
            if i > j:
                i, j = j, i
            self.selected = list(keys[i:j + 1])
        elif not folders:
            self.selected = [key]
            self._anchor = key
        self._nav_cur = key
        if folders:
            self._paint_folder_cursor()
        else:
            self._paint()
        self._see_strip_cell(tiles[new], 'top' if move in ('pageup', 'pagedown') else 'nearest')
        if new >= len(keys) - 1:
            self._maybe_more('strip')       # the last laid-out cell: pull the next batch in, as scrolling would
        return 'break'

    def _strip_visible_rows(self, tiles, cols):
        """Rows the strip's viewport shows, measured from two laid-out tiles rather than assumed."""
        pitch = 0
        if len(tiles) > cols:
            try:
                pitch = tiles[cols].winfo_y() - tiles[0].winfo_y()
            except Exception:
                pitch = 0
        if pitch <= 0 and tiles:
            try:
                pitch = tiles[0].winfo_height() + 2
            except Exception:
                pitch = 0
        return grid_visible_rows(self.gal_canvas.winfo_height(), pitch)

    def _see_strip_cell(self, tile, align='nearest'):
        """Scroll the strip so a cell under the keyboard is visible, and only then.

        The invariant the resize handlers protect - a resize never moves the view - also applies here: a cell that is
        already fully visible is left alone, so pressing a key that does not change rows never nudges the list.
        """
        canvas = self.gal_canvas
        try:
            self.recent.update_idletasks()
            canvas.update_idletasks()
        except Exception:
            return
        total = max(self.recent.winfo_height(), 0)
        view_h = canvas.winfo_height()
        if total <= 0 or view_h <= 0:
            return
        canvas.configure(scrollregion=(0, 0, max(1, canvas.winfo_width()), total))
        top, height = tile.winfo_y(), max(1, tile.winfo_height())
        view_top = canvas.canvasy(0)
        if align == 'top':
            target = top - 8.0
        elif top < view_top - 1:
            target = top - 8.0
        elif top + height > view_top + view_h + 1:
            target = top + height + 8.0 - view_h
        else:
            return
        target = max(0.0, min(float(max(0, total - view_h)), float(target)))
        if abs(target - view_top) < 1.0:
            return
        canvas.yview_moveto(max(0.0, min(1.0, target / float(total))))

    def _paint_folder_cursor(self):
        """Ring the folder tile the keyboard is on, without repainting a single contact sheet.

        The folder tiles already use their 1 px frame for "this folder is open", so the cursor is a second, outer ring
        in the accent - movement is visible without ever being mistaken for the open folder.
        """
        if not self._folders_view_active():
            return
        for index, tile in enumerate(self._folder_tiles()):
            try:
                current = (self._nav_cur == index)
                tile.configure(highlightthickness=2 if current else 0,
                               highlightbackground=self.theme.accent)
            except Exception:
                pass

    def _nav_toggle(self, event=None):
        """Space toggles the current cell; a folder tile has nothing to toggle, so it opens instead."""
        cur = self._nav_current()
        if cur is None:
            return self._nav(0, 0)
        if self._folders_view_active():
            return self._nav_open()
        self._nav_cur = cur
        if cur in self.selected:
            self.selected.remove(cur)
        else:
            self.selected.append(cur)
        self._paint()
        return 'break'

    def _nav_open(self, event=None):
        """Enter activates the current cell: open the folder, or load the picture the strip is on.

        A folder tile's activation is `select_folder`, exactly what clicking it does; a picture loads into the big view
        through `select_file`, which is the strip's existing open path (it looks, and restores the prompt - it arms no
        edit, as a click does not).
        """
        cur = self._nav_current()
        if self._folders_view_active() and cur is not None and self._plan:
            if 0 <= cur < len(self._plan['groups']):
                self.select_folder(cur)
                return 'break'
        if cur is not None:
            self.select_file(cur)
        elif self.selected:
            self.select_file(self.selected[-1])
        return 'break'

    # -- the new actions -----------------------------------------------------
    def _steps_ceiling(self, model):
        """The step count a model was distilled or documented for. Measured: turbo and klein are unchanged above these."""
        return int((zg.MODELS.get(model) or {}).get('max_steps') or 0)

    def _clamp_steps(self, model, steps):
        """Reduce an impossible step count to the model's ceiling, and say so.

        The picker offers up to 50 and nothing stopped a user choosing it: 34 pictures in the gallery were drawn at 50
        steps on a 4- or 9-step model, which is up to twelve times the compute for no measured gain. Silence is the
        wrong fix in both directions - the control is set to what will actually run, and the hint says why.
        """
        ceiling = self._steps_ceiling(model)
        if ceiling and steps > ceiling:
            if hasattr(self, 'steps_var'):
                self.steps_var.set(str(ceiling))
            try:
                self.hint.configure(text='%s draws in %d steps - its measured ceiling - so %d was reduced to %d; '
                                         'more buys nothing on a distilled model' % (model, ceiling, steps, ceiling))
            except Exception:
                pass
            return ceiling
        return steps

    def _on_model_change(self, event=None):
        """A profile brings its own step count; ALL brings every profile's ceiling and the largest size.

        The size and step boxes are disabled rather than ignored while ALL is chosen: the sweep overrides both per
        model, and a control that looks editable but is not is worse than one that says so.
        """
        choice = self.model_var.get()
        if choice == zg.ALL_MODEL:
            plan = zg.all_plan()
            self.size_var.set(str(zg.QUALITY_SIZE))
            self.steps_var.set(str(max(steps for _n, steps, _s in plan)))
            for widget in (getattr(self, 'size_cb', None), getattr(self, 'steps_cb', None)):
                if widget is not None:
                    widget.state(['disabled'])
            self._update_banner()
            return
        for widget in (getattr(self, 'size_cb', None), getattr(self, 'steps_cb', None)):
            if widget is not None:
                widget.state(['!disabled'])
        try:
            asked = int(self.steps_var.get())
        except (TypeError, ValueError):
            asked = 0
        if asked:
            self._clamp_steps(choice, asked)
        prof = zg.MODELS.get(choice) or {}
        top = prof.get('max_steps') or prof.get('steps')
        if top:
            self.steps_var.set(str(top))
        self._update_banner()
        self.hint.configure(text='%s: %s steps (its maximum) at cfg %.1f - the first image loads that checkpoint, '
                                 'about 10-30 s; lower the steps if you are in a hurry'
                            % (self.model_var.get(), top or '?', prof.get('cfg', 1.0)))

    def set_sidebar(self, show):
        """Fold the gallery away so the picture can use the width."""
        self._sidebar = bool(show)
        if self._sidebar:
            self.rail.grid_remove()
            self.gal.grid()
            self.root.columnconfigure(0, minsize=210)
        else:
            self.gal.grid_remove()
            self.rail.grid()
            self.root.columnconfigure(0, minsize=44)
        self._persist()

    def toggle_advanced(self):
        self._adv_shown = not self._adv_shown
        if self._adv_shown:
            self.advanced.grid()
            self.adv_btn.configure(text='Advanced \u25be')
        else:
            self.advanced.grid_remove()
            self.adv_btn.configure(text='Advanced \u25b8')

    @staticmethod
    def _parse_values(spec, n, param):
        """Parse '1,2,3' or '1..4x6' into a list; blank spreads the range over n tiles."""
        spec = (spec or '').strip()
        if not spec:
            if param == 'cfg':
                return [round(1.0 + 3.0 * i / max(1, n - 1), 2) for i in range(n)]
            if param == 'steps':
                return [max(1, int(round(2 + 10.0 * i / max(1, n - 1)))) for i in range(n)]
            return [i * 1000 for i in range(n)]
        try:
            if '..' in spec:
                rng, _, count = spec.partition('x')
                lo, _, hi = rng.partition('..')
                lo, hi = float(lo), float(hi)
                k = int(count) if count else n
                k = max(1, k)
                vals = [lo + (hi - lo) * i / max(1, k - 1) for i in range(k)]
            else:
                vals = [float(v) for v in spec.replace(' ', '').split(',') if v]
        except Exception:
            return []
        if param == 'steps':
            vals = [max(1, int(round(v))) for v in vals]
        elif param == 'cfg':
            vals = [round(v, 2) for v in vals]
        else:
            vals = [int(v) for v in vals]
        return vals

    def _style_suffix(self, name):
        if name in self._custom_styles:
            return self._custom_styles[name]
        return STYLES.get(name, '')

    def style_conflicts(self, prompt, style_name):
        """Character ranges in the prompt that name a medium the selected style also names - or another one.

        Returns [] when no style is chosen, when the style's own suffix already says the word (a photo style with the
        word "photo" is not a conflict, it is agreement), or when the prompt names no medium at all.
        """
        suffix = (self._style_suffix(style_name) or '').lower()
        if not suffix:
            return []
        low = (prompt or '').lower()
        found = []
        for word in STYLE_CONFLICT_WORDS:
            start = 0
            while True:
                at = low.find(word, start)
                if at < 0:
                    break
                end = at + len(word)
                whole_word = (at == 0 or not low[at - 1].isalnum()) and (end >= len(low) or not low[end].isalnum())
                overlaps = any(a <= at < b for a, b in found)
                if whole_word and not overlaps and word not in suffix:
                    found.append((at, end))
                start = end
        return sorted(found)

    def _mark_conflicts(self, spans, prompt=None):
        """Light the words up in the prompt box itself, so "remove" is a visible promise, not an abstraction."""
        if not hasattr(self, 'text'):
            return
        try:
            self.text.tag_remove('conflict', '1.0', 'end')
        except Exception:
            return
        source = prompt if prompt is not None else self.text.get('1.0', 'end').rstrip('\n')
        for start, end in spans:
            try:
                self.text.tag_add('conflict', '1.0 + %dc' % start, '1.0 + %dc' % end)
            except Exception:
                pass
        if spans:
            self.text.see('1.0 + %dc' % spans[0][0])

    def _strip_spans(self, prompt, spans):
        """The prompt with those ranges taken out, and the punctuation they leave behind tidied."""
        out = prompt
        for start, end in sorted(spans, reverse=True):
            out = out[:start] + out[end:]
        out = re.sub(r'\s{2,}', ' ', out)
        out = re.sub(r'\s+,', ',', out)
        out = re.sub(r',\s*,', ',', out)
        return out.strip(' ,')

    def _focus_prompt(self):
        """Put the keyboard in the prompt box, and record that as the app's intent.

        `focus_set` is a request: in a window that is not active (a test, a background run) it does nothing observable, so
        the intent is kept as state too - the honest evidence that "revise manually" really means the prompt.
        """
        self._focus_target = getattr(self, 'text', None)
        try:
            self.text.focus_set()
            self.text.mark_set('insert', 'end')
        except Exception:
            pass

    def ask_style_conflict(self, style_name, prompt, spans):
        """Remove, keep, or stop and edit. Modal: the caller needs the answer before it dispatches."""
        words = sorted({prompt[a:b] for a, b in spans})
        win = tk.Toplevel(self.root)
        win.title('Style and prompt disagree')
        win.transient(self.root)
        ttk.Label(win, justify='left', wraplength=460,
                  text=('The style "%s" is applied, and the prompt names %s.\n\n'
                        'Both would be sent, so the picture would be asked to be two mediums at once.\n'
                        'The words are highlighted in the prompt. What should happen?' %
                        (style_name, ', '.join('"%s"' % w for w in words)))).pack(anchor='w', padx=14, pady=(14, 8))
        choice = {'value': 'revise'}
        row = ttk.Frame(win)
        row.pack(fill='x', padx=14, pady=(4, 14))

        def pick(value):
            choice['value'] = value
            win.destroy()

        ttk.Button(row, text='Remove them and generate', command=lambda: pick('remove')).pack(side='left')
        ttk.Button(row, text='Keep and generate', command=lambda: pick('keep')).pack(side='left', padx=(6, 0))
        # the fourth way out: the prompt's own medium wins and the style suffix is dropped, which is the other half of
        # "remove" - removing the words keeps the style, this keeps the words
        ttk.Button(row, text='Clear style and proceed', command=lambda: pick('clear_style')).pack(side='left', padx=(6, 0))
        ttk.Button(row, text='Revise manually', command=lambda: pick('revise')).pack(side='left', padx=(6, 0))
        win.protocol('WM_DELETE_WINDOW', lambda: pick('revise'))
        win.grab_set()
        win.focus_set()
        self.root.wait_window(win)
        return choice['value']

    def _apply_style(self, prompt, style_name):
        """Append the style suffix exactly once: a prompt that already ends with it is left alone.

        Rerendering is the normal case - vary, hires and a gallery click all restore the prompt that produced an
        image, and that prompt already carries the suffix the first run appended.
        """
        text = (prompt or '').rstrip()
        suffix = self._style_suffix(style_name)
        if not suffix:
            return text
        if text.endswith(suffix):
            return text
        return (text + ', ' + suffix) if text else suffix

    def _strip_style(self, prompt, style_name):
        """The inverse: what the box should hold so the suffix is not applied twice."""
        text = (prompt or '').rstrip()
        suffix = self._style_suffix(style_name)
        if suffix and text.endswith(suffix):
            text = text[:-len(suffix)].rstrip().rstrip(',').rstrip()
        return text

    def _meta_of_current(self):
        return (read_meta(self._current) if self._current else None) or {}

    def rephrase(self):
        """Swap the prompt's words for synonyms. Offline and deterministic: pressing again walks the variants."""
        text = self.text.get('1.0', 'end').strip()
        if not text:
            self.hint.configure(text='nothing to rephrase yet - type a prompt first')
            return
        # each press re-derives from the original wording, so the variants cycle instead of collapsing into each other
        base = getattr(self, '_rephrase_base', None)
        if base is None or text != getattr(self, '_rephrase_last', None):
            base = text
            self._rephrase_base = base
            self._rephrase_turn = 0
        self._rephrase_turn = getattr(self, '_rephrase_turn', 0) + 1
        new, changed, detail = rephrase_text(base, self._rephrase_turn,
                                             intensity=self.rephrase_intensity.get(),
                                             fuzziness=self.rephrase_matching.get())
        if not changed:
            known = has_synonyms(base, fuzziness=self.rephrase_matching.get())
            if known:
                # the cycle has come all the way round: the original wording is one of the options, so write it back.
                # The turn is deliberately NOT rewound - the next press continues past the wrap into the cycle again.
                self.text.delete('1.0', 'end')
                self.text.insert('1.0', base)
                self._rephrase_last = base
                self.hint.configure(text='back to the original wording - %d word%s have synonyms; press again to walk '
                                         'the cycle from the start'
                                    % (known, '' if known == 1 else 's'))
                return
            self._rephrase_turn -= 1
            self.hint.configure(text='no known synonyms in that prompt - try different words, or enhance with a '
                                     'local model')
            return
        self._rephrase_last = new
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', new)
        period = cycle_period(base)
        left = (period - self._rephrase_turn % period) if period else 0
        # say the denominator: a long prompt has only a handful of words the dictionary knows, and a press that lands
        # back on the originals for some of them looks like a failure unless the total is on screen
        known = has_synonyms(base, fuzziness=self.rephrase_matching.get())
        how = ', '.join('%d %s' % (v, k) for k, v in (detail.get('matched') or {}).items() if v) or 'exact'
        self.hint.configure(text='rephrased %d of %d word%s that have synonyms (%s) - press again for another variant%s'
                                 % (changed, known, '' if known == 1 else 's', how,
                                    '; your own wording returns in %d press%s' % (left, '' if left == 1 else 'es')
                                    if period and left else ''))

    def save_style(self):
        """Store the current prompt as a reusable style, the way A1111 keeps styles.csv."""
        current = self.text.get('1.0', 'end').strip()
        if not current:
            self.hint.configure(text='type a prompt first; the style saves the words after the subject')
            return
        name = simpledialog.askstring('Save style', 'Name for this style:', parent=self.root)
        if not name:
            return
        name = name.strip().lower()[:24]
        self._custom_styles[name] = current
        self._persist()
        self.style_cb.configure(values=list(STYLES) + sorted(self._custom_styles))
        self.style_var.set(name)
        self.hint.configure(text='style "%s" saved (%d bytes of prompt)' % (name, len(current)))

    def describe(self):
        """Caption the current picture with a local vision model, so the prompt can start from what is there."""
        if self._current is None:
            self.hint.configure(text='nothing to describe yet')
            return
        if not self._local_llm_url():
            return
        import base64
        raw = Path(self._current).read_bytes()
        b64 = base64.b64encode(raw).decode('ascii')

        def work(url):
            body = json.dumps({'model': 'local', 'temperature': 0.4, 'max_tokens': 160, 'messages': [
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': 'Describe this image as a single image-generation prompt, '
                                             'under 40 words. Reply with the prompt only.'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}}]}]}).encode()
            try:
                req = urllib.request.Request(url + '/chat/completions', data=body,
                                             headers={'Content-Type': 'application/json'})
                data = json.loads(urllib.request.urlopen(req, timeout=90).read())
                self.ui_events.put(('described', data['choices'][0]['message']['content'].strip()))
            except Exception as e:
                self.ui_events.put(('enhance_failed', '%s: %s' % (type(e).__name__, e)))

        url = self._local_llm_url()
        self.hint.configure(text='describing with %s\u2026' % url)
        threading.Thread(target=work, args=(url,), daemon=True).start()

    def probe_llm(self):
        """Ask the configured endpoint whether it is there, once, in the background."""
        url = self._local_llm_url(silent=True)
        if not url:
            self._llm_ok = False
            return

        def work():
            ok = False
            try:
                urllib.request.urlopen(url + '/models', timeout=3).read()
                ok = True
            except Exception:
                ok = False
            self.ui_events.put(('llm_probe', '%s\t%s' % (url, 'ok' if ok else 'down')))

        threading.Thread(target=work, daemon=True).start()

    def _local_llm_url(self, silent=False):
        url = (self._settings.get('llm_url') or 'http://127.0.0.1:8080/v1').rstrip('/')
        host = urllib.parse.urlparse(url).hostname or ''
        if host not in ('127.0.0.1', 'localhost', '::1'):
            if not silent:
                self.hint.configure(text='this is local-only: llm_url points at %s' % host)
            return None
        return url

    def preview(self):
        """A cheap look before committing: 2 steps at 256 px, saved outside the gallery."""
        prompt = self.text.get('1.0', 'end').strip()
        if not prompt:
            self.hint.configure(text='type a prompt first')
            return
        if self.busy:
            return
        self._preview = True
        self.status.configure(text='preview: 2 steps at 256 px')
        self.engine.generate(prompt, 256, 256, 2, zg.MODELS.get(self.model_var.get(), {}).get('cfg', 1.0),
                             int(self.seed.get() or 0), str(Path(self.args.out) / '.previews'),
                             batch=1, sequential=True, sampler=self.sampler_var.get(),
                             negative=self.negative.get('1.0', 'end').strip(),
                             profile=self.model_var.get(), style=self.style_var.get())
        self.busy = True

    # -- ALL: one image per profile, at that profile's ceiling --------------------------------------------
    def _start_all(self):
        """Queue every profile once. Each runs at its own `max_steps` and the largest size, one at a time."""
        if getattr(self, '_all_queue', None):
            return                                     # already sweeping
        self._all_queue = list(zg.ALL_ORDER)
        self._all_total = len(self._all_queue)
        self._all_saved = (self.size_var.get(), self.steps_var.get())
        self._run_next_all()

    def _run_next_all(self):
        if not self._all_queue:
            self._all_queue = None
            saved = getattr(self, '_all_saved', None)
            if saved:
                self.size_var.set(saved[0])
                self.steps_var.set(saved[1])
            self.status.configure(text='ALL finished - %d images, one per model' % getattr(self, '_all_total', 0))
            self._update_banner()
            return
        profile = self._all_queue.pop(0)
        steps = int((zg.MODELS.get(profile) or {}).get('max_steps') or 4)
        index = getattr(self, '_all_total', 1) - len(self._all_queue)
        self.size_var.set(str(zg.QUALITY_SIZE))
        self.steps_var.set(str(steps))
        self.status.configure(text='ALL %d of %d · %s at %d px, %d steps'
                              % (index, getattr(self, '_all_total', 1), profile, zg.QUALITY_SIZE, steps))
        self.generate(_profile=profile, _force_single=True)

    # -- scenes: one image per scene a prompt describes -----------------------------------------------------
    def _start_scenes(self, found):
        """Queue one render per scene, first one now. The split is `scenes.detect`, which only reads marker lines."""
        self._scene_queue = list(found[1:])
        self._scene_total = len(found)
        first = found[0]
        self.status.configure(text='scene 1 of %d · %s' % (self._scene_total, (first['title'] or 'untitled')[:48]))
        self.generate(_prompt=first['text'], _scene=(1, self._scene_total), _force_single=True)

    def _run_next_scene(self):
        if not getattr(self, '_scene_queue', None):
            self._scene_queue = None
            self._scene_current = None
            self.status.configure(text='scenes done - %d images, one per scene' % getattr(self, '_scene_total', 0))
            self._update_banner()
            return
        total = getattr(self, '_scene_total', 1)
        index = total - len(self._scene_queue) + 1
        item = self._scene_queue.pop(0)
        # kept for the grader: when the picture lands, "does it show this scene" needs the scene, not just its number
        self._scene_current = (item.get('text') or '', item.get('title') or '')
        self.status.configure(text='scene %d of %d · %s' % (index, total, (item['title'] or 'untitled')[:48]))
        self.generate(_prompt=item['text'], _scene=(index, total), _force_single=True)

    def _grade_scene(self, path, scene_text, title, index, total):
        """Ask the vision model whether the picture shows the scene it was made from, and write the answer beside it.

        Off the UI thread (a request is seconds), and allowed to fail: without the grader the render is still a render.
        """
        answer, reason = 'unavailable', ''
        try:
            import scene_grade
            verdict = scene_grade.grade_scene(path, scene_text, title)
            answer = verdict.get('answer') or 'unclear'
            reason = verdict.get('reason') or ''
            scene_grade.attach_to_sidecar(path, verdict)
        except Exception as e:
            reason = '%s: %s' % (type(e).__name__, e)
        self.ui_events.put(('scene_graded', (index, total, answer, reason, Path(path).name)))

    def vary(self, mode, strength=0.35):
        """Re-run this picture's prompt. 'vary' moves the seed; 'subtle' re-samples the picture itself at 0.35."""
        meta = self._meta_of_current()
        prompt = (meta.get('prompt') or '').strip()
        if not prompt or self._current is None:
            self.hint.configure(text='vary needs an image with its prompt recorded')
            return
        if meta.get('style'):
            self.style_var.set(meta['style'])
            prompt = self._strip_style(prompt, meta['style'])
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', prompt)
        if meta.get('negative'):
            self.negative.delete('1.0', 'end')
            self.negative.insert('1.0', meta['negative'])
        if meta.get('seed') is not None:
            self.seed.set(str(meta['seed']))
        if mode == 'base':
            # denoise-based variation: low keeps the picture, high wanders. Not A1111's latent blend, which needs a
            # noise-blend node this ComfyUI does not have - the tooltip says which knob this actually is.
            self._set_base(self._current)
            self.denoise.set(strength)
            self.hint.configure(text='variation at strength %.2f of %s'
                                     % (strength, self._short_name(Path(self._current).name)))
            self.generate()
        else:
            self.clear_base()
            self.bump_seed(1)
            self.hint.configure(text='variation: same prompt, seed %s' % self.seed.get())
            self.generate()

    def hires(self):
        """Render at the current size, then a second pass at 2x with denoise 0.45 - A1111's Hires. fix.

        This is a real enlargement: the extra pixels are sampled by the model, not interpolated.
        """
        meta = self._meta_of_current()
        if self._current is not None and (meta.get('prompt') or '').strip():
            restored = meta['prompt']
            if meta.get('style'):
                self.style_var.set(meta['style'])
                restored = self._strip_style(restored, meta['style'])
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', restored)
            if meta.get('seed') is not None:
                self.seed.set(str(meta['seed']))
        self._pending_hires = 2.0
        self.hint.configure(text='hires: rendering at %s px, then a second pass at 2x with denoise 0.45'
                                 % self.size_var.get())
        self.generate()

    def paste_base(self):
        """Take a reference picture off the clipboard and use it as the base.

        Three shapes arrive on this clipboard and all three are accepted: a bitmap (screenshot, browser
        "copy image"), a list of files (copy in Explorer), and text that is a path to an image. A clipboard that
        holds something else says so instead of raising.
        """
        from PIL import ImageGrab
        try:
            got = ImageGrab.grabclipboard()
        except Exception as e:
            self.hint.configure(text='clipboard unavailable: %s' % type(e).__name__)
            return

        origin, image = None, None
        if got is None:
            # text on the clipboard: a copied path is a perfectly good reference
            try:
                import tkinter as _tk
                text = self.root.clipboard_get()
            except Exception:
                text = ''
            candidate = Path(text.strip().strip('"')) if text.strip() else None
            if candidate and candidate.exists() and candidate.suffix.lower() in (
                    '.png', '.jpg', '.jpeg', '.webp', '.bmp'):
                try:
                    from PIL import Image as _Image
                    image = _Image.open(candidate)
                    origin = candidate
                except Exception:
                    image = None
            if image is None:
                self.hint.configure(text='nothing usable on the clipboard - copy an image, or copy an image file '
                                         'or its path')
                return
        elif isinstance(got, list):
            files = [Path(f) for f in got if str(f).lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp'))]
            if not files:
                self.hint.configure(text='the clipboard holds %d file(s) and none of them is an image' % len(got))
                return
            origin = files[0]
            try:
                from PIL import Image as _Image
                image = _Image.open(origin)
            except Exception as e:
                self.hint.configure(text='could not read %s: %s' % (origin.name, type(e).__name__))
                return
        else:
            image = got                                  # a bitmap

        out = Path(self.args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / ('pasted-%s.png' % datetime.now().strftime('%Y%m%d-%H%M%S'))
        image.convert('RGB').save(path)
        meta = {'created': datetime.now().isoformat(timespec='seconds'), 'source': 'clipboard',
                'width': image.size[0], 'height': image.size[1]}
        if origin is not None:
            meta['source_image'] = str(origin.resolve())
            meta['prompt'] = (read_meta(origin) or {}).get('prompt', '')
        else:
            meta['prompt'] = ''
        path.with_suffix('.json').write_text(json.dumps(meta), encoding='utf-8')
        self.refresh_gallery()
        self.select_file(path)
        self._set_base(path, from_gallery=True)
        self.status.configure(text='pasted reference %dx%d is the new base' % image.size)
        self.hint.configure(text='%s at %dx%d - Enter edits it at strength %.2f; the size selector rescales it to %s px'
                            % (path.name, image.size[0], image.size[1], self.denoise.get(), self.size_var.get()))

    # -- mask and expand, on the picture itself ---------------------------------
    def toggle_mask_mode(self, on=None):
        self._mask_painting = bool(self.mask_var.get()) if on is None else bool(on)
        self.mask_var.set(self._mask_painting)
        if self._mask_painting and self._current is not None and self._mask is None:
            self._mask = Image.new('L', Image.open(self._current).size, 0)
        self.canvas.configure(cursor='pencil' if self._mask_painting else '')
        self._refresh_generate_label()

    def _read_brush(self):
        try:
            self._brush = max(4, int(self.brush_var.get()))
        except (TypeError, ValueError):
            pass

    def clear_mask(self):
        if self._mask is not None:
            self._mask = Image.new('L', self._mask.size, 0)
        self.canvas.delete('paint')
        self.clear_mask_btn.state(['disabled'])
        self._refresh_generate_label()

    def _paint_mask(self, event):
        """Paint into the mask and put a visible stroke on the canvas at the same time."""
        if not self._mask_painting or not self._draw or self._mask is None:
            return None
        x0, y0, w, h = self._draw
        if not (x0 <= event.x <= x0 + w and y0 <= event.y <= y0 + h):
            return None
        scale = self._mask.width / float(w)
        cx, cy = (event.x - x0) * scale, (event.y - y0) * scale
        r = max(1.0, self._brush / 2.0 * scale)
        ImageDraw.Draw(self._mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        rr = self._brush / 2.0
        # a crisp outline as well as the fill: the stipple alone disappeared on busy pictures
        self.canvas.create_oval(event.x - rr, event.y - rr, event.x + rr, event.y + rr,
                                fill='#e6283c', stipple='gray50', outline='#8f0b1f', width=1, tags='paint')
        self.clear_mask_btn.state(['!disabled'])
        self._refresh_generate_label()
        return 'break'

    def add_pad(self, index):
        if self._current is None:
            self.hint.configure(text='show a picture first, then expand it')
            return
        self._pads[index] = min(512, self._pads[index] + 64)
        self.pad_label.configure(text='%s px' % [p for p in self._pads])
        self._refresh_generate_label()

    def clear_pads(self):
        self._pads = [0, 0, 0, 0]
        self.pad_label.configure(text='')
        self._refresh_generate_label()

    def toggle_star(self):
        names = [Path(p).name for p in self.selected]
        if not names:
            self.hint.configure(text='select a tile first')
            return
        if all(n in self._favorites for n in names):
            self._favorites.difference_update(names)
            self.status.configure(text='unstarred %d image%s' % (len(names), '' if len(names) == 1 else 's'))
        else:
            self._favorites.update(names)
            self.status.configure(text='starred %d image%s' % (len(names), '' if len(names) == 1 else 's'))
        self._persist()
        self.refresh_gallery()

    def _run_recorded(self, path):
        wall = time.time() - self.run_started_at if self.run_started_at else 0
        self._runs.append({'time': datetime.now().strftime('%H:%M:%S'), 'file': Path(path).name,
                           'seed': self.seed.get(), 'model': self.model_var.get(),
                           'style': self.style_var.get(), 'wall': self._short_time(wall)})
        self._last_run = (wall, getattr(self, '_run_total', 1) or 1, getattr(self, '_run_desc', ''))
        self._refresh_last_label()

    def _refresh_last_label(self):
        """What the last run actually cost, measured - not what the next one might."""
        if not hasattr(self, 'last_label'):
            return
        last = getattr(self, '_last_run', None)
        if not last:
            self.last_label.configure(text='')
            return
        wall, tiles, desc = last
        if tiles > 1:
            text = 'last run %s · %d tiles, %s each' % (self._short_time(wall), tiles,
                                                            self._short_time(wall / tiles))
        else:
            text = 'last image %s' % self._short_time(wall)
        self.last_label.configure(text='%s  ·  %s' % (text, desc) if desc else text)

    def show_run_log(self):
        win = tk.Toplevel(self.root)
        self.root.after(30, lambda w=win: self.theme.titlebar(w))
        win.title('Run log - this session')
        win.geometry('760x320')
        cols = ('time', 'file', 'seed', 'model', 'style', 'wall')
        tree = ttk.Treeview(win, columns=cols, show='headings', height=12)
        for c, w in (('time', 90), ('file', 300), ('seed', 80), ('model', 110), ('style', 80), ('wall', 60)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor='w')
        for r in reversed(self._runs):
            tree.insert('', 'end', values=(r['time'], r['file'], r['seed'], r['model'], r['style'], r['wall']))
        def open_row(_=None):
            sel = tree.selection()
            if not sel:
                return
            name = tree.item(sel[0], 'values')[1]
            target = Path(self.args.out) / name
            if target.exists():
                self.select_file(target)
            else:
                self.hint.configure(text='%s is no longer on disk' % name)

        tree.bind('<Double-1>', open_row)
        tree.pack(fill='both', expand=True, padx=8, pady=8)
        if not self._runs:
            ttk.Label(win, text='nothing generated yet this session').pack(anchor='w', padx=10)

    def enhance_prompt(self):
        """Rewrite the prompt with a local OpenAI-compatible LLM. Off-machine URLs are refused."""
        url = (self._settings.get('llm_url') or 'http://127.0.0.1:8080/v1').rstrip('/')
        host = urllib.parse.urlparse(url).hostname or ''
        if host not in ('127.0.0.1', 'localhost', '::1'):
            self.hint.configure(text='enhance is local-only: llm_url points at %s' % host)
            return
        draft = self.text.get('1.0', 'end').strip()
        if not draft:
            self.hint.configure(text='type a draft first, then enhance')
            return

        def work():
            body = json.dumps({'model': 'local', 'temperature': 0.7, 'max_tokens': 160,
                               'messages': [
                                   {'role': 'system', 'content': 'Rewrite the user image prompt into one vivid, '
                                                                 'concrete paragraph under 60 words. Reply with the '
                                                                 'prompt only.'},
                                   {'role': 'user', 'content': draft}]}).encode('utf-8')
            try:
                req = urllib.request.Request(url + '/chat/completions', data=body,
                                             headers={'Content-Type': 'application/json'})
                raw = json.loads(urllib.request.urlopen(req, timeout=60).read())
                self.ui_events.put(('enhanced', raw['choices'][0]['message']['content'].strip()))
            except Exception as e:
                self.ui_events.put(('enhance_failed', '%s: %s' % (type(e).__name__, e)))

        self.hint.configure(text='enhancing with %s\u2026' % url)
        threading.Thread(target=work, daemon=True).start()

    def retry(self):
        """Try the last request again after a transport failure."""
        self.retry_btn.grid_remove()
        self.hint.configure(text='')
        self.generate()

    def show_graph(self):
        """Show the ComfyUI workflow the last run actually sent - the SwarmUI "look inside" trick."""
        wf = getattr(self.engine, 'last_workflow', None)
        win = tk.Toplevel(self.root)
        self.root.after(30, lambda w=win: self.theme.titlebar(w))
        win.title('Workflow sent to ComfyUI')
        win.geometry('620x460')
        txt = tk.Text(win, wrap='none', font=('Consolas', 9))
        bar = ttk.Scrollbar(win, orient='horizontal', command=txt.xview)
        txt.configure(xscrollcommand=bar.set)
        bar.pack(side='bottom', fill='x')
        txt.pack(fill='both', expand=True, padx=8, pady=8)
        body = json.dumps(wf, indent=1) if wf else 'nothing generated yet this session'
        txt.insert('1.0', body)
        txt.configure(state='disabled')

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(body)
            self.copy_status.configure(text='workflow copied')
        ttk.Button(win, text='Copy JSON', command=copy).pack(pady=(0, 8))

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
        self.root.after(30, lambda w=win: self.theme.titlebar(w))
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

    def _wheel_owner(self, widget):
        """Which list the pointer is over: 'contents', 'strip', or None for anything else (the prompt, dialogs)."""
        node = widget
        while node is not None:
            if node is self.contents_canvas or node is self.contents:
                return 'contents'
            if node is self.gal_canvas or node is self.recent:
                return 'strip'
            node = getattr(node, 'master', None)
        return None

    def _wheel_under(self, event):
        """The widget under the pointer. Its own method so a test can name one without depending on window stacking."""
        try:
            return self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return None

    def _on_any_wheel(self, event):
        """Windows sends <MouseWheel> to the *focused* widget, not the one under the pointer, so binding the canvases
        alone meant the wheel did nothing and only dragging the scrollbar moved a list. This routes the wheel to the
        list under the cursor, and returns None everywhere else so the prompt and dialogs behave normally."""
        owner = self._wheel_owner(self._wheel_under(event))
        steps = int(-event.delta / 120)
        if owner == 'contents':
            if self._active_folder is not None:
                self.contents_canvas.yview_scroll(steps, 'units')
                self._maybe_more('contents')
            return 'break'
        if owner == 'strip':
            self.gal_canvas.yview_scroll(steps, 'units')
            self._maybe_more('strip')
            return 'break'
        return None

    # -- gallery: selection --------------------------------------------------
    def _click_order(self):
        """The order shift-click ranges over: every file in the window that is currently shown."""
        shown = {**self._tile_widgets, **self._all_widgets, **getattr(self, '_content_widgets', {})}
        return [p for p in getattr(self, '_files', []) if p in shown]

    def _on_tile_click(self, event, path, browser=False, pane=False):
        """Strip: a plain click looks at the image and restores its prompt, without arming an edit.
        Browser: a plain click only selects, so nothing closes under you.
        Pane: as the strip, plus the folder list is brought back to the open folder when it had been scrolled away.

        Ctrl toggles, shift extends from the anchor in whichever list the click came from.
        """
        path = Path(path)
        if pane:
            # the pane keeps its own cursor and its own keyboard: a click here must not hand the arrows to the strip
            self._pane_cur = path
            try:
                self.contents_canvas.focus_set()
            except Exception:
                pass
        elif not browser:
            self._nav_cur = path          # a click puts the keyboard cursor where the mouse is
            try:
                self.gal_canvas.focus_set()  # so arrows and Space drive the strip after a click
            except Exception:
                pass
        ctrl = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)

        if shift and self._anchor is not None:
            order = ([p for p in self._browser_files if p in self._all_widgets] if browser
                     else self._click_order())
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
        elif browser:
            self.selected = [path]
            self._anchor = path
        else:
            self.selected = [path]
            self._anchor = path
            if pane and self._active_folder is not None:
                # working inside a folder scrolls the folder list somewhere else; selecting one of its pictures brings
                # the folder you are in back into sight - and only when it is actually out of view (the tile refuses
                # to move when it is already visible)
                self._see_folder(self._active_folder)
            self.select_file(path)          # a click looks: only Use as Base, chain, or a mask arms an edit
            return                          # select_file rebuilds the grid, which paints it
        self._paint()

    def _paint(self):
        """Colour the selection ring and the base-image border without rebuilding thumbnails."""
        ring = self.theme.accent
        plate = ttk.Style().lookup('TFrame', 'background') or self.theme.surface
        base_name = self.base_path.name if self.base_path else None
        seen = {}
        seen.update(self._tile_widgets)
        seen.update(self._all_widgets)
        seen.update(getattr(self, '_content_widgets', {}))   # the folder pane's pictures are selections too
        for p, lab in seen.items():
            try:
                sel = any(Path(p).name == Path(s).name for s in self.selected)
                is_base = base_name is not None and Path(p).name == base_name
                lab.configure(highlightthickness=3, highlightbackground=ring if sel else plate,
                              relief='solid' if is_base else 'flat', bd=2 if is_base else 1)
            except Exception:
                pass
        n = len(self.selected)
        if hasattr(self, 'use_base_btn'):
            armable = bool(self.selected) or self._current is not None
            self.use_base_btn.state(['!disabled'] if armable else ['disabled'])
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

    def _browser_mark(self, key, mode):
        """Keyboard selection inside View All, in the same vocabulary the mouse uses.

        View All's selection is `App.selected` because the window's Delete and Load buttons read it, so the grid hands
        the intent over instead of keeping a second copy that could disagree with the buttons.
        """
        order = [p for p in self._browser_files if p in self._all_widgets]
        if not order:
            return
        key = Path(key)
        if mode == 'toggle':
            if key in self.selected:
                self.selected.remove(key)
            else:
                self.selected.append(key)
            self._anchor = key
        elif mode == 'extend' and self._anchor in order and key in order:
            i, j = order.index(self._anchor), order.index(key)
            if i > j:
                i, j = j, i
            self.selected = order[i:j + 1]
        else:
            self.selected = [key]
            self._anchor = key
        self._paint()

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
        self._nav_cur = None             # no cursor either: the next arrow starts the walk from the first cell
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
        """One entry point for a click in the recent strip or the full browser: look at it, restore its prompt.

        It deliberately does NOT set the base. Clicking a thumbnail to see it, or to get its prompt and seed
        back, used to arm an img2img upload as a side effect, which is why a run could send an image while the
        chain box was unticked. Ticking chain, pressing Use as Base, or painting a mask are now the only ways
        an image is sent.
        """
        self._display(path)
        meta = read_meta(path)
        if meta:
            self.text.delete('1.0', 'end')
            restored = meta.get('prompt') or ''
            if meta.get('style'):
                self.style_var.set(meta['style'])
                restored = self._strip_style(restored, meta['style'])
            self.text.insert('1.0', restored)
            if meta.get('seed') is not None:
                self.seed.set(str(meta['seed']))
            self.hint.configure(text='restored prompt and seed %s from %s%s'
                                % (meta.get('seed'), Path(path).name,
                                   ('; itself edited %s at strength %s'
                                    % (Path(meta['base_image']).name, meta.get('denoise')))
                                   if meta.get('base_image') else ''))
        else:
            self.hint.configure(text='no prompt recorded for %s (saved before metadata existed)' % Path(path).name)
        self.status.configure(text='viewing %s - Use as Base to edit it at strength %.2f (Enter alone starts fresh)'
                              % (self._short_name(Path(path).name), self.denoise.get()))
        self.refresh_gallery(select=path)

    def use_as_base(self):
        """Arm the selected (or the shown) picture as the base for the next run - one deliberate click.

        Its prompt and seed come back with it, so an edit starts from what produced the picture rather than
        from whatever happens to be in the prompt box.
        """
        path = self.selected[-1] if self.selected else self._current
        if path is None:
            self.hint.configure(text='click a thumbnail first, then Use as Base')
            return
        self.select_file(path)
        self._set_base(path, from_gallery=True)
        self._paint()
        self.status.configure(text='editing %s at strength %.2f - Enter sends it to the model'
                              % (self._short_name(Path(path).name), self.denoise.get()))

    def open_all(self):
        """Every image, scrolling, with the same search and star filters as the strip."""
        existing = getattr(self, '_browser_win', None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        self.refresh_gallery()
        if not self._all_files():
            self.hint.configure(text='no images yet')
            return
        win = tk.Toplevel(self.root)
        self.root.after(30, lambda w=win: self.theme.titlebar(w))
        self._browser_win = win
        win.title('View All')
        try:
            win.state('zoomed')
        except Exception:
            win.geometry('1200x800')

        head = ttk.Frame(win)
        head.pack(side='top', fill='x', padx=8, pady=(8, 0))
        ttk.Label(head, text='search').pack(side='left')
        box = ttk.Entry(head, textvariable=self.search_var, width=22)
        box.pack(side='left', padx=(4, 8))
        only = ttk.Checkbutton(head, text='\u2605 only', variable=self.starred_only)
        only.pack(side='left')
        ttk.Button(head, text='\u2605 Star selected', command=lambda: (self.toggle_star(), render())).pack(
            side='left', padx=(8, 0))
        count = ttk.Label(head, text='', foreground=self.theme.muted)
        count.pack(side='left', padx=(10, 0))

        foot = ttk.Frame(win)
        foot.pack(side='bottom', fill='x', pady=6, padx=8)
        self._all_thumbs = {}
        self._all_widgets = {}
        self._browser_files = []

        def make_tile(parent, item, size):
            path = item['path']
            tp = thumb_for(path)
            if not tp:
                return tk.Label(parent, text=path.name[:12], width=8, height=4,
                                background=self.theme.window, foreground=self.theme.muted)
            try:
                with Image.open(tp) as im:
                    im.thumbnail((size, size), Image.LANCZOS)
                    ph = ImageTk.PhotoImage(im)
            except Exception:
                return tk.Label(parent, text='?', width=4)
            self._all_thumbs[path] = ph
            lab = self._tile_widget(parent, path, ph, browser=True)
            lab.image = ph
            self._all_widgets[Path(path)] = lab
            return lab

        # One grid, shared with the folder browser: columns follow the window, the tile grows to fill it, the wheel
        # scrolls under the pointer, and the next batch is appended at the bottom instead of laying out the lot.
        # The keyboard is the same layout-grid table as the strip; the selection stays App's (`_all_widgets` is what
        # `_paint`, Delete and Load read), so the grid only reports intent and paints the cursor ring itself.
        grid = ScrollGrid(win, make_tile, caption=lambda item: item['path'].name[:26], batch=90,
                          tile_target=190, background=self.theme.window, muted=self.theme.muted,
                          accent=self.theme.accent, sel_ring=False,
                          on_mark=self._browser_mark, on_activate=self._load_from_browser,
                          on_select_all=lambda: (self.select_all(), self._paint()))
        grid.pack(side='top', fill='both', expand=True, padx=8, pady=(4, 0))

        def render(*_):
            files = self._visible_files()
            self._browser_files = list(files)
            self._all_widgets = {}
            count.configure(text='%d image%s shown' % (len(files), '' if len(files) == 1 else 's'))
            grid.set_items([{'key': Path(p), 'path': Path(p)} for p in files])
            self._paint()

        def close_browser():
            self._all_widgets = {}
            self._browser_win = None
            win.destroy()

        win.protocol('WM_DELETE_WINDOW', close_browser)
        box.bind('<KeyRelease>', lambda e: (self._on_search_typing(), render()))
        only.configure(command=render)
        ttk.Button(foot, text='Close', command=close_browser).pack(side='left')
        ttk.Button(foot, text='Load selected', command=lambda: [
            self._load_from_browser(p) for p in (self.selected[-1:] or [])]).pack(side='left', padx=(6, 0))
        ttk.Button(foot, text='Select all', command=lambda: (self.select_all(), render())).pack(
            side='left', padx=(6, 0))
        ttk.Button(foot, text='Delete', command=lambda: (self.delete_selected(), render())).pack(
            side='left', padx=(6, 0))
        render()
        # the window opens with the keyboard on the grid, so an arrow press works before anything is clicked; the
        # search box takes the keyboard back the moment it is clicked, and the grid's keys are widget-bound there
        def focus_grid():
            try:
                if win.winfo_exists() and grid.cells:
                    grid.canvas.focus_set()
            except Exception:
                pass                                    # the window closed before the timer fired
        win.after(120, focus_grid)

    def _show_resolution(self):
        """Pixels, file size and the seed, under the picture - the three facts a render is usually asked about."""
        if not hasattr(self, 'res_label'):
            return
        if self._img is None:
            self.res_label.configure(text='')
            return
        parts = ['%dx%d' % self._img.size]
        path = self._current
        if path is not None:
            try:
                parts.append(human_size(Path(path).stat().st_size))
            except Exception:
                pass
        try:
            meta = (read_meta(Path(path)) or {}) if path is not None else {}
        except Exception:
            meta = {}
        if meta.get('seed') is not None:
            parts.append('seed %s' % meta['seed'])
        if meta.get('steps') is not None:
            parts.append('%s steps' % meta['steps'])
        origin = []
        if meta.get('model'):
            origin.append(str(meta['model']))
        created = str(meta.get('created') or '')
        if created:
            origin.append(created.replace('T', ' ')[:16])
        if path is not None and meta.get('scene') is not None:
            origin.append('scene %s of %s' % (meta['scene'], meta.get('scene_of') or '?'))
        lines = ['  \u00b7  '.join(parts)]
        if origin:
            lines.append('  \u00b7  '.join(origin))
        self.res_label.configure(text='\n'.join(lines))

    def _display_raw(self, raw, path=None):
        self._current = Path(path) if path else None
        self.copy_btn.state(['!disabled'])
        self.info_btn.state(['!disabled'] if self._current else ['disabled'])
        for b in (self.vary_btn, self.vary_soft_btn, self.upscale_btn, self.describe_btn, self.pin_btn):
            b.state(['!disabled' if self._current else 'disabled'])
        self.copy_status.configure(text='')
        self._img = Image.open(io.BytesIO(raw))
        self.canvas.delete('placeholder')
        self.root.after_idle(self._redraw)
        self.root.after_idle(self._show_resolution)

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
        self.root.after_idle(self._show_resolution)

    def _set_base(self, path, from_gallery=False):
        self._base_from_gallery = from_gallery
        self.base_path = Path(path)
        self.base_label.configure(text=self._short_name(self.base_path.name))
        if hasattr(self, 'clear_base_btn'):
            self.clear_base_btn.state(['!disabled'])
        if self._base_tip is None:
            self._base_tip = self._tip(self.base_label, '')
        self._base_tip.text = self.base_path.name
        self.hint.configure(text='next generation starts from %s at strength %.2f'
                            % (self.base_path.name, self.denoise.get()))
        self._update_banner()
        self._paint()

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

    def toggle_crop_mode(self, on=None):
        """Arm the crop rectangle. Painting a mask and cropping are mutually exclusive: both start with a drag."""
        want = bool(self.crop_var.get()) if on is None else bool(on)
        self.crop_var.set(want)
        if want and getattr(self, 'mask_var', None) is not None and self.mask_var.get():
            self.mask_var.set(False)
            self.toggle_mask_mode(False)
        self.canvas.configure(cursor='crosshair' if want else '')
        self._crop_start = None
        if self._crop_item is not None:
            self.canvas.delete(self._crop_item)
            self._crop_item = None
        self.hint.configure(text='drag a rectangle on the picture - it becomes the base for the next run'
                            if want else '')

    def _on_crop_drag(self, event):
        if not self.crop_var.get() or self._crop_start is None or self._mask_painting:
            return None
        x0, y0 = self._crop_start
        if self._crop_item is None:
            self._crop_item = self.canvas.create_rectangle(x0, y0, event.x, event.y, outline=self.theme.accent,
                                                           width=2, dash=(4, 3))
        else:
            self.canvas.coords(self._crop_item, x0, y0, event.x, event.y)
        return 'break'

    def _on_crop_release(self, event):
        """Turn the dragged rectangle into a PNG and make it the base, so the next Enter edits just that region."""
        if not self.crop_var.get() or self._crop_start is None or self._current is None or not self._draw:
            return None
        x0, y0 = self._crop_start
        self._crop_start = None
        if self._crop_item is not None:
            self.canvas.delete(self._crop_item)
            self._crop_item = None
        box_px = self._crop_box(x0, y0, event.x, event.y)
        if box_px is None:
            self.hint.configure(text='that rectangle is too small to crop - drag a bigger one')
            return 'break'
        left, top, right, bottom = box_px
        from PIL import Image as _Image
        with _Image.open(self._current) as im:
            crop = im.convert('RGB').crop((left, top, right, bottom))
            dest = Path(self.args.out) / ('%s-crop-%dx%d.png' % (Path(self._current).stem,
                                                                crop.width, crop.height))
            crop.save(dest)
        meta = read_meta(self._current) or {}
        record = dict(meta)
        record.update({'crop': [left, top, right, bottom], 'source_image': str(Path(self._current).resolve()),
                       'base_image': str(Path(self._current).resolve()),
                       'denoise': self.denoise.get(), 'width': crop.width, 'height': crop.height,
                       'created': datetime.now().isoformat(timespec='seconds')})
        dest.with_suffix('.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
        self.toggle_crop_mode(False)
        self.refresh_gallery()
        self.select_file(dest)
        self._set_base(dest, from_gallery=True)
        self.status.configure(text='crop %dx%d is the new base' % (crop.width, crop.height))
        self.hint.configure(text='%s at %dx%d - Enter edits it at strength %.2f; the size selector rescales it to %s px'
                            % (dest.name, crop.width, crop.height, self.denoise.get(), self.size_var.get()))
        return 'break'

    def _crop_box(self, ax, ay, bx, by):
        """Canvas drag -> pixels in the source image, or None when the rectangle is too small to be a crop."""
        if not self._draw or self._img is None:
            return None
        x0, y0, w, h = self._draw
        if w <= 0 or h <= 0:
            return None
        iw, ih = self._img.size
        sx, sy = iw / float(w), ih / float(h)
        left = int(round((min(ax, bx) - x0) * sx))
        top = int(round((min(ay, by) - y0) * sy))
        right = int(round((max(ax, bx) - x0) * sx))
        bottom = int(round((max(ay, by) - y0) * sy))
        left, top = max(0, left), max(0, top)
        right, bottom = min(iw, right), min(ih, bottom)
        if right - left < 16 or bottom - top < 16:
            return None
        return left, top, right, bottom

    def _on_canvas_click(self, event):
        if self.crop_var.get() and self._current is not None and self._mask_painting is False:
            self._crop_start = (event.x, event.y)
            return 'break'
        if self._mask_painting:
            return self._paint_mask(event)
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
        if hasattr(self, 'clear_base_btn'):
            self.clear_base_btn.state(['disabled'])
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
        t(self.rephrase_btn, 'Rewrites the prompt using synonyms. Offline: press again for another variant.\n'
                             'The two boxes beside it decide how many words move and how forgiving the match is.')
        t(self.clip_btn, 'Makes a 2-second clip from the picture on screen with Wan 2.2 TI2V-5B.\n'
                         'The image model is unloaded first, so the first clip waits for 10.8 GiB of weights.')
        t(self.clip_open_btn, 'Open the clip that was just made (MP4), without rendering it again.')
        t(self.negative, 'Ignored by every flux2 profile while cfg is 1.0 - measured 2026-10-26: a real negative\n'
                         'prompt produced a byte-identical image. It only bites when guidance is above 1.')
        t(self.paste_btn, 'Use a picture from outside the folder as the base: copy a bitmap, or copy an image file\n'
                          'or its path, then paste. Ctrl+V does the same thing.')
        t(self.crop_chk, 'Drag a rectangle on the picture and that rectangle becomes the base for the next run,\n'
                         'so Enter edits just that region at the current strength. The size selector rescales it.')
        t(self.model_cb, 'Which checkpoint draws the picture. Switching loads it on the next run - tens of seconds, '
                         'once.')
        t(self.steps_cb, 'Sampling steps. Changing the model sets this to that model\'s ceiling (50 for flux1 dev,\n'
                         '9 for z-image, 4 for the distilled turbo models). Above the ceiling costs time for no change:\n'
                         'measured on 2026-10-20/21, z-image and klein were identical from 4 steps upward.')
        t(self.sampler_cb, 'euler is clean. euler_ancestral injects noise every step: grain and more variation.')
        t(self.seed_entry, 'The seed picks the starting noise. Same seed and prompt give the same picture.')
        t(self.seed_minus, 'Seed - 1')
        t(self.seed_plus, 'Seed + 1')
        t(self.seed_rnd, 'Draw a new random seed. After a render the seed steps on by one unless hold seed is ticked.')
        t(self.hold_seed_chk, 'On: the seed does not change after a render, so pressing Enter again repeats the same '
                              'picture.\nOff: it steps on by one each time - reproducible, and never a surprise jump.')
        t(self.seq_chk, 'On: each tile is its own request with its own seed - reproducible, and only one image '
                        'in VRAM at a time.\nOff: one batch, about 8x faster, much higher VRAM peak.')
        t(self.chain_chk, 'On: the image just made becomes the base for the next run, so Enter keeps editing.\n'
                          'Off: a finished image does not become the base - Use as Base is what arms an edit.')
        t(self.use_base_btn, 'Edit the selected (or shown) picture: it becomes the base, and its own prompt and\n'
                             'seed are restored. Until this is pressed, a click in the gallery only looks.')
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

    def _base_age_note(self):
        """How long ago the picture about to be sent was made - an old base is the surprise this window had.

        Returns '' when the age is unknown or under two minutes, so a fresh pick reads normally.
        """
        try:
            age = time.time() - self.base_path.stat().st_mtime
        except Exception:
            return ''
        if age < 120:
            return ''
        if age < 3600:
            return ' (made %d min ago)' % int(age // 60)
        if age < 86400:
            return ' (made %dh %02dm ago)' % (int(age // 3600), int((age % 3600) // 60))
        return ' (made %d days ago)' % int(age // 86400)

    def _update_banner(self):
        """Say what the next Generate will do, in one line, before it is pressed."""
        if not hasattr(self, 'banner'):
            return
        if self.base_path is None:
            mode, colour = 'TEXT → IMAGE', self.theme.text
        else:
            mode = 'EDIT %s at %.2f' % (self._short_name(self.base_path.name), self.denoise.get())
            colour = self.theme.accent
        n = self._batch()
        what = 'one image' if n == 1 else '%d tiles' % n
        if self.base_path is None:
            try:
                found = scene_detect.detect(self.text.get('1.0', 'end'))
            except Exception:
                found = []
            if len(found) > 1:
                what = scene_detect.summary(found)     # Generate will render one image per scene
        style = self.style_var.get() if hasattr(self, 'style_var') else 'none'
        model = self.model_var.get() if hasattr(self, 'model_var') else zg.DEFAULT_MODEL
        if model == zg.ALL_MODEL:
            plan = zg.all_plan()
            peaks = [estimate_vram(name, 1, size)[0] or 0 for name, _steps, size in plan]
            peak = max(peaks) if peaks else 0
            # the sweep honours the base and the strength too (measured: both models get the same base_path and denoise),
            # so the banner has to say so - an "EDIT" that reads as a plain text sweep is the misleading version
            edit = ('  ·  EDIT %s at %.2f' % (self._short_name(self.base_path.name), self.denoise.get())
                    if self.base_path is not None else '')
            self.banner.configure(
                text='ALL  ·  %s  ·  one image each at their ceiling, %d px%s  ·  style %s   ·   '
                     'VRAM ≈ %.1f/%.1f GiB (peak of one model, not the sum)%s'
                     % (' + '.join('%s %d steps' % (name, steps) for name, steps, _s in plan),
                        zg.QUALITY_SIZE, edit, style, peak / 1024.0, VRAM_TOTAL_MIB / 1024.0,
                        '' if all(peaks) else '   ·   1024 px is unmeasured for at least one model'),
                foreground=self.theme.accent if self.base_path is not None else colour)
            return
        try:
            vram, _src = estimate_vram(model, n, int(self.size_var.get() or 0) or 512)
        except (TypeError, ValueError):
            vram = None
        vram_text = '   ·   VRAM ≈ %.1f/%.1f GiB' % (vram / 1024.0, VRAM_TOTAL_MIB / 1024.0) if vram else ''
        if vram and vram > VRAM_TOTAL_MIB - 400:
            colour = self.theme.error
        self.banner.configure(text='%s   ·   %s · %s steps · %s · %s px · %s   ·   '
                                   'style %s   ·   seed %s%s%s'
                                   % (mode, model, self.steps_var.get(), self.sampler_var.get(),
                                      self.size_var.get(), what, style, self.seed.get(),
                                      ' (held)' if getattr(self, 'hold_seed_var', None)
                                      and self.hold_seed_var.get() else '', vram_text),
                              foreground=colour)

    # -- persistence, starters, prompt history -------------------------------
    def _persist(self):
        self._settings = {'size': self.size_var.get(), 'steps': self.steps_var.get(),
                          'sampler': self.sampler_var.get(), 'layout': self.layout_var.get(),
                          'history': self._history[-20:], 'style': self.style_var.get(),
                          'rephrase_intensity': self.rephrase_intensity.get(),
                          'rephrase_matching': self.rephrase_matching.get(),
                          'model': self.model_var.get(), 'advanced': self._adv_shown,
                          'seed_step': self.seed_step_var.get(),
                          'denoise': round(self.denoise.get(), 2),
                          'favorites': sorted(self._favorites),
                          'custom_styles': self._custom_styles,
                          'sidebar': self._sidebar,
                          'runs': self._runs[-50:]}
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
        """Ctrl+Up recalls the previous prompt. Plain Up is left to the textbox, so the caret still moves by line."""
        if not self._history:
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
        sec = max(0.0, float(sec))
        if sec < 10:
            return '%.1f s' % sec          # seconds matter here: 6.4 and 9.8 are different decisions
        if sec < 60:
            return '%d s' % int(round(sec))
        return '%d m %02d s' % (int(sec) // 60, int(sec) % 60)

    def _show_tile_progress(self, idx, total):
        self.ticks.configure(text='\u25cf' * idx + '\u25cb' * max(0, total - idx))
        eta = ''
        if self._run_start and idx:
            per = (time.time() - self._run_start) / idx
            eta = ' \u00b7 \u2248 %s left' % self._short_time(per * (total - idx))
        self.status.configure(text='tile %d of %d%s' % (idx, total, eta))

    def _refresh_generate_label(self):
        """Name the action, not the guess: what the button does depends on the selection, and the timing line under it
        reports what the last run actually took."""
        painting = self._mask is not None and self._mask.getbbox() is not None
        label = ('\u26a1 Fill selection' if painting else
                 ('\u26a1 Expand' if any(self._pads) else '\u26a1 Generate'))
        if hasattr(self, 'gen_btn'):
            self.gen_btn.configure(text=label)

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

    def make_clip(self):
        """A few seconds of motion: first->last frame when two pictures are selected, otherwise from the one shown.

        The two modes are two models, not a flag: Wan 2.2 TI2V-5B takes a start image only, and Wan 2.1 I2V-14B takes a
        start *and* an end image. So selecting two pictures in the gallery is the gesture that asks for the 14B: both
        endpoints are frames you drew, and only the middle is invented.
        """
        if self.busy:
            return
        picked = [Path(p) for p in self.selected if Path(p).is_file()]
        start = picked[0] if len(picked) >= 2 else self._current
        end = picked[-1] if len(picked) >= 2 else None
        if start is None:
            self.hint.configure(text='show a picture first - a clip starts from the picture on screen')
            return
        try:
            seed = int(self.seed.get())
        except ValueError:
            seed = self.args.seed
        with Image.open(start) as im:
            grid = '16:9' if im.width >= im.height * 1.4 else '1:1'
            size = im.size
        self.busy = True
        self.gen_btn.state(['disabled'])
        self.clip_btn.state(['disabled'])
        self.stop_btn.state(['disabled'])
        self.bar.configure(mode='determinate', value=0, maximum=30)
        self.bar.grid()
        self.status.configure(text='preparing the clip from %s...' % self._short_name(Path(self._current).name))
        model = 'i2v14' if end is not None else '5b'
        clip_w, clip_h = ((832, 480) if grid == '16:9' else (480, 480)) if model == 'i2v14' else (
            (1280, 704) if grid == '16:9' else (704, 704))
        motion = (self.motion_var.get() or '').strip()
        self._settings['motion'] = motion
        save_settings(self._settings)
        if end is not None:
            self.hint.configure(text='first -> last frame at %dx%d through Wan 2.1 I2V-14B: both ends are yours, the '
                                     'middle is invented. Expect minutes.' % (clip_w, clip_h))
        else:
            self.hint.configure(text='%dx%d from a %dx%d still - the first clip of a session loads about 10 GiB of '
                                     'video weights, so expect minutes, not seconds'
                                % (clip_w, clip_h, size[0], size[1]))
        self.engine.make_clip(start, grid=grid, frames=49, prompt=motion, seed=seed, end=end, model=model)

    @staticmethod
    def find_player(exists=None):
        """The first clip player that is actually installed, or None for the Windows association.

        The association cannot be trusted here: every media extension's UserChoice points at the Windows Media Player
        app package, and .mp4 falls back to WMP11.AssocFile.MP4, a handler Windows 11 no longer ships. VLC 3.0.16 is
        installed on this machine and plays the clips, so it is preferred; ZIMAGE_PLAYER overrides.
        """
        exists = exists or (lambda p: Path(p).exists())
        forced = os.environ.get('ZIMAGE_PLAYER')
        if forced:
            return [forced, '--started-from-file']
        for exe, flags in (
                (r'C:\Program Files\VideoLAN\VLC\vlc.exe', ['--started-from-file']),
                (r'C:\Program Files (x86)\VideoLAN\VLC\vlc.exe', ['--started-from-file']),
                (r'C:\Program Files\mpv\mpv.exe', ['--force-window=yes']),
                (r'C:\Program Files\MPC-HC\mpc-hc64.exe', [])):
            if exists(exe):
                return [exe] + flags
        return None

    def _open_clip(self, path, video=True):
        """Open a clip the way its container deserves: MP4 to the player, WebP to the browser.

        Windows associates .webp with Photos and .mp4 with Media Player. Handing the animated WebP to the player is
        what produces "the file is corrupt or unsupported", so a WebP is opened in the browser instead, which animates
        it - and the hint says which happened.
        """
        path = Path(path)
        if not path.exists():
            self.hint.configure(text='%s is gone from disk' % path.name)
            return False
        if video and path.suffix.lower() == '.mp4':
            player = self.find_player()
            if player:
                try:
                    import winproc
                    codec = None
                    try:
                        import video as _v
                        codec = _v.mp4_codec(path)
                    except Exception:
                        pass
                    if codec not in (None, 'avc1', 'avc3'):
                        self.hint.configure(text='%s is %s - the Windows player needs a codec pack for that; '
                                                 'VLC decodes it itself' % (path.name, codec))
                    winproc.launch(player + [str(path)])      # visible and detached, unlike winproc.hidden()
                    return Path(player[0]).stem
                except Exception as e:
                    self.hint.configure(text='%s failed (%s) - falling back to the default app'
                                        % (Path(player[0]).name, e))
            zg.open_output(str(path))
        else:
            webbrowser.open(path.resolve().as_uri())
        return True

    def open_last_clip(self):
        """Open the clip just made, again, without re-rendering it."""
        if not self._last_clip:
            self.hint.configure(text='no clip yet - press Make clip first')
            return
        is_video = getattr(self, '_last_clip_is_video', False)
        who = self._open_clip(self._last_clip, video=is_video)
        if who:
            self.hint.configure(text='opening %s in %s%s' % (
                Path(self._last_clip).name,
                who if who is not True else 'the default app',
                '' if is_video else ' (animated WebP goes to the browser)'))
        else:
            self.hint.configure(text='could not open %s' % Path(self._last_clip).name)

    def _batch(self):
        return {'single': 1, '4-up (2x2)': 4, '9-up (3x3)': 9, '16-up (4x4)': 16}.get(self.layout_var.get(), 1)

    def generate(self, _profile=None, _force_single=False, _prompt=None, _scene=None):
        """Render once.

        `_profile` and `_force_single` exist for the ALL sweep, which drives this per model. `_prompt` and `_scene` exist
        for the scene run, which drives this once per detected scene without touching the prompt box.
        """
        if self.busy:
            return
        prompt = (_prompt if _prompt is not None else self.text.get('1.0', 'end')).strip()
        if not prompt:
            self.hint.configure(text='enter a prompt first')
            return
        # a prompt that describes several scenes renders one image per scene; an edit (mask, pad, base) never splits
        if _prompt is None and not _force_single and self.base_path is None and self._mask is None and not any(self._pads):
            found = scene_detect.detect(prompt)
            if len(found) > 1:
                self._start_scenes(found)
                return
        try:
            seed = int(self.seed.get())
        except ValueError:
            seed = self.args.seed
        try:
            size = int(self.size_var.get())
        except ValueError:
            size = self.args.size

        # a style suffix and a medium named in the prompt compete: ask before sending both
        style_choice = None
        if _prompt is None and not _force_single:
            chosen_style = self.style_var.get() if hasattr(self, 'style_var') else 'none'
            spans = self.style_conflicts(prompt, chosen_style)
            if spans:
                self._mark_conflicts(spans, prompt)
                style_choice = self.ask_style_conflict(chosen_style, prompt, spans)
                if style_choice == 'revise':
                    # stop here, leave the words lit, and put the caret where the edit has to happen
                    self._focus_prompt()
                    self.hint.configure(text='edit the prompt, then press Generate again')
                    return
                self._mark_conflicts([])
                if style_choice == 'clear_style':
                    if hasattr(self, 'style_var'):
                        self.style_var.set('none')          # the control and the banner are the receipt; a hint here is
                                                            # overwritten by Generate's own lines a moment later
                if style_choice == 'remove':
                    removed = sorted({prompt[a:b] for a, b in spans})
                    prompt = self._strip_spans(prompt, spans)
                    self.text.delete('1.0', 'end')
                    self.text.insert('1.0', prompt)
                    self.hint.configure(text='removed %s from the prompt' % ', '.join(removed))

        # ALL is a sweep of models, not of scenes: it runs after the style question has been answered, and never splits
        if _profile is None and getattr(self, 'model_var', None) is not None \
                and self.model_var.get() == zg.ALL_MODEL:
            self._start_all()
            return

        # the action follows the selection: a painted mask means fill, any pad means expand
        mask_path, outpaint = None, None
        edit_base = self.base_path
        if self._mask is not None and self._mask.getbbox() and self._current is not None:
            masks = Path(self.args.out) / '.masks'
            masks.mkdir(parents=True, exist_ok=True)
            mask_path = masks / (Path(self._current).stem + '-mask.png')
            self._mask.save(mask_path)
            edit_base = self._current
        elif any(self._pads) and self._current is not None:
            outpaint = tuple(self._pads)
            edit_base = self._current

        # an edit yields exactly one result, so an N-up layout does not apply
        batch = 1 if (edit_base or mask_path or outpaint or _force_single) else self._batch()
        model = _profile or (self.model_var.get() if hasattr(self, 'model_var') else zg.DEFAULT_MODEL)

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
        if mask_path:
            self.hint.configure(text='filling the painted area of %s'
                                     % self._short_name(Path(edit_base).name))
        elif outpaint:
            self.hint.configure(text='expanding %s by %s px'
                                     % (self._short_name(Path(edit_base).name), list(outpaint)))
        elif self.base_path:
            self.hint.configure(text='starting from %s%s at strength %.2f%s'
                                % (self.base_path.name, self._base_age_note(), self.denoise.get(),
                                   '  (layout ignored: an edit is one image)' if self._batch() > 1 else ''))
        else:
            self.hint.configure(text='')
        if batch == 1:
            self.status.configure(text='queued… one %d px image' % size)
        else:
            vram, src = estimate_vram(model, batch, size)
            if vram and vram > VRAM_TOTAL_MIB - 400:
                self.hint.configure(text='this shape peaked at %.1f GiB (%s) - close to the %.1f GiB on this card. '
                                         'Lower the size, or use one image at a time.'
                                         % (vram / 1024.0, src, VRAM_TOTAL_MIB / 1024.0))
            if not self.seq_var.get():
                self.hint.configure(text='one batch shares seed %s across all %d tiles - tick "one image at a time" '
                                         'for a different seed per tile' % (seed, batch))
            cols = 2 if batch == 4 else 4 if batch == 16 else 3
            self.status.configure(text='queued… %d images at %d px -> %d px sheet, %s'
                                  % (batch, size, size * cols + (cols - 1) * 6,
                                     'seeds %d-%d one at a time' % (seed, seed + batch - 1)
                                     if self.seq_var.get() else 'one batch of %d' % batch))
        try:
            steps = int(self.steps_var.get())
        except (AttributeError, ValueError):
            steps = self.args.steps
        steps = self._clamp_steps(model, steps)
        style = self.style_var.get() if hasattr(self, 'style_var') else 'none'
        full_prompt = self._apply_style(prompt, style)
        self._follow_prediction(full_prompt)
        cfg = zg.MODELS.get(model, {}).get('cfg', 1.0)
        negative = self.negative.get('1.0', 'end').strip() if hasattr(self, 'negative') else ''
        sweep = None
        choice = self.sweep_var.get() if hasattr(self, 'sweep_var') else 'none'
        if choice != 'none' and batch > 1:
            n = batch
            vals = self._parse_values(self.sweep_values.get(), n, choice)
            if not vals:
                self.hint.configure(text='could not read the sweep values - using the full range')
                vals = self._parse_values('', n, choice)
            sweep = {'param': choice, 'values': vals}
            self.hint.configure(text='sweeping %s across %d tiles: %s%s'
                                     % (choice, n, vals[:4], '...' if len(vals) > 4 else ''))
        hires, self._pending_hires = self._pending_hires, 0.0
        self.run_started_at = time.time()
        self._run_desc = '%s px · %s steps · %s' % (size, steps, model)
        self.engine.generate(full_prompt, size, size, steps, cfg,
                             seed, self.args.out,
                             base_path=(str(edit_base) if edit_base else None),
                             mask_path=(str(mask_path) if mask_path else None), outpaint=outpaint,
                             denoise=self.denoise.get(), batch=batch, sequential=self.seq_var.get(),
                             sampler=self.sampler_var.get(), negative=negative, profile=model, style=style,
                             hires=hires, hires_denoise=0.45, sweep=sweep,
                             seed_step=(self.seed_step_var.get() if hasattr(self, 'seed_step_var') else 1),
                             scene=(_scene[0] if _scene else None), of=(_scene[1] if _scene else None))
        if mask_path or outpaint:
            self.clear_mask()
            self.clear_pads()

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
                kind, payload = self.ui_events.get_nowait()
                if kind == 'llm_probe':
                    url, state = str(payload).split('\t')
                    self._llm_ok = (state == 'ok')
                    for b in (getattr(self, 'enhance_btn', None), getattr(self, 'describe_btn', None)):
                        try:
                            if b is not None and b.winfo_exists():
                                b.state(['!disabled' if self._llm_ok else 'disabled'])
                        except Exception:
                            pass
                    # only speak if the hint line is free: the probe finishes whenever it finishes, and it used to
                    # overwrite whatever the user had just done - a late probe was clobbering cost previews and
                    # rephrase messages in the test suite
                    if not self.hint['text'].strip():
                        self.hint.configure(text=('local model ready at %s' % url) if self._llm_ok
                                            else ('no local model at %s - enhance and describe stay off' % url))
                elif kind == 'described':
                    self.text.delete('1.0', 'end')
                    self.text.insert('1.0', payload)
                    self.hint.configure(text='prompt written from the picture by the local model')
                elif kind == 'enhanced':
                    self.text.delete('1.0', 'end')
                    self.text.insert('1.0', payload)
                    self.hint.configure(text='prompt rewritten by the local model')
                elif kind == 'enhance_failed':
                    self.hint.configure(text='enhance failed: %s' % payload)
                elif kind == 'groups':
                    # posted by _compute_groups on this queue (the engine's queue is a different one, which is why
                    # this branch sat unreachable for a while and the plan never arrived)
                    self._apply_plan(payload)
                elif kind == 'review_ready':
                    self.status.configure(text='pairing ready')
                    self.review_open(payload[0], payload[1])
                elif kind == 'review_error':
                    self.hint.configure(text='A/B review failed: %s' % payload)
                    self.status.configure(text='ready')
                elif kind == 'groups_error':
                    self._grouping = False
                    self.hint.configure(text='grouping failed: %s' % payload)
                    self._render_folder_bar()
        except queue.Empty:
            pass
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
                    # each tile is already on disk: show it in the strip now, not when the whole sheet finishes
                    self.refresh_gallery()
                elif kind == 'image':
                    raw, path = payload
                    if self._preview:
                        self._preview = False
                        self.busy = False
                        self.gen_btn.state(['!disabled'])
                        self.stop_btn.state(['disabled'])
                        self.bar.grid_remove()
                        self.ticks.configure(text='')
                        self._show(raw)
                        self.status.configure(text='preview (2 steps, 256 px) - nothing saved to the gallery')
                        return
                    self._raw = raw
                    self._regroup_soon()          # a new picture changes the folders: re-group off the event loop
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
                    if self._active_folder is not None:
                        # the live view is the open folder plus whatever outside it the folder would accept: a fresh
                        # render belongs there at once, before the debounced re-group even runs
                        self._render_contents()
                    scene_now = getattr(self, '_scene_current', None)
                    if scene_now and read_meta(Path(path)):
                        # only when the sidecar recorded a scene number: a picture made outside a scene run is not graded
                        try:
                            index = int((read_meta(Path(path)) or {}).get('scene') or 0)
                        except Exception:
                            index = 0
                        if index:
                            threading.Thread(target=self._grade_scene,
                                             args=(Path(path), scene_now[0], scene_now[1], index,
                                                   getattr(self, '_scene_total', 1)),
                                             daemon=True).start()
                    if getattr(self, '_all_queue', None):
                        self.root.after(400, self._run_next_all)   # a sweep: next model once the card is idle
                    if getattr(self, '_scene_queue', None):
                        self.root.after(400, self._run_next_scene)  # a storyboard: next scene once the card is idle
                    if self.chain_var.get():
                        self._set_base(path)
                        base_note = 'chained from %s' % Path(path).name
                    else:
                        base_note = 'base unchanged (%s)' % (
                            'none - next run is text to image' if self.base_path is None
                            else 'still %s' % self.base_path.name)
                    if self.hold_seed_var.get():
                        note = 'seed %s held' % self.seed.get()
                    elif self._base_from_gallery and self.base_path is not None:
                        note = 'seed %s kept from the gallery image' % self.seed.get()
                    else:
                        before = self.seed.get()
                        self.bump_seed(1)
                        note = 'next seed %s (+1)' % self.seed.get() if self.seed.get() != before \
                            else 'seed %s held at the top' % self.seed.get()
                        self._update_banner()
                    self.hint.configure(text='saved; %s; %s' % (base_note, note))
                    self._current = Path(path)
                    self._run_recorded(path)
                    self.copy_btn.state(['!disabled'])
                elif kind == 'scene_graded':
                    index, total, answer, reason, name = payload
                    write = {'yes': 'shows', 'no': 'does not show'}.get(answer, answer)
                    self.status.configure(text='scene %s of %s graded: %s - %s' % (index, total, write, name))
                    self.hint.configure(text=('scene %s of %s: %s the scene (%s)' % (index, total, write, reason))[:160])
                elif kind == 'clip':
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
                    self.clip_btn.state(['!disabled'])
                    self.bar.grid_remove()
                    mp4 = payload.get('mp4')
                    self._last_clip = mp4 or payload.get('clip')
                    self._last_clip_is_video = bool(mp4)
                    self.clip_open_btn.state(['!disabled'])
                    shown = Path(self._last_clip).name if self._last_clip else 'nothing'
                    self.status.configure(text='clip done: %s' % shown)
                    self.hint.configure(text='%d frames at 24 fps (%.1f s) in %ss, peak %s MiB%s%s - opening %s'
                                        % (payload['frames'], payload['seconds'], payload['wall_s'],
                                           payload['vram_peak_mib'],
                                           (', mp4 in %ss' % payload['mp4_seconds'])
                                           if payload.get('mp4_seconds') else ', no mp4 this time',
                                           (' - only %s MiB free, under the 0.5 GiB floor'
                                            % payload['vram_free_mib']) if payload.get('floor_breached') else '',
                                           self._last_clip))
                    if self._last_clip:
                        self._open_clip(self._last_clip, video=self._last_clip_is_video)
                elif kind == 'error':
                    self.busy = False
                    self.gen_btn.state(['!disabled'])
                    self.stop_btn.state(['disabled'])
                    self.bar.grid_remove()
                    self.ticks.configure(text='')
                    self.clip_btn.state(['!disabled'])
                    self.bar.stop()
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
        # pinned to the top right: the picture keeps still while the metadata block under it changes, and the eye always
        # starts in the same place instead of hunting a centred image of a different size
        margin = 6
        cx, cy = self.canvas.winfo_width() - margin, margin
        self._draw = (cx - im.width, cy, im.width, im.height)
        self.canvas.create_image(cx, cy, anchor='ne', image=self._tk, tags='img')
        self.canvas.tag_raise('paint')       # a repaint must not bury the mask strokes under the picture
        self._draw_compare(im)

    def pin_current(self):
        """Pin the displayed picture so any later result can be compared against it, not just the base."""
        if self._current is None:
            self.hint.configure(text='nothing to pin yet')
            return
        self._pinned = Path(self._current)
        self._base_img = None
        self.compare_var.set(True)
        self.hint.configure(text='pinned %s - compare now shows it against the current picture'
                                 % self._short_name(self._pinned.name))
        self._redraw()

    def _compare_reference(self):
        """The left-hand picture: the pinned one if there is one, otherwise the base."""
        return self._pinned or self.base_path

    def _draw_compare(self, result):
        """Draw the base image to the left of a movable split, so strength stops being a blind dial."""
        self.canvas.delete('compare')
        ref = self._compare_reference() if hasattr(self, '_pinned') else self.base_path
        if not (hasattr(self, 'compare_var') and self.compare_var.get()) or not ref:
            return
        try:
            if self._base_img is None or getattr(self, '_base_for', None) != str(ref):
                self._base_img = Image.open(ref).convert('RGB')
                self._base_for = str(ref)
            base = self._base_img.copy()
            base.thumbnail((result.width, result.height), Image.LANCZOS)
        except Exception:
            return
        if base.size != result.size:
            base = base.resize(result.size, Image.LANCZOS)
        cut = int(base.width * self._split)
        strip = base.crop((0, 0, cut, base.height))
        self._tk_cmp = ImageTk.PhotoImage(strip)
        x0, y0, w, h = self._draw
        self.canvas.create_image(x0, y0, anchor='nw', image=self._tk_cmp, tags='compare')
        # a white/dark pair so the split stays visible on any picture
        self.canvas.create_line(x0 + cut, y0, x0 + cut, y0 + h, fill='#101010', width=4, tags='compare')
        self.canvas.create_line(x0 + cut, y0, x0 + cut, y0 + h, fill='#ffffff', width=2, tags='compare')
        # labels sit on their own chip: bare text over an image was unreadable and collided with content
        for text, anchor, tx in (('base', 'nw', x0 + 8), ('result', 'ne', x0 + w - 8)):
            chip_w = 8 + 7 * len(text)
            cx0 = tx - 4 if anchor == 'nw' else tx - chip_w + 4
            self.canvas.create_rectangle(cx0, y0 + 6, cx0 + chip_w, y0 + 24, fill='#101010', outline='',
                                         tags='compare')
            self.canvas.create_text(tx, y0 + 15, anchor=anchor, text=text, fill='#ffffff', tags='compare')

    def _on_compare_drag(self, event):
        if getattr(self, 'crop_var', None) is not None and self.crop_var.get():
            return None                      # the crop rectangle owns this drag
        if self._mask_painting:
            return self._paint_mask(event)
        if not (hasattr(self, 'compare_var') and self.compare_var.get()) or not self._draw:
            return None
        x0, y0, w, h = self._draw
        if not (x0 <= event.x <= x0 + w):
            return None
        self._split = max(0.02, min(0.98, (event.x - x0) / float(w)))
        self._redraw()
        return 'break'

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
        """Closing is a deliberate act: ask while a render is in flight, and interrupt it before the window goes.

        `Engine.shutdown` stops the worker and, when this app started ComfyUI, the server - it does *not* interrupt the
        prompt. Attached to a server we did not start (`--no-autostart`), closing therefore used to leave the render
        running on the card with nothing on screen able to cancel it. So: confirm, cancel, then shut down.
        """
        pending = (1 + len(self._all_queue or []) + len(getattr(self, '_scene_queue', None) or [])) \
            if getattr(self, 'busy', False) else 0
        if pending and not messagebox.askyesno(
            'Still working',
            '%d render%s in flight.\n\nStop %s and close?' % (pending, '' if pending == 1 else 's',
                                                              'it' if pending == 1 else 'them'),
            parent=self.root,
        ):
            return
        if self.busy:
            self._all_queue = None
            self._scene_queue = None
            try:
                print('close: %s' % self.engine.cancel())
            except Exception as e:
                print('close: cancel failed (%s: %s)' % (type(e).__name__, e))
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

    # Per-monitor DPI awareness, before the window exists. Without it Windows scales the finished window as a bitmap,
    # so text and thumbnails go soft on every display whose scale factor is not 100 %.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)          # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    engine = Engine(args.port, autostart=not args.no_autostart, progress=not args.no_progress)
    engine.start()

    root = tk.Tk()
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
