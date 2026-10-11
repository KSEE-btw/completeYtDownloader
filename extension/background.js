"use strict";

/*
 * Background broker.
 *
 * The native host can only be talked to from the background context, and its
 * replies (including a stream of progress messages) have to reach whichever
 * UI is open — the in-page panel, the toolbar popup, or both.  So every UI
 * opens a long-lived port to us and we simply relay both directions.
 */

const HOST_NAME = "ytgrab";

let nativePort = null;
const uiPorts = new Set();

function broadcast(msg) {
  for (const port of uiPorts) {
    try {
      port.postMessage(msg);
    } catch (_) {
      uiPorts.delete(port);
    }
  }
}

function connectNative() {
  if (nativePort) {
    return nativePort;
  }
  let port;
  try {
    port = browser.runtime.connectNative(HOST_NAME);
  } catch (err) {
    broadcast({ type: "host_error", message: String(err) });
    return null;
  }
  nativePort = port;

  port.onMessage.addListener((msg) => broadcast(msg));

  port.onDisconnect.addListener(() => {
    const err = browser.runtime.lastError;
    broadcast({
      type: "host_disconnected",
      message: err ? err.message : "native host disconnected",
    });
    nativePort = null;
  });

  broadcast({ type: "host_connected" });
  return port;
}

browser.runtime.onConnect.addListener((port) => {
  if (port.name !== "ytgrab-ui") {
    return;
  }
  uiPorts.add(port);

  port.onMessage.addListener((msg) => {
    const target = connectNative();
    if (!target) {
      try {
        port.postMessage({
          type: "error",
          id: msg && msg.id,
          message: "native host unavailable — is ytgrab installed?",
        });
      } catch (_) {}
      return;
    }
    try {
      target.postMessage(msg);
    } catch (err) {
      try {
        port.postMessage({ type: "error", id: msg && msg.id, message: String(err) });
      } catch (_) {}
    }
  });

  port.onDisconnect.addListener(() => uiPorts.delete(port));
});
