# Validation: Webex source adapter

## Scope

The implementation adds only infrahub_sync/adapters/webex.py and infrahub_sync/adapters/webex_client.py. Existing core code, adapters and CLI behavior are unchanged. There is one meeting-room example, with destination site IDs prepared separately. Empty destination site identifiers remain a known limitation and are not fixed by this adapter.

## Fixture evidence

- HTTP: GET-only data, OAuth-only POST, service refresh/expiry/401, safe errors, bounded retries, pagination through empty pages, external-link rejection and cycles.
- Mapping: alternate keys, singleton/list extraction, parent path/query expansion, absent parent fields and 404, details, query overrides and native dict references.
- Meeting-room example: generated source/destination models, filtering, room-number derivation, correct existing parent node IDs, no site writes, preserved old names and a converged second diff with populated destination site IDs.
- Error rendering: new-client helpers prevent credential-bearing request and token-parsing frames from appearing in displayed traceback chains, including local-variable display; no global CLI setting changes.

## Checks

| Check | Observed result |
| --- | --- |
| `uv sync --extra dev` | Exit 0; no dependency or lockfile changes. |
| `uv run invoke format` | Exit 0; Ruff and rumdl pass. |
| `uv run invoke linter.lint-ruff` | Exit 0; all Python files formatted and lint-clean. |
| `uv run invoke linter.lint-yaml` | Exit 0. |
| `uv run ty check .` | Exit 0; zero errors and three existing unused-ignore warnings in unrelated optional-adapter tests. |
| Pylint on both new adapter modules and all three Webex test modules | Exit 0; JSON output `[]` (zero diagnostics). |
| `uv run pytest -q` | Exit 0; 260 passed, 3 skipped, 20 warnings. |
| CLI help and project listing | Exit 0; exactly one Webex project is listed. |
| Example generation against fixture destination schema | Passes as part of the Webex example tests; no servers contacted. |
| `uv run invoke docs.docusaurus` | Exit 0; static files generated, with existing tooling warnings. |
| `uv run invoke docs.rumdl` | Exit 0; no issues. |
| `git diff --check` | Exit 0. |

The full `uv run invoke lint` command still exits 30 on the same 56 existing Pylint diagnostics in the unchanged repository code. The previously approved baseline exception remains recorded; all new files pass Pylint with zero diagnostics and no suppressions or core cleanup were added to hide baseline findings. CI's Pylint step remains commented out. The required Ruff, yamllint, type, test and documentation checks pass independently.

The generated CLI reference is restored to 53290877; it is not regenerated for an adapter-only change with no CLI changes.

## Scope and privacy gates

The existing Infrahub adapter, CLI, shared mapping module and generated CLI reference were compared byte for byte with 53290877 and match. The core diff excluding the two new adapter files is empty. All changed paths were checked against the allowed scope. The final files, fixtures and proposed commit text contain only invented data: no customer identities, domains, addresses, site codes or real Webex IDs, and no credentials. The site-enrichment example and its notes convention are absent. Retain only the managed Spec Kit plan-reference line in CLAUDE.md. The final history must contain one commit directly on 53290877, with the explicitly requested author/committer and no extra trailers.
