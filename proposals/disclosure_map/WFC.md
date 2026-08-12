# WFC Phase 4a disclosure map

Status: research-only, disclosure-map-first assessment. This document does not
authorize parsing or publication. It covers the 53 Phase 3 metric IDs for fiscal
Q3 2025 through Q2 2026 and preserves native reporting boundaries.

## Verified issuer identity

| Field | Verified value | Official evidence |
|---|---|---|
| Legal registrant | Wells Fargo & Company | 2025 Form 10-K cover |
| SEC conformed name | WELLS FARGO & COMPANY/MN | SEC submissions JSON |
| Ticker / exchange | WFC / NYSE | SEC submissions JSON and 2025 Form 10-K cover |
| CIK | 0000072971 | SEC submissions JSON and filing covers |
| Fiscal year end | December 31; calendar-year quarters | SEC submissions JSON and periodic filings |
| BHC reporter | Wells Fargo & Company, RSSD 1120754 | FFIEC National Information Center |
| Depository reporter | Wells Fargo Bank, National Association, RSSD 451965 | FFIEC CDR/UBPR institution header |

The SEC registrant is Wells Fargo & Company and its consolidated subsidiaries.
It is not interchangeable with the parent-only company, the native FR Y-9C BHC
reporter, Wells Fargo Bank, N.A., Home Lending, or a mortgage-servicing
population. Regulatory facts must retain their own RSSD and consolidation scope.
Home Lending is modeled as a `DISCLOSED_OPERATING_UNIT`, with an explicit scope
for consolidated owned Home Lending loans; it is not attached directly to the SEC
registrant and is not a canonical servicing population.

## Selected-quarter source set

| Quarter | Earnings filing | Periodic filing | Retained documents |
|---|---|---|---|
| Q3 2025 | 8-K 0000072971-25-000239, accepted 2025-10-14 | 10-Q 0000072971-25-000253, accepted 2025-10-31 | EX-99.1 release, EX-99.2 supplement, EX-99.3 presentation, 10-Q |
| Q4 2025 | 8-K 0000072971-26-000009, accepted 2026-01-14 | 10-K 0000072971-26-000133, accepted 2026-02-24 | EX-99.1 release, EX-99.2 supplement, EX-99.3 presentation, 10-K cover, incorporated EX-13 financial report |
| Q1 2026 | 8-K 0000072971-26-000213, accepted 2026-04-14 | 10-Q 0000072971-26-000217, accepted 2026-04-29 | EX-99.1 release, EX-99.2 supplement, EX-99.3 presentation, 10-Q |
| Q2 2026 | 8-K 0000072971-26-000288, accepted 2026-07-14 | 10-Q 0000072971-26-000302, accepted 2026-07-28 | EX-99.1 release, EX-99.2 supplement, EX-99.3 presentation, 10-Q |

All 17 filing documents plus the SEC submissions response are retained as exact
original HTTP response bytes. The complete official URLs, accessions, acceptance
timestamps, byte lengths, SHA-256 values, and content-addressed locations are in
[`manifest.v1.yaml`](../../config/recorded_evidence/phase4/wfc/manifest.v1.yaml).

The native FR Y-9C (RSSD 1120754) and Call Report (RSSD 451965) quarter-end
bytes have not yet been retained. B492 (YTD net servicing fees across mortgages,
credit cards, and other financial assets), B804/B805 (1-4 family residential
servicing for others), F699 (servicing-for-others loans in foreclosure), 3164
(MSA carrying amount), and 6438/A590 (MSA fair value) remain separate regulatory
research and reconciliation items. B492 cannot satisfy the current contractual
servicing/subservicing fee-income definition, and F699/B804/B805 cannot satisfy
the current PFSI `owned_msr_and_msl` foreclosure definitions.

## Status legend

- `R`: an exact issuer-reported fact exists in the checked SEC source set at the
  stated source scope. Preliminary and final facts remain separate revisions.
- `D`: exact reported inputs exist, but this research package does not authorize
  derivation or publication. A governed WFC scope bridge is still required.
- `ND`: the complete applicable SEC source set was checked and no exact
  semantically compatible fact was disclosed. Combined, partial, wrong-period,
  and wrong-scope facts are not substitutes.

## Complete 53-metric matrix

