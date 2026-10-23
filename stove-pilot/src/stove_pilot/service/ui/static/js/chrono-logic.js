// Pure logic of the chrono editor (no DOM): week merge, overlaps, gap warnings, dirty and fingerprint checks.
// A model is { blocks: [{ kind, enabled, programs: [{ index, start, stop, days, setpoint, power }] }] }
// with "HH:MM" times (or null) and days = array of keys (null for the day block).
(() => {
  "use strict";
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const MIN_GAP = 120;
  const MINUTES_PER_DAY = 1440;

  const toMinutes = (hhmm) => {
    if (!hhmm) return null;
    const [h, m] = hhmm.split(":").map(Number);
    return h * 60 + m;
  };
  const programDays = (program) => program.days || DAYS;
  const isSet = (program) => program.start !== null || program.stop !== null;
  const sortKey = (e) => (e.start !== null ? e.start : e.stop || 0);

  // Entries of each day for the enabled blocks: { start, stop, kind, index, setpoint, power }.
  const weekEntries = (model) => {
    const week = Object.fromEntries(DAYS.map((day) => [day, []]));
    for (const block of model.blocks) {
      if (!block.enabled) continue;
      for (const program of block.programs.filter(isSet)) {
        for (const day of programDays(program)) {
          week[day].push({
            start: toMinutes(program.start),
            stop: toMinutes(program.stop),
            kind: block.kind,
            index: program.index,
            setpoint: program.setpoint,
            power: program.power,
          });
        }
      }
    }
    for (const day of DAYS) {
      week[day].sort((a, b) => sortKey(a) - sortKey(b) || a.index - b.index);
    }
    return week;
  };

  // Start and stop events of the enabled blocks over the week (minute of week); a stop sorts before a start of the same minute.
  const weekEvents = (model) => {
    const events = [];
    for (const block of model.blocks.filter((b) => b.enabled)) {
      for (const program of block.programs.filter(isSet)) {
        for (const day of programDays(program)) {
          for (const type of ["stop", "start"]) {
            if (!program[type]) continue;
            events.push({ type, at: DAYS.indexOf(day) * MINUTES_PER_DAY + toMinutes(program[type]), kind: block.kind, program });
          }
        }
      }
    }
    const rank = (e) => (e.type === "stop" ? 0 : 1);
    return events.sort((a, b) => a.at - b.at || rank(a) - rank(b) || a.program.index - b.program.index);
  };

  // Push the pieces of the cyclic span [from, to) (minutes of week) into the days it crosses.
  const addSpan = (days, from, to, opener, open) => {
    const { kind, program } = opener;
    for (let d = Math.floor(from / MINUTES_PER_DAY); d * MINUTES_PER_DAY < to; d++) {
      const base = d * MINUTES_PER_DAY;
      days[DAYS[d % DAYS.length]].push({
        from: Math.max(from, base) - base,
        to: Math.min(to, base + MINUTES_PER_DAY) - base,
        kind,
        program: program.index,
        setpoint: program.setpoint,
        power: program.power,
        start: from % MINUTES_PER_DAY,
        stop: open ? null : to % MINUTES_PER_DAY,
      });
    }
  };

  // The stove is on from a start event to the next stop event, cyclically over the week.
  // Returns { days: { mon: [{ from, to, kind, program, setpoint, power, start, stop }] }, empty } (minutes of the day).
  const weekSegments = (model) => {
    const days = Object.fromEntries(DAYS.map((day) => [day, []]));
    const events = weekEvents(model);
    const week = DAYS.length * MINUTES_PER_DAY;
    const firstStart = events.find((e) => e.type === "start");
    if (firstStart && !events.some((e) => e.type === "stop")) {
      addSpan(days, firstStart.at, firstStart.at + week, firstStart, true);
    } else if (firstStart) {
      events.forEach((opener, i) => {
        const previous = events[(i + events.length - 1) % events.length];
        if (opener.type !== "start" || previous.type !== "stop") return;
        let k = 1;
        while (events[(i + k) % events.length].type !== "stop") k++;
        addSpan(days, opener.at, events[(i + k) % events.length].at + (i + k >= events.length ? week : 0), opener, false);
      });
    }
    for (const list of Object.values(days)) {
      list.sort((a, b) => a.from - b.from);
      for (let i = list.length - 1; i > 0; i--) {
        const same = list[i - 1].to === list[i].from && list[i - 1].start === list[i].start && list[i - 1].program === list[i].program;
        if (same && list[i - 1].kind === list[i].kind) list.splice(i - 1, 2, { ...list[i - 1], to: list[i].to });
      }
    }
    return { days, empty: events.length === 0 };
  };

  // Complete entries per day, like the API checks them: an overnight entry (stop before start)
  // is split into start..24:00 on its day and 00:00..stop on the next day.
  const weekSlots = (model) => {
    const week = weekEntries(model);
    const slots = Object.fromEntries(DAYS.map((day) => [day, []]));
    for (const [d, day] of DAYS.entries()) {
      for (const e of week[day].filter((x) => x.start !== null && x.stop !== null && x.start !== x.stop)) {
        if (e.start < e.stop) slots[day].push(e);
        else slots[day].push({ ...e, stop: MINUTES_PER_DAY });
        if (e.stop < e.start && e.stop > 0) slots[DAYS[(d + 1) % DAYS.length]].push({ ...e, start: 0 });
      }
    }
    for (const list of Object.values(slots)) list.sort((a, b) => a.start - b.start || a.stop - b.stop);
    return slots;
  };

  const KIND_RANK = { day: 0, week: 1, weekend: 2 };
  const programId = (slot) => `${slot.kind}:${slot.index}`;
  const comparePrograms = (a, b) => KIND_RANK[a.kind] - KIND_RANK[b.kind] || a.index - b.index;
  const samePrograms = (left, right) =>
    left.length === right.length && left.every((program, i) => program.kind === right[i].kind && program.index === right[i].index);
  const unionPrograms = (left, right) => {
    const merged = [...left];
    for (const program of right) {
      if (!merged.some((item) => item.kind === program.kind && item.index === program.index)) merged.push(program);
    }
    merged.sort(comparePrograms);
    return merged;
  };

  // Minutes of one day covered by two or more distinct complete programs. Touching ends do not overlap.
  // Contiguous overlap minutes become one range; its programs are the union of the programs involved.
  const dayOverlaps = (slots) => {
    const events = [];
    for (const slot of slots) {
      const program = { kind: slot.kind, index: slot.index };
      events.push({ at: slot.start, delta: 1, id: programId(slot), program });
      events.push({ at: slot.stop, delta: -1, id: programId(slot), program });
    }
    events.sort((a, b) => a.at - b.at || a.delta - b.delta);
    const active = new Map();
    const ranges = [];
    let previous = null;
    for (const event of events) {
      if (previous !== null && event.at > previous && active.size >= 2) {
        const programs = [...active.values()].map((entry) => entry.program).sort(comparePrograms);
        const last = ranges[ranges.length - 1];
        if (last && last.to === previous && samePrograms(last.programs, programs)) last.to = event.at;
        else if (last && last.to === previous) {
          last.to = event.at;
          last.programs = unionPrograms(last.programs, programs);
        } else ranges.push({ from: previous, to: event.at, programs });
      }
      const current = active.get(event.id);
      if (event.delta === 1) active.set(event.id, { count: current ? current.count + 1 : 1, program: event.program });
      else if (current && current.count > 1) active.set(event.id, { count: current.count - 1, program: current.program });
      else active.delete(event.id);
      previous = event.at;
    }
    return ranges;
  };

  // Per day, merged ranges where enabled complete programs overlap (overnight programs split like weekSlots).
  const weekOverlaps = (model) => {
    const slots = weekSlots(model);
    return Object.fromEntries(DAYS.map((day) => [day, dayOverlaps(slots[day])]));
  };

  const programKey = (e) => `${e.kind}${e.index}`;

  // Warnings of the pending config: gap_short, start_off, stop_off, overnight and overlap (one per pair or program).
  const clientWarnings = (model) => {
    const found = [];
    const seen = new Set();
    const slots = weekSlots(model);
    for (const day of DAYS) {
      const ordered = slots[day];
      ordered.forEach((first, i) => {
        const second = ordered[i + 1];
        const gap = second ? second.start - first.stop : -1;
        const pair = second ? `${programKey(first)}-${programKey(second)}` : "";
        if (second && gap >= 0 && gap < MIN_GAP && !seen.has(pair)) {
          seen.add(pair);
          found.push({ code: "gap_short", first, second, gap });
        }
        for (const other of ordered.slice(i + 1)) {
          if (other.start >= first.stop) break;
          const key = "o" + [programKey(first), programKey(other)].sort().join("-");
          if (programKey(first) === programKey(other) || seen.has(key)) continue;
          seen.add(key);
          found.push({ code: "overlap", first, second: other });
        }
      });
    }
    for (const block of model.blocks.filter((b) => b.enabled)) {
      for (const p of block.programs) {
        const entry = { kind: block.kind, index: p.index };
        if (p.start === null && p.stop !== null) found.push({ code: "start_off", entry });
        else if (p.stop === null && p.start !== null) found.push({ code: "stop_off", entry });
        else if (p.start !== null && toMinutes(p.stop) < toMinutes(p.start)) {
          found.push({ code: "overnight", entry, start: p.start, stop: p.stop });
        }
      }
    }
    return found;
  };

  // Comparable text of the edited content (the enable flags are immediate actions, not edits).
  const snapshot = (model) =>
    JSON.stringify(model.blocks.map((b) => [b.kind, b.programs.map((p) => [p.index, p.start, p.stop, p.days, p.setpoint, p.power])]));

  const normalizeEtag = (value) => String(value || "").replace(/^W\//, "").replace(/^"|"$/g, "");

  // What to do with a fingerprint answer: "none", "reload" (clean form) or "banner" (local edits).
  const fingerprintAction = (loadedEtag, fingerprint, dirty) => {
    if (!fingerprint || normalizeEtag(loadedEtag) === normalizeEtag(fingerprint)) return "none";
    return dirty ? "banner" : "reload";
  };

  globalThis.stovePilotChronoLogic = {
    DAYS,
    MINUTES_PER_DAY,
    weekEntries,
    weekSegments,
    weekOverlaps,
    clientWarnings,
    snapshot,
    normalizeEtag,
    fingerprintAction,
  };
})();
