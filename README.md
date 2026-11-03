# lux-rental-monitor
Lux Rental Monitor

To test notifications:

1. Subscribe to your topic on `ntfy.sh` in the ntfy iPhone app.
2. In this repository's **Settings → Secrets and variables → Actions**, add a
   repository secret named `NTFY_TOPIC` containing only the topic name.
3. Open **Actions → Test iPhone Notification → Run workflow**.

## Rental monitoring

The **Monitor rentals** workflow checks atHome and Immotop every 10 minutes
(at minutes 00, 10, 20, 30, 40 and 50 UTC). It can also be started manually from Actions.
Scheduled runs start after this workflow is pushed to the default branch;
GitHub may delay scheduled runs.

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
development. Immotop returned HTTP 403, so its live parser remains unverified.
Parser tests use small representative HTML examples, including the atHome
structured-data layout observed on the live page.

For local parser checks (without sending notifications):

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```
