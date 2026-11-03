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


def merge(rows, stored, fingerprint, plan_fn):
    """Reuse the stored folders and cluster only the pictures the store does not know.

    **Not wired into the app yet, and the reason is measured.** Its arithmetic passes `test_groups_store.py`, but on the
    suite's own gallery - 24 pictures, a store holding **26 names** (three pictures since deleted), 21 of them in common - it
    produced a folder labelled `prompt for image 0` holding 21 pictures instead of the shared scene. So the failure is not
    hypothetical drift: a store that is *mostly* right plus a few deletions loses a family's identity, and re-clustering a
    subset cannot recover a label the full pass would have chosen.

    The gate before wiring it: a test that runs the full pass and the merge on the same gallery and requires **identical
    labels and identical membership**, including after deletions and additions. `reuse()` - exact, no clustering - is what the
    app actually calls.

    One unknown picture used to cost the whole pass, because the store was refused outright. Instead: cluster the unknown
    pictures **together with one anchor from each stored folder**. A candidate that lands with an anchor joins that folder; a
    candidate cluster with no anchor is a new family. Everything already stored keeps its folder by name.

    `plan_fn(rows)` must return a plan like `group_prompts.plan` (`{'groups': [{'label','items',...}]}`).
    Returns (plan, reused, clustered) where `clustered` is how many pictures went through clustering.
    """
    base = reuse(rows, stored, fingerprint)
    if base is not None:
        return base, True, 0
    if not stored or stored.get('fingerprint') != fingerprint or stored.get('version') != VERSION:
        return None, False, len(rows)
    groups = stored.get('groups') or []
    if not groups:
        return None, False, len(rows)
    by_name = {}
    for group in groups:
        for name in group.get('names') or []:
            by_name[name] = group
    anchors, unknown = [], []
    for row in rows:
        (unknown if by_name.get(_name(row)) is None else anchors).append(row)
    if not unknown:
        return None, False, len(rows)
    # one anchor per stored folder, so a candidate variation can be recognised as belonging to it
    anchor_of = {}
    for row in anchors:
        group = by_name.get(_name(row))
        anchor_of.setdefault(id(group), row)
    subset = unknown + list(anchor_of.values())
    sub = plan_fn(subset) or {}
    merged = {id(group): [] for group in groups}
    fresh = []
    for group in sub.get('groups') or []:
        members = list(group.get('items') or [])
        kept, new = [], []
        for row in members:
            home = by_name.get(_name(row))
            if home is not None:
                kept.append((home, row))
            else:
                new.append(row)
        if kept:
            for home, row in kept:
                merged[id(home)].append(row)
            for row in new:
                merged[id(kept[0][0])].append(row)
        elif new:
            fresh.append({'label': group.get('label') or '', 'items': new})
    # Every stored picture keeps its folder, anchor or not: the subset only carried one member per family, so the rest were
    # missing (the test caught the new picture joining, but b.png silently vanishing).
    for row in anchors:
        home = by_name.get(_name(row))
        bucket = merged.get(id(home)) or []
        if home is not None and all(_name(r) != _name(row) for r in bucket):
            bucket.append(row)
            merged[id(home)] = bucket
    out = []
    for group in groups:
        members = merged.get(id(group)) or []
        if members:
            out.append({'label': group.get('label') or '', 'items': members,
                        'oldest': min((m.get('created') or '' for m in members), default=''),
                        'newest': max((m.get('created') or '' for m in members), default='')})
    for group in fresh:
        members = group['items']
        out.append({'label': group.get('label') or '', 'items': members,
                    'oldest': min((m.get('created') or '' for m in members), default=''),
                    'newest': max((m.get('created') or '' for m in members), default='')})
    out.sort(key=lambda g: g.get('newest') or '', reverse=True)
    return ({'groups': out} if out else None), False, len(subset)
