/* Atualização local dos trabalhos de vetorização. */
(() => {
  "use strict";

  const stateLabels = {
    enfileirada: "Na fila",
    executando: "Gerando vetores",
    concluida: "Concluída",
    falhou: "Falhou",
    interrompida: "Interrompida",
  };
  const activeStates = new Set(["enfileirada", "executando"]);
  const finalStates = new Set(["concluida", "falhou", "interrompida"]);

  document.querySelectorAll("[data-vector-status-url]").forEach((panel) => {
    const statusUrl = new URL(panel.dataset.vectorStatusUrl, window.location.href);
    if (statusUrl.origin !== window.location.origin) return;
    const detailUrl = new URL(panel.dataset.vectorDetailUrl || window.location.href, window.location.href);
    if (detailUrl.origin !== window.location.origin) return;

    const state = panel.querySelector("[data-vector-state]");
    const progress = panel.querySelector("[data-vector-progress]");
    const label = panel.querySelector("[data-vector-progress-label]");
    const error = panel.querySelector("[data-vector-poll-error]");
    let retryDelay = 1500;

    const poll = async () => {
      try {
        const response = await fetch(statusUrl.href, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          cache: "no-store",
        });
        if (!response.ok) throw new Error("status_indisponivel");
        const job = await response.json();
        if (!activeStates.has(job.estado) && !finalStates.has(job.estado)) {
          throw new Error("estado_desconhecido");
        }
        if (state) state.textContent = stateLabels[job.estado];
        if (error) error.hidden = true;

        const total = job.progresso?.total;
        const completed = job.progresso?.concluidos;
        if (Number.isInteger(total) && total > 0 && Number.isInteger(completed) && completed >= 0 && completed <= total) {
          if (progress) {
            progress.max = total;
            progress.value = completed;
          }
          if (label) label.textContent = `${completed} de ${total} artefatos concluídos.`;
        } else {
          if (progress) progress.removeAttribute("value");
          if (label) label.textContent = "Preparando o processamento.";
        }

        if (finalStates.has(job.estado)) {
          window.location.assign(detailUrl.href);
          return;
        }
        retryDelay = 1500;
      } catch (_) {
        if (error) {
          error.textContent = "Não foi possível atualizar o progresso. Uma nova consulta será feita automaticamente.";
          error.hidden = false;
        }
        retryDelay = Math.min(retryDelay * 2, 15000);
      }
      window.setTimeout(poll, retryDelay);
    };

    window.setTimeout(poll, retryDelay);
  });
})();
