// Chrono editor wiring: dirty tracking, preview, client warnings and live fingerprint check (design 8.2).
(() => {
  "use strict";
  const logic = globalThis.stovePilotChronoLogic;
  const state = { root: null, loaded: "", texts: {} };

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
  const inEditor = (el) => Boolean(state.root && el && el.closest && el.closest("#chrono-editor"));
  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, key) => values[key]);
  const label = (kind, index) => `${state.texts.blocks[kind]} P${index}`;
  const FOLD_KEY = "stove-pilot.chrono.folded";

  const readFolded = () => {
    try {
      const raw = sessionStorage.getItem(FOLD_KEY);
      const kinds = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(kinds)) return new Set();
      return new Set(kinds.filter((kind) => typeof kind === "string"));
    } catch (_) {
      return new Set();
    }
  };

  const writeFolded = (kinds) => {
    try {
      sessionStorage.setItem(FOLD_KEY, JSON.stringify([...kinds]));
    } catch (_) {
      // Reason: storage can be blocked; folding still works on this page.
    }
  };

  // Hide the card body only. Inputs stay in the form, enabled, and in readModel.
  const applyFold = (card, folded) => {
    const body = $("[data-block-body]", card);
    const button = $("[data-action='fold']", card);
    if (!body || !button) return;
    body.hidden = folded;
    button.setAttribute("aria-expanded", folded ? "false" : "true");
    const text = folded ? button.dataset.labelExpand : button.dataset.labelCollapse;
    button.setAttribute("aria-label", text);
    button.title = text;
    $("[data-fold-icon='up']", button).hidden = folded;
    $("[data-fold-icon='down']", button).hidden = !folded;
  };

  const restoreFolds = () => {
    const folded = readFolded();
    $$("[data-block]", state.root).forEach((card) => applyFold(card, folded.has(card.dataset.block)));
  };

  const toggleFold = (button) => {
    const card = button.closest("[data-block]");
    if (!card) return;
    const folded = readFolded();
    const kind = card.dataset.block;
    const next = button.getAttribute("aria-expanded") !== "false";
    if (next) folded.add(kind);
    else folded.delete(kind);
    writeFolded(folded);
    applyFold(card, next);
  };

  const readProgram = (box, isDayBlock) => {
    const get = (field) => $(`[data-field="${field}"]`, box);
    const days = $$('[data-field="day"]', box).filter((chip) => chip.checked).map((chip) => chip.value);
    return {
      index: Number(box.dataset.program),
      start: get("no_start").checked ? null : get("start").value || null,
      stop: get("no_stop").checked ? null : get("stop").value || null,
      days: isDayBlock ? null : days,
      setpoint: Number(get("setpoint").value),
      power: Number(get("power").value),
    };
  };

  const readModel = (root) => ({
    blocks: $$("[data-block]", root).map((card) => ({
      kind: card.dataset.block,
      enabled: $("[data-block-toggle]", card).checked,
      programs: $$("[data-program]", card).map((box) => readProgram(box, !$('[data-field="day"]', box))),
    })),
  });

  const warningText = (warning) => {
    const names = { first: warning.first && label(warning.first.kind, warning.first.index) };
    if (warning.second) names.second = label(warning.second.kind, warning.second.index);
    if (warning.entry) names.label = label(warning.entry.kind, warning.entry.index);
    return fill(state.texts[warning.code], { ...names, gap: warning.gap, start: warning.start, stop: warning.stop });
  };

  const renderWarnings = (model, dirty) => {
    const mine = $("#chrono-client-warnings", state.root);
    mine.replaceChildren();
    for (const warning of logic.clientWarnings(model)) {
      const item = document.createElement("li");
      item.setAttribute("role", "alert");
      item.className = "alert alert-warning alert-soft";
      const span = document.createElement("span");
      span.textContent = warningText(warning);
      item.append(span);
      mine.append(item);
    }
    // The API warnings describe the saved chrono; once edited, the pending warnings replace them.
    $("#chrono-api-warnings", state.root).hidden = dirty;
    mine.hidden = !dirty;
  };

  const refresh = () => {
    if (!state.root) return;
    const model = readModel(state.root);
    const dirty = logic.snapshot(model) !== state.loaded;
    const form = $("#chrono-form", state.root);
    if (dirty) form.dataset.dirty = "1";
    else delete form.dataset.dirty;
    globalThis.stovePilotChronoPreview.render(state.root, model, state.texts);
    renderWarnings(model, dirty);
    return dirty;
  };

  const init = () => {
    const root = $("#chrono-editor");
    if (!root || root === state.root || !$("#chrono-form", root)) return;
    state.root = root;
    state.texts = JSON.parse(root.dataset.texts);
    restoreFolds();
    state.loaded = logic.snapshot(readModel(root));
    refresh();
  };

  const isDirty = () => Boolean($("#chrono-form", state.root).dataset.dirty);

  // Apply a fingerprint answer: nothing, a silent reload (clean form) or a sticky banner (dirty form).
  const handleFingerprint = (fingerprint) => {
    const action = logic.fingerprintAction(state.root.dataset.etag, fingerprint, isDirty());
    if (action === "reload") {
      globalThis.htmx.ajax("GET", state.root.dataset.reloadUrl, { target: "#chrono-editor", swap: "outerHTML" });
    } else if (action === "banner") {
      $("#chrono-changed", state.root).hidden = false;
    }
    return action;
  };

  const onTick = (evt) => {
    if (!state.root || !$("#chrono-form", state.root)) return;
    const sent = state.root;
    const forced = Boolean(evt.detail && evt.detail.forced);
    const refreshApi = globalThis.stovePilotRefresh;
    const maxAge = forced || !refreshApi ? 0 : refreshApi.interval();
    return fetch(`${sent.dataset.fingerprintUrl}?max_age_s=${maxAge}`, { headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : null))
      .then((answer) => {
        if (answer && sent === state.root) handleFingerprint(answer.fingerprint);
      })
      .catch(() => {});
  };

  const syncOff = (checkbox) => {
    const input = $(`[data-field="${checkbox.dataset.field.slice(3)}"]`, checkbox.closest("[data-program]"));
    if (checkbox.checked) {
      input.dataset.last = input.value || input.dataset.last || "";
      input.value = "";
    } else {
      input.value = input.dataset.last || "";
    }
    input.disabled = checkbox.checked;
  };

  const clearProgram = (box) => {
    for (const field of ["no_start", "no_stop"]) {
      const checkbox = $(`[data-field="${field}"]`, box);
      checkbox.checked = true;
      syncOff(checkbox);
    }
  };

  const lockToggles = () => {
    $$("[data-block-toggle]", state.root).forEach((toggle) => (toggle.disabled = true));
    $("#chrono-suspended", state.root).hidden = false;
  };

  document.addEventListener("input", (evt) => inEditor(evt.target) && refresh());
  document.addEventListener("change", (evt) => {
    const el = evt.target;
    if (!inEditor(el)) return;
    const field = el.dataset.field;
    if (field === "no_start" || field === "no_stop") syncOff(el);
    if (field === "day") el.classList.toggle("btn-active", el.checked);
    refresh();
  });
  document.addEventListener("keydown", (evt) => {
    const fold = evt.target.closest && evt.target.closest("[data-action='fold']");
    if (!fold || !inEditor(fold) || evt.repeat) return;
    if (evt.key !== "Enter" && evt.key !== " ") return;
    // Reason: preventDefault stops the native click, so Enter and Space toggle once.
    evt.preventDefault();
    fold.click();
  });
  document.addEventListener("click", (evt) => {
    if (!inEditor(evt.target)) return;
    const fold = evt.target.closest("[data-action='fold']");
    if (fold) {
      toggleFold(fold);
      return;
    }
    const button = evt.target.closest("[data-action='clear']");
    if (!button) return;
    clearProgram(button.closest("[data-program]"));
    refresh();
  });
  document.body.addEventListener("refresh-tick", onTick);
  document.body.addEventListener("htmx:afterSettle", init);
  // Reason: block toggles are immediate actions; the server tells us the new ETag or the refusal.
  document.body.addEventListener("chrono-etag", (evt) => {
    const { etag, kind, enabled } = evt.detail;
    state.root.dataset.etag = etag;
    $('[name="etag"]', state.root).value = etag;
    $(`[name="${kind}.enabled"]`, state.root).value = enabled ? "true" : "false";
    refresh();
  });
  document.body.addEventListener("chrono-toggle-rejected", (evt) => {
    const toggle = $(`[data-block-toggle="${evt.detail.kind}"]`, state.root);
    toggle.checked = !toggle.checked;
    if (evt.detail.suspended) lockToggles();
    refresh();
  });
  document.body.addEventListener("chrono-conflict", () => {
    const dialog = $("#chrono-conflict");
    if (dialog) dialog.showModal();
  });

  globalThis.stovePilotChrono = { init, refresh, handleFingerprint, onTick, readModel };
  init();
})();
