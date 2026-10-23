// Weekly preview of the chrono page (DOM only): one bar per day filled while the stove is on.
// Segments come from weekSegments; weekOverlaps draws a red hatch on minutes covered by two programs.
// The texts come from the editor's data-texts.
(() => {
  "use strict";
  const logic = globalThis.stovePilotChronoLogic;
  const SEGMENT_CLASSES = { day: "bg-base-content/40", week: "bg-primary", weekend: "bg-secondary" };

  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, key) => values[key]);
  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = (minutes) => `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;

  const segmentElement = (segment, texts) => {
    const element = document.createElement("div");
    element.className = `absolute inset-y-0 min-w-1 rounded-sm ${SEGMENT_CLASSES[segment.kind]}`;
    element.style.left = `${(segment.from / logic.MINUTES_PER_DAY) * 100}%`;
    element.style.width = `${((segment.to - segment.from) / logic.MINUTES_PER_DAY) * 100}%`;
    element.title = fill(texts.preview_tip, {
      start: hhmm(segment.start),
      stop: segment.stop === null ? texts.preview_open_end : hhmm(segment.stop),
      block: texts.blocks[segment.kind],
      setpoint: segment.setpoint,
      power: segment.power,
    });
    return element;
  };

  const programName = (texts, program) => `${texts.blocks[program.kind]} P${program.index}`;

  const overlapElement = (overlap, texts) => {
    const element = document.createElement("div");
    const names = overlap.programs.map((program) => programName(texts, program));
    element.className = "chrono-hatch absolute inset-y-0";
    element.style.left = `${(overlap.from / logic.MINUTES_PER_DAY) * 100}%`;
    element.style.width = `${((overlap.to - overlap.from) / logic.MINUTES_PER_DAY) * 100}%`;
    element.title = fill(texts.preview_overlap, { programs: names.join(` ${texts.preview_and} `) });
    element.setAttribute("aria-hidden", "true");
    return element;
  };

  // Touching segments (back-to-back programs) read as one range.
  const mergeRanges = (segments) =>
    segments.reduce((ranges, { from, to }) => {
      const last = ranges[ranges.length - 1];
      if (last && last.to === from) last.to = to;
      else ranges.push({ from, to });
      return ranges;
    }, []);

  const dayLabel = (texts, key, segments) => {
    const day = texts.days[key];
    const ranges = mergeRanges(segments);
    if (!ranges.length) return fill(texts.preview_day_off, { day });
    if (ranges.length === 1 && ranges[0].from === 0 && ranges[0].to === logic.MINUTES_PER_DAY) {
      return fill(texts.preview_day_full, { day });
    }
    const spans = ranges.map((r) => fill(texts.preview_range, { begin: hhmm(r.from), end: hhmm(r.to) }));
    return fill(texts.preview_day_on, { day, ranges: spans.join(` ${texts.preview_and} `) });
  };

  const overlapLabel = (texts, overlaps) => {
    if (!overlaps.length) return "";
    const spans = overlaps.map((range) => fill(texts.preview_range, { begin: hhmm(range.from), end: hhmm(range.to) }));
    return fill(texts.preview_overlap_aria, { ranges: spans.join(` ${texts.preview_and} `) });
  };

  const render = (root, model, texts) => {
    const { days, empty } = logic.weekSegments(model);
    const overlaps = logic.weekOverlaps(model);
    for (const row of root.querySelectorAll("[data-preview-day]")) {
      const key = row.dataset.previewDay;
      const bar = row.querySelector("[data-preview-bar]");
      const hatches = overlaps[key].map((overlap) => overlapElement(overlap, texts));
      bar.replaceChildren(...days[key].map((segment) => segmentElement(segment, texts)), ...hatches);
      const sentence = overlapLabel(texts, overlaps[key]);
      const label = dayLabel(texts, key, days[key]);
      bar.setAttribute("aria-label", sentence ? `${label}. ${sentence}` : label);
    }
    root.querySelector("[data-preview-empty]").hidden = !empty;
  };

  globalThis.stovePilotChronoPreview = { render, dayLabel };
})();
