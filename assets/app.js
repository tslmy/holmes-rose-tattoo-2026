// Interactive figures for the walk-through. No dependencies; every figure works
// without JavaScript as a static image or list, and this file only adds controls.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const press = (btns, on) => btns.forEach((b) => b.setAttribute("aria-pressed", String(b === on)));

  // Before/after comparison: drag (or arrow keys on the range input) to move the split.
  // A .pick row of buttons swaps the right-hand image and the verdict text.
  $$("figure[data-compare]").forEach((fig) => {
    const box = $(".compare", fig);
    const range = $("input[type=range]", box);
    const set = (v) => box.style.setProperty("--split", v + "%");
    range.addEventListener("input", () => set(range.value));
    set(range.value);
    const after = $("img.after", box);
    const tagR = $(".tag.r", box);
    const verdict = $(".verdict", fig);
    const btns = $$(".pick button", fig);
    const choose = (b) => {
      press(btns, b);
      after.src = b.dataset.src;
      after.alt = b.dataset.alt || b.textContent.trim();
      tagR.textContent = b.dataset.tag;
      if (verdict) verdict.innerHTML = $("template", b)?.innerHTML ?? "";
    };
    btns.forEach((b) => b.addEventListener("click", () => choose(b)));
    if (btns.length) choose(btns.find((b) => b.getAttribute("aria-pressed") === "true") || btns[0]);
  });

  // Generic "click a button, show its detail" figures (timeline, pipeline, iterations).
  $$("figure[data-tabs]").forEach((fig) => {
    const btns = $$("[data-key]", fig).filter((b) => b.tagName === "BUTTON");
    const out = $(".detail", fig);
    const choose = (b) => {
      press(btns, b);
      out.innerHTML = $("template", b).innerHTML;
      const range = b.dataset.range;
      if (range) {
        const [a, z] = range.split("-").map(Number);
        $$(".commits span", fig).forEach((s, i) => s.classList.toggle("on", i >= a && i <= z));
      }
    };
    btns.forEach((b) => b.addEventListener("click", () => choose(b)));
    choose(btns.find((b) => b.getAttribute("aria-pressed") === "true") || btns[0]);
  });

  // Commit histogram for the timeline: weeks from 2026-08-03 to 2026-11-23.
  const hist = $("#commit-hist");
  if (hist) {
    const counts = JSON.parse(hist.dataset.counts);
    const max = Math.max(...counts);
    hist.innerHTML = counts
      .map((c, i) => `<span style="height:${Math.max(2, (c / max) * 100)}%" title="week ${i + 1}: ${c} commits"></span>`)
      .join("");
  }

  // Phase 1 bake-off: one row per backend and level, metric chosen by buttons.
  const bake = $("#bakeoff-chart");
  if (bake) {
    const rows = [
      ["FLUX.2 klein 4B · L0", 0, 23, 74],
      ["FLUX.2 klein 4B · L1", 0, 31, 97],
      ["FLUX.2 klein 4B · L2", 0, 37, 11],
      ["FLUX.2 klein 9B · L0", 0, 24, 19],
      ["FLUX.2 klein 9B · L1", 0, 33, 110],
      ["FLUX.2 klein 9B · L2", 2, 42, 11],
      ["FLUX.3 image klein · L2", 0, 15, 2],
    ];
    const metrics = {
      passed: { i: 1, max: 7, fmt: (v) => `${v} / 7`, hot: (v) => v > 0,
        note: "Rooms (of 7 golden rooms) whose redraw passed every gate. Only the 9B at L2 passed any: scenes 4 and 9." },
      change: { i: 2, max: 50, fmt: (v) => `${v} / 255`, hot: (v) => v < 20,
        note: "Mean absolute pixel change from the original. Red bars sit under 20, the minimum-change gate added after this run: FLUX.3 L2 returned the input almost unchanged and would otherwise have won." },
      drift: { i: 3, max: 110, fmt: (v) => `${v} px`, hot: (v) => v > 4,
        note: "Median 95th-percentile geometry drift. L0/L1 (image-to-image at strength 0.6 and 0.45) must stay under 2 and 4 px; they moved furniture by 19-110 px. L2 is judged after the fitted room warp instead." },
    };
    const grid = $(".bars", bake);
    const note = $(".detail", bake);
    const btns = $$(".row button", bake);
    const draw = (key) => {
      const m = metrics[key];
      grid.innerHTML = rows
        .map((r) => {
          const v = r[m.i];
          return `<span>${r[0]}</span><span class="track"><span class="fill${m.hot(v) ? " hot" : ""}" style="width:${(v / m.max) * 100}%"></span></span><span class="v">${m.fmt(v)}</span>`;
        })
        .join("");
      note.textContent = m.note;
    };
    btns.forEach((b) => b.addEventListener("click", () => { press(btns, b); draw(b.dataset.metric); }));
    draw("passed");
  }
})();
