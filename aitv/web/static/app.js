// AI TV control room: the few things plain forms can't do.

// Header pill: is the worker alive, and how much is queued?
async function pulse() {
  const el = document.getElementById("pulse");
  try {
    const s = await (await fetch("/api/status", { cache: "no-store" })).json();
    const jobs = s.counts.jobs || {};
    const busy = (jobs.running || 0) + (jobs.queued || 0);
    el.className = "pill " + (s.worker.alive ? "ok" : "bad");
    el.textContent = !s.worker.alive ? "worker down" : busy ? `working · ${busy} in queue` : "worker idle";
  } catch {
    el.className = "pill bad";
    el.textContent = "offline";
  }
}
pulse();
setInterval(pulse, 5000);

// Reload the page while something on it is still in progress, unless the user is typing or playing
// something. Open sections and the scroll position survive the reload.
function autoRefreshIf(active, ms) {
  const key = "open:" + location.pathname;
  const sections = () => [...document.querySelectorAll("details")];
  const name = (d) => (d.querySelector("summary")?.firstChild?.textContent || "").trim();  // stable while counts change
  try {
    const kept = JSON.parse(sessionStorage.getItem(key) || "null");
    if (kept) {
      sections().forEach((d) => { if (name(d) in kept.open) d.open = kept.open[name(d)]; });
      window.scrollTo(0, kept.y);
      sessionStorage.removeItem(key);
    }
  } catch {}
  if (!active) return;
  setTimeout(() => {
    const typing = document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    const playing = [...document.querySelectorAll("video, audio")].some((m) => !m.paused);
    if (typing || playing) return autoRefreshIf(true, ms);
    try { sessionStorage.setItem(key, JSON.stringify({ open: Object.fromEntries(sections().map((d) => [name(d), d.open])), y: window.scrollY })); } catch {}
    location.reload();
  }, ms);
}

// Upload with drag-and-drop and a progress bar (large footage takes a while over Tailscale).
function initUpload() {
  const form = document.getElementById("upload");
  const input = form.querySelector('input[type=file]');
  const drop = document.getElementById("drop");
  const picked = document.getElementById("picked");
  const bar = document.getElementById("bar");
  const msg = document.getElementById("msg");
  const category = form.querySelector('[name=category]');
  const hint = document.getElementById("hint");
  let files = [];

  const hints = {
    image: "Headshots, set plates, logos, product shots, references. Thumbnails are made automatically.",
    voice: "A clean recording of someone talking; 10 seconds to a few minutes. It is trimmed, normalised and becomes a voice in the World.",
    sfx: "Sound effects. Local files are preferred over online ones.",
    music: "Music beds, stings, themes.",
    footage: "Video clips. A small 480p preview copy is made for browsing.",
  };
  const newType = form.querySelector('[name=new_type]');
  const showHint = () => {
    hint.textContent = hints[category.value] || "";
    // A new thing made from a voice upload is a voice; otherwise default to a character.
    newType.value = category.value === "voice" ? "voice" : "character";
    form.querySelector('[name=role]').value = category.value === "voice" ? "voice_reference" : "reference";
  };
  category.addEventListener("change", showHint);
  showHint();

  const show = () => {
    picked.innerHTML = "";
    for (const f of files) {
      const li = document.createElement("li");
      li.textContent = `${f.name} · ${(f.size / 1048576).toFixed(1)} MB`;
      picked.appendChild(li);
    }
  };
  input.addEventListener("change", () => { files = [...input.files]; show(); });
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => { files = [...ev.dataTransfer.files]; show(); });

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!files.length) { msg.textContent = "Pick at least one file."; return; }
    const data = new FormData(form);
    data.delete("files");
    files.forEach((f) => data.append("files", f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/upload");
    xhr.setRequestHeader("Accept", "application/json");
    bar.hidden = false;
    document.getElementById("go").disabled = true;
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.value = (100 * e.loaded) / e.total; };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const r = JSON.parse(xhr.responseText);
        msg.innerHTML = `Uploaded ${r.assets.length} file(s); processing now. ` +
          (r.entity_id ? `<a href="/world/${r.entity_id}">Open it in the World</a> · ` : "") +
          `<a href="/library">Library</a> · <a href="/jobs">Jobs</a>`;
        files = []; show(); form.reset(); showHint();
      } else {
        msg.textContent = `Upload failed (${xhr.status}): ${xhr.responseText.slice(0, 200)}`;
      }
      document.getElementById("go").disabled = false;
      bar.hidden = true; bar.value = 0;
    };
    xhr.onerror = () => { msg.textContent = "Upload failed: connection error."; document.getElementById("go").disabled = false; };
    xhr.send(data);
  });
}

