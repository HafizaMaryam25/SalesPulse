/* SalesPulse charts: a small SVG line + bar chart with hover tooltips.
   No library needed, follows the page theme (reads CSS variables).
   Pass `animate: true` to draw the line in / grow the bars (first paint and
   series switches); redraws for resize or theme changes stay instant. */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const EASE_OUT = "cubic-bezier(.22, 1, .36, 1)";
  const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const canAnimate = (cfg) => cfg.animate && !reducedMotion() && typeof Element.prototype.animate === "function";

  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }

  function scaleY(values, min0) {
    const max = Math.max(...values, 0);
    const min = min0 ? 0 : Math.min(...values);
    const step = niceStep((max - min) / 4 || 1);
    const lo = min0 ? 0 : Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step || step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v);
    return { lo, hi, ticks };
  }

  const fmt = {
    money(v) {
      const a = Math.abs(v);
      if (a >= 1e6) return "R$ " + (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
      if (a >= 1e3) return "R$ " + (v / 1e3).toFixed(0) + "K";
      return "R$ " + v.toFixed(0);
    },
    moneyFull: (v) => "R$ " + Math.round(v).toLocaleString("en-US"),
    count(v) {
      if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(v >= 1e4 ? 0 : 1) + "K";
      return String(Math.round(v));
    },
    countFull: (v) => Math.round(v).toLocaleString("en-US"),
    pct: (v) => v.toFixed(v >= 10 ? 0 : 1) + "%",
    score: (v) => v.toFixed(1),
  };

  function tooltip(container) {
    let tip = container.querySelector(".tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tip";
      container.appendChild(tip);
    }
    return {
      show(x, y, html) { tip.innerHTML = html; tip.style.left = x + "px"; tip.style.top = y + "px"; tip.classList.add("show"); },
      hide() { tip.classList.remove("show"); },
    };
  }

  function frame(container, opts) {
    container.querySelectorAll("svg").forEach((s) => s.remove());
    const w = container.clientWidth, h = container.clientHeight;
    const m = { top: 14, right: 14, bottom: 28, left: opts.left || 56 };
    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": opts.title || "chart" });
    container.prepend(svg);
    return { svg, w, h, m, iw: w - m.left - m.right, ih: h - m.top - m.bottom };
  }

  function axes(f, y, yFmt) {
    const grid = cssVar("--chart-grid"), ink3 = cssVar("--ink-3");
    y.ticks.forEach((t) => {
      const yy = f.m.top + f.ih - ((t - y.lo) / (y.hi - y.lo)) * f.ih;
      el("line", { x1: f.m.left, x2: f.w - f.m.right, y1: yy, y2: yy, stroke: grid, "stroke-width": 1 }, f.svg);
      const label = el("text", { x: f.m.left - 10, y: yy + 4, "text-anchor": "end", fill: ink3, "font-size": 11 }, f.svg);
      label.textContent = yFmt(t);
    });
  }

  function xLabels(f, labels, xAt, all) {
    const ink3 = cssVar("--ink-3");
    const every = all ? 1 : Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor(f.iw / 80))));
    labels.forEach((lab, i) => {
      if (i % every !== 0 && i !== labels.length - 1) return;
      if (i !== labels.length - 1 && labels.length - 1 - i < every * 0.75) return;
      const t = el("text", { x: xAt(i), y: f.h - 8, "text-anchor": "middle", fill: ink3, "font-size": 11 }, f.svg);
      t.textContent = lab;
    });
  }

  /* ---------------------------------------------------------------- line */
  function line(container, cfg) {
    const values = cfg.values;
    if (!values.length) { container.innerHTML = '<p class="muted" style="padding:24px">No data for this period.</p>'; return; }
    const f = frame(container, cfg);
    const y = scaleY(values, cfg.zero !== false);
    const color = cssVar(cfg.colorVar || "--brand");
    const n = values.length;
    const xAt = (i) => f.m.left + (n === 1 ? f.iw / 2 : (i / (n - 1)) * f.iw);
    const yAt = (v) => f.m.top + f.ih - ((v - y.lo) / (y.hi - y.lo)) * f.ih;

    axes(f, y, cfg.axisFormat);
    xLabels(f, cfg.labels, xAt);

    const pts = values.map((v, i) => [xAt(i), yAt(v)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");

    const animate = canAnimate(cfg);
    const DRAW_MS = 1100;
    const uid = Math.random().toString(36).slice(2, 8);
    const defs = el("defs", {}, f.svg);
    let area = null, reveal = null;
    if (cfg.area !== false) {
      const grad = el("linearGradient", { id: "g" + uid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      el("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.22 }, grad);
      el("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }, grad);
      area = el("path", { d: `${d} L${xAt(n - 1)} ${f.m.top + f.ih} L${xAt(0)} ${f.m.top + f.ih} Z`, fill: `url(#g${uid})` }, f.svg);
      if (animate) {
        // the area is uncovered left-to-right in step with the line being drawn
        const clip = el("clipPath", { id: "c" + uid }, defs);
        reveal = el("rect", { x: f.m.left - 2, y: 0, width: f.iw + 4, height: f.h }, clip);
        reveal.style.transformBox = "fill-box";
        reveal.style.transformOrigin = "0 50%";
        area.setAttribute("clip-path", `url(#c${uid})`);
      }
    }
    const path = el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);

    // mark the last point so "where are we now" is obvious, with a soft pulse ring
    const last = pts[n - 1];
    const ring = el("circle", { class: "sp-ring", cx: last[0], cy: last[1], r: 4.5, fill: "none", stroke: color, "stroke-width": 1.5 }, f.svg);
    const end = el("circle", { cx: last[0], cy: last[1], r: 4.5, fill: color, stroke: cssVar("--surface"), "stroke-width": 2 }, f.svg);

    if (animate) {
      const len = path.getTotalLength();
      path.style.strokeDasharray = `${len} ${len}`;
      path.animate([{ strokeDashoffset: len }, { strokeDashoffset: 0 }], { duration: DRAW_MS, easing: EASE_OUT, fill: "both" })
        .finished.then(() => { path.style.strokeDasharray = ""; }).catch(() => {});
      if (reveal) reveal.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], { duration: DRAW_MS, easing: EASE_OUT, fill: "both" });
      end.style.transformBox = "fill-box"; end.style.transformOrigin = "center";
      end.animate([{ transform: "scale(0)", opacity: 0 }, { transform: "scale(1.35)", opacity: 1, offset: 0.7 }, { transform: "scale(1)", opacity: 1 }],
                  { duration: 450, delay: DRAW_MS * 0.85, easing: EASE_OUT, fill: "both" });
    } else {
      ring.style.animationDelay = "0s";
    }

    // hover layer
    const cross = el("line", { class: "sp-cross", y1: f.m.top, y2: f.m.top + f.ih, stroke: cssVar("--line-strong"), "stroke-width": 1, opacity: 0 }, f.svg);
    const dot = el("circle", { class: "sp-dot", r: 5, fill: color, stroke: cssVar("--surface"), "stroke-width": 2, opacity: 0 }, f.svg);
    const tip = tooltip(container);
    const hit = el("rect", { x: f.m.left - 10, y: 0, width: f.iw + 20, height: f.h, fill: "transparent" }, f.svg);
    hit.addEventListener("mousemove", (e) => {
      const r = f.svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * f.w;
      const i = Math.max(0, Math.min(n - 1, Math.round(((px - f.m.left) / f.iw) * (n - 1))));
      const [cx, cy] = pts[i];
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("opacity", 1);
      dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.setAttribute("opacity", 1);
      tip.show((cx / f.w) * r.width, (cy / f.h) * r.height, cfg.tooltip(i));
    });
    hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); tip.hide(); });
  }

  /* ---------------------------------------------------------------- bars */
  function bars(container, cfg) {
    const values = cfg.values;
    if (!values.length) { container.innerHTML = '<p class="muted" style="padding:24px">No data yet.</p>'; return; }
    const f = frame(container, Object.assign({ left: 40 }, cfg));
    const y = scaleY(values.concat(cfg.min || []), cfg.zero !== false);
    const n = values.length;
    const slot = f.iw / n;
    const bw = Math.min(46, slot * 0.62);
    const xAt = (i) => f.m.left + slot * i + slot / 2;
    const yAt = (v) => f.m.top + f.ih - ((v - y.lo) / (y.hi - y.lo)) * f.ih;
    axes(f, y, cfg.axisFormat);
    xLabels(f, cfg.labels, xAt, slot >= 56);

    const tip = tooltip(container);
    const base = f.m.top + f.ih;
    const animate = canAnimate(cfg);
    values.forEach((v, i) => {
      const x = xAt(i) - bw / 2, top = yAt(v), r = Math.min(4, bw / 2, base - top);
      const color = cssVar(cfg.colorFor ? cfg.colorFor(i, v) : cfg.colorVar || "--brand");
      const bar = el("path", {
        class: "sp-bar",
        d: `M${x} ${base} V${top + r} Q${x} ${top} ${x + r} ${top} H${x + bw - r} Q${x + bw} ${top} ${x + bw} ${top + r} V${base} Z`,
        fill: color,
      }, f.svg);
      if (animate) {
        bar.animate([{ transform: "scaleY(0)" }, { transform: "scaleY(1.04)", offset: 0.75 }, { transform: "scaleY(1)" }],
                    { duration: 750, delay: 250 + i * 70, easing: EASE_OUT, fill: "both" });
      }
      const hit = el("rect", { x: f.m.left + slot * i, y: f.m.top, width: slot, height: f.ih, fill: "transparent" }, f.svg);
      hit.addEventListener("mousemove", () => {
        const rr = f.svg.getBoundingClientRect();
        bar.classList.add("is-hot");
        tip.show((xAt(i) / f.w) * rr.width, (top / f.h) * rr.height, cfg.tooltip(i));
      });
      hit.addEventListener("mouseleave", () => { bar.classList.remove("is-hot"); tip.hide(); });
    });
  }

  window.SPCharts = { line, bars, fmt };
})();