| Metric ID | Q3 2025 | Q4 2025 | Q1 2026 | Q2 2026 | Boundary / result |
|---|---:|---:|---:|---:|---|
| `servicing_for_others_upb` | R | R | R | R | Final Note 6 residential servicing-for-others; excludes residential subservicing. Regulatory reconciliation pending. |
| `total_servicing_upb` | R | R | ND | ND | 2025 total managed residential servicing; changed 2026 format does not disclose the managed total. |
| `owned_msr_upb` | ND | ND | ND | ND | No UPB explicitly limited to loans underlying owned residential MSRs. |
| `servicing_loan_count` | ND | ND | ND | ND | No same-population servicing loan count. |
| `subservicing_upb` | ND | ND | ND | ND | Third-party-serviced explicitly excludes subservicing; no exact isolated amount. |
| `bank_owned_loans_serviced_upb` | R | R | ND | ND | Exact 2025 residential owned-loans-serviced row; absent in changed 2026 Note 6. |
| `interim_servicing_upb` | ND | ND | ND | ND | No interim-servicing population. |
| `servicing_revenue` | R | ND | R | R | Final Note 6 Total net servicing income includes valuation/hedge effects. Preliminary Q4 Net servicing income is not proven equivalent. |
| `servicing_fee_income` | ND | ND | ND | ND | SEC fees combine contractual, late, and ancillary amounts; regulatory B492 is YTD net servicing fees across all financial assets and is incompatible. |
| `ancillary_servicing_income` | ND | ND | ND | ND | Late and ancillary charges are not isolated. |
| `servicing_operating_expense` | ND | ND | ND | ND | Unreimbursed servicing costs is partial, not complete operating expense. |
| `servicing_pretax_income` | ND | ND | ND | ND | No servicing-only pretax result. |
| `servicing_adjusted_pretax_income` | ND | ND | ND | ND | No adjusted servicing-only pretax result and reconciliation. |
| `cost_to_service_per_loan` | ND | ND | ND | ND | Compatible expense and servicing-count inputs are absent. |
| `weighted_average_servicing_fee_bps` | ND | ND | ND | ND | Weighted average loan rate is not a servicing-fee rate. |
| `msr_fair_value` | R | R | R | R | Exact residential MSR fair-value ending balance; regulatory reconciliation pending. |
| `msr_beginning_balance` | ND | ND | ND | ND | Opening instant is not relabeled as selected quarter-end. |
| `msr_additions` | ND | ND | ND | ND | Originations/purchases is combined. |
| `msr_purchases` | ND | ND | ND | ND | Originations/purchases is combined. |
| `msr_sales` | ND | ND | ND | ND | Sales and other is combined. |
| `msr_realization_or_amortization` | R | ND | R | R | Exact current-quarter collection/realization except Q4, where only annual is disclosed. |
| `msr_fair_value_market_change` | R | ND | R | R | Exact current-quarter market-interest-rate change except Q4 annual-only. |
| `msr_fair_value_assumption_change` | R | ND | R | R | Exact current-quarter other-input/assumption change except Q4 annual-only. |
| `msr_ending_balance` | R | R | R | R | Exact residential MSR ending balance; regulatory reconciliation pending. |
| `delinquency_30_plus_count_rate` | ND | ND | ND | ND | Home Lending 30+ is owned-loan UPB rate, not servicing-population count rate. |
| `delinquency_60_plus_count_rate` | ND | ND | ND | ND | No same-population servicing disclosure. |
| `delinquency_90_plus_count_rate` | ND | ND | ND | ND | No same-population servicing disclosure. |
| `government_servicing_upb` | ND | ND | ND | ND | No exact same-population investor/program component. |
| `conventional_servicing_upb` | ND | ND | ND | ND | No exact same-population investor/program component. |
| `gnma_servicing_upb` | ND | ND | ND | ND | No exact same-population investor/program component. |
| `fnma_servicing_upb` | ND | ND | ND | ND | No exact same-population investor/program component. |
| `fhlmc_servicing_upb` | ND | ND | ND | ND | No exact same-population investor/program component. |
| `delinquency_30_plus_upb_rate` | ND | ND | ND | ND | Home Lending 30+ excludes government-insured/guaranteed, HFS, and nonaccrual loans and is not servicing-scope. |
| `delinquency_60_plus_upb_rate` | ND | ND | ND | ND | No same-population servicing disclosure. |
| `delinquency_90_plus_upb_rate` | ND | ND | ND | ND | No same-population servicing disclosure. |
| `foreclosure_upb_rate` | ND | ND | ND | ND | Current definition requires PFSI owned-MSR-and-MSL population; WFC Home Lending and regulatory servicing-for-others facts are incompatible. |
| `reo_upb` | ND | ND | ND | ND | Consolidated foreclosed assets are not servicing-scope REO UPB. |
| `msr_hedging_result` | R | ND | R | R | Exact current-quarter economic-hedge result except Q4 annual-only. |
| `msr_fair_value_inputs_or_assumptions_change` | ND | ND | ND | ND | WFC reports separate market and other-assumption components; they are not relabeled as a combined-only metric. |
| `msr_realization_passage_time_and_other` | ND | ND | ND | ND | Collection/realization is narrower than this combined methodology. |
| `msr_fair_value_multiple_of_related_upb` | D | D | D | D | Exact SEC inputs; metric-engine v1.1.0 uses the governed same-date WFC residential-MSR-related UPB support boundary. |
| `msr_fair_value_bps_of_related_upb` | D | D | D | D | Exact SEC inputs; metric-engine v1.1.0 uses the governed same-date WFC residential-MSR-related UPB support boundary. |
| `capitalized_servicing_rate_on_additions` | ND | ND | ND | ND | Additions and related UPB are not separately disclosed. |
| `fha_servicing_upb` | ND | ND | ND | ND | No exact same-population program component. |
| `va_servicing_upb` | ND | ND | ND | ND | No exact same-population program component. |
| `usda_servicing_upb` | ND | ND | ND | ND | No exact same-population program component. |
| `closed_end_second_lien_servicing_upb` | ND | ND | ND | ND | No exact same-population servicing component. |
| `other_servicing_upb` | ND | ND | ND | ND | No exact same-population servicing component. |
| `owned_msr_msl_upb` | ND | ND | ND | ND | WFC does not report PFSI's owned-MSR-and-MSL population. |
| `msr_additions_related_upb` | ND | ND | ND | ND | No exact UPB related to combined originations/purchases. |
| `delinquency_30_to_89_upb` | ND | ND | ND | ND | No same-population servicing disclosure. |
| `delinquency_90_plus_upb` | ND | ND | ND | ND | No same-population servicing disclosure. |
| `foreclosure_upb` | ND | ND | ND | ND | Current definition requires PFSI owned-MSR-and-MSL population; F699 servicing-for-others is ineligible. |

