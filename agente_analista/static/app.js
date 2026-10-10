"use strict";

(() => {
  // Same whitespace set as Python's Unicode re/str.split and str.strip.
  const whitespace = "[\\u0009-\\u000D\\u001C-\\u0020\\u0085\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000]";
  const whitespaceRun = new RegExp(`${whitespace}+`, "u");
  const edgeWhitespace = new RegExp(`^${whitespace}+|${whitespace}+$`, "gu");
  const paragraphSeparator = /(?:\r\n|\n|\r(?!\n))[\t ]*(?:\r\n|\n|\r(?!\n))(?:[\t ]*(?:\r\n|\n|\r(?!\n)))*/u;
  const headings = ["Passagem do relato", "Trecho de Freud", "Ligação proposta", "Justificativa", "Referência", "Limites/situação"];
  const $ = (id) => document.getElementById(id);
  const elements = Object.fromEntries([
    "search-form", "relato", "word-count", "paragraph-count", "input-error", "search-button", "clear-button",
    "refresh-status", "environment-badge", "environment-description", "environment-problems", "processing",
    "processing-spinner", "processing-title", "processing-detail", "request-error", "results", "results-title",
    "results-summary", "result-message", "connections-table", "copy-button", "export-button", "export-feedback",
    "discarded-section", "discarded-summary", "discarded-table", "rejected-section", "rejected-summary",
    "rejected-list", "retrieval-section", "retrieval-method", "retrieval-candidates",
    "provider-select", "api-key", "model-select", "add-model-button", "add-model-panel", "new-model-id",
    "save-model-button", "cancel-model-button", "new-model-error", "configuration-error", "model-feedback",
    "save-story-button", "save-story-feedback", "refresh-history", "history-feedback", "history-list", "more-history",
    "vetorizacao-file", "import-feedback",
  ].map((id) => [id, $(id)]));
  const modelsStorageKey = "agente-analista-modelos-v1";
  const modelIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$/u;
  let ready = false;
  let busy = false;
  let touched = false;
  let configurationTouched = false;
  let savedModels = readModels();
  let currentResult = null;
  let currentJob = null;
  let pollTimer = null;
  let connectionFailures = 0;
  let saving = false;
  let openingRecord = false;
  let importing = false;
  let importedVectorization = null;
  let importedText = null;
  let savedId = null;
  let savedState = null;
  let historyRecords = [];
  let historyNext = null;
  let historyLoading = false;
  let historyRequest = 0;

  const trimWhitespace = (text) => text.replace(edgeWhitespace, "");
  const list = (value) => Array.isArray(value) ? value : [];
  const display = (value) => {
    if (value === null || value === undefined || value === "") return "";
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
  };
  const countFormat = (value) => Number(value).toLocaleString("pt-BR");
  const visibleText = (text) => text.replace(/\r\n?|\n/gu, "\n");
  const storyText = () => importedText !== null && elements.relato.value === visibleText(importedText) ? importedText : elements.relato.value;
  const node = (tag, text = "", className = "") => {
    const element = document.createElement(tag);
    if (text !== "") element.textContent = display(text);
    if (className) element.className = className;
    return element;
  };

  function readModels() {
    try {
      const stored = JSON.parse(localStorage.getItem(modelsStorageKey) || "[]");
      if (!Array.isArray(stored)) return [];
      const unique = new Set();
      return stored.filter((entry) => {
        if (!entry || !["openrouter", "openai"].includes(entry.provedor) || typeof entry.modelo !== "string" || !modelIdentifier.test(entry.modelo)) return false;
        const identity = `${entry.provedor}:${entry.modelo}`;
        if (unique.has(identity)) return false;
        unique.add(identity);
        return true;
      }).map(({ provedor, modelo }) => ({ provedor, modelo }));
    } catch (_) {
      return [];
    }
  }

  function renderModels(selected = "") {
    const models = savedModels.filter((entry) => entry.provedor === elements["provider-select"].value);
    const placeholder = node("option", models.length ? "Selecione um modelo" : "Inclua um modelo pelo botão +");
    placeholder.value = "";
    elements["model-select"].replaceChildren(placeholder);
    models.forEach(({ modelo }) => {
      const option = node("option", modelo);
      option.value = modelo;
      elements["model-select"].append(option);
    });
    elements["model-select"].value = selected;
    elements["new-model-id"].placeholder = elements["provider-select"].value === "openrouter" ? "Ex.: openai/gpt-4.1-mini" : "Ex.: gpt-4.1-mini";
  }

  function configurationState() {
    const hasModel = Boolean(elements["model-select"].value);
    const key = elements["api-key"].value.trim();
    const hasKey = Boolean(key);
    const validKey = /^[\x21-\x7e]{1,4096}$/u.test(key);
    const error = !hasModel ? "Inclua e selecione um modelo para a justificativa." : !hasKey ? "Informe a chave de API do provedor escolhido." : !validKey ? "Confira a chave de API: cole somente a chave, sem espaços no meio ou outros textos." : "";
    elements["configuration-error"].textContent = error;
    elements["configuration-error"].hidden = !configurationTouched || !error;
    elements["model-select"].setAttribute("aria-invalid", String(configurationTouched && !hasModel));
    elements["api-key"].setAttribute("aria-invalid", String(configurationTouched && !validKey));
    return { error, hasModel, hasKey };
  }

  function invalidateConfiguration() {
    configurationTouched = true;
    resetResults();
    elements["request-error"].hidden = true;
    updateInput();
  }

  function closeModelPanel(returnFocus = true) {
    elements["add-model-panel"].hidden = true;
    elements["add-model-button"].setAttribute("aria-expanded", "false");
    elements["new-model-id"].value = "";
    elements["new-model-error"].hidden = true;
    elements["new-model-id"].setAttribute("aria-invalid", "false");
    if (returnFocus) elements["add-model-button"].focus();
  }

  function addModel() {
    if (busy) return;
    const modelo = elements["new-model-id"].value.trim();
    if (!modelIdentifier.test(modelo)) {
      elements["new-model-error"].textContent = "Informe o identificador do modelo, com até 200 caracteres e sem espaços. Use letras, números ou . _ : / @ + -.";
      elements["new-model-error"].hidden = false;
      elements["new-model-id"].setAttribute("aria-invalid", "true");
      elements["new-model-id"].focus();
      return;
    }
    const provedor = elements["provider-select"].value;
    const existing = savedModels.some((entry) => entry.provedor === provedor && entry.modelo === modelo);
    if (!existing) savedModels.push({ provedor, modelo });
    let persisted = true;
    try { localStorage.setItem(modelsStorageKey, JSON.stringify(savedModels)); } catch (_) { persisted = false; }
    renderModels(modelo);
    closeModelPanel(false);
    invalidateConfiguration();
    elements["model-feedback"].textContent = persisted ? `${modelo} selecionado para a justificativa.` : `${modelo} selecionado. O navegador não permitiu salvar a lista; ela ficará disponível enquanto esta página estiver aberta.`;
    elements["model-select"].focus();
  }

  function inputState(text) {
    const trimmed = trimWhitespace(text);
    const words = trimmed ? trimmed.split(whitespaceRun).length : 0;
    const paragraphs = text.split(paragraphSeparator).map(trimWhitespace).filter(Boolean).length;
    let error = "";
    if (!trimmed) error = "Cole um relato com dois parágrafos para iniciar a busca.";
    else if (paragraphs !== 2) error = `O relato precisa ter exatamente dois parágrafos; foram encontrados ${paragraphs}. Separe-os por uma linha em branco.`;
    else if (words > 400) error = `O relato tem ${words} palavras. Reduza o texto para no máximo 400 palavras.`;
    return { words, paragraphs, error };
  }

  function updateInput() {
    const state = inputState(storyText());
    const configuration = configurationState();
    elements["word-count"].textContent = `${state.words} / 400 palavras`;
    elements["paragraph-count"].textContent = `${state.paragraphs} / 2 parágrafos`;
    elements["word-count"].classList.toggle("count-invalid", state.words > 400);
    elements["paragraph-count"].classList.toggle("count-invalid", touched && state.paragraphs !== 2);
    elements["input-error"].textContent = state.error;
    elements["input-error"].hidden = !touched || !state.error;
    elements.relato.setAttribute("aria-invalid", String(touched && Boolean(state.error)));
    elements["search-button"].disabled = busy || saving || openingRecord || importing || !ready || !importedVectorization || Boolean(state.error) || Boolean(configuration.error);
    elements["save-story-button"].disabled = busy || saving || openingRecord || importing || Boolean(state.error) || Boolean(savedId);
    return state;
  }

  function setBusy(value) {
    busy = value;
    const locked = busy || saving || openingRecord || importing;
    elements.relato.readOnly = locked;
    elements["clear-button"].disabled = locked;
    elements["clear-button"].title = locked ? "Aguarde o término da operação para limpar o relato." : "";
    elements["copy-button"].disabled = locked || !currentResult;
    elements["export-button"].disabled = locked || !currentResult;
    elements["search-button"].textContent = value ? "Buscando ligações…" : "Buscar ligações →";
    elements["save-story-button"].textContent = saving ? "Salvando…" : savedId ? "Relato salvo" : "Salvar relato";
    elements["vetorizacao-file"].disabled = locked;
    for (const id of ["provider-select", "api-key", "model-select", "add-model-button", "new-model-id", "save-model-button", "cancel-model-button"]) {
      elements[id].disabled = locked;
    }
    updateInput();
    updateHistoryControls();
  }

  function forgetVectorization(message = "Importe a vetorização para habilitar a busca.") {
    importedVectorization = null;
    importedText = null;
    elements["vetorizacao-file"].value = "";
    elements["import-feedback"].className = "field-help";
    elements["import-feedback"].textContent = message;
  }

  function useVectorization(record) {
    if (!record || typeof record.id !== "string" || !record.id || !record.relato || typeof record.relato.texto !== "string") {
      throw new Error("O servidor não retornou a vetorização e o relato importados.");
    }
    importedVectorization = record.id;
    importedText = record.relato.texto;
    const counts = record.contagens || {};
    elements["import-feedback"].className = "field-help";
    elements["import-feedback"].textContent = `Vetorização disponível: ${countFormat(counts.periodo || 0)} períodos, ${countFormat(counts.contextual || 0)} janelas contextuais e ${countFormat(counts.documento || 0)} documento(s).`;
  }

  async function importVectorization() {
    if (busy || saving || openingRecord || importing) return;
    const file = elements["vetorizacao-file"].files[0];
    if (!file) return;
    forgetSavedRecord();
    resetResults();
    importedVectorization = null;
    importedText = null;
    elements["request-error"].hidden = true;
    importing = true;
    setBusy(busy);
    elements["import-feedback"].className = "field-help";
    elements["import-feedback"].textContent = "Conferindo e importando a vetorização…";
    try {
      if (file.size > 32 * 1024 * 1024) throw new Error("O arquivo ultrapassa o limite de 32 MiB.");
      let payload;
      try { payload = JSON.parse((await file.text()).replace(/^\uFEFF/u, "")); }
      catch (_) { throw new Error("O arquivo não contém um JSON válido. Exporte novamente o arquivo completo da análise linguística."); }
      const record = await fetchJSON("/api/vetorizacoes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      useVectorization(record);
      elements.relato.value = importedText;
      touched = true;
      elements.processing.hidden = true;
      elements.relato.focus();
    } catch (error) {
      forgetVectorization();
      elements["import-feedback"].className = "field-error";
      elements["import-feedback"].textContent = `Não foi possível importar a vetorização. ${error.message}`;
    } finally {
      importing = false;
      setBusy(busy);
    }
  }

  function forgetSavedRecord() {
    savedId = null;
    savedState = null;
    elements["save-story-feedback"].textContent = "";
    elements["save-story-feedback"].className = "field-help";
    elements["save-story-button"].textContent = "Salvar relato";
  }

  function recordState(state) {
    return { registrado: "Sem busca", executando: "Busca em andamento", concluido: "Busca concluída", erro: "Busca interrompida", interrompido: "Busca interrompida pelo reinício do servidor" }[state] || "Relato salvo";
  }

  function markSavedRecord(id, state) {
    savedId = Number.isSafeInteger(id) && id > 0 ? id : null;
    savedState = state;
    elements["save-story-feedback"].className = "field-help";
    elements["save-story-feedback"].textContent = savedId ? `Relato #${savedId} salvo neste computador. ${recordState(state)}.` : "";
    elements["save-story-button"].textContent = savedId ? "Relato salvo" : "Salvar relato";
    updateInput();
  }

  function updateHistoryControls() {
    const locked = busy || saving || openingRecord || importing || historyLoading;
    elements["refresh-history"].disabled = locked;
    elements["more-history"].disabled = locked;
    elements["more-history"].hidden = historyNext === null;
    elements["history-list"].querySelectorAll("button").forEach((button) => { button.disabled = locked; });
  }

  function renderHistory() {
    elements["history-list"].replaceChildren();
    historyRecords.forEach((record) => {
      const item = node("div", "", "history-item");
      const content = node("div", "", "history-item-content");
      const timestamp = new Date(record.criado_em);
      const date = Number.isNaN(timestamp.getTime()) ? display(record.criado_em) : timestamp.toLocaleString("pt-BR");
      content.append(node("p", `Relato #${record.id}${date ? ` · ${date}` : ""}`, "history-item-title"));
      const text = display(record.texto).replace(/\s+/gu, " ");
      const characters = Array.from(text);
      content.append(node("p", characters.slice(0, 180).join("") + (characters.length > 180 ? "…" : ""), "history-item-preview"));
      const count = Number(record.total_relacoes) || 0;
      const metadata = [recordState(record.estado), `${record.palavras} palavras`, `${count} ${count === 1 ? "relação" : "relações"}`];
      if (record.modelo) metadata.push([record.provedor, record.modelo].filter(Boolean).join(" · "));
      content.append(node("p", metadata.join(" · "), "history-item-meta"));
      const open = node("button", "Abrir", "button button-secondary button-small");
      open.type = "button";
      open.setAttribute("aria-label", `Abrir relato #${record.id}`);
      open.addEventListener("click", () => openRecord(record.id));
      item.append(content, open);
      elements["history-list"].append(item);
    });
    updateHistoryControls();
  }

  async function refreshHistory(reset = true) {
    if (!reset && historyNext === null) return;
    const sequence = ++historyRequest;
    const cursor = reset ? "" : `&antes_id=${encodeURIComponent(historyNext)}`;
    historyLoading = true;
    elements["history-feedback"].className = "field-help";
    elements["history-feedback"].textContent = "Carregando relatos salvos…";
    updateHistoryControls();
    try {
      const response = await fetchJSON(`/api/relatos?limite=20${cursor}`);
      if (sequence !== historyRequest) return;
      const records = list(response.relatos);
      if (reset) historyRecords = records;
      else {
        const known = new Set(historyRecords.map((record) => record.id));
        historyRecords.push(...records.filter((record) => !known.has(record.id)));
      }
      historyNext = Number.isSafeInteger(response.proximo_antes_id) ? response.proximo_antes_id : null;
      renderHistory();
      elements["history-feedback"].textContent = historyRecords.length ? "" : "Nenhum relato salvo ainda. Use Salvar relato ou Buscar ligações.";
    } catch (error) {
      if (sequence !== historyRequest) return;
      elements["history-feedback"].className = "field-error";
      elements["history-feedback"].textContent = `Não foi possível consultar os relatos salvos. ${error.message}`;
    } finally {
      if (sequence === historyRequest) {
        historyLoading = false;
        updateHistoryControls();
      }
    }
  }

  async function saveStory() {
    if (busy || saving || openingRecord || importing || savedId) return;
    touched = true;
    const state = updateInput();
    if (state.error) { elements.relato.focus(); return; }
    saving = true;
    setBusy(busy);
    elements["save-story-feedback"].className = "field-help";
    elements["save-story-feedback"].textContent = "Salvando o relato neste computador…";
    try {
      const payload = { texto: storyText() };
      if (importedVectorization) payload.vetorizacao_id = importedVectorization;
      const record = await fetchJSON("/api/relatos", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!Number.isSafeInteger(record.id)) throw new Error("O servidor não informou o identificador do relato salvo.");
      markSavedRecord(record.id, record.estado);
      refreshHistory();
    } catch (error) {
      elements["save-story-feedback"].className = "field-error";
      elements["save-story-feedback"].textContent = `Não foi possível salvar o relato. ${error.message}`;
    } finally {
      saving = false;
      setBusy(busy);
    }
  }

  async function openRecord(id) {
    if (busy || saving || openingRecord || importing) return;
    openingRecord = true;
    setBusy(busy);
    elements["history-feedback"].className = "field-help";
    elements["history-feedback"].textContent = `Abrindo relato #${id}…`;
    try {
      const record = await fetchJSON(`/api/relatos/${encodeURIComponent(id)}`);
      let vectorization = null;
      let vectorizationError = "";
      if (record.vetorizacao_id) {
        try {
          vectorization = await fetchJSON(`/api/vetorizacoes/${encodeURIComponent(record.vetorizacao_id)}`);
          if (!vectorization.relato || vectorization.relato.texto !== record.texto) throw new Error("A vetorização salva não corresponde ao texto do relato.");
        } catch (error) { vectorizationError = error.message; }
      }
      resetResults();
      forgetVectorization(record.vetorizacao_id ? "Não foi possível recuperar a vetorização deste relato. Importe o arquivo para fazer uma nova busca." : "Este relato não tem vetorização associada. Importe o arquivo para fazer uma busca.");
      if (vectorization && !vectorizationError) useVectorization(vectorization);
      if (vectorizationError) {
        elements["import-feedback"].className = "field-error";
        elements["import-feedback"].textContent += ` ${vectorizationError}`;
      }
      elements.relato.value = record.texto;
      touched = true;
      elements["request-error"].hidden = true;
      elements.processing.hidden = true;
      markSavedRecord(record.id, record.estado);
      if (record.resultado && typeof record.resultado === "object") renderResult(record.resultado);
      if (record.erro) {
        elements["request-error"].textContent = display(record.erro);
        elements["request-error"].hidden = false;
      }
      elements["history-feedback"].textContent = `Relato #${record.id} aberto. ${recordState(record.estado)}.`;
      elements.relato.focus();
      elements.relato.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
    } catch (error) {
      elements["history-feedback"].className = "field-error";
      elements["history-feedback"].textContent = `Não foi possível abrir o relato. ${error.message}`;
    } finally {
      openingRecord = false;
      setBusy(busy);
    }
  }

  function resetResults() {
    currentResult = null;
    elements.results.hidden = true;
    elements["connections-table"].replaceChildren();
    elements["discarded-table"].replaceChildren();
    elements["retrieval-candidates"].replaceChildren();
    elements["rejected-list"].replaceChildren();
    elements["export-feedback"].textContent = "";
    elements["copy-button"].disabled = true;
    elements["export-button"].disabled = true;
    for (const id of ["discarded-section", "rejected-section", "retrieval-section"]) {
      elements[id].hidden = true;
      elements[id].open = false;
    }
  }

  async function fetchJSON(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal, headers: { Accept: "application/json", ...options.headers } });
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(display(data.erro) || `O servidor respondeu com erro ${response.status}.`);
        error.serverResponse = true;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("O servidor local demorou para responder. Confira se ele continua aberto no terminal.");
      if (error instanceof SyntaxError) throw new Error("O servidor retornou uma resposta inesperada. Confira o terminal e tente novamente.");
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function checkStatus() {
    elements["refresh-status"].disabled = true;
    elements["environment-badge"].textContent = "Conferindo…";
    elements["environment-badge"].className = "badge badge-neutral";
    try {
      const status = await fetchJSON("/api/status");
      ready = Boolean(status.pronto);
      elements["environment-badge"].textContent = ready ? "Pronto" : "Requer configuração";
      elements["environment-badge"].className = `badge ${ready ? "badge-ready" : "badge-warning"}`;
      const corpus = status.corpus || {};
      const parts = [];
      if (corpus.fragmentos) parts.push(`${countFormat(corpus.fragmentos)} fragmentos${corpus.blocos ? ` em ${countFormat(corpus.blocos)} blocos` : ""} no acervo local`);
      if (ready) parts.push("Índice disponível. Importe a vetorização da análise linguística e configure as justificativas");
      elements["environment-description"].textContent = parts.join(". ") || "Resolva os itens abaixo e confira o ambiente novamente.";
      const problems = list(status.problemas);
      elements["environment-problems"].replaceChildren();
      if (problems.length) {
        const items = node("ul");
        problems.forEach((problem) => items.append(node("li", problem)));
        elements["environment-problems"].append(items);
      }
      elements["environment-problems"].hidden = !problems.length;
    } catch (error) {
      ready = false;
      elements["environment-badge"].textContent = "Sem conexão";
      elements["environment-badge"].className = "badge badge-warning";
      elements["environment-description"].textContent = "Não foi possível conferir o ambiente. Verifique se o servidor local continua aberto no terminal e clique em Conferir novamente.";
      elements["environment-problems"].replaceChildren(node("p", error.message, "field-error"));
      elements["environment-problems"].hidden = false;
    } finally {
      elements["refresh-status"].disabled = false;
      updateInput();
    }
  }

  function showProgress(title, detail) {
    elements.processing.hidden = false;
    elements["processing-title"].textContent = title;
    elements["processing-detail"].textContent = detail || "Aguarde. Você pode acompanhar as etapas nesta página.";
  }

  function finishError(message) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
    currentJob = null;
    elements.processing.hidden = true;
    elements["request-error"].textContent = message;
    elements["request-error"].hidden = false;
    setBusy(false);
    refreshHistory();
  }

  async function pollJob(id) {
    if (currentJob !== id) return;
    try {
      const job = await fetchJSON(`/api/buscas/${encodeURIComponent(id)}`);
      if (currentJob !== id) return;
      connectionFailures = 0;
      if (job.estado === "concluido") {
        if (!job.resultado || typeof job.resultado !== "object") throw new Error("A busca terminou sem um resultado válido. Confira o terminal do servidor.");
        currentJob = null;
        elements.processing.hidden = true;
        if (savedId) markSavedRecord(savedId, "concluido");
        renderResult(job.resultado);
        setBusy(false);
        refreshHistory();
        elements["results-title"].focus({ preventScroll: true });
        elements.results.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
        return;
      }
      if (job.estado === "erro") {
        if (savedId) markSavedRecord(savedId, "erro");
        finishError(display(job.erro) || "Não foi possível concluir a busca. Confira o terminal do servidor.");
        return;
      }
      showProgress(display(job.etapa) || "Examinando o relato", "A busca está em andamento. Novos envios ficam disponíveis quando ela terminar.");
      pollTimer = setTimeout(() => pollJob(id), 1000);
    } catch (error) {
      if (currentJob !== id) return;
      if (!error.serverResponse && connectionFailures < 3) {
        connectionFailures += 1;
        showProgress("Restabelecendo a conexão", "A busca pode continuar no servidor. Tentando acompanhar seu andamento novamente…");
        pollTimer = setTimeout(() => pollJob(id), 2000);
      } else {
        finishError(`${error.message} A busca pode continuar no servidor; confira o terminal antes de iniciar outra.`);
      }
    }
  }

  function referenceText(reference) {
    if (!reference || typeof reference !== "object") return display(reference);
    const parts = [display(reference.obra)];
    if (reference.volume !== undefined && reference.volume !== null && reference.volume !== "") parts.push(`Volume ${reference.volume}`);
    if (reference.secao) parts.push(display(reference.secao));
    if (list(reference.paginas).length) parts.push(`Páginas ${reference.paginas.join(", ")}`);
    if (reference.nota_paginas) parts.push(display(reference.nota_paginas));
    if (list(reference.pendencias).length) parts.push(`Pendências: ${reference.pendencias.map(display).join("; ")}`);
    return parts.filter(Boolean).join(" · ");
  }

  function referenceNode(reference) {
    const container = node("div", "", "reference");
    if (!reference || typeof reference !== "object") {
      container.append(node("p", display(reference) || "Referência pendente."));
      return container;
    }
    container.append(node("p", reference.obra || "Título pendente", "source-title"));
    const location = [];
    if (reference.volume !== undefined && reference.volume !== null && reference.volume !== "") location.push(`Volume ${reference.volume}`);
    if (reference.secao) location.push(display(reference.secao));
    if (list(reference.paginas).length) location.push(`Páginas ${reference.paginas.join(", ")}`);
    if (location.length) container.append(node("p", location.join(" · ")));
    if (reference.nota_paginas) container.append(node("p", reference.nota_paginas, "reference-note"));
    if (list(reference.pendencias).length) container.append(node("p", `Pendências: ${reference.pendencias.map(display).join("; ")}`, "reference-note"));
    return container;
  }

  function labeled(container, label, value, className = "") {
    if (value === null || value === undefined || value === "") return;
    container.append(node("span", label, "small-label"), node("p", value, className));
  }

  function detailsNode(connection) {
    const details = node("details", "", "source-details");
    details.append(node("summary", "Contexto e detalhes da busca"));
    if (connection.contexto) {
      details.append(node("span", "Contexto teórico ampliado", "small-label"));
      details.append(node("div", connection.contexto, "context-text"));
    }
    const positions = {
      ligacao_id: connection.id,
      paragrafo: connection.paragrafo,
      posicoes_relato: connection.relato ? [connection.relato.inicio, connection.relato.fim] : [],
      bloco_id: connection.bloco_id,
      fragmentos_ids: connection.fragmentos_ids,
      posicoes_freud_no_bloco: connection.freud ? [connection.freud.inicio, connection.freud.fim] : [],
    };
    details.append(node("span", "Identificadores e posições", "small-label"));
    details.append(node("p", "Posições em caracteres Unicode: início incluído, fim excluído.", "reference-note"));
    details.append(node("pre", JSON.stringify(positions, null, 2), "technical-details"));
    if (connection.pontuacoes) {
      details.append(node("span", "Pontuações de recuperação por método", "small-label"));
      details.append(node("pre", JSON.stringify(connection.pontuacoes, null, 2), "technical-details"));
    }
    return details;
  }

  function tableNode(connections, caption) {
    const container = node("div");
    container.append(node("p", "Em telas menores, deslize a tabela para ver todas as colunas.", "table-hint"));
    const wrap = node("div", "", "table-wrap");
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", caption);
    const table = node("table", "", "connections");
    table.append(node("caption", caption));
    const head = node("thead");
    const headRow = node("tr");
    headings.forEach((heading) => {
      const cell = node("th", heading);
      cell.scope = "col";
      headRow.append(cell);
    });
    head.append(headRow);
    table.append(head);
    const body = node("tbody");
    connections.forEach((connection) => {
      const row = node("tr");
      const cells = Array.from({ length: 6 }, () => node("td"));
      cells[0].append(node("span", connection.paragrafo || "Parágrafo pendente", "badge badge-neutral"));
      cells[0].append(node("blockquote", connection.relato && connection.relato.texto));
      if (connection.observacao) labeled(cells[0], "Observação textual", connection.observacao);
      cells[1].append(node("blockquote", connection.freud && connection.freud.texto));
      cells[1].append(detailsNode(connection));
      cells[2].append(node("p", connection.ligacao));
      if (connection.conceito) labeled(cells[2], "Conceito candidato", connection.conceito);
      cells[3].append(node("p", connection.justificativa));
      cells[4].append(referenceNode(connection.referencia));
      const situation = ["pertinente", "parcial", "descartada"].includes(connection.situacao) ? connection.situacao : "parcial";
      const labels = { pertinente: "Pertinente", parcial: "Parcial", descartada: "Descartada" };
      cells[5].append(node("span", labels[situation], `badge badge-${situation}`));
      labeled(cells[5], "Limites", connection.limites);
      const alternatives = list(connection.alternativas);
      if (alternatives.length) {
        cells[5].append(node("span", "Leituras alternativas", "small-label"));
        const items = node("ul");
        alternatives.forEach((alternative) => items.append(node("li", alternative)));
        cells[5].append(items);
      }
      row.append(...cells);
      body.append(row);
    });
    table.append(body);
    wrap.append(table);
    container.append(wrap);
    return container;
  }

  function renderResult(result) {
    currentResult = result;
    const connections = list(result.ligacoes);
    const discarded = list(result.descartadas);
    const rejected = list(result.rejeitadas);
    const candidates = list(result.candidatos);
    elements.results.hidden = false;
    elements["results-summary"].textContent = `${connections.length} ${connections.length === 1 ? "ligação para revisão" : "ligações para revisão"}${discarded.length ? ` · ${discarded.length} ${discarded.length === 1 ? "descartada" : "descartadas"}` : ""}.`;
    const message = display(result.mensagem) || (!connections.length ? "Os candidatos examinados não sustentaram uma relação suficiente. Isso não demonstra ausência de material pertinente em todo o acervo." : "");
    elements["result-message"].textContent = message;
    elements["result-message"].hidden = !message;
    elements["connections-table"].replaceChildren();
    if (connections.length) elements["connections-table"].append(tableNode(connections, "Ligações propostas para revisão"));
    elements["discarded-section"].hidden = !discarded.length;
    elements["discarded-summary"].textContent = `Relações descartadas (${discarded.length})`;
    elements["discarded-table"].replaceChildren();
    if (discarded.length) elements["discarded-table"].append(tableNode(discarded, "Relações descartadas durante a avaliação"));
    elements["rejected-section"].hidden = !rejected.length;
    elements["rejected-summary"].textContent = `Propostas rejeitadas na conferência (${rejected.length})`;
    elements["rejected-list"].replaceChildren();
    rejected.forEach((item) => elements["rejected-list"].append(node("li", display(item.motivo) || display(item))));
    elements["retrieval-section"].hidden = !candidates.length;
    elements["retrieval-method"].textContent = result.metodo_fusao ? `Método de fusão: ${display(result.metodo_fusao)}` : "";
    elements["retrieval-candidates"].replaceChildren();
    const evaluation = result.avaliacao || {};
    const evaluatedBlocks = new Set(list(evaluation.blocos_avaliados_ids));
    const unevaluatedBlocks = new Set(list(evaluation.blocos_nao_avaliados_ids));
    candidates.forEach((candidate) => {
      const card = node("details", "", "retrieval-card");
      const title = candidate.referencia && candidate.referencia.obra ? candidate.referencia.obra : candidate.bloco_id;
      const summary = node("summary", title || "Fonte recuperada");
      if (evaluatedBlocks.has(candidate.bloco_id)) summary.append(" · ", node("span", "Avaliado pelo provedor", "badge badge-neutral"));
      if (unevaluatedBlocks.has(candidate.bloco_id)) summary.append(" · ", node("span", "Não avaliado", "badge badge-warning"));
      card.append(summary);
      if (unevaluatedBlocks.has(candidate.bloco_id) && evaluation.motivo_limite_contexto) {
        card.append(node("p", evaluation.motivo_limite_contexto, "field-help"));
      }
      card.append(referenceNode(candidate.referencia));
      card.append(node("div", candidate.texto, "context-text"));
      card.append(node("pre", JSON.stringify({ bloco_id: candidate.bloco_id, fragmentos_ids: candidate.fragmentos_ids, pontuacoes: candidate.pontuacoes }, null, 2), "technical-details"));
      elements["retrieval-candidates"].append(card);
    });
    elements["copy-button"].disabled = false;
    elements["export-button"].disabled = false;
  }

  async function submitSearch(event) {
    event.preventDefault();
    if (busy || saving || openingRecord || importing) return;
    touched = true;
    configurationTouched = true;
    const state = updateInput();
    if (state.error) {
      elements.relato.focus();
      return;
    }
    const configuration = configurationState();
    if (configuration.error) {
      elements[configuration.hasModel ? "api-key" : "model-select"].focus();
      return;
    }
    if (!importedVectorization) {
      elements["import-feedback"].className = "field-error";
      elements["import-feedback"].textContent = "Importe a vetorização correspondente ao relato antes de buscar.";
      elements["vetorizacao-file"].focus();
      return;
    }
    if (!ready) return;
    const originalText = storyText();
    resetResults();
    elements["request-error"].hidden = true;
    connectionFailures = 0;
    setBusy(true);
    showProgress("Iniciando a busca", "Usando os vetores de períodos, janelas contextuais e documento importados.");
    try {
      const payload = {
        texto: originalText,
        provedor: elements["provider-select"].value,
        modelo: elements["model-select"].value,
        chave_api: elements["api-key"].value.trim(),
        vetorizacao_id: importedVectorization,
      };
      if (savedId && savedState === "registrado") payload.relato_id = savedId;
      const response = await fetchJSON("/api/buscas", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!response.id) throw new Error("O servidor não informou o identificador da busca.");
      currentJob = response.id;
      markSavedRecord(response.relato_id, "executando");
      refreshHistory();
      pollJob(currentJob);
    } catch (error) {
      finishError(error.message);
    }
  }

  function copiedTableText() {
    const sanitizeCell = (value) => display(value).replace(/[\t\r\n]+/gu, " ");
    const rows = list(currentResult && currentResult.ligacoes).map((connection) => [
      `${connection.paragrafo}: ${connection.relato && connection.relato.texto || ""}`,
      connection.freud && connection.freud.texto,
      [connection.ligacao, connection.conceito ? `Conceito candidato: ${connection.conceito}` : ""].filter(Boolean).join(" · "),
      connection.justificativa,
      referenceText(connection.referencia),
      [connection.situacao, connection.limites, list(connection.alternativas).length ? `Alternativas: ${connection.alternativas.join("; ")}` : ""].filter(Boolean).join(" · "),
    ]);
    return [headings, ...rows].map((row) => row.map(sanitizeCell).join("\t")).join("\n");
  }

  async function copyTable() {
    if (!currentResult || busy) return;
    const text = copiedTableText();
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("Clipboard indisponível");
      await navigator.clipboard.writeText(text);
      elements["export-feedback"].textContent = "Tabela copiada. Você pode colá-la em uma planilha ou editor de texto.";
    } catch (_) {
      const temporary = node("textarea");
      temporary.value = text;
      temporary.setAttribute("aria-label", "Tabela para copiar");
      temporary.style.position = "fixed";
      temporary.style.left = "-10000px";
      document.body.append(temporary);
      temporary.select();
      let success = false;
      try { success = document.execCommand("copy"); } catch (_) { /* Show an actionable message below. */ }
      temporary.remove();
      elements["copy-button"].focus();
      elements["export-feedback"].textContent = success ? "Tabela copiada. Você pode colá-la em uma planilha ou editor de texto." : "O navegador não permitiu copiar. Selecione as células da tabela para copiar manualmente ou use Exportar JSON.";
    }
  }

  function exportJSON() {
    if (!currentResult || busy) return;
    const blob = new Blob([JSON.stringify(currentResult, null, 2) + "\n"], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = node("a");
    link.href = url;
    link.download = `agente-analista-${new Date().toISOString().slice(0, 19).replace(/[:T]/gu, "-")}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    elements["export-feedback"].textContent = "JSON exportado com o relato original, fontes, posições e detalhes da busca.";
  }

  elements["search-form"].addEventListener("submit", submitSearch);
  elements["vetorizacao-file"].addEventListener("change", importVectorization);
  elements["api-key"].addEventListener("input", invalidateConfiguration);
  elements["model-select"].addEventListener("change", invalidateConfiguration);
  elements["provider-select"].addEventListener("change", () => {
    if (busy) return;
    elements["api-key"].value = "";
    renderModels();
    closeModelPanel(false);
    elements["model-feedback"].textContent = "Provedor alterado. Informe a chave correspondente e selecione um modelo.";
    invalidateConfiguration();
  });
  elements["add-model-button"].addEventListener("click", () => {
    if (busy) return;
    if (!elements["add-model-panel"].hidden) {
      closeModelPanel();
      return;
    }
    elements["add-model-panel"].hidden = false;
    elements["add-model-button"].setAttribute("aria-expanded", "true");
    elements["new-model-id"].focus();
  });
  elements["save-model-button"].addEventListener("click", addModel);
  elements["cancel-model-button"].addEventListener("click", () => closeModelPanel());
  elements["new-model-id"].addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); addModel(); }
    if (event.key === "Escape") { event.preventDefault(); closeModelPanel(); }
  });
  elements.relato.addEventListener("input", () => {
    touched = true;
    forgetSavedRecord();
    forgetVectorization("O relato foi editado. Importe a vetorização desse texto atualizado antes de buscar.");
    resetResults();
    elements["request-error"].hidden = true;
    updateInput();
  });
  elements["clear-button"].addEventListener("click", () => {
    if (busy || saving || openingRecord || importing) return;
    elements.relato.value = "";
    touched = false;
    forgetSavedRecord();
    forgetVectorization();
    resetResults();
    elements["request-error"].hidden = true;
    elements.processing.hidden = true;
    updateInput();
    elements.relato.focus();
  });
  elements["refresh-status"].addEventListener("click", checkStatus);
  elements["save-story-button"].addEventListener("click", saveStory);
  elements["refresh-history"].addEventListener("click", () => refreshHistory());
  elements["more-history"].addEventListener("click", () => refreshHistory(false));
  elements["copy-button"].addEventListener("click", copyTable);
  elements["export-button"].addEventListener("click", exportJSON);
  elements["api-key"].value = "";
  renderModels();
  updateInput();
  checkStatus();
  refreshHistory();
})();
