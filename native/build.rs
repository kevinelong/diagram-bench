// Common Controls v6 and per-monitor DPI are both declared in an application manifest. The linker can carry the
// dependency directly, so the exe needs no .manifest file beside it and the window gets the Windows 11 control set.
fn main() {
    println!(
        "cargo:rustc-link-arg-bins=/MANIFESTDEPENDENCY:\"type='win32' name='Microsoft.Windows.Common-Controls' \
version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'\""
    );
}
