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

const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

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
    box.append(button("btn", "Generar vídeo", () => generate(g)));
    box.append(ytLink(g));
    return;
  }
  if (job.stage === "done") {
    box.append(button("btn dark", `Ver vídeo (${mmss(job.kept)})`, () => play(g)));
    box.append(text("muted", `${job.plays} jugadas, de ${mmss(job.original)}`));
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
    body: JSON.stringify({ video_id: g.video.id, network: g.network }),
  });
  g.job = await r.json();
  refreshCard(g);
  poll();
}

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
const video = $("#video");
function play(g) {
  $("#player-title").textContent = `${g.away.short} en ${g.home.short}`;
  video.src = `/media/${g.id}.mp4`;
  dlg.showModal();
  video.play().catch(() => {});
}
function closePlayer() {
  video.pause();
  video.removeAttribute("src");
  video.load();
  if (dlg.open) dlg.close();
}
$("#player-close").addEventListener("click", closePlayer);
dlg.addEventListener("close", closePlayer);

$("#prev").addEventListener("click", () => {
  const n = neighbor(-1);
  if (n) loadWeek({ season: data.season, seasontype: n.seasontype, week: n.week });
});
$("#next").addEventListener("click", () => {
  const n = neighbor(1);
  if (n) loadWeek({ season: data.season, seasontype: n.seasontype, week: n.week });
});

loadWeek();
