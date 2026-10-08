# Research: MVP Acceptance Contract

## Decision 1: Keep the human-readable contract normative

**Decision:** Publish the authoritative contract as
`docs/docs/develop/knowledge/mvp-acceptance-contract.md`. Give every criterion a stable identifier,
owning specification, validation method, and accepted evidence types.

**Rationale:** Release owners must be able to review the complete boundary without Jira or a tool.
Keeping it in the published, version-controlled knowledge tree avoids two human-readable copies.

**Alternatives considered:** Treat the feature spec as the permanent contract; use Jira as the
contract; make JSON normative. The active spec will eventually be archived, Jira is not repository
evidence, and a JSON-only contract contradicts the clarification that the human document is
normative.

## Decision 2: Identify an exact contract by version and content digest

**Decision:** The contract declares a monotonically increasing contract version. Validation computes
the SHA-256 of the exact contract bytes and requires the manifest to carry both the version and
digest. The contract does not embed the Git revision of the commit containing itself.

**Rationale:** A label alone can be accidentally reused, while a digest alone is unreadable in
review. Requiring both makes the contract revision exact and understandable. Validation against the
selected checked-out contract proves byte-level consistency without a network lookup; spec 014
separately proves approval provenance and applies the latest-approved-contract rule.

**Alternatives considered:** Semantic version alone; an `approved_revision` embedded in the
contract; Git revision alone; latest contract fetched from a remote branch. The embedded revision is
self-referential because changing it creates another commit. The others fail to bind exact content
or make offline validation depend on mutable external state.

## Decision 3: Add a final acceptance manifest beside the producer qualification record

**Decision:** Preserve `.release/qualification.json` as the producer-side candidate record. Define a
separate acceptance manifest that references its digest and records criterion results, evidence, the
overall decision, and evaluator approval.

**Rationale:** The existing record is written before an independent evaluator receives the tester
packet. Rewriting it afterward would destroy its immutability and mix producer claims with evaluator
acceptance. A referencing manifest keeps the chain explicit.

**Alternatives considered:** Expand and mutate `qualification.json`; replace the existing record;
store approval only as prose. All three weaken provenance or break the current candidate workflow.

## Decision 4: Use strict Pydantic models and generate the JSON Schema

**Decision:** Implement strict Pydantic v2 domain models and contract parsing in
`tasks/acceptance.py`, generate the committed JSON Schema from those models, and expose a thin
`release.validate-acceptance` Invoke task.

**Rationale:** Pydantic is already a base dependency, supplies typed strict-boundary validation, and
can generate the published schema from the same model used at runtime. Tests can assert the committed
schema and one valid/invalid fixture corpus against that single source.

**Alternatives considered:** Add `jsonschema`; hand-maintain standard-library field checks and a
separate schema; validate only in shell. These add a dependency, permit contract drift, or are
difficult to type and test comprehensively.

## Decision 5: Account for criteria exactly and forbid waiver states

**Decision:** The normative Markdown contains one rigid six-column criterion table. A fail-closed
parser derives the known criterion set directly from it. A manifest contains exactly one result for
every identifier in the selected contract.
Unknown and duplicate identifiers are errors. Results are `pass` or `fail`; there is no skipped,
waived, or not-applicable state. An overall `pass` requires every criterion to pass with at least one
matching evidence reference.

**Rationale:** This directly implements the clarified no-waiver policy and makes missing evidence a
failure rather than an omission that a reviewer might overlook.

**Alternatives considered:** Maintain criterion identifiers in Python or a JSON sidecar; allow
time-bounded waivers; allow optional criteria; infer absent criteria as failures. A second catalog
can drift from the normative document, while explicit complete accounting produces clearer
diagnostics and deterministic machine checks.

## Decision 6: Bind evidence metadata, not evidence bodies

**Decision:** A top-level evidence catalog stores each reference once. Criterion results refer to
catalog identifiers. References carry a stable identifier, declared evidence type, media type,
restricted locator, SHA-256, producer, production time, source revision, candidate digest, and
contract digest. One reference may support multiple criteria only when every referencing criterion
accepts its declared evidence type. A locator is either a normalized relative POSIX path or a
bounded opaque artifact ID; URI syntax, traversal, absolute paths, and backslashes are refused. The
validator checks metadata consistency and never fetches or renders the evidence body.

**Rationale:** Binding metadata makes evidence traceable while keeping validation offline and
preventing large or secret-bearing content from entering diagnostics. Artifact existence and byte
retrieval remain spec 014 responsibilities.

**Alternatives considered:** Repeat full evidence objects beneath criteria; embed evidence bodies in
the manifest; fetch every reference during schema validation; allow arbitrary locations; store
locations without digests. These create conflicting copies, expand the trust boundary, permit secret
retention, or fail to identify immutable evidence.

## Decision 7: Record evaluator independence as an explicit attestation

**Decision:** A passing manifest requires an evaluator identifier, approval timestamp, confirmation
that the evaluator did not implement the candidate, and confirmation that they executed the
qualified journey. Cryptographic signing is not required for MVP.

**Rationale:** This captures the clarified independence rule in a testable form while avoiding a new
identity/signing system. Spec 014 can decide how the release workflow authenticates and retains the
attestation.

**Alternatives considered:** Producer approval; two evaluators; mandatory cryptographic signature.
The first is not independent, the second exceeds the clarified MVP rule, and the third requires an
unapproved trust infrastructure.

## Decision 8: Separate contract consistency from release eligibility

**Decision:** Default validation uses the checked-out normative contract and answers whether the
manifest is consistent with those selected bytes. An optional explicit contract path validates a
manifest against retained contract bytes. Neither mode claims the selected bytes are approved or
that the candidate is release-eligible. Spec 014 supplies approved-contract provenance and owns the
release-eligibility decision.

**Rationale:** A byte-level validator can prove consistency but cannot infer organizational approval
from repository position. Separating those claims keeps outcomes honest while retained decisions
remain independently inspectable rather than becoming unreadable stale files.

**Alternatives considered:** Validate only against the current contract; fetch old contracts from a
remote Git server; accept the manifest's own criterion list as its contract. These prevent offline
historical review or allow an untrusted manifest to define its own obligations.
