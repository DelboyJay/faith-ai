# Frontend Risk Rules

Apply this file whenever a task touches the web UI, browser-facing JavaScript,
CSS, layout behaviour, panel behaviour, or any user-visible frontend flow.

- Treat frontend code as high risk for regressions.
- Do not change existing frontend code unless the current task genuinely
  requires it.
- If a required frontend change may alter the existing UI or UX look and feel,
  inform the user and seek approval first unless no other implementation path
  exists.
- If a UI option is greyed out, disabled, or otherwise not selectable because
  of its current state, it must always expose a tooltip that explains why it is
  unavailable and how the user can restore or enable it.
- Keep changes focused on the exact user-facing behaviour being worked on. Do
  not make surrounding cosmetic or layout tweaks “while you are there”.

