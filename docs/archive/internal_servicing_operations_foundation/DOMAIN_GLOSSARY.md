# Mortgage servicing domain glossary

These definitions establish shared product language; they are not legal advice, accounting policy, investor guidance, or a substitute for an organization's approved data dictionary. When a system-of-record code conflicts with a generic definition, preserve the source value and map it through a versioned, owner-approved crosswalk.

## A–C

**Active servicing population** — The loans the organization is responsible for servicing at a stated snapshot under an approved population rule. Boarding, transfers, payoff, liquidation, product, and test-record treatment must be explicit.

**Advance** — Funds a servicer supplies for a loan obligation or servicing expense under applicable contracts or requirements. Principal-and-interest, tax-and-insurance, property-preservation, and corporate advances must remain distinct.

**Adjustable-rate mortgage (ARM)** — A mortgage whose note rate may change under contractual index, margin, cap, and adjustment rules. The dashboard may show sourced terms and upcoming source events; it is not a notice or rate-calculation engine.

**Amortization** — Contractual allocation of scheduled payments over time to principal and interest. Modifications, curtailments, capitalization, ARM changes, and other events can change the schedule.

**Application of payment** — Allocation of received funds to principal, interest, escrow, fees, advances, or other components under authoritative rules. The dashboard does not perform or recommend application.

**As-of time / snapshot time** — The effective instant represented by a dataset or metric. It is different from extraction and ingestion time.

**Automatic stay** — A bankruptcy protection that generally restricts specified collection and legal activity after a filing. Applicability, scope, relief, and termination are legal questions for authorized staff/counsel, never the model.

**Bankruptcy case** — A court matter linked to a debtor and potentially a serviced loan. Chapter, court, case number, dates, status, and stay indicators require authoritative court/counsel sources and restricted access.

**Boarding** — Bringing a loan and its servicing history onto a servicing platform, often after origination or transfer. Boarding reconciliation should cover balances, terms, histories, documents, status, and protections.

**Borrower** — A person obligated under the note. A mortgagor, confirmed successor in interest, authorized party, consumer, debtor, and property owner may have different rights and roles; do not treat the terms as interchangeable.

**Business date** — The source-approved operational date used for servicing processing/reporting. It may differ from calendar date and must include timezone/calendar semantics.

**Charge-off** — An accounting or servicing status applied under governing requirements. It must not be interpreted by the dashboard as automatically extinguishing an obligation or authorizing communication.

**Claim** — A request for payment or reimbursement, often to an insurer, guarantor, investor, or bankruptcy estate. Claim type and status must identify their governing program/source.

**Complaint** — An expression of dissatisfaction captured under an organization's approved complaint definition. A complaint, inquiry, notice of error, request for information, dispute, and appeal can overlap but are not interchangeable.

**Continuity of contact** — Servicing processes for making assigned personnel available to certain delinquent borrowers under applicable rules. The dashboard may monitor authoritative status; it does not decide applicability.

**Corporate advance** — A servicer-funded expense, such as certain legal, inspection, preservation, or other costs, tracked separately from principal, interest, and escrow.

**Cure** — Movement from a defined delinquency state to a less delinquent/current state under a metric definition. An analytical “cure” does not necessarily mean legal reinstatement or satisfaction of every amount.

**Curtailment / principal curtailment** — Principal paid beyond the scheduled principal component, subject to authoritative application rules.

**Custodial account** — An account used to hold and remit servicing collections under contractual and regulatory controls. This dashboard is not a custodial accounting system.

## D–H

**Days past due (DPD)** — An operational measure of delinquency relative to the earliest unpaid contractual obligation under an approved definition. Prefer the authoritative source value; do not infer legal status from DPD alone.

**Delinquency** — Failure to make an amount sufficient to cover a periodic payment by the date required under the applicable definition. Operational buckets must state their calculation, source, and treatment of partial payments.

**Delinquency bucket** — A mutually defined range such as current, 1–29, 30–59, 60–89, or 90+ DPD. Unknown is a distinct value, not current.

**Dual tracking** — A term commonly used for pursuing foreclosure while a borrower is under loss-mitigation review. Applicable restrictions and procedural safeguards depend on law and facts and require authorized human/legal review.

**Effective date/time** — When a business fact or event takes effect. It differs from occurrence, posting, receipt, extraction, and ingestion times.

**Early intervention** — Outreach and information processes for certain delinquent borrowers under applicable servicing requirements. The dashboard does not determine whether, when, or how contact is legally required.

