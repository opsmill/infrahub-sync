# Canonical field and normalization map

The executable oracle is `tests/conformance/oracle.py`; executed-surface adapters are in
`tests/conformance/interface_adapters.py`. Its canonical envelope retains
the fields below; adapters remove only interface transport/rendering containers.

| Canonical field | CLI source | Python source | Managed source | Comparison |
|---|---|---|---|---|
| `operation` | captured core result after successful Typer invocation | actual v1 `RunResult` | actual worker result dictionary | exact |
| `plan_fingerprint` | decoded published review | decoded published review | decoded published review | exact |
| `counts.create/update/delete` | captured core result summary | actual v1 `RunResult.counts` | actual worker result summary | exact, zero-filled |
| `outcome` | captured core status | actual v1 `RunResult.outcome` | actual worker result outcome | exact |
| `destination_effects` | instrumented isolated destination measurement | instrumented isolated destination measurement | instrumented isolated destination measurement | exact |
| `product_record.*` | DB-003 lookup | DB-003 lookup | DB-003 lookup | every `ProductRun` field retained |
| `result.*` | mapped captured core result | mapped actual v1 return | mapped actual worker return | common run ID, operation, outcome, counts, and successful boundary retained |
| `artifact_references[]` | DB-003 artifact refs | DB-003 artifact refs | DB-003 artifact refs | every `ArtifactReference` field retained |
| `artifact_semantics` | decoded `plan-review` | decoded `plan-review` | decoded `plan-review` | exact |

## Approved normalization

Normalization is an exact schema-path allowlist, not a recursive key-name rule:

- `product_record.run_id`, `product_record.started_at`, `product_record.finished_at`;
- `product_record.artifact_refs[*].run_id` and `.created_at`;
- `product_record.prefect_executions[*].flow_run_id`, `.deployment_id`, and
  `.last_observed_at`;
- `result.run_id`;
- `artifact_references[*].run_id` and `.created_at`;
- `artifact_semantics.run_id`.

A single envelope must first prove that every present run identity above has the same
value. Only after this intra-envelope ownership check succeeds are those locations mapped
to the cross-surface `<generated>` placeholder. A ProductRun, returned result, reference,
or artifact that names a different owner therefore fails before normalization.

A nested semantic payload field named `run_id` or `created_at` remains exact. Mutation
tests prove disagreements at `product_record.payload.run_id`, `result.payload.created_at`,
and `artifact_semantics.payload.run_id` fail the oracle. Separate ownership mutations
prove disagreements in ProductRun, result, and artifact identity locations also fail.

`None` remains `None`, so absence is not normalized into presence. Configuration
references, operation/phase/outcome vocabulary, counts, results, artifact IDs, kinds,
media types, sizes, digests, object/manifest keys, audit links, actors, and Prefect link
structure are not removed or rewritten.

The conformance fixture uses one explicit run ID where possible. This keeps content
digests directly comparable instead of inventing a digest exception for an artifact whose
payload embeds a generated run ID.

## Interface-owned fields

HTTP actor/audit evidence and Prefect execution links remain visible. The executable
three-interface equality matrix uses an actor-free managed worker observation equivalent
to the CLI/Python invocation for plan, apply, and sync. The HTTP-to-Prefect matrix executes
plan, verify, apply, and sync separately and asserts that its actor, audit links, and
Prefect links remain present. Those HTTP-only product fields are neither projected away
nor falsely claimed equal to standalone records.

The landed CLI has review (`diff --from-plan`) rather than an independent verify
operation. Python and managed verify compare their actually returned common fields
(`run_id`, operation/stage, and outcome), durable verification result, and artifact.
Managed verify does not return action counts, so the report does not claim full verify
return-envelope equality. CLI review is exercised against the exact same manifest bytes
produced by CLI plan and subsequently verified by Python.
