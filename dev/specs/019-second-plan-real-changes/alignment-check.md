# Alignment check: spec against the brief

**Source**: the inline brief given to `/speckit-specify` on 2026-11-16 ("A second plan shows only
real changes, and the plan step is measured by part at tier M"), parts 1 to 3 and "Out of
scope". No URL.

**Verdict**: ⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | added | Part 1, tests | FR-023 | Per-adapter peer-order tests for NetBox, Nautobot and generic REST are not in the brief. They prove that a second plan does not change because a source returns peers in another order, which serves "only real changes". |
| Minor | added | Part 1, "zero operations" | FR-027 | A documentation rule for an empty plan with a non-empty plan report. It explains the brief's own rule to a reviewer. |
| Minor | added | Part 2, benchmark records | Data model, benchmark record | The record also holds the counts of the plan report (`left_out`, `kept`, `problems`), needed to handle a plan with records left out (FR-029). |
| Minor | changed | Part 1, "starting with the `default` IP namespace" | Assumptions | If Infrahub exposes no mark on every node it creates, the rule covers the IP namespace only, and the spec is amended if other built-in nodes are found. This keeps the brief's scope and states its limit. |
| Minor | changed | Part 3, "If a measurement names the slow part, fix it" | FR-035, FR-036 | The spec adds what happens when no single part is slow, or when the fix is larger than this feature: an issue tracks it. |

No requirement, goal or non-goal of the brief is missing, and no acceptance criterion is
softened. Every item of "Out of scope" is in the spec's "Out of Scope".

## Action

Proceed. No remediation pass.
