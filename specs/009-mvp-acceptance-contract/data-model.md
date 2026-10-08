# Data Model: MVP Acceptance Contract

## Acceptance Contract

The normative human-readable document that defines the current MVP boundary.

| Field | Type | Rules |
|---|---|---|
| `contract_version` | Positive integer | Increases whenever criteria, scope, or qualification semantics change. |
| `criteria` | Ordered set of `MvpCriterion` | Stable identifiers are unique; order is presentation-only. |
| `deferrals` | Ordered set of `ScopeDeferral` | Must not overlap a required criterion. |
| `content_sha256` | Derived SHA-256 | Computed over the exact contract bytes; never written into the document itself. |

The checked-out contract is the default selected contract for consistency validation. An explicit
path may select retained contract bytes instead. Matching either selection does not prove that the
contract is approved or that the candidate is release-eligible; spec 014 supplies approval
provenance and owns that decision. A contract does not embed its own Git revision because the commit
containing that value cannot name itself.

### Normative criterion representation

Immediately below its title, the contract contains exactly one positive-integer version declaration:

```markdown
**Contract version:** 1
```

It then contains exactly one `## Required MVP criteria` heading followed immediately by this exact
table header:

```markdown
| ID | Owner spec | Class | Requirement | Validation | Evidence |
|---|---|---|---|---|---|
```

Every following table row until the next level-two heading is one criterion. All six cells are
required single-line plain text. Additional pipes and HTML are invalid. The parser refuses a missing,
repeated, misplaced, or invalid version declaration; a missing or repeated section; a changed
header; a malformed row; a duplicate identifier; an unknown owner or class; or an empty cell. No
second criterion registry exists in code or a sidecar file.

The `Evidence` cell is a comma-and-space-separated list of lowercase evidence-type identifiers.
Each identifier matches `[a-z][a-z0-9-]*`; duplicates and empty entries are invalid. The list defines
the only evidence types accepted for that criterion.

## MVP Criterion

| Field | Type | Rules |
|---|---|---|
| `id` | Stable string | Unique and never reused for a different obligation. |
| `title` | String | Human-readable, concise, and non-empty. |
| `owner_spec` | Spec identifier | One of specs 010–014, or 009 for contract-integrity criteria. |
| `requirement` | String | Objective condition a reviewer can mark pass or fail. |
| `validation_method` | String | Names the reproducible check or review procedure. |
| `evidence_types` | Ordered set of identifiers | Names every retained-result type accepted from the owning spec. |
| `safety_class` | Enum | `safety`, `integrity`, or `release`; classification does not permit waivers. |

## Scope Deferral

| Field | Type | Rules |
|---|---|---|
| `name` | String | Unique post-MVP capability name. |
| `boundary` | String | Explains what is excluded without weakening an MVP criterion. |
| `v3_owner` | String | Larger-v3 area or roadmap card when known. |

## Qualification Manifest

| Field | Type | Rules |
|---|---|---|
| `schema_version` | Integer | Exactly `1` for this design. |
| `contract` | `ContractIdentity` | Must match the selected contract bytes. |
| `candidate` | `CandidateIdentity` | Names one source revision and immutable image digest. |
| `evidence` | Map of `EvidenceReference` | Stores each evidence description once, keyed by its `id`. |
| `qualification_record` | Evidence identifier | Refers to an entry whose type is exactly `qualification-record`. |
| `criteria` | List of `CriterionResult` | Exactly one result for every selected-contract criterion. |
| `evaluator` | `EvaluatorApproval` | Required for every decision. |
| `decision` | Enum | `pass` or `fail`; derived consistency is validated. |
| `decided_at` | UTC timestamp | ISO-8601 instant with an explicit zero offset. |

Unknown top-level or nested fields are refused so a misspelled security-relevant field cannot be
silently ignored.

The runtime returns a distinct deeply immutable validated wrapper. Its criterion collection and
each criterion's evidence identifiers are tuples, and its evidence catalog is read-only, so callers
cannot mutate checked data and later mistake it for the raw parsed manifest.

