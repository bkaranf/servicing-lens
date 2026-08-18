"use strict";

const menuButton = document.querySelector(".menu-toggle");
const primaryNavigation = document.querySelector("#primary-nav");
const evidenceDialog = document.querySelector("#provenance-dialog");
const evidenceContent = document.querySelector("#provenance-content");
const evidenceStatus = document.querySelector("#provenance-status");
const evidenceTitle = document.querySelector("#provenance-title");
const dialogClose = document.querySelector(".dialog-close");
let evidenceTrigger = null;
let evidenceRequest = null;

function element(tagName, text, className) {
  const node = document.createElement(tagName);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function valueOrDash(value) {
  return value === undefined || value === null || value === "" ? "Not available" : String(value);
}

function term(list, label, value) {
  list.append(element("dt", label), element("dd", valueOrDash(value)));
}

function safeHttpsUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" ? parsed.href : null;
  } catch (_error) {
    return null;
  }
}

function safeLocatorUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value, window.location.origin);
    return parsed.origin === window.location.origin &&
      parsed.pathname.startsWith("/evidence/") &&
      parsed.hash === "#cited-source-locator" ? parsed.href : null;
  } catch (_error) {
    return null;
  }
}

function locatorTerm(list, label, value, url) {
  const definition = element("dd");
  const safeUrl = safeLocatorUrl(url);
  if (safeUrl) {
    const link = element("a", valueOrDash(value), "locator-link");
    link.href = safeUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", `${valueOrDash(value)}. Open precise retained-source locator in a new tab.`);
    definition.append(link);
  } else {
    definition.textContent = valueOrDash(value);
  }
  list.append(element("dt", label), definition);
}

function section(title) {
  const wrapper = element("section", null, "evidence-section");
  wrapper.append(element("h4", title));
  return wrapper;
}

function renderEvidence(item) {
  evidenceContent.replaceChildren();
  evidenceStatus.textContent = "Evidence loaded.";
  evidenceStatus.setAttribute("role", "status");
  evidenceTitle.textContent = `${valueOrDash(item.ticker)} · ${valueOrDash(item.metric_name)}`;

  const heading = element("div", null, "evidence-heading");
  heading.append(
    element("h3", `${valueOrDash(item.period_type)} observation`),
    element("code", valueOrDash(item.observation_id || item.id)),
  );

  const values = element("div", null, "evidence-value-grid");
  const reported = element("article");
  reported.append(element("span", "Reported label and value"), element("strong", `${valueOrDash(item.reported_label)}: ${valueOrDash(item.reported_value)}`));
  const normalized = element("article");
  normalized.append(
    element("span", "Exact normalized value"),
    element("strong", `${valueOrDash(item.normalized_value)} · ${valueOrDash(item.currency)} · ${valueOrDash(item.scale)} · ${valueOrDash(item.unit)}`),
  );
  values.append(reported, normalized);

  const semantics = section("Observation semantics");
  const semanticList = element("dl", null, "evidence-grid");
  term(semanticList, "Observation ID", item.observation_id || item.id);
  term(semanticList, "Metric ID", item.metric_id);
  term(semanticList, "Metric semantic version", item.metric_version);
  term(semanticList, "Reporting entity", item.reporting_entity_id);
  term(semanticList, "Reporting scope", item.reporting_scope_id);
  term(semanticList, "Portfolio population", item.portfolio_population);
  term(semanticList, "Fiscal period", `Q${valueOrDash(item.fiscal_quarter)} ${valueOrDash(item.fiscal_year)}`);
  term(semanticList, "Period dates", `${valueOrDash(item.period_start)} through ${valueOrDash(item.period_end)}`);
  term(semanticList, "Unit / currency / scale", `${valueOrDash(item.unit)} / ${valueOrDash(item.currency)} / ${valueOrDash(item.scale)}`);
  term(semanticList, "Reported precision", item.reported_precision);
  term(semanticList, "Methodology", item.methodology);
  term(semanticList, "Observation state", item.state);
  term(semanticList, "Publication state", item.publication_state);
  term(semanticList, "Knowledge interval", `${valueOrDash(item.knowledge_from)} through ${valueOrDash(item.knowledge_to)}`);
  term(semanticList, "Valid interval", `${valueOrDash(item.valid_from)} through ${valueOrDash(item.valid_to)}`);
  semantics.append(semanticList);

  const extraction = section("Extraction and validation");
  const extractionList = element("dl", null, "evidence-grid");
  term(extractionList, "Extraction method", item.extraction_method);
  term(extractionList, "Validation status", item.validation_status);
  term(extractionList, "Validation summary", JSON.stringify(item.validation_summary || {}));
  locatorTerm(
    extractionList,
    "Evidence locator",
    item.evidence_locator,
    item.evidence_locator_url,
  );
  extraction.append(extractionList);

  const source = section("Immutable source evidence");
  const evidence = item.evidence || {};
  const sourceList = element("dl", null, "evidence-grid");
  term(sourceList, "Evidence ID", item.evidence_id || evidence.id);
  term(sourceList, "Source class", item.source_class || evidence.source_class);
  term(sourceList, "Accession / regulatory ID", item.accession_or_identifier || evidence.accession_or_identifier);
  term(sourceList, "Published", item.published_at || evidence.published_at);
  term(sourceList, "Retrieved", item.retrieved_at || evidence.retrieved_at);
  term(sourceList, "Content SHA-256", evidence.content_sha256);
  term(sourceList, "Media type", evidence.media_type);
  term(sourceList, "Parser version", evidence.parser_version);
  term(sourceList, "Retention location", evidence.retention_location);
  source.append(sourceList);
  const excerpt = element("p", valueOrDash(item.bounded_excerpt || evidence.bounded_excerpt), "evidence-excerpt");
  source.append(excerpt);

  const revisions = section("Revision history");
  const history = Array.isArray(item.revision_history) ? item.revision_history : [];
  if (history.length === 0) {
    revisions.append(element("p", "No superseding revisions are recorded for this observation.", "no-revisions"));
  } else {
    const list = element("ol", null, "revision-list");
    history.forEach((revision) => {
      const entry = element("li");
      entry.textContent = `${valueOrDash(revision.created_at)} — ${valueOrDash(revision.reason)} (${valueOrDash(revision.revision_id)})`;
      list.append(entry);
    });
    revisions.append(list);
  }

  evidenceContent.append(heading, values, semantics, extraction, source, revisions);
  const sourceUrl = safeHttpsUrl(item.source_url || evidence.original_url);
  if (sourceUrl) {
    const link = element("a", "Open retained authoritative source in a new tab ↗", "source-link");
    link.href = sourceUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    evidenceContent.append(link);
  }
}

