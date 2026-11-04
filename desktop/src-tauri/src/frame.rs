//! The window's own buttons, drawn by the page (#2331).
//!
//! The console used to sit under the operating system's title bar - a strip of
//! grey above a page that has its own header, its own logo and its own idea of
//! where things go. There is nothing in that strip the page does not already
//! show, so the window is built without it and the three controls that must
//! exist anyway are drawn on top of the page's own header, at the end where
//! Windows puts them.
//!
//! Drawn by a script injected into every page of this window rather than by the
//! console's own React tree, because the console is a remote page: shipping the
//! buttons with the shell means they are there on the sign-in page, on the error
//! page, and on whatever the console will look like after its next deploy - and
//! it means a person on Windows gets the same three buttons as a person on macOS
//! instead of a native frame in one place and traffic lights in the other.
//!
//! What the script may do is bounded by `console::GRANTS`: minimize, maximize,
//! close. No resize, no move, no window enumeration - and nothing at all from a
//! page on an origin the person did not configure.

/// The script that draws the buttons, for a window with this label.
///
/// `label` is baked in rather than read from the page: a page that could choose
/// the window it closes could close another one.
pub fn script(label: &str) -> String {
    let label = serde_json::to_string(label).expect("a window label is a string");
    TITLE_BAR.replace("__LABEL__", &label)
}

/// Buttons are 46x32, the size Windows uses, because a person's hand knows it.
const TITLE_BAR: &str = r##"
(function () {
  if (window.__UNIONE_TITLE_BAR__) return;
  var internals = window.__TAURI_INTERNALS__;
  if (!internals || typeof internals.invoke !== "function") return;
  window.__UNIONE_TITLE_BAR__ = true;

  var label = __LABEL__;
  var BUTTON_WIDTH = 46;
  var BUTTON_HEIGHT = 32;
  var BAR_HEIGHT = 46;

  function call(command, args) {
    return internals.invoke(command, Object.assign({ label: label }, args || {}));
  }

  // 页面的底色说了算：控制台可以自己切深色，和系统的深浅无关。
  function palette() {
    var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    try {
      var background = window.getComputedStyle(document.body).backgroundColor;
      var match = /rgba?\(([^)]+)\)/.exec(background);
      if (match) {
        var parts = match[1].split(",").map(Number);
        var alpha = parts.length > 3 ? parts[3] : 1;
        if (alpha > 0.5) {
          var luma = (0.299 * parts[0] + 0.587 * parts[1] + 0.114 * parts[2]) / 255;
          dark = luma < 0.5;
        }
      }
    } catch (error) {
      // 页面还没上色，就用系统的深浅。
    }
    return dark
      ? { icon: "#f2f2f4", hover: "rgba(255,255,255,0.13)", close: "#e81123", closeIcon: "#ffffff" }
      : { icon: "#202024", hover: "rgba(0,0,0,0.07)", close: "#e81123", closeIcon: "#ffffff" };
  }

  var ICONS = {
    minimize: '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 5h8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/></svg>',
    maximize: '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><rect x="1.4" y="1.4" width="7.2" height="7.2" rx="1.1" fill="none" stroke="currentColor" stroke-width="1.1"/></svg>',
    restore: '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><rect x="1" y="3" width="6" height="6" rx="1" fill="none" stroke="currentColor" stroke-width="1.1"/><path d="M3.4 3V2.1c0-.6.5-1.1 1.1-1.1h3.4c.6 0 1.1.5 1.1 1.1v3.4c0 .6-.5 1.1-1.1 1.1H8" fill="none" stroke="currentColor" stroke-width="1.1"/></svg>',
    close: '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1.6 1.6l6.8 6.8M8.4 1.6l-6.8 6.8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/></svg>'
  };

  var colours = palette();
  var bar = document.createElement("div");
  bar.setAttribute("data-unione-title-bar", "");
  bar.style.cssText = [
    "position:fixed",
    "top:0",
    "right:0",
    "height:" + BAR_HEIGHT + "px",
    "display:flex",
    "align-items:flex-start",
    "justify-content:flex-end",
    "gap:2px",
    "padding:7px 8px 0 8px",
    "box-sizing:border-box",
    "z-index:2147483647",
    "pointer-events:none",
    "-webkit-user-select:none",
    "user-select:none"
  ].join(";");

  function button(name, icon, action, kind) {
    var element = document.createElement("button");
    element.type = "button";
    element.setAttribute("aria-label", name);
    element.title = name;
    element.innerHTML = ICONS[icon];
    element.style.cssText = [
      "pointer-events:auto",
      "display:flex",
      "align-items:center",
      "justify-content:center",
      "width:" + BUTTON_WIDTH + "px",
      "height:" + BUTTON_HEIGHT + "px",
      "padding:0",
      "margin:0",
      "border:0",
      "border-radius:6px",
      "background:transparent",
      "color:" + colours.icon,
      "cursor:default",
      "outline:none",
      "transition:background-color 90ms linear"
    ].join(";");
    element.addEventListener("mouseenter", function () {
      element.style.background = kind === "close" ? colours.close : colours.hover;
      if (kind === "close") element.style.color = colours.closeIcon;
    });
    element.addEventListener("mouseleave", function () {
      element.style.background = "transparent";
      element.style.color = colours.icon;
    });
    element.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      action(element);
    });
    return element;
  }

  var minimize = button("Minimize", "minimize", function () {
    call("plugin:window|minimize");
  });
  var maximize = button("Maximize", "maximize", function () {
    call("plugin:window|toggle_maximize").then(refresh);
  });
  var close = button("Close", "close", function () {
    call("plugin:window|close");
  }, "close");
  bar.appendChild(minimize);
  bar.appendChild(maximize);
  bar.appendChild(close);

  function refresh() {
    call("plugin:window|is_maximized").then(function (maximized) {
      maximize.innerHTML = ICONS[maximized ? "restore" : "maximize"];
      maximize.setAttribute("aria-label", maximized ? "Restore" : "Maximize");
      maximize.title = maximized ? "Restore" : "Maximize";
    });
  }

  function repaint() {
    colours = palette();
    [minimize, maximize, close].forEach(function (element) {
      element.style.color = colours.icon;
      element.style.background = "transparent";
    });
  }

  function attach() {
    if (!document.body) {
      window.requestAnimationFrame(attach);
      return;
    }
    document.body.appendChild(bar);
    refresh();
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", repaint);
    }
    window.addEventListener("focus", refresh);
    window.addEventListener("resize", refresh);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
"##;

#[cfg(test)]
mod tests {
    use super::script;

    #[test]
    fn the_script_names_the_window_it_draws_for() {
        let drawn = script("main");
        assert!(drawn.contains(r#"var label = "main";"#));
        assert!(!drawn.contains("__LABEL__"));
        // A button with a command the shell does not grant is a button that
        // does nothing when pressed - which is worse than not drawing it.
        for command in [
            "plugin:window|minimize",
            "plugin:window|toggle_maximize",
            "plugin:window|close",
            "plugin:window|is_maximized",
        ] {
            assert!(drawn.contains(command), "{command} is missing");
        }
    }
}
