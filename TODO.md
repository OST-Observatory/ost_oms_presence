# TODO

## Features

- [ ] Detect the connected camera.
- [ ] Get the coordinates from the mount while it is running:
  - check whether the coordinates change
  - get and show an image of the observed region

## Data protection / retention

- [x] Observing session log (`SESSION_LOG_FILE`): **kept permanently on purpose** (decided 2026-09).
  Observers have consented to the log in its current form; it records who made which
  observation so they can be credited when the data is reused (e.g. in papers). Legal basis in
  the central privacy policy (`#status` / `#en-status`): consent, withdrawal removes the name
  from the entries concerned (by hand in `SESSION_LOG_FILE`).
