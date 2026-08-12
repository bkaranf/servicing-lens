# TFC disclosure map

Status: Stage A retained-evidence map as of August 12, 2026. The Q2 2026 filed
exhibit below is retained and parsed; linked periodic filings are official source
inventory but remain `NOT_RETAINED_NOT_CHECKED` for catalog-wide missingness.

## Entity and scope

- SEC registrant: Truist Financial Corporation
- Ticker/exchange: TFC / NYSE
- CIK: `0000092230`
- Fiscal year end: December 31
- Stage A periods: Q3 2025, Q4 2025, Q1 2026, and Q2 2026
- Observation entity: consolidated Truist Financial Corporation
- Observation scope: residential mortgage servicing
- Principal bank subsidiary: Truist Bank, a North Carolina-chartered bank

The SEC observations must not be assigned to Truist Bank. Future Call Report or other regulatory observations belong to their actual regulatory reporting entity and require an explicit relationship to the SEC registrant.

Truist reports Consumer and Small Business Banking and Wholesale Banking as its two reportable segments. The selected servicing disclosure is a consolidated residential mortgage table, not a standalone servicing segment.

## Canonical evidence

The canonical four-quarter table is the [Q2 2026 Quarterly Performance Summary](https://www.sec.gov/Archives/edgar/data/92230/000009223026000096/ex992-qpsx2q26.htm), Exhibit 99.2 to Form 8-K accession `0000092230-26-000096`, filed July 17, 2026. The 8-K states that Exhibit 99.2 is deemed filed. The locator is “Selected Mortgage Banking Information & Additional Information,” page 10.

Accounting-policy and portfolio support comes from [Q2 2026 Form 10-Q, Note 6](https://www.sec.gov/Archives/edgar/data/92230/000009223026000099/tfc-20260630.htm), accession `0000092230-26-000099`, filed July 31, 2026.

Period-specific filed support:

- [Q3 2025 Form 10-Q](https://www.sec.gov/Archives/edgar/data/92230/000009223025000157/tfc-20250930.htm), accession `0000092230-25-000157`
- [Q4 2025 Form 10-K](https://www.sec.gov/Archives/edgar/data/92230/000009223026000030/tfc-20251231.htm), accession `0000092230-26-000030`
- [Q1 2026 Form 10-Q](https://www.sec.gov/Archives/edgar/data/92230/000009223026000062/tfc-20260331.htm), accession `0000092230-26-000062`
- [Q2 2026 Form 10-Q](https://www.sec.gov/Archives/edgar/data/92230/000009223026000099/tfc-20260630.htm), accession `0000092230-26-000099`

## Implemented retained mappings

| Metric ID | Exact source label | Semantics | Reported precision | Scope note |
|---|---|---|---|---|
| `servicing_for_others_upb` | Loans serviced for others | period-end UPB | whole USD millions | residential mortgages serviced for others |
| `bank_owned_loans_serviced_upb` | Bank-owned loans serviced | period-end UPB | whole USD millions | bank-owned residential mortgages serviced |
| `total_servicing_upb` | Total servicing portfolio | period-end UPB | whole USD millions | serviced for others plus bank-owned loans; residential only |
| `weighted_average_servicing_fee_bps` | Weighted-average servicing fee on mortgage loans serviced for others | disclosed quarter-end rate | 0.01 percentage point / one basis point | serviced-for-others population only |
| `servicing_revenue` | Residential mortgage servicing income before MSR valuation | quarterly duration | whole USD millions | issuer-specific methodology; not contractual servicing fees |

The source reports UPB and income in whole millions. The fixture preserves the displayed integer and a scale of `1000000`; it does not invent dollar precision. The fee is reported as a percentage to two decimal places and is deterministically converted to basis points.

## Accounting and comparability caveats

- Note 6 says loan servicing income is derived primarily from contractual servicing fees, late fees net of curtailment costs, and other ancillary fees. It also reports residential MSRs at fair value.
- “Residential mortgage servicing income before MSR valuation” must retain its exact issuer methodology. It is not interchangeable with `servicing_fee_income`.
- The TFC total portfolio excludes separately disclosed commercial servicing. “Total” therefore means total residential servicing portfolio, not all enterprise servicing.
- TFC total servicing UPB is not comparable to PFSI total servicing UPB: TFC combines serviced-for-others and bank-owned loans, while PFSI combines owned-MSR, subserviced, and held-for-sale populations.
- TFC serviced-for-others UPB versus PFSI owned-MSR UPB may be assessed as comparable with caveats, never silently treated as identical.
- TFC does not disclose a standalone servicing-segment expense, pretax income, or loan count in the selected table. Those metrics must remain `NOT_DISCLOSED` for this scope.

Recorded fixture: `tests/fixtures/stage_a/tfc_servicing_2025q3_2026q2.json`.
