# Metric catalog

## Contract

The catalog defines canonical meanings; it does not assert that TFC or PFSI
discloses every metric. An unsupported cell is \`NOT_DISCLOSED\`, never zero,
estimated, or populated from a “closest” metric.

Each definition below is semantic version \`1.0.0\`, effective 2025-07-01 until
superseded. A change to meaning, scope, formula, timing, or publication evidence
creates a new immutable version. Display-only wording can change without altering
semantics only when the decision record explains why.

Every machine-readable definition contains:

- stable metric ID, semantic version, effective interval, display name, and
  business meaning;
- grain, instant/duration semantics, permitted period types, reporting entities,
  reporting scopes, accounting bases, currency, unit, canonical scale, and
  reported precision behavior;
- numerator, denominator, formula, methodology variants, inclusions, exclusions,
  null behavior, and sign normalization;
- source-specific raw labels and aliases, each with issuer, source, scope,
  effective dates, and evidence;
- eligible extraction methods and required evidence;
- validation, reconciliation, comparability, and derivation rules; and
- prohibited interpretations.

Aliases help qualify a candidate; text similarity alone never publishes a value.
The exact TFC and PFSI labels belong in versioned \`metric_aliases\` records after
source discovery and retain the issuer wording in observation evidence.

## Shared numeric and evidence rules

- Monetary values and UPB use \`Decimal\`, ISO 4217 currency, and database
  \`NUMERIC\`. The normalized unit is currency units with canonical scale 1; the
  original reported scale and decimals remain evidence.
- Counts are exact nonnegative integers unless the source explicitly reports a
  rounded count, in which case reported precision is retained.
- Rates use an exact decimal ratio plus a display unit of percent or basis points.
- Instant metrics apply at period end. Duration metrics carry exact start/end
  dates and are not treated as standalone quarters when the source is YTD.
- Reported negatives and parenthetical values preserve raw text. Metric-specific
  normalization records the transformation rather than changing evidence.
- A measured zero requires source evidence. Missing, unknown, not applicable, and
  not disclosed carry no numeric value.
- Eligible evidence is an authoritative structured fact, deterministic filed
  table/document extraction, or controlled manual extraction. An ambiguous
  candidate remains quarantined.
- Cross-company comparison follows the pairwise comparability policy. Matching
  canonical IDs alone is insufficient.

## Phase 3 extension

`config/metrics/phase3_deepening.v1.yaml` composes with, and does not mutate,
the base catalog. It versions the richer delinquency methodology dimensions,
annualized cost and fee calculations, MSR fair-value economics, investor mix,
and TFC SEC-versus-regulatory reconciliation rules. The machine-readable
extension is authoritative for its exact definitions and formula versions.

Every Phase 3 derivation requires exact `PUBLISHED` and `VALIDATED` observation
revisions for all numerators, denominators, components, and averaging anchors.
The ordered input observation IDs, input values, roles, and formula version are
retained in `derived_observation_inputs`. A disclosed component that has not yet
been modeled as a canonical published input blocks the derivation; it is neither
silently used as parser-local arithmetic nor mislabeled `NOT_DISCLOSED`.

Delinquency observations carry explicit count-versus-UPB basis, threshold,
denominator, foreclosure, bankruptcy, and forbearance semantics. Portfolio-mix
observations carry the parent population, category, basis, and overlap policy.
PFSI's MSR-only related UPB is therefore not interchangeable with its broader
owned-MSR-and-MSL mix and delinquency population. TFC's SEC registrant scope,
FR Y-9C holding-company scope, and Call Report depository scope also remain
distinct even when a reconciliation rule evaluates two values.

## Portfolio metrics

| Metric ID | Business meaning and grain | Scope, unit, period | Numerator/denominator or formula | Inclusion and exclusion | Labels and required evidence | Reconciliation and comparability | Prohibited interpretation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| \`servicing_for_others_upb\` | UPB explicitly serviced for parties other than the reporting entity; one entity/scope at one period end | \`SERVICING_FOR_OTHERS\`; currency; instant | Reported UPB; no denominator or derivation | Include only source-defined servicing for others; exclude owned loans, interim servicing, and any unconfirmed overlap | Candidate labels include “loans serviced for others”; require table/XBRL/regulatory context, entity, date, currency, scale, and population | Reconcile to a disclosed total only when components are explicitly bridged; prefer for cross-company comparison when definitions align | Total servicing, owned-MSR UPB, or regulatory subsidiary value is not a substitute |
| \`total_servicing_upb\` | Issuer-defined total servicing portfolio UPB; one entity/scope at one period end | \`TOTAL_SERVICING_PORTFOLIO\`; currency; instant | Reported total; no denominator | Include only components the issuer calls total; preserve its definition; do not assume components are additive | Candidate labels include “total servicing portfolio” and “UPB serviced”; require explicit total label and scope note | Compare only when portfolio populations are demonstrably aligned; reconcile to components only with issuer bridge | Never silently use as servicing for others or owned-MSR UPB |
| \`owned_msr_upb\` | UPB associated with MSRs owned or capitalized by the reporting entity; one owned-MSR scope at period end | \`OWNED_MSR_PORTFOLIO\`; currency; instant | Reported UPB; no denominator | Include owned/capitalized MSR population; exclude subservicing without owned rights and interim servicing | Candidate labels include “UPB of loans underlying MSRs”; require explicit ownership basis | Can compare when ownership population and measurement date align; do not derive from MSR fair value | MSR carrying value or total servicing UPB cannot imply owned-MSR UPB |
| \`servicing_loan_count\` | Number of loans in an explicitly identified servicing population; one scope at period end | Any permitted portfolio scope; loans; instant | Reported count; no denominator | Include only loans matching the observation's scope; retain rounded precision | Candidate labels include “loan count” and “number of loans serviced”; require adjacent population/scope | Reconcile only to an explicitly stated total; count-based trends require consistent scope | Never estimate from UPB or an assumed average balance |
| \`subservicing_upb\` | UPB serviced on behalf of another servicing-rights owner; one subservicing scope at period end | \`SUBSERVICING_PORTFOLIO\`; currency; instant | Reported UPB; no denominator | Include source-defined subservicing; exclude owned rights unless source states overlap | Candidate labels include “subservicing UPB”; require ownership and population context | Treat overlap with total servicing as unknown unless issuer reconciles it | Never add to total servicing without an explicit bridge |
| \`bank_owned_loans_serviced_upb\` | UPB of loans owned by the bank/reporting entity that it services; one bank-owned population at period end | \`BANK_OWNED_LOANS_SERVICED\`; currency; instant | Reported UPB; no denominator | Include only source-defined owned loans serviced; exclude servicing for others | Candidate labels include “bank-owned loans serviced”; require legal entity and regulatory/SEC scope | Compare only within compatible legal-entity and ownership boundaries | A holding-company or depository value cannot be relabeled as consolidated SEC scope |
| \`interim_servicing_upb\` | UPB under temporary/interim servicing before sale or transfer; one interim population at period end | \`INTERIM_SERVICING_PORTFOLIO\`; currency; instant | Reported UPB; no denominator | Include only explicitly temporary/interim servicing; exclude long-term owned/subserviced populations | Candidate labels include “interim servicing”; require transfer/sale context | Compare only under the same interim methodology; preserve overlap disclosures | Never infer from production volume or expected sales |

## Servicing economics

| Metric ID | Business meaning and grain | Scope, unit, period | Numerator/denominator or formula | Inclusion and exclusion | Labels and required evidence | Reconciliation and comparability | Prohibited interpretation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| \`servicing_revenue\` | Revenue explicitly attributed to servicing for one reporting entity/scope and duration | Servicing or explicitly qualified combined segment; currency; duration | Reported revenue | Include issuer-defined servicing revenue; preserve gross/net method; exclude origination revenue unless inseparable and scope is labeled combined | Candidate labels include “servicing revenue” or “loan servicing fees”; require period, segment, basis, and line-item evidence | Reconcile to disclosed segment revenue when available; compare only matching gross/net populations | Do not equate automatically with servicing fee income |
| \`servicing_fee_income\` | Contractual servicing fee income for one compatible servicing population and duration | Servicing scope; currency; duration | Reported fee income | Include base servicing fees under source definition; exclude ancillary income and valuation effects unless explicitly included and qualified | Candidate labels include “servicing fees”; require line-item definition and period | Compare only matching inclusion/netting policy; may reconcile as a component of revenue | Combined revenue is not a substitute |
| \`ancillary_servicing_income\` | Income from ancillary activities explicitly attributed to servicing for one duration | Servicing scope; currency; duration | Reported ancillary amount | Include only disclosed ancillary categories; exclude base fees and MSR valuation | Candidate labels include “ancillary income”; require category and scope evidence | Components may sum only under an issuer reconciliation | Do not infer as revenue minus servicing fees |
| \`servicing_operating_expense\` | Operating expense explicitly attributed to servicing for one duration, normalized as a positive expense magnitude | Servicing or qualified combined segment; currency; duration | Absolute normalized expense with raw sign retained | Include source-defined servicing operating costs; exclude MSR valuation, hedge result, and origination expense unless source combines them | Candidate labels include “servicing expense”; require line item, sign, scope, and exclusions | Reconcile to segment expense or pretax bridge when disclosed; compare only compatible allocations | Do not derive by forcing revenue minus pretax income when components differ |
| \`servicing_pretax_income\` | Pretax income/loss explicitly reported for servicing for one duration | Servicing segment preferred; currency; duration | Reported signed pretax amount | Include only servicing-specific result; retain combined mortgage-banking result under combined scope | Candidate labels include “servicing pretax income”; require segment/basis and period | Reconcile revenue/expense only when issuer bridge covers all components; compare combined scopes as not comparable to servicing-only | Do not treat MSR marks or hedges as operations when the source separates them |
| \`servicing_adjusted_pretax_income\` | Issuer-reported adjusted/non-GAAP pretax servicing result for one duration | Servicing scope; currency; duration; \`NON_GAAP_REPORTED\` | Reported signed amount; no platform-created adjustment | Include only issuer-defined adjustments with reconciliation; exclude analyst-selected adjustments | Candidate labels include “adjusted pretax income”; require reconciliation and definition | Compare only when adjustment sets align or with explicit caveats | Never relabel a platform calculation as issuer-reported adjusted income |
| \`cost_to_service_per_loan\` | Annualized deterministic servicing operating expense per average compatible loan for one duration | Same scope for expense and counts; currency/loan/year; duration; \`DERIVED\` | \`servicing_operating_expense / average(beginning_count, ending_count) * 365 / actual_period_days\` | Require both compatible count instants, exact expense duration, and explicit actual-day count; exclude zero/missing denominator | Evidence is linked published input observations plus formula version, averaging dates, observed days, and Decimal annual basis | Reconcile the unrounded calculation to every input; compare only the same formula, scope, allocation, and annualization method | No ending-count shortcut, partial-period input, estimation, or implicit day-count convention |
| \`weighted_average_servicing_fee_bps\` | Weighted average servicing fee rate for an explicitly defined portfolio and duration | Compatible owned/servicing population; basis points; instant if reported rate, duration if derived | Reported rate preferred; derived only as versioned annualized eligible fee income divided by exact average eligible UPB | Include only fee income and UPB for the same population; day-count/annualization method is explicit | Require reported rate evidence or all published inputs and formula parameters | Derived rate reconciles to inputs; compare only matching population and methodology | Do not divide generic servicing revenue by period-end total UPB |

## MSR metrics

The roll-forward sign convention records additions and purchases as positive
increases, sales and realization/amortization as positive reduction magnitudes,
and market/assumption changes as signed effects. Raw issuer signs remain evidence.

| Metric ID | Business meaning and grain | Scope, unit, period | Numerator/denominator or formula | Inclusion and exclusion | Labels and required evidence | Reconciliation and comparability | Prohibited interpretation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| \`msr_fair_value\` | Fair value of the specified MSR asset population at period end | Owned-MSR entity/scope; currency; instant | Reported fair value | Include only assets measured/presented at fair value for the stated class; exclude UPB and hedge instruments | Candidate labels include “fair value of MSRs”; require balance/class/method/date | Reconcile to ending balance when the same roll-forward is fair-value based; compare only compatible classes/methods | Fair value is not UPB, cash value, or forecast |
| \`msr_beginning_balance\` | Opening carrying/fair-value balance in an MSR roll-forward | Exact roll-forward scope; currency; instant at the actual opening date | Reported opening balance | Preserve measurement method and asset class | Require table identity, opening date, label, method, and scale | Must equal prior compatible ending balance or carry an explained difference | Do not relabel an opening instant as the current quarter end or bridge methods/classes silently |
| \`msr_additions\` | MSRs originated/capitalized or otherwise added under issuer-defined additions during a roll-forward | Exact roll-forward scope; currency; duration | Reported positive increase | Include issuer-classified additions; keep purchases separate | Require row label and table headers | Participates in signed roll-forward | Production volume is not an addition amount |
| \`msr_purchases\` | MSRs acquired through purchase during a roll-forward | Exact roll-forward scope; currency; duration | Reported positive increase | Include purchases only; exclude originated additions | Require purchase row and acquisition context | Participates in signed roll-forward and corporate-action review | Do not infer from portfolio growth |
| \`msr_sales\` | Carrying/fair-value amount removed through MSR sales during a roll-forward | Exact roll-forward scope; currency; duration | Positive reduction magnitude with raw sign retained | Include issuer-classified sales; exclude realization/amortization | Require sale row and table context | Subtracted in roll-forward; material sale creates continuity assessment | Sale proceeds and carrying-value removal are not interchangeable |
| \`msr_realization_or_amortization\` | Reduction from realization of expected cash flows under fair value or amortization under amortized cost, preserving methodology | Exact roll-forward scope; currency; duration | Positive reduction magnitude plus methodology variant | Include only source-labeled realization/amortization; do not merge impairment or market change | Require row, accounting method, and period | Subtracted in roll-forward; compare only same methodology or not comparable | Fair-value realization and amortization are not economic synonyms |
| \`msr_fair_value_market_change\` | Signed fair-value change attributed to market inputs or market movement | Fair-value MSR scope; currency; duration | Reported signed effect | Include only issuer-attributed market component; exclude realization and assumption-specific change when separated | Require exact row/category and method | Participates in roll-forward; compare only aligned taxonomy | Do not treat as servicing operating income |
| \`msr_fair_value_assumption_change\` | Signed fair-value change attributed to valuation-model assumptions | Fair-value MSR scope; currency; duration | Reported signed effect | Include only separately disclosed assumption component | Require exact row/category and method | Participates in roll-forward; compare only compatible assumption category | Do not infer as total fair-value change less market change unless formula is approved and complete |
| \`msr_ending_balance\` | Closing carrying/fair-value MSR balance in a roll-forward | Exact roll-forward scope; currency; instant at duration end | Reported closing balance | Preserve method/class and any other disclosed components | Require table identity, closing date, label, method, and scale | Reconcile beginning plus/minus every disclosed component; unexplained residual quarantines or qualifies publication | Do not equate automatically with balance-sheet total or \`msr_fair_value\` across methods |

## Portfolio quality and composition

| Metric ID | Business meaning and grain | Scope, unit, period | Numerator/denominator or formula | Inclusion and exclusion | Labels and required evidence | Reconciliation and comparability | Prohibited interpretation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| \`delinquency_30_plus_count_rate\` | Share of loans 30 or more days delinquent by count at period end | Explicit servicing population; percent; instant | Reported rate, or exact disclosed 30+ count divided by compatible total count when derivation is permitted | Preserve delinquency convention, exclusions, forbearance treatment, and denominator | Require aging definition, numerator/denominator or reported rate, scope, and date | Must reconcile to disclosed buckets when available; compare only same convention/population | UPB rate is not a count rate; missing buckets are not zero |
| \`delinquency_60_plus_count_rate\` | Share of loans 60 or more days delinquent by count at period end | Explicit servicing population; percent; instant | Same rules using 60+ count | Same, with exact 60+ threshold | Require threshold and population evidence | Compare only aligned aging methodology | Never derive from 30+ and 90+ by interpolation |
| \`delinquency_90_plus_count_rate\` | Share of loans 90 or more days delinquent by count at period end | Explicit servicing population; percent; instant | Same rules using 90+ count | Same, preserving foreclosure/bankruptcy inclusion policy | Require threshold, inclusions, and population evidence | Compare only aligned aging and inclusion methodology | A regulatory nonaccrual measure is not a substitute |
| \`government_servicing_upb\` | UPB explicitly classified as government servicing at period end | Compatible servicing portfolio; currency; instant | Reported UPB | Include source-defined government population; retain agency/program definition | Require category label, parent population, date, and scale | Components reconcile only under issuer taxonomy | Do not assume it equals GNMA UPB |
| \`conventional_servicing_upb\` | UPB explicitly classified as conventional servicing at period end | Compatible servicing portfolio; currency; instant | Reported UPB | Include source-defined conventional population | Require category and parent-scope evidence | Compare only matching classification; reconcile to total only with complete partition | Government plus conventional is not automatically exhaustive |
| \`gnma_servicing_upb\` | UPB explicitly associated with Ginnie Mae servicing at period end | Compatible servicing portfolio; currency; instant | Reported UPB | Include only source-defined GNMA population | Require agency label, parent scope, date, and scale | Compare only compatible issuer/portfolio boundary | Ginnie Mae issuer MBS outstanding is not silently total GNMA servicing UPB |
| \`fnma_servicing_upb\` | UPB explicitly associated with Fannie Mae servicing at period end | Compatible servicing portfolio; currency; instant | Reported UPB | Include only source-defined FNMA population | Require agency label and scope evidence | Compare only compatible population | Do not infer from conventional residual |
| \`fhlmc_servicing_upb\` | UPB explicitly associated with Freddie Mac servicing at period end | Compatible servicing portfolio; currency; instant | Reported UPB | Include only source-defined FHLMC population | Require agency label and scope evidence | Compare only compatible population | Do not merge FNMA and FHLMC without an explicit reported aggregate |

## Stage A publication profile

The configuration identifies which of these definitions has a verified extraction
path for TFC and PFSI in Q3 2025 through Q2 2026. Stage A must display at least
five useful metrics across the selected coverage, but the catalog does not promise
that every metric appears for both issuers.

Default cross-company portfolio comparisons prefer
\`servicing_for_others_upb\` and \`owned_msr_upb\` when both issuers disclose
compatible populations. \`total_servicing_upb\` remains a separate metric even
when it is the only portfolio measure an issuer reports.
