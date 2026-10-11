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
    shadow.innerHTML =
      `<link rel="stylesheet" href="${api.runtime.getURL("shared/ui.css")}">` +
      `<style>${STYLE}</style>` +
      `<div class="fab" title="ytgrab">` +
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
      `stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">` +
      `<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/>` +
      `<path d="M5 21h14"/></svg></div>` +
      `<div class="panel" hidden></div>`;
    panel = shadow.querySelector(".panel");
    shadow.querySelector(".fab").addEventListener("click", toggle);
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
    build();
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
