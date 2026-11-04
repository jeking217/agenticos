/**
 * The onboarding's own two languages.
 *
 * The console is a server-rendered app with five catalogs and a locale in the
 * URL; this page is a static file that the shell paints before any server
 * answers, so it cannot borrow that machinery. It carries the strings that
 * change here - Simplified and Traditional Chinese - and falls back to the
 * English written into `index.html`.
 *
 * The language is the system's, not a stored preference: this page exists to be
 * finished once, and a person who reads Chinese on a Chinese Windows should not
 * have to find a switcher to read the first screen they see.
 */

/** The locale this page speaks: `zh-CN`, `zh-TW`, or `en`. */
export function systemLocale() {
  const tags = navigator.languages?.length
    ? navigator.languages
    : [navigator.language || "en"];
  for (const tag of tags) {
    const lower = String(tag).toLowerCase();
    if (lower.startsWith("zh")) {
      // Region first, then script: `zh-TW`, `zh-HK`, `zh-Hant-CN` all read
      // Traditional; anything else Chinese reads Simplified.
      return /-(tw|hk|mo)\b/.test(lower) || lower.includes("hant")
        ? "zh-TW"
        : "zh-CN";
    }
  }
  return "en";
}

/**
 * Only what the scripts ask for. The markup's English lives in `index.html`,
 * where a translator can read it next to the element it belongs to - copying all
 * of it here would be a second catalog to keep in step.
 */
const en = {
  "welcome.title": "Welcome to UnioneAgent",

  "local.heading": "UnioneAgent on this computer",
  "local.lead": "A local workspace, managed from your menu bar.",
  "local.checking.title": "Checking this computer",
  "local.checking.message": "Looking for UnioneAgent and Docker…",
  "local.btn.use": "Connect and continue",

  "js.connecting": "Connecting…",
  "js.connect": "Connect",
  "js.docker.downloading": "Downloading Docker Desktop…",
  "js.docker.downloadingPct": "Downloading Docker Desktop… {pct}%",
  "js.docker.downloadingMb": "Downloading Docker Desktop… {mb} MB",
  "js.local.finishHeading": "Finish setting up your instance",
  "js.local.createHeading": "Create a local instance",
  "js.local.createLead": "Your own workspace, running on this computer.",
  "js.instance.unfinished": "Setup unfinished",
  "js.instance.running": "Running",
  "js.instance.stopped": "Stopped",
  "js.instance.finish": "Finish setup",
  "js.instance.startConnect": "Start and connect",
  "js.instance.retry": "Retry connection",
  "js.check.failedTitle": "Could not check this computer",
  "js.docker.finishInstall":
    "Finish installing Docker Desktop, then open it and check again.",
  "js.docker.finishSetup": "Finish Docker Desktop's setup, then check again.",
  "js.perm.allowed": "Allowed",
  "js.perm.notEnabled": "Not enabled",
  "js.perm.open": "Open settings",
  "js.unreadableInstances": "The app could not read its local instances.",
  "js.unreadableStatus": "The app could not read this computer's status.",

  "view.no_docker.title": "Install Docker to get started",
  "view.no_docker.message":
    "Open Docker Desktop after installing it, then check again.",
  "view.docker_stopped.title": "Docker is ready to open",
  "view.docker_stopped.message":
    "Start Docker Desktop, then check again to set up your workspace.",
  "view.running.title": "Your local workspace is ready",
  "view.running.message":
    "UnioneAgent is running on this computer. Connect to sign in and finish setting up desktop features.",
  "view.ready.title": "Ready to create your workspace",
  "view.ready.message":
    "Create an instance for your organization and choose its owner account.",
};

