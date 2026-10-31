//! The A/B review window: two panes, three decisions, a finish screen - and the *same* session file as the Python app.
//!
//! It is a second top-level window, not a dialog, and every control in it is a real one: `BUTTON` for the decisions,
//! `STATIC` for the text, and two custom child windows that do the one thing no stock control does well at this size -
//! draw a picture scaled into the space it was given (the panes), and the ranked order as large tiles (the finish
//! screen).
//!
//! Keyboard: `1`/`2`/`3` and `Left`/`Right`/`Down` record a decision, `Up` retracts the last one, `Esc` stops and keeps
//! the progress - all through a real accelerator table (`TranslateAcceleratorW`), so they work wherever the focus is in
//! the window. `Enter` and `Space` are the system's business: `IsDialogMessageW` plus a `BS_DEFPUSHBUTTON`, which is why
//! the finish screen puts the default ring - and therefore Enter - on *Keep and close*.

use crate::review;
use crate::{dark, fit_image, short, wide};
use serde_json::Value;
use std::path::{Path, PathBuf};
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    BeginPaint, CreateCompatibleDC, CreateFontW, CreateSolidBrush, DeleteDC, DeleteObject, DrawTextW, EndPaint,
    FillRect, GetStockObject, GetSysColor, GetSysColorBrush, HDC, HBRUSH, HFONT, HGDIOBJ, InvalidateRect, SelectObject, SetBkMode, SetStretchBltMode,
    SetTextColor, StretchBlt, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, COLOR_WINDOW, COLOR_WINDOWTEXT, DEFAULT_CHARSET,
    DEFAULT_GUI_FONT, DRAW_TEXT_FORMAT, DT_CENTER, DT_END_ELLIPSIS, DT_LEFT, DT_NOPREFIX, DT_SINGLELINE,
    OUT_DEFAULT_PRECIS, PAINTSTRUCT, SRCCOPY, STRETCH_DELETESCANS, TRANSPARENT,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::{SetScrollInfo, SetWindowTheme};
use windows::Win32::UI::Input::KeyboardAndMouse::SetFocus;
use windows::Win32::UI::Input::KeyboardAndMouse::{VK_DOWN, VK_ESCAPE, VK_LEFT, VK_RIGHT, VK_UP};
use windows::Win32::UI::WindowsAndMessaging::*;

/// The main window's `A/B…` button: a `WM_COMMAND` from it starts a review of the open family.
pub const ID_AB: i32 = 106;

const ID_PANE_L: i32 = 211;
const ID_PANE_R: i32 = 212;
const ID_TALLY: i32 = 213;
const ID_HEAD: i32 = 214;
const ID_TILES: i32 = 215;
const ID_RV_LEFT: i32 = 201;
const ID_RV_TIE: i32 = 202;
const ID_RV_RIGHT: i32 = 203;
const ID_RV_STOP: i32 = 204;
const ID_RV_UNDO: i32 = 205;
const ID_RV_RESET: i32 = 206;
const ID_RV_KEEP: i32 = 207;

/// The finish screen's grid: four tiles across, as the Python's result pane has it.
const TILE_COLS: i32 = 4;
/// `SS_LEFT` is 0 too; the named constants sit behind a feature this prototype does not need (as in main.rs).
const SS_NOPREFIX: u32 = 0x0080;

struct Session {
    hwnd: HWND,
    owner: HWND,
    dir: PathBuf,
    key: String,
    state: Value,
    /// the pairs still to ask, in the schedule's order
    queue: Vec<(String, String, i64)>,
    pair: Option<(String, String)>,
    history: Vec<(String, String)>,
    finished: bool,
    /// the finish screen's order, and how far it is scrolled
    ordered: Vec<String>,
    tile_offset: i32,
    left: HWND,
    right: HWND,
    tally: HWND,
    buttons: [HWND; 4],
    head: HWND,
    tiles: HWND,
    b_reset: HWND,
    b_keep: HWND,
}

static mut OPEN: Option<Box<Session>> = None;
static mut ACCEL: isize = 0;

fn session() -> Option<&'static Session> {
    unsafe { OPEN.as_deref() }
}

