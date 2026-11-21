# Design decision: the schedule form

Decided on 2026-11-20 by the maintainer, from a prototype in three rounds of the form that creates
and changes a schedule. The versions were built on the configurations and schedules of the
development stack, with a long configuration name, an invalid cron expression, a cadence that
never starts, values the form's rules refuse, and a refusal of the Sync API on save. The change
form opened on a schedule that runs version 2 while version 3 is registered.

## Chosen: Wide dialog

The form opens as a large dialog over the page it was opened from: the schedules list, a schedule
page, or a configuration page, where the configuration is fixed.

- **From the `lg` breakpoint:** the dialog is up to 1100 px wide and 90% of the window high.
  - The form is on the left, in three parts: "Configuration", "Cadence" and "Writing", each with
    a heading and one sentence. The reason follows them. The form scrolls on its own.
  - On the right, "Next starts" lists the next 5 starts of the cadence entered, in the look of
    the schedule page's timeline: above a "Now" marker, the soonest nearest it. When the cadence
    has no start, it states why, with the Sync API's message.
  - The footer, always visible, holds Cancel and the save button.
- **At 390 px:** the dialog fills the screen.
  - Above the footer, one line shows the next start, for example "Next start Mon, Nov 23, 06:30,
    in 2 days", or why there is none. It opens to the full list of next starts.
- **The footer count:** when a schedule is changed, the footer states how many settings changed,
  for example "2 settings changed", left of Cancel. It shows nothing when creating a schedule or
  when no setting changed.
- **What a change sends:** saving sends only the settings that changed, as before. The count and
  the request are computed by the same rule (`changedFields`).
- **Problems before a save:** the save button stays enabled. Pressing it with a missing or refused
  value lists the problems above the footer, shows each under its field and moves to the first.
- **Kept from the previous form:** every field and rule, the refusals under their field, the
  notice when someone else changed the schedule after the form opened, and the safe retry after
  a network failure.

## Rejected

- **Current:** a narrow dialog where the next starts sat in the middle of a long form, and the save
  button was below the fold.
- **Steps:** four steps and four clicks to save; reaching a field in a later step took several
  clicks.
- **Full page:** the form left the page it was opened from.
- **Sentence:** long names wrapped badly inside the sentence, and the less common settings were
  hidden behind "More settings".
- **Side sheet:** one 640 px column made the form long, and the full list of next starts was
  behind a link.
- **In place:** the form column on the schedule page was narrow, and on a phone the save button sat
  at the top of the page and again below the form.
- **A summary card above the next starts:** the name, the schedule in sentences and the list of
  changed settings repeated the form, so the right column holds only the next starts.
