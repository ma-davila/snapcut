const $ = (s, el = document) => el.querySelector(s);
const days = $("#days");
const tpl = $("#card-tpl");

let data = null;          // current week payload
let polling = null;

const STAGE = {
  queued: "En cola",
  downloading: "Descargando de YouTube",
  analyzing: "Buscando las jugadas",
  rendering: "Montando el vídeo",
};

// Scores the viewer has already chosen to see, remembered per browser.
const revealed = new Set(load("revealed", []));
function load(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}

const mmss = (s) => {
  s = Math.round(s);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

function weekLabel(d) {
  const entry = d.calendar.find((c) => c.seasontype === d.seasontype && c.week === d.week);
  if (!entry) return `Semana ${d.week}`;
  const m = entry.label.match(/^Week (\d+)$/);
  if (m) return `Semana ${m[1]}`;
  return {
    "Wild Card": "Wild Card", "Divisional Round": "Divisional",
    "Conference Championship": "Final de conferencia", "Super Bowl": "Super Bowl",
    "Pro Bowl": "Pro Bowl",
  }[entry.label] ?? entry.label.replace("Preseason Week", "Pretemporada");
}

async function loadWeek(params = {}) {
  const q = new URLSearchParams(params).toString();
  $("#week-title").textContent = "Cargando…";
  try {
    const r = await fetch(`/api/week${q ? "?" + q : ""}`);
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    data = await r.json();
  } catch (e) {
    days.innerHTML = "";
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = `No se pudo cargar la semana: ${e.message}. Comprueba la conexión y recarga.`;
    days.append(p);
    $("#week-title").textContent = "—";
    return;
  }
  save("week", { season: data.season, seasontype: data.seasontype, week: data.week });
  render();
  poll();
}

function neighbor(step) {
  const i = data.calendar.findIndex((c) => c.seasontype === data.seasontype && c.week === data.week);
  return data.calendar[i + step];
}

function render() {
  $("#week-title").textContent = weekLabel(data);
  $("#prev").disabled = !neighbor(-1);
  $("#next").disabled = !neighbor(1);

  const byDay = new Map();
  const sorted = [...data.games].sort((a, b) => new Date(a.date) - new Date(b.date));
  for (const g of sorted) {
    const d = new Date(g.date);
    const key = d.toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(g);
  }

  days.innerHTML = "";
  if (!data.games.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "No hay partidos esta semana.";
    days.append(p);
    return;
  }
  for (const [day, list] of byDay) {
    const sec = document.createElement("section");
    sec.className = "day";
    const h = document.createElement("h2");
    h.textContent = day[0].toUpperCase() + day.slice(1);
    const grid = document.createElement("div");
    grid.className = "grid";
    for (const g of list) grid.append(card(g));
    sec.append(h, grid);
    days.append(sec);
  }
}

function card(g) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  el.dataset.id = g.id;
  const final = g.state === "post";
  el.classList.toggle("final", final);
  el.classList.toggle("revealed", final && revealed.has(g.id));

  const kick = new Date(g.date).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
  $(".kick", el).textContent =
    g.state === "pre" ? kick : g.state === "in" ? "En juego" : "Terminado";
  $(".net", el).textContent = g.network ?? "";

  for (const side of ["away", "home"]) {
    const t = g[side];
    const row = $(`.team.${side}`, el);
    row.style.setProperty("--team", t.color);
    $(".abbr", row).textContent = t.abbr;
    $(".name", row).textContent = t.short;
    $(".name", row).title = t.name;
    $(".score", row).textContent = final ? t.score : "";
    row.classList.toggle("win", final && t.winner);
  }
  // "Final/OT" gives away a close game, so it stays behind the shutter too.
  $(".detail", el).textContent = final && /OT/.test(g.status) ? "Con prórroga" : "";

  $(".shutter", el).setAttribute("aria-label", `Ver resultado de ${g.away.name} en ${g.home.name}`);
  $(".shutter", el).addEventListener("click", () => {
    revealed.add(g.id);
    save("revealed", [...revealed]);
    el.classList.add("revealed");
  });

  renderAction(el, g);
  return el;
}

