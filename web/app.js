/* app.js - the gallery page: folders, a thumbnail grid, the A/B ranking, and Describe all.
 *
 * Nothing here decides anything: the server owns grouping (group_prompts), the pair schedule (pair_review) and the
 * wins in the sidecars (pair_rank). This file moves JSON into the DOM and the keyboard into those calls.
 */
'use strict';

const VIEWS = ['folders', 'folder', 'review', 'done'];

const state = {
  view: 'folders',
  folders: [],
  folder: null,
  review: null,
  rank: null,
  busy: false,
  job: null,
  jobTimer: 0,
  focusOnLoad: true,
};

const $ = (id) => document.getElementById(id);

/* --- plumbing --------------------------------------------------------------------------------------------------- */

async function api(path, options) {
  const res = await fetch(path, options);
  let data = {};
  try {
    data = await res.json();
  } catch (err) {
    data = {};
  }
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status + ' ' + res.statusText));
  return data;
}

function show(view) {
  state.view = view;
  for (const name of VIEWS) $('view-' + name).hidden = name !== view;
  document.body.dataset.view = view;
}

function setStatus(text) {
  $('status').textContent = text || '';
}

function setJobStatus(text) {
  $('job-status').textContent = text || '';
}

function showError(text) {
  const box = $('error');
  box.textContent = text || '';
  box.hidden = !text;
}

function fileOf(path) {
  return String(path).split(/[\\/]/).pop();
}

function thumbUrl(path) {
  return '/thumb?path=' + encodeURIComponent(path);
}

function imageUrl(path) {
  return '/image?path=' + encodeURIComponent(path);
}

function plural(n, one, many) {
  return n + ' ' + (n === 1 ? one : (many || one + 's'));
}

function recordText(wins, losses, ties) {
  if (!wins && !losses && !ties) return '';
  const bits = [];
  if (wins) bits.push(wins + 'W');
  if (losses) bits.push(losses + 'L');
  if (!wins && !losses && ties) bits.push(ties + 'T');
  return bits.join(' ');
}

function itemFor(payload, path) {
  const items = (payload && payload.items) || [];
  return items.find((item) => item.path === path) || null;
}

/* --- folders ---------------------------------------------------------------------------------------------------- */

async function loadFolders(focusFirst) {
  showError('');
  setStatus('grouping\u2026');
  const data = await api('/api/folders');
  state.folders = data.folders;
  const list = $('folders');
  list.textContent = '';
  for (const folder of data.folders) {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'folder';
    btn.dataset.id = folder.id;
    const label = document.createElement('span');
    label.className = 'folder-label';
    label.textContent = folder.label;
    const meta = document.createElement('span');
    meta.className = 'folder-meta';
    meta.textContent = plural(folder.count, 'picture')
      + ' \u00b7 ' + plural(folder.series, 'series', 'series')
      + (folder.undescribed ? ' \u00b7 ' + folder.undescribed + ' undescribed' : '');
    btn.append(label, meta);
    btn.addEventListener('click', () => openFolder(folder.id));
    li.append(btn);
    list.append(li);
  }
  $('folders-summary').textContent = plural(data.pictures, 'picture') + ' in ' + plural(data.folders.length, 'folder')
    + ' \u00b7 grouped in ' + data.seconds + ' s';
  setStatus(data.folders.length ? '' : 'no pictures here yet');
  show('folders');
  if (focusFirst) {
    const first = list.querySelector('button');
    if (first) first.focus();
  }
}

/* --- one folder ------------------------------------------------------------------------------------------------- */

async function openFolder(id, opts) {
  const data = await api('/api/folder/' + encodeURIComponent(id));
  state.folder = data;
  showError('');
  setStatus('');
  $('folder-h').textContent = data.label;
  $('folder-meta').textContent = plural(data.count, 'picture')
    + ' \u00b7 ' + plural(data.series, 'series', 'series')
    + ' \u00b7 ' + data.kind;
  $('describe-all').disabled = !data.undescribed;
  $('start-review').disabled = data.count < 2;
  $('start-review').title = data.count < 2 ? 'a folder needs two pictures to compare' : '';
  $('describe-count').textContent = data.undescribed ? data.undescribed + ' with no prompt yet' : '';
  renderGrid(data.items);
  show('folder');
  const fallback = data.count < 2 ? $('back-to-folders') : $('start-review');
  const focus = (opts && opts.focus) || fallback;
  if (focus && focus.focus && !focus.disabled) focus.focus();
}

