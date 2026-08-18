(function publishServicingLensState(root) {
  "use strict";

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

  const api = Object.freeze({ resolveEarningsCompany });
  root.ServicingLensState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window === "undefined" ? globalThis : window);