**Escrow account** — Funds controlled by a servicer on a borrower's behalf for taxes, insurance premiums, or other covered property-related charges. It is distinct from suspense and custodial accounts.

**Escrow analysis** — A periodic evaluation of expected escrow receipts and disbursements under applicable requirements and contracts. A dashboard projection is not an authoritative analysis.

**Escrow cushion** — A permitted target balance retained for anticipated escrow disbursements, subject to applicable limits and rules.

**Escrow deficiency** — Under applicable escrow terminology, a negative escrow balance. Keep it distinct from shortage and from a projected future balance.

**Escrow shortage** — Under applicable escrow terminology, an amount by which the current escrow balance is less than the target balance at the time of analysis.

**Escrow surplus** — Under applicable escrow terminology, an amount by which the current escrow balance exceeds the target balance at analysis, subject to applicable treatment rules.

**Event time** — When an event occurred in the source domain. Preserve it separately from effective, posting, and ingestion times.

**Exception** — A record requiring investigation because it violates an approved operational or data rule. It is not automatically an error, compliance violation, or indication of borrower fault.

**Fee** — A sourced charge assessed under contractual, legal, investor, and policy rules. A displayed fee is not proof that it is collectible, due, or appropriate to communicate.

**First notice or filing** — A jurisdiction-dependent foreclosure milestone used in federal servicing rules. The authoritative legal/case system and counsel determine the actual event and implications.

**First payment default (FPD)** — Delinquency within an explicitly defined early-payment window. The window, population, and purpose must be approved; the term must not drive adverse treatment by itself.

**Forbearance** — An arrangement to temporarily pause or reduce required payments under approved terms. It does not generally erase amounts and must be represented using authoritative plan facts.

**Foreclosure** — A legal process to enforce a security interest in property. Stage, requirements, holds, timelines, and terminology vary by jurisdiction and case; the dashboard cannot provide a legal conclusion or advance the process.

**Force-placed / lender-placed insurance** — Hazard insurance obtained by a servicer when required coverage is believed absent, subject to applicable notice, evidence, cancellation, and charge rules. No dashboard or model action is authoritative.

**Grace period** — A contractual or policy period after a due date that may affect late charges or treatment. It does not necessarily change the contractual due date or DPD.

**Hold** — A source-system restriction on specified servicing activity. Holds are typed, sourced, time-bound where appropriate, and fail closed when stale or contradictory.

**Human review** — An explicit, attributable evaluation by a trained and authorized person with sufficient evidence, authority, time, and an ability to reject or correct a proposal. Clicking “acknowledge” is not necessarily approval.

## I–P

**Ingestion time** — When the dashboard's data platform accepted a record. It is not proof of source occurrence or effective time.

**Investor** — The owner or beneficial interest holder for a mortgage asset, or a party represented by an investor code. Investor contracts/guides may affect servicing; codes and effective dates must be governed.

**Investor reporting/remittance** — Contractual reporting and transfer of funds to an investor. Portfolio dashboard metrics are not investor reports or remittance calculations.

**Loan modification** — An approved change to loan terms. Trial and permanent modification stages, effective dates, documents, and accounting treatment must be sourced and kept distinct.

**Loan token** — An application-safe pseudonymous identifier used instead of a full loan/account number. Tokenization reduces exposure but does not make the record non-sensitive.

**Loss mitigation** — Processes and options intended to address delinquency or avoid foreclosure, depending on product and program. Application review, completeness, eligibility, evaluation, offer, denial, and appeal are high-impact human decisions.

**Mortgage insurance / guaranty** — Coverage or guaranty protecting specified parties against defined losses. Private mortgage insurance and government program coverage have different rules and data.

**Nonperforming loan (NPL)** — A portfolio classification based on an approved delinquency/nonaccrual definition. It is not interchangeable with 90+ DPD without an explicit definition.

**Note rate** — The contractual interest rate used under the note, including current sourced rate for an ARM where applicable. It is distinct from annual percentage rate and effective yield.

**Notice of error (NOE)** — A borrower assertion of a servicing error that may invoke response duties when applicable. The authoritative case process determines scope, timing, investigation, and response.

**Partial payment** — Funds less than the amount required for a full periodic payment under authoritative application rules. Treatment can include suspense or other handling; the dashboard does not decide it.

**Paid-through date / next payment due date** — Source fields representing the installment status of a loan. Exact semantics vary; both require a data contract and must not be reconstructed casually from cash events.

