"use strict";

/*
 * Shared UI for ytgrab.  Mounted into a shadow root by the content script on
 * YouTube, and into the toolbar popup.  All work is delegated to the native
 * host through the background broker.
 */

(function () {
  if (window.YTGrabUI) {
    return;
  }

  const api = window.browser || window.chrome;
  const uid = Math.random().toString(36).slice(2, 8);
  let seq = 0;
  const nextId = (tag) => `${uid}-${tag}-${++seq}`;

  // ---- tiny DOM helpers -------------------------------------------------- //

  function el(tag, props, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) {
        continue;
      }
      if (key === "class") {
        node.className = value;
      } else if (key === "text") {
        node.textContent = value;
      } else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else {
        node.setAttribute(key, value === true ? "" : value);
      }
    }
    for (const child of [].concat(children || [])) {
      if (child) {
        node.appendChild(child);
      }
    }
    return node;
  }

  // ---- formatting -------------------------------------------------------- //

  function fmtBytes(n) {
    if (!n || n < 0) {
      return "";
    }
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    let x = n;
    while (x >= 1024 && i < units.length - 1) {
      x /= 1024;
      i += 1;
    }
    return `${x.toFixed(x < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
  }

  const fmtSpeed = (n) => (n ? `${fmtBytes(n)}/s` : "");

  function fmtEta(s) {
    if (!s || s < 0) {
      return "";
    }
    s = Math.round(s);
    const m = Math.floor(s / 60);
    return m ? `${m}m ${s % 60}s` : `${s}s`;
  }

  const COMPAT = {
    ok: { cls: "ok", short: "Premiere \u2713" },
    remux: { cls: "warn", short: "repack" },
    transcode: { cls: "bad", short: "convert" },
  };

  // A tiny audio marker shown only in front of *video* sources, so you can
  // tell at a glance whether a video stream already carries audio.  A native
  // <select> can only hold text, so the marker is a speaker glyph, not an image.
  const AUDIO_MARK = {
    av: "\u{1F50A}",     // video that already has audio
    video: "\u{1F507}",  // video only (no audio in this stream)
  };

  // ---- the widget -------------------------------------------------------- //

  function mount(root, options) {
    const opts = Object.assign(
      { url: "", editableUrl: true, title: "", onClose: null, showClose: false },
      options || {}
    );

    const state = {
      port: null,
      pingId: null,
      listId: null,
      jobId: null,
      folderId: null,
      formats: [],
      config: {},
      targets: {},
      running: false,
      connected: false,
      pingTimer: null,
      probeTimer: null,
    };

    // --- elements ---
    const hostBadge = el("span", { class: "host-badge", text: "checking\u2026" });
    const urlInput = el("input", {
      type: "text",
      placeholder: "Paste a video URL",
      value: opts.url,
      spellcheck: "false",
    });
    const probeBtn = el("button", {
      class: "ghost",
      text: "Load",
      onclick: () => probe(),
    });
    const urlRow = el("div", { class: "urlrow" }, [urlInput, probeBtn]);
    if (!opts.editableUrl) {
      urlRow.hidden = true;
    }

    const titleEl = el("div", { class: "title", text: opts.title });
    const formatSel = el("select", { class: "format", onchange: updateHint });
    const targetSel = el("select", { class: "target", onchange: updateHint });
    const convertBox = el("input", {
      type: "checkbox",
      checked: true,
      onchange: () => {
        targetSel.disabled = !convertBox.checked;
        updateHint();
      },
    });
    const outdirInput = el("input", {
      type: "text",
      class: "outdir",
      readonly: true,
    });
    const outdirBtn = el("button", {
      class: "ghost folder",
      type: "button",
      text: "\u2026",
      title: "Choose a folder on this computer",
      onclick: chooseFolder,
    });
    const hintEl = el("div", { class: "hint" });

    const dlBtn = el("button", {
      class: "primary",
      text: "Download",
      onclick: startDownload,
    });
    const openBtn = el("button", {
      class: "ghost",
      text: "Show file",
      hidden: true,
      onclick: showFile,
    });
    const queueBtn = el("button", {
      class: "ghost",
      text: "Open queue window",
      title: "Track progress and cancel downloads in the ytgrab window",
      onclick: openQueue,
    });

    const bar = el("i");
    const pinfoLeft = el("span", { text: "" });
    const pinfoRight = el("span", { text: "" });
    const progress = el("div", { class: "progress", hidden: true }, [
      el("div", { class: "bar" }, [bar]),
      el("div", { class: "pinfo" }, [pinfoLeft, pinfoRight]),
    ]);

    const statusEl = el("div", { class: "status" });
    const logEl = el("div", { class: "log" });

    const formatField = el("label", { class: "field" }, [
      el("span", { text: "Format" }),
      formatSel,
    ]);
    const targetField = el("label", { class: "field" }, [
      el("span", { text: "Convert to" }),
      targetSel,
    ]);
    const outdirField = el("label", { class: "field" }, [
      el("span", { text: "Save to" }),
      el("div", { class: "folderrow" }, [outdirInput, outdirBtn]),
    ]);

    const body = el("div", { class: "body" }, [
      urlRow,
      titleEl,
      formatField,
      el("div", { class: "checkline" }, [
        convertBox,
        el("span", {
          text: "Convert to a Premiere-compatible file \u2014 only if needed",
        }),
      ]),
      el("div", { class: "row2" }, [targetField, outdirField]),
      hintEl,
      el("div", { class: "actions" }, [dlBtn, openBtn, queueBtn]),
      progress,
      statusEl,
      logEl,
    ]);

    const head = el("div", { class: "head" }, [
      el("span", { class: "logo", text: "ytgrab" }),
      el("span", { class: "spacer" }),
      hostBadge,
      opts.showClose
        ? el("button", {
            class: "close",
            text: "\u00d7",
            title: "Close",
            onclick: () => destroy(),
          })
        : null,
    ]);

    root.textContent = "";
    root.appendChild(el("div", { class: "ytgrab" }, [head, body]));
    titleEl.style.display = opts.title || opts.url ? "" : "none";

    // --- helpers ---

    function setStatus(text, cls) {
      statusEl.textContent = text || "";
      statusEl.className = "status" + (cls ? ` ${cls}` : "");
    }

    function setBusy(busy) {
      state.running = busy;
      dlBtn.disabled = busy;
      dlBtn.textContent = busy ? "Working\u2026" : "Download";
      formatSel.disabled = busy;
      convertBox.disabled = busy;
      targetSel.disabled = busy || !convertBox.checked;
      probeBtn.disabled = busy;
      outdirBtn.disabled = busy;
    }

    function resolvedSelection() {
      const value = formatSel.value || "";
      if (value === "auto:premiere_safe") {
        return { format: "", mode: "premiere_safe" };
      }
      if (value === "auto:best") {
        return { format: "", mode: "best" };
      }
      return { format: value.replace(/^id:/, ""), mode: "" };
    }

    function selectedFormat() {
      const value = formatSel.value || "";
      return state.formats.find((f) => `id:${f.id}` === value) || null;
    }

    function targetLabel() {
      return state.targets[targetSel.value] || targetSel.value;
    }

    function updateHint() {
      const value = formatSel.value || "";
      const convert = convertBox.checked;
      const entry = selectedFormat();
      let tag = null;               // { cls, label } or null
      let text = "";

      if (value === "auto:premiere_safe") {
        tag = { cls: "ok", label: "auto" };
        text = "Best H.264 + AAC MP4 available \u2014 normally nothing to convert.";
      } else if (value === "auto:best") {
        tag = { cls: "warn", label: "auto" };
        text = "Highest quality; converts only if the codec isn't Premiere-friendly.";
      } else if (entry) {
        const size = entry.size ? ` \u00b7 ${fmtBytes(entry.size)}` : "";
        if (entry.kind === "video") {
          tag = { cls: "warn", label: "audio+" };
          text = `${entry.label}${size} \u2014 video only; the best audio ` +
            `track is downloaded and muxed in.`;
          text += convert
            ? ` Converts to ${targetLabel()} if needed.`
            : " Conversion is off, so it lands as the muxed file (usually MKV).";
        } else if (entry.compat === "ok") {
          tag = { cls: "ok", label: "ready" };
          text = `${entry.label}${size} \u2014 already Premiere-compatible.`;
        } else if (entry.compat === "remux") {
          tag = { cls: "warn", label: "repack" };
          text = `${entry.label}${size} \u2014 right codecs, repackaged into MP4 (fast).`;
        } else {
          tag = { cls: "bad", label: "convert" };
          text = `${entry.label}${size} \u2014 ${entry.vcodec || "?"} \u2192 ` +
            `${targetLabel()} (re-encode).`;
        }
        if (!convert && entry.compat !== "ok" && entry.kind !== "video") {
          text += " Conversion is off, so it downloads as-is.";
        }
      }
      // textContent only -- never innerHTML (Trusted Types on YouTube).
      hintEl.replaceChildren();
      if (tag) {
        const badge = document.createElement("span");
        badge.className = "tag " + tag.cls;
        badge.textContent = tag.label;
        hintEl.appendChild(badge);
      }
      if (text) {
        hintEl.appendChild(document.createTextNode(text));
      }
    }

    // --- native communication ---

    function send(msg) {
      if (!state.port) {
        setStatus("Not connected to the ytgrab host.", "bad");
        return;
      }
      try {
        state.port.postMessage(msg);
      } catch (err) {
        setStatus(String(err), "bad");
      }
    }

    function connect() {
      let port;
      try {
        port = api.runtime.connect({ name: "ytgrab-ui" });
      } catch (err) {
        hostBadge.textContent = "host offline";
        hostBadge.className = "host-badge bad";
        setStatus(`Could not reach the extension background: ${err}`, "bad");
        return;
      }
      state.port = port;
      port.onMessage.addListener(onMessage);
      port.onDisconnect.addListener(() => {
        state.connected = false;
        hostBadge.textContent = "host offline";
        hostBadge.className = "host-badge bad";
      });

      state.pingId = nextId("ping");
      send({ cmd: "ping", id: state.pingId });
      // If the helper does not answer, say so instead of sitting on
      // "checking…" forever.
      clearTimeout(state.pingTimer);
      state.pingTimer = setTimeout(() => {
        if (!state.connected) {
          hostBadge.textContent = "host not responding";
          hostBadge.className = "host-badge bad";
          setStatus(
            "The ytgrab helper is not answering. Try again, or restart " +
              "Firefox if it keeps happening.",
            "bad"
          );
        }
      }, 20000);
      if (opts.url) {
        probe();
      }
    }

    function onMessage(msg) {
      if (!msg || typeof msg !== "object") {
        return;
      }
      const type = msg.type;

      if (type === "host_connected") {
        return;
      }
      if (type === "host_disconnected" || type === "host_error") {
        state.connected = false;
        hostBadge.textContent = "host offline";
        hostBadge.className = "host-badge bad";
        if (type === "host_error") {
          setStatus(msg.message, "bad");
        } else {
          setStatus(
            msg.message ||
              "Lost the ytgrab host. Run scripts/install.ps1, then fully " +
                "restart Firefox and reload the extension.",
            "bad"
          );
        }
        setBusy(false);
        return;
      }

      if (msg.id === state.pingId && type === "pong") {
        state.connected = true;
        clearTimeout(state.pingTimer);
        state.config = msg.config || {};
        state.targets = msg.targets || {};
        const ok = msg.ok;
        hostBadge.textContent = ok ? `ytgrab ${msg.version}` : "tools missing";
        hostBadge.className = "host-badge " + (ok ? "ok" : "bad");
        if (!ok) {
          const missing = !(msg.tools.ytdlp || {}).path
            ? "yt-dlp"
            : "ffmpeg";
          setStatus(
            `${missing} not found on PATH.` +
              " If you just installed it, fully restart Firefox.",
            "bad"
          );
        }
        fillTargets();
        outdirInput.value = state.config.outdir || "";
        return;
      }

      if (msg.id === state.listId) {
        clearTimeout(state.probeTimer);
        state.listId = null;
        if (type === "formats") {
          state.formats = msg.formats || [];
          titleEl.textContent = msg.title || "";
          titleEl.style.display = "";
          fillFormats(msg);
          setStatus(
            msg.is_live
              ? "This is a live stream \u2014 recorded downloads may be partial."
              : ""
          );
        } else if (type === "error") {
          setStatus(msg.message, "bad");
          titleEl.textContent = "";
        }
        return;
      }

      if (state.folderId && msg.id === state.folderId) {
        if (msg.type === "folder") {
          if (msg.cancelled) {
            setStatus("Kept the current folder.", "");
          } else if (msg.path) {
            state.config.outdir = msg.path;
            outdirInput.value = msg.path;
            setStatus(`Saving downloads to ${msg.path}`, "ok");
          }
        } else if (msg.type === "error") {
          setStatus(msg.message, "bad");
        }
        return;
      }

      if (state.jobId && msg.id === state.jobId) {
        handleJobMessage(msg);
      }

      if (msg.type !== "progress" && msg.id &&
          String(msg.id).startsWith(`${uid}-`)) {
        logLine(msg);
      }
    }

    function handleJobMessage(msg) {
      switch (msg.type) {
        case "queued": {
          setBusy(false);
          progress.hidden = true;
          bar.style.width = "0%";
          pinfoLeft.textContent = "";
          pinfoRight.textContent = "";
          state.jobId = null;
          statusEl.replaceChildren();
          const span = document.createElement("span");
          span.className = "ok";
          span.textContent =
            "Added to the queue \u2014 it keeps downloading while this " +
            "closes. Track it in the ytgrab window.";
          statusEl.appendChild(span);
          statusEl.className = "status ok";
          openBtn.hidden = true;
          break;
        }
        case "stage":
          pinfoLeft.textContent = msg.stage_label || msg.stage;
          break;
        case "progress": {
          const pct = Math.max(0, Math.min(100, msg.percent || 0));
          bar.style.width = `${pct}%`;
          pinfoLeft.textContent = `${(msg.phase || "").replace(/^\w/, (c) =>
            c.toUpperCase()
          )}  ${pct.toFixed(0)}%`;
          const bits = [];
          if (msg.speed) {
            bits.push(fmtSpeed(msg.speed));
          }
          if (msg.eta) {
            bits.push(`ETA ${fmtEta(msg.eta)}`);
          }
          if (!bits.length && msg.total) {
            bits.push(fmtBytes(msg.total));
          }
          pinfoRight.textContent = bits.join("  ");
          break;
        }
        case "done":
          setBusy(false);
          bar.style.width = "100%";
          progress.hidden = false;
          pinfoLeft.textContent = msg.mode === "none" ? "Done" : `Done (${msg.mode})`;
          pinfoRight.textContent = "";
          setStatus("", "");
          statusEl.replaceChildren();
          const savedSpan = document.createElement("span");
          savedSpan.className = "ok";
          savedSpan.textContent = `Saved \u2014 ${msg.codecs || ""}` +
            (msg.note ? " \u00b7 " + msg.note : "");
          const pathSpan = document.createElement("span");
          pathSpan.className = "path";
          pathSpan.textContent = msg.path || "";
          statusEl.append(savedSpan, pathSpan);
          statusEl.className = "status ok";
          statusEl.dataset.path = msg.path || "";
          openBtn.hidden = false;
          state.jobId = null;
          break;
        case "error":
          setBusy(false);
          progress.hidden = true;
          if (msg.cancelled) {
            setStatus("Cancelled.", "");
          } else {
            setStatus(msg.message || "Download failed.", "bad");
          }
          state.jobId = null;
          break;
        default:
          break;
      }
    }

    function logLine(msg) {
      const stamp = new Date().toLocaleTimeString();
      logEl.textContent += `[${stamp}] ${JSON.stringify(msg)}\n`;
      const lines = logEl.textContent.split("\n");
      if (lines.length > 60) {
        logEl.textContent = lines.slice(-60).join("\n");
      }
      logEl.scrollTop = logEl.scrollHeight;
    }

    // --- data filling ---

    function fillTargets() {
      targetSel.textContent = "";
      const entries = Object.entries(state.targets);
      if (!entries.length) {
        targetSel.appendChild(
          el("option", { value: "h264", text: "H.264 / AAC (MP4)" })
        );
      }
      for (const [key, label] of entries) {
        targetSel.appendChild(el("option", { value: key, text: label }));
      }
      targetSel.value = state.config.default_target || "h264";
      targetSel.disabled = !convertBox.checked;
    }

    function fillFormats(data) {
      formatSel.textContent = "";
      const recommended = el("optgroup", { label: "Recommended" }, [
        el("option", {
          value: "auto:premiere_safe",
          text: "Auto \u2014 best Premiere-safe (H.264 if offered)",
        }),
        el("option", {
          value: "auto:best",
          text: "Auto \u2014 highest quality (convert if needed)",
        }),
      ]);
      formatSel.appendChild(recommended);

      const groups = [
        ["Video + audio", "av"],
        ["Video only", "video"],
        ["Audio only", "audio"],
      ];
      for (const [label, kind] of groups) {
        const items = state.formats.filter((f) => f.kind === kind);
        if (!items.length) {
          continue;
        }
        const group = el("optgroup", { label });
        for (const f of items) {
          const badge = (COMPAT[f.compat] || COMPAT.transcode).short;
          const size = f.size ? `  \u00b7  ${fmtBytes(f.size)}` : "";
          // speaker icon in front of video sources only (never audio-only)
          const mark = AUDIO_MARK[f.kind];
          const option = el("option", {
            value: `id:${f.id}`,
            text: `${mark ? mark + "  " : ""}${f.label}${size}  \u00b7  ${badge}`,
            title: f.kind === "video"
              ? `${f.vcodec_raw || ""} \u2014 no audio; ytgrab muxes the best audio`
              : `${f.vcodec_raw || ""} + ${f.acodec_raw || ""}`,
          });
          option.dataset.compat = f.compat;
          group.appendChild(option);
        }
        formatSel.appendChild(group);
      }

      formatSel.value = "auto:premiere_safe";
      updateHint();
      dlBtn.disabled = false;
    }

    function probe() {
      const url = (urlInput.value || opts.url || "").trim();
      if (!url) {
        setStatus("Paste a video URL first.", "bad");
        return;
      }
      urlInput.value = url;
      opts.url = url;
      setStatus("Reading available formats\u2026", "");
      state.listId = nextId("list");
      // A probe can legitimately take a while (a long or live video), but say
      // so rather than look frozen.
      clearTimeout(state.probeTimer);
      state.probeTimer = setTimeout(() => {
        if (state.listId) {
          setStatus(
            "Still reading this video\u2026 live or age-restricted videos " +
              "can take a while.",
            ""
          );
        }
      }, 25000);
      send({ cmd: "list_formats", id: state.listId, url });
    }

    // --- actions ---

    function startDownload() {
      const url = (urlInput.value || opts.url || "").trim();
      if (!url) {
        setStatus("Paste a video URL first.", "bad");
        return;
      }
      const sel = resolvedSelection();
      state.jobId = nextId("dl");
      // The Windows queue window owns the download from here on, so the panel
      // only confirms the hand-off -- no inline progress bar to lose when the
      // popup closes.
      progress.hidden = true;
      openBtn.hidden = true;
      setStatus("Adding to the queue\u2026", "");
      setBusy(true);
      send({
        cmd: "download",
        id: state.jobId,
        url,
        title: titleEl.textContent || "",
        format: sel.format,
        mode: sel.mode,
        convert: convertBox.checked,
        target: targetSel.value,
        keep_original: !!state.config.keep_original,
      });
    }

    function openQueue() {
      send({ cmd: "open_ui", id: nextId("ui") });
    }

    function chooseFolder() {
      if (state.running) {
        return;
      }
      state.folderId = nextId("folder");
      const start =
        outdirInput.value || (state.config && state.config.outdir) || "";
      setStatus("Pick a folder in the window that just opened\u2026", "");
      send({ cmd: "choose_folder", id: state.folderId, start });
    }

    function showFile() {
      const path = statusEl.dataset.path;
      if (path) {
        send({ cmd: "reveal", id: nextId("reveal"), path });
      }
    }

    function destroy() {
      try {
        state.port && state.port.disconnect();
      } catch (_) {}
      state.port = null;
      if (typeof opts.onClose === "function") {
        opts.onClose();
      }
    }

    function setUrl(url, { autoprobe = true } = {}) {
      if (!url || url === opts.url) {
        return;
      }
      opts.url = url;
      urlInput.value = url;
      if (autoprobe && state.connected) {
        probe();
      }
    }

    connect();
    updateHint();

    return { destroy, setUrl, probe, element: root };
  }

  window.YTGrabUI = { mount };
})();
