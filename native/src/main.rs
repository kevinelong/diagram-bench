//! zimage-native - the gallery built out of real Windows controls.
//!
//! The experiment, not the finished app: find out what a port costs when the controls are the ones Windows provides instead
//! of a toolkit's imitation. Everything usable here is a genuine `SysListView32`, `EDIT`, `BUTTON` and `STATIC`, so tab
//! order, focus rectangles, Enter on the default button, Space on the focused one, Alt mnemonics, arrow keys, Home/End/Page
//! and the wheel all come from the system.
//!
//! Run: `zimage-native.exe C:\Users\kevin\Pictures\zimage-ui`   (or set ZIMAGE_GALLERY)

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};

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
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::{
    SetWindowTheme, LVCOLUMNW, LVIF_TEXT, LVIS_FOCUSED, LVIS_SELECTED, LVITEMW, LVN_GETDISPINFOW, LVN_ITEMCHANGED,
    LVM_DELETEALLITEMS, LVM_GETITEMCOUNT, LVM_GETITEMSTATE, LVM_INSERTCOLUMNW, LVM_INSERTITEMW,
    LVM_SETEXTENDEDLISTVIEWSTYLE, LVM_SETICONSPACING, LVM_SETITEMCOUNT, LVM_SETITEMSTATE, LIST_VIEW_ITEM_STATE_FLAGS,
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
const ID_STATUS: i32 = 105;

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

/// Decode one picture with WIC, scale it to the tile and hand back a DIB-backed `HBITMAP`.
///
/// WIC is the same decoder Explorer uses, so this is the native path: no image crate, no Python, no intermediate PNG.
unsafe fn decode_thumb(path: &Path, width: i32, height: i32) -> Option<isize> {
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
    Some(dib.0 as isize)
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

fn wide(text: &str) -> Vec<u16> {
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

fn short(text: &str, n: usize) -> String {
    if text.chars().count() > n {
        text.chars().take(n).collect::<String>() + "\u{2026}"
    } else {
        text.to_string()
    }
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
                STATE = Some(Box::new(State { gallery, shown, folder_label: first, disp: Vec::new() }));
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
        WM_CTLCOLORSTATIC | WM_CTLCOLOREDIT => {
            // EDIT and STATIC are not themed by the list view's dark theme; they are told what colours to use here
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
            return LRESULT(brush);
        }
        WM_COMMAND => {
            let id = (wparam.0 & 0xffff) as i32;
            let code = ((wparam.0 >> 16) & 0xffff) as u16;
            if id == ID_GENERATE && code == BN_CLICKED as u16 {
                // the default button: Enter from anywhere in this window lands here
                let prompt = text_of(child(hwnd, ID_PROMPT));
                let text = format!("A native port would send this to ComfyUI:\n\n{}", prompt);
                let body = wide(&text);
                let caption = wide("Generate");
                let _ = MessageBoxW(Some(hwnd), PCWSTR(body.as_ptr()), PCWSTR(caption.as_ptr()),
                                    MB_OK);
            }
            if id == IDCANCEL.0 as i32 {
                let _ = DestroyWindow(hwnd);
            }
            LRESULT(0)
        }
        WM_NOTIFY => {
            let header = &*(lparam.0 as *const NmHdr);
            {
                // temporary probe: which notifications actually arrive, and with which code
                use std::io::Write;
                if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true)
                    .open(r"C:\Users\kevin\AppData\Local\Temp\native-notify.log") {
                    let _ = writeln!(f, "notify from {} code {:#x}", header.id_from, header.code);
                }
            }
            if header.id_from == ID_FOLDERS as usize && header.code == LVN_ITEMCHANGED {
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
                let seen = format!("cd from {} stage {:#x}", header.id_from, cd.nmcd.dwDrawStage);
                let buf = wide(&seen);
                let _ = SetWindowTextW(child(hwnd, ID_STATUS), PCWSTR(buf.as_ptr()));
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
    let text = format!(
        "{} pictures, {} families \u{00b7} showing {} \u{00b7} {}",
        st.gallery.pictures.len(),
        st.gallery.folders.len(),
        st.shown.len(),
        short(&st.folder_label, 60)
    );
    let buf = wide(&text);
    let _ = SetWindowTextW(child(hwnd, ID_STATUS), PCWSTR(buf.as_ptr()));
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
    // the status gets its own line under the controls, tall enough for the font (at 16 px it was clipped)
    let _ = MoveWindow(child(hwnd, ID_STATUS), pad, h - STATUS_H + 2, w - 2 * pad, STATUS_H - 4, true);
}

fn main() -> anyhow::Result<()> {
    // `--theme` prints the decision and exits: the only honest way to know whether the registry read works
    if std::env::args().nth(1).as_deref() == Some("--theme") {
        println!("dark = {}", dark());
        return Ok(());
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
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).0 > 0 {
            if IsDialogMessageW(msg.hwnd, &mut msg).as_bool() {
                continue;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    Ok(())
}
