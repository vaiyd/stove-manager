// Hover (mouse) and scrub (touch/pen) for [data-chart-plot]. One delegated listener set,
// so an HTMX swap needs no rebinding. The parsed points live in a WeakMap: a new node
// (the swap) parses again. Cursor, dot and tooltip are one HTML overlay per plot.
(() => {
  "use strict";

  const PLOT = "[data-chart-plot]";
  const NEAR_PX = 12;
  const TOUCH_HIDE_MS = 1500;
  const TIP_GAP_PX = 8;
  const EDGE_PX = 4;
  const UNIT = "C";

  const pointsCache = new WeakMap();
  const textsCache = new WeakMap();
  const indexCache = new WeakMap();
  let hideTimer = 0;
  let touchPlot = null;

  const readJson = (raw, fallback) => {
    try {
      return JSON.parse(raw);
    } catch (_) {
      return fallback;
    }
  };

  const pointsOf = (plot) => {
    const cached = pointsCache.get(plot);
    if (cached) return cached;
    const parsed = readJson(plot.dataset.points || "[]", []);
    const points = Array.isArray(parsed) ? parsed : [];
    pointsCache.set(plot, points);
    return points;
  };

  const textsOf = (plot) => {
    const cached = textsCache.get(plot);
    if (cached) return cached;
    const parsed = readJson(plot.dataset.texts || "{}", {});
    const texts = parsed && typeof parsed === "object" ? parsed : {};
    textsCache.set(plot, texts);
    return texts;
  };

  const plotOf = (node) => (node instanceof Element ? node.closest(PLOT) : null);

  const isTouch = (event) => event.pointerType === "touch" || event.pointerType === "pen";

  const nearestIndex = (points, x) => {
    let lo = 0;
    let hi = points.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (points[mid].x < x) lo = mid + 1;
      else hi = mid - 1;
    }
    const right = Math.min(Math.max(lo, 0), points.length - 1);
    const left = Math.max(lo - 1, 0);
    return Math.abs(points[left].x - x) <= Math.abs(points[right].x - x) ? left : right;
  };

  // The segment before a point flagged gap is an outage: nothing was measured there.
  const inGap = (points, x) => {
    for (let i = 1; i < points.length; i += 1) {
      if (points[i].gap && x > points[i - 1].x && x < points[i].x) return true;
    }
    return false;
  };

  const element = (tag, className) => {
    const node = document.createElement(tag);
    node.className = className;
    return node;
  };

  const ensureOverlay = (plot) => {
    const existing = plot.querySelector("[data-chart-overlay]");
    if (existing) return existing;
    const overlay = element("div", "pointer-events-none absolute inset-0 z-20");
    overlay.dataset.chartOverlay = "1";
    overlay.hidden = true;
    const cursor = element(
      "div",
      "absolute inset-y-0 w-px -translate-x-1/2 bg-base-content/40"
    );
    cursor.dataset.chartCursor = "1";
    cursor.setAttribute("aria-hidden", "true");
    const dot = element(
      "div",
      "absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-base-100"
    );
    dot.dataset.chartDot = "1";
    dot.setAttribute("aria-hidden", "true");
    const tip = element(
      "div",
      "absolute z-30 whitespace-nowrap rounded-box border border-base-300 bg-base-100 px-2 py-1 text-xs shadow-md"
    );
    tip.dataset.chartTip = "1";
    tip.setAttribute("aria-hidden", "true");
    const time = element("p", "font-bold");
    time.dataset.chartTime = "1";
    const ambient = element("p", "");
    ambient.dataset.chartAmbient = "1";
    const setpoint = element("p", "");
    setpoint.dataset.chartSetpoint = "1";
    const state = element("p", "");
    state.dataset.chartState = "1";
    tip.append(time, ambient, setpoint, state);
    const live = element("div", "sr-only");
    live.dataset.chartLive = "1";
    live.setAttribute("aria-live", "polite");
    overlay.append(cursor, dot, tip, live);
    plot.appendChild(overlay);
    return overlay;
  };

  const formatAmbient = (value) => {
    if (value == null || value === "") return "";
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(1) : "";
  };

  const formatSetpoint = (value) => {
    const text = formatAmbient(value);
    return text.endsWith(".0") ? text.slice(0, -2) : text;
  };

  const withUnit = (word, value) => `${word} : ${value} ${UNIT}`;

  const linesOf = (point, texts) => {
    const time = point.label || "";
    const ambientValue = formatAmbient(point.ambient);
    const ambient = ambientValue ? withUnit(texts.ambient || "", ambientValue) : "";
    const setpointValue = point.setpoint == null ? "" : formatSetpoint(point.setpoint);
    const setpoint = setpointValue ? withUnit(texts.setpoint || "", setpointValue) : "";
    const state = point.state ? `${texts.state || ""} : ${point.state}` : "";
    const spoken = [time, ambient, setpoint, state].filter(Boolean);
    return { time, ambient, setpoint, state, spoken };
  };

  const setLine = (node, text) => {
    node.hidden = !text;
    node.textContent = text || "";
  };

  const placeTip = (plot, tip) => {
    const width = plot.clientWidth;
    const tipWidth = tip.offsetWidth;
    const cursor = plot.querySelector("[data-chart-cursor]");
    const cursorPx = (parseFloat(cursor.style.left) / 100) * width;
    let left = cursorPx + TIP_GAP_PX;
    if (left + tipWidth > width - EDGE_PX) left = cursorPx - tipWidth - TIP_GAP_PX;
    if (left < EDGE_PX) left = EDGE_PX;
    if (left + tipWidth > width - EDGE_PX) {
      left = Math.max(EDGE_PX, width - EDGE_PX - tipWidth);
    }
    const rect = plot.getBoundingClientRect();
    const pastRight = rect.left + left + tipWidth - (window.innerWidth - EDGE_PX);
    if (pastRight > 0) left -= pastRight;
    const pastLeft = EDGE_PX - (rect.left + left);
    if (pastLeft > 0) left += pastLeft;
    tip.style.left = `${left}px`;
    const tipHeight = tip.offsetHeight;
    if (rect.top < tipHeight + EDGE_PX * 2) {
      tip.style.top = `${EDGE_PX}px`;
      tip.style.bottom = "auto";
    } else {
      tip.style.top = "auto";
      tip.style.bottom = "calc(100% + 4px)";
    }
  };

  const render = (plot, index, announce) => {
    const points = pointsOf(plot);
    const point = points[index];
    if (!point) return;
    const overlay = ensureOverlay(plot);
    const lines = linesOf(point, textsOf(plot));
    const tip = overlay.querySelector("[data-chart-tip]");
    setLine(overlay.querySelector("[data-chart-time]"), lines.time);
    setLine(overlay.querySelector("[data-chart-ambient]"), lines.ambient);
    setLine(overlay.querySelector("[data-chart-setpoint]"), lines.setpoint);
    setLine(overlay.querySelector("[data-chart-state]"), lines.state);
    overlay.querySelector("[data-chart-cursor]").style.left = `${point.x}%`;
    const dot = overlay.querySelector("[data-chart-dot]");
    dot.style.left = `${point.x}%`;
    dot.style.top = `${point.y}%`;
    overlay.hidden = false;
    placeTip(plot, tip);
    if (announce) overlay.querySelector("[data-chart-live]").textContent = lines.spoken.join(". ");
  };

  const hide = (plot) => {
    const overlay = plot.querySelector("[data-chart-overlay]");
    if (overlay) overlay.hidden = true;
  };

  const cancelHide = () => {
    if (!hideTimer) return;
    clearTimeout(hideTimer);
    hideTimer = 0;
  };

  const showAt = (plot, xPercent, announce) => {
    const points = pointsOf(plot);
    if (!points.length) return;
    const index = nearestIndex(points, xPercent);
    if (inGap(points, xPercent)) {
      const width = plot.clientWidth || 1;
      const dist = (Math.abs(points[index].x - xPercent) / 100) * width;
      if (dist > NEAR_PX) {
        hide(plot);
        return;
      }
    }
    indexCache.set(plot, index);
    render(plot, index, announce);
  };

  const percentFrom = (plot, event) => {
    const rect = plot.getBoundingClientRect();
    if (rect.width <= 0) return null;
    return ((event.clientX - rect.left) / rect.width) * 100;
  };

  const onPointerDown = (event) => {
    const plot = plotOf(event.target);
    if (!plot) {
      if (touchPlot) hide(touchPlot);
      touchPlot = null;
      cancelHide();
      return;
    }
    if (isTouch(event)) {
      touchPlot = plot;
      cancelHide();
      try {
        plot.setPointerCapture(event.pointerId);
      } catch (_) {
        // The pointer can already be gone; the move events still update while it is down.
      }
    }
    const x = percentFrom(plot, event);
    if (x != null) showAt(plot, x, false);
  };

  const onPointerMove = (event) => {
    const plot = plotOf(event.target);
    if (!plot) return;
    if (isTouch(event) && touchPlot !== plot) return;
    const x = percentFrom(plot, event);
    if (x != null) showAt(plot, x, false);
  };

  const onPointerUp = (event) => {
    if (!isTouch(event)) return;
    const plot = plotOf(event.target) || touchPlot;
    if (!plot) return;
    cancelHide();
    touchPlot = plot;
    hideTimer = setTimeout(() => {
      hide(plot);
      hideTimer = 0;
      if (touchPlot === plot) touchPlot = null;
    }, TOUCH_HIDE_MS);
  };

  const onPointerLeave = (event) => {
    if (isTouch(event)) return;
    const plot = plotOf(event.target);
    if (!plot) return;
    const next = event.relatedTarget;
    if (next instanceof Node && plot.contains(next)) return;
    hide(plot);
  };

  const onKeyDown = (event) => {
    if (!(event.target instanceof Element) || !event.target.matches(PLOT)) return;
    const plot = event.target;
    const points = pointsOf(plot);
    if (!points.length) return;
    const key = event.key;
    if (key === "Escape") {
      hide(plot);
      indexCache.delete(plot);
      return;
    }
    let index = indexCache.has(plot) ? indexCache.get(plot) : null;
    if (key === "Home") index = 0;
    else if (key === "End") index = points.length - 1;
    else if (key === "ArrowLeft" || key === "ArrowRight") {
      if (index == null) index = nearestIndex(points, 50);
      else if (key === "ArrowLeft") index = Math.max(0, index - 1);
      else index = Math.min(points.length - 1, index + 1);
    } else {
      return;
    }
    event.preventDefault();
    indexCache.set(plot, index);
    render(plot, index, true);
  };

  const hideTouch = () => {
    if (!touchPlot) return;
    hide(touchPlot);
    touchPlot = null;
    cancelHide();
  };

  const onVisibility = () => {
    if (document.visibilityState !== "hidden") return;
    document.querySelectorAll("[data-chart-overlay]").forEach((overlay) => {
      overlay.hidden = true;
    });
    touchPlot = null;
    cancelHide();
  };

  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerUp);
  document.addEventListener("pointerleave", onPointerLeave, true);
  document.addEventListener("keydown", onKeyDown);
  document.addEventListener("visibilitychange", onVisibility);
  document.addEventListener("scroll", hideTouch, true);
})();