fn session_mut() -> Option<&'static mut Session> {
    unsafe { OPEN.as_deref_mut() }
}

/// The accelerator table, for the message loop. Only exists while a review is open.
pub fn haccel() -> Option<HACCEL> {
    unsafe {
        if ACCEL == 0 {
            None
        } else {
            Some(HACCEL(ACCEL as *mut core::ffi::c_void))
        }
    }
}

/// The window those accelerators speak for: the loop re-targets key messages here, so `1` works from any child.
pub fn window() -> Option<HWND> {
    session().map(|s| s.hwnd)
}

// ---- drawing helpers ---------------------------------------------------------------------------------------------

fn bg() -> COLORREF {
    if dark() {
        COLORREF(0x0010_1010)
    } else {
        COLORREF(unsafe { GetSysColor(COLOR_WINDOW) })
    }
}

fn ink() -> COLORREF {
    if dark() {
        COLORREF(0x00E8_E8E8)
    } else {
        COLORREF(unsafe { GetSysColor(COLOR_WINDOWTEXT) })
    }
}

fn muted() -> COLORREF {
    if dark() {
        COLORREF(0x00A0_A0A0)
    } else {
        COLORREF(0x0060_6060)
    }
}

unsafe fn fill(hdc: HDC, rect: &RECT, colour: COLORREF) {
    let brush = CreateSolidBrush(colour);
    FillRect(hdc, rect, brush);
    let _ = DeleteObject(HGDIOBJ(brush.0));
}

unsafe fn line(hdc: HDC, text: &str, rect: &mut RECT, flags: DRAW_TEXT_FORMAT, colour: COLORREF, font: HFONT) {
    let old = SelectObject(hdc, HGDIOBJ(font.0));
    SetBkMode(hdc, TRANSPARENT);
    SetTextColor(hdc, colour);
    let mut buf = wide(text);
    DrawTextW(hdc, &mut buf, rect as *mut RECT, flags);
    SelectObject(hdc, old);
}

/// A Segoe UI font at a specific pixel height: the stock font is one size, and the finish screen needs a heading.
unsafe fn ui_font(height: i32, bold: bool) -> HFONT {
    CreateFontW(
        -height,
        0,
        0,
        0,
        if bold { 700 } else { 400 },
        0,
        0,
        0,
        DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY,
        0, // DEFAULT_PITCH | FF_DONTCARE
        w!("Segoe UI"),
    )
}

/// A separate image inside a pane, centred, on a background darker than the window.
unsafe fn blit_fit(hdc: HDC, path: &str, box_x: i32, box_y: i32, box_w: i32, box_h: i32) {
    if let Some((dib, iw, ih)) = fit_image(Path::new(path), box_w, box_h) {
        let mem = CreateCompatibleDC(Some(hdc));
        let old = SelectObject(mem, HGDIOBJ(dib as *mut core::ffi::c_void));
        SetStretchBltMode(hdc, STRETCH_DELETESCANS);
        let _ = StretchBlt(
            hdc,
            box_x + (box_w - iw) / 2,
            box_y + (box_h - ih) / 2,
            iw,
            ih,
            Some(mem),
            0,
            0,
            iw,
            ih,
            SRCCOPY,
        );
        SelectObject(mem, old);
        let _ = DeleteDC(mem);
    }
}

// ---- opening -----------------------------------------------------------------------------------------------------

