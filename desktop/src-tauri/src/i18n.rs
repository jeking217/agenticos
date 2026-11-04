//! The language the shell's own menus speak.
//!
//! The console is a server-rendered application with its own catalogs and a
//! locale in the URL; the shell's menus are native, built here in Rust, and
//! cannot borrow that machinery. They carry this small catalog instead: the
//! tray's menu, which is also blobby's right-click menu, the macOS menu bar, and
//! the tooltip on the tray icon.
//!
//! The language is the system's - the same signal `ui/i18n.js` reads when the
//! shell paints its own page before any server answers. Nothing here is a
//! setting, so a person who reads Chinese on a Chinese Windows gets these menus
//! in Chinese without going looking for a switcher. Catalogs exist for the three
//! languages that first screen speaks; every other system language falls back to
//! English, which is what these menus said until now.

use std::sync::OnceLock;

use crate::blobby;

/// A language this shell's menus can speak.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Locale {
    En,
    ZhCn,
    ZhTw,
}

impl Locale {
    /// The locale a language tag names.
    ///
    /// Tags arrive as `zh-CN` from a Windows locale name or as `zh_TW` from
    /// AppleLocale, so both separators are accepted. Region first, then script:
    /// `zh-TW`, `zh-HK`, `zh-MO` and `zh-Hant-CN` read Traditional, every other
    /// Chinese reads Simplified.
    pub fn from_tag(tag: &str) -> Self {
        let tag = tag.trim().to_ascii_lowercase().replace('_', "-");
        if !tag.starts_with("zh") {
            return Self::En;
        }
        let traditional = tag.contains("hant")
            || ["zh-tw", "zh-hk", "zh-mo"]
                .iter()
                .any(|region| tag == *region || tag.starts_with(&format!("{region}-")));
        if traditional {
            Self::ZhTw
        } else {
            Self::ZhCn
        }
    }

    /// The system's language, as the shell sees it at startup.
    fn system() -> Self {
        Self::from_tag(system_tag().as_deref().unwrap_or_default())
    }
}

/// Every label the shell's native menus draw.
///
/// One struct per language, so a field added for a new menu item fails to
/// compile in the other catalogs instead of silently falling back to English.
pub struct Strings {
    pub open_console: &'static str,
    pub new_chat: &'static str,
    pub new_chat_with: &'static str,
    pub quick_ask: &'static str,
    pub screenshot: &'static str,
    pub settings: &'static str,
    pub change_server: &'static str,
    pub local_group: &'static str,
    pub local_start: &'static str,
    pub local_stop: &'static str,
    pub local_update: &'static str,
    pub check_updates: &'static str,
    pub quit: &'static str,
    pub about: &'static str,
    pub blobby: &'static str,
    pub blobby_show: &'static str,
    pub blobby_size: &'static str,
    pub blobby_small: &'static str,
    pub blobby_medium: &'static str,
    pub blobby_large: &'static str,
    pub blobby_customize: &'static str,
    pub activity: &'static str,
    pub activity_open: &'static str,
    pub activity_stop: &'static str,
    pub activity_ready: &'static str,
    pub activity_screen: &'static str,
    pub revoke_access: &'static str,
    pub revoke_folder: &'static str,
    /// The grant's owner, before the conversation it belongs to: `Whole computer · 3f9a1c2b`.
    pub whole_computer_name: &'static str,
    /// A whole-computer grant that no conversation has claimed yet.
    pub whole_computer_new: &'static str,
    pub tooltip_working: &'static str,
    pub menu_file: &'static str,
    pub menu_edit: &'static str,
    pub menu_view: &'static str,
    pub menu_go: &'static str,
    pub menu_window: &'static str,
    pub menu_help: &'static str,
    pub reload: &'static str,
    pub zoom: &'static str,
    pub back: &'static str,
    pub forward: &'static str,
    pub documentation: &'static str,
    pub go_dashboard: &'static str,
    pub go_chat: &'static str,
    pub go_agents: &'static str,
    pub go_activity: &'static str,
    pub go_knowledge: &'static str,
}

impl Strings {
    pub fn of(locale: Locale) -> &'static Self {
        match locale {
            Locale::En => &EN,
            Locale::ZhCn => &ZH_CN,
            Locale::ZhTw => &ZH_TW,
        }
    }

    /// The label of a whole-computer grant: its owner and, when a conversation
    /// has claimed it, the short id of that conversation.
    pub fn whole_computer(&self, conversation: Option<&str>) -> String {
        match conversation {
            Some(id) => format!("{} · {id}", self.whole_computer_name),
            None => self.whole_computer_new.to_owned(),
        }
    }
}

