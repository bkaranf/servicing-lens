# PFSI disclosure map

Status: proposed Stage A nonbank issuer, based on public evidence available through August 11, 2026.

## Entity and scope

- SEC registrant: PennyMac Financial Services, Inc.
- Ticker/exchange: PFSI / NYSE
- CIK: `0001745916`
- Fiscal year end: December 31
- Stage A periods: Q3 2025, Q4 2025, Q1 2026, and Q2 2026
- Observation entity: consolidated PFSI
- Observation scope: PFSI Servicing reportable segment

PFSI is a holding corporation that controls Private National Mortgage Acceptance Company, LLC. PennyMac Loan Services, LLC is its principal nonbank mortgage-banking subsidiary. PennyMac Mortgage Investment Trust (`PMT`) is separately listed; PFSI, PNMAC, PLS, and PMT must remain distinct entities.

The Servicing segment performs servicing and subservicing for nonaffiliate investors, manages early-buyout transactions, and services loans for PMT. Corporate and other activity is outside the segment.

## Canonical evidence

The canonical four-quarter tables are in the [Q2 2026 earnings release](https://www.sec.gov/Archives/edgar/data/1745916/000110465926088174/tm2621541d1_ex99-1.htm), Exhibit 99.1 to Form 8-K accession `0001104659-26-088174`, furnished July 29, 2026. The locators are “Servicing Segment Profitability and Key Metrics,” pages 12-13.

The exhibit is furnished under Item 2.02 and is not deemed filed for Exchange Act Section 18. Preserve `FURNISHED` status and use the filed 10-Q/10-K as the primary GAAP and accounting-policy authority.

Filed support:

- [Q3 2025 Form 10-Q](https://www.sec.gov/Archives/edgar/data/1745916/000110465925103162/pfsi-20250930x10q.htm), accession `0001104659-25-103162`
- [Q4 2025 Form 10-K](https://www.sec.gov/Archives/edgar/data/1745916/000110465926018142/pfsi-20251231x10k.htm), accession `0001104659-26-018142`
- [Q1 2026 Form 10-Q](https://www.sec.gov/Archives/edgar/data/1745916/000110465926055690/pfsi-20260331x10q.htm), accession `0001104659-26-055690`
- [Q2 2026 Form 10-Q](https://www.sec.gov/Archives/edgar/data/1745916/000110465926090486/pfsi-20260630x10q.htm), accession `0001104659-26-090486`; relevant locators are Note 11 and Note 26

## Proposed mappings

| Metric ID | Exact source label | Semantics | Reported precision | Scope note |
|---|---|---|---|---|
| `total_servicing_upb` | Total UPB | period-end UPB | whole USD billions | owned MSR UPB plus subserviced UPB plus loans held for sale |
| `owned_msr_upb` | Owned MSR UPB | period-end UPB | whole USD billions | UPB underlying servicing rights owned by PFSI |
| `servicing_pretax_income` | Servicing pretax income | quarterly duration | whole USD millions | issuer-reported Servicing segment result including valuation-related effects |

The source reports portfolio values in whole billions and pretax income in whole millions. The fixture preserves those values with scales of `1000000000` and `1000000`; it does not infer finer precision.

## Accounting and comparability caveats

- PFSI classifies MSRs as Level 3 fair-value assets and uses a discounted-cash-flow approach.
- Beginning in Q3 2025, PFSI adopted an option-adjusted-spread model for period-end MSR fair value. Record an accounting-policy regime boundary effective Q3 2025 and surface it in MSR comparisons.
- `total_servicing_upb` includes three different populations: owned MSRs, subservicing, and loans held for sale. It must not be relabeled as servicing-for-others UPB.
- `servicing_pretax_income` includes valuation-related effects. “Pretax income excluding valuation-related items” is a separate non-GAAP measure and requires its own metric definition and reconciliation.
- PFSI total servicing UPB is not comparable to TFC total servicing UPB because their portfolio populations differ.
- PFSI owned MSR UPB versus TFC serviced-for-others UPB may be comparable with caveats after confirming population exclusions; it is not automatically equivalent.
- The announced Cenlar subservicing acquisition had not closed by Q2 2026. If it closes, create a corporate-action and reporting-scope boundary rather than silently extending the trend.

The label “Expenses excluding valuation-related items” is deliberately not mapped to `servicing_operating_expense`: it includes operating expense, payoff-related expense, credit losses/provisions, and interest expense. The recorded candidate must quarantine pending a distinct definition or human review.

Recorded fixtures:

- `tests/fixtures/stage_a/pfsi_servicing_2025q3_2026q2.json`
- `tests/fixtures/stage_a/ambiguous_candidate_pfsi_servicing_expense.json`
