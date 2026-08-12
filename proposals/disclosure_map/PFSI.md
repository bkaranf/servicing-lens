# PFSI Phase 3 disclosure map

Status: complete official-source review for Q3 2025 through Q2 2026, as of
August 12, 2026. The machine-readable authority is
`config/phase3/pfsi_sources.yaml`; this document summarizes it.

## Entity, scope, and checked source set

- SEC registrant: PennyMac Financial Services, Inc. (`PFSI`, CIK `0001745916`).
- Reporting scopes remain distinct: Servicing segment, MSR-only related-loan
  population, owned MSR-and-MSL portfolio, subservicing portfolio, and
  issuer-defined total portfolio.
- Each quarter's complete eligible set is its filed earnings release (EX-99.1),
  filed presentation (EX-99.2), and periodic 10-Q/10-K. Filed exhibits take
  precedence over any issuer-IR copy.
- The 12 exact SEC HTTP response bodies are retained unchanged under
  `config/recorded_evidence/phase3/pfsi/`, labeled
  `ORIGINAL_HTTP_RESPONSE` / `sec_http_get`.
- A cell is `NOT_DISCLOSED` only after all three quarter sources were checked.
  Annual 10-K roll-forward values do not masquerade as standalone Q4 values.

Official periodic filings:

- [Q3 2025 Form 10-Q](https://www.sec.gov/Archives/edgar/data/1745916/000110465925103162/pfsi-20250930x10q.htm), accession `0001104659-25-103162`.
- [Q4 2025 Form 10-K](https://www.sec.gov/Archives/edgar/data/1745916/000110465926018142/pfsi-20251231x10k.htm), accession `0001104659-26-018142`.
- [Q1 2026 Form 10-Q](https://www.sec.gov/Archives/edgar/data/1745916/000110465926055690/pfsi-20260331x10q.htm), accession `0001104659-26-055690`.
- [Q2 2026 Form 10-Q](https://www.sec.gov/Archives/edgar/data/1745916/000110465926090486/pfsi-20260630x10q.htm), accession `0001104659-26-090486`.

The Q2 2026 filed [earnings release](https://www.sec.gov/Archives/edgar/data/1745916/000110465926088174/tm2621541d1_ex99-1.htm)
contains a five-quarter servicing table. Its exact original bytes are 741,162
bytes, SHA-256
`55bbe562574a015979ce480aa794d0a8ff7b09e05972d7adfd49b351dd932bd2`.

## Quarter-cell map

Legend: `R` = reported measured value; `D` = deterministic derived value with
canonical published input observations and observation-ID lineage; `ND` = `NOT_DISCLOSED`
after the complete eligible source set was checked. A reported dash is a
measured zero only where the row and scope are explicit.

| Metric | Q3 2025 | Q4 2025 | Q1 2026 | Q2 2026 | Principal locator/methodology |
|---|---:|---:|---:|---:|---|
| `servicing_for_others_upb` | ND | ND | ND | ND | PFSI reports owned and subserviced populations, not this canonical population |
| `total_servicing_upb` | R | R | R | R | EX-99.1, “Total UPB”; owned + subserviced + held for sale |
| `owned_msr_upb` | R | R | R | R | Periodic MSR fair-value table, exact “Unpaid principal balance of underlying loans” |
| `servicing_loan_count` | R | R | R | R | EX-99.1, “Total loans serviced (in thousands)” |
| `subservicing_upb` | R | R | R | R | EX-99.1, “Subserviced UPB” |
| `bank_owned_loans_serviced_upb` | ND | ND | ND | ND | Nonbank issuer; no bank-owned scope disclosed |
| `interim_servicing_upb` | ND | R | R | R | Periodic-filing servicing-portfolio table; Q1/Q2 dash is reported zero |
| `servicing_revenue` | R | R | R | R | EX-99.1 Servicing segment “Net revenues” |
| `servicing_fee_income` | R | R | R | R | EX-99.1 “Total loan servicing fees” |
| `ancillary_servicing_income` | R | R | R | R | EX-99.1 “Ancillary and other fees”; issuer-combined category retained |
| `servicing_operating_expense` | R | R | R | R | Filed presentation, “Operating expenses” in servicing profitability excluding valuation-related changes |
| `servicing_pretax_income` | R | R | R | R | EX-99.1 GAAP Servicing segment |
| `servicing_adjusted_pretax_income` | R | R | R | R | EX-99.1 issuer-reconciled non-GAAP measure |
| `cost_to_service_per_loan` | D | D | D | D | Quarterly expense / average adjacent exact loan counts, annualized by 365 / actual period days; Q3 uses the published June 2025 beginning-count support observation |
| `weighted_average_servicing_fee_bps` | R | R | R | R | EX-99.1 owned-portfolio key metrics |
| `msr_fair_value` | R | R | R | R | Periodic Note 11/10 MSR fair-value table, exact thousands |
| `msr_beginning_balance` | ND | ND | ND | ND | Opening rows remain source evidence only; they are not published as quarter-end observations or support facts |
| `msr_additions` | R | ND | R | R | “MSRs resulting from loan sales” quarterly roll-forward |
| `msr_purchases` | ND | ND | ND | ND | No separately labeled purchase component |
| `msr_sales` | ND | ND | ND | ND | No exact aggregate sales row; component arithmetic is not an issuer-reported observation |
| `msr_realization_or_amortization` | R | R | R | R | “Realization of MSR cash flows” / periodic “Other changes” footnote |
| `msr_fair_value_market_change` | ND | ND | ND | ND | Market-only change is not isolated |
| `msr_fair_value_assumption_change` | ND | ND | ND | ND | Assumption-only change is not isolated |
| `msr_ending_balance` | R | R | R | R | Exact period-end fair-value balance |
| `delinquency_30_plus_count_rate` | ND | ND | ND | ND | No 30+ loan-count rate |
| `delinquency_60_plus_count_rate` | R | R | R | R | EX-99.1 explicitly count-weighted, owned portfolio |
| `delinquency_90_plus_count_rate` | ND | ND | ND | ND | 90+ is disclosed by UPB, not count |
| `government_servicing_upb` | D | D | D | D | Exact FHA + VA + USDA published support observations in the owned MSR-and-MSL table |
| `conventional_servicing_upb` | D | D | D | D | Exact Freddie + Fannie + closed-end second-lien + issuer “Other” published support observations |
| `gnma_servicing_upb` | ND | ND | ND | ND | Government category includes Ginnie pools and private-investor loans |
| `fnma_servicing_upb` | R | R | R | R | Periodic loan-type table, Fannie Mae row |
| `fhlmc_servicing_upb` | R | R | R | R | Periodic loan-type table, Freddie Mac row |
| `msr_hedging_result` | R | R | R | R | EX-99.1 “Hedging results,” MSR scope |
| `msr_fair_value_multiple_of_related_upb` | D | D | D | D | Exact MSR fair value / exact UPB of the loans underlying those MSRs |
| `msr_fair_value_bps_of_related_upb` | D | D | D | D | Exact fair-value multiple × 10,000 |
| `capitalized_servicing_rate_on_additions` | D | ND | D | D | Quarterly recognized additions / exact additions-related UPB; annual Q4 inputs rejected |
| `delinquency_30_plus_upb_rate` | D | D | D | D | (30–89 UPB + 90+ UPB) / exact same-table owned MSR-and-MSL total UPB |
| `delinquency_60_plus_upb_rate` | R | R | R | R | Periodic loan-type total row, explicitly UPB-weighted |
| `delinquency_90_plus_upb_rate` | D | D | D | D | Exact 90+ owned-servicing UPB / same-table owned MSR-and-MSL total UPB |
| `foreclosure_upb_rate` | D | D | D | D | Exact detailed-table owned-servicing In foreclosure UPB / same-population total |
| `reo_upb` | ND | ND | ND | ND | No exact same-scope owned-servicing REO disclosure |
| `msr_fair_value_inputs_or_assumptions_change` | R | R | R | R | Issuer-combined valuation-model input change |
| `msr_realization_passage_time_and_other` | ND | ND | ND | ND | PFSI reports realization without TFC's combined methodology |
| `fha_servicing_upb` | R | R | R | R | Exact periodic loan-type table FHA row |
| `va_servicing_upb` | R | R | R | R | Exact periodic loan-type table VA row |
| `usda_servicing_upb` | R | R | R | R | Exact periodic loan-type table USDA row |
| `closed_end_second_lien_servicing_upb` | R | R | R | R | Exact periodic closed-end second-lien row |
| `other_servicing_upb` | R | R | R | R | Exact issuer-defined `Other (3)` row; composition is not inferred |
| `owned_msr_msl_upb` | R | R | R | R | Exact same-table owned MSR-and-MSL total UPB denominator |
| `msr_additions_related_upb` | R | ND | R | R | Exact quarterly related UPB; annual 10-K input is not relabeled as Q4 |
| `delinquency_30_to_89_upb` | R | R | R | R | Exact owned-servicing 30–89 days UPB bucket |
| `delinquency_90_plus_upb` | R | R | R | R | Exact owned-servicing 90+ UPB bucket |
| `foreclosure_upb` | R | R | R | R | Exact detailed-table In foreclosure row, current owned-servicing column |

## Methodology and comparability boundaries

- The 212-cell matrix contains 125 reported cells, including 39 governed support
  facts used by formulas, 35 derived cells, and 52 `NOT_DISCLOSED` cells. One
  additional June 2025 loan-count support fact supplies the exact opening input
  for the Q3 2025 cost derivation, so the loader contains 40 support candidates.

- The issuer's 60+ rate in the earnings table is count-weighted; the periodic
  loan-type table's 60+ rate is UPB-weighted. They are distinct metrics.
- PFSI's loan-type and delinquency tables cover owned MSRs and MSLs. The exact
  UPB underlying recognized MSRs used in fair-value economics is a separate,
  slightly narrower source field; the two denominators are never substituted.
- The ten support metrics are governed published observations with one-to-one
  source lineage. They exist to make aggregate and rate derivations reproducible;
  they are never configured authoritative numeric values.
- The owned portfolio is predominantly government-insured or guaranteed. PFSI's
  `total_servicing_upb` also includes subservicing and loans held for sale, so it
  is not interchangeable with a bank's servicing-for-others population.
- The option-adjusted-spread period-end MSR methodology began in Q3 2025. The
  accounting-policy boundary must remain visible.
- `Expenses excluding valuation-related items` remains excluded: it combines
  operating, payoff-related, credit-loss/provision, and interest expense.
- “Per-loan annual cost of servicing” in the periodic valuation table is an MSR
  model input, not reported operating cost per serviced loan.
- The Cenlar subservicing acquisition had not closed by Q2 2026 and is not
  included in these observations.

## Retained-byte identities

The exact byte length, SHA-256, URL, accession, publication/retrieval timestamps,
HTTP last-modified value, representation, capture method, and locators for every
source are recorded in `config/phase3/pfsi_sources.yaml`. The existing Stage A
PFSI and TFC retained files were not edited.