function renderEvidenceError(message) {
  evidenceStatus.textContent = message;
  evidenceStatus.setAttribute("role", "alert");
  evidenceContent.replaceChildren();
  const wrapper = element("section", null, "system-message unavailable compact");
  wrapper.append(
    element("h3", "Evidence could not be loaded"),
    element("p", "The observation remains unchanged. Close this view and try again, or follow the JSON evidence link directly."),
  );
  const retry = element("button", "Retry evidence request", "primary-button");
  retry.type = "button";
  retry.addEventListener("click", () => {
    if (evidenceTrigger) openEvidence(evidenceTrigger);
  });
  wrapper.append(retry);
  evidenceContent.append(wrapper);
}

async function openEvidence(trigger) {
  if (!evidenceDialog || !evidenceContent || !evidenceStatus) return;
  evidenceTrigger = trigger;
  if (evidenceRequest) evidenceRequest.abort();
  evidenceRequest = new AbortController();
  evidenceTitle.textContent = "Observation evidence";
  evidenceStatus.textContent = "Loading complete evidence…";
  evidenceStatus.setAttribute("role", "status");
  evidenceContent.replaceChildren();
  if (!evidenceDialog.open) evidenceDialog.showModal();
  if (dialogClose) dialogClose.focus();

  try {
    const identifier = encodeURIComponent(trigger.dataset.observationId || "");
    const response = await fetch(`/api/v1/observations/${identifier}`, {
      headers: { Accept: "application/json" },
      signal: evidenceRequest.signal,
    });
    if (!response.ok) throw new Error(`Evidence request failed with status ${response.status}.`);
    renderEvidence(await response.json());
  } catch (error) {
    if (error.name !== "AbortError") renderEvidenceError(error.message || "Evidence request failed.");
  }
}

document.addEventListener("click", (event) => {
  const trigger = event.target.closest(".provenance-trigger");
  if (!trigger) return;
  event.preventDefault();
  openEvidence(trigger);
});

document.addEventListener("keydown", (event) => {
  const trigger = event.target.closest?.(".provenance-trigger");
  if (trigger && event.key === " ") {
    event.preventDefault();
    openEvidence(trigger);
  }
});

if (dialogClose && evidenceDialog) {
  dialogClose.addEventListener("click", () => evidenceDialog.close());
  evidenceDialog.addEventListener("close", () => {
    if (evidenceRequest) evidenceRequest.abort();
    if (evidenceTrigger) evidenceTrigger.focus();
  });
  evidenceDialog.addEventListener("click", (event) => {
    if (event.target === evidenceDialog) evidenceDialog.close();
  });
  evidenceDialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      evidenceDialog.close();
    }
  });
}

