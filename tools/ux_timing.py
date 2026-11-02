"""ux_timing.py - measure the interactions a reader actually waits for, on any machine, in one command.

The comparison in `MACHINES-2026-11-02.md` needs the same six numbers from both boxes. This produces them, plus the
machine facts that make them interpretable, as one JSON block:

    python tools/ux_timing.py                                  # the gallery in the settings
    python tools/ux_timing.py --source D:/gallery --json out.json

It measures, from the profile the window already keeps:

    app_first_paint     Tk root to the first plan (scan + group)      -> "how long until the window is usable"
    folder_click_ack    the click was visibly received                -> budget 20 ms
    folder_click_to_pane the worst folder's pictures are on screen     -> budget 1 s
    render_contents     one page of the pane                          -> budget p90 250 ms
    refresh_gallery     the strip rebuilt                             -> budget 300 ms
    apply_strip_resize  the strip reflowed after a resize             -> budget 200 ms

and then *calculates* what the comparison is actually about: the share of interactions inside the 0.1 s and 1 s budgets
(`instant` / `in_flow`), and the jitter ratio p90/p50, which is why a machine can have a good p50 and still feel rough.

Read-only: settings go to a temp dir, the gallery is never written, no model is loaded and no engine is contacted.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import test_gallery_ui as T          # noqa: E402  (StubEngine: no GPU, no ComfyUI)
import zimage_ui as zui              # noqa: E402

BUDGETS = {'folder_click_ack': 20.0, 'folder_click_to_pane': 1000.0,
           'render_contents': 250.0, 'refresh_gallery': 300.0, 'apply_strip_resize': 200.0}
MEASURED = ('app_first_paint',) + tuple(BUDGETS)


def ps(command):
    """PowerShell one-liners, because the interesting facts are WMI-only on Windows."""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command', command],
                             capture_output=True, text=True, timeout=60).stdout
        return [ln.strip() for ln in out.splitlines() if ln.strip()]
    except Exception:
        return []


def machine():
    """What a comparison has to know about a box, and what is routinely missing from notes."""
    facts = {'os': platform.platform(), 'python': platform.python_version(), 'tk': str(tk.TkVersion)}
    cpu = ps("(Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed | Format-List | Out-String).Trim()")
    for line in cpu:
        if ':' in line:
            k, v = line.split(':', 1)
            facts[k.strip().lower()] = v.strip()
    ram = ps("'{0:N1}' -f ((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)")
    if ram:
        facts['ram_gb'] = float(ram[0].replace(',', ''))
    gpu = ps("nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader")
    if gpu:
        parts = [p.strip() for p in gpu[0].split(',')]
        facts['gpu'] = parts[0]
        facts['gpu_mib'] = int(''.join(ch for ch in parts[1] if ch.isdigit()) or 0)
        facts['driver'] = parts[2]
        facts['compute'] = parts[3]
    return facts


def settle(app, root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app._poll()
        root.update()
        time.sleep(0.04)


def wait_for(app, root, predicate, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app._poll()
        root.update()
        if predicate():
            return True
        time.sleep(0.04)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default=r'C:/Users/kevin/Pictures/zimage-ui',
                    help='the gallery to open (read-only)')
    ap.add_argument('--size', default='1600x1000', help='window WxH - fixed, so two machines compare the same layout')
    ap.add_argument('--json', default='', help='write the result here as well as printing it')
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='ux-timing-'))
    os.environ['LOCALAPPDATA'] = str(tmp / 'appdata')      # never touch the real profile
    width, height = (int(v) for v in a.size.lower().split('x'))
    root = tk.Tk()
    root.geometry('%dx%d' % (width, height))
    args = T.NS(port=8021, size=512, steps=4, cfg=1.0, seed=1, out=str(Path(a.source)),
                initial='', initial_file='', auto=False, no_progress=True)

    started = time.perf_counter()
    app = zui.App(root, T.StubEngine(), args)
    root.update()
    got = wait_for(app, root, lambda: bool(app._plan), 300)
    first_paint = (time.perf_counter() - started) * 1000.0
    if not got:
        print('no plan arrived - is %s readable, and does it hold pictures?' % a.source)
        return 2
    zui.PROFILE.record('app_first_paint', first_paint)

    groups = app._plan['groups']
    pictures = sum(len(g['items']) for g in groups)
    big = max(range(len(groups)), key=lambda i: len(groups[i]['items']))
    folder_size = len(groups[big]['items'])
    print('gallery: %d pictures in %d folders; worst folder %d pictures' % (pictures, len(groups), folder_size))

    # p50 and p90 need repetitions, not one gesture: five folder opens and four resizes, and the first pass is the
    # cold one (scans, thumbnail cache) which is reported separately rather than averaged into the warm numbers.
    order = sorted(range(len(groups)), key=lambda i: -len(groups[i]['items']))[:5]
    cold = None
    passes = []
    for attempt in range(2):
        zui.PROFILE.reset()
        t0 = time.perf_counter()
        for index in order:
            app.select_folder(index)
            settle(app, root, 0.8)
        open_ms = (time.perf_counter() - t0) * 1000.0
        for delta in (-140, 140, -240, 240):
            root.geometry('%dx%d' % (width + delta, height))
            settle(app, root, 0.7)
        settle(app, root, 0.5)
        summary = zui.PROFILE.summary()
        counts = {name: (summary.get(name) or {}).get('n', 0) for name in MEASURED}
        block = {'events': {name: {'n': st['n'], 'p50': round(st['p50'], 1), 'p90': round(st['p90'], 1),
                                   'max': round(st['max'], 1)}
                            for name, st in summary.items() if name in MEASURED},
                 'open_wall_ms': round(open_ms, 1), 'counts': counts}
        if attempt == 0:
            cold = block
        else:
            passes = block
    summary = passes['events']

    events = dict(summary)

    # what to calculate: budget compliance and jitter, which is what "feels slow" actually means
    instant = [e for e in ('folder_click_ack', 'render_contents', 'apply_strip_resize', 'refresh_gallery')
               if e in events and events[e]['p50'] <= 100.0]
    in_flow = [e for e in BUDGETS if e in events and events[e]['p90'] <= BUDGETS[e]]
    jitter = {e: round(v['p90'] / v['p50'], 2) for e, v in events.items() if v['p50'] > 0}

    report = {
        'machine': machine(),
        'app': {'version': getattr(zui, 'VERSION', '') or _version(), 'window': a.size},
        'gallery': {'pictures': pictures, 'folders': len(groups), 'worst_folder': folder_size},
        'events': events,
        'calculated': {
            'instant_p50_under_100ms': sorted(instant),
            'in_flow_p90_within_budget': sorted(in_flow),
            'budget_compliance': '%d of %d' % (len(in_flow), len(BUDGETS)),
            'jitter_p90_over_p50': jitter,
            'open_wall_ms': passes['open_wall_ms'],
            'repeat_count': min([v['n'] for v in events.values()] or [0]),
        },
        'cold_pass': cold,
    }
    text = json.dumps(report, indent=2)
    print(text)
    if a.json:
        Path(a.json).write_text(text, encoding='utf-8')
        print('\nwritten to %s' % a.json)
    return 0


def _version():
    try:
        import version as v
        return v.display
    except Exception:
        return ''


if __name__ == '__main__':
    sys.exit(main())