Current research counts are **31 R, 8 D, 173 ND, and 0
SOURCE_NOT_CHECKED**, totaling 212 cells. Regulatory items remain research or
reconciliation inputs without changing the current grid definition boundaries.

## Material source and comparability boundaries

- The 2025 periodic format reports residential total managed servicing and owned
  loans serviced. The 2026 format reports loans serviced for others and does not
  permit reconstruction of the earlier managed total.
- Earnings-supplement third-party mortgage loans serviced excludes residential
  subservicing. The more precise preliminary supplement amount and rounded final
  periodic-filing amount remain separate revisions. For Q2 2026, 361.4 and 362
  are representation/rounding variants, not silent overwrites.
- The Q1 2026 supplement changed format. It retains the current third-party
  serviced amount and Home Lending 30+ rate but drops current net servicing
  income and MSR carrying value. Historical comparison columns are never carried
  forward as current-period facts.
- Home Lending 30+ delinquency excludes government-insured or guaranteed loans,
  loans held for sale, and nonaccrual loans. It measures owned Home Lending loans,
  not the servicing portfolio.
- Contractually specified servicing fees are combined with late and ancillary
  charges. Total net servicing income additionally incorporates servicing costs,
  MSR realization and valuation effects, and economic hedges.
- Q4 periodic MSR roll-forward flows are annual. No annual-minus-YTD subtraction
  is authorized, and annual values are not labeled as standalone Q4 values.
- Combined roll-forward labels (`Originations/purchases` and `Sales and other`)
  do not authorize fabricated component observations.
- FR Y-9C, Call Report, SEC registrant, Home Lending, and servicing-portfolio
  scopes remain distinct even when they describe related economics.

## Remaining evidence work

Acquire and retain the official quarter-end FR Y-9C and FFIEC Call Report bytes
for both RSSDs, verify B804/B805/F699/B492/3164/6438/A590 with exact locators,
then reconcile related SEC facts without crossing reporting scopes. This future
research does not reopen the current grid classifications: B492 is incompatible
with contractual servicing/subservicing fee income, and F699/B804/B805 must not
populate the current PFSI-scoped foreclosure grid metrics.