function renderGrid(items) {
  const list = $('grid');
  list.textContent = '';
  for (const item of items) {
    const li = document.createElement('li');
    const fig = document.createElement('figure');
    fig.className = 'tile';

    const link = document.createElement('a');
    link.href = imageUrl(item.path);
    link.target = '_blank';
    link.rel = 'noopener';

    const img = document.createElement('img');
    img.src = thumbUrl(item.path);
    img.alt = item.prompt || item.name;
    img.loading = 'lazy';
    img.decoding = 'async';
    img.width = 168;
    img.height = 168;
    link.append(img);

    const cap = document.createElement('figcaption');
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = item.name;
    cap.append(name);
    const record = recordText(item.wins, item.losses, item.ties);
    if (record) {
      const rec = document.createElement('span');
      rec.className = 'record';
      rec.textContent = record;
      cap.append(rec);
    }
    const prompt = document.createElement('span');
    prompt.className = 'prompt';
    prompt.textContent = item.has_prompt ? item.prompt
      : (item.caption_only ? 'caption: ' + item.prompt : 'no prompt yet');
    cap.append(prompt);

    fig.append(link, cap);
    li.append(fig);
    list.append(li);
  }
}

/* --- the A/B review --------------------------------------------------------------------------------------------- */

async function startReview() {
  if (!state.folder) return;
  setStatus('preparing the pairs\u2026');
  const data = await api('/api/review/' + encodeURIComponent(state.folder.id));
  setStatus('');
  renderReview(data);
}

function reviewInfo(payload) {
  const bits = [payload.label, payload.stage];
  bits.push(payload.decided + ' of ' + payload.total + ' pairs decided');
  if (!payload.done) {
    bits.push(plural(payload.queue, 'pair') + ' left');
    bits.push(payload.minutes_left >= 1 ? '~' + payload.minutes_left + ' min'
      : '~' + Math.max(1, Math.round(payload.minutes_left * 60)) + ' s');
    bits.push(fileOf(payload.pair[0]) + ' vs ' + fileOf(payload.pair[1]));
  }
  if (payload.settled) bits.push('the order settled after round ' + payload.round);
  return bits.join(' \u00b7 ');
}

function renderReview(payload) {
  state.review = payload;
  if (!payload.pair) {
    openDone(payload);
    return;
  }
  const [a, b] = payload.pair;
  setCandidate($('left-img'), $('left-cap'), payload, a);
  setCandidate($('right-img'), $('right-cap'), payload, b);
  $('review-info').textContent = reviewInfo(payload);
  $('review-h').textContent = 'A/B review \u2014 ' + payload.label;
  show('review');
  $('pick-left').focus();
}

function setCandidate(img, cap, payload, path) {
  const item = itemFor(payload, path);
  img.src = imageUrl(path);
  img.alt = 'candidate: ' + fileOf(path) + (item && item.prompt ? ' \u2014 ' + item.prompt : '');
  const record = item ? recordText(item.wins, item.losses, item.ties) : '';
  cap.textContent = fileOf(path) + (record ? '  (' + record + ')' : '');
}

async function decide(choice) {
  const payload = state.review;
  if (!payload || !payload.pair || state.busy) return;
  state.busy = true;
  try {
    const data = await api('/api/decide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: payload.id, a: payload.pair[0], b: payload.pair[1], choice: choice }),
    });
    showError('');
    renderReview(data);
  } catch (err) {
    showError(err.message);
  } finally {
    state.busy = false;
  }
}

function leaveReview() {
  setStatus('left the review \u2014 the decisions were kept');
  if (state.folder) openFolder(state.folder.id);
  else loadFolders(false);
}

/* --- the finished screen ---------------------------------------------------------------------------------------- */

async function openDone(payload) {
  const source = payload || state.review;
  if (!source) return;
  const data = await api('/api/rank/' + encodeURIComponent(source.id));
  state.rank = data;
  $('done-h').textContent = data.decided ? 'Ranked by wins' : 'Nothing to rank yet';
  $('done-meta').textContent = data.decided + ' of ' + data.total + ' pairs decided \u00b7 best first'
    + (data.settled ? ' \u00b7 settled after round ' + data.round : '');

  const list = $('done-list');
  list.textContent = '';
  for (const entry of data.entries) {
    const li = document.createElement('li');
    const fig = document.createElement('figure');

    // The result tiles are the payoff, so they are laid out large (360 px against the grid's 168) - but they are
    // drawn from the same 336 px cache the grid uses rather than re-sending 48 full-size PNGs, and each one links to
    // the full picture for the pixel-level look.
    const link = document.createElement('a');
    link.href = imageUrl(entry.path);
    link.target = '_blank';
    link.rel = 'noopener';
    link.setAttribute('aria-label', 'open ' + entry.name + ' at full size');
    const img = document.createElement('img');
    img.src = thumbUrl(entry.path);
    img.alt = 'rank ' + entry.rank + ': ' + entry.name;
    img.loading = 'lazy';
    img.decoding = 'async';
    img.width = 360;
    img.height = 360;
    link.append(img);
    fig.append(link);

    const cap = document.createElement('figcaption');
    const rank = document.createElement('span');
    rank.className = 'rank';
    rank.textContent = String(entry.rank);
    const who = document.createElement('span');
    who.className = 'who';
    who.textContent = entry.name;
    const wins = document.createElement('span');
    wins.className = 'wins';
    wins.textContent = recordText(entry.wins, entry.losses, entry.ties) || 'no bout';
    cap.append(rank, who, wins);
    fig.append(cap);

    li.append(fig);
    list.append(li);
  }
  show('done');
  setStatus('');
  $('keep-close').focus();
}

