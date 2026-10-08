# Acceptance Validation Contract

## Entry point

The repository exposes a read-only Invoke task:

```text
uv run invoke release.validate-acceptance --manifest <path> [--contract <path>]
```

Without `--contract`, the task validates consistency against the checked-out normative contract.
With `--contract`, it validates consistency against explicitly retained contract bytes. Neither mode
claims that the selected contract is approved or that the candidate is release-eligible; spec 014
supplies approval provenance and owns that decision. The task performs no network request, does not
fetch evidence bodies, and does not modify the manifest or release record.

The contract parser accepts exactly one positive-integer `**Contract version:** N` declaration in
the position and exactly one `## Required MVP criteria` section followed by the six-column table
defined in [data-model.md](../data-model.md). It fails closed on any structural change or invalid
criterion row; the Markdown table is the only criterion catalog. Each `Evidence` cell is a
comma-and-space-separated list of lowercase evidence-type identifiers matching
`[a-z][a-z0-9-]*`; it defines the types accepted for that criterion.

## Successful result

Success exits with status 0 and reports only:

- contract version and digest;
- candidate version, source revision, and image digest;
- criterion pass count and total count;
- evaluator identifier and approval time;
- overall manifest decision;
- whether the selected bytes are the checked-out or an explicitly retained contract;
- confirmation that the manifest is consistent with the selected contract;
- a notice that evidence metadata passed but artifact availability and bytes remain a spec-014 gate.
- a notice that contract approval and release eligibility remain spec-014 decisions.

No evidence body, credential-bearing location, or unbounded note is printed.

## Refusals

Every refusal exits non-zero and identifies the field or criterion plus a safe next action. Required
refusal families are:

| Refusal | Required next action |
|---|---|
| Unreadable or non-object JSON | Regenerate the manifest from the documented template. |
| Unsupported schema version | Use a validator supporting the recorded version. |
| Contract version or digest mismatch | Select the contract used for qualification or requalify against the intended contract. |
| Candidate or evidence identity mismatch | Regenerate evidence for the named candidate. |
| Missing, duplicate, or unknown criterion | Rebuild the manifest from the current criterion catalog. |
| Passing criterion without evidence | Produce and attach passing evidence. |
| Missing, dangling, or duplicate evidence ID | Rebuild the normalized evidence catalog and references. |
| Evidence type not accepted by a criterion | Attach evidence of a type declared by that criterion. |
| Unsafe evidence locator | Use a normalized relative POSIX path or bounded opaque artifact ID. |
| Inconsistent overall decision | Correct the decision to match criterion and evaluator results. |
| Missing independent evaluator approval | Have a non-implementing evaluator execute and approve the journey. |
| Unknown field | Correct the field name or update the schema deliberately. |

Diagnostics may show identifiers, field names, counts, and digests. They must not echo evidence
bodies, evaluator notes, locator values, or other untrusted values.

## Evidence locator contract

- `relative-path` accepts normalized POSIX segments only. It rejects leading `/`, backslashes,
  empty segments, `.` or `..`, URI schemes, user information, queries, and fragments.
- `artifact-id` accepts 1–128 ASCII letters, digits, periods, underscores, or hyphens, starts with an
  alphanumeric character, and contains no colon.
- Locators are retained but never printed by the validator.

## Compatibility

- Schema version `1` is strict: unknown fields are refused.
- The committed JSON Schema is generated from the runtime Pydantic models; a test fails when it is
  stale. One shared valid/invalid fixture corpus exercises the generated schema shape and runtime
  semantic validation.
- A new optional field requires a schema-version decision, contract tests, and documented semantics.
- Removing or redefining a field is a breaking schema-version change.
- Changing the normative criteria changes the contract version and digest and requires
  requalification; it does not rewrite historical manifests.
