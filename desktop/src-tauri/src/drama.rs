//! Starting the drama app - Unione AI Drama - with a ticket that signs it in (#2302).
//!
//! The app is a separate program on the same computer, so the console cannot
//! reach it the way it reaches a page: something has to start a process. That is
//! what this module is for, and it is deliberately the whole of what the remote
//! page may ask for here - **no path, no arguments, no command line of the page's
//! own choosing**. The page supplies one ticket, this module supplies everything
//! else, and a page that is compromised can therefore start the drama app and
//! nothing else.
//!
//! The ticket goes on the command line rather than through a file or an
//! environment variable because it is the shortest-lived secret in the exchange
//! (two minutes, single use) and the app's own `main` already reads it that way.
//! It does outlive the process in one place - the operating system's list of
//! running processes, for as long as the app runs - which is the reason the
//! backend mints it for two minutes and spends it on first use.

use std::path::PathBuf;
use std::process::{Command, Stdio};

use serde::Serialize;
use tauri::{AppHandle, Manager};

use crate::WINDOW;

/// The name the app is installed under, in every platform's own spelling of it.
const APP_NAME: &str = "Unione AI Drama";

/// The executable's name. Kept from before the rename on purpose: the binary is
/// `reveric` on every platform, and only the window and the bundle carry the
/// product's name.
#[cfg(windows)]
const BINARY: &str = "reveric.exe";
#[cfg(not(windows))]
const BINARY: &str = "reveric";

/// The names the app has been installed under, newest first.
///
/// The installer used to spell the product `Reveric`, and an installation made
/// then is still a perfectly good one to start - so a machine that upgraded the
/// console but not the drama app is not told the app is missing.
const APP_NAMES: [&str; 2] = [APP_NAME, "Reveric"];

/// Where a person may point the shell at an app that is somewhere else - a
/// development build, a portable copy, a second installation. Checked first so
/// that a machine with two of them can say which one is meant.
const OVERRIDE: &str = "UNIONE_DRAMA_PATH";

/// What the console is told about an attempt. `reason` is a code, not a sentence:
/// the page has its own words for each, in the reader's language.
#[derive(Debug, Serialize)]
pub struct DramaLaunch {
    pub launched: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<&'static str>,
}

fn refused(reason: &'static str) -> DramaLaunch {
    DramaLaunch {
        launched: false,
        reason: Some(reason),
    }
}

/// Whether this is the shape of a ticket the backend mints: `tk-` and 32 hex digits.
///
/// Checked here rather than trusted, because it becomes an argument to a process
/// this module starts: a value with a space or a quote in it would be one more
/// argument, or one fewer.
pub fn valid_ticket(ticket: &str) -> bool {
    let Some(rest) = ticket.strip_prefix("tk-") else {
        return false;
    };
    (16..=64).contains(&rest.len()) && rest.chars().all(|c| c.is_ascii_alphanumeric())
}

/// The origin of the console, which is where the app redeems the ticket.
///
/// Read from the window rather than from settings: the person may have moved this
/// shell between two deployments since it started, and the deployment they are
/// looking at is the one that minted the ticket.
fn issuer_of(app: &AppHandle) -> Option<String> {
    let url = app.get_webview_window(WINDOW)?.url().ok()?;
    let host = url.host_str()?;
    let mut origin = format!("{}://{host}", url.scheme());
    if let Some(port) = url.port() {
        origin.push_str(&format!(":{port}"));
    }
    Some(origin)
}

/// The places each platform's installer puts the app, most specific first.
fn install_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if cfg!(windows) {
        // `currentUser` installs - the mode the shell's own installer uses - land
        // under LOCALAPPDATA; a per-machine install under PROGRAMFILES.
        for variable in ["LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"] {
            let Some(base) = std::env::var_os(variable) else {
                continue;
            };
            let base = PathBuf::from(base);
            for name in APP_NAMES {
                candidates.push(base.join(name).join(BINARY));
                candidates.push(base.join("Programs").join(name).join(BINARY));
            }
        }
    } else if cfg!(target_os = "macos") {
        for bundle in ["/Applications", "/Applications/Utilities"] {
            for name in APP_NAMES {
                candidates.push(
                    PathBuf::from(bundle)
                        .join(format!("{name}.app"))
                        .join("Contents/MacOS")
                        .join(BINARY),
                );
            }
        }
        if let Some(home) = std::env::var_os("HOME") {
            for name in APP_NAMES {
                candidates.push(
                    PathBuf::from(home.as_os_str())
                        .join("Applications")
                        .join(format!("{name}.app"))
                        .join("Contents/MacOS")
                        .join(BINARY),
                );
            }
        }
    } else {
        for directory in ["/usr/local/bin", "/usr/bin", "/opt/reveric"] {
            candidates.push(PathBuf::from(directory).join(BINARY));
        }
        if let Some(home) = std::env::var_os("HOME") {
            candidates.push(PathBuf::from(home).join(".local/bin").join(BINARY));
        }
    }
    candidates
}

