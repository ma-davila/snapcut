// Opening animation. Each half of the screen holds its own copy of the mark,
// positioned so the cut line sits exactly on the seam; both copies animate in
// lockstep. The cut repeats until app.js says the week has loaded
// (opening.ready), then the halves part along that line. If it can't load,
// opening.fail leaves the mark on screen with the reason and a retry button.
(() => {
  const intro = document.getElementById("intro");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let seen = false;
  try { seen = sessionStorage.getItem("intro-seen") === "1"; } catch {}
  if (reduce || seen) return;

  const ROUND = 1920; // one cut, as timed in style.css
  const HOLD = 600;   // the finished cut stays this long before the next round
  const FADE = 350;   // the mark fading out between rounds

  const tpl = document.getElementById("intro-mark");
  for (const half of intro.querySelectorAll(".half")) {
    const svg = tpl.content.firstElementChild.cloneNode(true);
    // Each copy needs its own clipPath id.
    const id = `cut-reveal-${half.classList.contains("top") ? "t" : "b"}`;
    svg.querySelector("clipPath").id = id;
    svg.querySelector("[clip-path]").setAttribute("clip-path", `url(#${id})`);
    half.append(svg);
  }
  intro.hidden = false;
  document.documentElement.classList.add("intro-playing");

  let loaded = false;
  let cut = false; // the current round has finished cutting
  let timer;

  function round() {
    cut = false;
    intro.classList.remove("run");
    void intro.offsetWidth; // restart the animations
    intro.classList.add("run");
    intro.classList.remove("rest");
    timer = setTimeout(() => {
      cut = true;
      if (loaded) open();
      else timer = setTimeout(rest, HOLD);
    }, ROUND);
  }
  function rest() {
    cut = false;
    intro.classList.add("rest");
    timer = setTimeout(round, FADE);
  }
  function open() {
    intro.classList.add("open");
    setTimeout(done, 2000); // safety net if animations never fire
  }
  function done() {
    clearTimeout(timer);
    intro.remove();
    document.documentElement.classList.remove("intro-playing");
    delete window.opening;
    try { sessionStorage.setItem("intro-seen", "1"); } catch {}
  }

  intro.querySelector(".bottom").addEventListener("animationend", (e) => {
    if (e.animationName === "part-down") done();
  });
  const skip = () => { if (intro.classList.contains("open")) intro.classList.add("skip"); };
  intro.addEventListener("click", skip);
  addEventListener("keydown", skip);

  window.opening = {
    ready() {
      loaded = true;
      if (cut) { clearTimeout(timer); open(); }
    },
    fail(message, retry) {
      clearTimeout(timer);
      intro.classList.remove("rest");
      intro.classList.add("skip"); // the mark, drawn and still
      const box = document.createElement("div");
      box.className = "fail";
      box.setAttribute("role", "alert");
      const p = document.createElement("p");
      p.textContent = message;
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = "Reintentar";
      b.addEventListener("click", retry);
      box.append(p, b);
      intro.querySelector(".bottom").append(box);
      intro.removeAttribute("aria-hidden");
      b.focus();
    },
    retrying() {
      intro.querySelector(".fail")?.remove();
      intro.setAttribute("aria-hidden", "true");
      intro.classList.remove("skip");
      round();
    },
  };
  round();
})();