const EN: Strings = Strings {
    open_console: "Open UnioneAgent",
    new_chat: "New Chat",
    new_chat_with: "New Chat With",
    quick_ask: "Quick Ask",
    screenshot: "Screenshot to New Chat",
    settings: "Settings…",
    change_server: "Change Server…",
    local_group: "UnioneAgent on This Computer",
    local_start: "Start",
    local_stop: "Stop",
    local_update: "Update",
    check_updates: "Check for Updates…",
    quit: "Quit UnioneAgent",
    about: "About UnioneAgent",
    blobby: "Blobby",
    blobby_show: "Show blobby",
    blobby_size: "Size",
    blobby_small: "Small",
    blobby_medium: "Medium",
    blobby_large: "Large",
    blobby_customize: "Customize blobby…",
    activity: "Activity & Access",
    activity_open: "Open",
    activity_stop: "Stop",
    activity_ready: "App readiness…",
    activity_screen: "Take back screen control",
    revoke_access: "Revoke access",
    revoke_folder: "Revoke folder access",
    whole_computer_name: "Whole computer",
    whole_computer_new: "Whole computer · new chat",
    tooltip_working: "UnioneAgent - an agent is working on this computer",
    menu_file: "File",
    menu_edit: "Edit",
    menu_view: "View",
    menu_go: "Go",
    menu_window: "Window",
    menu_help: "Help",
    reload: "Reload",
    zoom: "Zoom",
    back: "Back",
    forward: "Forward",
    documentation: "UnioneAgent Documentation",
    go_dashboard: "Dashboard",
    go_chat: "Chat",
    go_agents: "Agents",
    go_activity: "Activity",
    go_knowledge: "Knowledge",
};

const ZH_CN: Strings = Strings {
    open_console: "打开 UnioneAgent",
    new_chat: "新建对话",
    new_chat_with: "新建对话（指定智能体）",
    quick_ask: "快速提问",
    screenshot: "截图并新建对话",
    settings: "设置…",
    change_server: "更换服务器…",
    local_group: "本机 UnioneAgent",
    local_start: "启动",
    local_stop: "停止",
    local_update: "更新",
    check_updates: "检查更新…",
    quit: "退出 UnioneAgent",
    about: "关于 UnioneAgent",
    blobby: "桌面宠物",
    blobby_show: "显示桌面宠物",
    blobby_size: "大小",
    blobby_small: "小",
    blobby_medium: "中",
    blobby_large: "大",
    blobby_customize: "自定义桌面宠物…",
    activity: "活动与权限",
    activity_open: "打开",
    activity_stop: "停止",
    activity_ready: "应用就绪状态…",
    activity_screen: "收回屏幕控制",
    revoke_access: "收回访问权限",
    revoke_folder: "收回文件夹访问权限",
    whole_computer_name: "整台电脑",
    whole_computer_new: "整台电脑 · 新对话",
    tooltip_working: "UnioneAgent · 有智能体正在这台电脑上工作",
    menu_file: "文件",
    menu_edit: "编辑",
    menu_view: "显示",
    menu_go: "前往",
    menu_window: "窗口",
    menu_help: "帮助",
    reload: "重新载入",
    zoom: "缩放",
    back: "后退",
    forward: "前进",
    documentation: "UnioneAgent 文档",
    go_dashboard: "仪表盘",
    go_chat: "对话",
    go_agents: "智能体",
    go_activity: "活动",
    go_knowledge: "知识库",
};

const ZH_TW: Strings = Strings {
    open_console: "開啟 UnioneAgent",
    new_chat: "新增對話",
    new_chat_with: "新增對話（指定智慧體）",
    quick_ask: "快速提問",
    screenshot: "截圖並新增對話",
    settings: "設定…",
    change_server: "更換伺服器…",
    local_group: "本機 UnioneAgent",
    local_start: "啟動",
    local_stop: "停止",
    local_update: "更新",
    check_updates: "檢查更新…",
    quit: "結束 UnioneAgent",
    about: "關於 UnioneAgent",
    blobby: "桌面寵物",
    blobby_show: "顯示桌面寵物",
    blobby_size: "大小",
    blobby_small: "小",
    blobby_medium: "中",
    blobby_large: "大",
    blobby_customize: "自訂桌面寵物…",
    activity: "活動與權限",
    activity_open: "開啟",
    activity_stop: "停止",
    activity_ready: "應用程式就緒狀態…",
    activity_screen: "收回螢幕控制",
    revoke_access: "收回存取權限",
    revoke_folder: "收回資料夾存取權限",
    whole_computer_name: "整台電腦",
    whole_computer_new: "整台電腦 · 新對話",
    tooltip_working: "UnioneAgent · 有智慧體正在這台電腦上工作",
    menu_file: "檔案",
    menu_edit: "編輯",
    menu_view: "顯示方式",
    menu_go: "前往",
    menu_window: "視窗",
    menu_help: "輔助說明",
    reload: "重新載入",
    zoom: "縮放",
    back: "上一頁",
    forward: "下一頁",
    documentation: "UnioneAgent 說明文件",
    go_dashboard: "儀表板",
    go_chat: "對話",
    go_agents: "智慧體",
    go_activity: "活動",
    go_knowledge: "知識庫",
};

/// The locale the menus are built in. Settled once, from the system, when the
/// first menu asks for it: nothing in the shell changes it while it runs.
static LOCALE: OnceLock<Locale> = OnceLock::new();

