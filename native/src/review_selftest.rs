//! `--review-selftest`: run a whole A/B review headlessly and check it against the Python implementation.
//!
//! It builds a four-picture gallery in a temp directory, writes the sidecars the gallery scan reads, and then does what
//! the window does - `build`, decide every pair, `order` - with no window and no screen. What it asserts is the thing
//! this ticket is about: **the session must survive being opened in the other app**. So the state file and the sidecars
//! are handed to `pair_review.py`/`pair_rank.py` themselves (the repo's own copy, imported by the Python that is
//! installed) and the two answers are compared; and a decision made *by* the Python is then read back *by* this code.
//!
//! Nothing here touches `~/.local/zimage-ui/review`: every state directory is passed explicitly.

use crate::review;
use std::path::{Path, PathBuf};
use std::process::Command;

/// The one place a failure is counted, so the exit code means "every check passed".
struct Checks {
    failed: usize,
}

impl Checks {
    fn confirm(&mut self, what: &str, ok: bool, detail: impl std::fmt::Display) {
        if ok {
            println!("  ok    {:<52} {}", what, detail);
        } else {
            self.failed += 1;
            println!("  FAIL  {:<52} {}", what, detail);
        }
    }

    fn same<T: PartialEq + std::fmt::Debug>(&mut self, what: &str, got: &T, want: &T) {
        let ok = got == want;
        self.confirm(what, ok, format!("got {:?} / want {:?}", got, want));
    }
}

