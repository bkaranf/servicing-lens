# TFC Phase 3 disclosure map

Status: official-source assessment as of August 12, 2026. This map covers the
final 53-metric Phase 3 catalog for Q3 2025 through Q2 2026. It is a disclosure
map, not a ranking or investment recommendation.

## Checked eligible source set

Each quarter's complete eligible SEC-registrant source set is the filed Quarterly
Performance Summary plus the same-period Form 10-Q or Form 10-K. Full original
HTTP response bytes, source identities, timestamps, byte lengths, SHA-256 hashes,
and locators are retained under `config/recorded_evidence/phase3/tfc/` and listed
in `manifest.v1.yaml`. The deterministic source/recipe authority is
`config/phase3/tfc_sources.yaml`.

| Period | Filed exhibit | Periodic filing |
| --- | --- | --- |
| Q3 2025 | `tfc_2025q3_qps`, accession `0000092230-25-000150`, EX-99.2 | `tfc_2025q3_10q`, accession `0000092230-25-000157`, Note 7 |
| Q4 2025 | `tfc_2025q4_qps`, accession `0000092230-26-000023`, EX-99.2 | `tfc_2025q4_10k`, accession `0000092230-26-000030`, Note 8 |
| Q1 2026 | `tfc_2026q1_qps`, accession `0000092230-26-000039`, EX-99.2 | `tfc_2026q1_10q`, accession `0000092230-26-000062`, Note 6 |
| Q2 2026 | `tfc_2026q2_qps`, accession `0000092230-26-000096`, EX-99.2 | `tfc_2026q2_10q`, accession `0000092230-26-000099`, Note 6 |

Issuer IR is not an additional eligible source because its earnings materials are
filed as the retained exhibits. FR Y-9C and Call Report sources belong to the BHC
or bank regulatory reporting scopes; they do not establish disclosure or
missingness for this SEC-registrant residential-servicing scope.

Legend: `R` = reported disclosure found; `D` = governed deterministic derivation
from reported observations; `ND` = `CHECKED_COMPLETE` / `NOT_DISCLOSED` after the
full two-source set was checked. `ND` never means zero.

## Complete 53-metric matrix