/// Start (or resume) a review of `paths` in a new window. The session file is the Python's, keyed the Python's way.
pub unsafe fn open(owner: HWND, label: &str, paths: &[String]) -> anyhow::Result<()> {
    if let Some(open) = session() {
        // already open: bring it forward rather than opening a second window on the same session
        let _ = SetForegroundWindow(open.hwnd);
        return Ok(());
    }
    let dir = review::state_dir();
    let key = review::state_key(paths);
    let mut state = review::load_state(&key, &dir);
    review::build(paths, &mut state, &dir);
    review::save_state(&mut state, &dir)?;

    let instance = GetModuleHandleW(None)?;
    let title = wide(&format!("A/B review \u{2014} {}", short(label, 50)));
    let hwnd = CreateWindowExW(
        WS_EX_CONTROLPARENT | WS_EX_APPWINDOW,
        w!("zimage-review"),
        PCWSTR(title.as_ptr()),
        WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        1100,
        760,
        Some(owner),
        None,
        Some(instance.into()),
        None,
    )?;
    if dark() {
        let yes: i32 = 1;
        let _ = windows::Win32::Graphics::Dwm::DwmSetWindowAttribute(
            hwnd,
            windows::Win32::Graphics::Dwm::DWMWA_USE_IMMERSIVE_DARK_MODE,
            &yes as *const i32 as *const core::ffi::c_void,
            4,
        );
    }
    if let Some(s) = session_mut() {
        s.dir = dir;
        s.key = key;
        s.state = state;
    }
    let _ = ShowWindow(hwnd, SW_SHOW);
    let _ = SetForegroundWindow(hwnd);
    let mut rect = RECT::default();
    let _ = GetWindowRect(hwnd, &mut rect);
    let (w, h) = (rect.right - rect.left, rect.bottom - rect.top);
    let _ = MoveWindow(
        hwnd,
        (GetSystemMetrics(SM_CXSCREEN) - w) / 2,
        (GetSystemMetrics(SM_CYSCREEN) - h) / 3,
        w,
        h,
        true,
    );
    install_accelerators();
    show_next();
    layout();
    let _ = SetFocus(Some(hwnd));
    Ok(())
}

/// The accelerator table: `1`/`2`/`3`, the arrows, and Esc. Created with the window, destroyed with it.
unsafe fn install_accelerators() {
    let keys: [(u16, i32); 8] = [
        (b'1' as u16, ID_RV_LEFT),
        (b'2' as u16, ID_RV_TIE),
        (b'3' as u16, ID_RV_RIGHT),
        (VK_LEFT.0, ID_RV_LEFT),
        (VK_RIGHT.0, ID_RV_RIGHT),
        (VK_DOWN.0, ID_RV_TIE),
        (VK_UP.0, ID_RV_UNDO),
        (VK_ESCAPE.0, ID_RV_STOP),
    ];
    let table: Vec<ACCEL> = keys
        .iter()
        .map(|(key, cmd)| ACCEL { fVirt: FVIRTKEY, key: *key, cmd: *cmd as u16 })
        .collect();
    if let Ok(haccel) = CreateAcceleratorTableW(&table) {
        ACCEL = haccel.0 as isize;
    }
}

// ---- controls ----------------------------------------------------------------------------------------------------

