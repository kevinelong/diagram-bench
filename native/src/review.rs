//! The A/B review session, transcribed from `pair_review.py` and `pair_rank.py` so that both apps read and write the
//! *same* session.
//!
//! The interface is two files:
//!
//! * `~/.local/zimage-ui/review/<state-key>.json` - the schedule (`pairs`), the decisions, the round bookkeeping. Its
//!   `state_key` is the first 16 hex digits of the sha1 of the sorted picture *names*, which is why a session is "this
//!   folder's review" and survives being closed, reopened, or opened by the other app.
//! * the picture's own `<name>.json` - `wins`, `losses`, `ties`, `bouts`, `last_bout` and the per-opponent `vs` tally,
//!   beside the `prompt` that was already there.
//!
//! Nothing here is a re-design: keys, choice vocabulary, id format (`'|'.join(sorted(names))`), timestamp format
//! (`%Y-%m-%dT%H:%M:%S`) and even the JSON indentation follow the Python, because two programs writing one file must
//! agree on every byte that is not merely whitespace. `--selftest` checks that agreement against the real Python.

use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

/// Up to this many pictures the schedule is the complete graph: every pair once, cheapest question first.
pub const EXHAUSTIVE_MAX: usize = 6;
/// The ranking choices. `left_wins`/`right_wins`/`tie` only ever add to a record; nothing is deleted.
pub const WIN_CHOICES: [&str; 3] = ["left_wins", "right_wins", "tie"];
/// The Python's older delete vocabulary, transcribed for compatibility: a session may already contain these.
pub const DELETE_CHOICES: [&str; 3] = ["keep_both", "delete_left", "delete_right"];
/// The durable half of a result - `pair_rank.META_KEYS`.
const META_KEYS: [&str; 5] = ["wins", "losses", "ties", "bouts", "last_bout"];
/// A decision costs about three seconds of attention (`pair_review.DECISION_SECONDS`), so a plan can be priced.
const DECISION_SECONDS: f64 = 3.0;

fn wide(text: &str) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    std::ffi::OsStr::new(text).encode_wide().chain(std::iter::once(0)).collect()
}

/// `pair_review.STATE_DIR`: `Path.home() / '.local' / 'zimage-ui' / 'review'`.
///
/// `ZIMAGE_REVIEW_DIR` overrides it, which is only used to keep a test (or a scripted run) out of a real session.
pub fn state_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("ZIMAGE_REVIEW_DIR") {
        return PathBuf::from(dir);
    }
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(".local").join("zimage-ui").join("review")
}

/// A path's `Path(p).name`, the only part of a path the state file talks about.
pub fn name_of(path: &str) -> String {
    Path::new(path)
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| path.to_string())
}

// ---- sha1 (the state key) ----------------------------------------------------------------------------------------

/// SHA-1, hex. Present because `state_key` *is* a sha1 prefix: a different hash would be a different session.
pub fn sha1_hex(data: &[u8]) -> String {
    let mut h: [u32; 5] = [0x6745_2301, 0xEFCD_AB89, 0x98BA_DCFE, 0x1032_5476, 0xC3D2_E1F0];
    let bit_len = (data.len() as u64) * 8;
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());
    for block in msg.chunks(64) {
        let mut w = [0u32; 80];
        for (i, word) in block.chunks(4).enumerate() {
            w[i] = u32::from_be_bytes([word[0], word[1], word[2], word[3]]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let (mut a, mut b, mut c, mut d, mut e) = (h[0], h[1], h[2], h[3], h[4]);
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5A82_7999),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6),
            };
            let temp = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = temp;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
    }
    h.iter().map(|word| format!("{:08x}", word)).collect()
}

/// `pair_review.state_key`: the first 16 hex digits of the sha1 of the sorted member names, newline-joined.
pub fn state_key(paths: &[String]) -> String {
    let mut names: Vec<String> = paths.iter().map(|p| name_of(p)).collect();
    names.sort();
    let joined = names.join("\n");
    sha1_hex(joined.as_bytes())[..16].to_string()
}

// ---- timestamps --------------------------------------------------------------------------------------------------

/// `time.strftime('%Y-%m-%dT%H:%M:%S')` and `datetime.now().isoformat(timespec='seconds')` in local time.
pub fn now() -> String {
    let st = unsafe { windows::Win32::System::SystemInformation::GetLocalTime() };
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond
    )
}

// ---- JSON files --------------------------------------------------------------------------------------------------