if (menuButton && primaryNavigation) {
  menuButton.addEventListener("click", () => {
    const isOpen = menuButton.getAttribute("aria-expanded") === "true";
    menuButton.setAttribute("aria-expanded", String(!isOpen));
    primaryNavigation.classList.toggle("open", !isOpen);
  });
  primaryNavigation.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      primaryNavigation.classList.remove("open");
      menuButton.setAttribute("aria-expanded", "false");
      menuButton.focus();
    }
  });
}

const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
function activateTab(activeTab, moveFocus = true) {
  tabs.forEach((tab) => {
    const selected = tab === activeTab;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    const panel = document.querySelector(`#${tab.getAttribute("aria-controls")}`);
    if (panel) panel.hidden = !selected;
  });
  if (moveFocus) activeTab.focus();
}

const companyRowsContainer = document.querySelector("#company-rows");
const companyRows = Array.from(document.querySelectorAll(".company-row"));
const companySearch = document.querySelector("#company-search");
const companySort = document.querySelector("#company-sort");
const companyEmpty = document.querySelector("#company-empty");
const matchCount = document.querySelector("#match-count");

function refreshCompanyRows() {
  if (!companyRowsContainer) return;
  const query = (companySearch?.value || "").trim().toLocaleLowerCase();
  const sortKey = companySort?.value || "upb";
  companyRows.sort((left, right) => {
    const leftValue = BigInt(left.dataset[`sort${sortKey[0].toUpperCase()}${sortKey.slice(1)}`] || "0");
    const rightValue = BigInt(right.dataset[`sort${sortKey[0].toUpperCase()}${sortKey.slice(1)}`] || "0");
    if (leftValue === rightValue) return (left.dataset.search || "").localeCompare(right.dataset.search || "");
    return leftValue > rightValue ? -1 : 1;
  });
  let visible = 0;
  companyRows.forEach((row) => {
    const matches = !query || (row.dataset.search || "").includes(query);
    row.hidden = !matches;
    if (matches) visible += 1;
    companyRowsContainer.append(row);
  });
  if (matchCount) matchCount.textContent = String(visible);
  if (companyEmpty) companyEmpty.hidden = visible !== 0;
}

companySearch?.addEventListener("input", refreshCompanyRows);
companySort?.addEventListener("change", refreshCompanyRows);
document.querySelector("#clear-company-search")?.addEventListener("click", () => {
  companySearch.value = "";
  companySearch.focus();
  refreshCompanyRows();
});
refreshCompanyRows();

const comparisonCards = Array.from(document.querySelectorAll(".compare-card"));
const kpiSelectors = Array.from(document.querySelectorAll(".kpi-selector"));

function updateKpiSlot(selector) {
  const slot = selector.dataset.slot;
  const key = selector.value;
  comparisonCards.forEach((card) => {
    card.querySelectorAll(`.kpi-slot[data-slot="${slot}"] .kpi-option`).forEach((option) => {
      option.hidden = option.dataset.key !== key;
    });
  });
}

kpiSelectors.forEach((selector) => {
  updateKpiSlot(selector);
  selector.addEventListener("change", () => updateKpiSlot(selector));
});
const earningsSearch = document.querySelector("#earnings-search");
const earningsCards = Array.from(document.querySelectorAll(".earnings-card"));
const earningsStatus = document.querySelector("#earnings-status");
const earningsOptions = Array.from(document.querySelectorAll("#earnings-companies option"));

function updateEarnings() {
  if (!earningsCards.length) return;
  const companyId = window.ServicingLensState.resolveEarningsCompany(
    earningsSearch?.value || "",
    earningsOptions.map((option) => ({
      companyId: option.dataset.companyId,
      value: option.value,
    })),
    earningsCards.map((card) => ({
      companyId: card.dataset.companyId,
      searchText: card.dataset.search || "",
    })),
  );
  const match = earningsCards.find((card) => card.dataset.companyId === companyId);
  earningsCards.forEach((card) => {
    card.hidden = card !== match;
  });
  if (earningsStatus) {
    earningsStatus.textContent = match ? "" : "No governed company matches. Search by legal name or ticker; servicing platform names are unavailable.";
  }
}

earningsSearch?.addEventListener("input", updateEarnings);

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => activateTab(tab, false));
  tab.addEventListener("keydown", (event) => {
    let targetIndex = null;
    if (event.key === "ArrowRight") targetIndex = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") targetIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") targetIndex = 0;
    if (event.key === "End") targetIndex = tabs.length - 1;
    if (targetIndex !== null) {
      event.preventDefault();
      activateTab(tabs[targetIndex]);
    }
  });
});