pub fn run() -> anyhow::Result<()> {
    let root = std::env::temp_dir().join("zimage-review-selftest");
    if root.exists() {
        let _ = std::fs::remove_dir_all(&root);
    }
    std::fs::create_dir_all(&root)?;
    let gallery = root.join("gallery");
    std::fs::create_dir_all(&gallery)?;

    let names = ["a.png", "b.png", "c.png", "d.png"];
    for (i, name) in names.iter().enumerate() {
        make_picture(&gallery.join(name), i)?;
        // the sidecar the gallery scan reads: one prompt root, so all four are one family and one A/B session
        let sidecar = serde_json::json!({
            "prompt": format!("selftest take {} - a red apple on a wooden table, soft window light", i + 1),
            "lineage_root": "selftest: a red apple on a wooden table, soft window light",
            "seed": 4711 + i,
        });
        std::fs::write(gallery.join(name).with_extension("json"), serde_json::to_string_pretty(&sidecar)?)?;
    }
    let paths: Vec<String> = names
        .iter()
        .map(|n| gallery.join(n).to_string_lossy().to_string())
        .collect();

    let mut checks = Checks { failed: 0 };
    println!("review selftest  gallery {}", gallery.display());
    println!("                 state   (a temp dir - the real {} is untouched)", review::state_dir().display());

    // ---- the session, built and decided exactly as the window does it ----
    let dir = root.join("state");
    std::fs::create_dir_all(&dir)?;
    let key = review::state_key(&paths);
    let mut state = review::load_state(&key, &dir);
    review::build(&paths, &mut state, &dir);
    let built = review::progress(&state);
    checks.same("four pictures build six pairs (the complete graph)", &built.1, &6);
    checks.confirm(
        "the schedule is exhaustive and one round",
        state.get("exhaustive").and_then(|v| v.as_bool()) == Some(true)
            && state.get("round").and_then(|v| v.as_i64()) == Some(1)
            && state.get("target_rounds").and_then(|v| v.as_i64()) == Some(1),
        format!(
            "exhaustive={:?} round={:?} target={:?}",
            state.get("exhaustive"),
            state.get("round"),
            state.get("target_rounds")
        ),
    );
    let pairs_written = state.get("pairs").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    checks.same("state['pairs'] carries all six", &pairs_written, &6);
    checks.same("state['paths'] is the picture list", &review::session_paths(&state), &paths);

    // the choices the window would produce from 1/2/3, one per pair in schedule order
    let script = ["left_wins", "tie", "right_wins", "left_wins", "tie", "right_wins"];
    let queue = review::remaining(&state);
    let mut chosen: Vec<(String, String, &str)> = Vec::new();
    for (i, (a, b, _)) in queue.iter().enumerate() {
        let choice = script[i % script.len()];
        review::record(&mut state, a, b, choice)?;
        chosen.push((a.clone(), b.clone(), choice));
    }
    let after = review::progress(&state);
    checks.same("all six pairs decided", &after.0, &6);
    checks.same("nothing was deleted", &after.2, &0);
    checks.same("no pair is left to ask", &review::remaining(&state).len(), &0);
    let saved = review::save_state(&mut state, &dir)?;

    // ---- the file's shape ----
    let parsed: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&saved)?)?;
    let mut keys: Vec<String> = parsed.as_object().map(|o| o.keys().cloned().collect()).unwrap_or_default();
    keys.sort();
    let want_keys = vec![
        "created", "decisions", "exhaustive", "key", "leftovers", "pairs", "paths", "round", "target_rounds", "total",
        "updated",
    ];
    checks.confirm(
        "the session file has the Python's keys",
        keys == want_keys,
        format!("{:?}", keys),
    );
    checks.same("the key in the file is the state key", &parsed["key"].as_str().unwrap_or("").to_string(), &key);
    checks.confirm(
        "the key is 16 hex digits",
        key.len() == 16 && key.chars().all(|c| c.is_ascii_hexdigit()),
        &key,
    );
    let decision_ok = parsed["decisions"].as_object().map(|map| {
        map.len() == 6
            && map.iter().all(|(id, d)| {
                id.split('|').count() == 2
                    && d.get("left").and_then(|v| v.as_str()).is_some()
                    && d.get("right").and_then(|v| v.as_str()).is_some()
                    && d.get("mode").and_then(|v| v.as_str()) == Some("rank")
                    && matches!(d.get("choice").and_then(|v| v.as_str()), Some("left_wins" | "right_wins" | "tie"))
                    && d.get("at").and_then(|v| v.as_str()).map(|s| s.len() == 19 && s.as_bytes()[10] == b'T')
                        == Some(true)
            })
    }) == Some(true);
    checks.confirm(
        "each decision is {choice, left, right, at, mode} keyed by 'name|name'",
        decision_ok,
        format!("{} decisions", parsed["decisions"].as_object().map(|m| m.len()).unwrap_or(0)),
    );
    checks.confirm(
        "pairs are [path, path, distance]",
        parsed["pairs"].as_array().map(|rows| {
            rows.iter().all(|r| {
                let row = r.as_array().map(|v| v.len()).unwrap_or(0);
                row == 3 && r[0].is_string() && r[1].is_string() && r[2].is_i64()
            })
        }) == Some(true),
        "3 elements, two strings and a distance",
    );

    // ---- the sidecars: the wins have to be in the pictures ----
    let mut expected: std::collections::BTreeMap<String, (i64, i64, i64)> = std::collections::BTreeMap::new();
    for name in &names {
        expected.insert(name.to_string(), (0, 0, 0));
    }
    for (a, b, choice) in &chosen {
        let (na, nb) = (review::name_of(a), review::name_of(b));
        match *choice {
            "left_wins" => {
                expected.get_mut(&na).unwrap().0 += 1;
                expected.get_mut(&nb).unwrap().1 += 1;
            }
            "right_wins" => {
                expected.get_mut(&nb).unwrap().0 += 1;
                expected.get_mut(&na).unwrap().1 += 1;
            }
            _ => {
                expected.get_mut(&na).unwrap().2 += 1;
                expected.get_mut(&nb).unwrap().2 += 1;
            }
        }
    }
    let mut sidecars_ok = true;
    let mut line = String::new();
    for (name, (wins, losses, ties)) in &expected {
        let path = gallery.join(name);
        let path = path.to_string_lossy().to_string();
        let (got_w, got_l, got_t, bouts) = review::record_of(&path);
        if (got_w, got_l, got_t) != (*wins, *losses, *ties) || bouts != wins + losses + ties {
            sidecars_ok = false;
        }
        let meta: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(gallery.join(name).with_extension("json"))?)?;
        // the prompt is still there: the record is added to the sidecar, never instead of it
        if meta.get("prompt").and_then(|v| v.as_str()).is_none() || meta.get("seed").is_none() {
            sidecars_ok = false;
        }
        let vs_ok = meta.get("vs").and_then(|v| v.as_object()).map(|vs| {
            vs.values().all(|row| {
                row.as_array().map(|r| r.len() == 3 && r.iter().all(|x| x.is_i64())).unwrap_or(false)
            })
        }) == Some(true);
        if !vs_ok {
            sidecars_ok = false;
        }
        line.push_str(&format!("{} {}-{}-{}T  ", name, wins, losses, ties));
    }
    checks.confirm(
        "wins/losses/ties/bouts land in each picture's sidecar",
        sidecars_ok,
        line.trim_end(),
    );

    // ---- order ----
    let order = review::order(&state);
    let mut want_order: Vec<String> = names.iter().map(|n| n.to_string()).collect();
    want_order.sort_by(|a, b| {
        let sa = expected.get(a).copied().unwrap_or((0, 0, 0));
        let sb = expected.get(b).copied().unwrap_or((0, 0, 0));
        (-sa.0, sa.1, a.clone()).cmp(&(-sb.0, sb.1, b.clone()))
    });
    checks.same("the order is this review's record, best first", &names_of(&order), &want_order);

    // ---- the Python agrees: same key, same order, same progress ----
    let mut python_ran = false;
    if let Some(answer) = python(&[
        "-c",
        PY_CHECK,
        &saved.to_string_lossy(),
    ]) {
        python_ran = true;
        let value: serde_json::Value = serde_json::from_str(&answer).unwrap_or(serde_json::Value::Null);
        checks.same(
            "pair_review.state_key() reproduces the key",
            &value["key"].as_str().unwrap_or("-").to_string(),
            &key,
        );
        checks.same(
            "pair_review.order() gives this order",
            &value["order"],
            &serde_json::json!(order),
        );
        checks.same(
            "pair_review.progress() gives (6, 6, 0)",
            &value["progress"],
            &serde_json::json!([6, 6, 0]),
        );
        checks.same(
            "pair_review reads the same keys",
            &value["keys"],
            &serde_json::json!(want_keys),
        );
    } else {
        println!("  SKIP  python cross-check (no python found, or pair_review not importable)");
    }

    // ---- the other direction: the Python decides, this app reads it ----
    //
    // A second, untouched copy of the gallery: a win belongs to the *picture*, so writing into the first copy's sidecars
    // would make every count here depend on what phase A did. The counts below are therefore absolute.
    if python_ran {
        let gallery2 = root.join("gallery2");
        std::fs::create_dir_all(&gallery2)?;
        for (i, name) in names.iter().enumerate() {
            make_picture(&gallery2.join(name), i)?;
            let sidecar = serde_json::json!({
                "prompt": format!("selftest take {} - a red apple on a wooden table, soft window light", i + 1),
                "lineage_root": "selftest: a red apple on a wooden table, soft window light",
                "seed": 4711 + i,
            });
            std::fs::write(gallery2.join(name).with_extension("json"), serde_json::to_string_pretty(&sidecar)?)?;
        }
        let paths2: Vec<String> = names.iter().map(|n| gallery2.join(n).to_string_lossy().to_string()).collect();
        let dir2 = root.join("state-python");
        std::fs::create_dir_all(&dir2)?;
        let mut fresh = review::load_state(&review::state_key(&paths2), &dir2);
        review::build(&paths2, &mut fresh, &dir2);
        review::save_state(&mut fresh, &dir2)?;
        let key2 = review::state_key(&paths2);
        if let Some(answer) = python(&["-c", PY_RECORD, &key2, &dir2.to_string_lossy()]) {
            let decided: serde_json::Value = serde_json::from_str(&answer).unwrap_or(serde_json::Value::Null);
            let a = decided["pair"][0].as_str().unwrap_or_default().to_string();
            let b = decided["pair"][1].as_str().unwrap_or_default().to_string();
            let mut back = review::load_state(&key2, &dir2);
            let choices: Vec<String> = back
                .get("decisions")
                .and_then(|v| v.as_object())
                .map(|map| {
                    map.values()
                        .filter_map(|d| d.get("choice").and_then(|c| c.as_str()).map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            checks.same(
                "a decision written by pair_rank.record is read back",
                &choices,
                &vec!["right_wins".to_string()],
            );
            checks.confirm(
                "the Python found the session under the same key",
                dir2.join(format!("{}.json", key2)).exists(),
                format!("{}.json", key2),
            );
            let rank = review::order(&back);
            checks.same(
                "and it ranks the winner first",
                &rank.first().map(|p| review::name_of(p)).unwrap_or_default(),
                &review::name_of(&b),
            );
            checks.same("the winner's sidecar carries the win", &review::record_of(&b), &(1, 0, 0, 1));
            checks.same("the loser's sidecar carries the loss", &review::record_of(&a), &(0, 1, 0, 1));
            let meta: serde_json::Value =
                serde_json::from_str(&std::fs::read_to_string(review::sidecar(&b))?)?;
            checks.confirm(
                "the Python's own `vs` tally and `last_bout` are read back",
                meta.get("vs").and_then(|v| v.as_object()).map(|vs| vs.contains_key(&review::name_of(&a))) == Some(true)
                    && meta.get("last_bout").and_then(|v| v.as_str()).is_some(),
                review::badge(&b),
            );
            // and the retraction: hand the counters back to nothing
            review::unrecord(&mut back, &a, &b);
            review::save_state(&mut back, &dir2)?;
            checks.same(
                "unrecord hands the counters back",
                &(review::record_of(&b), review::record_of(&a)),
                &((0, 0, 0, 0), (0, 0, 0, 0)),
            );
            if let Some(answer) = python(&["-c", PY_CHECK, &dir2.join(format!("{}.json", key2)).to_string_lossy()]) {
                let value: serde_json::Value = serde_json::from_str(&answer).unwrap_or(serde_json::Value::Null);
                checks.same(
                    "pair_review sees the retracted session as empty",
                    &value["progress"],
                    &serde_json::json!([0, 6, 0]),
                );
            }
        }
    }

    // ---- reset ----
    let touched = review::reset_wins(&review::session_paths(&state));
    if let Some(object) = state.as_object_mut() {
        object.insert("decisions".to_string(), serde_json::json!({}));
    }
    let reset_saved = review::save_state(&mut state, &dir)?;
    checks.same("reset zeroed every picture in the folder", &touched, &4);
    let mut clean = true;
    for name in &names {
        let text = std::fs::read_to_string(gallery.join(name).with_extension("json"))?;
        if text.contains("\"wins\"") || text.contains("\"losses\"") || text.contains("\"bouts\"") || text.contains("\"vs\"")
        {
            clean = false;
        }
        let meta: serde_json::Value = serde_json::from_str(&text)?;
        if meta.get("prompt").and_then(|v| v.as_str()).is_none() {
            clean = false;
        }
    }
    checks.confirm("reset leaves a bare sidecar: no record, the prompt intact", clean, "4 sidecars");
    checks.same("after reset nothing is decided", &review::progress(&state).0, &0);
    checks.same(
        "after reset the order is by name",
        &names_of(&review::order(&state)),
        &names.iter().map(|n| n.to_string()).collect::<Vec<_>>(),
    );
    if python_ran {
        if let Some(answer) = python(&["-c", PY_CHECK, &reset_saved.to_string_lossy()]) {
            let value: serde_json::Value = serde_json::from_str(&answer).unwrap_or(serde_json::Value::Null);
            checks.same(
                "pair_review.progress() after reset is (0, 6, 0)",
                &value["progress"],
                &serde_json::json!([0, 6, 0]),
            );
            let mut by_name = paths.clone();
            by_name.sort_by_key(|p| review::name_of(p));
            checks.same(
                "pair_review.order() after reset is by name",
                &value["order"],
                &serde_json::json!(by_name),
            );
        }
    }

    // ---- dHash: the same 64 bits, or say so ----
    if python_ran {
        let mut agree = 0;
        for name in &names {
            let path = gallery.join(name);
            let ours = review::dhash(&path).unwrap_or_default();
            if let Some(theirs) = python(&["-c", PY_HASH, &path.to_string_lossy()]) {
                if theirs.trim() == ours {
                    agree += 1;
                } else {
                    println!("        {} ours {} / python {}", name, ours, theirs.trim());
                }
            }
        }
        // reported, not asserted: WIC's resampler is not Pillow's LANCZOS, and the hash only orders the questions
        println!("  note  dHash bits identical on {}/4 pictures (orders questions, never scores them)", agree);
    }

    println!(
        "\n{} check{} failed",
        checks.failed,
        if checks.failed == 1 { "" } else { "s" }
    );
    if checks.failed > 0 {
        anyhow::bail!("review selftest: {} check(s) failed", checks.failed);
    }
    Ok(())
}

fn names_of(paths: &[String]) -> Vec<String> {
    paths.iter().map(|p| review::name_of(p)).collect()
}

/// The installed Python, with the repo on its path so it imports `pair_review`/`pair_rank` from the checkout.
fn python(args: &[&str]) -> Option<String> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent()?.to_path_buf();
    let candidates: [&[&str]; 3] = [&["py", "-3"], &["python"], &["python3"]];
    for prefix in candidates {
        let mut cmd = Command::new(prefix[0]);
        for arg in &prefix[1..] {
            cmd.arg(arg);
        }
        cmd.args(args).env("PYTHONPATH", &root).env("PYTHONIOENCODING", "utf-8");
        if let Ok(out) = cmd.output() {
            if out.status.success() {
                return Some(String::from_utf8_lossy(&out.stdout).trim().to_string());
            }
            let err = String::from_utf8_lossy(&out.stderr);
            eprintln!("python {:?} failed: {}", prefix, err.lines().next().unwrap_or(""));
        }
    }
    None
}

const PY_CHECK: &str = r#"
import json, sys
from pathlib import Path
import pair_review
state = json.loads(open(sys.argv[1], encoding='utf-8').read())
print(json.dumps({
    'key': pair_review.state_key([str(p) for p in (state.get('paths') or [])]),
    'order': pair_review.order(state),
    'progress': list(pair_review.progress(state)),
    'keys': sorted(state.keys()),
}, sort_keys=True))
"#;

const PY_RECORD: &str = r#"
import json, sys
import pair_review, pair_rank
key, directory = sys.argv[1], sys.argv[2]
state = pair_review.load_state(key, directory)
a, b, _d = (state.get('pairs') or [None, None, None])[0]
pair_rank.record(state, a, b, 'right_wins')
pair_review.save_state(state, directory)
print(json.dumps({'pair': [a, b], 'id': pair_review._pair_id(a, b)}))
"#;

const PY_HASH: &str = r#"
import sys
import pair_review
print(pair_review.dhash(sys.argv[1]))
"#;

// ---- a four-picture test gallery, written by hand (no image crate, no painter) -------------------------------------

/// A 320x200 PNG: three channels of a different shape each, so the four pictures are visually distinct and hash apart.
fn make_picture(path: &Path, variant: usize) -> std::io::Result<()> {
    const W: u32 = 320;
    const H: u32 = 200;
    let mut raw = Vec::with_capacity((W as usize * 3 + 1) * H as usize);
    for y in 0..H {
        raw.push(0); // filter: none
        for x in 0..W {
            let fx = x as f64 / W as f64;
            let fy = y as f64 / H as f64;
            let px = match variant {
                0 => [((1.0 - fx) * 255.0) as u8, (fy * 90.0) as u8, ((1.0 - fx) * 60.0) as u8],
                1 => [(fx * 80.0) as u8, ((1.0 - fy) * 200.0) as u8, (fx * 255.0) as u8],
                2 => {
                    let on = ((x / 20) + (y / 20)) % 2 == 0;
                    if on { [40, 190, 90] } else { [10, 40, 30] }
                }
                _ => {
                    let band = y > 60 && y < 140;
                    if band { [20, 20, 20] } else { [(fx * 255.0) as u8, (fx * 255.0) as u8, (fx * 255.0) as u8] }
                }
            };
            raw.extend_from_slice(&px);
        }
    }
    let compressed = zlib_store(&raw);
    let mut png = vec![0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a];
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&W.to_be_bytes());
    ihdr.extend_from_slice(&H.to_be_bytes());
    ihdr.extend_from_slice(&[8, 2, 0, 0, 0]); // 8 bits, truecolour, deflate, no filter, no interlace
    chunk(&mut png, b"IHDR", &ihdr);
    chunk(&mut png, b"IDAT", &compressed);
    chunk(&mut png, b"IEND", &[]);
    std::fs::write(path, png)
}