unsafe fn make(hwnd: HWND, class: PCWSTR, text: &str, style: WINDOW_STYLE, id: i32) -> HWND {
    let instance = GetModuleHandleW(None).unwrap_or_default();
    let body = wide(text);
    let created = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        class,
        PCWSTR(body.as_ptr()),
        WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | style,
        0,
        0,
        100,
        30,
        Some(hwnd),
        Some(HMENU(id as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    let font = GetStockObject(DEFAULT_GUI_FONT);
    SendMessageW(created, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));
    created
}

unsafe fn build_controls(hwnd: HWND) {
    let left = make(hwnd, w!("zimage-pane"), "", WINDOW_STYLE(0), ID_PANE_L);
    let right = make(hwnd, w!("zimage-pane"), "", WINDOW_STYLE(0), ID_PANE_R);
    let tally = make(hwnd, w!("STATIC"), "", WINDOW_STYLE(SS_NOPREFIX), ID_TALLY);
    let b_left = make(hwnd, w!("BUTTON"), "&Left wins  (1 or \u{2190})", WINDOW_STYLE(BS_PUSHBUTTON as u32 | WS_TABSTOP.0), ID_RV_LEFT);
    let b_tie = make(hwnd, w!("BUTTON"), "&Tie  (2 or \u{2193})", WINDOW_STYLE(BS_PUSHBUTTON as u32 | WS_TABSTOP.0), ID_RV_TIE);
    let b_right = make(hwnd, w!("BUTTON"), "&Right wins  (3 or \u{2192})", WINDOW_STYLE(BS_PUSHBUTTON as u32 | WS_TABSTOP.0), ID_RV_RIGHT);
    let b_stop = make(hwnd, w!("BUTTON"), "S&top  (Esc)", WINDOW_STYLE(BS_PUSHBUTTON as u32 | WS_TABSTOP.0), ID_RV_STOP);
    let head = make(hwnd, w!("STATIC"), "", WINDOW_STYLE(SS_NOPREFIX), ID_HEAD);
    let tiles = make(hwnd, w!("zimage-tiles"), "", WINDOW_STYLE(WS_VSCROLL.0 | WS_BORDER.0), ID_TILES);
    let b_reset = make(hwnd, w!("BUTTON"), "&Reset and start over", WINDOW_STYLE(BS_PUSHBUTTON as u32 | WS_TABSTOP.0), ID_RV_RESET);
    // the default button: Windows draws the ring, and Enter/Space are the system's, not ours
    let b_keep = make(hwnd, w!("BUTTON"), "&Keep and close", WINDOW_STYLE(BS_DEFPUSHBUTTON as u32 | WS_TABSTOP.0), ID_RV_KEEP);
    if dark() {
        for b in [b_left, b_tie, b_right, b_stop, b_reset, b_keep] {
            let _ = SetWindowTheme(b, w!("DarkMode_Explorer"), PCWSTR::null());
        }
    }
    if let Some(s) = session_mut() {
        s.left = left;
        s.right = right;
        s.tally = tally;
        s.buttons = [b_left, b_tie, b_right, b_stop];
        s.head = head;
        s.tiles = tiles;
        s.b_reset = b_reset;
        s.b_keep = b_keep;
    }
    let heading = ui_font(20, true);
    SendMessageW(head, WM_SETFONT, Some(WPARAM(heading.0 as usize)), Some(LPARAM(1)));
    let status = ui_font(13, false);
    SendMessageW(tally, WM_SETFONT, Some(WPARAM(status.0 as usize)), Some(LPARAM(1)));
    show_finish_parts(false);
}

unsafe fn show_finish_parts(on: bool) {
    let Some(s) = session() else { return };
    let show = |target: HWND, visible: bool| {
        let _ = ShowWindow(target, if visible { SW_SHOW } else { SW_HIDE });
    };
    show(s.head, on);
    show(s.tiles, on);
    show(s.b_reset, on);
    show(s.b_keep, on);
    show(s.left, !on);
    show(s.right, !on);
    show(s.tally, !on);
    for b in s.buttons {
        show(b, !on);
    }
}

unsafe fn layout() {
    let Some(s) = session() else { return };
    let mut rc = RECT::default();
    let _ = GetClientRect(s.hwnd, &mut rc);
    let (w, h) = (rc.right, rc.bottom);
    let pad = 10;
    let row = 34;
    if s.finished {
        let actions = row + 12;
        let _ = MoveWindow(s.head, pad, pad, w - 2 * pad, 52, true);
        let _ = MoveWindow(s.tiles, pad, pad + 54, w - 2 * pad, (h - pad - actions - 54).max(80), true);
        let bw = 216;
        let _ = MoveWindow(s.b_reset, (w - pad - 2 * bw - 8).max(pad), h - pad - row, bw, row, true);
        let _ = MoveWindow(s.b_keep, (w - pad - bw).max(pad), h - pad - row, bw, row, true);
        return;
    }
    let pane_w = ((w - 3 * pad) / 2).max(80);
    let pane_h = (h - 2 * pad - 26 - row - 6).max(120);
    let _ = MoveWindow(s.left, pad, pad, pane_w, pane_h, true);
    let _ = MoveWindow(s.right, 2 * pad + pane_w, pad, pane_w, pane_h, true);
    let _ = MoveWindow(s.tally, pad, pad + pane_h + 4, w - 2 * pad, 22, true);
    let bw = ((w - 2 * pad - 24) / 4).max(80);
    for (i, b) in s.buttons.iter().enumerate() {
        let _ = MoveWindow(*b, pad + i as i32 * (bw + 8), h - pad - row, bw, row, true);
    }
}

// ---- the review itself -------------------------------------------------------------------------------------------

fn set_text(hwnd: HWND, text: &str) {
    let buf = wide(text);
    unsafe {
        let _ = SetWindowTextW(hwnd, PCWSTR(buf.as_ptr()));
    }
}

/// Decide the pair in front of you: `record` writes the sidecar *and* the decision, then `show_next` moves on.
unsafe fn decide(choice: &str) {
    let Some(s) = session_mut() else { return };
    if s.finished {
        return;
    }
    let Some((a, b)) = s.pair.clone() else { return };
    if let Err(e) = review::record(&mut s.state, &a, &b, choice) {
        set_text(
            s.tally,
            &format!("could not record {} | {}: {}", review::name_of(&a), review::name_of(&b), e),
        );
        return;
    }
    let _ = review::save_state(&mut s.state, &s.dir);
    s.history.push((a, b));
    s.pair = None;
    show_next();
}

/// Undo (`Up`): retract the last decision, sidecars included, and put that pair back in front.
unsafe fn undo() {
    let Some(s) = session_mut() else { return };
    if s.finished {
        return;
    }
    let Some((a, b)) = s.history.pop() else {
        set_text(s.tally, "nothing to go back to yet");
        return;
    };
    review::unrecord(&mut s.state, &a, &b);
    let _ = review::save_state(&mut s.state, &s.dir);
    s.pair = None;
    show_next();
}

/// `pair_review.remaining` + `next_round` + the finish screen, in the order the Python window does it.
unsafe fn show_next() {
    let Some(s) = session_mut() else { return };
    s.finished = false;
    show_finish_parts(false);
    let mut queue = review::remaining(&s.state);
    if queue.is_empty() {
        let more = review::next_round(&mut s.state);
        if !more.is_empty() {
            let _ = review::save_state(&mut s.state, &s.dir);
            queue = review::remaining(&s.state);
        }
    }
    let (decided, total, _) = review::progress(&s.state);
    if queue.is_empty() {
        let settled = s.state.get("settled").and_then(|v| v.as_bool()).unwrap_or(false);
        let round = s.state.get("round").and_then(|v| v.as_i64()).unwrap_or(1);
        let tail = if settled {
            format!("\u{b7} the order settled after round {}", round)
        } else {
            String::new()
        };
        set_text(s.tally, &format!("{} {}", review::tally(decided, total, None), tail));
        finish();
        return;
    }
    s.queue = queue;
    let (a, b, _) = s.queue[0].clone();
    s.pair = Some((a.clone(), b.clone()));
    let minutes = review::estimate_minutes(s.queue.len());
    let left = if minutes >= 1.0 {
        format!("{} min", minutes.round() as i64)
    } else {
        format!("{} s", (minutes * 60.0).round() as i64)
    };
    let stage = review::stage_label(&s.state);
    set_text(
        s.tally,
        &format!(
            "{} \u{b7} {} | {}",
            review::tally(decided, total, Some((&stage, &left))),
            short(&review::name_of(&a), 30),
            short(&review::name_of(&b), 30)
        ),
    );
    layout();
    let _ = InvalidateRect(Some(s.left), None, true);
    let _ = InvalidateRect(Some(s.right), None, true);
}

/// The finish screen: the new order as large tiles, and the two things to do with it.
unsafe fn finish() {
    let Some(s) = session_mut() else { return };
    s.finished = true;
    s.ordered = review::order(&s.state);
    s.tile_offset = 0;
    let (decided, total, _) = review::progress(&s.state);
    let heading = if decided > 0 { "Ranked by wins" } else { "Nothing to rank yet" };
    set_text(
        s.head,
        &format!("{}\r\n{} of {} pairs decided \u{b7} best first", heading, decided, total),
    );
    show_finish_parts(true);
    layout();
    let _ = InvalidateRect(Some(s.tiles), None, true);
    // the default button takes the focus, so Enter (default) and Space (focused control) agree on Keep and close
    let _ = SetFocus(Some(s.b_keep));
}

/// `_review_reset`: zero this folder's record and start again from the first pair. Nothing else is touched.
unsafe fn reset() {
    let Some(s) = session_mut() else { return };
    let paths = review::session_paths(&s.state);
    let touched = review::reset_wins(&paths);
    if let Some(object) = s.state.as_object_mut() {
        object.insert("decisions".to_string(), Value::Object(Default::default()));
    }
    let _ = review::save_state(&mut s.state, &s.dir);
    s.history.clear();
    s.pair = None;
    show_next();
    set_text(
        s.tally,
        &format!("cleared the record on {} pictures - comparisons start from zero", touched),
    );
}

unsafe fn close() {
    if let Some(s) = session() {
        let _ = DestroyWindow(s.hwnd);
    }
}

// ---- the window procedure ----------------------------------------------------------------------------------------

unsafe extern "system" fn review_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_CREATE => {
            OPEN = Some(Box::new(Session {
                hwnd,
                owner: GetWindow(hwnd, GW_OWNER).unwrap_or_default(),
                dir: review::state_dir(),
                key: String::new(),
                state: Value::Null,
                queue: Vec::new(),
                pair: None,
                history: Vec::new(),
                finished: false,
                ordered: Vec::new(),
                tile_offset: 0,
                left: HWND::default(),
                right: HWND::default(),
                tally: HWND::default(),
                buttons: [HWND::default(); 4],
                head: HWND::default(),
                tiles: HWND::default(),
                b_reset: HWND::default(),
                b_keep: HWND::default(),
            }));
            build_controls(hwnd);
            LRESULT(0)
        }
        WM_SIZE => {
            layout();
            if let Some(s) = session() {
                if s.finished {
                    let _ = InvalidateRect(Some(s.tiles), None, true);
                }
            }
            LRESULT(0)
        }
        WM_COMMAND => {
            match (wparam.0 & 0xffff) as i32 {
                ID_RV_LEFT => decide("left_wins"),
                ID_RV_RIGHT => decide("right_wins"),
                ID_RV_TIE => decide("tie"),
                ID_RV_UNDO => undo(),
                ID_RV_STOP => close(),
                ID_RV_RESET => reset(),
                ID_RV_KEEP => {
                    if let Some(s) = session_mut() {
                        let _ = review::save_state(&mut s.state, &s.dir);
                    }
                    close()
                }
                _ => {}
            }
            LRESULT(0)
        }
        WM_CTLCOLORSTATIC => crate::ctl_color(wparam),
        WM_CLOSE => {
            close();
            LRESULT(0)
        }
        WM_DESTROY => {
            let (owner, text) = match session() {
                Some(s) => {
                    let (decided, total, _) = review::progress(&s.state);
                    let text = if decided > 0 {
                        format!(
                            "A/B review stopped: {} - progress saved; the folder is now ordered by wins",
                            review::tally(decided, total, None)
                        )
                    } else {
                        format!("A/B review stopped: {}", review::tally(decided, total, None))
                    };
                    (s.owner, text)
                }
                None => (HWND::default(), String::new()),
            };
            if ACCEL != 0 {
                let _ = DestroyAcceleratorTable(HACCEL(ACCEL as *mut core::ffi::c_void));
                ACCEL = 0;
            }
            OPEN = None;
            if !owner.is_invalid() && !text.is_empty() {
                if let Ok(status) = GetDlgItem(Some(owner), crate::ID_STATUS) {
                    set_text(status, &text);
                }
            }
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

// ---- the two picture panes ---------------------------------------------------------------------------------------

unsafe extern "system" fn pane_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_ERASEBKGND => LRESULT(1),
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            let hdc = BeginPaint(hwnd, &mut ps);
            paint_pane(hwnd, hdc);
            let _ = EndPaint(hwnd, &ps);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

unsafe fn pane_path(hwnd: HWND) -> Option<(String, bool)> {
    let s = session()?;
    let (a, b) = s.pair.clone()?;
    match GetDlgCtrlID(hwnd) {
        ID_PANE_L => Some((a, true)),
        ID_PANE_R => Some((b, false)),
        _ => None,
    }
}

unsafe fn paint_pane(hwnd: HWND, hdc: HDC) {
    let mut rc = RECT::default();
    let _ = GetClientRect(hwnd, &mut rc);
    fill(hdc, &rc, bg());
    let Some((path, is_left)) = pane_path(hwnd) else { return };
    let label_h = 26;
    let (w, h) = (rc.right - rc.left, rc.bottom - rc.top);
    blit_fit(hdc, &path, 6, 4, w - 12, (h - label_h - 8).max(40));
    let (wins, losses, ties, bouts) = review::record_of(&path);
    let caption = if bouts == 0 {
        format!(
            "{} \u{b7} the {} side, no bouts yet",
            review::name_of(&path),
            if is_left { "left" } else { "right" }
        )
    } else {
        format!(
            "{} \u{b7} {}W-{}L-{}T",
            review::name_of(&path),
            wins,
            losses,
            ties
        )
    };
    let mut text_rect = RECT {
        left: rc.left + 10,
        top: rc.bottom - label_h,
        right: rc.right - 10,
        bottom: rc.bottom - 4,
    };
    line(
        hdc,
        &caption,
        &mut text_rect,
        DT_CENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
        muted(),
        HFONT(GetStockObject(DEFAULT_GUI_FONT).0),
    );
}

// ---- the ranked tiles --------------------------------------------------------------------------------------------

unsafe extern "system" fn tiles_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_ERASEBKGND => LRESULT(1),
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            let hdc = BeginPaint(hwnd, &mut ps);
            paint_tiles(hwnd, hdc);
            let _ = EndPaint(hwnd, &ps);
            LRESULT(0)
        }
        WM_VSCROLL => {
            scroll_tiles(hwnd, (lparam.0 & 0xffff) as u32, ((wparam.0 >> 16) & 0xffff) as i32);
            LRESULT(0)
        }
        WM_MOUSEWHEEL => {
            let mm = ((wparam.0 >> 16) & 0xffff) as u16 as i16;
            scroll_tiles(hwnd, SB_THUMBPOSITION.0 as u32, i32::MIN); // "recompute"
            if let Some(s) = session_mut() {
                let (_cw, cell_h) = tile_cell(hwnd);
                s.tile_offset = (s.tile_offset - (mm as i32 / 120) * cell_h).max(0);
            }
            scroll_tiles(hwnd, SB_THUMBPOSITION.0 as u32, i32::MIN);
            LRESULT(0)
        }
        WM_SIZE => {
            scroll_tiles(hwnd, SB_THUMBPOSITION.0 as u32, i32::MIN);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

/// (cell width, cell height) for the current client width - the painter and the scroller must agree.
unsafe fn tile_cell(hwnd: HWND) -> (i32, i32) {
    let mut rc = RECT::default();
    let _ = GetClientRect(hwnd, &mut rc);
    let gap = 10;
    let cell_w = ((rc.right - gap) / TILE_COLS).max(80);
    let img_h = ((cell_w - 2 * gap) as f64 * 0.72) as i32;
    (cell_w, img_h + gap + 34)
}

/// The scroll range, page and position of the tile grid. `track == i32::MIN` means "recompute, do not jump".
unsafe fn scroll_tiles(hwnd: HWND, code: u32, track: i32) {
    let Some(s) = session_mut() else { return };
    let mut rc = RECT::default();
    let _ = GetClientRect(hwnd, &mut rc);
    let (_cell_w, cell_h) = tile_cell(hwnd);
    let rows = (s.ordered.len() as i32 + TILE_COLS - 1) / TILE_COLS;
    let total = (rows * cell_h + 10).max(0);
    let page = rc.bottom.max(1);
    let max_offset = (total - page).max(0);
    let step = if track == i32::MIN {
        0
    } else {
        match code {
            c if c == SB_LINEUP.0 as u32 => -cell_h / 4,
            c if c == SB_LINEDOWN.0 as u32 => cell_h / 4,
            c if c == SB_PAGEUP.0 as u32 => -page + 30,
            c if c == SB_PAGEDOWN.0 as u32 => page - 30,
            c if c == SB_TOP.0 as u32 => -total,
            c if c == SB_BOTTOM.0 as u32 => total,
            _ => {
                // a thumb drag: the 32-bit position lives in TrackPos, not in the 16-bit wParam
                let mut info = SCROLLINFO {
                    cbSize: std::mem::size_of::<SCROLLINFO>() as u32,
                    fMask: SIF_TRACKPOS,
                    nTrackPos: track,
                    ..Default::default()
                };
                if GetScrollInfo(hwnd, SB_VERT, &mut info).is_ok() {
                    info.nTrackPos / cell_h * cell_h - s.tile_offset
                } else {
                    0
                }
            }
        }
    };
    s.tile_offset = (s.tile_offset + step).clamp(0, max_offset);
    let info = SCROLLINFO {
        cbSize: std::mem::size_of::<SCROLLINFO>() as u32,
        fMask: SIF_RANGE | SIF_PAGE | SIF_POS,
        nMin: 0,
        nMax: total,
        nPage: page as u32,
        nPos: s.tile_offset,
        nTrackPos: 0,
    };
    SetScrollInfo(hwnd, SB_VERT, &info, true);
    let _ = InvalidateRect(Some(hwnd), None, true);
}

unsafe fn paint_tiles(hwnd: HWND, hdc: HDC) {
    let mut rc = RECT::default();
    let _ = GetClientRect(hwnd, &mut rc);
    fill(hdc, &rc, bg());
    let Some(s) = session() else { return };
    if s.ordered.is_empty() {
        let mut r = rc;
        line(
            hdc,
            "nothing to show",
            &mut r,
            DT_CENTER | DT_SINGLELINE,
            muted(),
            HFONT(GetStockObject(DEFAULT_GUI_FONT).0),
        );
        return;
    }
    let (cell_w, cell_h) = tile_cell(hwnd);
    let gap = 10;
    let img_w = cell_w - 2 * gap;
    let img_h = cell_h - gap - 34;
    let label_font = ui_font(13, false);
    let badge_font = ui_font(13, true);
    for (i, path) in s.ordered.iter().enumerate() {
        let col = (i as i32) % TILE_COLS;
        let row = (i as i32) / TILE_COLS;
        let x = gap + col * cell_w;
        let y = gap + row * cell_h - s.tile_offset;
        if y + cell_h < 0 || y > rc.bottom {
            continue;
        }
        let cell = RECT { left: x, top: y, right: x + cell_w - gap, bottom: y + cell_h - gap };
        fill(hdc, &cell, if dark() { COLORREF(0x0020_2020) } else { COLORREF(0x00F2_F2F2) });
        blit_fit(hdc, path, x + gap / 2, y + gap / 2, img_w, img_h);
        let mut label = RECT {
            left: x + gap,
            top: y + cell_h - 30,
            right: x + cell_w - 2 * gap - 46,
            bottom: y + cell_h - 12,
        };
        line(
            hdc,
            &format!("{}. {}", i + 1, review::name_of(path)),
            &mut label,
            DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
            ink(),
            label_font,
        );
        let badge = review::badge(path);
        if !badge.is_empty() {
            let mut tag = RECT {
                left: x + cell_w - 2 * gap - 46,
                top: y + cell_h - 30,
                right: x + cell_w - gap,
                bottom: y + cell_h - 12,
            };
            line(hdc, &badge, &mut tag, DT_CENTER | DT_SINGLELINE, muted(), badge_font);
        }
    }
    let _ = DeleteObject(HGDIOBJ(label_font.0));
    let _ = DeleteObject(HGDIOBJ(badge_font.0));
}

// ---- registration ------------------------------------------------------------------------------------------------

/// Register the three window classes. Called once, from `main`, before the loop starts.
pub unsafe fn register(instance: windows::Win32::Foundation::HINSTANCE) -> anyhow::Result<()> {
    let background = if dark() {
        HBRUSH(GetStockObject(windows::Win32::Graphics::Gdi::BLACK_BRUSH).0)
    } else {
        GetSysColorBrush(COLOR_WINDOW)
    };
    let cursor = LoadCursorW(None, IDC_ARROW)?;
    let classes: [(PCWSTR, WNDPROC); 3] = [
        (w!("zimage-review"), Some(review_proc)),
        (w!("zimage-pane"), Some(pane_proc)),
        (w!("zimage-tiles"), Some(tiles_proc)),
    ];
    for (class, proc_) in classes {
        let wc = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: proc_,
            hInstance: instance,
            hCursor: cursor,
            hbrBackground: background,
            lpszClassName: class,
            ..Default::default()
        };
        RegisterClassW(&wc);
    }
    Ok(())
}
