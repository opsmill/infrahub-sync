# Quickstart: Validate the MVP Acceptance Contract

This guide describes the validation track the implementation must make runnable. It does not
qualify a release by itself; specs 010–014 supply the evidence and final candidate workflow.

## Prerequisites

- Python 3.11–3.13 development environment:

  ```bash
  uv sync --extra dev --extra prefect --extra service
  ```

- A manifest prepared against the checked-out
  `docs/docs/develop/knowledge/mvp-acceptance-contract.md`.
- Evidence references that name one source revision and immutable candidate digest.

## Validate a manifest against the selected contract

```bash
uv run invoke release.validate-acceptance --manifest /path/to/acceptance.json
```

Expected result:

- exit status 0;
- selected contract version and digest reported;
- every selected-contract criterion accounted for exactly once;
- candidate identity and independent evaluator approval summarized;
- overall decision reported as `pass`;
- manifest consistency with the selected contract confirmed;
- no claim that the selected contract is approved or the candidate is release-eligible;
- no evidence body or credential-bearing value printed;
- an explicit notice that artifact availability and byte verification remain for spec 014.

## Validate a historical decision

When retained contract bytes no longer match the checked-out contract, provide them explicitly:

```bash
uv run invoke release.validate-acceptance \
  --manifest /path/to/historical-acceptance.json \
  --contract /path/to/retained-mvp-acceptance-contract.md
```

Expected result: the command reports consistency with the explicitly retained contract bytes but
does not claim that those bytes are approved or that the candidate is release-eligible. Spec 014
supplies approval provenance and owns the release-eligibility decision.

## Exercise fail-closed behavior

Make separate copies of the manifest and change one condition at a time:

1. remove one criterion result;
2. duplicate a criterion identifier;
3. replace the contract digest with another valid digest;
4. change one evidence reference to another candidate digest;
5. mark a criterion `fail` while keeping the overall decision `pass`;
6. remove all evidence from a passing criterion;
7. set evaluator independence or journey execution to `false`;
8. add an unknown field;
9. add an unreferenced evidence entry;
10. use a signed URL, absolute path, traversal path, backslash, query, or fragment as an evidence
    locator.
11. reuse an evidence ID for a criterion that does not accept the reference's declared
    `evidence_type`.

Each command must exit non-zero, name the refusal family without echoing arbitrary evidence content,
and give the next action defined in
[acceptance-validation.md](./contracts/acceptance-validation.md).

## Run focused tests

```bash
uv run pytest \
  tests/release/test_acceptance_contract.py \
  tests/release/test_acceptance_manifest.py \
  -q
```

## Run repository gates

```bash
uv run invoke format
uv run invoke lint
```

If implementation changes the published knowledge page, also run:

```bash
uv run invoke docs.generate
uv run invoke docs.docusaurus
```