function renderAction(el, g) {
  const box = $(".action", el);
  box.innerHTML = "";
  const job = g.job;

  if (g.state !== "post") {
    box.append(text("muted", g.state === "in" ? "El vídeo estará cuando acabe el partido." : ""));
    return;
  }
  if (!g.video) {
    box.append(text("muted", "La NFL aún no ha subido los highlights."));
    return;
  }

  if (!job || !job.stage) {
    if (g.cuts) {
      // Cut points already published: only the download and the render are left.
      box.append(button("btn", "Generar vídeo", () => generate(g)));
      box.append(text("fast", "Rápido"));
      box.append(text("muted small", "Jugadas ya localizadas: no hay que analizar."));
    } else if (g.supported) {
      box.append(button("btn", "Generar vídeo", () => generate(g)));
    } else {
      box.append(text("muted", `Aún no sé leer el marcador de ${g.network ?? "esta cadena"}.`));
      box.append(button("link-btn", "Probar igualmente", () => generate(g)));
    }
    box.append(ytLink(g));
    return;
  }
  if (job.stage === "done") {
    box.append(button("btn dark", `Ver vídeo (${mmss(job.kept)})`, () => play(g)));
    box.append(text("muted", `${job.plays} jugadas, de ${mmss(job.original)}`));
    return;
  }
  if (job.stage === "expired") {
    box.append(text("muted", "El vídeo se borró para ahorrar espacio."));
    box.append(button("btn", "Volver a generar", () => generate(g)));
    return;
  }
  if (job.stage === "error") {
    box.append(text("error", job.error || "No se pudo generar el vídeo."));
    box.append(button("btn", "Reintentar", () => generate(g)));
    box.append(ytLink(g));
    return;
  }
  const p = document.createElement("div");
  p.className = "progress" + (job.stage === "analyzing" || job.stage === "queued" ? " indeterminate" : "");
  p.innerHTML = `<div class="label"><span></span><span></span></div><div class="track"><div class="fill"></div></div>`;
  $(".label span", p).textContent = STAGE[job.stage] ?? job.stage;
  $(".label span:last-child", p).textContent =
    job.stage === "downloading" || job.stage === "rendering" ? `${Math.round(job.progress * 100)}%` : "";
  $(".fill", p).style.width = `${Math.round(job.progress * 100)}%`;
  box.append(p);
}

function text(cls, s) {
  const span = document.createElement("span");
  span.className = cls;
  span.textContent = s;
  return span;
}
function button(cls, label, fn) {
  const b = document.createElement("button");
  b.className = cls;
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", fn);
  return b;
}
function ytLink(g) {
  const a = document.createElement("a");
  a.className = "link";
  a.href = `https://www.youtube.com/watch?v=${g.video.id}`;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = `Original en YouTube (${mmss(g.video.duration ?? 0)})`;
  return a;
}

