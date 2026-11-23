# Design decision: the schedule page

Decided on 2026-11-20 by the maintainer, from a prototype with five versions of
`/schedules/$scheduleId`. The versions were built on test schedules in every state: active,
failing, stopped for each reason, paused, deleted, and one with long names.

## Chosen: Status first+

The base is Status first, with the side column of Timeline and the settings moved to the left.

- **Header, above the columns:**
  - the name, the state, the configuration and its version, and the owner;
  - the actions the account may use (Change, Pause or Resume, Take over, Delete), as quiet
    buttons.
- **Left column:**
  - "Settings", each value under its label:
    - the version, with "Version N is registered" when a newer one exists;
    - the cadence in words, with its cron expression;
    - the time zone and the write target;
    - "Records left out", with who set it and when;
    - "When a run is in progress" and "Start late by at most".
  - "Review branch", only when one is open: the branch, the proposed change and the run that
    opened it.
  - One line with the creation and update times.
- **Middle column:**
  - A status card states the situation in one sentence, with the next step as the main button.
    - Examples: "Starts in 9 hours, Saturday 02:00 Paris time." or the stop reason.
    - Possible steps: Take over, Change the write target, Resume, Change to version N.
    - Its tint matches the schedules list: red for stopped, amber for failing, neutral otherwise.
  - "History" follows, with "Show all events" and "Load older events".
- **Right column:** a timeline.
  - The next 5 starts are listed above a "Now" marker, the soonest nearest it.
  - When the schedule does not start runs, this part reads "No start while this schedule is
    stopped" (or paused).
  - The last 5 runs are listed below the marker, each with its state, time and "View run".
  - An "All runs" link ends the column.
  - Times are in the viewer's time zone, with the schedule's on hover.
- **Columns:** three from the `lg` breakpoint. Below it, the order is status, timeline, history,
  settings, review branch, then record.

## Rejected

- **The current page:** the state, its reason and the action were spread over the header, the
  notices and "Next start and last run", across eight stacked sections.
- **Status first as built:** the settings sat above the history in the main column, so reaching
  the history took a long scroll. The settings moved to their own column.
- **Timeline as the whole page:** the timeline is useful for next starts and recent runs, and
  became the side column. As the whole page, it had no "Show all events".
- **Tabs:**
  - the history and the settings each took a click to reach;
  - the Overview of a healthy schedule said less than the other versions;
  - per-setting "Change" links were not kept.
