//! Generation: send the graph to the local ComfyUI over WinHTTP and land the picture on disk.
//!
//! WinHTTP is the HTTP implementation Windows ships (`winhttp.dll`), so there is no client crate in this prototype: the
//! four calls are `WinHttpOpen` → `WinHttpConnect` → `WinHttpOpenRequest`/`SendRequest` → `ReceiveResponse`/`ReadData`.
//! The protocol mirrors the Python `Engine._queue_wait`: `POST /prompt` for a `prompt_id`, poll `GET /history/<id>` until
//! the images appear, then `GET /view?...` for the bytes.
//!
//! The output is the Python app's contract, not something invented here: `<stamp>_<w>x<h>_seed<n>.png` beside a sidecar
//! JSON carrying `prompt`, `seed`, `steps`, `cfg`, `model`, `width`, `height`, `created` (plus `lineage_root`, which is
//! what the family grouping reads). This is why a picture produced here appears in the gallery at all.

use std::net::ToSocketAddrs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::Value;
use windows::core::PCWSTR;
use windows::Win32::Networking::WinHttp::*;
use windows::Win32::System::SystemInformation::GetLocalTime;

use crate::graph;

/// The app's per-image budget is 30 s; the prototype is generous on purpose - the first render of a session also pays for
/// loading a 9B checkpoint and its encoder, which alone can take minutes.
pub const BUDGET_SECS: u64 = 300;
const POLL_MS: u64 = 300;

fn wide(text: &str) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    std::ffi::OsString::from(text).encode_wide().chain(std::iter::once(0)).collect()
}

fn describe(error: windows::core::Error) -> String {
    let code = error.code().0 as u32;
    if code == 0 {
        return "no error code".to_string();
    }
    // WinHTTP's own errors (12000-12186) are not Win32 codes, so `Error`'s Display can only print the HRESULT: the
    // numbers that matter here get their meaning spelled out.
    let number = code & 0xffff;
    let meaning = match number {
        12029 | 12028 => ": no answer - is ComfyUI running on that port?",
        12002 => ": the request timed out",
        12007 => ": that host name does not resolve",
        12030 | 12031 => ": the connection dropped",
        12175 => ": TLS negotiation failed",
        _ => "",
    };
    format!("0x{code:08X} (WinHTTP {number}){meaning}")
}

fn last_error() -> String {
    describe(windows::core::Error::from_thread())
}

// ---- endpoint ------------------------------------------------------------------------------------------------------------

/// Where ComfyUI is. `COMFY_HOST` wins (the Python app reads the same variable); otherwise the port this box uses.
#[derive(Clone, Debug)]
pub struct Config {
    pub host: String,
    pub port: u16,
}

impl Config {
    pub fn from_env() -> Config {
        let raw = std::env::var("COMFY_HOST").unwrap_or_default();
        Config::parse(if raw.trim().is_empty() { "http://127.0.0.1:8006" } else { &raw })
    }

    pub fn parse(raw: &str) -> Config {
        let rest = raw.trim().trim_end_matches('/');
        let rest = rest.strip_prefix("http://").unwrap_or(rest);
        let (host, port) = match rest.rsplit_once(':') {
            Some((h, p)) => match p.parse::<u16>() {
                Ok(port) => (h.to_string(), port),
                Err(_) => (rest.to_string(), 8006),
            },
            None => (rest.to_string(), 8006),
        };
        Config {
            host: if host.trim().is_empty() { "127.0.0.1".to_string() } else { host },
            port,
        }
    }

    pub fn origin(&self) -> String {
        format!("http://{}:{}", self.host, self.port)
    }
}

// ---- transport -----------------------------------------------------------------------------------------------------------

pub struct Response {
    pub status: u32,
    pub body: Vec<u8>,
}

pub struct Session {
    session: *mut core::ffi::c_void,
    connect: *mut core::ffi::c_void,
    origin: String,
}

impl Drop for Session {
    fn drop(&mut self) {
        unsafe {
            let _ = WinHttpCloseHandle(self.connect);
            let _ = WinHttpCloseHandle(self.session);
        }
    }
}

