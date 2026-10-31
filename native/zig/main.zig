//! zimage-zig - the same milestone as ../src/main.rs, in Zig, calling the same Win32 entry points.
//!
//! Folders: SysListView32 in LVS_LIST. Pictures: SysListView32 in LVS_REPORT + LVS_OWNERDATA (virtual). Prompt: EDIT.
//! Default action: BUTTON with BS_DEFPUSHBUTTON. Status: STATIC. The message loop calls IsDialogMessageW, so tab order,
//! Enter on the default button, Space on the focused one, Alt mnemonics, arrows, the wheel and the system's focus
//! rectangles are the operating system's, exactly as in the Rust version - the comparison is the language, not the design.
//!
//! Build: zig build-exe main.zig -O ReleaseSmall -luser32 -lgdi32 -lcomctl32 -subsystem windows -femit-bin=zimage-zig.exe

const std = @import("std");
const win = @cImport({
    @cInclude("windows.h");
    @cInclude("commctrl.h");
});

const Allocator = std.mem.Allocator;

const Picture = struct {
    name: []const u8,
    prompt: []const u8,
};

const Folder = struct {
    label: []const u8,
    items: []usize,
};

const State = struct {
    alloc: Allocator,
    pictures: []Picture,
    folders: []Folder,
    shown: []usize,
    folder_label: []const u8,
    disp: std.ArrayListUnmanaged([]const u8) = .empty, // UTF-8 text, converted on demand
    disp_w: std.ArrayListUnmanaged([:0]const u16) = .empty,
};

var g_state: ?*State = null;

fn state() *State {
    return g_state.?;
}

const ID_FOLDERS: c_int = 101;
const ID_PICTURES: c_int = 102;
const ID_PROMPT: c_int = 103;
const ID_GENERATE: c_int = 104;
const ID_STATUS: c_int = 105;

fn toWide(alloc: Allocator, text: []const u8) [:0]u16 {
    return std.unicode.utf8ToUtf16LeAllocZ(alloc, text) catch unreachable;
}

fn toUtf8(alloc: Allocator, text: []const u16) []const u8 {
    var out: std.ArrayListUnmanaged(u8) = .empty;
    for (text) |unit| {
        var buf: [4]u8 = undefined;
        const n = std.unicode.utf16LeToUtf8(&buf, unit) catch 1;
        out.appendSlice(alloc, buf[0..n]) catch unreachable;
    }
    return out.items;
}

fn labelOf(alloc: Allocator, root: []const u8, prompt: []const u8) []const u8 {
    const text = if (std.mem.trim(u8, root, " \t\r\n").len == 0) prompt else root;
    const flat = std.mem.trim(u8, text, " \t\r\n");
    if (flat.len == 0) return dup(alloc, "(no prompt recorded)");
    const cut = @min(flat.len, 46);
    return dup(alloc, flat[0..cut]);
}

fn dup(alloc: Allocator, text: []const u8) []const u8 {
    return alloc.dupe(u8, text) catch unreachable;
}

fn scan(alloc: Allocator, root_path: []const u8) void {
    var dir = std.fs.cwd().openDir(root_path, .{ .iterate = true }) catch {
        g_state = makeState(alloc, &.{}, &.{}, &.{}, "");
        return;
    };
    defer dir.close();

    var pictures: std.ArrayListUnmanaged(Picture) = .empty;
    var families = std.StringArrayHashMapUnmanaged(std.ArrayListUnmanaged(usize)){};

    var it = dir.iterate();
    while (it.next() catch null) |entry| {
        if (entry.kind != .file) continue;
        if (!std.mem.endsWith(u8, entry.name, ".png")) continue;

        var prompt: []const u8 = "";
        var rot: []const u8 = "";
        const json_name = std.fmt.allocPrint(alloc, "{s}.json", .{entry.name[0 .. entry.name.len - 4]}) catch continue;
        if (dir.readFileAlloc(alloc, json_name, 1 << 20)) |text| {
            if (std.json.parseFromSlice(std.json.Value, alloc, text, .{}) catch null) |parsed| {
                if (parsed.value == .object) {
                    if (parsed.value.object.get("prompt")) |v| {
                        if (v == .string) {
                            prompt = dup(alloc, v.string);
                            rot = prompt;
                        }
                    }
                    if (parsed.value.object.get("lineage_root")) |v| {
                        if (v == .string and std.mem.trim(u8, v.string, " \t\r\n").len > 0) rot = dup(alloc, v.string);
                    }
                }
            }
        } else |_| {}

        const label = labelOf(alloc, rot, prompt);
        const index = pictures.items.len;
        pictures.append(alloc, .{ .name = dup(alloc, entry.name), .prompt = prompt }) catch unreachable;
        const slot = families.getOrPut(alloc, label) catch unreachable;
        if (!slot.found_existing) slot.value_ptr.* = .empty;
        slot.value_ptr.append(alloc, index) catch unreachable;
    }

    // biggest family first
    var folders: std.ArrayListUnmanaged(Folder) = .empty;
    var fit = families.iterator();
    while (fit.next()) |kv| {
        folders.append(alloc, .{ .label = kv.key_ptr.*, .items = kv.value_ptr.items }) catch unreachable;
    }
    std.mem.sort(Folder, folders.items, {}, struct {
        fn less(_: void, a: Folder, b: Folder) bool {
            if (a.items.len != b.items.len) return a.items.len > b.items.len;
            return std.mem.order(u8, a.label, b.label) == .lt;
        }
    }.less);

    const first: []const u8 = if (folders.items.len > 0) folders.items[0].label else "";
    const shown: []usize = if (folders.items.len > 0) folders.items[0].items else &.{};
    g_state = makeState(alloc, pictures.items, folders.items, shown, first);
}