const zhCN = {
  "welcome.title": "欢迎使用 UnioneAgent",
  "welcome.lead": "你的智能体触手可及：桌面、菜单栏、快捷方式。",
  "welcome.start": "开始使用",

  "perm.title": "让这台电脑准备就绪",
  "perm.lead": "桌面功能需要一些可选权限。可以跳过，之后在系统设置里再开启。",
  "perm.screen.title": "屏幕录制",
  "perm.screen.desc": "把选中的截图附到对话里。安装过程不会截取任何内容。",
  "perm.access.title": "辅助功能",
  "perm.access.desc":
    "在「快速提问」里使用选中的文字，并允许已授权的智能体操作鼠标和键盘。",
  "perm.checking": "检查中…",
  "perm.open": "打开设置",
  "perm.hint":
    "macOS 在开启屏幕录制后可能需要重启应用。智能体使用你的屏幕前仍会征求同意。麦克风与文件夹权限会在你使用相应功能时再请求。",
  "perm.back": "返回",
  "perm.continue": "继续",

  "where.title": "UnioneAgent 放在哪里？",
  "where.lead": "之后可以在菜单里修改。",
  "where.local.title": "这台电脑",
  "where.local.hint": "在 Docker 里本机运行，使用你自己的文件和设置。",
  "where.server.title": "我所在组织的服务器",
  "where.server.hint": "连接到公司运行的 UnioneAgent，用工作账号登录。",

  "server.title": "你所在组织的服务器",
  "server.lead": "你在浏览器里打开 UnioneAgent 的那个地址。",
  "server.label": "服务器地址",
  "server.placeholder": "agenticos.example.com",
  "server.back": "返回",
  "server.connect": "连接",

  "local.heading": "这台电脑上的 UnioneAgent",
  "local.lead": "由菜单栏管理的本地工作区。",
  "local.checking.title": "正在检查这台电脑",
  "local.checking.message": "正在查找 UnioneAgent 与 Docker…",
  "local.btn.docker": "下载 Docker Desktop",
  "local.btn.openDocker": "打开 Docker Desktop",
  "local.btn.check": "重新检查",
  "local.btn.create": "新建实例",
  "local.btn.use": "连接并继续",

  "form.workspace": "工作区",
  "form.instanceName": "实例名称",
  "form.instanceNamePlaceholder": "工作或私人",
  "form.organization": "组织",
  "form.orgPlaceholder": "Acme",
  "form.owner": "所有者账号",
  "form.email": "你的邮箱",
  "form.emailPlaceholder": "you@company.com",
  "form.password": "密码",
  "form.passwordPlaceholder": "至少 8 个字符",
  "form.aiProvider": "AI 提供商",
  "form.provider": "提供商",
  "form.apiKey": "API 密钥",
  "form.optional": "可选",
  "form.apiKeyPlaceholder": "现在添加或稍后添加",
  "form.hint": "智能体回复需要密钥。也可以稍后在「AI 提供商」里添加。",
  "form.note": "数据与登录分离。密钥存放在这个实例的密钥库中。",
  "form.cancel": "取消",
  "form.create": "创建实例",

  "progress.title": "正在设置 UnioneAgent",
  "progress.lead": "第一次下载需要几分钟。这个窗口可以一直开着。",
  "progress.files": "准备中",
  "progress.download": "下载 UnioneAgent",
  "progress.start": "启动中",
  "progress.owner": "创建你的组织",
  "progress.back": "返回",

  "js.connecting": "连接中…",
  "js.connect": "连接",
  "js.docker.downloading": "正在下载 Docker Desktop…",
  "js.docker.downloadingPct": "正在下载 Docker Desktop… {pct}%",
  "js.docker.downloadingMb": "正在下载 Docker Desktop… {mb} MB",
  "js.local.finishHeading": "完成实例设置",
  "js.local.createHeading": "创建本地实例",
  "js.local.createLead": "你自己的、运行在这台电脑上的工作区。",
  "js.instance.unfinished": "设置未完成",
  "js.instance.running": "运行中",
  "js.instance.stopped": "已停止",
  "js.instance.finish": "完成设置",
  "js.instance.startConnect": "启动并连接",
  "js.instance.retry": "重新连接",
  "js.check.failedTitle": "无法检查这台电脑",
  "js.docker.finishInstall": "完成 Docker Desktop 安装后打开它，再重新检查。",
  "js.docker.finishSetup": "完成 Docker Desktop 的初始化设置，再重新检查。",
  "js.perm.allowed": "已允许",
  "js.perm.notEnabled": "未开启",
  "js.perm.open": "打开设置",
  "js.unreadableInstances": "应用无法读取本地实例。",
  "js.unreadableStatus": "应用无法读取这台电脑的状态。",

  "view.no_docker.title": "先安装 Docker",
  "view.no_docker.message": "安装后打开 Docker Desktop，再重新检查。",
  "view.docker_stopped.title": "Docker 已就绪，等待打开",
  "view.docker_stopped.message": "启动 Docker Desktop，再重新检查以创建工作区。",
  "view.running.title": "本地工作区已就绪",
  "view.running.message":
    "UnioneAgent 正在这台电脑上运行。连接后即可登录并完成桌面功能设置。",
  "view.ready.title": "可以创建工作区了",
  "view.ready.message": "为你的组织创建实例，并选择它的所有者账号。",
};