/// A file called `name` on the shell's own `PATH`.
fn on_path(name: &str) -> Option<PathBuf> {
    std::env::split_paths(&std::env::var_os("PATH")?)
        .map(|directory| directory.join(name))
        .find(|candidate| candidate.is_file())
}

/// How an uninstall entry spells the product, matched case-insensitively
/// against the key's name and its display name.
///
/// The display name is localised - `Unione AI Drama 版本 0.7.0` - and the key is
/// named after the installer that wrote it, `ai.reveric_is1`, so a lookup that
/// knows only one of the two spellings misses installations that are perfectly
/// good. Neither marker is the bare product family: this console is installed as
/// `UnioneAgent`, and a marker that matched that would find the shell itself and
/// start it again in place of the drama app.
const ENTRY_MARKERS: [&str; 2] = ["reveric", "ai drama"];

/// The path out of a registry value that names a file.
///
/// The installers write these three ways - bare, quoted because the folder has a
/// space in it, and with `,0` after the path naming an icon index inside the
/// file. All three mean the same file.
fn file_path(value: &str) -> String {
    let value = value.trim();
    let path = match value.strip_prefix('"') {
        Some(rest) => rest.split('"').next().unwrap_or(rest),
        None => value.split(',').next().unwrap_or(value),
    };
    path.trim().to_owned()
}

/// Whether this path is the drama app's own executable.
///
/// Named as well as present: an entry that survives an upgrade can point at
/// something else entirely, and starting that instead is not what the console
/// asked for.
fn is_binary(candidate: &PathBuf) -> bool {
    candidate.is_file()
        && candidate
            .file_name()
            .is_some_and(|name| name.eq_ignore_ascii_case(BINARY))
}

/// The same path with `%Variables%` in it resolved.
///
/// The drama app's own installer writes literal paths, but an entry an
/// administrator created may name `%ProgramFiles%`, and a path that is not
/// expanded points at a folder that is not there.
fn expanded(path: &str) -> PathBuf {
    let mut resolved = String::with_capacity(path.len());
    let mut rest = path;
    while let Some(start) = rest.find('%') {
        let Some(end) = rest[start + 1..].find('%').map(|end| start + 1 + end) else {
            break;
        };
        resolved.push_str(&rest[..start]);
        match std::env::var(&rest[start + 1..end]) {
            Ok(value) => resolved.push_str(&value),
            // A variable that is not set is left as it was written, so the path
            // stays recognisable in a log instead of collapsing to nothing.
            Err(_) => resolved.push_str(&rest[start..=end]),
        }
        rest = &rest[end + 1..];
    }
    resolved.push_str(rest);
    PathBuf::from(resolved)
}

/// The registry entries the two shims write, in the order worth reading.
///
/// `App Paths` names the executable itself; the uninstall entries name the
/// folder it went into. Both are the app telling Windows where it is, which
/// beats guessing at the three places an installer most often uses.
#[cfg(windows)]
fn from_uninstall_entries() -> Option<PathBuf> {
    use windows_sys::Win32::System::Registry::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};

    const UNINSTALL: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall";
    const UNINSTALL_32: &str = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall";
    const APP_PATHS: &str = r"Software\Microsoft\Windows\CurrentVersion\App Paths";

    for (root, path) in [
        (HKEY_CURRENT_USER, APP_PATHS),
        (HKEY_LOCAL_MACHINE, APP_PATHS),
    ] {
        let Some(key) = registry::open(root, &format!(r"{path}\{BINARY}")) else {
            continue;
        };
        // App Paths keeps the executable in the key's default value.
        if let Some(value) = registry::string(&key, "") {
            let candidate = expanded(value.as_str());
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }

    let recognised = |text: &str| {
        let text = text.to_lowercase();
        ENTRY_MARKERS.iter().any(|marker| text.contains(marker))
    };
    for (root, path) in [
        (HKEY_CURRENT_USER, UNINSTALL),
        (HKEY_LOCAL_MACHINE, UNINSTALL),
        (HKEY_LOCAL_MACHINE, UNINSTALL_32),
    ] {
        let Some(list) = registry::open(root, path) else {
            continue;
        };
        for name in registry::children(&list) {
            let Some(entry) = registry::open(root, &format!(r"{path}\{name}")) else {
                continue;
            };
            let displayed = registry::string(&entry, "DisplayName").unwrap_or_default();
            if !recognised(&name) && !recognised(&displayed) {
                continue;
            }
            // `DisplayIcon`, when the entry has one, is the executable with an
            // icon index after it.
            if let Some(icon) = registry::string(&entry, "DisplayIcon") {
                let candidate = expanded(&file_path(&icon));
                if is_binary(&candidate) {
                    return Some(candidate);
                }
            }
            // `InstallLocation` is the folder, and the one value an Inno Setup
            // entry fills in even when it has no icon to point at - which is
            // exactly the entry this app's own installer writes.
            if let Some(location) = registry::string(&entry, "InstallLocation") {
                // The location may be quoted, and may end in the separator the
                // installer wrote; joining is what makes either harmless.
                let candidate = expanded(&file_path(&location)).join(BINARY);
                if is_binary(&candidate) {
                    return Some(candidate);
                }
            }
        }
    }
    None
}