impl Session {
    /// Is anything listening? `WinHttpConnect` only builds a handle, and WinHTTP reports a refused connection about two
    /// seconds after the fact - its own retry machinery, which no timeout or retry setting shortens (measured: 2.05 s
    /// with `WINHTTP_OPTION_CONNECT_RETRIES` = 0 and a 200 ms connect timeout). A plain TCP connect answers in
    /// microseconds here and its error text is the one worth putting on the status line.
    fn probe(cfg: &Config) -> anyhow::Result<()> {
        let mut last = "no address".to_string();
        let addresses = (cfg.host.as_str(), cfg.port)
            .to_socket_addrs()
            .map_err(|e| anyhow::anyhow!("{} does not resolve: {e}", cfg.origin()))?;
        for address in addresses {
            match std::net::TcpStream::connect_timeout(&address, Duration::from_millis(600)) {
                Ok(stream) => {
                    drop(stream);
                    return Ok(());
                }
                Err(e) => last = e.to_string(),
            }
        }
        anyhow::bail!("nothing is listening on {} ({})", cfg.origin(), last)
    }

    pub fn open(cfg: &Config) -> anyhow::Result<Session> {
        Session::probe(cfg)?;
        unsafe {
            let agent = wide("zimage-native/0.1");
            let session = WinHttpOpen(
                PCWSTR(agent.as_ptr()),
                WINHTTP_ACCESS_TYPE_NO_PROXY,
                PCWSTR::null(),
                PCWSTR::null(),
                0,
            );
            if session.is_null() {
                anyhow::bail!("WinHttpOpen failed: {}", last_error());
            }
            // A closed port must be reported in well under a second, so the connect budget is short and the default five
            // connect retries are turned off (they cost ~2 s on a refused port before this). The receive budget covers
            // the wait for a reply, not for the render - that wait is the poll loop's.
            let retries: u32 = 0;
            let _ = WinHttpSetOption(
                Some(session as *const core::ffi::c_void),
                WINHTTP_OPTION_CONNECT_RETRIES,
                Some(&retries.to_ne_bytes()),
            );
            let _ = WinHttpSetTimeouts(session, 1000, 1000, 30_000, 300_000);
            let host = wide(&cfg.host);
            let connect = WinHttpConnect(session, PCWSTR(host.as_ptr()), cfg.port, 0);
            if connect.is_null() {
                let message = last_error();
                let _ = WinHttpCloseHandle(session);
                anyhow::bail!("cannot reach {}: {}", cfg.origin(), message);
            }
            Ok(Session { session, connect, origin: cfg.origin() })
        }
    }

    /// One request/response. `body` is the payload and its content type.
    pub fn request(&self, verb: &str, path: &str, body: Option<(&[u8], &str)>) -> anyhow::Result<Response> {
        unsafe {
            let verb_w = wide(verb);
            let path_w = wide(path);
            let request = WinHttpOpenRequest(
                self.connect,
                PCWSTR(verb_w.as_ptr()),
                PCWSTR(path_w.as_ptr()),
                PCWSTR::null(),
                PCWSTR::null(),
                std::ptr::null(),
                WINHTTP_OPEN_REQUEST_FLAGS(0),
            );
            if request.is_null() {
                anyhow::bail!("WinHttpOpenRequest({} {}) failed: {}", verb, path, last_error());
            }
            let exchanged = self.exchange(request, verb, path, body);
            let _ = WinHttpCloseHandle(request);
            exchanged
        }
    }

    unsafe fn exchange(
        &self,
        request: *mut core::ffi::c_void,
        verb: &str,
        path: &str,
        body: Option<(&[u8], &str)>,
    ) -> anyhow::Result<Response> {
        if let Some((_, content_type)) = body {
            // the wrapper takes an explicit length, so the terminator is not part of it
            let headers = wide(&format!("Content-Type: {content_type}\r\n"));
            WinHttpAddRequestHeaders(
                request,
                &headers[..headers.len() - 1],
                WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE,
            )
            .map_err(|e| anyhow::anyhow!("{}: adding headers: {}", self.origin, describe(e)))?;
        }
        let (payload, length) = match body {
            Some((bytes, _)) => (Some(bytes.as_ptr() as *const core::ffi::c_void), bytes.len() as u32),
            None => (None, 0),
        };
        WinHttpSendRequest(request, None, payload, length, length, 0).map_err(|e| {
            anyhow::anyhow!("cannot reach {} ({}) - {}", self.origin, format!("{verb} {path}"), describe(e))
        })?;
        WinHttpReceiveResponse(request, std::ptr::null_mut())
            .map_err(|e| anyhow::anyhow!("{} {verb} {path}: no response: {}", self.origin, describe(e)))?;

        let mut status: u32 = 0;
        let mut size = std::mem::size_of::<u32>() as u32;
        let _ = WinHttpQueryHeaders(
            request,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            PCWSTR::null(),
            Some(&mut status as *mut u32 as *mut core::ffi::c_void),
            &mut size,
            std::ptr::null_mut(),
        );

        let mut body_bytes = Vec::new();
        let mut buffer = vec![0u8; 64 * 1024];
        loop {
            let mut got: u32 = 0;
            WinHttpReadData(
                request,
                buffer.as_mut_ptr() as *mut core::ffi::c_void,
                buffer.len() as u32,
                &mut got,
            )
            .map_err(|e| anyhow::anyhow!("{} {verb} {path}: reading the body: {}", self.origin, describe(e)))?;
            if got == 0 {
                break;
            }
            body_bytes.extend_from_slice(&buffer[..got as usize]);
        }
        Ok(Response { status, body: body_bytes })
    }
}

