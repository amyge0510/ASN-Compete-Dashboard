(() => {
  "use strict";

  // ---- strip geometry (in canvas pixels) ----
  const FRAME_W = 600;
  const FRAME_H = 440;
  const PAD = 30;
  const GAP = 22;
  const FOOTER = 96;
  const COUNTDOWN = 3;      // seconds before each shot
  const BETWEEN_SHOTS = 700; // ms pause after a shot
  const MIN_SHOTS = 1;
  const MAX_SHOTS = 10;

  // Live preview approximations of each filter (the real one is applied to
  // the captured pixels afterwards, so it looks the same in every browser).
  const PREVIEW_CSS = {
    vintage: "grayscale(1) sepia(.35) contrast(.92) brightness(1.05)",
    noir: "grayscale(1) contrast(1.25)",
    warm: "sepia(.22) saturate(.85) contrast(.94) brightness(1.04)",
    natural: "none",
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    video: $("video"),
    vfEmpty: $("vfEmpty"),
    vfError: $("vfError"),
    vfTurn: $("vfTurn"),
    vfCount: $("vfCount"),
    vfProgress: $("vfProgress"),
    vfFlash: $("vfFlash"),
    cameraBtn: $("cameraBtn"),
    shootBtn: $("shootBtn"),
    downloadBtn: $("downloadBtn"),
    againBtn: $("againBtn"),
    countDown: $("countDown"),
    countUp: $("countUp"),
    countOut: $("countOut"),
    filters: $("filters"),
    hint: $("hint"),
    thumbs: $("thumbs"),
    tray: $("tray"),
    trayEmpty: $("trayEmpty"),
    strip: $("strip"),
    stars: $("stars"),
  };

  const state = {
    mode: "solo",          // "solo" | "together"
    count: 4,              // rows on the strip
    filter: "vintage",
    phase: "off",          // off | ready | shooting | done
    shots: [],             // raw, unfiltered frame canvases
    stream: null,
    stripBlob: null,
  };

  // ---------------------------------------------------------------- camera

  async function startCamera() {
    els.vfError.hidden = true;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showCameraError("this browser can't open a camera here. try a recent browser, over https.");
      return;
    }
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      });
    } catch (err) {
      const denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
      showCameraError(denied
        ? "camera access was blocked. allow it in your browser's site settings, then try again."
        : "couldn't find a camera to use.");
      return;
    }
    els.video.srcObject = state.stream;
    await new Promise((res) => {
      if (els.video.readyState >= 1) res();
      else els.video.addEventListener("loadedmetadata", res, { once: true });
    });
    await els.video.play().catch(() => {});
    els.vfEmpty.hidden = true;
    setPhase("ready");
  }

  function showCameraError(msg) {
    els.vfError.textContent = msg;
    els.vfError.hidden = false;
  }

  // Grab the current video frame, mirrored and cover-cropped to the frame size.
  function grabFrame() {
    const v = els.video;
    const c = document.createElement("canvas");
    c.width = FRAME_W;
    c.height = FRAME_H;
    const ctx = c.getContext("2d");
    const vw = v.videoWidth, vh = v.videoHeight;
    const target = FRAME_W / FRAME_H;
    let sw = vw, sh = vh;
    if (vw / vh > target) sw = vh * target; else sh = vw / target;
    const sx = (vw - sw) / 2, sy = (vh - sh) / 2;
    ctx.translate(FRAME_W, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(v, sx, sy, sw, sh, 0, 0, FRAME_W, FRAME_H);
    return c;
  }

  // ---------------------------------------------------------------- shooting

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  function totalShots() {
    return state.mode === "together" ? state.count * 2 : state.count;
  }

  async function shoot() {
    if (state.phase !== "ready") return;
    state.shots = [];
    els.thumbs.innerHTML = "";
    setPhase("shooting");
    const total = totalShots();

    for (let i = 0; i < total; i++) {
      els.vfProgress.textContent = `${i + 1} of ${total}`;
      els.vfProgress.hidden = false;
      if (state.mode === "together") {
        els.vfTurn.textContent = i % 2 === 0 ? "your turn ♡" : "their turn ♡";
        els.vfTurn.hidden = false;
      }
      for (let s = COUNTDOWN; s >= 1; s--) {
        els.vfCount.textContent = s;
        restartAnim(els.vfCount, "pop");
        await wait(1000);
      }
      els.vfCount.textContent = "";
      state.shots.push(grabFrame());
      shutter();
      addThumb(state.shots[state.shots.length - 1]);
      await wait(BETWEEN_SHOTS);
    }

    els.vfProgress.hidden = true;
    els.vfTurn.hidden = true;
    await printStrip(true);
    setPhase("done");
  }

  function shutter() {
    restartAnim(els.vfFlash, "go");
    clickSound();
  }

  function addThumb(src) {
    const t = document.createElement("canvas");
    t.width = 128;
    t.height = Math.round(128 * FRAME_H / FRAME_W);
    const ctx = t.getContext("2d");
    ctx.drawImage(src, 0, 0, t.width, t.height);
    applyFilter(ctx, t.width, t.height, state.filter, 0.4);
    els.thumbs.appendChild(t);
  }

  let audioCtx = null;
  function clickSound() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const len = Math.floor(audioCtx.sampleRate * 0.08);
      const buf = audioCtx.createBuffer(1, len, audioCtx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3);
      const src = audioCtx.createBufferSource();
      const gain = audioCtx.createGain();
      gain.gain.value = 0.35;
      src.buffer = buf;
      src.connect(gain).connect(audioCtx.destination);
      src.start();
    } catch (_) { /* sound is optional */ }
  }

  function restartAnim(el, cls) {
    el.classList.remove(cls);
    void el.offsetWidth; // reflow so the animation replays
    el.classList.add(cls);
  }

  // ---------------------------------------------------------------- filters

  // Applied per pixel so the result is identical in every browser
  // (canvas `ctx.filter` isn't available everywhere).
  function applyFilter(ctx, w, h, name, scale = 1) {
    if (name === "natural") return;
    const img = ctx.getImageData(0, 0, w, h);
    const d = img.data;
    const grain = ({ vintage: 20, noir: 26, warm: 12 })[name] * scale;
    const vignette = ({ vintage: 0.42, noir: 0.5, warm: 0.3 })[name];
    const cx = w / 2, cy = h / 2;
    const maxD2 = cx * cx + cy * cy;

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        let r = d[i], g = d[i + 1], b = d[i + 2];
        const lum = 0.299 * r + 0.587 * g + 0.114 * b;

        if (name === "vintage") {
          // soft contrast, lifted blacks, cream highlights, brown shadows
          const l = (lum - 128) * 0.9 + 128;
          r = l * 0.97 + 18;
          g = l * 0.93 + 11;
          b = l * 0.80 + 8;
        } else if (name === "noir") {
          const l = (lum - 128) * 1.28 + 124;
          r = g = b = l * 0.96 + 6;
        } else if (name === "warm") {
          // desaturate a touch, warm it, fade the blacks
          r = lum + (r - lum) * 0.82;
          g = lum + (g - lum) * 0.82;
          b = lum + (b - lum) * 0.82;
          r = r * 1.04 + 14;
          g = g * 0.98 + 8;
          b = b * 0.84 + 10;
        }

        const dx = x - cx, dy = y - cy;
        const v = 1 - vignette * ((dx * dx + dy * dy) / maxD2);
        const n = (Math.random() - 0.5) * grain;
        d[i] = r * v + n;
        d[i + 1] = g * v + n;
        d[i + 2] = b * v + n;
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  // ---------------------------------------------------------------- strip

  function renderStrip() {
    const cols = state.mode === "together" ? 2 : 1;
    const rows = Math.ceil(state.shots.length / cols);
    const W = PAD * 2 + cols * FRAME_W + (cols - 1) * GAP;
    const H = PAD + rows * FRAME_H + (rows - 1) * GAP + FOOTER;

    const c = $("work");
    c.width = W;
    c.height = H;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#191613";
    ctx.fillRect(0, 0, W, H);

    const frame = document.createElement("canvas");
    frame.width = FRAME_W;
    frame.height = FRAME_H;
    const fctx = frame.getContext("2d");

    state.shots.forEach((shot, i) => {
      fctx.drawImage(shot, 0, 0);
      applyFilter(fctx, FRAME_W, FRAME_H, state.filter);
      const col = i % cols, row = Math.floor(i / cols);
      ctx.drawImage(frame, PAD + col * (FRAME_W + GAP), PAD + row * (FRAME_H + GAP));
    });

    ctx.fillStyle = "#8c8479";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = '700 22px "Courier Prime", "Courier New", monospace';
    const now = new Date();
    const date = `${now.getMonth() + 1}.${now.getDate()}.${String(now.getFullYear()).slice(2)}`;
    const label = state.mode === "together" ? `photo booth ♡ ${date}` : `photo booth · ${date}`;
    if ("letterSpacing" in ctx) ctx.letterSpacing = "3px";
    ctx.fillText(label, W / 2, H - FOOTER / 2 - 4);
    return c;
  }

  async function printStrip(animate) {
    const c = renderStrip();
    state.stripBlob = await new Promise((r) => c.toBlob(r, "image/png"));
    const url = URL.createObjectURL(state.stripBlob);
    const img = els.strip;
    const prev = img.src;

    await new Promise((res) => {
      img.onload = res;
      img.src = url;
    });
    if (prev && prev.startsWith("blob:")) URL.revokeObjectURL(prev);

    img.classList.toggle("wide", state.mode === "together");
    img.hidden = false;
    els.trayEmpty.hidden = true;
    placeStars();

    if (animate) {
      img.classList.remove("printed", "redevelop");
      img.style.animationDuration = `${1.6 + state.shots.length * 0.35}s, 3.5s`;
      img.style.animationDelay = `0s, ${0.6 + state.shots.length * 0.25}s`;
      restartAnim(img, "printing");
    } else {
      img.classList.remove("printing");
      img.style.animationDuration = "";
      img.style.animationDelay = "";
      img.classList.add("printed");
      restartAnim(img, "redevelop");
    }
  }

  function clearStrip() {
    els.strip.hidden = true;
    els.strip.className = "strip";
    els.trayEmpty.hidden = false;
    els.stars.innerHTML = "";
    els.thumbs.innerHTML = "";
    state.shots = [];
    state.stripBlob = null;
  }

  function download() {
    if (!state.stripBlob) return;
    const a = document.createElement("a");
    const t = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    a.download = `photobooth-${t.getFullYear()}${pad(t.getMonth() + 1)}${pad(t.getDate())}-${pad(t.getHours())}${pad(t.getMinutes())}.png`;
    a.href = URL.createObjectURL(state.stripBlob);
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---------------------------------------------------------------- stars

  const STAR_SVG = {
    five: '<path d="M12 1.5l3.1 6.6 7.2.9-5.3 5 1.4 7.1L12 17.6l-6.4 3.5L7 14l-5.3-5 7.2-.9z"/>',
    four: '<path d="M12 0c.9 6.3 5.2 10.9 12 12-6.8 1.1-11.1 5.7-12 12-.9-6.3-5.2-10.9-12-12C6.8 10.9 11.1 6.3 12 0z"/>',
  };

  function starEl(kind, size, x, y, rot, delay, id) {
    const s = document.createElement("span");
    s.className = "star";
    s.style.left = `${x}%`;
    s.style.top = `${y}%`;
    s.style.setProperty("--r", `${rot}deg`);
    s.style.animationDelay = `${delay}s`;
    s.innerHTML =
      `<svg width="${size}" height="${size}" viewBox="0 0 24 24">` +
      `<defs><linearGradient id="g${id}" x1="0" y1="0" x2="1" y2="1">` +
      `<stop offset="0" stop-color="#ffffff"/><stop offset=".45" stop-color="#c9c6cc"/>` +
      `<stop offset=".7" stop-color="#8f8a93"/><stop offset="1" stop-color="#e6e2e8"/></linearGradient></defs>` +
      `<g fill="url(#g${id})" stroke="#7a757d" stroke-width=".6">${STAR_SVG[kind]}</g></svg>`;
    return s;
  }

  // Scatter a handful of silver stars along both edges of the strip.
  function placeStars() {
    els.stars.innerHTML = "";
    const n = 10;
    for (let i = 0; i < n; i++) {
      const left = i % 2 === 0;
      const y = 14 + (i / n) * 76 + Math.random() * 6;
      const x = left ? 4 + Math.random() * 18 : 74 + Math.random() * 18;
      const kind = Math.random() < 0.5 ? "five" : "four";
      const size = 16 + Math.round(Math.random() * 18);
      els.stars.appendChild(starEl(kind, size, x, y, Math.round(Math.random() * 40 - 20), Math.random() * 3, i));
    }
  }

  // ---------------------------------------------------------------- ui state

  function setPhase(phase) {
    state.phase = phase;
    const shooting = phase === "shooting";
    const live = phase !== "off";

    els.shootBtn.disabled = phase !== "ready";
    els.shootBtn.hidden = phase === "done";
    els.downloadBtn.hidden = phase !== "done";
    els.againBtn.hidden = phase !== "done";

    document.querySelectorAll(".mode").forEach((b) => (b.disabled = shooting));
    els.filters.querySelectorAll(".chip").forEach((b) => (b.disabled = shooting));
    updateStepper();

    const hints = {
      off: "turn on your camera to begin",
      ready: state.mode === "together"
        ? `${totalShots()} photos — take turns in front of the camera`
        : `${totalShots()} photos, ${COUNTDOWN} seconds each. press space or the button`,
      shooting: "smile ♡",
      done: "try another filter — your strip re-develops instantly",
    };
    els.hint.textContent = hints[phase];
    els.video.style.visibility = live ? "visible" : "hidden";
  }

  function updateStepper() {
    els.countOut.textContent = state.count;
    const locked = state.phase === "shooting";
    els.countDown.disabled = locked || state.count <= MIN_SHOTS;
    els.countUp.disabled = locked || state.count >= MAX_SHOTS;
  }

  function setCount(n) {
    state.count = Math.max(MIN_SHOTS, Math.min(MAX_SHOTS, n));
    if (state.phase === "done") resetToReady();
    else setPhase(state.phase);
  }

  function setFilter(name) {
    state.filter = name;
    els.filters.querySelectorAll(".chip").forEach((b) => {
      const on = b.dataset.filter === name;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-checked", on);
    });
    els.video.style.filter = PREVIEW_CSS[name];
    if (state.phase === "done") printStrip(false);
  }

  function setMode(mode) {
    if (mode === state.mode) return;
    state.mode = mode;
    document.querySelectorAll(".mode").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-checked", on);
    });
    if (state.phase === "done") resetToReady();
    else setPhase(state.phase);
  }

  function resetToReady() {
    clearStrip();
    setPhase(state.stream ? "ready" : "off");
  }

  // ---------------------------------------------------------------- wiring

  els.cameraBtn.addEventListener("click", startCamera);
  els.shootBtn.addEventListener("click", shoot);
  els.downloadBtn.addEventListener("click", download);
  els.againBtn.addEventListener("click", resetToReady);
  els.countDown.addEventListener("click", () => setCount(state.count - 1));
  els.countUp.addEventListener("click", () => setCount(state.count + 1));
  els.filters.addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (b && !b.disabled) setFilter(b.dataset.filter);
  });
  document.querySelectorAll(".mode").forEach((b) =>
    b.addEventListener("click", () => setMode(b.dataset.mode)));

  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || e.repeat) return;
    if (e.target.closest("button")) return; // let buttons handle their own space
    if (state.phase === "ready") { e.preventDefault(); shoot(); }
  });

  setFilter(state.filter);
  setPhase("off");
})();