/// Write JSON the way `json.dump(..., indent=n)` does: `indent` is the indentation unit (empty for `indent=0`).
///
/// Replaced atomically: a half-written sidecar would lose the prompt, which is the picture's identity.
fn write_json(value: &Value, path: &Path, indent: &[u8]) -> anyhow::Result<()> {
    use serde::Serialize;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut buf = Vec::new();
    let formatter = serde_json::ser::PrettyFormatter::with_indent(indent);
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, formatter);
    value.serialize(&mut ser)?;
    let tmp = path.with_file_name(format!(
        ".{}.{}.tmp",
        path.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default(),
        std::process::id()
    ));
    std::fs::write(&tmp, &buf)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

fn read_json_object(path: &Path) -> Map<String, Value> {
    match std::fs::read_to_string(path) {
        Ok(text) => match serde_json::from_str::<Value>(&text) {
            Ok(Value::Object(map)) => map,
            _ => Map::new(),
        },
        Err(_) => Map::new(),
    }
}

// ---- the session file --------------------------------------------------------------------------------------------

/// `pair_review.load_state`: the stored session, or the same empty shape the Python starts from.
pub fn load_state(key: &str, dir: &Path) -> Value {
    let path = dir.join(format!("{}.json", key));
    if let Ok(text) = std::fs::read_to_string(&path) {
        if let Ok(value) = serde_json::from_str::<Value>(&text) {
            if value.is_object() {
                return value;
            }
        }
    }
    json!({
        "key": key,
        "created": now(),
        "decisions": {},
        "pairs": Value::Null,
    })
}

/// `pair_review.save_state`: stamps `updated` and writes `indent=1`, atomically.
pub fn save_state(state: &mut Value, dir: &Path) -> anyhow::Result<PathBuf> {
    let key = state.get("key").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    let path = dir.join(format!("{}.json", key));
    if let Some(object) = state.as_object_mut() {
        object.insert("updated".to_string(), Value::from(now()));
    }
    write_json(state, &path, b" ")?;
    Ok(path)
}

/// `'|'.join(sorted((Path(a).name, Path(b).name)))` - the key a decision is remembered under.
pub fn pair_id(a: &str, b: &str) -> String {
    let mut names = [name_of(a), name_of(b)];
    names.sort();
    names.join("|")
}

// ---- dHash (only ever used to order the questions) ----------------------------------------------------------------

/// `pair_review.dhash`: 64 bits from a 9x8 greyscale, left pixel brighter than its right neighbour -> 1.
///
/// The Python scales the cached thumbnail when one exists and the picture otherwise, so this does too. The 9x8
/// resample is WIC's, not Pillow's LANCZOS, so individual bits can differ; the hash orders questions and never filters
/// or scores them, and the pair list itself is written by whichever app built the session first.
pub fn dhash(path: &Path) -> Option<String> {
    use windows::Win32::Foundation::GENERIC_READ;
    use windows::Win32::Graphics::Imaging::*;
    use windows::Win32::System::Com::*;
    unsafe {
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let stem = path.file_stem()?.to_string_lossy().to_string();
        let cached = path.parent()?.join(".thumbs").join(format!("{}.jpg", stem));
        let src = if cached.exists() { cached } else { path.to_path_buf() };
        let factory: IWICImagingFactory = CoCreateInstance(&CLSID_WICImagingFactory2, None, CLSCTX_INPROC_SERVER).ok()?;
        let name = wide(&src.to_string_lossy());
        let decoder = factory
            .CreateDecoderFromFilename(
                windows::core::PCWSTR(name.as_ptr()),
                None,
                GENERIC_READ,
                WICDecodeMetadataCacheOnDemand,
            )
            .ok()?;
        let frame = decoder.GetFrame(0).ok()?;
        // convert to grey *first*, then scale: the order Pillow's convert('L').resize() uses
        let gray = factory.CreateFormatConverter().ok()?;
        gray.Initialize(
            &frame,
            &GUID_WICPixelFormat8bppGray,
            WICBitmapDitherTypeNone,
            None,
            0.0,
            WICBitmapPaletteTypeCustom,
        )
        .ok()?;
        let scaler = factory.CreateBitmapScaler().ok()?;
        scaler
            .Initialize(&gray, 9, 8, WICBitmapInterpolationModeHighQualityCubic)
            .ok()?;
        let mut pixels = [0u8; 9 * 8];
        scaler.CopyPixels(std::ptr::null(), 9, &mut pixels).ok()?;
        let mut bits: u64 = 0;
        for row in 0..8 {
            for col in 0..8 {
                let left = pixels[row * 9 + col];
                let right = pixels[row * 9 + col + 1];
                bits = (bits << 1) | u64::from(left > right);
            }
        }
        Some(format!("{:016x}", bits))
    }
}