// ---- the job -------------------------------------------------------------------------------------------------------------

/// What the prompt box and the profile ask for.
pub struct Job {
    pub prompt: String,
    pub width: i32,
    pub height: i32,
    pub steps: u32,
    pub cfg: f64,
    pub seed: u64,
    pub model: String,
    pub out_dir: PathBuf,
}

impl Job {
    /// `256 px`, the profile's own step count, `ZIMAGE_SEED` for a reproducible name: the prototype has no size or seed
    /// control, so the defaults are the ones its acceptance run uses.
    pub fn new(prompt: &str, model: &str, out_dir: &Path) -> Job {
        let prof = graph::profile(model);
        let size = std::env::var("ZIMAGE_SIZE").ok().and_then(|v| v.trim().parse::<i32>().ok()).unwrap_or(256);
        let steps = std::env::var("ZIMAGE_STEPS").ok().and_then(|v| v.trim().parse::<u32>().ok()).unwrap_or(prof.steps);
        let seed = std::env::var("ZIMAGE_SEED").ok().and_then(|v| v.trim().parse::<u64>().ok()).unwrap_or_else(random_seed);
        Job {
            prompt: prompt.to_string(),
            width: size,
            height: size,
            steps,
            cfg: prof.cfg,
            seed,
            model: prof.name.to_string(),
            out_dir: out_dir.to_path_buf(),
        }
    }
}

pub struct Rendered {
    pub png: PathBuf,
    pub sidecar: PathBuf,
    pub width: i32,
    pub height: i32,
    pub seconds: f64,
}