// Notepad: save a second after you stop typing, and before leaving.
function initNotepad() {
  const pad = document.getElementById("pad");
  const saved = document.getElementById("saved");
  let timer = null;
  let dirty = false;
  const save = async () => {
    if (!dirty) return;
    dirty = false;
    saved.textContent = "saving…";
    try {
      const r = await fetch("/notepad", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: pad.value }) });
      saved.textContent = r.ok ? "saved" : "save failed";
    } catch {
      saved.textContent = "save failed (offline?)";
      dirty = true;
    }
  };
  pad.addEventListener("input", () => { dirty = true; saved.textContent = "editing…"; clearTimeout(timer); timer = setTimeout(save, 1000); });
  window.addEventListener("beforeunload", () => { if (dirty) navigator.sendBeacon("/notepad", new Blob([JSON.stringify({ text: pad.value })], { type: "application/json" })); });
}

// SoundFonts page: upload .sf2 files with a progress bar (they can be hundreds of MB).
function initSoundfontUpload() {
  const form = document.getElementById("sf-upload");
  const input = form.querySelector("input[type=file]");
  const drop = document.getElementById("drop");
  const picked = document.getElementById("picked");
  const bar = document.getElementById("bar");
  const msg = document.getElementById("msg");
  const go = document.getElementById("go");
  let files = [];
  const show = () => {
    picked.innerHTML = "";
    for (const f of files) {
      const li = document.createElement("li");
      li.textContent = `${f.name} · ${(f.size / 1048576).toFixed(1)} MB`;
      picked.appendChild(li);
    }
  };
  input.addEventListener("change", () => { files = [...input.files]; show(); });
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (ev) => { files = [...ev.dataTransfer.files]; show(); });
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!files.length) { msg.textContent = "Pick at least one .sf2 file."; return; }
    const data = new FormData();
    files.forEach((f) => data.append("files", f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/soundfonts/upload");
    xhr.setRequestHeader("Accept", "application/json");
    bar.hidden = false;
    go.disabled = true;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      bar.value = (100 * e.loaded) / e.total;
      if (e.loaded === e.total) msg.textContent = "Uploaded; reading and measuring the presets…";
    };
    xhr.onload = () => {
      go.disabled = false;
      bar.hidden = true; bar.value = 0;
      let r = {};
      try { r = JSON.parse(xhr.responseText); } catch { r = { problems: [xhr.responseText.slice(0, 300)] }; }
      const lines = [];
      if (r.saved && r.saved.length) lines.push(`Added ${r.saved.join(", ")}.`);
      (r.problems || []).forEach((p) => lines.push(p));
      (r.catalog_errors || []).forEach((e) => lines.push(`${e.file}: ${e.error}`));
      if (r.detail) lines.push(String(r.detail));
      msg.textContent = lines.join(" ") || `Upload failed (${xhr.status}).`;
      files = []; show(); form.reset();
      if (r.saved && r.saved.length) setTimeout(() => location.reload(), 1200);
    };
    xhr.onerror = () => { msg.textContent = "Upload failed: connection error."; go.disabled = false; };
    xhr.send(data);
  });
}

