"use strict";

/*
 * Injects a floating button + panel on YouTube pages.  The panel lives in a
 * shadow root so YouTube's stylesheet can't touch it.
 */

(function () {
  const api = window.browser || window.chrome;
  if (!api || !api.runtime || window.__ytgrabLoaded) {
    return;
  }
  window.__ytgrabLoaded = true;
  console.info("[ytgrab] content script running");

  const STYLE = `
    :host { all: initial; }
    .fab {
      position: fixed; right: 18px; bottom: 96px;
      width: 46px; height: 46px; border-radius: 50%;
      background: #4f8cff; color: #fff;
      display: flex; align-items: center; justify-content: center;
      cursor: pointer; z-index: 2147483647;
      box-shadow: 0 4px 16px rgba(0,0,0,.4);
      font: 700 20px/1 -apple-system, "Segoe UI", sans-serif;
      user-select: none; transition: transform .12s ease;
    }
    .fab:hover { transform: scale(1.06); }
    .fab svg { width: 22px; height: 22px; }
    .panel {
      position: fixed; right: 18px; bottom: 152px;
      width: 400px; max-width: calc(100vw - 36px);
      max-height: calc(100vh - 190px); overflow: auto;
      z-index: 2147483647;
      border-radius: 12px;
      box-shadow: 0 12px 44px rgba(0,0,0,.55);
    }
    .panel[hidden] { display: none; }
  `;

  let shadow;
  let panel;
  let ui = null;

  const isWatch = () =>
    location.pathname === "/watch" &&
    !!new URLSearchParams(location.search).get("v");

  const currentUrl = () =>
    isWatch() ? location.href.replace(/&?(list|index|t|start_radio)=[^&]*/g, "")
      .replace(/[?&]$/, "") : "";

  function build() {
    const host = document.createElement("div");
    host.id = "ytgrab-host";
    document.documentElement.appendChild(host);
    shadow = host.attachShadow({ mode: "open" });

    // Everything below is built with DOM APIs, never innerHTML.  Sites like
    // YouTube enforce Trusted Types (require-trusted-types-for 'script'), and
    // a string assigned to an HTML sink there throws and aborts the whole
    // injection -- which would leave no button at all.
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = api.runtime.getURL("shared/ui.css");

    const style = document.createElement("style");
    style.textContent = STYLE;

    const fab = document.createElement("div");
    fab.className = "fab";
    fab.title = "ytgrab";

    const SVG = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2.2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    for (const d of ["M12 3v12", "M7 10l5 5 5-5", "M5 21h14"]) {
      const path = document.createElementNS(SVG, "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    }
    fab.appendChild(svg);

    panel = document.createElement("div");
    panel.className = "panel";
    panel.hidden = true;

    shadow.append(link, style, fab, panel);
    fab.addEventListener("click", toggle);
  }

  function toggle() {
    if (panel.hidden) {
      openPanel();
    } else {
      closePanel();
    }
  }

  function openPanel() {
    panel.hidden = false;
    if (!ui) {
      ui = window.YTGrabUI.mount(panel, {
        url: currentUrl(),
        editableUrl: true,
        showClose: true,
        onClose: closePanel,
      });
    } else {
      ui.setUrl(currentUrl());
    }
  }

  function closePanel() {
    panel.hidden = true;
  }

  function onNavigate() {
    if (ui) {
      ui.setUrl(currentUrl());
    }
  }

  function boot() {
    if (!document.documentElement) {
      return;
    }
    try {
      build();
    } catch (err) {
      console.error("[ytgrab] could not inject the panel:", err);
      return;
    }
    console.info("[ytgrab] button added to", location.pathname);
    window.addEventListener("yt-navigate-finish", onNavigate);
    let last = location.href;
    setInterval(() => {
      if (location.href !== last) {
        last = location.href;
        onNavigate();
      }
    }, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
