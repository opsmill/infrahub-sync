# Canonical field and normalization map

The executable oracle is `tests/conformance/oracle.py`. Its canonical envelope retains
the fields below; adapters remove only interface transport/rendering containers.

| Canonical field | CLI source | Python source | Managed source | Comparison |
|---|---|---|---|---|
| `operation` | core request/result | v1 request/result | managed stage/product run | exact |
| `plan_fingerprint` | saved-plan checksum | saved-plan checksum | retained plan checksum | exact |
| `counts.create/update/delete` | core/public summary | `ActionCounts` | stage/product summary | exact, zero-filled |
| `outcome` | core status | v1 outcome | managed stage/product outcome | exact |
| `destination_effects` | isolated destination observation | isolated destination observation | isolated destination observation | exact |
| `product_record.*` | DB-003 lookup | DB-003 lookup | DB-003 lookup | every `ProductRun` field retained |
| `result.*` | retained result | v1/retained result | retained result | every named field retained |
| `artifact_references[]` | DB-003 artifact refs | DB-003 artifact refs | DB-003 artifact refs | every `ArtifactReference` field retained |
| `artifact_semantics` | decoded `plan-review` | decoded `plan-review` | decoded `plan-review` | exact |

## Approved normalization

Only generated identity and timestamp fields are replaced:

- generated identities: `run_id`, `flow_run_id`, `deployment_id`, `receipt_id`, `event_id`;
- timestamps: `started_at`, `finished_at`, `created_at`, `updated_at`, `last_observed_at`.

`None` remains `None`, so absence is not normalized into presence. Configuration
references, operation/phase/outcome vocabulary, counts, results, artifact IDs, kinds,
media types, sizes, digests, object/manifest keys, audit links, actors, and Prefect link
structure are not removed or rewritten.

The conformance fixture uses one explicit run ID where possible. This keeps content
digests directly comparable instead of inventing a digest exception for an artifact whose
payload embeds a generated run ID.

## Interface-owned fields

HTTP actor/audit evidence and Prefect execution links remain visible. A direct managed
worker comparison can use a zero-link, actor-free product run equivalent to standalone;
HTTP tests separately assert the managed actor/audit/link contract. The oracle never drops
those fields to manufacture equality.
