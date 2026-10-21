"""docs_check.py - acceptance check for README.md, INSTRUMENTS.md and TROUBLESHOOTING.md.

Two assertions, both exit non-zero on failure:
  1. every relative markdown link in the three files resolves to a real path in this folder;
  2. none of the three files contains a TODO / TBD / placeholder marker.

Usage (cmd.exe):
  MSYS_NO_PATHCONV=1 cmd.exe /c "C:\\Users\\kevin\\ai_bench\\image-models-20261020\\.venv\\Scripts\\python.exe docs_check.py"
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ['README.md', 'INSTRUMENTS.md', 'TROUBLESHOOTING.md']
FORBIDDEN = ['TODO', 'TBD', 'placeholder']

LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
SKIP = ('http://', 'https://', 'mailto:', 'www.', '#')


def main():
    failures = 0
    total_links = 0
    for name in DOCS:
        path = ROOT / name
        if not path.is_file():
            print('MISSING  %s' % name)
            failures += 1
            continue
        text = path.read_text(encoding='utf-8')
        targets = LINK.findall(text)
        seen = set()
        for raw in targets:
            if raw.lower().startswith(SKIP):
                continue
            target = raw.split('#', 1)[0]
            if not target or target in seen:
                continue
            seen.add(target)
            total_links += 1
            resolved = (path.parent / target).resolve()
            ok = resolved.exists()
            print('%-4s %-22s -> %s' % ('OK' if ok else 'FAIL', name, target))
            if not ok:
                failures += 1
        for word in FORBIDDEN:
            if word.lower() in text.lower():
                hits = text.lower().count(word.lower())
                print('FAIL %-22s contains %r x%d' % (name, word, hits))
                failures += 1
    print('\n%d relative links checked, %d failures' % (total_links, failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
