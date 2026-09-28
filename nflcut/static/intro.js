// Opening animation. Each half of the screen holds its own copy of the mark,
// positioned so the cut line sits exactly on the seam; both copies animate in
// lockstep, then the halves part along that line.
(() => {
  const intro = document.getElementById("intro");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let seen = false;
  try { seen = sessionStorage.getItem("intro-seen") === "1"; } catch {}
  if (reduce || seen) return;

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

  const done = () => {
    intro.remove();
    document.documentElement.classList.remove("intro-playing");
    try { sessionStorage.setItem("intro-seen", "1"); } catch {}
  };
  const skip = () => intro.classList.add("skip");
  intro.querySelector(".bottom").addEventListener("animationend", (e) => {
    if (e.animationName === "part-down") done();
  });
  intro.addEventListener("click", skip);
  addEventListener("keydown", skip, { once: true });
  setTimeout(done, 3500); // safety net if animations never fire
})();
