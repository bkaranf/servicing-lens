(function publishServicingLensState(root) {
  "use strict";

  function toggleCompany(selected, companyId, maximum = 3) {
    const current = Array.from(new Set(selected)).slice(0, maximum);
    if (current.includes(companyId)) {
      const next = current.filter((item) => item !== companyId);
      return {
        accepted: true,
        selected: next,
        status: `${next.length} of ${maximum} companies selected for the comparison bench.`,
      };
    }
    if (current.length >= maximum) {
      return {
        accepted: false,
        selected: current,
        status: `Selection limit reached. Compare up to ${maximum} companies at a time.`,
      };
    }
    const next = [...current, companyId];
    return {
      accepted: true,
      selected: next,
      status: `${next.length} of ${maximum} companies selected for the comparison bench.`,
    };
  }

  function normalizedSearch(value) {
    return String(value).normalize("NFKC").trim().toLocaleLowerCase();
  }

  function resolveEarningsCompany(query, options, cards) {
    const normalizedQuery = normalizedSearch(query);
    if (!normalizedQuery) return cards[0]?.companyId || null;
    const exactOption = options.find(
      (option) => normalizedSearch(option.value) === normalizedQuery,
    );
    if (exactOption) return exactOption.companyId;
    return cards.find(
      (card) => normalizedSearch(card.searchText).includes(normalizedQuery),
    )?.companyId || null;
  }

  const api = Object.freeze({ resolveEarningsCompany, toggleCompany });
  root.ServicingLensState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window === "undefined" ? globalThis : window);
