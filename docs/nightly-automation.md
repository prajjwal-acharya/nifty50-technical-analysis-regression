# Phase 4: scheduled private EOD runner and gated publisher

The workflow in the **private** `nifty50-live-state` repository runs at 00:17
and 06:17 Asia/Kolkata every day. The first run is just after the requested
midnight EOD point. The second is a backup before the regular 09:00 NSE open.
Daily scheduling also catches announced special weekend sessions. GitHub may
delay or occasionally drop a scheduled run; the updater scans every missing
exchange session on the next successful run, rather than advancing over a gap.

The workflow checks out its own private state with its repository-scoped
`GITHUB_TOKEN`, then checks out a **pinned commit** of the public pipeline. It
verifies the audited baseline, runs the idempotent Phase 2 updater, and commits
only new files under `sessions/`, `forecasts/`, `reviews/`, and `outcomes/`.
Missing due reports cause a failed run and are retried by the backup schedule
or next run. A no-change run creates no commit. GitHub's workflow results and
job summary provide success/failure reporting.

## Separate authorization gates

The workflow is installed, but market-data collection is **paused** unless
the private repository variable `NSE_AUTOMATION_AUTHORIZED` is exactly `true`.
NSE's [terms of use](https://www.nseindia.com/static/nse-terms-of-use)
prohibit systematic automated collection from its website without permission.
An existing manual download or the free GitHub Actions allowance does not
grant that permission. Keep the variable off until the permitted acquisition
method is documented and the runner uses that method.

Public display is a second, independent gate. It requires
`NSE_PUBLICATION_AUTHORIZED=true` **and** a `PUBLIC_REPO_WRITE_TOKEN` secret
limited to Contents write on the public code repository. The workflow then
builds the allowlisted JSON projection with
`scripts/build_pages_snapshot.py`, commits only `site/data/latest.json`, and
lets the public Pages workflow deploy it. NSE's
[data sharing policy](https://www.nseindia.com/static/market-data/nse-data-policy)
restricts redistribution and display of EOD prices. Do not enable this gate
without permission covering the displayed closes and derived forecasts.

The JSON URL is already the dashboard's endpoint:
`https://prajjwal-acharya.github.io/nifty50-technical-analysis-regression/data/latest.json`.
A Hugging Face Space is not needed for a once-daily precomputed snapshot. The
committed endpoint currently returns an unavailable marker with no stock data.

## Verification

The private workflow can be dispatched manually while both gates are off. Its
permission-gate job should succeed with the summary “Ingestion paused” and its
ingest job should be skipped; it must make no NSE request. A local run of the
pipeline against the private store remains available through
`python -m scripts.run_live_eod --state-dir ../nifty50-live-state` when the
data source is authorized. The runner never refits the pinned model.

The current calendar covers 2026 only. Before 2027 or an exceptional calendar
change, add and review the next NSE equity calendar, update the pinned public
pipeline commit in the private workflow, and verify strict target-session
dates. The runner fails outside its reviewed calendar rather than guessing.
