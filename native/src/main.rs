//! zimage-native - the gallery built out of real Windows controls.
//!
//! The experiment, not the finished app: find out what a port costs when the controls are the ones Windows provides instead
//! of a toolkit's imitation. Everything usable here is a genuine `SysListView32`, `EDIT`, `BUTTON` and `STATIC`, so tab
//! order, focus rectangles, Enter on the default button, Space on the focused one, Alt mnemonics, arrow keys, Home/End/Page
//! and the wheel all come from the system.
//!
//! Run: `zimage-native.exe C:\Users\kevin\Pictures\zimage-ui`   (or set ZIMAGE_GALLERY)

mod comfy;
mod graph;
mod review;
mod review_selftest;
mod review_window;

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

use windows::core::{w, PCWSTR, PWSTR};
use windows::Win32::Foundation::{GENERIC_READ, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, CreateDIBSection, CreateSolidBrush, DeleteDC, DeleteObject, DrawTextW, GetStockObject,
    InvalidateRect, SetBkColor,
    SelectObject, SetBkMode, SetStretchBltMode, SetTextColor, StretchBlt, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
    COLOR_HIGHLIGHT, COLOR_HIGHLIGHTTEXT, COLOR_WINDOW, COLOR_WINDOWTEXT, DEFAULT_GUI_FONT, DIB_RGB_COLORS, HBRUSH,
    DT_END_ELLIPSIS, DT_LEFT, DT_NOPREFIX, DT_SINGLELINE,
    SRCCOPY, STRETCH_DELETESCANS, TRANSPARENT,
};
use windows::Win32::Graphics::Imaging::{
    CLSID_WICImagingFactory2, IWICImagingFactory, WICBitmapDitherTypeNone, WICBitmapInterpolationModeFant,
    WICBitmapInterpolationMode, WICBitmapPaletteTypeCustom, WICDecodeMetadataCacheOnDemand,
    GUID_WICPixelFormat32bppBGR,
};
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::KeyboardAndMouse::EnableWindow;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::{
    SetWindowTheme, LVCOLUMNW, LVIF_TEXT, LVIS_FOCUSED, LVIS_SELECTED, LVITEMW, LVN_GETDISPINFOW, LVN_ITEMCHANGED,
    LVM_DELETEALLITEMS, LVM_GETITEMCOUNT, LVM_GETITEMSTATE, LVM_INSERTCOLUMNW, LVM_INSERTITEMW,
    LVM_ENSUREVISIBLE, LVM_SETEXTENDEDLISTVIEWSTYLE, LVM_SETICONSPACING, LVM_SETITEMCOUNT, LVM_SETITEMSTATE, LIST_VIEW_ITEM_STATE_FLAGS,
    LVSICF_NOINVALIDATEALL, LVM_GETHEADER, NM_CUSTOMDRAW, ImageList_Add, ImageList_Create, LVM_SETIMAGELIST, LVSIL_SMALL,
    LVS_EX_DOUBLEBUFFER,
    LVS_EX_FULLROWSELECT, LVS_LIST, LVS_OWNERDATA, LVS_REPORT, LVS_SHOWSELALWAYS, LVS_SINGLESEL, LVCF_TEXT, LVCF_WIDTH,
};
use windows::Win32::UI::HiDpi::{SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
use windows::Win32::UI::WindowsAndMessaging::*;

const SS_LEFT: u32 = 0;
const SS_NOPREFIX: u32 = 0x0080;
const STATUS_H: i32 = 22; // SS_LEFT is 0; the named constant sits behind a feature this prototype does not need

/// Whether Windows is in dark mode, from the same registry value the Tk app reads: dark mode is a mode, not a colour.
fn os_prefers_dark() -> bool {
    let key = wide(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
    let mut value: u32 = 1;
    let mut size = std::mem::size_of::<u32>() as u32;
    let ok = unsafe {
        windows::Win32::System::Registry::RegGetValueW(
            windows::Win32::System::Registry::HKEY_CURRENT_USER,
            PCWSTR(key.as_ptr()),
            w!("AppsUseLightTheme"),
            windows::Win32::System::Registry::RRF_RT_REG_DWORD,
            None,
            Some(&mut value as *mut u32 as *mut core::ffi::c_void),
            Some(&mut size),
        )
    };
    if ok.is_err() {
        return false;
    }
    value == 0
}

static DARK: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
static DARK_BRUSH: std::sync::OnceLock<isize> = std::sync::OnceLock::new();

fn dark() -> bool {
    *DARK.get_or_init(os_prefers_dark)
}

const ID_FOLDERS: i32 = 101;
const ID_PICTURES: i32 = 102;
const ID_PROMPT: i32 = 103;
const ID_GENERATE: i32 = 104;
pub(crate) const ID_STATUS: i32 = 105;

const WM_GEN_DONE: u32 = WM_APP + 2;
const ID_TICK: usize = 1;

/// The render in flight. The worker thread owns the network and the disk; the window owns the clock and the button, and
/// the two meet through this slot plus one `PostMessage`, because touching child windows from a thread that did not create
/// them is not allowed.
struct Render {
    started: Instant,
    caption: String,
    result: Option<anyhow::Result<comfy::Rendered>>,
}

static RENDER: std::sync::LazyLock<std::sync::Mutex<Option<Render>>> =
    std::sync::LazyLock::new(|| std::sync::Mutex::new(None));

/// A poisoned lock means a thread panicked mid-render; the slot's contents are still worth reading, so take them.
fn render_slot() -> std::sync::MutexGuard<'static, Option<Render>> {
    RENDER.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Set while the controls are being refilled after a render: a programmatic `LVM_SETITEMSTATE` still sends
/// `LVN_ITEMCHANGED`, and the handler would read the list back while it is being rebuilt.
static REFILLING: AtomicBool = AtomicBool::new(false);

// ---- data ----------------------------------------------------------------------------------------------------------------

#[derive(Clone)]
struct Picture {
    path: PathBuf,
    prompt: String,
}

impl Picture {
    fn name(&self) -> String {
        self.path.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default()
    }
}

struct Gallery {
    folders: Vec<(String, Vec<usize>)>,
    pictures: Vec<Picture>,
}

impl Gallery {
    /// Read the gallery the way the Python app does: a picture plus a sidecar carrying its prompt and the root of its
    /// prompt chain. Folders are families keyed by that root, which is what `group_prompts.plan` does.
    fn scan(root: &Path) -> Gallery {
        let mut families: BTreeMap<String, Vec<usize>> = BTreeMap::new();
        let mut pictures = Vec::new();
        let mut paths: Vec<PathBuf> = std::fs::read_dir(root)
            .map(|entries| {
                entries
                    .filter_map(|e| e.ok().map(|e| e.path()))
                    .filter(|p| p.extension().map(|e| e.eq_ignore_ascii_case("png")).unwrap_or(false))
                    .collect()
            })
            .unwrap_or_default();
        paths.sort();
        for path in paths {
            let (prompt, root_text) = match std::fs::read_to_string(path.with_extension("json")) {
                Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
                    Ok(json) => {
                        let prompt = json.get("prompt").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let root = json
                            .get("lineage_root")
                            .and_then(|v| v.as_str())
                            .filter(|s| !s.trim().is_empty())
                            .unwrap_or(&prompt)
                            .to_string();
                        (prompt, root)
                    }
                    Err(_) => (String::new(), String::new()),
                },
                Err(_) => (String::new(), String::new()),
            };
            let label = label_of(&root_text, &prompt);
            let index = pictures.len();
            pictures.push(Picture { path, prompt });
            families.entry(label).or_default().push(index);
        }
        let mut folders: Vec<(String, Vec<usize>)> = families.into_iter().collect();
        folders.sort_by(|a, b| b.1.len().cmp(&a.1.len()).then(a.0.cmp(&b.0)));
        Gallery { folders, pictures }
    }
}

fn label_of(root: &str, prompt: &str) -> String {
    let text = if root.trim().is_empty() { prompt } else { root };
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.trim().is_empty() {
        return "(no prompt recorded)".to_string();
    }
    let cut = flat.char_indices().nth(46).map(|(i, _)| i).unwrap_or(flat.len());
    flat[..cut].to_string()
}

// ---- state ---------------------------------------------------------------------------------------------------------------

struct State {
    gallery: Gallery,
    shown: Vec<usize>,
    folder_label: String,
    /// text handed to the virtual list, kept alive because the control stores the pointer, not the string
    disp: Vec<Vec<u16>>,
    /// the last render's one-line result, appended to the status so the timing survives a thumbnail repaint
    last_render: Option<String>,
}

static mut STATE: Option<Box<State>> = None;

fn state() -> &'static State {
    unsafe { STATE.as_ref().expect("state installed at WM_CREATE") }
}

