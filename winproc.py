"""winproc.py - run child processes without a console window stealing focus.

Every `nvidia-smi` poll, every PowerShell helper and every server launch in this repository is a child process. On
Windows a console child of a GUI app gets its own window, and that window takes the foreground - so a 250 ms VRAM
sampler flashes a black window four times a second while a render runs, and starting ComfyUI pops another. The user
is working in another application; none of this should be visible.

`hidden()` returns the keyword arguments that suppress it, and `run`/`popen` are drop-in wrappers that apply them on
Windows and do nothing at all elsewhere.

    from winproc import run, popen
    run(['nvidia-smi', ...], capture_output=True, text=True)
    popen(argv, stdout=fh, stderr=STDOUT)
"""
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
_WINDOWS = os.name == 'nt'


def hidden():
    """Keyword arguments for subprocess that keep the child invisible. Empty on non-Windows platforms."""
    if not _WINDOWS:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {'creationflags': CREATE_NO_WINDOW, 'startupinfo': si}


def run(*args, **kwargs):
    """subprocess.run, but the child has no visible window."""
    kwargs.update(hidden())
    return subprocess.run(*args, **kwargs)


def popen(*args, **kwargs):
    """subprocess.Popen, but the child has no visible window."""
    kwargs.update(hidden())
    return subprocess.Popen(*args, **kwargs)


def checked(*args, **kwargs):
    """subprocess.check_output, but the child has no visible window."""
    kwargs.update(hidden())
    return subprocess.check_output(*args, **kwargs)