const zhTW = {
  "welcome.title": "歡迎使用 UnioneAgent",
  "welcome.lead": "你的智能體隨手可得：桌面、選單列、捷徑。",
  "welcome.start": "開始使用",

  "perm.title": "讓這台電腦準備就緒",
  "perm.lead": "桌面功能需要一些選用權限。可以略過，之後在系統設定裡再開啟。",
  "perm.screen.title": "螢幕錄製",
  "perm.screen.desc": "把選取的截圖附到對話裡。安裝過程不會擷取任何內容。",
  "perm.access.title": "輔助使用",
  "perm.access.desc":
    "在「快速提問」裡使用選取的文字，並允許已授權的智能體操作滑鼠和鍵盤。",
  "perm.checking": "檢查中…",
  "perm.open": "開啟設定",
  "perm.hint":
    "macOS 在開啟螢幕錄製後可能需要重新啟動應用程式。智能體使用你的螢幕前仍會徵求同意。麥克風與資料夾權限會在你使用相應功能時再請求。",
  "perm.back": "返回",
  "perm.continue": "繼續",

  "where.title": "UnioneAgent 要放在哪裡？",
  "where.lead": "之後可以在選單裡修改。",
  "where.local.title": "在這台電腦",
  "where.local.hint": "在 Docker 裡本機執行，使用你自己的檔案與設定。",
  "where.server.title": "我所在組織的伺服器",
  "where.server.hint": "連線到公司執行的 UnioneAgent，用工作帳號登入。",

  "server.title": "你所在組織的伺服器",
  "server.lead": "你在瀏覽器裡開啟 UnioneAgent 的那個位址。",
  "server.label": "伺服器位址",
  "server.placeholder": "agenticos.example.com",
  "server.back": "返回",
  "server.connect": "連線",

  "local.heading": "這台電腦上的 UnioneAgent",
  "local.lead": "由選單列管理的本機工作區。",
  "local.checking.title": "正在檢查這台電腦",
  "local.checking.message": "正在尋找 UnioneAgent 與 Docker…",
  "local.btn.docker": "下載 Docker Desktop",
  "local.btn.openDocker": "開啟 Docker Desktop",
  "local.btn.check": "重新檢查",
  "local.btn.create": "新增執行個體",
  "local.btn.use": "連線並繼續",

  "form.workspace": "工作區",
  "form.instanceName": "執行個體名稱",
  "form.instanceNamePlaceholder": "工作或私人",
  "form.organization": "組織",
  "form.orgPlaceholder": "Acme",
  "form.owner": "擁有者帳號",
  "form.email": "你的電子郵件",
  "form.emailPlaceholder": "you@company.com",
  "form.password": "密碼",
  "form.passwordPlaceholder": "至少 8 個字元",
  "form.aiProvider": "AI 供應商",
  "form.provider": "供應商",
  "form.apiKey": "API 金鑰",
  "form.optional": "選填",
  "form.apiKeyPlaceholder": "現在新增或稍後新增",
  "form.hint": "智能體回覆需要金鑰。也可以稍後在「AI 供應商」裡新增。",
  "form.note": "資料與登入分離。金鑰存放在這個執行個體的密鑰庫中。",
  "form.cancel": "取消",
  "form.create": "建立執行個體",

  "progress.title": "正在設定 UnioneAgent",
  "progress.lead": "第一次下載需要幾分鐘。這個視窗可以一直開著。",
  "progress.files": "準備中",
  "progress.download": "下載 UnioneAgent",
  "progress.start": "啟動中",
  "progress.owner": "建立你的組織",
  "progress.back": "返回",

  "js.connecting": "連線中…",
  "js.connect": "連線",
  "js.docker.downloading": "正在下載 Docker Desktop…",
  "js.docker.downloadingPct": "正在下載 Docker Desktop… {pct}%",
  "js.docker.downloadingMb": "正在下載 Docker Desktop… {mb} MB",
  "js.local.finishHeading": "完成執行個體設定",
  "js.local.createHeading": "建立本機執行個體",
  "js.local.createLead": "你自己的、執行在這台電腦上的工作區。",
  "js.instance.unfinished": "設定未完成",
  "js.instance.running": "執行中",
  "js.instance.stopped": "已停止",
  "js.instance.finish": "完成設定",
  "js.instance.startConnect": "啟動並連線",
  "js.instance.retry": "重新連線",
  "js.check.failedTitle": "無法檢查這台電腦",
  "js.docker.finishInstall": "完成 Docker Desktop 安裝後開啟它，再重新檢查。",
  "js.docker.finishSetup": "完成 Docker Desktop 的初始化設定，再重新檢查。",
  "js.perm.allowed": "已允許",
  "js.perm.notEnabled": "未開啟",
  "js.perm.open": "開啟設定",
  "js.unreadableInstances": "應用程式無法讀取本機執行個體。",
  "js.unreadableStatus": "應用程式無法讀取這台電腦的狀態。",

  "view.no_docker.title": "先安裝 Docker",
  "view.no_docker.message": "安裝後開啟 Docker Desktop，再重新檢查。",
  "view.docker_stopped.title": "Docker 已就緒，等待開啟",
  "view.docker_stopped.message": "啟動 Docker Desktop，再重新檢查以建立工作區。",
  "view.running.title": "本機工作區已就緒",
  "view.running.message":
    "UnioneAgent 正在這台電腦上執行。連線後即可登入並完成桌面功能設定。",
  "view.ready.title": "可以建立工作區了",
  "view.ready.message": "為你的組織建立執行個體，並選擇它的擁有者帳號。",
};

