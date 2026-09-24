# TODO

## Features

- [ ] Detect the connected camera.
- [ ] Get the coordinates from the mount while it is running:
  - check whether the coordinates change
  - get and show an image of the observed region

## Data protection / retention

- [ ] The observing session log (`SESSION_LOG_FILE`) has no automatic deletion.
  Define a retention period (e.g. delete or anonymise entries older than N months),
  implement it (cleaner loop or cron job), and update the dashboard section
  (`#status` / `#en-status`) of the central privacy policy
  (ost_landing_page, `static/datenschutz.html`) and the table in `README.md`
  ("Personal data processed by the login") to match.
