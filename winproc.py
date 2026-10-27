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


DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def detached():
    """Keyword arguments for a program the user should SEE, detached from this process.

    `hidden()` is for consoles we never want to show; a video player is the opposite case, and STARTF_USESHOWWINDOW
    with SW_HIDE would start it minimised or invisible. Detached means closing this app does not close the player.
    """
    if not _WINDOWS:
        return {'close_fds': True}
    return {'creationflags': DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, 'close_fds': True,
            'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}


def launch(*args, **kwargs):
    """Start a GUI program for the user, outside this process's console and lifetime."""
    kwargs.update(detached())
    return subprocess.Popen(*args, **kwargs)


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