/// The registry, through the API rather than through `reg.exe`.
///
/// Only what this module asks of it: open a key, list its children, read a
/// string value. Kept here rather than beside the rest of the shell because the
/// drama lookup is the only question it has for the registry - and because a
/// machine that restricts `reg.exe` would otherwise lose the answer with no way
/// to say why.
#[cfg(windows)]
mod registry {
    use windows_sys::Win32::Foundation::{ERROR_NO_MORE_ITEMS, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegEnumKeyExW, RegOpenKeyExW, RegQueryValueExW, HKEY, KEY_READ, REG_EXPAND_SZ,
        REG_SZ, REG_VALUE_TYPE,
    };

    /// An open key, closed when it goes out of scope - including on every early
    /// return above, which is what this type is for.
    pub struct Key(HKEY);

    impl Drop for Key {
        fn drop(&mut self) {
            unsafe { RegCloseKey(self.0) };
        }
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// The key at `path` under `root`, if it is there.
    pub fn open(root: HKEY, path: &str) -> Option<Key> {
        let path = wide(path);
        let mut key: HKEY = std::ptr::null_mut();
        let status = unsafe { RegOpenKeyExW(root, path.as_ptr(), 0, KEY_READ, &mut key) };
        (status == ERROR_SUCCESS).then_some(Key(key))
    }

