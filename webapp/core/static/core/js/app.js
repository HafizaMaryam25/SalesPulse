/* SalesPulse page behaviour: theme toggle, mobile nav, upload drop zone,
   entrance staggering, number count-up, sliding segmented controls, dashboard charts. */
(function () {
  "use strict";
  const root = document.documentElement;
  const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- entrance stagger: give each item its position as --i ---------- */
  function stagger(selector, max) {
    document.querySelectorAll(selector).forEach((parent) => {
      Array.from(parent.children).slice(0, max || 24).forEach((child, i) => child.style.setProperty("--i", i));
    });
  }
  stagger(".grid"); stagger(".main"); stagger(".stack"); stagger(".ranks", 12);
  stagger(".data tbody", 20); stagger(".nav", 12);

  /* ---------- KPI count-up: numbers roll up to their value on page load ---------- */
  function countUp(node) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let text;
    while ((text = walker.nextNode()) && !/\d/.test(text.nodeValue)) { /* find the first number */ }
    if (!text) return;
    const m = text.nodeValue.match(/-?[\d,]*\.?\d+/);
    if (!m) return;
    const raw = m[0], target = parseFloat(raw.replace(/,/g, ""));
    if (!isFinite(target) || target === 0) return;
    const decimals = (raw.split(".")[1] || "").length, grouped = raw.includes(",");
    const before = text.nodeValue.slice(0, m.index), after = text.nodeValue.slice(m.index + raw.length);
    const format = (v) => before + v.toLocaleString("en-US", {
      minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: grouped }) + after;
    const final = text.nodeValue, start = performance.now(), dur = 1100;
    node.classList.add("counting");
    (function frame(now) {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 4);
      text.nodeValue = t < 1 ? format(target * eased) : final;
      if (t < 1) requestAnimationFrame(frame); else node.classList.remove("counting");
    })(start);
  }
  if (!reducedMotion()) document.querySelectorAll(".hero-value, .metric-value, .stat-value").forEach(countUp);

  /* ---------- segmented controls: a thumb that slides to the chosen option ---------- */
  document.querySelectorAll(".segmented").forEach((seg) => {
    const thumb = document.createElement("span");
    thumb.className = "seg-thumb";
    thumb.setAttribute("aria-hidden", "true");
    seg.prepend(thumb);
    seg.classList.add("has-thumb", "no-anim");
    const activeItem = () => seg.querySelector('[aria-current="true"], [aria-pressed="true"]');
    function place(item) {
      item = item || activeItem();
      if (!item) { thumb.style.width = "0"; return; }
      thumb.style.width = item.offsetWidth + "px";
      thumb.style.transform = `translateX(${item.offsetLeft}px)`;
    }
    place();
    requestAnimationFrame(() => requestAnimationFrame(() => seg.classList.remove("no-anim")));
    // links: slide immediately on click, so there is feedback while the next page loads
    seg.querySelectorAll("a").forEach((a) => a.addEventListener("click", (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      seg.querySelectorAll("a").forEach((x) => x.setAttribute("aria-current", String(x === a)));
      place(a);
    }));
    new MutationObserver(() => place()).observe(seg, { subtree: true, attributes: true, attributeFilter: ["aria-pressed", "aria-current"] });
    window.addEventListener("resize", () => { seg.classList.add("no-anim"); place(); requestAnimationFrame(() => seg.classList.remove("no-anim")); });
    document.fonts?.ready.then(() => place());
  });

  /* ---------- theme (remembered per browser) ---------- */
  function storedTheme() { try { return localStorage.getItem("sp-theme"); } catch (e) { return null; } }
  function currentTheme() {
    return root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  function syncThemeButtons() {
    const dark = currentTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((b) => {
      b.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      b.querySelector(".when-light")?.toggleAttribute("hidden", dark);
      b.querySelector(".when-dark")?.toggleAttribute("hidden", !dark);
    });
  }
  const saved = storedTheme();
  if (saved) root.dataset.theme = saved;
  function applyTheme(next) {
    root.dataset.theme = next;
    try { localStorage.setItem("sp-theme", next); } catch (e) { /* private mode */ }
    syncThemeButtons();
    document.dispatchEvent(new Event("sp:theme"));
  }
  document.querySelectorAll("[data-theme-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      if (!document.startViewTransition || reducedMotion()) { applyTheme(next); return; }
      // new theme grows out of the button as a circle
      const r = btn.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      root.classList.add("theme-swapping");
      const vt = document.startViewTransition(() => applyTheme(next));
      vt.ready.then(() => root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        { duration: 600, easing: "cubic-bezier(.65, 0, .35, 1)", pseudoElement: "::view-transition-new(root)" }
      )).catch(() => {});
      vt.finished.finally(() => root.classList.remove("theme-swapping"));
    }));
  syncThemeButtons();

  /* ---------- mobile navigation ---------- */
  document.querySelectorAll("[data-nav-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => document.body.classList.toggle("nav-open")));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.body.classList.remove("nav-open"); });

  /* ---------- dismissible messages ---------- */
  document.querySelectorAll(".message .close").forEach((b) =>
    b.addEventListener("click", () => {
      const msg = b.closest(".message");
      if (reducedMotion()) { msg.remove(); return; }
      msg.classList.add("leaving");
      msg.addEventListener("animationend", () => msg.remove(), { once: true });
      setTimeout(() => msg.remove(), 500);   // in case animationend never fires
    }));

  /* ---------- upload drop zone ---------- */
  document.querySelectorAll(".dropzone").forEach((zone) => {
    const input = zone.querySelector("input[type=file]");
    const label = zone.querySelector("[data-file-label]");
    const show = () => { if (input.files.length) { label.textContent = input.files[0].name; label.classList.add("chosen"); } };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.add("is-over")));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("is-over")));
  });

  /* ---------- dashboard charts ---------- */
  const dataNode = document.getElementById("chart-data");
  if (!dataNode || !window.SPCharts) return;
  const data = JSON.parse(dataNode.textContent);
  const { line, bars, fmt } = window.SPCharts;
  let series = "revenue";

  function drawHero(animate) {
    const box = document.getElementById("chart-revenue");
    if (!box) return;
    const rows = data.monthly;
    const isRevenue = series === "revenue";
    line(box, {
      animate,
      title: isRevenue ? "Monthly revenue" : "Monthly orders",
      labels: rows.map((r) => r.label),
      values: rows.map((r) => (isRevenue ? r.revenue : r.orders) || 0),
      axisFormat: isRevenue ? fmt.money : fmt.count,
      tooltip: (i) => {
        const r = rows[i];
        const mom = r.mom == null ? "" : `<br>${r.mom >= 0 ? "+" : ""}${r.mom.toFixed(1)}% vs previous month`;
        return `${r.label}<b>${isRevenue ? fmt.moneyFull(r.revenue) : fmt.countFull(r.orders) + " orders"}</b>` +
               (isRevenue ? `${fmt.countFull(r.orders)} orders${mom}` : fmt.moneyFull(r.revenue));
      },
    });
  }

  function drawDelivery(animate) {
    const late = document.getElementById("chart-late");
    if (late) line(late, {
      animate, title: "Late delivery rate by month", left: 44, colorVar: "--accent",
      labels: data.late.map((r) => r.label), values: data.late.map((r) => r.value || 0),
      axisFormat: fmt.pct, tooltip: (i) => `${data.late[i].label}<b>${fmt.pct(data.late[i].value || 0)} late</b>`,
    });
    const rv = document.getElementById("chart-review");
    if (rv) bars(rv, {
      animate, title: "Average review score by delivery delay", left: 32,
      labels: data.review_delay.map((r) => r.short), values: data.review_delay.map((r) => r.value || 0),
      axisFormat: fmt.score, colorFor: (i, v) => (v < 3 ? "--bad" : "--brand"),
      tooltip: (i) => { const r = data.review_delay[i];
        return `${r.label}<b>${fmt.score(r.value)} average score</b>${fmt.countFull(r.orders)} orders`; },
    });
  }

  function drawAll(animate) { drawHero(animate === true); drawDelivery(animate === true); }

  document.querySelectorAll("[data-series]").forEach((btn) =>
    btn.addEventListener("click", () => {
      series = btn.dataset.series;
      document.querySelectorAll("[data-series]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      drawHero(true);
    }));

  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(drawAll, 120); });
  document.addEventListener("sp:theme", drawAll);
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", drawAll);
  // first paint animates; resize and theme redraws are instant
  drawAll(true);
})();
