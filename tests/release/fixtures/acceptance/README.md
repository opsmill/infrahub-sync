# MVP acceptance manifest fixtures

This directory contains the shared JSON corpus for acceptance-manifest schema and runtime
validation.

## Corpus layout

- `valid/` contains one stable, fully passing manifest. Add another valid fixture only when it
  exercises a materially different supported shape.
- `invalid/` contains one focused fixture per refusal family. Derive each file from the passing
  manifest and introduce exactly one intentional violation so its expected refusal is unambiguous.
- Name fixtures for the behavior they demonstrate, using lowercase kebab-case names ending in
  `.json`.

## Fixture rules

- Use deterministic synthetic revisions, digests, timestamps, evaluator identities, producers,
  and evidence locators. Never include credentials, signed URLs, host paths, or real customer data.
- Keep JSON formatted with two-space indentation, a trailing newline, and stable field ordering.
- Keep evidence bodies out of the corpus. Fixtures contain bounded evidence metadata only.
- Ensure every invalid fixture remains valid JSON. It may violate the published JSON Schema or a
  runtime semantic invariant, but should not combine both unless that refusal family requires it.
- When the normative contract changes, update the contract version, exact-byte SHA-256 digest, and
  criterion accounting together. Do not rewrite a fixture merely to hide a validator regression.
- Tests must enumerate the corpus and associate each invalid filename with its expected safe refusal
  family; diagnostics must not echo locator values, notes, or other untrusted fixture content.