fn makeState(alloc: Allocator, pictures: []Picture, folders: []Folder, shown: []usize, label: []const u8) *State {
    const st = alloc.create(State) catch unreachable;
    st.* = .{
        .alloc = alloc,
        .pictures = pictures,
        .folders = folders,
        .shown = @constCast(shown),
        .folder_label = @constCast(label),
    };
    return st;
}

fn child(hwnd: win.HWND, id: c_int) win.HWND {
    return win.GetDlgItem(hwnd, id);
}

fn setText(hwnd: win.HWND, text: []const u8) void {
    const w = toWide(std.heap.page_allocator, text);
    _ = win.SetWindowTextW(hwnd, w.ptr);
}

fn textOf(hwnd: win.HWND) []const u8 {
    var buf: [1024]u16 = undefined;
    const got = win.GetWindowTextW(hwnd, &buf, buf.len);
    if (got <= 0) return "";
    return toUtf8(std.heap.page_allocator, std.mem.sliceTo(buf[0..@intCast(got)], 0));
}

fn fillFolders(hwnd: win.HWND) void {
    const st = state();
    const list = child(hwnd, ID_FOLDERS);
    _ = win.SendMessageW(list, win.LVM_DELETEALLITEMS, 0, 0);
    for (st.folders, 0..) |folder, i| {
        const text = std.fmt.allocPrint(st.alloc, "{s} ({d})", .{
            folder.label[0..@min(folder.label.len, 40)], folder.items.len,
        }) catch continue;
        const w = toWide(st.alloc, text);
        var item: win.LVITEMW = std.mem.zeroes(win.LVITEMW);
        item.mask = win.LVIF_TEXT;
        item.iItem = @intCast(i);
        item.pszText = @constCast(@ptrCast(w.ptr));
        _ = win.SendMessageW(list, win.LVM_INSERTITEMW, 0, @intFromPtr(&item));
    }
    if (st.folders.len > 0) {
        var item: win.LVITEMW = std.mem.zeroes(win.LVITEMW);
        item.state = win.LVIS_SELECTED | win.LVIS_FOCUSED;
        item.stateMask = win.LVIS_SELECTED | win.LVIS_FOCUSED;
        _ = win.SendMessageW(list, win.LVM_SETITEMSTATE, 0, @intFromPtr(&item));
    }
}

fn fillPictures(hwnd: win.HWND) void {
    const st = state();
    const list = child(hwnd, ID_PICTURES);
    st.disp_w.clearRetainingCapacity();
    for (st.shown) |index| {
        if (index >= st.pictures.len) continue;
        const picture = st.pictures[index];
        const text = std.fmt.allocPrint(st.alloc, "{s}   \u{2014}   {s}", .{
            picture.name, if (picture.prompt.len == 0) "(no prompt)" else picture.prompt,
        }) catch continue;
        st.disp_w.append(st.alloc, toWide(st.alloc, text)) catch {};
    }
    _ = win.SendMessageW(list, win.LVM_SETITEMCOUNT, st.shown.len, 0);
    _ = win.InvalidateRect(list, null, 1);
}

fn updateStatus(hwnd: win.HWND) void {
    const st = state();
    const text = std.fmt.allocPrint(st.alloc, "{d} pictures, {d} families \u{00b7} showing {d} \u{00b7} {s}", .{
        st.pictures.len,
        st.folders.len,
        st.shown.len,
        st.folder_label[0..@min(st.folder_label.len, 60)],
    }) catch return;
    setText(child(hwnd, ID_STATUS), text);
}