**Payoff** — Satisfaction of the loan obligation based on an authoritative payoff process and amount as of a stated date. The dashboard must not quote or calculate payoff.

**Periodic payment** — The amount due for a scheduled payment under the applicable contractual/regulatory definition, commonly including principal, interest, and escrow where applicable.

**P&I / PITI** — Principal and interest; and principal, interest, taxes, and insurance. “PITI” may omit other components and must not be used as an authoritative amount without field-level definition.

**Posting date** — Date a transaction is recorded in an authoritative ledger. It is distinct from received and effective dates.

**Prepayment** — Principal paid before scheduled maturity through payoff, curtailment, or other source-coded activity. Voluntary and involuntary liquidation must not be combined without an approved definition.

**Property preservation** — Activities intended to protect and maintain collateral, subject to legal, investor, insurer/guarantor, vendor, and policy controls.

## Q–Z

**Reconciliation** — Comparison of two approved datasets or calculations at aligned grain, population, time, and definition, with documented tolerance and ownership.

**Redefault** — A new defined delinquency/default event after a modification, cure, or other resolved state within an explicit observation window. Cohort and seasoning rules are essential.

**Reinstatement** — Satisfaction of conditions necessary to bring a defaulted loan current under authoritative legal/servicing rules. It is not the same as an analytical cure unless expressly defined.

**Repayment plan** — An approved arrangement to repay past-due amounts over time in addition to or through scheduled obligations.

**Request for information (RFI)** — A borrower request for servicing information that may invoke response duties when applicable. Scope, exceptions, deadlines, and response belong to the authoritative case process.

**Return / reversal** — A return is a payment lifecycle result such as bank return under source rules; a reversal removes or offsets a prior posting. Preserve event lineage and do not treat the terms as synonyms.

**Roll rate** — The proportion of a stable prior-period cohort that moves between defined delinquency buckets at a later approved snapshot. Transfers, payoff, liquidation, missing records, and cures need explicit treatment.

**Scheduled payment** — A payment expected under authoritative contractual terms. It is distinct from actual amount received/applied and from an amount a model could infer.

**Servicer** — The party responsible for receiving scheduled payments and performing other servicing functions under applicable definitions and contracts.

**Servicing transfer** — Assignment of servicing responsibility between servicers. Transfer dates, notice, payment handling, histories, documents, and borrower protections require controlled boarding/deboarding.

**Source of truth / system of record** — The owner-approved authority for a defined fact at a defined time. No single system is necessarily authoritative for every field.

**Special servicing** — Enhanced handling for specified delinquent, defaulted, high-touch, or otherwise designated loans, often under contractual rules. It is not a standardized legal status.

**Suspense** — A controlled holding classification for funds not yet applied under authoritative rules. It is not interchangeable with an escrow account or generic unapplied amount.

**Successor in interest** — A person who receives an ownership interest in a property securing a mortgage loan, with status and protections determined under applicable procedures. Potential and confirmed status must remain distinct.

**Trial period plan (TPP)** — A temporary performance period associated with a potential loan modification under authoritative program terms. Successful trial payments do not let the dashboard declare a permanent modification.

**Unapplied funds** — Funds received but not yet allocated to authoritative loan components. Source accounting classification controls; do not derive by subtraction.

**Unpaid principal balance (UPB)** — Principal remaining on the loan as represented by the authoritative source at a stated time. It excludes or includes no other components unless the data contract explicitly says so.

**Waterfall** — An ordered evaluation of loss-mitigation options under applicable investor/insurer/program rules. It is a controlled decision process, not a general-purpose model prompt.

**Workout** — A broad operational term for an arrangement addressing delinquency/default. Use the precise sourced option/status whenever possible.

## Data and AI language

**Deterministic calculation** — A versioned computation that produces the same result from the same inputs and exposes its rules, precision, and lineage.

**Grounded response** — A model response whose factual claims can be traced to approved retrieved sources or typed tool results. Grounding reduces but does not eliminate error.

**Hallucination** — Model-generated content unsupported by the available evidence. It must never be written back as a servicing fact.

**Prompt injection** — Instructions embedded in user or retrieved content that try to override system policy, expose data, or invoke tools improperly. Retrieved text is untrusted data, not authority.

**Structured tool** — A narrowly scoped function with a typed request/response, authorization, deterministic behavior, read-only policy for this product, audit metadata, and bounded output.

**Synthetic data** — Artificial records created without copying or transforming real borrower data and designed to demonstrate states and edge cases. Synthetic data is still labeled and access-controlled because it may resemble real data or expose internal logic.