// Tests page: draw a MIDI file as a piano roll; follow (and seek) its rendered .wav when one is next to it.
function parseMidi(buf) {
  const d = new DataView(buf);
  let p = 0;
  const str = (n) => String.fromCharCode(...new Uint8Array(buf, p, n));
  const vlq = () => { let v = 0, b; do { b = d.getUint8(p++); v = (v << 7) | (b & 0x7f); } while (b & 0x80); return v; };
  if (str(4) !== "MThd") throw new Error("not a MIDI file");
  const ntracks = d.getUint16(p + 10), tpb = d.getUint16(p + 12);
  p += 8 + d.getUint32(p + 4);
  const notes = [], tempos = [[0, 500000]], names = [];
  for (let t = 0; t < ntracks && p < buf.byteLength; t++) {
    if (str(4) !== "MTrk") break;
    const end = p + 8 + d.getUint32(p + 4);
    p += 8;
    let tick = 0, status = 0;
    const open = {};
    while (p < end) {
      tick += vlq();
      let b = d.getUint8(p);
      if (b & 0x80) { status = b; p++; }
      if (status === 0xff) {
        const type = d.getUint8(p++), len = vlq();
        if (type === 0x51) tempos.push([tick, (d.getUint8(p) << 16) | (d.getUint8(p + 1) << 8) | d.getUint8(p + 2)]);
        if (type === 0x03 && names[t] === undefined) names[t] = str(len);
        p += len;
      } else if (status === 0xf0 || status === 0xf7) {
        p += vlq();
      } else {
        const kind = status & 0xf0, ch = status & 0x0f;
        const a = d.getUint8(p++), v = kind === 0xc0 || kind === 0xd0 ? 0 : d.getUint8(p++);
        const key = ch * 128 + a;
        if (kind === 0x90 && v > 0) (open[key] = open[key] || []).push([tick, v]);
        else if ((kind === 0x80 || kind === 0x90) && open[key] && open[key].length) {
          const [on, vel] = open[key].shift();
          notes.push({ track: t, ch, pitch: a, on, off: tick, vel });
        }
      }
    }
    p = end;
  }
  tempos.sort((x, y) => x[0] - y[0]);
  const sec = (tick) => {
    let s = 0, last = 0, tempo = 500000;
    for (const [tt, us] of tempos) { if (tt > tick) break; s += ((tt - last) / tpb) * tempo / 1e6; last = tt; tempo = us; }
    return s + ((tick - last) / tpb) * tempo / 1e6;
  };
  notes.forEach((n) => { n.start = sec(n.on); n.end = sec(n.off); });
  return { notes, names };
}

async function initPianoRolls(root = document) {
  for (const box of root.querySelectorAll(".pianoroll")) {
    const canvas = box.querySelector("canvas"), legend = box.querySelector(".legend");
    const audio = box.parentElement.querySelector("audio");
    let midi;
    try { midi = parseMidi(await (await fetch(box.dataset.src)).arrayBuffer()); }
    catch (e) { legend.textContent = `can't draw this file: ${e.message}`; continue; }
    const { notes, names } = midi;
    if (!notes.length) { legend.textContent = "no notes"; continue; }
    const tracks = [...new Set(notes.map((n) => n.track))];
    const color = (t) => `hsl(${(tracks.indexOf(t) * 137) % 360} 65% 62%)`;
    const total = Math.max(...notes.map((n) => n.end), audio && audio.duration || 0);
    const lo = Math.min(...notes.map((n) => n.pitch)) - 2, hi = Math.max(...notes.map((n) => n.pitch)) + 2;
    legend.innerHTML = tracks.map((t) => `<span style="color:${color(t)}">■ ${names[t] || "track " + t}</span>`).join(" ");
    const draw = (now) => {
      const w = (canvas.width = canvas.clientWidth * devicePixelRatio), h = (canvas.height = canvas.clientHeight * devicePixelRatio);
      const g = canvas.getContext("2d"), x = (s) => (s / total) * w, rowH = h / (hi - lo + 1);
      g.clearRect(0, 0, w, h);
      for (const n of notes) {
        g.globalAlpha = 0.35 + 0.65 * (n.vel / 127);
        g.fillStyle = n.ch === 9 ? "#bbb" : color(n.track);
        g.fillRect(x(n.start), h - (n.pitch - lo + 1) * rowH, Math.max(2, x(n.end) - x(n.start) - 1), Math.max(2, rowH - 1));
      }
      g.globalAlpha = 1;
      if (now != null) { g.fillStyle = "#fff"; g.fillRect(x(now), 0, 2 * devicePixelRatio, h); }
    };
    draw();
    if (audio) {
      const tick = () => { draw(audio.currentTime); if (!audio.paused) requestAnimationFrame(tick); };
      audio.addEventListener("play", tick);
      audio.addEventListener("seeked", () => draw(audio.currentTime));
      canvas.addEventListener("click", (ev) => {
        audio.currentTime = (ev.offsetX / canvas.clientWidth) * total;
        if (audio.paused) audio.play();
      });
    }
    window.addEventListener("resize", () => draw(audio ? audio.currentTime : null));
  }
}