async function generate(g) {
  const r = await fetch(`/api/games/${g.id}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_id: g.video.id,
      network: g.network,
      week: [data.season, data.seasontype, data.week],
      teams: `${g.away.short} - ${g.home.short}`,
    }),
  });
  g.job = await r.json();
  refreshCard(g);
  poll();
}

// Games cut in the background show up without a reload.
async function refreshJobs() {
  if (!data || document.hidden) return;
  const q = new URLSearchParams({ season: data.season, seasontype: data.seasontype, week: data.week });
  try {
    const fresh = await (await fetch(`/api/week?${q}`)).json();
    for (const f of fresh.games) {
      const g = data.games.find((x) => x.id === f.id);
      if (!g) continue;
      const before = JSON.stringify([g.job?.stage, g.video?.id, g.state]);
      Object.assign(g, { job: f.job, video: f.video, cuts: f.cuts, state: f.state });
      if (JSON.stringify([g.job?.stage, g.video?.id, g.state]) !== before) refreshCard(g);
    }
    poll();
  } catch {}
}
setInterval(refreshJobs, 60_000);
document.addEventListener("visibilitychange", refreshJobs);

function refreshCard(g) {
  const el = days.querySelector(`.card[data-id="${g.id}"]`);
  if (el) renderAction(el, g);
}

// Poll every active job until it finishes.
function poll() {
  clearTimeout(polling);
  const active = data.games.filter((g) => g.job && !["done", "error", null].includes(g.job.stage));
  if (!active.length) return;
  polling = setTimeout(async () => {
    await Promise.all(active.map(async (g) => {
      try {
        g.job = await (await fetch(`/api/games/${g.id}/job`)).json();
        refreshCard(g);
      } catch {}
    }));
    poll();
  }, 1500);
}

// ---------- player ----------

const dlg = $("#player");
const frame = $(".frame", dlg);
const video = $("#video");
const timeline = $("#timeline");
const segsEl = $(".segs", timeline);
const tip = $(".tip", timeline);
const REWIND_GRACE = 1.5;  // seconds into a play before ← restarts it instead of going back
const IDLE_MS = 2500;

let plays = null;   // {starts, duration} from the server, on the cut's nominal clock
let starts = [0];   // play starts scaled to the real video duration
let current = -1;
let session = 0;    // guards against a late /plays reply for a game already closed
let idleTimer = null;
let dragging = false;

const clock = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

async function play(g) {
  const id = ++session;
  showEndCard(false);
  saved = g.job && g.job.original && g.job.kept ? g.job.original - g.job.kept : 0;
  $("#player-title").textContent = `${g.away.short} en ${g.home.short}`;
  plays = null;
  layout();
  video.volume = load("volume", 1);
  video.muted = load("muted", false);
  video.src = `/media/${g.id}.mp4`;
  dlg.showModal();
  wake();
  video.play().catch(() => {});
  try {
    const r = await fetch(`/api/games/${g.id}/plays`);
    if (r.ok && id === session) {
      plays = await r.json();
      layout();
    }
  } catch {}
}

// ---------- end of video: time saved, and a donation ask ----------

let saved = 0;          // seconds cut from the original
let donateUrl = null;
const endCard = $("#end-card");

function showEndCard(on) {
  endCard.hidden = !on;
  frame.classList.toggle("ended", on);
  if (!on) return;
  const min = Math.round(saved / 60);
  $(".saved", endCard).textContent = min >= 1
    ? `Te has ahorrado ${min} ${min === 1 ? "minuto" : "minutos"}.`
    : "Fin del partido.";
  $(".ask", endCard).hidden = !donateUrl;
  $("#end-donate").hidden = !donateUrl;
  if (donateUrl) $("#end-donate").href = donateUrl;
}

video.addEventListener("ended", () => showEndCard(true));
video.addEventListener("seeking", () => { if (!video.ended) showEndCard(false); });
video.addEventListener("play", () => showEndCard(false));
$("#end-replay").addEventListener("click", () => { seek(0); video.play().catch(() => {}); });

const about = $("#about");
$("#about-open").addEventListener("click", async () => {
  about.showModal();
  const box = $("#about-licenses");
  if (box.dataset.loaded) return;
  const r = await fetch("/licenses.txt").catch(() => null);
  if (!r?.ok) return;
  $("pre", box).textContent = await r.text();
  box.dataset.loaded = "1";
  box.hidden = false;
});

fetch("/api/config").then((r) => r.json()).then((c) => {
  if (c.version) $("#about-version").textContent = `versión ${c.version}`;
  donateUrl = c.donate_url;
  if (!donateUrl) return;
  const a = $("#donate");
  a.href = donateUrl;
  a.hidden = false;
}).catch(() => {});

function closePlayer() {
  session++;
  video.pause();
  video.removeAttribute("src");
  video.load();
  clearTimeout(idleTimer);
  if (fullscreenElement()) exitFullscreen();
  if (dlg.open) dlg.close();
}

// Frame rounding makes the real video drift a little from the summed
// segments, so the starts are stretched to fit it.
function layout() {
  const dur = video.duration;
  starts = plays && plays.starts.length && dur
    ? plays.starts.map((s) => s * dur / plays.duration)
    : [0];
  segsEl.innerHTML = "";
  starts.forEach((s, i) => {
    const seg = document.createElement("div");
    seg.className = "seg";
    const end = starts[i + 1] ?? dur;
    seg.style.left = `${(s / dur) * 100 || 0}%`;
    seg.style.width = `${((end - s) / dur) * 100 || 100}%`;
    segsEl.append(seg);
  });
  current = -1;
  update();
}

function playAt(t) {
  let i = 0;
  while (i + 1 < starts.length && starts[i + 1] <= t + 0.05) i++;
  return i;
}

function update() {
  const dur = video.duration || 0;
  const t = video.currentTime;
  const i = playAt(t);
  const segs = segsEl.children;
  if (i !== current) {
    [...segs].forEach((seg, k) => {
      seg.classList.toggle("current", k === i);
      seg.style.setProperty("--p", k < i ? "100%" : "0%");
    });
    current = i;
  }
  const end = starts[i + 1] ?? dur;
  if (segs[i]) segs[i].style.setProperty("--p", `${Math.min(100, ((t - starts[i]) / (end - starts[i])) * 100 || 0)}%`);
  $(".head", timeline).style.left = `${dur ? (t / dur) * 100 : 0}%`;
  $("#p-time").textContent = `${clock(t)} / ${clock(dur)}`;
  const count = plays && dur ? `Jugada ${i + 1} de ${starts.length}` : "";
  $("#p-count").textContent = count;
  timeline.setAttribute("aria-valuemax", Math.round(dur));
  timeline.setAttribute("aria-valuenow", Math.round(t));
  timeline.setAttribute("aria-valuetext", `${count ? count + ", " : ""}${clock(t)}`);
  $("#p-prev").disabled = !dur;
  $("#p-next").disabled = !dur || i + 1 >= starts.length;
}

function tick() {
  update();
  if (!video.paused) requestAnimationFrame(tick);
}

function seek(t) {
  if (!video.duration) return;
  video.currentTime = Math.min(Math.max(0, t), video.duration - 0.05);
  update();
  wake();
}

function prevPlay() {
  const i = playAt(video.currentTime);
  seek(video.currentTime - starts[i] > REWIND_GRACE ? starts[i] : starts[Math.max(0, i - 1)]);
}
function nextPlay() {
  const i = playAt(video.currentTime);
  if (i + 1 < starts.length) seek(starts[i + 1]);
}

function togglePlay() {
  if (video.paused || video.ended) video.play().catch(() => {});
  else video.pause();
  wake();
}

function syncPlaying() {
  const playing = !video.paused;
  frame.classList.toggle("playing", playing);
  $("#p-play").setAttribute("aria-label", playing ? "Pausa (espacio)" : "Reproducir (espacio)");
  wake();
  if (playing) requestAnimationFrame(tick);
}

function syncVolume() {
  const silent = video.muted || video.volume === 0;
  frame.classList.toggle("muted", silent);
  $("#p-vol").value = video.muted ? 0 : video.volume;
  $("#p-mute").setAttribute("aria-label", silent ? "Activar sonido (M)" : "Silenciar (M)");
  save("volume", video.volume);
  save("muted", video.muted);
}
function toggleMute() {
  if (video.muted || video.volume === 0) {
    video.muted = false;
    if (video.volume === 0) video.volume = 1;
  } else {
    video.muted = true;
  }
}

// Safari still needs the prefixed API; iPhone only has fullscreen for the <video> itself.
const fullscreenElement = () => document.fullscreenElement ?? document.webkitFullscreenElement;
function exitFullscreen() {
  (document.exitFullscreen ?? document.webkitExitFullscreen).call(document);
}
function toggleFullscreen() {
  if (fullscreenElement()) return exitFullscreen();
  const enter = frame.requestFullscreen ?? frame.webkitRequestFullscreen;
  if (enter) enter.call(frame);
  else video.webkitEnterFullscreen?.();
}
function syncFullscreen() {
  frame.classList.toggle("full", fullscreenElement() === frame);
  $("#p-full").setAttribute("aria-label",
    fullscreenElement() ? "Salir de pantalla completa (F)" : "Pantalla completa (F)");
}

// Controls fade out while playing and come back on any activity.
function wake() {
  frame.classList.remove("idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    if (!video.paused && !dragging && !frame.matches(":has(.hud-bottom:hover)")) frame.classList.add("idle");
  }, IDLE_MS);
}

function timeAtPointer(e) {
  const r = timeline.getBoundingClientRect();
  const x = Math.min(Math.max(0, e.clientX - r.left), r.width);
  return { t: (x / r.width) * (video.duration || 0), x };
}
function showTip(e) {
  if (!video.duration) return;
  const { t, x } = timeAtPointer(e);
  const n = plays ? `Jugada ${playAt(t) + 1} · ` : "";
  tip.textContent = `${n}${clock(t)}`;
  tip.style.left = `${x}px`;
  tip.hidden = false;
}

timeline.addEventListener("pointerdown", (e) => {
  dragging = true;
  timeline.setPointerCapture(e.pointerId);
  seek(timeAtPointer(e).t);
  showTip(e);
});
timeline.addEventListener("pointermove", (e) => {
  if (dragging) seek(timeAtPointer(e).t);
  if (dragging || e.pointerType === "mouse") showTip(e);
});
timeline.addEventListener("pointerup", () => { dragging = false; });
timeline.addEventListener("lostpointercapture", () => { dragging = false; tip.hidden = true; });
timeline.addEventListener("pointerleave", () => { if (!dragging) tip.hidden = true; });

// A tap on a sleeping touch screen only brings the controls back.
video.addEventListener("pointerup", (e) => {
  if (e.pointerType !== "mouse" && frame.classList.contains("idle")) return wake();
  togglePlay();
});
video.addEventListener("dblclick", toggleFullscreen);
frame.addEventListener("pointermove", wake);

video.addEventListener("loadedmetadata", layout);
video.addEventListener("play", syncPlaying);
video.addEventListener("pause", syncPlaying);
video.addEventListener("ended", syncPlaying);
video.addEventListener("timeupdate", update);
video.addEventListener("volumechange", syncVolume);
document.addEventListener("fullscreenchange", syncFullscreen);
document.addEventListener("webkitfullscreenchange", syncFullscreen);

$("#p-play").addEventListener("click", togglePlay);
$("#p-prev").addEventListener("click", prevPlay);
$("#p-next").addEventListener("click", nextPlay);
$("#p-mute").addEventListener("click", toggleMute);
$("#p-full").addEventListener("click", toggleFullscreen);
$("#p-close").addEventListener("click", closePlayer);
$("#p-vol").addEventListener("input", (e) => {
  video.volume = Number(e.target.value);
  video.muted = video.volume === 0;
});
dlg.addEventListener("close", closePlayer);

dlg.addEventListener("keydown", (e) => {
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  const onRange = e.target.type === "range";
  const act = {
    " ": togglePlay,
    ArrowLeft: onRange ? null : prevPlay,
    ArrowRight: onRange ? null : nextPlay,
    f: toggleFullscreen,
    m: toggleMute,
  }[e.key.length === 1 ? e.key.toLowerCase() : e.key];
  if (!act) return;
  e.preventDefault();
  act();
  wake();
});

$("#prev").addEventListener("click", () => {
  const n = neighbor(-1);
  if (n) loadWeek({ season: data.season, seasontype: n.seasontype, week: n.week });
});
$("#next").addEventListener("click", () => {
  const n = neighbor(1);
  if (n) loadWeek({ season: data.season, seasontype: n.seasontype, week: n.week });
});

loadWeek();
