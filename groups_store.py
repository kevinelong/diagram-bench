"""groups_store.py - remember which folder a picture was filed under, so a launch only clusters what is new.

Grouping 2,935 pictures costs ~75 s measured (`STATE 28`), and every launch paid it for pictures whose prompt and lineage
root had not changed. The assignment is the durable thing: a folder's *label* is a medoid and moves when content changes,
so only the mapping name -> family root is stored, and the label is presentation.

    stored = load(gallery)
    plan   = reuse(rows, stored, fingerprint)      # None when the store cannot be trusted, or a picture fits nowhere
    if plan is None:
        plan = group_prompts.plan(...)            # the full pass, once
        save(gallery, plan, fingerprint)

What makes it safe: the fingerprint (grouping version, thresholds, recency window) must match, a picture that is not in the
store is a *candidate* and forces the full pass, and an empty result is never written. A stale-but-valid store is fixed by
`Regroup everything`, not by silently drifting.
"""
from __future__ import annotations

import json
from pathlib import Path

STORE = '.zimage-groups.json'
VERSION = 1


def store_path(gallery):
    return Path(gallery) / STORE


def _fingerprint(parts):
    """A stable string for the inputs that change the *rule*: version, thresholds, recency window."""
    return json.dumps([VERSION] + [round(float(p), 6) if isinstance(p, (int, float)) else str(p) for p in parts],
                      sort_keys=True)


def _key(row):
    """A picture's family: its lineage root when recorded, else its prompt - the same rule `group_prompts` files by."""
    root = (row.get('root') or '').strip() if isinstance(row, dict) else ''
    if root:
        return 'root:' + root
    prompt = (row.get('prompt') or '').strip() if isinstance(row, dict) else ''
    return 'prompt:' + prompt


def _name(row):
    path = row.get('path') if isinstance(row, dict) else None
    return Path(str(path)).name if path else ''


def load(gallery):
    """The stored plan, or {} when there is none or it is unreadable - never an exception."""
    try:
        blob = json.loads(store_path(gallery).read_text(encoding='utf-8'))
        return blob if isinstance(blob, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save(gallery, plan, fingerprint):
    """Write the assignment of a plan that was actually computed. Returns the path, or None when there was nothing to keep."""
    groups = (plan or {}).get('groups') or []
    if not groups:
        return None
    records = []
    for group in groups:
        labels = group.get('label') or ''
        keys, names = [], []
        for row in group.get('items') or []:
            keys.append(_key(row))
            names.append(_name(row))
        if names:
            records.append({'label': labels, 'keys': sorted(set(keys)), 'names': names})
    if not records:
        return None
    blob = {'version': VERSION, 'fingerprint': fingerprint, 'groups': records}
    path = store_path(gallery)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(blob, indent=1, sort_keys=True), encoding='utf-8')
        return path
    except OSError:
        return None


def reuse(rows, stored, fingerprint):
    """Rebuild the folders from the store, or return None when a full pass is needed.

    None means: no store, a different rule (fingerprint), an unreadable record, or a picture that no stored folder claims -
    the last is the important one, because a new family has to be discovered by clustering rather than guessed at.
    """
    if not stored or stored.get('fingerprint') != fingerprint or stored.get('version') != VERSION:
        return None
    groups = stored.get('groups') or []
    by_name = {}
    for group in groups:
        for name in group.get('names') or []:
            by_name[name] = group
    items = {}
    for group in groups:
        items[id(group)] = []
    for row in rows:
        target = by_name.get(_name(row))
        if target is None:
            return None                       # a picture fits nowhere: only clustering can say what it is
        items[id(target)].append(row)
    out = []
    for group in groups:
        members = items.get(id(group)) or []
        if not members:
            continue                          # the folder is gone from the corpus: drop it, do not keep an empty one
        out.append({'label': group.get('label') or '',
                    'items': members,
                    'oldest': min(((m.get('created') or '') for m in members), default=''),
                    'newest': max(((m.get('created') or '') for m in members), default='')})
    if not out:
        return None
    out.sort(key=lambda g: g.get('newest') or '', reverse=True)
    return {'groups': out}


def fingerprint_for(threshold, recent, min_group):
    """The parts of the rule a stored plan was made under: change any and the store must not be reused."""
    return _fingerprint([threshold, recent, min_group])