fn layout(hwnd: win.HWND) void {
    var client: win.RECT = std.mem.zeroes(win.RECT);
    _ = win.GetClientRect(hwnd, &client);
    const width = @max(client.right, 600);
    const height = @max(client.bottom, 400);
    const pad = 10;
    const row = 32;
    const col = 300;
    _ = win.MoveWindow(child(hwnd, ID_FOLDERS), pad, pad, col, height - 2 * pad - row - 6, 1);
    _ = win.MoveWindow(child(hwnd, ID_PICTURES), col + 2 * pad, pad, width - col - 3 * pad, height - 2 * pad - row - 6, 1);
    _ = win.MoveWindow(child(hwnd, ID_PROMPT), pad, height - pad - row + 4, width - 330, 24, 1);
    _ = win.MoveWindow(child(hwnd, ID_GENERATE), width - pad - 150, height - pad - row + 2, 150, 28, 1);
    _ = win.MoveWindow(child(hwnd, ID_STATUS), pad, height - pad + 2, width - 2 * pad, 16, 1);
}

fn createControls(hwnd: win.HWND) void {
    const st = state();
    const instance = win.GetModuleHandleW(null);
    const font = win.GetStockObject(win.DEFAULT_GUI_FONT);

    const folders = win.CreateWindowExW(0, win.utf16Literal("SysListView32"), win.utf16Literal(""),
        win.WS_CHILD | win.WS_VISIBLE | win.WS_TABSTOP | win.WS_BORDER | win.LVS_LIST | win.LVS_SINGLESEL | win.LVS_SHOWSELALWAYS,
        0, 0, 280, 400, hwnd, @ptrFromInt(ID_FOLDERS), instance, null);
    _ = win.SetWindowTheme(folders, win.utf16Literal("Explorer"), null);
    _ = win.SendMessageW(folders, win.WM_SETFONT, @intFromPtr(font), 1);

    const pictures = win.CreateWindowExW(0, win.utf16Literal("SysListView32"), win.utf16Literal(""),
        win.WS_CHILD | win.WS_VISIBLE | win.WS_TABSTOP | win.WS_BORDER | win.LVS_REPORT | win.LVS_OWNERDATA | win.LVS_SHOWSELALWAYS,
        0, 0, 700, 400, hwnd, @ptrFromInt(ID_PICTURES), instance, null);
    _ = win.SetWindowTheme(pictures, win.utf16Literal("Explorer"), null);
    _ = win.SendMessageW(pictures, win.WM_SETFONT, @intFromPtr(font), 1);
    _ = win.SendMessageW(pictures, win.LVM_SETEXTENDEDLISTVIEWSTYLE, 0,
        win.LVS_EX_FULLROWSELECT | win.LVS_EX_DOUBLEBUFFER);

    var column: win.LVCOLUMNW = std.mem.zeroes(win.LVCOLUMNW);
    column.mask = win.LVCF_TEXT | win.LVCF_WIDTH;
    column.cx = 620;
    const col_name = toWide(st.alloc, "Picture");
    column.pszText = @constCast(@ptrCast(col_name.ptr));
    _ = win.SendMessageW(pictures, win.LVM_INSERTCOLUMNW, 0, @intFromPtr(&column));

    const prompt = win.CreateWindowExW(0, win.utf16Literal("EDIT"), win.utf16Literal(""),
        win.WS_CHILD | win.WS_VISIBLE | win.WS_TABSTOP | win.WS_BORDER | win.ES_AUTOHSCROLL,
        0, 0, 400, 26, hwnd, @ptrFromInt(ID_PROMPT), instance, null);
    _ = win.SendMessageW(prompt, win.WM_SETFONT, @intFromPtr(font), 1);

    const generate = win.CreateWindowExW(0, win.utf16Literal("BUTTON"), win.utf16Literal("&Generate"),
        win.WS_CHILD | win.WS_VISIBLE | win.WS_TABSTOP | win.BS_DEFPUSHBUTTON,
        0, 0, 140, 30, hwnd, @ptrFromInt(ID_GENERATE), instance, null);
    _ = win.SendMessageW(generate, win.WM_SETFONT, @intFromPtr(font), 1);

    const status = win.CreateWindowExW(0, win.utf16Literal("STATIC"), win.utf16Literal(""),
        win.WS_CHILD | win.WS_VISIBLE, 0, 0, 400, 20, hwnd, @ptrFromInt(ID_STATUS), instance, null);
    _ = win.SendMessageW(status, win.WM_SETFONT, @intFromPtr(font), 1);
}