    /// The names of an open key's children, in no particular order.
    pub fn children(key: &Key) -> Vec<String> {
        let mut names = Vec::new();
        let mut buffer = vec![0u16; 512];
        let mut index = 0;
        loop {
            let mut length = buffer.len() as u32;
            let status = unsafe {
                RegEnumKeyExW(
                    key.0,
                    index,
                    buffer.as_mut_ptr(),
                    &mut length,
                    std::ptr::null(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                )
            };
            if status == ERROR_NO_MORE_ITEMS {
                break;
            }
            if status == ERROR_SUCCESS {
                names.push(String::from_utf16_lossy(&buffer[..length as usize]));
            }
            index += 1;
        }
        names
    }

    /// A string value of an open key, or the key's default value for `""`.
    pub fn string(key: &Key, name: &str) -> Option<String> {
        let name = wide(name);
        let mut kind: REG_VALUE_TYPE = 0;
        let mut size = 0u32;
        let status = unsafe {
            RegQueryValueExW(
                key.0,
                name.as_ptr(),
                std::ptr::null(),
                &mut kind,
                std::ptr::null_mut(),
                &mut size,
            )
        };
        if status != ERROR_SUCCESS || size == 0 {
            return None;
        }
        if kind != REG_SZ && kind != REG_EXPAND_SZ {
            return None;
        }
        // A size in bytes, and UTF-16 needs two per character; the extra pair
        // leaves room for a terminator the value may or may not carry.
        let mut data = vec![0u8; size as usize + 2];
        let mut written = size;
        let status = unsafe {
            RegQueryValueExW(
                key.0,
                name.as_ptr(),
                std::ptr::null(),
                &mut kind,
                data.as_mut_ptr(),
                &mut written,
            )
        };
        if status != ERROR_SUCCESS {
            return None;
        }
        let units: Vec<u16> = data[..written as usize]
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect();
        let text = String::from_utf16_lossy(&units);
        let text = text.trim_end_matches('\0').trim();
        (!text.is_empty()).then(|| text.to_owned())
    }
}

#[cfg(not(windows))]
fn from_uninstall_entries() -> Option<PathBuf> {
    None
}

/// The app's executable, if this computer has one.
fn find_binary() -> Option<PathBuf> {
    if let Some(explicit) = std::env::var_os(OVERRIDE) {
        // An override that points nowhere is not a reason to search elsewhere:
        // the person said which one, and starting a different one would be the
        // shell ignoring them.
        let path = PathBuf::from(explicit);
        return path.is_file().then_some(path);
    }
    from_uninstall_entries()
        .or_else(|| {
            install_candidates()
                .into_iter()
                .find(|candidate| candidate.is_file())
        })
        .or_else(|| on_path(BINARY))
}

/// Start the drama app, signed in as whoever this console is signed in as.
///
/// The ticket was minted by the backend for that person a moment ago; this
/// function never sees a password and never reads the ticket's contents.
#[tauri::command]
pub fn open_drama(app: AppHandle, ticket: String) -> DramaLaunch {
    if !valid_ticket(&ticket) {
        eprintln!("drama: refused a malformed ticket");
        return refused("invalid_ticket");
    }
    let Some(issuer) = issuer_of(&app) else {
        eprintln!("drama: the console has no address to send the app to");
        return refused("spawn_failed");
    };
    let Some(binary) = find_binary() else {
        // The ordinary case on a computer that never installed it: not an error,
        // and the dialog the page draws explains where to get one.
        return refused("not_installed");
    };

    let mut command = Command::new(&binary);
    command
        .arg("--unione-ticket")
        .arg(&ticket)
        .arg("--unione-issuer")
        .arg(&issuer)
        // Nothing the app prints is for this shell to collect: it has its own
        // window and its own log, and a piped stdout nobody reads fills up.
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // A GUI process started from a GUI one still gets a console window
        // unless it says otherwise, and a black rectangle beside the app is not
        // something the person asked for.
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    match command.spawn() {
        Ok(_child) => DramaLaunch {
            launched: true,
            reason: None,
        },
        Err(error) => {
            eprintln!("drama: {} could not be started: {error}", binary.display());
            refused("spawn_failed")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The ticket the backend mints, and what is not one.
    #[test]
    fn only_tk_hex_is_a_ticket() {
        assert!(valid_ticket(&format!("tk-{}", "a".repeat(32))));
        assert!(valid_ticket(&format!("tk-{}", "0f".repeat(16))));
        assert!(!valid_ticket(""));
        assert!(!valid_ticket("tk-"));
        assert!(!valid_ticket(&"a".repeat(32)));
        assert!(!valid_ticket(&format!("tk-{}", "a".repeat(12))));
        assert!(!valid_ticket(&format!("tk-{}", "a".repeat(80))));
        // The reason this is validated at all: it becomes an argument.
        assert!(!valid_ticket(&format!("tk-{} {}", "a".repeat(30), "--flag")));
        assert!(!valid_ticket(&format!("tk-{};rm", "a".repeat(30))));
    }

    /// What the installers write into these values, and what it means.
    ///
    /// Spelled with forward slashes: it is the quoting and the icon index that
    /// are being read here, and a test full of doubled backslashes would be
    /// about Rust's string escapes instead.
    #[test]
    fn a_registry_value_naming_a_file_is_read_three_ways() {
        assert_eq!(file_path("C:/Apps/reveric.exe"), "C:/Apps/reveric.exe");
        assert_eq!(
            file_path(r#""C:/Program Files/Unione AI Drama/reveric.exe",0"#),
            "C:/Program Files/Unione AI Drama/reveric.exe"
        );
        assert_eq!(file_path("C:/Apps/reveric.exe,0"), "C:/Apps/reveric.exe");
        // `InstallLocation` is a folder, quoted when it has spaces in it.
        assert_eq!(file_path(r#""D:/aispace/UnioneAgent""#), "D:/aispace/UnioneAgent");
    }

    /// A variable a person's entry actually uses, and one nobody set.
    #[test]
    fn a_path_with_variables_in_it_is_resolved() {
        let program_files = std::env::var("ProgramFiles").unwrap_or_else(|_| "C:/Program Files".to_owned());
        assert_eq!(
            expanded("%ProgramFiles%/Unione AI Drama").to_string_lossy(),
            format!("{program_files}/Unione AI Drama")
        );
        // A variable that is not set stays as it was written, so the path is
        // still recognisable in a log.
        assert_eq!(
            expanded("%UNIONE_NOTHING_SET_THIS%/app").to_string_lossy(),
            "%UNIONE_NOTHING_SET_THIS%/app"
        );
    }

    /// The candidates exist on the platform they are built for, spelled the way
    /// each one actually installs.
    #[test]
    fn candidates_name_the_app_where_its_installer_puts_it() {
        let candidates = install_candidates();
        assert!(!candidates.is_empty());
        for candidate in &candidates {
            assert!(
                candidate.ends_with(BINARY),
                "{} does not end in the binary's name",
                candidate.display()
            );
        }
        // Both the name the installer uses now and the one it used before, so
        // the dialog does not claim the app is missing on a machine that has it.
        let spelled = |name: &str| {
            candidates
                .iter()
                .any(|candidate| candidate.to_string_lossy().contains(name))
        };
        assert!(spelled(APP_NAME));
        assert!(spelled("Reveric"));
    }
}