pub fn locale() -> Locale {
    *LOCALE.get_or_init(Locale::system)
}

/// The catalog the native menus are built from.
pub fn strings() -> &'static Strings {
    Strings::of(locale())
}

/// The label of a size in blobby's menu, by its menu id.
pub fn blobby_size_label(id: &str) -> Option<&'static str> {
    let strings = strings();
    match id {
        blobby::SMALL_SIZE => Some(strings.blobby_small),
        blobby::MEDIUM_SIZE => Some(strings.blobby_medium),
        blobby::LARGE_SIZE => Some(strings.blobby_large),
        _ => None,
    }
}

/// The label of a destination under Go, by its menu id.
pub fn go_label(id: &str) -> Option<&'static str> {
    let strings = strings();
    Some(match id {
        "go-dashboard" => strings.go_dashboard,
        "go-chat" => strings.go_chat,
        "go-agents" => strings.go_agents,
        "go-runs" => strings.go_activity,
        "go-knowledge" => strings.go_knowledge,
        _ => return None,
    })
}

/// The system's language as a tag, or `None` where the platform has no answer.
fn system_tag() -> Option<String> {
    #[cfg(windows)]
    {
        windows_tag()
    }
    #[cfg(target_os = "macos")]
    {
        macos_tag()
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        posix_tag()
    }
}

/// `GetUserDefaultLocaleName` - the locale the person chose in Windows'
/// settings, which is what the shell's own page reads as `navigator.language`.
#[cfg(windows)]
fn windows_tag() -> Option<String> {
    use windows_sys::Win32::Globalization::GetUserDefaultLocaleName;

    // LOCALE_NAME_MAX_LENGTH, and the count includes the terminator.
    let mut buffer = [0u16; 85];
    let length = unsafe { GetUserDefaultLocaleName(buffer.as_mut_ptr(), buffer.len() as i32) };
    if length <= 1 {
        return None;
    }
    String::from_utf16(&buffer[..length as usize - 1]).ok()
}

/// AppleLocale first, which is the person's own choice; then the POSIX
/// variables a launch from a terminal inherits. `defaults` is the one stable
/// reader of that preference - a GUI launch has no `LANG`.
#[cfg(target_os = "macos")]
fn macos_tag() -> Option<String> {
    if let Some(tag) = posix_tag() {
        return Some(tag);
    }
    let output = std::process::Command::new("/usr/bin/defaults")
        .args(["read", "-g", "AppleLocale"])
        .output()
        .ok()?;
    let tag = String::from_utf8(output.stdout).ok()?.trim().to_owned();
    (!tag.is_empty()).then_some(tag)
}

#[cfg(not(windows))]
fn posix_tag() -> Option<String> {
    ["LC_ALL", "LC_MESSAGES", "LANG"]
        .iter()
        .find_map(|name| std::env::var(name).ok())
        .map(|tag| tag.split(['.', '@']).next().unwrap_or_default().to_owned())
        .filter(|tag| !tag.is_empty() && tag != "C" && tag != "POSIX")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_simplified_and_traditional_tags() {
        assert_eq!(Locale::from_tag("zh-CN"), Locale::ZhCn);
        assert_eq!(Locale::from_tag("zh-Hans-CN"), Locale::ZhCn);
        assert_eq!(Locale::from_tag("zh_SG"), Locale::ZhCn);
        assert_eq!(Locale::from_tag("zh-TW"), Locale::ZhTw);
        assert_eq!(Locale::from_tag("zh_HK"), Locale::ZhTw);
        assert_eq!(Locale::from_tag("zh-Hant-CN"), Locale::ZhTw);
        assert_eq!(Locale::from_tag("en-US"), Locale::En);
        assert_eq!(Locale::from_tag("de"), Locale::En);
        assert_eq!(Locale::from_tag(""), Locale::En);
    }

    #[test]
    fn chinese_catalogs_are_translated() {
        let en = Strings::of(Locale::En);
        for (locale, strings) in [(Locale::ZhCn, Strings::of(Locale::ZhCn)), (Locale::ZhTw, Strings::of(Locale::ZhTw))] {
            assert_ne!(strings.blobby, en.blobby, "{locale:?} keeps English");
            assert_ne!(strings.activity, en.activity, "{locale:?} keeps English");
            assert_ne!(strings.quit, en.quit, "{locale:?} keeps English");
        }
        assert_eq!(Strings::of(Locale::ZhCn).whole_computer(Some("3f9a1c2b")), "整台电脑 · 3f9a1c2b");
        assert_eq!(Strings::of(Locale::ZhCn).whole_computer(None), "整台电脑 · 新对话");
    }

    #[test]
    fn every_menu_id_has_a_label() {
        for id in ["go-dashboard", "go-chat", "go-agents", "go-runs", "go-knowledge"] {
            assert!(go_label(id).is_some(), "{id} has no label");
        }
        assert!(go_label("go-unknown").is_none());
        assert!(blobby_size_label("blobby-size-small").is_some());
        assert!(blobby_size_label("blobby-size-unknown").is_none());
    }
}
