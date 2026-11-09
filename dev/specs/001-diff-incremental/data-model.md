# Data Model

No persisted formats change. A reusable run has status applied or dry-run and an existing cursors.json file. Run directories remain sorted by name. A sidecar may contain only some side/resource entries; missing entries use the current full-read behavior with a reason. CursorState and CursorTier are unchanged.
