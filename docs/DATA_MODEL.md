# Data model

## Design goals

The database preserves source evidence, semantic interpretation, publication
history, and what the system knew at a point in time. It is not a mutable cache of
the latest number.

The implementation uses SQLAlchemy 2 models and Alembic migrations over
PostgreSQL. Monetary amounts, balances, UPB, rates, and derived values use
\`NUMERIC\` mapped to Python \`Decimal\`. Authoritative schemas reject binary
floating-point values. Timestamps are timezone-aware.

Typed Pydantic/domain models validate boundaries. Pure financial and
comparability functions do not depend on SQLAlchemy, FastAPI, LangChain, or
LangGraph.

## Required tables

### Company, security, entity, and regime tables

| Table | Grain and required responsibility |
| --- | --- |
| \`companies\` | One stable corporate subject; legal/display name, bank/nonbank classification, status, valid and knowledge time |
| \`securities\` | One effective-dated traded security; company, ticker, exchange, security identifiers, valid and knowledge time |
| \`reporting_entities\` | One legal, regulatory, segment, subsidiary, or operating reporting boundary; type, name, parent company, status |
| \`reporting_scopes\` | One versioned entity/population/business boundary; category, inclusions/exclusions, source label, effective interval |
| \`entity_identifiers\` | One typed namespace/value for a company or reporting entity; normalized/source values, effective interval, evidence |
| \`entity_relationships\` | One effective-dated typed directed relationship between entities; attributes, evidence, valid/knowledge time |
| \`fiscal_calendar_regimes\` | One entity's effective fiscal-calendar rule set and evidence |
| \`accounting_policy_regimes\` | One entity/scope's effective accounting or measurement policy and evidence |
| \`corporate_actions\` | One acquisition, divestiture, rename, reorganization, ticker change, or material portfolio transfer; dates, parties, continuity effect, evidence |

### Filing and raw-evidence tables

| Table | Grain and required responsibility |
| --- | --- |
| \`filings\` | One SEC or regulatory filing/report identity; reporting entity, form/type, accession/native ID, filed/publication/report dates, amendment and supersession links |
| \`filing_documents\` | One primary document or exhibit belonging to a filing; document name/type/sequence, source evidence, version |
| \`source_evidence\` | One immutable acquired byte payload; source class, URL, retrieval/publication times, identifier, SHA-256, media type, parser version, acquisition run, candidate entity/period, byte length, HTTP validators, retention location |
| \`raw_xbrl_facts\` | One unnormalized XBRL fact; filing/document/evidence, concept, taxonomy, entity/context, dimensions, unit, raw value, decimals, instant/duration, dates |
| \`raw_regulatory_facts\` | One unnormalized regulatory fact; source/evidence, reporting entity and scope, schedule/item identifiers, period, unit, raw value, revision |

Original source bytes live behind the evidence retention interface. Database
records point to content-addressed retention locations; they do not replace the
bytes.

### Metric and observation tables

| Table | Grain and required responsibility |
| --- | --- |
| \`metric_definitions\` | One stable canonical metric identity and display/business ownership metadata |
| \`metric_definition_versions\` | One immutable semantic version; meaning, grain, formula, scopes, units, period, methodology, evidence, validation, reconciliation, comparability, prohibited uses, effective interval |
| \`metric_aliases\` | One source/issuer/scope/effective-dated raw label mapping to a definition version, with evidence and approval state |
| \`metric_observations\` | One semantic metric/entity/scope/period/methodology observation revision |
| \`observation_evidence\` | One observation-to-evidence link with locator, raw label/value, role, extraction and validation metadata |
| \`observation_revisions\` | One attributable transition between observation revisions, with reason, run/review origin, valid and knowledge time |
| \`comparability_assessments\` | One pairwise assessment for two observation revisions under a policy version; verdict, ordered reasons, caveats, exact calculation permission |

### Event, pipeline, quarantine, and review tables

| Table | Grain and required responsibility |
| --- | --- |
| \`earnings_events\` | One company earnings event; fiscal period, event/publication times, filing and evidence links |
| \`pipeline_runs\` | One idempotent pipeline execution; run key, requested company/period/stages, code/config/parser versions, state, timestamps, counts |
| \`ingestion_errors\` | One structured error attached to a run/stage/evidence ID; safe code, deterministic/transient class, retryability, terminal effect |
| \`quarantine_candidates\` | One unpublished extraction candidate and bounded metadata/excerpt reference; proposed semantics, conflicts, uncertainty, model/parser versions, state |
| \`human_review_decisions\` | One attributable approve/reject/escalate decision; candidate, reviewer identity/role, reason, evidence snapshot, thread/run IDs, resulting revision/config version, timestamp |

## Observation contract

Every \`metric_observations\` row includes:

- observation ID and immutable revision number;
- metric ID and semantic definition version;
- reporting entity ID and reporting scope ID;
- fiscal-calendar and accounting-policy regime IDs;
- period start and end;
- fiscal year and fiscal quarter;
- instant or duration classification;
- exact nullable value;
- currency, unit, canonical scale, reported scale, and precision/reported decimals;
- observation state;
- methodology and accounting basis;
- extraction method;
- parser, model, prompt, recipe, code, and configuration versions where
  applicable;
- quality state and validation summary;
- valid-from/valid-to timestamps;
- known-from/known-to timestamps;
- supersedes/superseded-by links;
- publication timestamp and publishing pipeline run; and
- semantic-key digest.

Evidence IDs are represented through \`observation_evidence\`, with at least one
eligible link required for a published measured observation. Each link includes
the evidence role, page/table/row/column, section, DOM, or XBRL locator, issuer
label, raw reported text/value, disclosed unit/scale, extraction method, and
validation result.

## Observation states

The controlled states are:

- \`REPORTED_ACTUAL\`;
- \`PRELIMINARY_REPORTED\`;
- \`PRO_FORMA\`;
- \`ANNOUNCED_IMPACT\`;
- \`DERIVED\`; and
- \`NOT_DISCLOSED\`.

\`NOT_DISCLOSED\` has no numeric value. It means the expected eligible sources for
that entity/metric/period/scope were evaluated and no disclosure was found.
Source-not-checked, processing-failed, ambiguous, quarantined, and not-applicable
are quality/workflow conditions and cannot masquerade as \`NOT_DISCLOSED\`.

Publication state is independent of observation state:

- \`CANDIDATE\`;
- \`QUARANTINED\`;
- \`VALIDATED\`;
- \`PUBLISHED\`;
- \`REJECTED\`; and
- \`SUPERSEDED\`.

Only \`PUBLISHED\` revisions appear as values on public analytical routes.

## Exact numeric rules

- Database columns use bounded \`NUMERIC(precision, scale)\` chosen for the metric
  family; application conversion uses \`Decimal\` from source strings.
- No financial value passes through JSON or templates as a binary float.
- API values serialize as strings with explicit currency, unit, scale, and
  precision.
- Rounding occurs only under the metric-definition display rule; stored normalized
  values preserve exact parsed precision.
- Negative and parenthetical source values retain raw text and an explicit sign
  transformation.
- Division specifies zero, missing, quantization, and precision behavior.

## Semantic identity and idempotency

The active semantic key contains at least:

- metric-definition version;
- reporting entity and scope;
- period start/end, fiscal regime, and instant/duration type;
- observation state and methodology variant;
- currency, unit, and scale;
- accounting-policy regime; and
- controlled metric dimensions.

Pipeline-run ID, acquisition time, and evidence ID are not semantic dimensions.
Replaying the same evidence and versions collides with the existing semantic
observation rather than producing a duplicate. Changed evidence, mapping,
definition, policy, or review outcome creates a revision and preserves the prior
row.

## Bitemporal behavior

Valid time answers when a fact or mapping applied to the issuer. Knowledge time
answers when the system knew it. Both intervals are half-open and timezone-aware.

An as-known-at query selects the observation revision whose valid interval covers
the requested business time and whose knowledge interval covers the requested
knowledge time. Amendments, late evidence, and human decisions close knowledge
intervals and create successors; they do not update history in place.

## Constraints and publication invariants

- Foreign keys enforce company/entity/scope/evidence identity.
- SHA-256 plus byte length protects evidence identity; URL is not unique identity.
- Accession/native filing identity plus document sequence is unique within its
  source.
- One active published revision exists per semantic key.
- A published measured observation has a numeric value and eligible evidence.
- A published \`NOT_DISCLOSED\` observation has no numeric value and records the
  checked source set.
- A derived observation links every exact input revision and formula version.
- A quarantine candidate cannot be selected by public observation queries.
- A review decision cannot delete source, candidate, observation, or revision
  history.
- Comparability assessments reference exact observation revisions and policy
  version; they are invalidated/recomputed when an input is superseded.

## Migration contract

Alembic migrations must:

- create the schema from an empty PostgreSQL database;
- upgrade through every committed revision and support a documented downgrade
  policy;
- use exact database types and explicit constraints;
- seed no fabricated financial observations;
- keep metric/universe seed artifacts versioned and reproducible; and
- pass schema-contract checks comparing ORM metadata, migrations, API schemas, and
  generated artifacts.
