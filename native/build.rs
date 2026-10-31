// Common Controls v6 and per-monitor DPI come from an application manifest. It is passed as a *file* to the linker: the
// /MANIFESTDEPENDENCY form is one argument containing spaces and quotes, and the linker word-splits it
// (LNK1181: cannot open input file "name='Microsoft.Windows.Common-Controls'").
fn main() {
    let dir = std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets this");
    println!("cargo:rustc-link-arg-bins=/MANIFEST:EMBED");
    println!("cargo:rustc-link-arg-bins=/MANIFESTINPUT:{dir}/app.manifest");
    println!("cargo:rerun-if-changed=app.manifest");
}
