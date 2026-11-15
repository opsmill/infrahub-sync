# 32. The run, configuration and version lists page with a cursor and read only the product store

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/010-run-discovery-api/research.md` (R1, R2, R4, R5, R6, R8)

## Context

A team that runs Sync needs to find the runs of a configuration, the configurations that are
registered, and the versions of one configuration, without knowing a run ID in advance. Before
this decision:

- the Sync API had no list of runs;
- `GET /configs` and `GET /configs/{config_id}/versions` returned every row in one JSON array.
  They accepted `offset` and `limit` and ignored both;
- some values that a list must show or filter on were not stored where a list can read them. The
  destination branch of a run was only in its saved plan, in object storage. The account that
  registered a version, and its reason, were only on the stored receipt of the registration
  request, and that receipt held the version number only inside its stored response body. The
  name of a configuration was only inside its package content.

New runs are created while a person or a script pages through the list. A list that pages by
offset then shows a run twice or leaves one out.

## Decision

- **One page shape for the three lists.** `GET /runs`, `GET /configs` and
  `GET /configs/{config_id}/versions` return `{"items": [...], "next_cursor": ...}`. `limit` is
  from 1 to 200 and defaults to 50. `next_cursor` is `null` on the last page. A list returns no
  total count. `offset` is removed.
- **Each list has one fixed order, newest first, and pages by position in that order.**
    - Runs: `(started_at, run_id)` descending.
    - Configurations: `(created_at, config_id)` descending.
    - Versions: `registry_version` descending.

  The next page starts after the last row of the previous page. A run created after the first
  page is newer than every cursor, so it never appears in a later page, and no run that existed
  is left out.
- **The cursor is opaque and is valid only for the request it was issued for.** It holds the
  last row's position and a digest of the list's name and filters. The service refuses a cursor
  that does not decode, or that is sent to another list or with other filters, with
  `422 invalid-cursor`.
- **A list reads only the product store database.** It reads no saved plan and no other
  artifact. A value that a list shows or filters on is stored in a column when the row is
  written:
    - `product_runs.destination_branch`, written when the run is created;
    - `configuration_versions.registered_by` and `registration_reason`, written in the
      transaction that registers the version;
    - `configurations.name`, updated in the transaction that registers each version.
- **A run in a list has the same fields as the run itself, with two left empty.**
  `artifact_refs` and `prefect_executions` are always empty in a list. `GET /runs/{run_id}`
  returns them.
- **Rows written before a column existed keep an empty value.** The service does not fill
  `destination_branch`, `registered_by` or `registration_reason` from saved plans or stored
  responses. It fills `configurations.name` once, from the latest version's package, in the
  start-up that adds the column.

This record covers these three lists only. Other paged routes of the Sync API were designed
separately and do not use a cursor: `GET /runs/{run_id}/logs`, the plan report routes
(`/plan/problems`, `/plan/left-out`, `/plan/kept`) and `GET /events` page with `offset` and
`limit`. A new list of rows that are added while a client pages through them should follow this
record: the same page shape and limits, a fixed order with a unique last column, a cursor from
`infrahub_sync/service/paging.py`, and a column for every value it filters on or shows.

## Consequences

- A client that read the configuration lists as a bare array must read `items` and follow
  `next_cursor`. The service ignores `offset`, so a client that sends it reads the first page
  again.
- A client cannot request a page by its number, and a list does not say how many rows exist.
- Runs created before `destination_branch` existed show no branch. Versions registered before
  `registered_by` existed show no registering account and no reason.
- A cursor cannot be reused after the filters change. The client starts again from the first
  page.
- Two configurations can have the same name. `GET /configs?name=` returns every match.
- Runs stored by a service whose clock was not UTC, before run start times were compared as UTC
  text, can appear out of order.
- Each new filter needs an index that matches its order. The store creates the list indexes at
  start-up.

## Alternatives considered

- **Offset and limit.** Rejected: rows move between pages when new runs arrive, so a run is
  returned twice or missed.
- **A cursor on `run_id` alone.** Rejected: a run ID holds its start time only to the minute,
  followed by random characters, so the order within a minute would not be newest first.
- **Read the destination branch from each saved plan while listing.** Rejected: one object-store
  read for each row.
- **Read the registering account from the stored registration receipt at list time, or fill it
  once at start-up.** Rejected: both must parse the stored response body to find the version
  number, which is fragile.
- **A separate, smaller resource for a run in a list.** Rejected: a second model for the same
  fields.
- **Read the configuration name from the package content in the list query.** Rejected: JSON
  extraction differs between SQLite and PostgreSQL, and the filter could not use an index.
