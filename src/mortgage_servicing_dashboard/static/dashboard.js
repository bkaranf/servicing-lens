const dialog = document.querySelector("#provenance-dialog");
const content = document.querySelector("#provenance-content");

function safe(value) {
  const node = document.createElement("span");
  node.textContent = value == null ? "—" : String(value);
  return node.innerHTML;
}

function renderProvenance(item) {
  const sourceLink = item.source_url
    ? `<a class="source-link" href="${safe(item.source_url)}" target="_blank" rel="noopener">Open authoritative source ↗</a>`
    : "";
  content.innerHTML = `<section class="provenance">
    <span class="kicker">OBSERVATION PROVENANCE</span>
    <h2 id="provenance-title">${safe(item.ticker)} · ${safe(item.metric_name)}</h2>
    <p class="excerpt">${safe(item.bounded_excerpt)}</p>
    <dl class="provenance-grid">
      <dt>Reported value</dt><dd>${safe(item.reported_value)}</dd>
      <dt>Normalized value</dt><dd>${safe(item.value)} ${safe(item.scale)} ${safe(item.unit)}</dd>
      <dt>Period</dt><dd>Q${safe(item.fiscal_quarter)} ${safe(item.fiscal_year)} · ${safe(item.period_end)}</dd>
      <dt>State</dt><dd>${safe(item.state)}</dd>
      <dt>Entity</dt><dd>${safe(item.reporting_entity_id)}</dd>
      <dt>Scope</dt><dd>${safe(item.reporting_scope_id)}</dd>
      <dt>Population</dt><dd>${safe(item.portfolio_population)}</dd>
      <dt>Methodology</dt><dd>${safe(item.methodology)}</dd>
      <dt>Source class</dt><dd>${safe(item.source_class)}</dd>
      <dt>Accession</dt><dd>${safe(item.accession_or_identifier)}</dd>
      <dt>Retrieved</dt><dd>${safe(item.retrieved_at)}</dd>
      <dt>Knowledge from</dt><dd>${safe(item.knowledge_from)}</dd>
      <dt>Locator</dt><dd>${safe(item.evidence_locator)}</dd>
    </dl>${sourceLink}</section>`;
}

document.addEventListener("click", async (event) => {
  const trigger = event.target.closest(".provenance-trigger");
  if (!trigger || !dialog) return;
  content.innerHTML = "<p>Loading evidence…</p>";
  dialog.showModal();
  try {
    const response = await fetch(`/api/v1/observations/${encodeURIComponent(trigger.dataset.observationId)}`);
    if (!response.ok) throw new Error("Evidence could not be loaded.");
    renderProvenance(await response.json());
  } catch (error) {
    content.textContent = error.message;
  }
});
