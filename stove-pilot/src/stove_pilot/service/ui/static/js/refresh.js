// Display refresh controller (design 8.5): one timer per page, no framework.
(() => {
  "use strict";
  const KEY = "stovePilot.refreshS";
  const ALLOWED = [0, 10, 30, 60, 300];
  const FOLLOW_UP_MS = 5000;
  const TOAST_MS = 7000;
  const ALIGN = { minMs: 800, targetMs: 2500, maxMs: 6000, minWaitMs: 1000 };
  const body = document.body;
  let timer = null, alignTimer = null, caughtUp = false;

  const stored = () => {
    try {
      const raw = localStorage.getItem(KEY);
      return raw === null ? NaN : Number(raw);
    } catch (_) {
      return NaN;
    }
  };
  const interval = () => {
    const fallback = Number(body.dataset.refreshDefault);
    if (body.dataset.refreshFixed) return fallback;
    return ALLOWED.includes(stored()) ? stored() : fallback;
  };
  const visible = () => document.visibilityState === "visible";
  // forced: ask the API for a live read; polite: skip regions being edited.
  const tick = (forced, polite) =>
    body.dispatchEvent(new CustomEvent("refresh-tick", { detail: { forced, polite } }));
  const now = () => tick(true, false);

  const markSelected = () => {
    const option = document.querySelector(`[data-refresh-set="${interval()}"]`);
    if (!option) return;
    document.querySelectorAll("[data-refresh-label]").forEach((el) => {
      el.textContent = option.textContent;
    });
  };
  // Ticks only re-render the poller's cached status, so a timer phased just before a poll always
  // shows data about poll-s old. Keep each tick a little after a poll (no extra stove read): too
  // late, catch up once on the next poll; too early (drift), postpone the timer.
  const align = () => {
    clearTimeout(alignTimer);
    const el = document.querySelector("#glance [data-polled-at]");
    const at = el ? Date.parse(el.dataset.polledAt) : NaN;
    const pollMs = Number(body.dataset.pollS) * 1000;
    const ageMs = Date.now() - at;
    if (!(pollMs > 0) || !(ageMs >= 0) || ageMs >= 2 * pollMs || interval() * 1000 < pollMs) return;
    caughtUp = caughtUp && ageMs > ALIGN.maxMs;
    if (ageMs < ALIGN.minMs) {
      alignTimer = setTimeout(restart, ALIGN.targetMs - ageMs);
    } else if (ageMs > ALIGN.maxMs && !caughtUp) {
      let wait = at + pollMs + ALIGN.targetMs - Date.now();
      while (wait < ALIGN.minWaitMs) wait += pollMs;
      alignTimer = setTimeout(() => ((caughtUp = true), restart(), visible() && tick(false, true)), wait);
    }
  };
  const restart = () => {
    clearInterval(timer);
    timer = interval() > 0 ? setInterval(() => visible() && tick(false, true), interval() * 1000) : null;
    markSelected();
    align();
  };

  const updateAges = () => {
    const limitMs = 2 * interval() * 1000;
    document.querySelectorAll("[data-polled-at]").forEach((el) => {
      const at = Date.parse(el.dataset.polledAt);
      if (Number.isNaN(at)) return;
      const age = Math.max(0, Math.round((Date.now() - at) / 1000));
      const text = age < 60 ? body.dataset.agoS : body.dataset.agoMin;
      el.textContent = text.replace("{n}", age < 60 ? age : Math.floor(age / 60));
      const stale = el.closest("[data-stale]")?.dataset.stale === "true";
      el.classList.toggle("text-warning", stale || (limitMs > 0 && age * 1000 > limitMs));
    });
  };

  const armToasts = () => {
    document.querySelectorAll("#toasts [data-toast]:not([data-armed])").forEach((el) => {
      el.dataset.armed = "1";
      setTimeout(() => el.remove(), TOAST_MS);
    });
  };

  body.addEventListener("htmx:configRequest", (evt) => {
    if (!evt.detail.elt.hasAttribute("data-live")) return;
    const source = evt.detail.triggeringEvent;
    const forced = Boolean(source && source.detail && source.detail.forced);
    evt.detail.parameters.max_age_s = forced ? 0 : interval();
  });
  body.addEventListener("htmx:beforeRequest", (evt) => {
    const el = evt.detail.elt;
    const source = evt.detail.requestConfig.triggeringEvent;
    if (!el.hasAttribute("data-live") || !(source && source.detail && source.detail.polite)) return;
    const active = document.activeElement;
    const typing = el.contains(active) && active.matches("input, select, textarea");
    if (typing || el.querySelector("[data-dirty]") || document.querySelector("dialog[open]")) {
      evt.preventDefault();
    }
  });
  body.addEventListener("refresh-now", () => {
    now();
    setTimeout(() => tick(true, true), FOLLOW_UP_MS);
  });
  body.addEventListener("htmx:afterSettle", () => (updateAges(), align()));
  body.addEventListener("htmx:oobAfterSwap", armToasts);
  document.addEventListener("visibilitychange", () => visible() && interval() > 0 && tick(false, true));
  document.addEventListener("click", (evt) => {
    const choice = evt.target.closest("[data-refresh-set]");
    if (choice) {
      try {
        localStorage.setItem(KEY, choice.dataset.refreshSet);
      } catch (_) {
        // Storage is blocked: the choice only lasts until the next reload.
      }
      caughtUp = false;
      restart();
      document.activeElement.blur();
    } else if (evt.target.closest("[data-refresh-now]")) {
      now();
    }
  });

  window.stovePilotRefresh = { now, interval };
  setInterval(updateAges, 1000);
  restart();
  updateAges();
})();
