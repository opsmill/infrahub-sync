# Quickstart: check the schedule pages

These steps check the pages against the development stack, with the NetBox demo data and the
`from-netbox` package. Run them from the repository root.

## Prerequisites

1. Start NetBox with the `demo` dataset, the development stack with a local Infrahub, the schema
   library and the `from-netbox` package, as
   [The `from-netbox` example check](../../../docs/docs/development/guidelines/testing-tiers.md#the-from-netbox-example-check)
   describes (steps 1 to 4).
2. Create a second account that is not an administrator:

   ```bash
   export INFRAHUB_SYNC_API_URL="http://127.0.0.1:8001"
   export INFRAHUB_SYNC_API_TOKEN="infrahub-sync-dev-token"
   uv run infrahub-sync accounts create operator --reason "check the schedule pages"
   ```

   The command asks twice for the new account's password.

3. Run the web interface with hot reload: `uv run invoke dev.frontend`. It prints the address.
   Sign in as `admin` with the password `infrahub-sync-dev-password` (the development default).

## Automated checks

```bash
uv run pytest tests/service/test_schedule_next_starts.py tests/service/test_events_route.py
cd frontend
pnpm check
pnpm typecheck
pnpm test
pnpm build
```

All commands exit 0.

## Scenarios

| # | Steps | Expected |
| --- | --- | --- |
| 1 | Open "Schedules" with no schedule. | "No schedule exists." and "Create schedule". |
| 2 | Open the `from-netbox` configuration page. | "No schedule" and "Create schedule". |
| 3 | Create a schedule from the configuration page with the cron expression `0 2 * *`. | The form keeps the values and shows the Sync API message under the cadence. |
| 4 | Correct it to `*/5 * * * *` and save. Press save twice quickly. | The schedule page opens with a next start. Only one schedule exists. |
| 5 | Open "Create schedule" again for the same configuration and save. | The refusal names the existing schedule. |
| 5a | In the create form, choose "Every day" at 02:00, time zone `Europe/Paris`. | The cron expression `0 2 * * *` is shown, with "Every day at 02:00" and five start times at 02:00 Paris time. |
| 5b | Choose "Every week", Monday and Thursday at 06:30. | `30 6 * * 1,4` and the next five Mondays and Thursdays. |
| 5b2 | Choose "Every N minutes" with 15, then "Every N hours" with 4 at minute 10. | `*/15 * * * *`, then `10 */4 * * *`, each with its preview. |
| 5b3 | Open the create form with a browser set to `Europe/Paris`. | The time zone is `Europe/Paris`, with the notice that the CLI fills in UTC. |
| 5c | Choose "Custom" and type `*/15 9-17 * * 1-5`. | Each field is labelled; within a second of the last key press the preview shows five starts. |
| 5d | Type `0 0 29 2 *`. Then type `0 0 30 2 *`. | One or two starts on 29 February and "No further start in the next 5 years."; then the Sync API's message "has no start time" under the field. |
| 5e | Type `61 * * * *`. | The Sync API's message under the field, before saving. The preview records no event. |
| 5f | Type `30 2 28 3 *` with the time zone `Europe/Paris`. 02:30 on 28 March 2027 falls in the hour that the daylight-saving change removes. | The first start shown is the one Prefect computes for that date. Save; the schedule's next start equals the first preview start. |
| 6 | Wait for the schedule to start a run. Open the runs list. | The run has the "Scheduled" mark, linking to the schedule. |
| 7 | Open the run. | "Started by schedule <name>, scheduled for <time>". |
| 8 | On the schedule page, open "View all runs of this schedule". | The runs list shows only that schedule's runs, with the filter shown. |
| 9 | Pause the schedule with a reason, then resume it. | "Paused" with no next start, then "Active" with a next start. |
| 10 | Change the cron expression to `0 3 * * *` and the time zone. | The page shows the new values and the new next start. |
| 11 | Open the change dialog and save without changing anything. | "Nothing changed"; no request is sent. |
| 11a | Open the change dialog. In a terminal, run `uv run infrahub-sync schedules change <schedule> --cron "0 4 * * *"`. Wait 10 seconds, change only the time zone in the dialog and save. | The dialog states that the schedule changed since it opened. After the save, the cron expression is `0 4 * * *` and the time zone is the new one. |
| 12 | Sign in as `operator` in a private window and open the schedule. | Change, pause, resume and delete are unavailable with the reason; "Take over" is not shown. |
| 13 | As `operator`, create a schedule for another configuration, then, as `admin`, delete the `operator` account. Wait for the next start. | The schedule is stopped with `owner-not-found`, the owner shows `Deleted account <short identifier>`, and the page states the action it needs. |
| 14 | As `admin`, take the schedule over. | The confirmation states that next runs record `admin`. The schedule is active, owned by `admin`. |
| 15 | Delete a schedule. | The confirmation states that a run in progress is not cancelled and that an open review branch stays. The state is "Deleted"; only "View all runs" remains. |
| 16 | In the list, filter by state "Deleted". | The deleted schedule is listed. |
| 17 | Open `/schedules/does-not-exist`. | "Schedule not found" with a link to the list. |
| 18 | On an active schedule's page, read "Next starts". | Five start times, the first equal to the next start in the header. |
| 19 | Start a manual run of the scheduled configuration just before a scheduled start, so the start is skipped. Open the schedule page. | "History" lists "Start skipped: a run of this configuration was in progress", linking to the manual run, above the earlier pause and resume events with their reasons and accounts. |
| 20 | Turn on "Show all events". | Run events ("Run started", "Run wrote its changes") and requests repeated with the same retry key appear between the schedule events. |
| 21 | With more than 50 events, choose "Load older events" while a new event is recorded. | Older events are added below; no event is listed twice; the new event appears at the top. |

Scenario 13 needs a second configuration. Register a copy of the `from-netbox` package under
another name, or skip the scenario and check the stop panel by reading the page of a schedule in
that state.

## Clean up

```bash
uv run invoke dev.destroy
uv run invoke netbox.destroy
```
