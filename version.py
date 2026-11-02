"""The app's version, in one place.

Kevin's rule (2026-11-01), and it holds for every project of his:

    major . minor . patch
    - patch  += 1  when a **bug is fixed**
    - minor  += 1  when a **feature is added**, and patch goes back to 0
    - major  += 1  **only when an official release is published**, and minor and patch go back to 0

So a version only reaches the left of a point by being released, never by having a lot of work done to it. Bumping is part
of the change that earns it, not a separate chore: a commit that fixes a bug bumps the patch in the same commit.

The window shows it bottom right; `python -m` and the web server read the same constant, so the three surfaces cannot
disagree.
"""

__version__ = '0.14.0'

#: the same string with the conventional `v` in front, for display
display = 'v' + __version__


def triple():
    """(major, minor, patch) as integers, for a test that enforces the rule."""
    return tuple(int(part) for part in __version__.split('.'))
