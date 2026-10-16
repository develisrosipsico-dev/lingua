/* Navegação gramatical: cada resultado pertence à oração e à execução escolhidas. */
(() => {
  "use strict";
  const panel = document.querySelector('[data-stage-panel="segmentacao"]');
  if (!panel) return;

  const payload = (form) => Object.fromEntries(new FormData(form));
  const send = async (form) => {
    const response = await fetch(form.action, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload(form)),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.erro || "Não foi possível concluir esta análise.");
    return data;
  };

  let initializing = false;
  const initialize = async () => {
    const form = panel.querySelector("[data-initial-segmentation]");
    if (!form || initializing || panel.hidden) return;
    initializing = true;
    const button = form.querySelector("button");
    const status = panel.querySelector("[data-segmentation-status]");
    button.disabled = true;
    status.textContent = "Organizando os parágrafos e períodos…";
    try {
      const data = await send(form);
      const url = new URL(window.location.href);
      ["anotacao_id", "analise_id", "execucao_id", "contexto_execucao_id", "vetorizacao_execucao_id"]
        .forEach((name) => url.searchParams.delete(name));
      url.searchParams.set("preparacao_id", data.preparacao_id);
      url.searchParams.set("segmentacao_id", data.segmentacao_id);
      url.hash = "etapa-segmentacao";
      window.location.assign(url.href);
    } catch (error) {
      status.textContent = error.message;
      button.disabled = false;
      initializing = false;
    }
  };

  // Uma primeira abertura constrói a cadeia; as demais usam os registros salvos.
  // Serializar as aberturas evita gerar duas cadeias enquanto o modelo trabalha.
  let periodQueue = Promise.resolve();
  const refreshStages = async (data, details) => {
    if (!data.atualizou_cadeia) return;
    try {
      const response = await fetch(data.detalhe_url);
      if (!response.ok) throw new Error("Etapas indisponíveis");
      const saved = new DOMParser().parseFromString(await response.text(), "text/html");
      for (const stage of ["morfologia", "sintaxe", "regras", "contexto"]) {
        const selector = `[data-stage-panel="${stage}"]`;
        const current = document.querySelector(selector);
        const updated = saved.querySelector(selector);
        if (current && updated) {
          current.innerHTML = updated.innerHTML;
          current.className = updated.className;
          current.setAttribute("aria-labelledby", updated.getAttribute("aria-labelledby"));
        }
      }
      for (const tab of document.querySelectorAll("[data-stage-tab]")) {
        const updated = saved.querySelector(`[data-stage-tab="${tab.dataset.stageTab}"]`);
        if (updated) tab.innerHTML = updated.innerHTML;
      }
      const url = new URL(data.detalhe_url, window.location.href);
      url.hash = window.location.hash;
      window.history.replaceState(window.history.state, "", url);
    } catch (_) {
      const note = document.createElement("a");
      note.href = data.detalhe_url;
      note.textContent = "Orações salvas. Abra a execução para atualizar as outras etapas.";
      details.querySelector("[data-period-content]").append(note);
    }
  };
  const loadPeriod = (details) => {
    const form = details.querySelector("[data-period-analysis]");
    if (!form || form.dataset.loading === "true") return;
    form.dataset.loading = "true";
    const status = details.querySelector(".map-period-status");
    const button = form.querySelector("button");
    button.disabled = true;
    status.textContent = "Identificando as orações com as relações gramaticais do texto…";
    periodQueue = periodQueue.catch(() => {}).then(async () => {
      try {
        const data = await send(form);
        details.querySelector("[data-period-content]").innerHTML = data.html;
        await refreshStages(data, details);
      } catch (error) {
        status.textContent = error.message;
        button.disabled = false;
        delete form.dataset.loading;
      }
    });
  };
  panel.addEventListener("toggle", (event) => {
    if (event.target.matches("[data-map-period]") && event.target.open) loadPeriod(event.target);
  }, true);

  panel.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const word = event.target.closest("[data-morph-word]");
    if (!word) return;
    const reading = word.closest("[data-morphology]");
    const detail = document.getElementById(word.getAttribute("aria-controls"));
    if (!detail || !reading.contains(detail)) return;
    event.preventDefault();
    reading.classList.add("morph-enhanced");
    for (const link of reading.querySelectorAll("[data-morph-word]")) {
      if (link === word) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    }
    for (const item of reading.querySelectorAll(".morph-word-detail")) {
      item.classList.toggle("is-selected", item === detail);
    }
  });

  panel.addEventListener("submit", async (event) => {
    const form = event.target;
    if (form.matches("[data-initial-segmentation]")) {
      event.preventDefault();
      initialize();
      return;
    }
    if (form.matches("[data-period-analysis]")) {
      event.preventDefault();
      loadPeriod(form.closest("[data-map-period]"));
      return;
    }
    if (!form.matches("[data-clause-analysis]")) return;
    event.preventDefault();
    const button = form.querySelector("button");
    const result = document.getElementById(button.getAttribute("aria-controls"));
    if (result.dataset.loaded === "true") {
      result.hidden = !result.hidden;
      button.setAttribute("aria-expanded", String(!result.hidden));
      return;
    }
    button.disabled = true;
    result.hidden = false;
    button.setAttribute("aria-expanded", "true");
    result.textContent = "Preparando a análise desta oração…";
    try {
      const data = await send(form);
      result.innerHTML = data.html;
      result.dataset.loaded = "true";
    } catch (error) {
      result.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  document.addEventListener("lingua:etapa", (event) => {
    if (event.detail.etapa === "segmentacao") initialize();
  });
  initialize();
})();
