# Design decision: the schedules list

Decided on 2026-11-20 by the maintainer, from a prototype with six versions of `/schedules`. The
versions were built on test data with long names, every state, every stop reason, a deleted owner
and newer versions.

## Chosen: Triage+

The base is Triage, which groups schedules by what needs a person. The side panel comes from
Agenda.

- The page opens with "Needs action": one card per stopped schedule, and per active schedule
  whose last runs failed.
  - Each card shows the reason in a full sentence (`stopReasonText`, or "N runs in a row failed.
    After 5, the schedule stops.").
  - Each card names the next step: "Take over", "Change the write target", "Resume" or "View the
    runs".
- "Active" lists the other active schedules by name. Each row shows:
  - the name, the configuration and its version, and the owner;
  - the cadence in words ("Every day at 02:00"), the time zone and "next in …".
- "Paused" lists the paused schedules with the reason given when they were paused.
- Tabs with counts: All, Needs action, Active, Paused, Deleted. A search field finds a schedule by
  its name or its configuration's name.
- A side panel, "Next starts", lists the next 8 starts.
  - It groups them under Today, Tomorrow, This week and Later, in the viewer's time zone.
  - Each line shows the time, the schedule and "in …"; hovering the time shows it in the
    schedule's time zone.
  - Further starts are counted ("3 more later").
  - From the `lg` breakpoint the panel stays in view while the page scrolls. Below it, the panel
    comes after the main list.
- A schedule behind its configuration's latest version reads "version 2 · version 3 available".

## Rejected

- **The current table:** a stopped schedule looks like any other row, and its stop reason is
  only on the schedule page.
- **Agenda as the whole page:** the next starts are useful, but a list ordered by time makes it
  hard to find one schedule by name. It became the side panel.
- **Coverage, one row per configuration:** the page would list configurations, not schedules.
- **Active schedules soonest first in the main list:** the side panel already shows that order,
  so the main list is sorted by name.
- **Optional parts shown in "Triage+ all" and not kept:**
  - the next start in the header line, which repeats the side panel;
  - a "No schedule" list of configurations in the side panel;
  - deleted schedules behind a "Show N deleted" link instead of a tab;
  - the wording "runs version 2 of 3" instead of "version 3 available".
