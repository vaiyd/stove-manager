// Reglages: keeps the energy saving form coherent and its sentence live (no framework).
(() => {
  "use strict";
  const FIRST_OFFSET = "1";
  const OFF = "off";

  const sentence = (form) => {
    const stop = form.elements.stop.value;
    const template = stop === OFF ? form.dataset.textOff : form.dataset.textOn;
    form.querySelector("[data-energy-sentence]").textContent = template
      .replace("{stop}", stop)
      .replace("{start}", form.elements.start.value);
  };

  // The toggle and the stop select are two views of the same value: OFF means disabled.
  document.addEventListener("change", (evt) => {
    const form = evt.target.closest("[data-energy-form]");
    if (!form) return;
    const { enabled, stop } = form.elements;
    if (evt.target === enabled) {
      stop.value = enabled.checked ? (stop.value === OFF ? FIRST_OFFSET : stop.value) : OFF;
    } else if (evt.target === stop) {
      enabled.checked = stop.value !== OFF;
    }
    sentence(form);
  });
})();
