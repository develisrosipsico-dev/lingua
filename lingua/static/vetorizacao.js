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
    const jobError = panel.querySelector("[data-vector-job-error]");
    let retryDelay = 1500;
    let finished = false;

    const poll = async () => {
      if (finished) return;
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
        document.querySelectorAll("[data-vector-history-execution-id]").forEach((link) => {
          if (link.dataset.vectorHistoryExecutionId === job.execucao_id) {
            link.textContent = `${link.dataset.vectorHistoryProfileId} · ${stateLabels[job.estado]}`;
          }
        });

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
          finished = true;
          if (progress) progress.hidden = true;
          if (label) {
            label.textContent = job.estado === "concluida" ? "Vetorização concluída."
              : job.estado === "falhou" ? "A vetorização falhou." : "A vetorização foi interrompida.";
          }
          if (jobError) {
            jobError.hidden = job.estado === "concluida";
            const message = job.erro?.mensagem;
            jobError.textContent = typeof message === "string" && message.trim() ? message
              : job.estado === "falhou" ? "Abra os detalhes da vetorização para verificar o motivo da falha."
                : job.estado === "interrompida" ? "Abra os detalhes da vetorização para verificar a interrupção." : "";
          }
          const stagePanel = panel.closest("[data-stage-panel]");
          if (stagePanel?.hidden) {
            // Outra etapa pode conter uma edição em andamento: preserve a página.
            let result = panel.querySelector("[data-vector-detail-link]");
            if (!result) {
              result = document.createElement("a");
              result.dataset.vectorDetailLink = "";
              result.className = "download-link";
              panel.append(result);
            }
            detailUrl.hash = "etapa-vetorizacao";
            result.href = detailUrl.href;
            result.textContent = job.estado === "concluida" ? "Ver resultado da vetorização →" : "Ver detalhes da vetorização →";
            return;
          }
          if (stagePanel) detailUrl.hash = "etapa-vetorizacao";
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
