/* Complete o processamento antes de baixar o pacote, sem perder a página atual. */
(() => {
  "use strict";
  const form = document.querySelector("[data-review-export]");
  if (!form) return;
  const area = form.closest(".review-export");
  const status = area.querySelector("[data-review-status]");
  const detail = area.querySelector("[data-review-detail]");
  const button = form.querySelector("button");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
    status.hidden = false;
    detail.hidden = true;
    status.classList.remove("review-export-warning");
    status.textContent = "Processando todo o texto e preparando o pacote. A vetorização local pode levar alguns minutos…";
    try {
      const response = await fetch(form.action, {method: "POST", body: new FormData(form)});
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.erro || "Não foi possível gerar o pacote.");
      }
      if (!response.headers.get("Content-Type")?.includes("application/zip")) {
        throw new Error("O servidor não retornou o pacote de revisão.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const download = document.createElement("a");
      download.href = url;
      download.download = response.headers.get("Content-Disposition")?.match(/filename="?([^";]+)"?/)?.[1]
        || `lingua-revisao-${form.dataset.recordId}.zip`;
      document.body.append(download);
      download.click();
      download.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      const complete = response.headers.get("X-Lingua-Revisao-Estado") === "completo";
      status.textContent = complete ? "Processamento concluído. O pacote de revisão foi baixado."
        : "O pacote foi baixado com etapas pendentes ou com falha. Consulte o diagnóstico no ZIP; os resultados concluídos foram preservados.";
      status.classList.toggle("review-export-warning", !complete);
      const target = response.headers.get("X-Lingua-Revisao-Detalhe");
      if (target) {
        const next = new URL(target, window.location.href);
        if (next.origin === window.location.origin) {
          next.hash = window.location.hash;
          detail.href = next.href;
          detail.hidden = false;
        }
      }
    } catch (error) {
      status.classList.add("review-export-warning");
      status.textContent = error instanceof TypeError
        ? "Não foi possível comunicar com o Língua. Confira o terminal e use a aba da execução atual. As etapas concluídas permanecem salvas."
        : error.message;
    } finally {
      button.disabled = false;
      form.removeAttribute("aria-busy");
    }
  });
})();