fn state_mut() -> &'static mut State {
    unsafe { STATE.as_mut().expect("state installed at WM_CREATE") }
}

#[repr(C)]
struct NmHdr {
    hwnd_from: isize,
    id_from: usize,
    code: u32,
}

#[repr(C)]
struct NmLvDispInfo {
    hdr: NmHdr,
    item: LVITEMW,
}

static IMAGE_LIST: std::sync::OnceLock<isize> = std::sync::OnceLock::new();

/// Decoded thumbnails, keyed by picture path, holding an *image list index* - an owner-data list draws its own rows, so
/// the picture has to arrive through LVSIL_SMALL rather than through custom draw (measured: NM_CUSTOMDRAW arrives a
/// handful of times for the window itself and never per row in owner-data mode). Created in worker threads (GDI handles are process-wide) and read by the
/// custom-draw handler on the UI thread, so the map is behind a lock and painting never decodes.
static THUMBS: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<PathBuf, isize>>> =
    std::sync::OnceLock::new();
const WM_THUMB: u32 = WM_APP + 1;

fn thumbs() -> &'static std::sync::Mutex<std::collections::HashMap<PathBuf, isize>> {
    THUMBS.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

/// Decode one picture with WIC at an exact size and hand back a DIB-backed `HBITMAP` with that size.
///
/// WIC is the same decoder Explorer uses, so this is the native path: no image crate, no Python, no intermediate PNG.
unsafe fn decode_scaled(path: &Path, width: i32, height: i32) -> Option<(isize, i32, i32)> {
    let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
    let factory: IWICImagingFactory = CoCreateInstance(&CLSID_WICImagingFactory2, None, CLSCTX_INPROC_SERVER).ok()?;
    let name = wide(&path.to_string_lossy());
    let decoder = factory
        .CreateDecoderFromFilename(PCWSTR(name.as_ptr()), None, GENERIC_READ, WICDecodeMetadataCacheOnDemand)
        .ok()?;
    let frame = decoder.GetFrame(0).ok()?;
    let scaler = factory.CreateBitmapScaler().ok()?;
    scaler
        .Initialize(&frame, width as u32, height as u32,
                    WICBitmapInterpolationModeFant)
        .ok()?;
    let converter = factory.CreateFormatConverter().ok()?;
    converter
        .Initialize(
            &scaler,
            &GUID_WICPixelFormat32bppBGR,
            WICBitmapDitherTypeNone,
            None,
            0.0,
            WICBitmapPaletteTypeCustom,
        )
        .ok()?;

    let stride = width as usize * 4;
    let mut pixels = vec![0u8; stride * height as usize];
    converter.CopyPixels(std::ptr::null(), stride as u32, &mut pixels).ok()?;

    let mut info = BITMAPINFO::default();
    info.bmiHeader = BITMAPINFOHEADER {
        biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
        biWidth: width,
        biHeight: -height, // top-down, the order WIC gives us
        biPlanes: 1,
        biBitCount: 32,
        biCompression: BI_RGB.0,
        ..Default::default()
    };
    let mut bits: *mut core::ffi::c_void = std::ptr::null_mut();
    let dib = CreateDIBSection(None, &info, DIB_RGB_COLORS, &mut bits, None, 0).ok()?;
    if bits.is_null() {
        return None;
    }
    std::ptr::copy_nonoverlapping(pixels.as_ptr(), bits as *mut u8, pixels.len());
    Some((dib.0 as isize, width, height))
}

unsafe fn decode_thumb(path: &Path, width: i32, height: i32) -> Option<isize> {
    decode_scaled(path, width, height).map(|(dib, _, _)| dib)
}

/// Images decoded to fit a box, keyed by path and box size: a review pane and a ranked tile both ask for "this picture,
/// as large as fits", and neither can re-decode on every `WM_PAINT`.
static FITS: std::sync::LazyLock<std::sync::Mutex<std::collections::HashMap<(PathBuf, i32, i32), (isize, i32, i32)>>> =
    std::sync::LazyLock::new(|| std::sync::Mutex::new(std::collections::HashMap::new()));

/// The picture scaled to fit `max_w` x `max_h` without cropping and without enlarging, plus its real size.
pub(crate) fn fit_image(path: &Path, max_w: i32, max_h: i32) -> Option<(isize, i32, i32)> {
    if max_w < 16 || max_h < 16 {
        return None;
    }
    let fits = &FITS;
    if let Some(hit) = fits.lock().ok().and_then(|m| m.get(&(path.to_path_buf(), max_w, max_h)).copied()) {
        return Some(hit);
    }
    unsafe {
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let factory: IWICImagingFactory = CoCreateInstance(&CLSID_WICImagingFactory2, None, CLSCTX_INPROC_SERVER).ok()?;
        let name = wide(&path.to_string_lossy());
        let decoder = factory
            .CreateDecoderFromFilename(PCWSTR(name.as_ptr()), None, GENERIC_READ, WICDecodeMetadataCacheOnDemand)
            .ok()?;
        let frame = decoder.GetFrame(0).ok()?;
        let (mut fw, mut fh) = (0u32, 0u32);
        frame.GetSize(&mut fw, &mut fh).ok()?;
        // `Image.thumbnail` never enlarges, and neither does this
        let scale = (max_w as f64 / fw.max(1) as f64)
            .min(max_h as f64 / fh.max(1) as f64)
            .min(1.0);
        let (tw, th) = (
            ((fw as f64 * scale).round() as i32).max(1),
            ((fh as f64 * scale).round() as i32).max(1),
        );
        let made = decode_scaled(path, tw, th)?;
        let mut map = fits.lock().ok()?;
        if map.len() > 32 {
            // a resize changes every key; the old bitmaps are unreachable and must be handed back to GDI
            for (_, (dib, _, _)) in map.drain() {
                let _ = DeleteObject(windows::Win32::Graphics::Gdi::HGDIOBJ(dib as *mut core::ffi::c_void));
            }
        }
        map.insert((path.to_path_buf(), max_w, max_h), made);
        Some(made)
    }
}

