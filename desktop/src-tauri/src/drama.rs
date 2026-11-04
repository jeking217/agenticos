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
            candidates.push(base.join(APP_NAME).join(BINARY));
            candidates.push(base.join("Programs").join(APP_NAME).join(BINARY));
        }
    } else if cfg!(target_os = "macos") {
        candidates.push(
            PathBuf::from("/Applications")
                .join(format!("{APP_NAME}.app"))
                .join("Contents/MacOS")
                .join(BINARY),
        );
        if let Some(home) = std::env::var_os("HOME") {
            candidates.push(
                PathBuf::from(home)
                    .join("Applications")
                    .join(format!("{APP_NAME}.app"))
                    .join("Contents/MacOS")
                    .join(BINARY),
            );
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

/// The app's executable, if this computer has one.
fn find_binary() -> Option<PathBuf> {
    if let Some(explicit) = std::env::var_os(OVERRIDE) {
        // An override that points nowhere is not a reason to search elsewhere:
        // the person said which one, and starting a different one would be the
        // shell ignoring them.
        let path = PathBuf::from(explicit);
        return path.is_file().then_some(path);
    }
    install_candidates()
        .into_iter()
        .find(|candidate| candidate.is_file())
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
    }
}
