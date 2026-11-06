# Draft PR title

feat: add a Webex source adapter

## Draft PR description

Add a built-in Webex source for configurable JSON GET endpoints, Link pagination, query parameters, parent-dependent resources, per-record details and existing filters/transforms. Fixed bearer tokens and Service App refresh use environment variables; the only POST is OAuth refresh at `/v1/access_token`. All implementation changes are confined to the two new Webex adapter files; existing adapters, shared mapping/filter behavior and CLI commands are unchanged.

One example maps meetingRoom workspaces into LocationMeetingRoom using the complete displayName as the identifier, its final segment as room_number and locationId as the existing site's Webex ID. LocationSite models are loaded only as references and have no diff operations. Destination site IDs must be prepared in a separate workflow; no site-enrichment config or matching convention is included.

Validation is recorded in [validation.md](validation.md). No dependencies are added. The release-intent label when opened is `changes/minor`; the news fragment is `changelog/+webex-adapter.added.md`.

Known limitations and follow-ups:

- Existing Infrahub loading of sites without webex_location_id can produce empty or duplicate identities. This remains unresolved; the example is validated with populated, unique destination site IDs. The existing continue-on-error option does not reliably skip optional empty identifiers, and diff has no such flag.
- A room rename creates a new room and retains the old room; removed rooms are retained under the example policy. Rename/removal reconciliation and cross-side parent preflight are follow-ups.
- Add the destination schema attribute and write Webex IDs onto matching existing sites separately. Schema changes, site enrichment, other workspace types, additional room attributes and devices are outside scope.
- Service App credentials are retained only during the process; maintaining valid/rotated credentials between scheduled runs belongs to an external secret manager.
- Live validation remains with the engineer; all automated checks use invented fixtures. No push or PR is part of this work.