pub fn generate(job: &Job, cfg: &Config) -> anyhow::Result<Rendered> {
    let started = Instant::now();
    let prof = graph::profile(&job.model);
    let prefix = format!("zimage-native-{}", millis_now());
    let workflow = graph::workflow(
        &job.prompt, job.width, job.height, job.steps, job.cfg, job.seed, &prefix, prof,
    );
    let payload = serde_json::to_vec(&serde_json::json!({ "prompt": workflow }))?;

    let session = Session::open(cfg)?;
    let posted = session.request("POST", "/prompt", Some((&payload, "application/json")))?;
    if posted.status != 200 {
        anyhow::bail!(
            "the server rejected the graph (HTTP {}): {}",
            posted.status,
            snippet(&posted.body)
        );
    }
    let queued: Value = serde_json::from_slice(&posted.body).unwrap_or(Value::Null);
    let prompt_id = queued
        .get("prompt_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow::anyhow!("the server queued nothing: {}", snippet(&posted.body)))?;

    // The poll: /history stays `{}` until the prompt is done, then carries the images.
    let deadline = Instant::now() + Duration::from_secs(BUDGET_SECS);
    let image = loop {
        let waited = started.elapsed().as_secs_f64();
        if Instant::now() > deadline {
            anyhow::bail!("no image after {} s (prompt {prompt_id} was accepted but never finished)", BUDGET_SECS);
        }
        let history = session.request("GET", &format!("/history/{prompt_id}"), None)?;
        if history.status == 200 {
            let parsed: Value = serde_json::from_slice(&history.body).unwrap_or(Value::Null);
            if let Some(entry) = parsed.get(prompt_id) {
                if entry.pointer("/status/status_str").and_then(|v| v.as_str()) == Some("error") {
                    anyhow::bail!("the workflow failed after {waited:.0} s: {}", history_error(entry));
                }
                if let Some(found) = first_image(entry) {
                    break found.clone();
                }
            }
        }
        std::thread::sleep(Duration::from_millis(POLL_MS));
    };

    // /view is a plain GET on the same connection: the bytes ComfyUI saved, which are the bytes written here.
    let filename = image.get("filename").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    let subfolder = image.get("subfolder").and_then(|v| v.as_str()).unwrap_or("");
    let kind = image.get("type").and_then(|v| v.as_str()).unwrap_or("output");
    let view_path = format!(
        "/view?filename={}&subfolder={}&type={}",
        encode(&filename),
        encode(subfolder),
        encode(kind)
    );
    let view = session.request("GET", &view_path, None)?;
    if view.status != 200 {
        anyhow::bail!("could not download {filename} (HTTP {}): {}", view.status, snippet(&view.body));
    }
    if view.body.len() < 8 || &view.body[..4] != b"\x89PNG" {
        anyhow::bail!("the server did not return a PNG ({} bytes)", view.body.len());
    }
    // the size a sidecar records is the size of the file, as in the Python app (an outpaint can change the canvas)
    let (width, height) = png_size(&view.body).unwrap_or((job.width, job.height));

    std::fs::create_dir_all(&job.out_dir)?;
    let stamp = stamp_local();
    let png = job.out_dir.join(format!("{stamp}_{width}x{height}_seed{}.png", job.seed));
    std::fs::write(&png, &view.body)?;
    let record = serde_json::json!({
        "image": png.to_string_lossy(),
        "prompt": job.prompt,
        "seed": job.seed,
        "steps": job.steps,
        "cfg": job.cfg,
        "model": prof.name,
        "width": width,
        "height": height,
        "created": iso_local(),
        // the rest is what the Python sidecars carry and the gallery reads
        "unet": prof.unet,
        "denoise": 1.0,
        "negative": Value::Null,
        "lineage": [job.prompt],
        "lineage_root": job.prompt,
    });
    let sidecar = png.with_extension("json");
    std::fs::write(&sidecar, serde_json::to_string_pretty(&record)? + "\n")?;

    Ok(Rendered { png, sidecar, width, height, seconds: started.elapsed().as_secs_f64() })
}

/// The first image any output node reported. `SaveImage` puts one dict per saved file in `images`.
fn first_image(entry: &Value) -> Option<&Value> {
    let outputs = entry.get("outputs")?.as_object()?;
    for output in outputs.values() {
        if let Some(images) = output.get("images").and_then(|v| v.as_array()) {
            for image in images {
                if image.get("filename").and_then(|v| v.as_str()).is_some() {
                    return Some(image);
                }
            }
        }
    }
    None
}

/// The node and the exception out of a failed history entry, the way `Engine._history_error` does it: "workflow failed"
/// alone is undiagnosable.
fn history_error(entry: &Value) -> String {
    if let Some(messages) = entry.pointer("/status/messages").and_then(|v| v.as_array()) {
        for message in messages {
            let Some(pair) = message.as_array() else { continue };
            let kind = pair.first().and_then(|v| v.as_str()).unwrap_or_default();
            let data = pair.get(1).cloned().unwrap_or(Value::Null);
            if kind == "execution_error" {
                let node = data
                    .get("node_type")
                    .or_else(|| data.get("node_id"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("?");
                let text = data
                    .get("exception_message")
                    .or_else(|| data.get("exception_type"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("no message");
                let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
                return format!("{node}: {}", flat.chars().take(200).collect::<String>());
            }
            if kind == "execution_interrupted" {
                return "interrupted".to_string();
            }
        }
    }
    "no detail in the history entry".to_string()
}

/// Width and height out of a PNG's IHDR, which is always the first chunk: no decoder, no crate.
fn png_size(bytes: &[u8]) -> Option<(i32, i32)> {
    if bytes.len() < 24 || &bytes[12..16] != b"IHDR" {
        return None;
    }
    let read = |at: usize| i32::from_be_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]]);
    Some((read(16), read(20)))
}

fn snippet(body: &[u8]) -> String {
    let text = String::from_utf8_lossy(body);
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    flat.chars().take(200).collect()
}

/// Percent-encode a query value: a filename is safe today, but `subfolder` can be a path.
fn encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

fn millis_now() -> u128 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0)
}

fn random_seed() -> u64 {
    // no RNG crate: the system clock is enough to make two presses different seeds
    (millis_now() as u64 * 7919) % 2_147_483_646
}

/// `20261030-181233`: the stamp the Python app puts in front of every file name.
fn stamp_local() -> String {
    let t = unsafe { GetLocalTime() };
    format!(
        "{:04}{:02}{:02}-{:02}{:02}{:02}",
        t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond
    )
}

/// `2026-10-30T18:12:33`, the `created` field the Python sidecars carry (`isoformat(timespec='seconds')`).
fn iso_local() -> String {
    let t = unsafe { GetLocalTime() };
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond
    )
}
