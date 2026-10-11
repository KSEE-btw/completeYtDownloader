"use strict";

/* Toolbar popup: seed the URL from the active tab, then hand off to the
   shared UI. */

(async function () {
  const api = window.browser || window.chrome;

  function clean(url) {
    try {
      const parsed = new URL(url);
      if (/(^|\.)youtube\.com$/.test(parsed.hostname) ||
          parsed.hostname === "youtu.be") {
        for (const key of ["list", "index", "t", "start_radio", "pp"]) {
          parsed.searchParams.delete(key);
        }
      }
      return parsed.toString();
    } catch (_) {
      return url;
    }
  }

  let url = "";
  try {
    const tabs = await api.tabs.query({ active: true, currentWindow: true });
    const tab = tabs && tabs[0];
    if (tab && tab.url && /^https?:/.test(tab.url)) {
      url = clean(tab.url);
    }
  } catch (_) {
    /* no permission, user will paste */
  }

  window.YTGrabUI.mount(document.getElementById("root"), {
    url,
    editableUrl: true,
  });
})();
