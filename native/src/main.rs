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
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::Graphics::Gdi::{GetStockObject, InvalidateRect, DEFAULT_GUI_FONT};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::{
    SetWindowTheme, LVCOLUMNW, LVIF_TEXT, LVIS_FOCUSED, LVIS_SELECTED, LVITEMW, LVN_GETDISPINFOW, LVN_ITEMCHANGED,
    LVM_DELETEALLITEMS, LVM_GETITEMCOUNT, LVM_GETITEMSTATE, LVM_INSERTCOLUMNW, LVM_INSERTITEMW,
    LVM_SETEXTENDEDLISTVIEWSTYLE, LVM_SETITEMCOUNT, LVM_SETITEMSTATE, LIST_VIEW_ITEM_STATE_FLAGS, LVSICF_NOINVALIDATEALL, LVS_EX_DOUBLEBUFFER,
    LVS_EX_FULLROWSELECT, LVS_LIST, LVS_OWNERDATA, LVS_REPORT, LVS_SHOWSELALWAYS, LVS_SINGLESEL, LVCF_TEXT, LVCF_WIDTH,
};
use windows::Win32::UI::HiDpi::{SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
use windows::Win32::UI::WindowsAndMessaging::*;

const SS_LEFT: u32 = 0; // SS_LEFT is 0; the named constant sits behind a feature this prototype does not need

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
            } else if header.id_from == ID_PICTURES as usize && header.code == LVN_GETDISPINFOW {
                // the control holds no rows of its own: it asks for the text of the row it is about to paint
                let info = &mut *(lparam.0 as *mut NmLvDispInfo);
                if info.item.mask.0 & LVIF_TEXT.0 != 0 && info.item.iSubItem == 0 {
                    if let Some(text) = state().disp.get(info.item.iItem as usize) {
                        info.item.pszText = PWSTR(text.as_ptr() as *mut u16);
                        info.item.cchTextMax = text.len() as i32;
                    }
                }
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

unsafe fn create_controls(hwnd: HWND) {
    let instance = GetModuleHandleW(None).unwrap_or_default();
    let font = GetStockObject(DEFAULT_GUI_FONT);

    let folders = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("SysListView32"),
        w!(""),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_BORDER | WS_CLIPSIBLINGS
            | WINDOW_STYLE((LVS_LIST | LVS_SINGLESEL | LVS_SHOWSELALWAYS) as u32),
        0, 0, 280, 400,
        Some(hwnd),
        Some(HMENU(ID_FOLDERS as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SetWindowTheme(folders, w!("Explorer"), PCWSTR::null());
    SendMessageW(folders, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));

    // virtual: LVS_OWNERDATA tells the control "ask me for what you need", so 1 000 pictures cost 1 000 indices
    let pictures = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        w!("SysListView32"),
        w!(""),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_BORDER | WS_CLIPSIBLINGS
            | WINDOW_STYLE((LVS_REPORT | LVS_OWNERDATA | LVS_SHOWSELALWAYS) as u32),
        0, 0, 700, 400,
        Some(hwnd),
        Some(HMENU(ID_PICTURES as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SetWindowTheme(pictures, w!("Explorer"), PCWSTR::null());
    SendMessageW(pictures, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));
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
        WS_CHILD | WS_VISIBLE | WINDOW_STYLE(SS_LEFT),
        0, 0, 400, 20,
        Some(hwnd),
        Some(HMENU(ID_STATUS as isize as *mut core::ffi::c_void)),
        Some(instance.into()),
        None,
    )
    .unwrap_or_default();
    SendMessageW(status_text, WM_SETFONT, Some(WPARAM(font.0 as usize)), Some(LPARAM(1)));
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
    InvalidateRect(Some(list), None, true);
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
    let _ = MoveWindow(child(hwnd, ID_STATUS), pad, h - pad + 2, w - 2 * pad, 16, true);
}

fn main() -> anyhow::Result<()> {
    unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let instance = GetModuleHandleW(None)?;
        let class = w!("zimage-native");
        let wc = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(wndproc),
            hInstance: instance.into(),
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            hbrBackground: windows::Win32::Graphics::Gdi::GetSysColorBrush(
                windows::Win32::Graphics::Gdi::COLOR_BTNFACE,
            ),
            lpszClassName: class,
            ..Default::default()
        };
        RegisterClassW(&wc);

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