// Make: a typed format with a list of the ones used before, exact length or a range, pickers with
// filters, voice previews, and files added from here uploaded to the library and attached.
// A typed box with a ▾ list (.combo: an input, a button, a ul). Typing narrows the list; picking an item
// fills the box with its data-value (or its text). The list can be refilled later with setItems.
function initCombo(box) {
  const input = box.querySelector("input"), button = box.querySelector("button"), list = box.querySelector("ul");
  const show = (all) => {
    const q = all ? "" : input.value.trim().toLowerCase();
    let any = false;
    list.querySelectorAll("li:not(.none)").forEach((li) => {
      li.hidden = q && !li.textContent.toLowerCase().includes(q);
      any = any || !li.hidden;
    });
    list.hidden = !any && !list.querySelector(".none");
  };
  button.addEventListener("click", () => {
    if (list.hidden) { show(true); input.focus(); } else list.hidden = true;
  });
  input.addEventListener("input", () => show(false));
  list.addEventListener("mousedown", (ev) => {
    const li = ev.target.closest("li:not(.none)");
    if (!li) return;
    ev.preventDefault();
    input.value = li.dataset.value || li.textContent;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    list.hidden = true;
  });
  input.addEventListener("blur", () => setTimeout(() => (list.hidden = true), 150));
  input.addEventListener("keydown", (ev) => { if (ev.key === "Escape") list.hidden = true; });
  box.setItems = (items) => {
    list.replaceChildren(...items.map(([value, text]) => {
      const li = document.createElement("li");
      li.dataset.value = value;
      li.textContent = text;
      return li;
    }));
  };
}

const usd = (v) => "$" + (v >= 1 ? v.toFixed(2) : String(+v.toPrecision(3)));