/// `pair_review.hamming`: bit distance between two hex hashes.
pub fn hamming(a: &str, b: &str) -> u32 {
    match (u64::from_str_radix(a, 16), u64::from_str_radix(b, 16)) {
        (Ok(x), Ok(y)) => (x ^ y).count_ones(),
        _ => 999,
    }
}

fn hash_store_path(key: &str, dir: &Path) -> PathBuf {
    dir.join(format!("{}-hashes.json", key))
}

/// `pair_review.hashes`: each picture's dHash, cached in the `<key>-hashes.json` store beside the session.
fn hashes(paths: &[String], store: &Path) -> std::collections::HashMap<String, Option<String>> {
    let mut known = read_json_object(store);
    let mut out = std::collections::HashMap::new();
    let mut changed = false;
    for path in paths {
        if let Some(value) = known.get(path) {
            out.insert(path.clone(), value.as_str().map(|s| s.to_string()));
            continue;
        }
        let hash = dhash(Path::new(path));
        known.insert(path.clone(), hash.as_deref().map(Value::from).unwrap_or(Value::Null));
        changed = true;
        out.insert(path.clone(), hash);
    }
    if changed {
        // `json.dumps(out, indent=0)`: one entry per line, no indentation
        let _ = write_json(&Value::Object(known), store, b"");
    }
    out
}

// ---- the schedule ------------------------------------------------------------------------------------------------

/// `pair_review.rounds_for`: log2(n) rounds, at least two.
pub fn rounds_for(count: usize) -> i64 {
    let n = count.max(2) as f64;
    (n.log2().ceil() as i64).max(2)
}

/// `pair_review.pair_up`: greedy disjoint pairing, most similar first.
fn pair_up(paths: &[String], hashes: &std::collections::HashMap<String, Option<String>>)
    -> (Vec<(String, String, i64)>, Vec<String>) {
    let items: Vec<(&String, &String)> = paths
        .iter()
        .filter_map(|p| hashes.get(p).and_then(|h| h.as_ref()).map(|h| (h, p)))
        .collect();
    let mut scored: Vec<(u32, &String, &String)> = Vec::new();
    for i in 0..items.len() {
        for j in (i + 1)..items.len() {
            scored.push((hamming(items[i].0, items[j].0), items[i].1, items[j].1));
        }
    }
    scored.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(b.1)).then(a.2.cmp(b.2)));
    let mut used: std::collections::HashSet<&String> = std::collections::HashSet::new();
    let mut pairs = Vec::new();
    for (dist, a, b) in scored {
        if used.contains(a) || used.contains(b) {
            continue;
        }
        used.insert(a);
        used.insert(b);
        pairs.push((a.clone(), b.clone(), dist as i64));
    }
    let leftovers = items.iter().map(|(_, p)| (*p).clone()).filter(|p| !used.contains(p)).collect();
    (pairs, leftovers)
}

/// `pair_review.build`: fill `pairs` from a picture list, honouring decisions already recorded. Idempotent.
///
/// Up to `EXHAUSTIVE_MAX` pictures this is the complete graph, most similar pair first; beyond that it is one round of
/// the most-similar matching and `next_round` takes over. The distance only orders the questions.
/// The key is built from the pictures' *names*, so two folders whose pictures share those names map to the same session
/// file. The stored paths decide which folder a session belongs to: if they name a different folder, this is a new session
/// and the old pairs and decisions must not be inherited (measured: a review opened on a second folder drew the first
/// folder's pictures).
fn same_folder(a: &[String], b: &[String]) -> bool {
    let norm = |list: &[String]| {
        let mut set: Vec<String> = list
            .iter()
            .map(|p| p.replace('/', "\\").to_lowercase())
            .collect();
        set.sort();
        set.dedup();
        set
    };
    norm(a) == norm(b)
}