## Contract Identity

| Field | Type | Rules |
|---|---|---|
| `version` | Positive integer | Equals the selected contract version. |
| `sha256` | Lowercase SHA-256 | Equals the digest computed from selected contract bytes. |

## Candidate Identity

| Field | Type | Rules |
|---|---|---|
| `source_revision` | Full Git revision | The source used to produce the candidate. |
| `image_digest` | OCI SHA-256 digest | Immutable candidate identity, including the `sha256:` prefix. |
| `version` | Normalized version string | 1–128 characters in the bounded ASCII version grammar. |

## Criterion Result

| Field | Type | Rules |
|---|---|---|
| `criterion_id` | String | Known, unique contract criterion identifier. |
| `result` | Enum | `pass` or `fail`; no waiver state exists. |
| `evidence` | List of evidence identifiers | Each identifier resolves in the top-level catalog; a passing result requires at least one. |
| `note` | Optional string | Bounded non-secret explanation, required for a failure. |

## Evidence Reference

| Field | Type | Rules |
|---|---|---|
| `evidence_type` | Identifier | Declared type accepted by every criterion that references this item. |
| `media_type` | String | Describes the referenced artifact. |
| `locator` | `EvidenceLocator` | Restricted relative path or opaque artifact identifier. |
| `sha256` | Lowercase SHA-256 | Digest of the referenced bytes. |
| `producer` | String | Gate, workflow, or evaluator that produced it. |
| `produced_at` | UTC timestamp | ISO-8601 instant with an explicit zero offset. |
| `source_revision` | Full Git revision | Must match the candidate. |
| `candidate_digest` | OCI SHA-256 digest | Must match the candidate. |
| `contract_sha256` | Lowercase SHA-256 | Must match the manifest contract. |

The evidence map key is the reference's unique identifier. One evidence reference may support
multiple criteria only when each criterion's accepted evidence-type list contains the reference's
declared `evidence_type`; its metadata appears exactly once. Every catalog entry must be referenced
by the qualification-record field or at least one criterion; dangling entries are refused.

## Evidence Locator

| Field | Type | Rules |
|---|---|---|
| `kind` | Enum | `relative-path` or `artifact-id`. |
| `value` | String | Interpreted according to `kind`; never rendered in validation diagnostics. |

A relative path uses normalized POSIX segments, is repository- or bundle-relative, and contains no
empty, `.` or `..` segment. It may not start with `/`, contain `\\`, or use URI schemes, user
information, query strings, or fragments. An artifact ID is 1–128 ASCII letters, digits, periods,
underscores, or hyphens, begins with an alphanumeric character, and contains no colon. These rules
exclude signed URLs, bearer tokens, traversal, host paths, and ambiguous platform-specific paths.

## Evaluator Approval

| Field | Type | Rules |
|---|---|---|
| `identity` | String | One non-empty, bounded printable ASCII line identifying a human or organization. |
| `independent_from_implementation` | Boolean | Must be `true` for a passing decision. |
| `executed_qualified_journey` | Boolean | Must be `true` for a passing decision. |
| `approved` | Boolean | Must be `true` for a passing decision. |
| `approved_at` | UTC timestamp | Explicit zero-offset ISO-8601 instant, not earlier than evidence production. |

## Decision invariants

A manifest is structurally valid only when all identity and accounting rules hold. Its decision is
consistent when:

- `pass`: every selected-contract criterion appears once, every result is `pass`, every result has
  evidence, every evidence identifier resolves, every referenced evidence type is accepted by its
  criterion, all evidence identities match, no catalog entry is dangling, and the evaluator fields
  required above are true;
- `fail`: every selected-contract criterion still appears once, and at least one result is `fail` or
  evaluator approval is false.

There is no transition that changes an existing manifest. Requalification creates a new manifest:

```text
candidate + selected contract + evidence
                  |
                  v
          fail manifest (immutable)
                  |
       new evidence or new contract
                  |
                  v
          new manifest (pass or fail)
```