// The make form's model boxes and the video price box, from /api/prices (OpenRouter's live list).
async function loadPrices(form) {
  const videoBox = document.getElementById("video-prices");
  let data;
  try {
    data = await (await fetch("/api/prices")).json();
  } catch (e) {
    data = { error: e.message };
  }
  if (data.error) {
    videoBox.querySelector("p").textContent = "Prices unavailable right now: " + data.error;
    return;
  }
  const chat = Object.fromEntries(data.chat.map((m) => [m.id, m]));
  const rate = (m) => `${usd(m.in_per_m)} in / ${usd(m.out_per_m)} out`;
  form.querySelectorAll(".model-row").forEach((row) => {
    const input = row.querySelector("input"), needs = input.dataset.needs, price = row.querySelector(".price");
    const fit = data.chat.filter((m) => m.in_per_m !== null && (!needs || m.inputs.includes(needs)));
    const rank = (m) => (m.id === input.value ? 0 : m.id.startsWith("deepseek/") ? 1 : 2);
    fit.sort((a, b) => rank(a) - rank(b) || a.in_per_m - b.in_per_m);
    row.querySelector(".combo").setItems(fit.map((m) => [m.id, `${m.id}: ${rate(m)}`]));
    const showPrice = () => {
      const m = chat[input.value.trim()];
      price.textContent = m ? `${rate(m)} per million tokens`
        : input.value.trim() ? "not in OpenRouter's list of models that can answer in JSON" : "";
    };
    input.addEventListener("input", showPrice);
    input.addEventListener("change", showPrice);
    showPrice();
  });

  const line = (r, res) => {
    const p = document.createElement("p");
    p.className = "line small";
    p.textContent = `${r.label}: ${usd(r.usd)} ${r.unit}`;
    if (res && r.label.split(" ").includes(res)) p.classList.add("on");
    return p;
  };
  const parts = [];
  for (const m of data.video.filter((v) => v.in_use)) {
    const h = document.createElement("h3");
    h.textContent = m.name;
    const note = document.createElement("p");
    note.className = "muted small";
    note.textContent = `${m.id}, used at ${m.resolution || "its default"}`;
    parts.push(h, note, ...m.rates.map((r) => line(r, m.resolution)));
  }
  const others = document.createElement("details");
  others.innerHTML = "<summary class='small'>All video models</summary>";
  data.video.filter((v) => !v.in_use).sort((a, b) => (a.cheapest_per_s ?? 9) - (b.cheapest_per_s ?? 9)).forEach((m) => {
    const p = document.createElement("p");
    p.className = "line small";
    p.textContent = m.cheapest_per_s !== null ? `${m.name}: from ${usd(m.cheapest_per_s)} per second`
      : `${m.name}: ${m.rates.map((r) => `${usd(r.usd)} ${r.unit}`).join(", ")}`;
    others.append(p);
  });
  const note = document.createElement("p");
  note.className = "muted small";
  note.textContent = "Live from OpenRouter; the bold line is the resolution in use.";
  videoBox.replaceChildren(videoBox.querySelector("h2"), note, ...parts, others);
}

function initMake() {
  const form = document.getElementById("make");
  const min = form.elements.len_min;
  form.querySelectorAll(".combo").forEach(initCombo);
  loadPrices(form);

  // Exact uses only the second box; Range turns on the first (the shortest) too.
  const showLength = () => {
    const range = form.elements.len_mode.value === "range";
    min.disabled = !range;
    min.required = range;
    document.getElementById("len-max-name").textContent = range ? "Longest" : "Length";
  };
  form.querySelectorAll('input[name="len_mode"]').forEach((r) => r.addEventListener("change", showLength));
  showLength();

  const count = () => form.querySelectorAll("[data-count]").forEach((el) => {
    const n = form.querySelectorAll(`input[name="${el.dataset.count}"]:checked`).length;
    el.textContent = n ? `${n} picked` : "";
  });
  form.addEventListener("change", count);
  count();

  form.querySelectorAll(".filter").forEach((box) => box.addEventListener("input", () => {
    const q = box.value.trim().toLowerCase();
    document.getElementById(box.dataset.for).querySelectorAll(".pick").forEach((el) => {
      el.hidden = q && !el.dataset.name.includes(q) && !el.querySelector("input").checked;
    });
  }));

  const player = new Audio();
  form.querySelectorAll(".play").forEach((b) => b.addEventListener("click", (ev) => {
    ev.preventDefault();
    const again = player.src.endsWith(b.dataset.src) && !player.paused;
    player.pause();
    form.querySelectorAll(".play").forEach((x) => (x.textContent = "▶"));
    if (again) return;
    player.src = b.dataset.src; player.play(); b.textContent = "■";
    player.onended = () => (b.textContent = "▶");
  }));

  const attach = document.getElementById("attach"), msg = document.getElementById("attach-msg");
  const grid = document.getElementById("asset-grid");
  attach.addEventListener("change", async () => {
    const files = [...attach.files];
    attach.value = "";
    msg.hidden = false;
    for (const f of files) {
      const category = f.type.startsWith("image/") ? "image" : f.type.startsWith("video/") ? "footage" : "music";
      const data = new FormData();
      data.append("files", f, f.name);
      data.append("category", category);
      msg.textContent = `Uploading ${f.name}…`;
      try {
        const r = await fetch("/upload", { method: "POST", body: data, headers: { Accept: "application/json" } });
        if (!r.ok) throw new Error(`${r.status}`);
        for (const id of (await r.json()).assets) {
          const card = document.createElement("label");
          card.className = "pick";
          card.dataset.name = f.name.toLowerCase();
          card.innerHTML = `<input type="checkbox" name="assets" value="${id}" checked>` +
            `<span class="pthumb"><span class="ph">${category}</span><span class="kind">new</span></span><span class="pname"></span>`;
          card.querySelector(".pname").textContent = f.name;
          grid.prepend(card);
        }
        msg.textContent = `Added ${f.name}; it's processed in the background.`;
      } catch (e) {
        msg.textContent = `Upload of ${f.name} failed (${e.message}).`;
      }
    }
    count();
  });
}

