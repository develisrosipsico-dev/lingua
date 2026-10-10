/* Abas progressivas: sem JavaScript, os links e todas as etapas continuam disponíveis. */
(() => {
  "use strict";

  const list = document.querySelector("[data-stage-tabs]");
  if (!list) return;
  const tabs = Array.from(list.querySelectorAll("[data-stage-tab]"));
  const panels = new Map(Array.from(document.querySelectorAll("[data-stage-panel]"))
    .map((panel) => [panel.dataset.stagePanel, panel]));
  if (!tabs.length || tabs.some((tab) => !panels.has(tab.dataset.stageTab))) return;

  const stages = new Map(tabs.map((tab) => [tab.dataset.stageTab, tab]));
  const queryStages = [
    ["vetorizacao_execucao_id", "vetorizacao"], ["contexto_execucao_id", "contexto"],
    ["execucao_id", "regras"], ["analise_id", "sintaxe"],
    ["anotacao_id", "morfologia"], ["segmentacao_id", "segmentacao"],
    ["preparacao_id", "preparacao"],
  ];
  let activeStage = null;

  const stageFromUrl = () => {
    const url = new URL(window.location.href);
    const hashStage = url.hash.startsWith("#etapa-") ? url.hash.slice(7) : null;
    if (stages.has(hashStage)) return hashStage;
    const selection = queryStages.find(([parameter]) => url.searchParams.has(parameter));
    return selection ? selection[1] : list.dataset.defaultStage;
  };

  const select = (stage, { focus = false, scroll = false, remember = false } = {}) => {
    if (!stages.has(stage)) stage = tabs[0].dataset.stageTab;
    const focused = document.activeElement;
    const focusWasInPreviousPanel = activeStage && activeStage !== stage && panels.get(activeStage).contains(focused);
    tabs.forEach((tab) => {
      const selected = tab.dataset.stageTab === stage;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      const panel = panels.get(tab.dataset.stageTab);
      panel.hidden = !selected;
      panel.tabIndex = selected ? 0 : -1;
    });
    activeStage = stage;
    if (focus || focusWasInPreviousPanel) stages.get(stage).focus({ preventScroll: true });
    if (remember) {
      const url = new URL(window.location.href);
      url.hash = `etapa-${stage}`;
      if (url.href !== window.location.href) window.history.pushState(window.history.state, "", url);
    }
    if (scroll) {
      // O marcador permanece no fluxo normal mesmo quando a barra fica fixa.
      const start = document.querySelector("[data-stage-start]");
      if (start) window.scrollTo({ top: Math.max(0, start.getBoundingClientRect().top + window.scrollY - 8), behavior: "auto" });
    }
  };

  list.setAttribute("role", "tablist");
  list.setAttribute("aria-label", "Etapas da análise");
  list.setAttribute("aria-orientation", "horizontal");
  tabs.forEach((tab) => {
    const panel = panels.get(tab.dataset.stageTab);
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", panel.id);
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tab.id);
    panel.addEventListener("focus", () => {
      // Painéis longos devem abrir pelo início quando Tab entra no conteúdo.
      panel.scrollIntoView({ block: "start", behavior: "auto" });
    });
  });
  const help = document.querySelector("[data-stage-help]");
  if (help) help.textContent = "Use as setas ← → para mudar de aba e Tab para entrar no conteúdo.";
  const navigation = document.querySelector("[data-stage-navigation]");
  const measureNavigation = () => {
    if (navigation) document.documentElement.style.setProperty("--stage-navigation-height", `${navigation.getBoundingClientRect().height + 12}px`);
  };
  measureNavigation();
  if (navigation && "ResizeObserver" in window) new ResizeObserver(measureNavigation).observe(navigation);
  else window.addEventListener("resize", measureNavigation);

  document.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("[data-stage-tab], [data-stage-link]");
    if (!link) return;
    const stage = new URL(link.href, window.location.href).hash.slice(7);
    if (!stages.has(stage)) return;
    event.preventDefault();
    select(stage, { focus: true, scroll: true, remember: true });
  });

  list.addEventListener("keydown", (event) => {
    const tab = event.target.closest("[data-stage-tab]");
    if (!tab) return;
    const index = tabs.indexOf(tab);
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else if (event.key === " ") next = index;
    else return;
    event.preventDefault();
    select(tabs[next].dataset.stageTab, { focus: true, scroll: true, remember: true });
  });

  const restore = () => {
    const stage = stageFromUrl();
    if (stage !== activeStage) select(stage, { scroll: true });
  };
  window.addEventListener("popstate", restore);
  window.addEventListener("hashchange", restore);
  select(stageFromUrl(), { scroll: window.location.hash.startsWith("#etapa-") });
  const url = new URL(window.location.href);
  url.hash = `etapa-${activeStage}`;
  window.history.replaceState(window.history.state, "", url);
})();
