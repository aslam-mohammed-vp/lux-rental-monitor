# lux-rental-monitor
Lux Rental Monitor

To test notifications:

1. Subscribe to your topic on `ntfy.sh` in the ntfy iPhone app.
2. In this repository's **Settings → Secrets and variables → Actions**, add a
   repository secret named `NTFY_TOPIC` containing only the topic name.
3. Open **Actions → Test iPhone Notification → Run workflow**.

## Rental monitoring

The **Monitor rentals** workflow checks atHome every 15 minutes
from **08:00 through the 20:00 slot**, **Monday–Saturday**, in
**Europe/Luxembourg** local time (daylight saving adjusts automatically).
Manual runs bypass the time window and Sunday exclusion.
Scheduled runs start after this workflow is pushed to the default branch;
GitHub may delay scheduled runs.

Configure the schedule without editing code in **Settings → Secrets and variables
→ Actions → Variables**. These repository variables become workflow environment
variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MONITOR_START` | `08:00` | First local check slot, inclusive |
| `MONITOR_END` | `20:00` | Last local check slot, inclusive |
| `MONITOR_TIMEZONE` | `Europe/Luxembourg` | IANA timezone |
| `MONITOR_INTERVAL_MINUTES` | `15` | Multiple of 15, from 15 to 1440, counted from the start time |
| `MONITOR_SOURCES` | `athome` | Comma-separated sources; use `athome,immotop` to retry Immotop |

Start/end times must use `HH:MM` on quarter-hour boundaries. Equal start/end times allow
just that slot. The last slot includes its following 14 minutes to tolerate
small runner delays. Delayed runs use the current local slot; GitHub does not
guarantee exact execution times or backfill missed checks.

GitHub cron cannot read environment variables. The cron itself requests runs
every 15 minutes from 08:00 to 19:45, plus 20:00, Monday–Saturday, in Luxembourg
time. Changing the variables can narrow this window or reduce frequency;
expanding it (including overnight hours) requires editing the cron as well.
No scheduled workflows are requested overnight or on Luxembourg Sundays.
GitHub can delay or drop scheduled runs, so this is not a guaranteed interval.
The gate also excludes Sundays in `MONITOR_TIMEZONE` if overridden.
Outside the configured slots, the actual
monitor job is skipped before dependencies are installed or websites checked.
Intervals below 15 minutes require changing the cron and schedule gate in code.

It alerts on rentals matching all these criteria:

- Luxembourg **country**, including towns outside Luxembourg City.
- Apartments or houses (including duplexes and penthouses), excluding studios
  and individual rooms.
- At least **40 m²** and monthly advertised rent at most **€1,800**, before charges.
- An explicitly listed parking space or garage. A mention of nearby public
  parking alone does not qualify.

Use the same `NTFY_TOPIC` repository secret as the test notification workflow.
Subscribe to that topic in the ntfy app. The workflow's GitHub token needs
permission to write repository contents so it can automatically create and
update the `rental-monitor-state` branch. Repository rules must permit these
writes. No additional personal access token is needed.

The first run notifies for all matching listings it discovers. Subsequent runs
skip listing URLs already successfully notified; prices changing on an already
notified listing do not cause another alert. History is kept in
`seen-rentals.json` on `rental-monitor-state`, separately from your source branch.
Do not delete that branch unless you want to reset notification history.
The two websites may advertise the same property with different URLs; these
are treated as separate listings. If notification delivery succeeds but saving
history fails, the next run may repeat that alert.

The monitor uses Chromium to render public pages and follows pagination up to
30 pages per search, starting with the newest listings. If more pages exist,
the run logs a warning and monitors only those first 30 pages. It applies the final filters to listing details rather
than relying only on search parameters. Unknown required property fields,
empty results, changed markup, or blocked access
make the run fail visibly in Actions. A failure on one site does not stop
the other site from being checked. Sites can block automated browsers, including
GitHub runners; live access and delivery need to be verified in Actions.
atHome search parameters and one detail page were verified with Chromium during
development. Immotop returned HTTP 403 on live runs, so it is disabled by
default with a visible warning. This does not restore Immotop coverage; use
its native saved-search alerts for that site. Opting in through `MONITOR_SOURCES`
retries its parser, and blocked access still makes the run fail visibly.
Parser tests use small representative HTML examples, including the atHome
structured-data layout observed on the live page.

For local parser checks (without sending notifications):

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```
