# Change-to-case ledger

| Change | Red matrix case | Repair | Green evidence |
|---|---|---|---|
| Add configured standalone DB-003 projection | C01: configured standalone plan left no `ProductRun` or `plan-review`; initial run failed collection with `ModuleNotFoundError: infrahub_sync.product_store.standalone` | Add `execute_standalone`, create unfinished plan/sync records, publish the existing review shape, finish only after complete publication | `test_configured_standalone_plan_publishes_the_managed_product_contract` |
| Preserve D028 identity through reviewed apply | C02: standalone apply had no product record to extend | Require the original record in the same cache and finish it under the original run ID | `test_configured_standalone_apply_extends_the_planning_record` |
| Keep one confirmed-sync identity | C03: public composed sync had three core calls but no durable product owner | Pass the semantic operation through plan/verify/apply and defer completion until apply | `test_python_confirmed_sync_keeps_one_product_identity_across_all_stages` |
| Route both standalone interfaces through one adapter | C04: CLI and public Python had no product-cache configuration axis | Add optional absolute `product_cache_location` request field and matching CLI option; both call `execute_standalone` | `test_python_plan_request_projects_the_same_record_and_review_artifact`; `test_cli_plan_accepts_explicit_product_cache_configuration` |
| Prove managed artifact/record equivalence | C05: no executable comparison retained every DB-003 field | Add the lossless canonical oracle and compare complete plan records, references, result, and decoded review artifact | `test_managed_and_standalone_plan_records_and_artifacts_are_canonically_equal`; oracle mutation test |
| Retain lifecycle and secret safety | C06: standalone projection failure/restart behavior was unproved | Reuse DB-003 atomic/redaction boundaries; retain typed error class only and never persist exception text | restart and secret-sentinel conformance tests |

No production edit exists without a case above. Existing managed lifecycle ownership and
DB-005 routes were not refactored.