/// Decode every listed picture off the UI thread and poke the list as each one lands. A 336 px JPEG is ~2-4 ms, so 1 000
/// of them would stall the window if painting did it; the list simply draws a placeholder until its tile is ready.
unsafe fn spawn_thumbs(hwnd: HWND, paths: Vec<PathBuf>) {
    let hwnd_value = hwnd.0 as isize;
    std::thread::spawn(move || {
        for (i, path) in paths.iter().enumerate() {
            let already = thumbs().lock().map(|m| m.contains_key(path)).unwrap_or(true);
            if already {
                continue;
            }
            if let Some(hbitmap) = decode_thumb(path, THUMB_W, THUMB_H) {
                if let Some(list) = IMAGE_LIST.get().copied() {
                    let index = ImageList_Add(
                        windows::Win32::UI::Controls::HIMAGELIST(list),
                        windows::Win32::Graphics::Gdi::HBITMAP(hbitmap as *mut core::ffi::c_void),
                        None,
                    );
                    if index >= 0 {
                        if let Ok(mut map) = thumbs().lock() {
                            map.insert(path.clone(), index as isize);
                        }
                    }
                }
            }
            if i % 8 == 0 {
                let hwnd = HWND(hwnd_value as *mut core::ffi::c_void);
                let _ = PostMessageW(Some(hwnd), WM_THUMB, WPARAM(0), LPARAM(0));
            }
        }
        let hwnd = HWND(hwnd_value as *mut core::ffi::c_void);
        let _ = PostMessageW(Some(hwnd), WM_THUMB, WPARAM(1), LPARAM(0));
    });
}

static LIST_OWNER: std::sync::OnceLock<isize> = std::sync::OnceLock::new();

const THUMB_W: i32 = 112;
const THUMB_H: i32 = 84;

// ---- helpers -------------------------------------------------------------------------------------------------------------

pub(crate) fn wide(text: &str) -> Vec<u16> {
    OsString::from(text).encode_wide().chain(std::iter::once(0)).collect()
}

fn gallery_root() -> PathBuf {
    std::env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("ZIMAGE_GALLERY").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from(r"C:\Users\kevin\Pictures\zimage-ui"))
}

unsafe fn child(hwnd: HWND, id: i32) -> HWND {
    GetDlgItem(Some(hwnd), id).unwrap_or_default()
}

unsafe fn text_of(hwnd: HWND) -> String {
    let len = GetWindowTextLengthW(hwnd);
    let mut buf = vec![0u16; len as usize + 1];
    let got = GetWindowTextW(hwnd, &mut buf);
    OsString::from_wide(&buf[..got.max(0) as usize]).to_string_lossy().to_string()
}

pub(crate) fn short(text: &str, n: usize) -> String {
    if text.chars().count() > n {
        text.chars().take(n).collect::<String>() + "\u{2026}"
    } else {
        text.to_string()
    }
}

/// The two colours a `STATIC`/`EDIT` is told to use, and the brush behind it.
///
/// Shared with the A/B review window: a second window in the same process must paint its text the same way, and the
/// brush is process-wide (`DARK_BRUSH`), so there is one place that decides.
pub(crate) unsafe fn ctl_color(wparam: WPARAM) -> LRESULT {
    let hdc = windows::Win32::Graphics::Gdi::HDC(wparam.0 as *mut core::ffi::c_void);
    let (text, back) = if dark() {
        (
            windows::Win32::Foundation::COLORREF(0x00E8E8E8),
            windows::Win32::Foundation::COLORREF(0x001A1A1A),
        )
    } else {
        (
            windows::Win32::Foundation::COLORREF(windows::Win32::Graphics::Gdi::GetSysColor(COLOR_WINDOWTEXT)),
            windows::Win32::Foundation::COLORREF(windows::Win32::Graphics::Gdi::GetSysColor(COLOR_WINDOW)),
        )
    };
    SetTextColor(hdc, text);
    SetBkColor(hdc, back);
    let brush = *DARK_BRUSH.get_or_init(|| {
        CreateSolidBrush(if dark() {
            windows::Win32::Foundation::COLORREF(0x001A1A1A)
        } else {
            windows::Win32::Foundation::COLORREF(windows::Win32::Graphics::Gdi::GetSysColor(COLOR_WINDOW))
        })
        .0 as isize
    });
    LRESULT(brush)
}

// ---- window --------------------------------------------------------------------------------------------------------------

unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_CREATE => {
            let gallery = Gallery::scan(&gallery_root());
            let first = gallery.folders.first().map(|f| f.0.clone()).unwrap_or_default();
            let shown = gallery
                .folders
                .iter()
                .find(|f| f.0 == first)
                .map(|f| f.1.clone())
                .unwrap_or_default();
            unsafe {
                STATE = Some(Box::new(State {
                    gallery,
                    shown,
                    folder_label: first,
                    disp: Vec::new(),
                    last_render: None,
                }));
            }
            let _ = LIST_OWNER.set(hwnd.0 as isize);
            create_controls(hwnd);
            fill_folders(hwnd);
            fill_pictures(hwnd);
            update_status(hwnd);
            LRESULT(0)
        }
        WM_SIZE => {
            layout(hwnd);
            LRESULT(0)
        }
        WM_CTLCOLORSTATIC | WM_CTLCOLOREDIT => return ctl_color(wparam),
        WM_COMMAND => {
            let id = (wparam.0 & 0xffff) as i32;
            let code = ((wparam.0 >> 16) & 0xffff) as u16;
            if id == ID_GENERATE && code == BN_CLICKED as u16 {
                // the default button: Enter from anywhere in this window lands here
                start_generation(hwnd, text_of(child(hwnd, ID_PROMPT)));
            }
            if id == review_window::ID_AB && code == BN_CLICKED as u16 {
                start_review(hwnd);
            }
            if id == IDCANCEL.0 as i32 {
                let _ = DestroyWindow(hwnd);
            }
            LRESULT(0)
        }
        WM_NOTIFY => {
            let header = &*(lparam.0 as *const NmHdr);
            if header.id_from == ID_FOLDERS as usize
                && header.code == LVN_ITEMCHANGED
                && !REFILLING.load(Ordering::SeqCst)
            {
                let list = child(hwnd, ID_FOLDERS);
                let count = SendMessageW(list, LVM_GETITEMCOUNT, None, None).0 as i32;
                for i in 0..count {
                    let flags = SendMessageW(list, LVM_GETITEMSTATE, Some(WPARAM(i as usize)),
                                             Some(LPARAM(LVIS_SELECTED.0 as isize))).0 as u32;
                    if flags & LVIS_SELECTED.0 != 0 {
                        let st = state_mut();
                        if let Some((label, indices)) = st.gallery.folders.get(i as usize) {
                            st.folder_label = label.clone();
                            st.shown = indices.clone();
                        }
                        fill_pictures(hwnd);
                        update_status(hwnd);
                        break;
                    }
                }
            } else if header.code == NM_CUSTOMDRAW {
                let cd = &mut *(lparam.0 as *mut NmcdCustomDraw);
                if cd.nmcd.dwDrawStage == CDDS_PREPAINT {
                    return LRESULT(CDRF_NOTIFYITEMDRAW as isize);
                }
                if cd.nmcd.dwDrawStage == CDDS_ITEMPREPAINT {
                    paint_row(cd, hwnd);
                    return LRESULT(CDRF_SKIPDEFAULT as isize);
                }
                return LRESULT(CDRF_DODEFAULT as isize);
            } else if header.id_from == ID_PICTURES as usize && header.code == LVN_GETDISPINFOW {
                // the control holds no rows of its own: it asks for the text of the row it is about to paint
                let info = &mut *(lparam.0 as *mut NmLvDispInfo);
                if info.item.mask.0 & windows::Win32::UI::Controls::LVIF_IMAGE.0 != 0 {
                    let st = state();
                    if let Some(picture) = st.shown.get(info.item.iItem as usize)
                        .and_then(|i| st.gallery.pictures.get(*i))
                    {
                        if let Some(index) = thumbs().lock().ok().and_then(|m| m.get(&picture.path).copied()) {
                            info.item.iImage = index as i32;
                        }
                    }
                }
                if info.item.mask.0 & LVIF_TEXT.0 != 0 && info.item.iSubItem == 0 {
                    if let Some(text) = state().disp.get(info.item.iItem as usize) {
                        info.item.pszText = PWSTR(text.as_ptr() as *mut u16);
                        info.item.cchTextMax = text.len() as i32;
                    }
                }
            }
            LRESULT(0)
        }
        WM_THUMB => {
            let list = child(hwnd, ID_PICTURES);
            let _ = InvalidateRect(Some(list), None, false);
            if wparam.0 == 1 {
                update_status(hwnd);
            }
            LRESULT(0)
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            LRESULT(0)
        }
        WM_TIMER => {
            // the elapsed clock: the worker cannot touch the status, so the window ticks it four times a second
            if wparam.0 == ID_TICK {
                let text = {
                    let slot = render_slot();
                    slot.as_ref()
                        .map(|entry| format!("{} \u{2014} rendering {:.0} s",
                                             entry.caption, entry.started.elapsed().as_secs_f64()))
                };
                if let Some(text) = text {
                    set_status(hwnd, &text);
                }
            }
            LRESULT(0)
        }
        WM_GEN_DONE => {
            finish_generation(hwnd);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

#[repr(C)]
struct NmcdCustomDraw {
    hdr: NmHdr,
    nmcd: NmCustomDraw,
}

#[repr(C)]
struct NmCustomDraw {
    hdr: NmHdr,
    dwDrawStage: u32,
    hdc: *mut core::ffi::c_void,
    rc: windows::Win32::Foundation::RECT,
    dwItemSpec: usize,
    uItemState: u32,
    lItemlParam: isize,
}

const CDDS_PREPAINT: u32 = 0x0000_0001;
const CDDS_ITEMPREPAINT: u32 = 0x0001_0001; // CDDS_ITEM | CDDS_PREPAINT, not CDDS_ITEM alone
const CDRF_DODEFAULT: isize = 0x0000_0000;
const CDRF_NOTIFYITEMDRAW: isize = 0x0000_0020;
const CDRF_SKIPDEFAULT: isize = 0x0000_0004;

/// One row: the decoded tile, then the file name and the prompt. The list owns selection, focus and scrolling.
unsafe fn paint_row(cd: &mut NmcdCustomDraw, hwnd: HWND) {
    let st = state();
    let Some(&index) = st.shown.get(cd.nmcd.dwItemSpec) else { return };
    let Some(picture) = st.gallery.pictures.get(index) else { return };
    let hdc = windows::Win32::Graphics::Gdi::HDC(cd.nmcd.hdc as *mut core::ffi::c_void);
    let rect = cd.nmcd.rc;
    let selected = (cd.nmcd.uItemState as u32) & 0x0002 != 0; // CDIS_SELECTED
    let background: HBRUSH = windows::Win32::Graphics::Gdi::GetSysColorBrush(
        if selected { COLOR_HIGHLIGHT } else { COLOR_WINDOW },
    );
    windows::Win32::Graphics::Gdi::FillRect(hdc, &rect, background);

    let tile = windows::Win32::Foundation::RECT {
        left: rect.left + 4,
        top: rect.top + 2,
        right: rect.left + 4 + THUMB_W,
        bottom: rect.top + 2 + THUMB_H,
    };
    let bitmap = thumbs().lock().ok().and_then(|m| m.get(&picture.path).copied());
    match bitmap {
        Some(dib) => {
            let mem = CreateCompatibleDC(Some(hdc));
            let old = SelectObject(mem, windows::Win32::Graphics::Gdi::HGDIOBJ(dib as *mut core::ffi::c_void));
            SetStretchBltMode(hdc, STRETCH_DELETESCANS);
            let _ = StretchBlt(hdc, tile.left, tile.top, THUMB_W, THUMB_H, Some(mem), 0, 0, THUMB_W, THUMB_H, SRCCOPY);
            SelectObject(mem, old);
            let _ = DeleteDC(mem);
        }
        None => {
            let placeholder = windows::Win32::Graphics::Gdi::CreateSolidBrush(windows::Win32::Foundation::COLORREF(
                if selected { 0x00302010 } else { 0x00181818 },
            ));
            windows::Win32::Graphics::Gdi::FillRect(hdc, &tile, placeholder);
            let _ = DeleteObject(windows::Win32::Graphics::Gdi::HGDIOBJ(placeholder.0));
        }
    }

    SetBkMode(hdc, TRANSPARENT);
    SetTextColor(
        hdc,
        windows::Win32::Foundation::COLORREF(windows::Win32::Graphics::Gdi::GetSysColor(
            if selected { COLOR_HIGHLIGHTTEXT } else { COLOR_WINDOWTEXT },
        )),
    );
    let old_font = SelectObject(hdc, GetStockObject(DEFAULT_GUI_FONT));
    let mut y = rect.top + 4;
    for line in row_lines(picture, 70).into_iter().take(3) {
        let mut r = windows::Win32::Foundation::RECT {
            left: rect.left + THUMB_W + 14,
            top: y,
            right: rect.right - 6,
            bottom: rect.bottom,
        };
        let mut text = wide(&line);
        DrawTextW(hdc, &mut text, &mut r as *mut _, DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
        y += 17;
    }
    SelectObject(hdc, old_font);
    let _ = hwnd;
}

fn row_lines(picture: &Picture, per_line: usize) -> Vec<String> {
    let mut out = vec![picture.name()];
    let body = if picture.prompt.trim().is_empty() { "(no prompt recorded)" } else { &picture.prompt };
    let mut line = String::new();
    for word in body.split_whitespace() {
        if line.len() + word.len() + 1 > per_line {
            out.push(std::mem::take(&mut line));
        }
        if !line.is_empty() {
            line.push(' ');
        }
        line.push_str(word);
        if out.len() >= 3 {
            break;
        }
    }
    if out.len() < 3 && !line.is_empty() {
        out.push(line);
    }
    out
}

unsafe fn create_controls(hwnd: HWND) {
    let instance = GetModuleHandleW(None).unwrap_or_default();
    let font = GetStockObject(DEFAULT_GUI_FONT);

    let folders = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("SysListView32"),
        w!(""),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_CLIPSIBLINGS
            | WINDOW_STYLE((LVS_LIST | LVS_SINGLESEL | LVS_SHOWSELALWAYS) as u32),
        0, 0, 280, 400,
        Some(hwnd),
        Some(HMENU(ID_FOLDERS as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SetWindowTheme(folders, w!("Explorer"), PCWSTR::null());
    paint_list_dark(folders);
    SendMessageW(folders, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));

    // virtual: LVS_OWNERDATA tells the control "ask me for what you need", so 1 000 pictures cost 1 000 indices
    let pictures = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("SysListView32"),
        w!(""),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_CLIPSIBLINGS
            | WINDOW_STYLE((LVS_REPORT | LVS_OWNERDATA | LVS_SHOWSELALWAYS) as u32),
        0, 0, 700, 400,
        Some(hwnd),
        Some(HMENU(ID_PICTURES as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SetWindowTheme(pictures, w!("Explorer"), PCWSTR::null());
    paint_list_dark(pictures);
    // the column header is a separate HWND: without its own theme it stays a light band across a dark list
    let header = SendMessageW(pictures, LVM_GETHEADER, None, None);
    let header = HWND(header.0 as *mut core::ffi::c_void);
    if dark() && !header.is_invalid() {
        SetWindowTheme(header, w!("DarkMode_ItemsView"), PCWSTR::null());
    }
    SendMessageW(pictures, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));
    // In report mode the row height comes from the *small* image list, so a 84 px tile needs an image list that tall:
    // the list never draws from it (we paint every row ourselves), it only sets the height of a row.
    let row_images = ImageList_Create(THUMB_W, THUMB_H + 6, windows::Win32::UI::Controls::ILC_COLOR32, 8, 8);
    let _ = IMAGE_LIST.set(row_images.0 as isize);
    let _ = SendMessageW(pictures, LVM_SETIMAGELIST, Some(WPARAM(LVSIL_SMALL as usize)),
                         Some(LPARAM(row_images.0 as isize)));
    SendMessageW(
        pictures,
        LVM_SETEXTENDEDLISTVIEWSTYLE,
        Some(WPARAM(0)),
        Some(LPARAM((LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER) as isize)),
    );
    let name = wide("Picture");
    let mut column = LVCOLUMNW {
        mask: LVCF_TEXT | LVCF_WIDTH,
        cx: 620,
        pszText: PWSTR(name.as_ptr() as *mut u16),
        ..Default::default()
    };
    SendMessageW(pictures, LVM_INSERTCOLUMNW, Some(WPARAM(0)), Some(LPARAM(&mut column as *mut _ as isize)));

    let prompt = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("EDIT"),
        w!(""),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_BORDER | WINDOW_STYLE(ES_AUTOHSCROLL as u32 | WS_GROUP.0),
        0, 0, 400, 26,
        Some(hwnd),
        Some(HMENU(ID_PROMPT as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SendMessageW(prompt, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));

    // BS_DEFPUSHBUTTON is the real default button: Windows draws its ring and Enter activates it
    let generate = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("BUTTON"),
        w!("&Generate"),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WINDOW_STYLE(BS_DEFPUSHBUTTON as u32 | WS_GROUP.0),
        0, 0, 140, 30,
        Some(hwnd),
        Some(HMENU(ID_GENERATE as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SendMessageW(generate, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));

    // A/B…: compare the open family two pictures at a time. A plain BUTTON, because nothing about it is special - what
    // it opens is a second *window*, not a dialog.
    let ab = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("BUTTON"),
        w!("&A/B\u{2026}"),   // the mnemonic is the A: the label still reads "A/B…", as the app it replaces
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WINDOW_STYLE(BS_PUSHBUTTON as u32),
        0, 0, 112, 30,
        Some(hwnd),
        Some(HMENU(review_window::ID_AB as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SendMessageW(ab, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));

    let status_text = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("STATIC"),
        w!(""),
        WS_CHILD | WS_VISIBLE | WINDOW_STYLE(SS_LEFT | SS_NOPREFIX),
        0, 0, 400, STATUS_H,
        Some(hwnd),
        Some(HMENU(ID_STATUS as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SendMessageW(status_text, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));
}

/// A dark list view: the colours are the control's own (LVM_SET*COLOR), not the system's classic palette, which never
/// becomes dark. Without this the window stays light in a dark OS however the theme is set.
unsafe fn paint_list_dark(list: HWND) {
    if !dark() {
        return;
    }
    let text = windows::Win32::Foundation::COLORREF(0x00E8E8E8);
    let back = windows::Win32::Foundation::COLORREF(0x001A1A1A);
    let _ = SendMessageW(list, windows::Win32::UI::Controls::LVM_SETBKCOLOR, None, Some(LPARAM(back.0 as isize)));
    let _ = SendMessageW(list, windows::Win32::UI::Controls::LVM_SETTEXTCOLOR, None, Some(LPARAM(text.0 as isize)));
    let _ = SendMessageW(list, windows::Win32::UI::Controls::LVM_SETTEXTBKCOLOR, None,
                         Some(LPARAM(back.0 as isize)));
}

unsafe fn fill_folders(hwnd: HWND) {
    let list = child(hwnd, ID_FOLDERS);
    SendMessageW(list, LVM_DELETEALLITEMS, None, None);
    let labels: Vec<Vec<u16>> = state()
        .gallery
        .folders
        .iter()
        .map(|(label, indices)| wide(&format!("{} ({})", short(label, 40), indices.len())))
        .collect();
    for (i, text) in labels.iter().enumerate() {
        let mut item = LVITEMW {
            mask: LVIF_TEXT,
            iItem: i as i32,
            pszText: PWSTR(text.as_ptr() as *mut u16),
            ..Default::default()
        };
        SendMessageW(list, LVM_INSERTITEMW, Some(WPARAM(0)), Some(LPARAM(&mut item as *mut _ as isize)));
    }
    if !labels.is_empty() {
        let mut item = LVITEMW {
            state: LIST_VIEW_ITEM_STATE_FLAGS(LVIS_SELECTED.0 | LVIS_FOCUSED.0),
            stateMask: LIST_VIEW_ITEM_STATE_FLAGS(LVIS_SELECTED.0 | LVIS_FOCUSED.0),
            ..Default::default()
        };
        SendMessageW(list, LVM_SETITEMSTATE, Some(WPARAM(0)), Some(LPARAM(&mut item as *mut _ as isize)));
    }
}

unsafe fn fill_pictures(hwnd: HWND) {
    let list = child(hwnd, ID_PICTURES);
    let st = state_mut();
    st.disp = st
        .shown
        .iter()
        .filter_map(|i| st.gallery.pictures.get(*i))
        .map(|p| {
            let first = p.prompt.split_whitespace().take(14).collect::<Vec<_>>().join(" ");
            wide(&format!("{}   \u{2014}   {}", p.name(), if first.is_empty() { "(no prompt)" } else { &first }))
        })
        .collect();
    let count = st.shown.len();
    SendMessageW(list, LVM_SETITEMCOUNT, Some(WPARAM(count)), Some(LPARAM(LVSICF_NOINVALIDATEALL as isize)));
    let _ = InvalidateRect(Some(list), None, true);
    if let Some(hwnd_value) = LIST_OWNER.get().copied() {
        let paths: Vec<PathBuf> = st
            .shown
            .iter()
            .filter_map(|i| st.gallery.pictures.get(*i).map(|p| p.path.clone()))
            .collect();
        spawn_thumbs(HWND(hwnd_value as *mut core::ffi::c_void), paths);
    }
}

unsafe fn update_status(hwnd: HWND) {
    let st = state();
    let mut text = format!(
        "{} pictures, {} families \u{00b7} showing {} \u{00b7} {}",
        st.gallery.pictures.len(),
        st.gallery.folders.len(),
        st.shown.len(),
        short(&st.folder_label, 60)
    );
    // the render line is the interesting part of the status when there is one, and a thumbnail repaint must not eat it
    if let Some(line) = &st.last_render {
        text.push_str(" \u{00b7} ");
        text.push_str(line);
    }
    set_status(hwnd, &text);
}

/// `A/B…` - review the family that is open (or the folder, since that is what the list is showing), two at a time.
///
/// The session is the Python app's: the same key from the same names, the same file, the same sidecar counters. Nothing
/// is deleted, so the review only ever adds a record.
unsafe fn start_review(hwnd: HWND) {
    let st = state();
    let paths: Vec<String> = st
        .shown
        .iter()
        .filter_map(|i| st.gallery.pictures.get(*i))
        .map(|p| p.path.to_string_lossy().to_string())
        .collect();
    let label = st.folder_label.clone();
    if paths.len() < 2 {
        set_status(hwnd, &format!(
            "{} - {} picture{}, nothing to pair",
            short(&label, 40),
            paths.len(),
            if paths.len() == 1 { "" } else { "s" }
        ));
        return;
    }
    if let Err(e) = review_window::open(hwnd, &label, &paths) {
        set_status(hwnd, &format!("A/B review failed: {}", e));
    }
}

// ---- generation ----------------------------------------------------------------------------------------------------------

unsafe fn set_status(hwnd: HWND, text: &str) {
    let buf = wide(text);
    let _ = SetWindowTextW(child(hwnd, ID_STATUS), PCWSTR(buf.as_ptr()));
}

/// `Generate` was pressed - or Enter, through the default button. One render at a time: the button goes grey, the status
/// becomes a clock, and everything left (queue, poll, download, write) happens on a thread that is not this one.
unsafe fn start_generation(hwnd: HWND, prompt: String) {
    let prompt = prompt.trim().to_string();
    if prompt.is_empty() {
        set_status(hwnd, "type a prompt first");
        return;
    }
    let mut slot = render_slot();
    if slot.is_some() {
        return; // a render is already in flight; the button is disabled for exactly that window
    }
    let model = std::env::var("ZIMAGE_MODEL").unwrap_or_else(|_| graph::DEFAULT_MODEL.to_string());
    let job = comfy::Job::new(&prompt, &model, &gallery_root());
    let caption = format!("{}x{} px, {} steps, seed {}, {}", job.width, job.height, job.steps, job.seed, job.model);
    set_status(hwnd, &format!("{caption} \u{2014} rendering 0 s"));
    let _ = EnableWindow(child(hwnd, ID_GENERATE), false);
    let _ = SetTimer(Some(hwnd), ID_TICK, 250, None);
    *slot = Some(Render { started: Instant::now(), caption, result: None });
    drop(slot);

    let hwnd_value = hwnd.0 as isize;
    std::thread::spawn(move || {
        let outcome = comfy::generate(&job, &comfy::Config::from_env());
        if let Some(entry) = render_slot().as_mut() {
            entry.result = Some(outcome);
        }
        let hwnd = HWND(hwnd_value as *mut core::ffi::c_void);
        let _ = PostMessageW(Some(hwnd), WM_GEN_DONE, WPARAM(0), LPARAM(0));
    });
}

/// The worker is done: put the button and the clock back, then either show the picture or say why there is none.
unsafe fn finish_generation(hwnd: HWND) {
    let _ = KillTimer(Some(hwnd), ID_TICK);
    let _ = EnableWindow(child(hwnd, ID_GENERATE), true);
    let Some((elapsed, outcome)) = render_slot()
        .take()
        .map(|entry| (entry.started.elapsed().as_secs_f64(), entry.result))
    else {
        return;
    };
    match outcome {
        Some(Ok(rendered)) => {
            let name = rendered.png.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            state_mut().last_render = Some(format!("rendered {name} in {:.1} s", rendered.seconds));
            refresh_after_generate(hwnd, &rendered.png);
        }
        Some(Err(error)) => {
            let line = format!("last render failed: {}", short(&error.to_string(), 90));
            state_mut().last_render = Some(line);
            set_status(hwnd, &format!("generation failed after {elapsed:.1} s: {error}"));
        }
        None => {
            let line = "last render ended without a result".to_string();
            state_mut().last_render = Some(line.clone());
            set_status(hwnd, &line);
        }
    }
}

/// Re-scan the gallery and show the family the new picture landed in. `fill_pictures` hands the listed paths to the
/// thumbnail worker, so the tile for the picture that was just written appears without a restart.
unsafe fn refresh_after_generate(hwnd: HWND, new_path: &Path) {
    let gallery = Gallery::scan(&gallery_root());
    let holds_new = |indices: &Vec<usize>| {
        indices.iter().any(|i| gallery.pictures.get(*i).map(|p| p.path == new_path).unwrap_or(false))
    };
    // the new picture's family, else the family that was on screen, else whatever is first
    let target = gallery
        .folders
        .iter()
        .position(|(_, indices)| holds_new(indices))
        .or_else(|| gallery.folders.iter().position(|(label, _)| *label == state().folder_label))
        .unwrap_or(0);
    let (label, shown) = gallery
        .folders
        .get(target)
        .map(|(label, indices)| (label.clone(), indices.clone()))
        .unwrap_or_default();
    {
        let st = state_mut();
        st.gallery = gallery;
        st.shown = shown;
        st.folder_label = label;
    }
    // programmatic selection still sends LVN_ITEMCHANGED, and the handler would read the list mid-rebuild
    REFILLING.store(true, Ordering::SeqCst);
    fill_folders(hwnd);
    select_row(child(hwnd, ID_FOLDERS), target);
    REFILLING.store(false, Ordering::SeqCst);
    fill_pictures(hwnd);
    // put the new picture on screen: its own row, scrolled into view
    let row = {
        let st = state();
        st.shown
            .iter()
            .position(|i| st.gallery.pictures.get(*i).map(|p| p.path == new_path).unwrap_or(false))
    };
    if let Some(row) = row {
        let list = child(hwnd, ID_PICTURES);
        select_row(list, row);
        let _ = SendMessageW(list, LVM_ENSUREVISIBLE, Some(WPARAM(row)), Some(LPARAM(0)));
    }
    update_status(hwnd);
}

/// Select one row of a list view the way the control itself would report it: selected and focused, in one message.
unsafe fn select_row(list: HWND, row: usize) {
    let mut item = LVITEMW {
        state: LIST_VIEW_ITEM_STATE_FLAGS(LVIS_SELECTED.0 | LVIS_FOCUSED.0),
        stateMask: LIST_VIEW_ITEM_STATE_FLAGS(LVIS_SELECTED.0 | LVIS_FOCUSED.0),
        ..Default::default()
    };
    SendMessageW(list, LVM_SETITEMSTATE, Some(WPARAM(row)), Some(LPARAM(&mut item as *mut _ as isize)));
}

unsafe fn layout(hwnd: HWND) {
    let mut client = windows::Win32::Foundation::RECT::default();
    let _ = GetClientRect(hwnd, &mut client);
    let (w, h) = (client.right.max(600), client.bottom.max(400));
    let pad = 10;
    let row = 32;
    let col = 300;
    let _ = MoveWindow(child(hwnd, ID_FOLDERS), pad, pad, col, h - 2 * pad - row - 6, true);
    let _ = MoveWindow(child(hwnd, ID_PICTURES), col + 2 * pad, pad, w - col - 3 * pad, h - 2 * pad - row - 6, true);
    let _ = MoveWindow(child(hwnd, ID_PROMPT), pad, h - pad - row + 4, w - 330, 24, true);
    let _ = MoveWindow(child(hwnd, ID_GENERATE), w - pad - 150, h - pad - row + 2, 150, 28, true);
    let _ = MoveWindow(child(hwnd, review_window::ID_AB), w - pad - 150 - 8 - 112, h - pad - row + 2, 112, 28, true);
    // the status gets its own line under the controls, at its full height: shaving 2 px off the top and 4 off the
    // bottom left a 12 px client for a 16 px font, so the descenders of the render clock were cut
    let _ = MoveWindow(child(hwnd, ID_STATUS), pad, h - STATUS_H, w - 2 * pad, STATUS_H, true);
}

fn main() -> anyhow::Result<()> {
    // `--theme` prints the decision and exits: the only honest way to know whether the registry read works
    if std::env::args().nth(1).as_deref() == Some("--theme") {
        println!("dark = {}", dark());
        return Ok(());
    }
    // `--probe <png> [maxw] [maxh]` prints what the decoder hands back: the size it scaled to and the centre pixel
    if std::env::args().nth(1).as_deref() == Some("--probe") {
        let path = std::env::args_os().nth(2).map(PathBuf::from).expect("a png path");
        let maxw: i32 = std::env::args().nth(3).and_then(|s| s.parse().ok()).unwrap_or(517);
        let maxh: i32 = std::env::args().nth(4).and_then(|s| s.parse().ok()).unwrap_or(290);
        use windows::Win32::Graphics::Gdi::HBITMAP as PROBE_BITMAP;
        use windows::Win32::Graphics::Gdi::{DIB_RGB_COLORS as PROBE_COLORS, GetDIBits, HGDIOBJ as PROBE_OBJ};
        unsafe {
            match fit_image(&path, maxw, maxh) {
                Some((dib, w, h)) => {
                    let mut info = BITMAPINFO::default();
                    info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
                    info.bmiHeader.biWidth = w;
                    info.bmiHeader.biHeight = -h;
                    info.bmiHeader.biPlanes = 1;
                    info.bmiHeader.biBitCount = 32;
                    info.bmiHeader.biCompression = BI_RGB.0;
                    let mut px = vec![0u8; (w * h * 4) as usize];
                    let mem = CreateCompatibleDC(None);
                    let old = SelectObject(mem, PROBE_OBJ(dib as *mut core::ffi::c_void));
                    let n = GetDIBits(mem, PROBE_BITMAP(dib as *mut core::ffi::c_void), 0, h as u32, Some(px.as_mut_ptr() as *mut core::ffi::c_void), &mut info, PROBE_COLORS);
                    SelectObject(mem, old);
                    let _ = DeleteDC(mem);
                    let centre = ((h / 2 * w + w / 2) * 4) as usize;
                    println!("probe {} -> {}x{} dib={} rows={} centre=({}, {}, {})",
                        path.display(), w, h, dib, n, px[centre + 2], px[centre + 1], px[centre]);
                }
                None => println!("probe {} -> the decoder returned nothing", path.display()),
            }
        }
        return Ok(());
    }
    // `--selftest [dir]` renders once with no window and asserts the sidecar: the same path, minus the controls
    if std::env::args().nth(1).as_deref() == Some("--selftest") {
        let dir = std::env::args_os().nth(2).map(PathBuf::from);
        return selftest(dir);
    }
    // `--review-selftest` builds its own four-picture gallery, runs an A/B review to the end, and checks the state file
    // and the sidecars against the Python implementation - no server, no window, no screen.
    if std::env::args().nth(1).as_deref() == Some("--review-selftest") {
        return review_selftest::run();
    }
    unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let instance = GetModuleHandleW(None)?;
        let class = w!("zimage-native");
        let wc = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(wndproc),
            hInstance: instance.into(),
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            hbrBackground: if dark() {
                HBRUSH(windows::Win32::Graphics::Gdi::GetStockObject(
                    windows::Win32::Graphics::Gdi::BLACK_BRUSH,
                ).0)
            } else {
                windows::Win32::Graphics::Gdi::GetSysColorBrush(windows::Win32::Graphics::Gdi::COLOR_BTNFACE)
            },
            lpszClassName: class,
            ..Default::default()
        };
        RegisterClassW(&wc);
        // the A/B review's three classes: the window, the picture panes, the ranked tiles
        review_window::register(instance.into())?;
        let is_dark = dark();
        if is_dark {
            let _ = DARK_BRUSH.set(
                CreateSolidBrush(windows::Win32::Foundation::COLORREF(0x001A1A1A)).0 as isize,
            );
        }

        let _hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            class,
            w!("zimage-native \u{2014} real Windows controls"),
            WS_OVERLAPPEDWINDOW | WS_VISIBLE,
            CW_USEDEFAULT, CW_USEDEFAULT, 1180, 740,
            None,
            None,
            Some(instance.into()),
            None,
        )?;

        if dark() {
            // the immersive dark title bar, and the Explorer list-view theme that goes with it
            let yes: i32 = 1;
            let _ = windows::Win32::Graphics::Dwm::DwmSetWindowAttribute(
                _hwnd,
                windows::Win32::Graphics::Dwm::DWMWA_USE_IMMERSIVE_DARK_MODE,
                &yes as *const i32 as *const core::ffi::c_void,
                4,
            );
            for id in [ID_FOLDERS, ID_PICTURES] {
                SetWindowTheme(child(_hwnd, id), w!("DarkMode_Explorer"), PCWSTR::null());
            }
            let _ = InvalidateRect(Some(_hwnd), None, true);
        }

        // IsDialogMessage is the point of the experiment: Tab between controls, Enter on the default button, Esc and Alt
        // mnemonics, from the system, with none of our code in the path.
        //
        // Two things had to be addressed to the *top-level* window rather than to whatever has the focus: `GetAncestor`
        // because IsDialogMessage is what finds the default button (a child has no buttons of its own), and the A/B
        // review's accelerator table because `1`/`2`/`3` must answer wherever the focus sits inside that window.
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).0 > 0 {
            if let (Some(haccel), Some(review)) = (review_window::haccel(), review_window::window()) {
                let original = msg.hwnd;
                msg.hwnd = review;
                let translated = TranslateAcceleratorW(review, haccel, &msg as *const MSG);
                msg.hwnd = original;
                if translated != 0 {
                    continue;
                }
            }
            let root = GetAncestor(msg.hwnd, GA_ROOT);
            let target = if root.is_invalid() { msg.hwnd } else { root };
            if IsDialogMessageW(target, &mut msg).as_bool() {
                continue;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    Ok(())
}

// ---- selftest ------------------------------------------------------------------------------------------------------------

/// Render once headlessly and assert the sidecar, so the part the gallery depends on can be checked without a screen.
/// Pinned values: 256 px, the profile's own steps, seed 4711 - assertions against numbers nobody can accidentally match.
fn selftest(dir: Option<PathBuf>) -> anyhow::Result<()> {
    let out = dir.unwrap_or_else(|| std::env::temp_dir().join("zimage-native-selftest"));
    std::fs::create_dir_all(&out)?;
    let config = comfy::Config::from_env();
    let prompt = "selftest: a red apple on a wooden table, soft window light";
    let model = std::env::var("ZIMAGE_MODEL").unwrap_or_else(|_| graph::DEFAULT_MODEL.to_string());
    let mut job = comfy::Job::new(prompt, &model, &out);
    job.width = 256;
    job.height = 256;
    job.seed = 4711;
    job.steps = graph::profile(&job.model).steps;

    println!("selftest  server {}  dir {}", config.origin(), out.display());
    println!(
        "          {} at {}x{} px, {} steps, cfg {}, seed {}",
        job.model, job.width, job.height, job.steps, job.cfg, job.seed
    );
    let started = Instant::now();
    let rendered = comfy::generate(&job, &config)?;
    println!(
        "          rendered in {:.1} s (wall {:.1} s) -> {}",
        rendered.seconds,
        started.elapsed().as_secs_f64(),
        rendered.png.display()
    );

    let record: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&rendered.sidecar)?)?;
    let text = |key: &str| record.get(key).and_then(|v| v.as_str()).unwrap_or_default().to_string();
    let number = |key: &str| record.get(key).and_then(|v| v.as_u64());
    let name = rendered.png.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let bytes = std::fs::read(&rendered.png)?;
    let size = (bytes.get(..4) == Some(b"\x89PNG".as_slice())).then(|| png_size(&bytes)).flatten();

    let mut checked = 0;
    let mut failed = 0;
    macro_rules! check {
        ($label:expr, $ok:expr, $detail:expr) => {{
            checked += 1;
            let ok: bool = $ok;
            println!("          {:<14} {:<4} {}", $label, if ok { "ok" } else { "FAIL" }, $detail);
            if !ok {
                failed += 1;
            }
        }};
    }
    check!("prompt", text("prompt") == prompt, text("prompt"));
    check!("seed", number("seed") == Some(job.seed), format!("{:?}", number("seed")));
    check!("steps", number("steps") == Some(job.steps as u64), format!("{:?}", number("steps")));
    check!("cfg", record.get("cfg").and_then(|v| v.as_f64()) == Some(job.cfg),
           format!("{:?}", record.get("cfg")));
    check!("model", text("model") == job.model, text("model"));
    check!("width", number("width") == Some(rendered.width as u64), format!("{:?}", number("width")));
    check!("height", number("height") == Some(rendered.height as u64), format!("{:?}", number("height")));
    check!("created", is_created(&text("created")), text("created"));
    check!("image", text("image") == rendered.png.to_string_lossy(), text("image"));
    check!("lineage_root", text("lineage_root") == prompt, text("lineage_root"));
    check!("file name", name.ends_with(&format!("_{}x{}_seed{}.png", rendered.width, rendered.height, job.seed))
                          && name.as_bytes().get(8) == Some(&b'-'),
           name);
    check!("png", size == Some((rendered.width, rendered.height)), format!("{size:?}, {} bytes", bytes.len()));

    println!("          {checked} checks, {failed} failed");
    if failed > 0 {
        anyhow::bail!("selftest failed: {failed} of {checked} assertions");
    }
    Ok(())
}

/// `2026-10-30T18:12:33`: the shape `datetime.isoformat(timespec='seconds')` writes into every Python sidecar.
fn is_created(text: &str) -> bool {
    let digits = |range: std::ops::Range<usize>| text.get(range).map_or(false, |s| s.bytes().all(|b| b.is_ascii_digit()));
    text.len() == 19
        && digits(0..4) && digits(5..7) && digits(8..10)
        && digits(11..13) && digits(14..16) && digits(17..19)
        && text.as_bytes()[4] == b'-'
        && text.as_bytes()[7] == b'-'
        && text.as_bytes()[10] == b'T'
        && text.as_bytes()[13] == b':'
        && text.as_bytes()[16] == b':'
}

/// The PNG's own size, for the selftest: the sidecar must describe the file, not the request.
fn png_size(bytes: &[u8]) -> Option<(i32, i32)> {
    if bytes.len() < 24 || &bytes[12..16] != b"IHDR" {
        return None;
    }
    let read = |at: usize| i32::from_be_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]]);
    Some((read(16), read(20)))
}
