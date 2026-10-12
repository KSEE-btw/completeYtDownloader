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

  // A tiny marker in front of *video* sources that answers the only question
  // that matters: will the file I get have sound?
  //
  // YouTube muxes audio into just the low "progressive" formats (normally 360p
  // and sometimes 720p); every other resolution is a video-only DASH stream
  // with the sound in a separate track.  That is why almost everything shows a
  // mark -- ytgrab merges the best audio back in, so the download is never
  // silent unless you switch ensure_audio off.  A native <select> can only hold
  // text, hence a glyph rather than an image.
  const AUDIO_MARK = {
    av: "\u{1F50A}",          // the stream already carries audio
    mux: "\u{1F50A}+",        // no audio of its own; ytgrab muxes the best in
    silent: "\u{1F507}",      // no audio, and auto-muxing is switched off
  };

  function audioMark(kind, ensureAudio) {
    if (kind === "av") return AUDIO_MARK.av;
    if (kind === "video") return ensureAudio ? AUDIO_MARK.mux : AUDIO_MARK.silent;
    return "";                // an audio-only entry obviously has audio
  }

  // config arrives as JSON: treat the usual false-y spellings as "off"
  function configFlag(value, fallback) {
    if (value === undefined || value === null || value === "") return fallback;
    if (typeof value === "string") {
      return !["false", "0", "no", "off", "none"].includes(value.toLowerCase());
    }
    return !!value;
  }

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

    // --- editor pack: clip range, constant frame rate, 48 kHz ---
    const clipStart = el("input", {
      type: "text",
      placeholder: "start",
      spellcheck: "false",
      title: "Start of the clip \u2014 1:30 or 0:01:30. Blank means the start.",
      oninput: updateHint,
    });
    const clipEnd = el("input", {
      type: "text",
      placeholder: "end",
      spellcheck: "false",
      title: "End of the clip \u2014 2:15 or 0:02:15. Blank means the end.",
      oninput: updateHint,
    });
    const cfrBox = el("input", { type: "checkbox", onchange: updateHint });
    const k48Box = el("input", { type: "checkbox", onchange: updateHint });

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
    const clipField = el("label", { class: "field" }, [
      el("span", { text: "Clip (optional)" }),
      el("div", { class: "timerow" }, [
        clipStart,
        el("span", { class: "dash", text: "\u2192" }),
        clipEnd,
      ]),
    ]);
    const cfrLine = el("label", {
      class: "checkline",
      title: "Force a constant frame rate \u2014 stops VFR footage drifting " +
        "against the audio on a timeline",
    }, [cfrBox, el("span", { text: "Constant FPS" })]);
    const k48Line = el("label", {
      class: "checkline",
      title: "Re-encode the audio at 48 kHz, the rate editing tools mix at",
    }, [k48Box, el("span", { text: "48 kHz audio" })]);
    const editorBoxes = el("div", { class: "checkgrid" }, [cfrLine, k48Line]);

    const body = el("div", { class: "body" }, [
      urlRow,
      titleEl,
      formatField,
      clipField,
      editorBoxes,
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
      clipStart.disabled = busy;
      clipEnd.disabled = busy;
      cfrBox.disabled = busy;
      k48Box.disabled = busy;
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

    // Mirrors the host's parse_timecode: "90", "1:30" or "0:01:30".  Blank is
    // 0 ("no bound"); nonsense is null so a bad clip is refused before we
    // hand anything to the downloader.
    function parseTime(value) {
      const text = String(value || "")
        .trim()
        .replace(/s$/i, "")
        .replace(",", ".");
      if (!text) {
        return 0;
      }
      const parts = text.split(":");
      if (parts.length > 3) {
        return null;
      }
      let total = 0;
      for (const part of parts) {
        const piece = Number(part.trim());
        if (part.trim() === "" || !Number.isFinite(piece) || piece < 0) {
          return null;
        }
        total = total * 60 + piece;
      }
      return total;
    }

    function editorNote() {
      const bits = [];
      const start = (clipStart.value || "").trim();
      const end = (clipEnd.value || "").trim();
      if (start || end) {
        bits.push(`clip ${start || "0:00"}\u2013${end || "end"}`);
      }
      if (cfrBox.checked) {
        bits.push("constant frame rate");
      }
      if (k48Box.checked) {
        bits.push("48 kHz audio");
      }
      return bits.join(", ");
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
      const editor = editorNote();
      if (editor) {
        text += (text ? " " : "") + `Editor: ${editor}.`;
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
        clipStart.value = state.config.clip_start || "";
        clipEnd.value = state.config.clip_end || "";
        cfrBox.checked = configFlag(state.config.force_cfr, false);
        k48Box.checked = configFlag(state.config.audio_48k, false);
        updateHint();
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

      const ensureAudio = configFlag(state.config.ensure_audio, true);
      const groups = [
        ["Video + audio", "av"],
        [ensureAudio
          ? "Video only \u2014 ytgrab adds the best audio"
          : "Video only", "video"],
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
          // speaker in front of video sources only (never audio-only)
          const mark = audioMark(f.kind, ensureAudio);
          const option = el("option", {
            value: `id:${f.id}`,
            text: `${mark ? mark + "  " : ""}${f.label}${size}  \u00b7  ${badge}`,
            title: f.kind === "av"
              ? `${f.vcodec_raw || ""} + ${f.acodec_raw || ""}`
              : f.kind === "video"
                ? (ensureAudio
                    ? `${f.vcodec_raw || ""} \u2014 video-only stream; ytgrab ` +
                      "muxes the best audio in"
                    : `${f.vcodec_raw || ""} \u2014 video-only stream; no audio`)
                : `${f.acodec_raw || ""}`,
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
      const clipFrom = parseTime(clipStart.value);
      const clipTo = parseTime(clipEnd.value);
      if (clipFrom === null || clipTo === null) {
        setStatus(
          "Clip times look like 1:30 or 0:01:30 (seconds are fine too).",
          "bad"
        );
        return;
      }
      if (clipFrom && clipTo && clipTo <= clipFrom) {
        setStatus("The clip's end has to come after its start.", "bad");
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
        clip_start: (clipStart.value || "").trim(),
        clip_end: (clipEnd.value || "").trim(),
        force_cfr: cfrBox.checked,
        audio_48k: k48Box.checked,
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