| Metric | Q3 2025 | Q4 2025 | Q1 2026 | Q2 2026 | Exact disclosure or gap basis |
| --- | --- | --- | --- | --- | --- |
| `servicing_for_others_upb` | R | R | R | R | Current-period QPS `Loans serviced for others`; periodic filing corroborates as `UPB ... serviced for others` |
| `total_servicing_upb` | R | R | R | R | Current-period QPS `Total servicing portfolio`; exact periodic-filing reconciliation |
| `owned_msr_upb` | ND | ND | ND | ND | No explicit UPB underlying owned MSRs; serviced-for-others UPB is not silently substituted |
| `servicing_loan_count` | ND | ND | ND | ND | No servicing-population loan count |
| `subservicing_upb` | ND | ND | ND | ND | No explicitly defined subservicing UPB |
| `bank_owned_loans_serviced_upb` | R | R | R | R | Current-period QPS `Bank-owned loans serviced` |
| `interim_servicing_upb` | ND | ND | ND | ND | No interim-servicing population |
| `servicing_revenue` | R | R | R | R | Current-period QPS `Residential mortgage servicing income before MSR valuation` |
| `servicing_fee_income` | R | ND | R | ND | Q3 footnote and Q1 quarterly row; Q4 and Q2 disclose only annual/YTD values, so no quarter subtraction is authorized |
| `ancillary_servicing_income` | ND | ND | ND | ND | Ancillary fees described qualitatively but not separately quantified |
| `servicing_operating_expense` | ND | ND | ND | ND | No standalone residential-servicing expense line |
| `servicing_pretax_income` | ND | ND | ND | ND | No standalone residential-servicing segment pretax result |
| `servicing_adjusted_pretax_income` | ND | ND | ND | ND | No issuer-reported adjusted residential-servicing pretax result |
| `cost_to_service_per_loan` | ND | ND | ND | ND | Neither compatible servicing expense nor beginning/ending loan counts is disclosed |
| `weighted_average_servicing_fee_bps` | R | R | R | R | Current-period QPS weighted-average servicing fee row; exact percent-to-bps normalization |
| `msr_fair_value` | R | R | R | R | Periodic-filing residential MSR ending carrying value; issuer accounts for servicing rights at fair value |
| `msr_beginning_balance` | ND | ND | ND | ND | January 1 opening rows remain source evidence only; they are not published as quarter-end observations or support facts |
| `msr_additions` | ND | ND | R | ND | Q1 is a standalone quarter; YTD/annual rows remain source evidence and are never relabeled as quarters |
| `msr_purchases` | ND | ND | R | ND | Q1 is a standalone quarter; YTD/annual rows remain source evidence and are never relabeled as quarters |
| `msr_sales` | ND | ND | ND | ND | YTD/annual rows remain source evidence only; 2026 filings contain no exact standalone-quarter Sales row |
| `msr_realization_or_amortization` | ND | ND | ND | ND | Issuer combines realization, passage of time, and other; narrower metric not published |
| `msr_fair_value_market_change` | ND | ND | ND | ND | Market change is not separated from valuation-input/assumption change |
| `msr_fair_value_assumption_change` | ND | ND | ND | ND | Inputs and assumptions are one combined issuer row |
| `msr_ending_balance` | R | R | R | R | Residential MSR roll-forward ending row |
| `delinquency_30_plus_count_rate` | ND | ND | ND | ND | No servicing-portfolio count-rate disclosure or MBA/OTS method |
| `delinquency_60_plus_count_rate` | ND | ND | ND | ND | No servicing-portfolio count-rate disclosure or MBA/OTS method |
| `delinquency_90_plus_count_rate` | ND | ND | ND | ND | No servicing-portfolio count-rate disclosure or MBA/OTS method |
| `government_servicing_upb` | ND | ND | ND | ND | `Primarily agency conforming fixed rate` is qualitative, not a government UPB amount |
| `conventional_servicing_upb` | ND | ND | ND | ND | No exact conventional servicing split |
| `gnma_servicing_upb` | ND | ND | ND | ND | No exact GNMA servicing split |
| `fnma_servicing_upb` | ND | ND | ND | ND | No exact FNMA servicing split |
| `fhlmc_servicing_upb` | ND | ND | ND | ND | No exact FHLMC servicing split |
| `msr_hedging_result` | ND | ND | ND | ND | Derivatives row combines `MSRs and mortgage banking`; no MSR-only result |
| `msr_fair_value_multiple_of_related_upb` | D | D | D | D | Exact `msr_fair_value / servicing_for_others_upb`, same instant date, full input observation IDs required |
| `msr_fair_value_bps_of_related_upb` | D | D | D | D | Exact multiple × 10,000, same instant date, full input observation IDs required |
| `capitalized_servicing_rate_on_additions` | ND | ND | ND | ND | No capitalized servicing rate on additions |
| `delinquency_30_plus_upb_rate` | ND | ND | ND | ND | No servicing-portfolio UPB-weighted 30+ rate |
| `delinquency_60_plus_upb_rate` | ND | ND | ND | ND | No servicing-portfolio UPB-weighted 60+ rate |
| `delinquency_90_plus_upb_rate` | ND | ND | ND | ND | No servicing-portfolio UPB-weighted 90+ rate |
| `foreclosure_upb_rate` | ND | ND | ND | ND | Owned residential loans in foreclosure are a different population and not a servicing UPB rate |
| `reo_upb` | ND | ND | ND | ND | Foreclosed-real-estate balance is not disclosed for the servicing portfolio |
| `msr_fair_value_inputs_or_assumptions_change` | ND | ND | R | ND | Q1 exact combined row; YTD/annual rows remain source evidence only |
| `msr_realization_passage_time_and_other` | ND | ND | R | ND | Q1 exact combined row; YTD/annual rows remain source evidence only |
| `fha_servicing_upb` | ND | ND | ND | ND | No exact FHA servicing UPB row in the complete eligible source set |
| `va_servicing_upb` | ND | ND | ND | ND | No exact VA servicing UPB row in the complete eligible source set |
| `usda_servicing_upb` | ND | ND | ND | ND | No exact USDA servicing UPB row in the complete eligible source set |
| `closed_end_second_lien_servicing_upb` | ND | ND | ND | ND | No exact closed-end second-lien servicing UPB row |
| `other_servicing_upb` | ND | ND | ND | ND | No exact issuer-defined Other servicing component |
| `owned_msr_msl_upb` | ND | ND | ND | ND | No same-table owned MSR-and-MSL UPB population |
| `msr_additions_related_upb` | ND | ND | ND | ND | No exact UPB explicitly related to MSR additions |
| `delinquency_30_to_89_upb` | ND | ND | ND | ND | No servicing-population 30–89 days UPB bucket |
| `delinquency_90_plus_upb` | ND | ND | ND | ND | No servicing-population 90+ UPB bucket |
| `foreclosure_upb` | ND | ND | ND | ND | Owned residential loans in foreclosure are a different population |

## Exact MSR reconciliation and derived values

The reported roll-forwards reconcile with `Decimal` and zero tolerance. Q3 2025
and Q2 2026 are YTD, Q4 2025 is annual, and Q1 2026 is a standalone quarter; the
YTD rows are never relabeled as standalone quarters.

| Period | Exact reported reconciliation, USD millions | Result |
| --- | --- | --- |
| Q3 2025 | `3430 + 182 + 160 - 0 - 16 - 232` | `3524` |
| Q4 2025 | `3431 + 339 + 239 - 0 + 39 - 324` | `3724` |
| Q1 2026 | `3724 + 131 + 85 + 13 - 85` | `3868` |
| Q2 2026 | `3724 + 275 + 197 + 39 - 189` | `4046` |

| Period | MSR fair value / related UPB | Basis points |
| --- | --- | --- |
| Q3 2025 | `0.015926` | `159.26` |
| Q4 2025 | `0.016306` | `163.06` |
| Q1 2026 | `0.016539` | `165.39` |
| Q2 2026 | `0.016805` | `168.05` |

The derivations are publishable only after their exact reported inputs exist as
published observations and every output stores both input observation IDs and
formula version `1.0.0`.

## Explicit semantic exclusions

- Residential mortgage loan delinquency and foreclosure tables describe loans
  owned by Truist, not the servicing portfolio. They cannot populate servicing
  delinquency, foreclosure-rate, or REO metrics.
- The derivatives disclosure combines MSRs and mortgage banking. It cannot
  populate `msr_hedging_result`.
- The phrase `primarily agency conforming fixed rate` is qualitative and cannot
  populate an investor-mix amount or percentage.
- Regulatory BHC and bank values remain available for cross-source reconciliation
  only under their native scopes. A difference from SEC scope is not a conflict
  and is never silently blended.