fn chunk(out: &mut Vec<u8>, kind: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(kind);
    out.extend_from_slice(data);
    let mut crc_input = kind.to_vec();
    crc_input.extend_from_slice(data);
    out.extend_from_slice(&crc32(&crc_input).to_be_bytes());
}

/// zlib with stored (uncompressed) deflate blocks: a valid stream, and no compressor to depend on.
fn zlib_store(data: &[u8]) -> Vec<u8> {
    let mut out = vec![0x78, 0x01];
    if data.is_empty() {
        out.extend_from_slice(&[0x01, 0x00, 0x00, 0xff, 0xff]);
    } else {
        for (i, block) in data.chunks(0xffff).enumerate() {
            let last = (i + 1) * 0xffff >= data.len();
            out.push(if last { 1 } else { 0 });
            let len = block.len() as u16;
            out.extend_from_slice(&len.to_le_bytes());
            out.extend_from_slice(&(!len).to_le_bytes());
            out.extend_from_slice(block);
        }
    }
    out.extend_from_slice(&adler32(data).to_be_bytes());
    out
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xffff_ffffu32;
    for byte in data {
        crc ^= u32::from(*byte);
        for _ in 0..8 {
            let mask = (crc & 1).wrapping_neg();
            crc = (crc >> 1) ^ (0xedb8_8320 & mask);
        }
    }
    !crc
}

fn adler32(data: &[u8]) -> u32 {
    let (mut a, mut b) = (1u32, 0u32);
    for byte in data {
        a = (a + u32::from(*byte)) % 65521;
        b = (b + a) % 65521;
    }
    (b << 16) | a
}

/// The gallery the `--drive` script uses, so the real run and the headless one look at the same four pictures.
pub fn gallery_dir() -> PathBuf {
    std::env::temp_dir().join("zimage-review-selftest").join("gallery")
}