function keepClose() {
  stopPolling();
  const id = state.folder && state.folder.id;
  const label = (state.review && state.review.label) || 'this folder';
  setStatus('kept the ranking of ' + label);
  if (id) openFolder(id).catch(() => loadFolders(false));
  else loadFolders(true);
}

async function resetAndRestart() {
  const source = state.review || state.rank;
  if (!source || !source.id) return;
  try {
    const data = await api('/api/reset/' + encodeURIComponent(source.id), { method: 'POST' });
    showError('');
    setStatus('cleared the record on ' + plural(data.count, 'picture') + ' \u2014 starting from zero');
    renderReview(data);
  } catch (err) {
    showError(err.message);
  }
}

/* --- Describe all ----------------------------------------------------------------------------------------------- */

async function describeAll() {
  if (!state.folder) return;
  $('describe-all').disabled = true;
  try {
    const data = await api('/api/describe-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: state.folder.id }),
    });
    showError('');
    if (!data.job) {
      setJobStatus(data.message || 'nothing to describe here');
      $('describe-all').disabled = false;
      return;
    }
    state.job = data.job;
    $('cancel-job').hidden = false;
    setJobStatus('describing 0/' + data.total + '\u2026');
    pollJob();
  } catch (err) {
    showError(err.message);
    $('describe-all').disabled = false;
  }
}

function stopPolling() {
  if (state.jobTimer) {
    clearTimeout(state.jobTimer);
    state.jobTimer = 0;
  }
}

function pollJob() {
  stopPolling();
  state.jobTimer = setTimeout(async () => {
    try {
      const job = await api('/api/job/' + encodeURIComponent(state.job));
      const line = 'describing ' + job.done + '/' + job.total
        + (job.current ? ' \u00b7 ' + job.current : '')
        + (job.failed ? ' \u00b7 ' + job.failed + ' failed' : '');
      setJobStatus(line);
      if (job.state === 'running' || job.state === 'cancelling') {
        pollJob();
        return;
      }
      $('cancel-job').hidden = true;
      state.job = null;
      setJobStatus(job.state === 'cancelled'
        ? 'stopped after ' + job.done + ' of ' + job.total
        : 'described ' + job.done + ' of ' + job.total + (job.failed ? ', ' + job.failed + ' failed' : ''));
      setStatus('the prompts changed, so the folders were regrouped');
      // Regrouping moves folder ids: reopen by id, and fall back to the list when that id no longer exists.
      try {
        await openFolder(state.folder.id);
      } catch (err) {
        await loadFolders(false);
      }
    } catch (err) {
      showError(err.message);
    }
  }, 700);
}

async function cancelJob() {
  if (!state.job) return;
  try {
    await api('/api/job/' + encodeURIComponent(state.job) + '/cancel', { method: 'POST' });
    setJobStatus('stopping after this picture\u2026');
  } catch (err) {
    showError(err.message);
  }
}

/* --- the keyboard: 1/2/3 and the arrows decide, Esc backs out, Enter/Space keep ---------------------------------- */

document.addEventListener('keydown', (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target || {};
  const tag = (target.tagName || '').toUpperCase();
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  const onControl = tag === 'BUTTON' || tag === 'A';

  if (state.view === 'review') {
    if (event.key === '1' || event.key === 'ArrowLeft') {
      event.preventDefault();
      decide('left_wins');
    } else if (event.key === '2') {
      event.preventDefault();
      decide('tie');
    } else if (event.key === '3' || event.key === 'ArrowRight') {
      event.preventDefault();
      decide('right_wins');
    } else if (event.key === 'Escape') {
      event.preventDefault();
      leaveReview();
    }
    return;
  }

  if (state.view === 'done') {
    if ((event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') && !onControl) {
      event.preventDefault();
      keepClose();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      keepClose();
    }
    return;
  }

  if (state.view === 'folder' && event.key === 'Escape') {
    event.preventDefault();
    // Esc first stops a describe batch - that is the transient thing here - and only then leaves the folder.
    if (state.job) cancelJob();
    else loadFolders(true);
  }
});

/* --- wiring ----------------------------------------------------------------------------------------------------- */

$('back-to-folders').addEventListener('click', () => loadFolders(true));
$('start-review').addEventListener('click', () => {
  startReview().catch((err) => showError(err.message));
});
$('describe-all').addEventListener('click', () => {
  describeAll().catch((err) => showError(err.message));
});
$('cancel-job').addEventListener('click', () => {
  cancelJob().catch((err) => showError(err.message));
});
$('pick-left').addEventListener('click', () => decide('left_wins'));
$('pick-tie').addEventListener('click', () => decide('tie'));
$('pick-right').addEventListener('click', () => decide('right_wins'));
$('leave-review').addEventListener('click', leaveReview);
$('reset-start').addEventListener('click', () => {
  resetAndRestart().catch((err) => showError(err.message));
});
$('keep-close').addEventListener('click', keepClose);

loadFolders(true).catch((err) => showError(err.message));