pub fn build(paths: &[String], state: &mut Value, dir: &Path) {
    let stored: Vec<String> = state
        .get("paths")
        .and_then(|v| v.as_array())
        .map(|list| list.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();
    if !stored.is_empty() && !same_folder(&stored, paths) {
        let key = state.get("key").cloned().unwrap_or(Value::Null);
        *state = json!({
            "key": key,
            "created": now(),
            "decisions": {},
            "pairs": Value::Null,
        });
    }
    if state.get("pairs").map(|p| !p.is_null()).unwrap_or(false) {
        return;
    }
    let key = state.get("key").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    let paths_json: Vec<Value> = paths.iter().map(|p| Value::from(p.clone())).collect();
    if let Some(object) = state.as_object_mut() {
        object.insert("paths".to_string(), Value::Array(paths_json));
        object.insert("target_rounds".to_string(), Value::from(rounds_for(paths.len())));
        object.insert("round".to_string(), Value::from(1));
    }
    let got = hashes(paths, &hash_store_path(&key, dir));
    if paths.len() <= EXHAUSTIVE_MAX {
        let mut scored: Vec<(u32, String, String)> = Vec::new();
        for i in 0..paths.len() {
            for j in (i + 1)..paths.len() {
                let (a, b) = (&paths[i], &paths[j]);
                let dist = match (got.get(a).and_then(|h| h.as_ref()), got.get(b).and_then(|h| h.as_ref())) {
                    (Some(ha), Some(hb)) => hamming(ha, hb),
                    _ => 999,
                };
                scored.push((dist, a.clone(), b.clone()));
            }
        }
        scored.sort_by(|x, y| x.0.cmp(&y.0).then(x.1.cmp(&y.1)).then(x.2.cmp(&y.2)));
        let pairs: Vec<Value> = scored
            .iter()
            .map(|(d, a, b)| json!([a, b, *d as i64]))
            .collect();
        let total = pairs.len() as i64;
        if let Some(object) = state.as_object_mut() {
            object.insert("pairs".to_string(), Value::Array(pairs));
            object.insert("leftovers".to_string(), Value::Array(Vec::new()));
            object.insert("exhaustive".to_string(), Value::Bool(true));
            object.insert("round".to_string(), Value::from(1));
            object.insert("target_rounds".to_string(), Value::from(1));
            object.insert("total".to_string(), Value::from(total));
        }
        return;
    }
    let (pairs, leftovers) = pair_up(paths, &got);
    let pairs_json: Vec<Value> = pairs.iter().map(|(a, b, d)| json!([a, b, d])).collect();
    let total = pairs.len() as i64;
    if let Some(object) = state.as_object_mut() {
        object.insert("pairs".to_string(), Value::Array(pairs_json));
        object.insert(
            "leftovers".to_string(),
            Value::Array(leftovers.iter().map(|p| Value::from(p.clone())).collect()),
        );
        object.insert("exhaustive".to_string(), Value::Bool(false));
        object.insert("total".to_string(), Value::from(total));
    }
}

fn pairs_of(state: &Value) -> Vec<(String, String, i64)> {
    state
        .get("pairs")
        .and_then(|v| v.as_array())
        .map(|rows| {
            rows.iter()
                .filter_map(|row| {
                    let row = row.as_array()?;
                    Some((
                        row.first()?.as_str()?.to_string(),
                        row.get(1)?.as_str()?.to_string(),
                        row.get(2).and_then(|d| d.as_i64()).unwrap_or(0),
                    ))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn decisions_of(state: &Value) -> Map<String, Value> {
    state
        .get("decisions")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default()
}

/// `pair_review.remaining`: the pairs still undecided, in order.
pub fn remaining(state: &Value) -> Vec<(String, String, i64)> {
    let done = decisions_of(state);
    pairs_of(state)
        .into_iter()
        .filter(|(a, b, _)| !done.contains_key(&pair_id(a, b)))
        .collect()
}

#[derive(Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord)]
struct Record {
    wins: i64,
    losses: i64,
    ties: i64,
}

/// `pair_review._session_record`: {name: (wins, losses, ties)} from *this* review's decisions, not the sidecars.
fn session_record(state: &Value) -> std::collections::BTreeMap<String, Record> {
    let mut scores: std::collections::BTreeMap<String, Record> = std::collections::BTreeMap::new();
    for decided in decisions_of(state).values() {
        let left = decided.get("left").and_then(|v| v.as_str()).unwrap_or_default().to_string();
        let right = decided.get("right").and_then(|v| v.as_str()).unwrap_or_default().to_string();
        let choice = decided.get("choice").and_then(|v| v.as_str()).unwrap_or_default();
        scores.entry(left.clone()).or_default();
        scores.entry(right.clone()).or_default();
        match choice {
            "left_wins" => {
                scores.get_mut(&left).unwrap().wins += 1;
                scores.get_mut(&right).unwrap().losses += 1;
            }
            "right_wins" => {
                scores.get_mut(&right).unwrap().wins += 1;
                scores.get_mut(&left).unwrap().losses += 1;
            }
            "tie" => {
                scores.get_mut(&left).unwrap().ties += 1;
                scores.get_mut(&right).unwrap().ties += 1;
            }
            _ => {}
        }
    }
    scores
}

/// `pair_review.order`: the pictures in the order this review put them in - most wins, fewest losses, then by name.
pub fn order(state: &Value) -> Vec<String> {
    let scores = session_record(state);
    let mut paths: Vec<String> = state
        .get("paths")
        .and_then(|v| v.as_array())
        .map(|rows| rows.iter().filter_map(|p| p.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();
    paths.sort_by(|a, b| {
        let sa = scores.get(&name_of(a)).copied().unwrap_or_default();
        let sb = scores.get(&name_of(b)).copied().unwrap_or_default();
        (-sa.wins, sa.losses, name_of(a)).cmp(&(-sb.wins, sb.losses, name_of(b)))
    });
    paths
}

/// `pair_review._session_order`: the order the decisions currently imply, cheap enough to notice no change.
fn session_order(state: &Value) -> Vec<String> {
    let scores = session_record(state);
    let mut names: Vec<String> = state
        .get("paths")
        .and_then(|v| v.as_array())
        .map(|rows| rows.iter().filter_map(|p| p.as_str().map(|s| name_of(s))).collect())
        .unwrap_or_default();
    names.sort();
    names.dedup();
    names.sort_by(|a, b| {
        let sa = scores.get(a).copied().unwrap_or_default();
        let sb = scores.get(b).copied().unwrap_or_default();
        (-sa.wins, sa.losses, a.clone()).cmp(&(-sb.wins, sb.losses, b.clone()))
    });
    names
}

/// `pair_review.next_round`: equal records paired, never a rematch, until the order stops moving.
///
/// Round one is the most-similar matching; after that, winners meet winners - which is what makes log2(n) rounds enough
/// for a total order instead of n^2 questions. Returns the pairs appended (and extends `state['pairs']`).
pub fn next_round(state: &mut Value) -> Vec<(String, String, i64)> {
    let paths: Vec<String> = state
        .get("paths")
        .and_then(|v| v.as_array())
        .map(|rows| rows.iter().filter_map(|p| p.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();
    if paths.is_empty() {
        return Vec::new();
    }
    let target = state
        .get("target_rounds")
        .and_then(|v| v.as_i64())
        .unwrap_or_else(|| rounds_for(paths.len()));
    let done_round = state.get("round").and_then(|v| v.as_i64()).unwrap_or(1);
    if done_round >= target {
        return Vec::new();
    }
    let current = session_order(state);
    let previous: Option<Vec<String>> = state
        .get("last_order")
        .and_then(|v| v.as_array())
        .map(|rows| rows.iter().filter_map(|p| p.as_str().map(|s| s.to_string())).collect());
    if let Some(previous) = previous {
        if previous == current {
            if let Some(object) = state.as_object_mut() {
                object.insert("settled".to_string(), Value::Bool(true));
            }
            return Vec::new();
        }
    }
    if let Some(object) = state.as_object_mut() {
        object.insert(
            "last_order".to_string(),
            Value::Array(current.iter().map(|n| Value::from(n.clone())).collect()),
        );
    }
    let decided: std::collections::HashSet<String> =
        decisions_of(state).into_iter().map(|(id, _)| id).collect();
    let scores = session_record(state);
    // bucket by record, strongest first, each bucket by name
    let mut buckets: std::collections::BTreeMap<(i64, i64, i64), Vec<String>> = std::collections::BTreeMap::new();
    let mut by_name: Vec<(String, String)> = paths.iter().map(|p| (name_of(p), p.clone())).collect();
    by_name.sort();
    for (name, path) in &by_name {
        let r = scores.get(name).copied().unwrap_or_default();
        buckets.entry((-r.wins, r.losses, r.ties)).or_default().push(path.clone());
    }
    let mut queue: Vec<String> = Vec::new();
    for key in buckets.keys() {
        let mut bucket = buckets[key].clone();
        bucket.sort_by_key(|p| name_of(p));
        queue.extend(bucket);
    }
    let mut taken: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut pairs = Vec::new();
    while queue.len() > 1 {
        let a = queue.remove(0);
        if taken.contains(&a) {
            continue;
        }
        let mut partner: Option<String> = None;
        for b in &queue {
            if taken.contains(b) || *b == a {
                continue;
            }
            if decided.contains(&pair_id(&a, b)) {
                continue;
            }
            partner = Some(b.clone());
            break;
        }
        if partner.is_none() {
            // a rematch beats dropping a picture from the round
            partner = queue.iter().find(|b| !taken.contains(*b) && **b != a).cloned();
        }
        let Some(partner) = partner else { continue };
        queue.retain(|p| *p != partner);
        taken.insert(a.clone());
        taken.insert(partner.clone());
        pairs.push((a, partner, 0));
    }
    if pairs.is_empty() {
        return Vec::new();
    }
    let mut rows = pairs_of(state);
    rows.extend(pairs.iter().cloned());
    let pairs_json: Vec<Value> = rows.iter().map(|(a, b, d)| json!([a, b, d])).collect();
    if let Some(object) = state.as_object_mut() {
        object.insert("pairs".to_string(), Value::Array(pairs_json));
        object.insert("round".to_string(), Value::from(done_round + 1));
    }
    pairs
}

/// `pair_review.progress`: (decided, total, deletes).
pub fn progress(state: &Value) -> (i64, i64, i64) {
    let decisions = decisions_of(state);
    let deletes = decisions
        .values()
        .filter(|d| d.get("choice").and_then(|c| c.as_str()).map(|c| c.starts_with("delete")).unwrap_or(false))
        .count() as i64;
    let total = state
        .get("total")
        .and_then(|v| v.as_i64())
        .filter(|t| *t > 0)
        .unwrap_or_else(|| pairs_of(state).len() as i64);
    (decisions.len() as i64, total, deletes)
}

// ---- the picture's own record (pair_rank) -------------------------------------------------------------------------

/// `pair_rank.sidecar`: the `.json` beside the picture, which already carries its prompt.
pub fn sidecar(path: &str) -> PathBuf {
    Path::new(path).with_extension("json")
}

fn read_meta(path: &str) -> Map<String, Value> {
    read_json_object(&sidecar(path))
}

fn write_meta(path: &str, meta: &Map<String, Value>) -> anyhow::Result<()> {
    write_json(&Value::Object(meta.clone()), &sidecar(path), b" ")
}

fn counter(meta: &Map<String, Value>, key: &str) -> i64 {
    meta.get(key).and_then(|v| v.as_i64()).unwrap_or(0).max(0)
}

/// `pair_rank._bump`: one bout for one side of one decision.
///
/// `win` is `Some(true)` for a win, `Some(false)` for a loss, `None` for a tie. `delta` is -1 when a decision is
/// retracted, and every counter is clamped at zero so an undo after a reset cannot poison a ranking.
fn bump(path: &str, win: Option<bool>, when: &str, delta: i64, opponent: &str) -> anyhow::Result<()> {
    let mut meta = read_meta(path);
    if meta.is_empty() {
        meta.insert("image".to_string(), Value::from(path));
    }
    meta.insert("bouts".to_string(), Value::from((counter(&meta, "bouts") + delta).max(0)));
    let key = match win {
        Some(true) => "wins",
        Some(false) => "losses",
        None => "ties",
    };
    meta.insert(key.to_string(), Value::from((counter(&meta, key) + delta).max(0)));

    let mut vs = match meta.get("vs").and_then(|v| v.as_object()) {
        Some(map) => map.clone(),
        None => Map::new(),
    };
    let rival = name_of(opponent);
    let mut row: Vec<i64> = vs
        .get(&rival)
        .and_then(|v| v.as_array())
        .map(|row| row.iter().map(|x| x.as_i64().unwrap_or(0)).collect())
        .unwrap_or_default();
    while row.len() < 3 {
        row.push(0);
    }
    let slot = match win {
        Some(true) => 0,
        Some(false) => 1,
        None => 2,
    };
    row[slot] = (row[slot] + delta).max(0);
    vs.insert(rival, json!([row[0], row[1], row[2]]));
    meta.insert("vs".to_string(), Value::Object(vs));

    if delta > 0 {
        meta.insert("last_bout".to_string(), Value::from(when));
    } else if meta.get("last_bout").and_then(|v| v.as_str()) == Some(when) {
        meta.remove("last_bout");
    }
    write_meta(path, &meta)
}

/// `pair_rank.record`: apply one decision. Win choices update both sidecars; delete choices return the path to delete.
pub fn record(state: &mut Value, a: &str, b: &str, choice: &str) -> anyhow::Result<Option<String>> {
    let when = now();
    let id = pair_id(a, b);
    if DELETE_CHOICES.contains(&choice) {
        // transcribed so a session written by the delete mode is readable here; the window never asks for it
        let mut decisions = decisions_of(state);
        decisions.insert(
            id,
            json!({"choice": choice, "left": name_of(a), "right": name_of(b), "at": when}),
        );
        if let Some(object) = state.as_object_mut() {
            object.insert("decisions".to_string(), Value::Object(decisions));
        }
        return Ok(match choice {
            "delete_left" => Some(a.to_string()),
            "delete_right" => Some(b.to_string()),
            _ => None,
        });
    }
    if !WIN_CHOICES.contains(&choice) {
        anyhow::bail!("unknown choice {:?}", choice);
    }
    match choice {
        "left_wins" => {
            bump(a, Some(true), &when, 1, b)?;
            bump(b, Some(false), &when, 1, a)?;
        }
        "right_wins" => {
            bump(b, Some(true), &when, 1, a)?;
            bump(a, Some(false), &when, 1, b)?;
        }
        _ => {
            bump(a, None, &when, 1, b)?;
            bump(b, None, &when, 1, a)?;
        }
    }
    let mut decisions = decisions_of(state);
    decisions.insert(
        id,
        // the Python's key order for this dict: choice, left, right, at, mode
        json!({"choice": choice, "left": name_of(a), "right": name_of(b), "at": when, "mode": "rank"}),
    );
    if let Some(object) = state.as_object_mut() {
        object.insert("decisions".to_string(), Value::Object(decisions));
    }
    Ok(None)
}

/// `pair_rank.unrecord`: undo the recorded decision for one pair, leaving the sidecars exactly as they were.
pub fn unrecord(state: &mut Value, a: &str, b: &str) -> Option<String> {
    let id = pair_id(a, b);
    let mut decisions = decisions_of(state);
    let decided = decisions.remove(&id)?;
    if let Some(object) = state.as_object_mut() {
        object.insert("decisions".to_string(), Value::Object(decisions));
    }
    let choice = decided.get("choice").and_then(|c| c.as_str()).unwrap_or_default().to_string();
    let when = decided
        .get("at")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(now);
    match choice.as_str() {
        "left_wins" => {
            let _ = bump(a, Some(true), &when, -1, b);
            let _ = bump(b, Some(false), &when, -1, a);
        }
        "right_wins" => {
            let _ = bump(b, Some(true), &when, -1, a);
            let _ = bump(a, Some(false), &when, -1, b);
        }
        "tie" => {
            let _ = bump(a, None, &when, -1, b);
            let _ = bump(b, None, &when, -1, a);
        }
        _ => {}
    }
    Some(choice)
}

/// `pair_rank.record_of`: (wins, losses, ties, bouts).
pub fn record_of(path: &str) -> (i64, i64, i64, i64) {
    let meta = read_meta(path);
    (
        counter(&meta, "wins"),
        counter(&meta, "losses"),
        counter(&meta, "ties"),
        counter(&meta, "bouts"),
    )
}

/// `pair_rank.badge`: '4W', '4W 1L', '1T', or "" when the picture has no record.
pub fn badge(path: &str) -> String {
    let (wins, losses, ties, bouts) = record_of(path);
    if bouts == 0 {
        return String::new();
    }
    let mut out = if wins > 0 { format!("{}W", wins) } else { String::new() };
    if losses > 0 {
        if !out.is_empty() {
            out.push(' ');
        }
        out.push_str(&format!("{}L", losses));
    }
    if ties > 0 && out.is_empty() {
        out = format!("{}T", ties);
    }
    out
}

/// `pair_rank.reset_wins`: forget every record in a set of pictures. The prompt and every other key stay.
pub fn reset_wins(paths: &[String]) -> usize {
    let mut touched = 0;
    for path in paths {
        let mut meta = read_meta(path);
        if meta.is_empty() {
            continue;
        }
        let mut changed = false;
        for key in META_KEYS.iter().chain(["vs"].iter()) {
            if meta.remove(*key).is_some() {
                changed = true;
            }
        }
        if changed && write_meta(path, &meta).is_ok() {
            touched += 1;
        }
    }
    touched
}

// ---- the window's two lines of prose -----------------------------------------------------------------------------

/// `pair_review.estimate_minutes`.
pub fn estimate_minutes(pairs: usize) -> f64 {
    pairs as f64 * DECISION_SECONDS / 60.0
}

/// `review_tally`: how a review reports itself. A ranking never deletes, so it never says "0 deleted".
pub fn tally(decided: i64, total: i64, left: Option<(&str, &str)>) -> String {
    if let Some((stage, minutes)) = left {
        return format!(
            "pair {} of {} \u{b7} {} \u{b7} ~{} left at 3 s each \u{b7} {} decided, nothing deleted",
            decided + 1,
            total,
            stage,
            minutes,
            decided
        );
    }
    format!("{} of {} pairs decided, nothing deleted", decided, total)
}

/// The stage label the tally uses: `every pair`, or `round n of m`.
pub fn stage_label(state: &Value) -> String {
    if state.get("exhaustive").and_then(|v| v.as_bool()).unwrap_or(false) {
        return "every pair".to_string();
    }
    format!(
        "round {} of {}",
        state.get("round").and_then(|v| v.as_i64()).unwrap_or(1),
        state.get("target_rounds").and_then(|v| v.as_i64()).unwrap_or(1)
    )
}

/// The paths a review is about, as the state file has them.
pub fn session_paths(state: &Value) -> Vec<String> {
    state
        .get("paths")
        .and_then(|v| v.as_array())
        .map(|rows| rows.iter().filter_map(|p| p.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha1_matches_the_published_vector() {
        assert_eq!(sha1_hex(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(sha1_hex(b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
    }

    #[test]
    fn pair_id_is_order_free_and_name_only() {
        assert_eq!(pair_id(r"C:\pics\b.png", r"C:\pics\a.png"), "a.png|b.png");
        assert_eq!(pair_id(r"D:\x\a.png", r"C:\pics\b.png"), "a.png|b.png");
    }

    #[test]
    fn state_key_is_the_sha1_prefix_of_sorted_names() {
        // cross-checked against pair_review.state_key by --selftest; this pins the construction
        let paths = vec![r"C:\g\b.png".to_string(), r"C:\g\a.png".to_string()];
        let expected = sha1_hex(b"a.png\nb.png")[..16].to_string();
        assert_eq!(state_key(&paths), expected);
        assert_eq!(state_key(&paths).len(), 16);
        // order of the input does not matter, names do
        let mut reversed = paths.clone();
        reversed.reverse();
        assert_eq!(state_key(&paths), state_key(&reversed));
    }

    #[test]
    fn order_ranks_by_session_decisions_only() {
        let mut state = json!({
            "paths": [r"C:\g\a.png", r"C:\g\b.png", r"C:\g\c.png", r"C:\g\d.png"],
            "decisions": {
                "a.png|b.png": {"choice": "left_wins", "left": "a.png", "right": "b.png"},
                "a.png|c.png": {"choice": "left_wins", "left": "a.png", "right": "c.png"},
                "b.png|c.png": {"choice": "right_wins", "left": "b.png", "right": "c.png"},
                "a.png|d.png": {"choice": "tie", "left": "a.png", "right": "d.png"},
            },
        });
        // a: 2W 0L 1T, c: 1W 1L, d: 0W 0L 1T (a tie is neither), b: 0W 2L - so most wins first, then fewest losses
        assert_eq!(order(&state), vec![
            r"C:\g\a.png".to_string(),
            r"C:\g\c.png".to_string(),
            r"C:\g\d.png".to_string(),
            r"C:\g\b.png".to_string()
        ]);
        state["paths"] = json!([]);
        assert!(order(&state).is_empty());
    }

    #[test]
    fn progress_counts_decisions_not_pairs() {
        let state = json!({
            "pairs": [["a.png", "b.png", 1], ["c.png", "d.png", 2]],
            "decisions": {"a.png|b.png": {"choice": "left_wins", "left": "a.png", "right": "b.png"}},
        });
        assert_eq!(progress(&state), (1, 2, 0));
    }

    #[test]
    fn next_round_never_asks_the_same_pair_twice() {
        let paths: Vec<String> = (0..8).map(|i| format!(r"C:\g\p{}.png", i)).collect();
        let mut state = json!({
            "paths": paths,
            "pairs": [[*paths[0].as_str(), *paths[1].as_str()], null, null],
            "round": 1,
            "target_rounds": 3,
            "decisions": {},
        });
        state["pairs"] = json!([
            [paths[0], paths[1], 0], [paths[2], paths[3], 0], [paths[4], paths[5], 0], [paths[6], paths[7], 0]
        ]);
        let mut decisions = Map::new();
        for i in 0..4 {
            let (a, b) = (&paths[2 * i], &paths[2 * i + 1]);
            decisions.insert(
                pair_id(a, b),
                json!({"choice": "left_wins", "left": name_of(a), "right": name_of(b)}),
            );
        }
        state["decisions"] = Value::Object(decisions);
        let more = next_round(&mut state);
        assert_eq!(more.len(), 4, "a round of eight pictures is four pairs");
        let done = decisions_of(&state);
        for (a, b, _) in &more {
            assert!(!done.contains_key(&pair_id(a, b)), "{a} vs {b} was already decided");
        }
    }
}