const CATALOGS = { en, "zh-CN": zhCN, "zh-TW": zhTW };

export const locale = systemLocale();

/** One string, in this page's language, with `{name}` slots filled in. */
export function t(key, values) {
  const template = CATALOGS[locale]?.[key] ?? en[key];
  if (template === undefined) return key;
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (_, name) =>
    values[name] === undefined ? `{${name}}` : String(values[name]),
  );
}

/**
 * Translate the markup: `data-i18n` sets the text, `data-i18n-placeholder` and
 * `data-i18n-label` the two attributes worth translating.
 *
 * The English stays in `index.html` as the thing a translator reads and as what
 * shows if a key is missing - not a second copy of every string in JS.
 */
export function translate(root = document) {
  document.documentElement.lang = locale;
  const title = document.querySelector("title");
  if (title && title.dataset.i18n) title.textContent = t(title.dataset.i18n);
  // A key nobody translated leaves the English already in the markup alone, so a
  // missing entry is a page that reads in English, not one that reads `form.x`.
  const text = (key) => (CATALOGS[locale]?.[key] === undefined ? null : t(key));
  for (const element of root.querySelectorAll("[data-i18n]")) {
    const translated = text(element.dataset.i18n);
    if (translated !== null) element.textContent = translated;
  }
  for (const element of root.querySelectorAll("[data-i18n-placeholder]")) {
    const translated = text(element.dataset.i18nPlaceholder);
    if (translated !== null) element.placeholder = translated;
  }
  for (const element of root.querySelectorAll("[data-i18n-label]")) {
    const translated = text(element.dataset.i18nLabel);
    if (translated !== null) element.setAttribute("aria-label", translated);
  }
}