fn wndproc(hwnd: win.HWND, msg: c_uint, wparam: win.WPARAM, lparam: win.LPARAM) callconv(std.builtin.CallingConvention.winapi) win.LRESULT {
    switch (msg) {
        win.WM_CREATE => {
            const alloc = std.heap.page_allocator;
            scan(alloc, galleryRoot());
            createControls(hwnd);
            fillFolders(hwnd);
            fillPictures(hwnd);
            updateStatus(hwnd);
            return 0;
        },
        win.WM_SIZE => {
            layout(hwnd);
            return 0;
        },
        win.WM_COMMAND => {
            const id: c_int = @intCast(wparam & 0xffff);
            if (id == ID_GENERATE) {
                const prompt = textOf(child(hwnd, ID_PROMPT));
                const text = std.fmt.allocPrint(std.heap.page_allocator, "A native port would send this to ComfyUI:\n\n{s}", .{prompt}) catch return 0;
                const body = toWide(std.heap.page_allocator, text);
                const caption = toWide(std.heap.page_allocator, "Generate");
                _ = win.MessageBoxW(hwnd, body.ptr, caption.ptr, win.MB_OK);
            }
            if (id == win.IDCANCEL) {
                _ = win.DestroyWindow(hwnd);
            }
            return 0;
        },
        win.WM_NOTIFY => {
            const header: *win.NMHDR = @ptrFromInt(@as(usize, @intCast(lparam)));
            if (header.idFrom == ID_FOLDERS and header.code == win.LVN_ITEMCHANGED) {
                const list = child(hwnd, ID_FOLDERS);
                const count = win.SendMessageW(list, win.LVM_GETITEMCOUNT, 0, 0);
                var i: c_int = 0;
                while (i < count) : (i += 1) {
                    const flags = win.SendMessageW(list, win.LVM_GETITEMSTATE, @intCast(i), win.LVIS_SELECTED);
                    if (flags & win.LVIS_SELECTED != 0) {
                        const st = state();
                        const index: usize = @intCast(i);
                        if (index < st.folders.len) {
                            st.folder_label = st.folders[index].label;
                            st.shown = st.folders[index].items;
                        }
                        fillPictures(hwnd);
                        updateStatus(hwnd);
                        break;
                    }
                }
            } else if (header.idFrom == ID_PICTURES and header.code == win.LVN_GETDISPINFOW) {
                const info: *win.NMLVDISPINFOW = @ptrFromInt(@as(usize, @intCast(lparam)));
                if (info.item.mask & win.LVIF_TEXT != 0 and info.item.iSubItem == 0) {
                    const st = state();
                    const index: usize = @intCast(info.item.iItem);
                    if (index < st.disp_w.items.len) {
                        const text = st.disp_w.items[index];
                        info.item.pszText = @constCast(@ptrCast(text.ptr));
                        info.item.cchTextMax = @intCast(text.len);
                    }
                }
            }
            return 0;
        },
        win.WM_DESTROY => {
            win.PostQuitMessage(0);
            return 0;
        },
        else => return win.DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

var root_buffer: [512]u8 = undefined;

fn galleryRoot() []const u8 {
    const fallback = "C:\\Users\\kevin\\Pictures\\zimage-ui";
    var args = std.process.args();
    _ = args.next();
    if (args.next()) |arg| {
        const len = @min(arg.len, root_buffer.len);
        @memcpy(root_buffer[0..len], arg[0..len]);
        return root_buffer[0..len];
    }
    return fallback;
}

pub fn main() !void {
    const instance = win.GetModuleHandleW(null);
    var wc: win.WNDCLASSW = std.mem.zeroes(win.WNDCLASSW);
    wc.style = win.CS_HREDRAW | win.CS_VREDRAW;
    wc.lpfnWndProc = wndproc;
    wc.hInstance = instance;
    wc.hCursor = win.LoadCursorW(null, win.IDC_ARROW);
    wc.hbrBackground = win.GetSysColorBrush(win.COLOR_BTNFACE);
    wc.lpszClassName = win.utf16Literal("zimage-zig");
    _ = win.RegisterClassW(&wc);

    const hwnd = win.CreateWindowExW(0, wc.lpszClassName, win.utf16Literal("zimage-zig \u{2014} real Windows controls"),
        win.WS_OVERLAPPEDWINDOW | win.WS_VISIBLE, win.CW_USEDEFAULT, win.CW_USEDEFAULT, 1180, 740,
        null, null, instance, null);
    if (hwnd == null) return error.NoWindow;

    var msg: win.MSG = std.mem.zeroes(win.MSG);
    while (win.GetMessageW(&msg, null, 0, 0) > 0) {
        if (win.IsDialogMessageW(hwnd, &msg) != 0) continue;
        _ = win.TranslateMessage(&msg);
        _ = win.DispatchMessageW(&msg);
    }
}