// Tests page: a folder's file list is fetched when it's opened, and each file's player, picture or
// text is only created when its button is clicked, so nothing heavy loads on its own.
function initTests() {
  document.addEventListener("toggle", async (e) => {
    const box = e.target;
    if (!box.matches || !box.matches("details.test") || !box.open || box.dataset.loaded) return;
    box.dataset.loaded = "1";
    const body = box.querySelector(":scope > .folder-body");
    const q = new URLSearchParams({ rel: box.dataset.folder, area: box.dataset.area || "" });
    try {
      const r = await fetch(`/tests/folder?${q}`);
      if (!r.ok) throw new Error(r.status);
      body.classList.remove("muted", "small");
      body.innerHTML = await r.text();
    } catch (err) {
      body.textContent = `couldn't load this folder (${err.message})`;
      delete box.dataset.loaded;
    }
  }, true);

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".tfile .show");
    if (!btn) return;
    const file = btn.closest(".tfile"), src = file.dataset.src;
    const shown = file.querySelector(".shown");
    if (shown) { shown.remove(); btn.textContent = btn.dataset.label; return; }  // click again to close
    btn.dataset.label = btn.textContent;
    btn.textContent = "hide";
    const wrap = document.createElement("div");
    wrap.className = "shown";
    const kind = file.dataset.kind;
    if (kind === "video" || kind === "audio") {
      const m = document.createElement(kind);
      m.controls = true; m.autoplay = true; m.src = src;
      wrap.append(m);
    } else if (kind === "image") {
      const a = document.createElement("a"), img = document.createElement("img");
      a.href = src; a.target = "_blank"; img.src = src;
      a.append(img); wrap.append(a);
    } else if (kind === "text") {
      const pre = document.createElement("pre");
      pre.textContent = "loading…";
      wrap.append(pre);
      fetch(src).then((r) => r.text()).then((t) => { pre.textContent = t.slice(0, 20000); });
    } else if (kind === "midi") {
      wrap.innerHTML = '<div class="pianoroll"><div class="legend muted"></div><canvas></canvas></div>';
      wrap.firstChild.dataset.src = src;
      if (file.dataset.render) {
        const a = document.createElement("audio");
        a.controls = true; a.src = file.dataset.render;
        wrap.append(a);
      } else {
        wrap.insertAdjacentHTML("beforeend", '<p class="muted small">No rendered .wav next to this file; download it to play it in a MIDI player.</p>');
      }
      file.append(wrap);
      initPianoRolls(wrap);
      return;
    }
    file.append(wrap);
  });
}
