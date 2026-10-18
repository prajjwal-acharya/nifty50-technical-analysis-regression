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
job summary provide success/failure reporting. After the private commit, the
workflow audits each issued forecast against its original Git addition and
verified outcomes. The summary reports equal-stock rupee MAE beside the
unchanged-close baseline only after target closes have been ingested.

## Separate authorization gates

Market-data collection runs only when the private repository variable
`NSE_AUTOMATION_AUTHORIZED` is exactly `true`.
NSE's [terms of use](https://www.nseindia.com/static/nse-terms-of-use)
prohibit systematic automated collection from its website without permission.
An existing manual download or the free GitHub Actions allowance does not
grant that permission. The repository owner confirmed permission for automated
collection before enabling the variable. If the permission limits the source
or method, configure the runner to match those terms before its next run.

Public display is a second, independent gate. It requires
`NSE_PUBLICATION_AUTHORIZED=true` **and** a `PUBLIC_REPO_DEPLOY_KEY` secret
whose public half is a write-enabled deploy key on the public code repository.
The key grants access to that repository only. The workflow then
builds the allowlisted JSON projection with
`scripts/build_pages_snapshot.py`, commits only `site/data/latest.json`, and
lets the public Pages workflow deploy it. NSE's
[data sharing policy](https://www.nseindia.com/static/market-data/nse-data-policy)
restricts redistribution and display of EOD prices. The repository owner
confirmed permission for public display of closes and derived forecasts before
enabling the variable.

The JSON URL is already the dashboard's endpoint:
`https://prajjwal-acharya.github.io/nifty50-technical-analysis-regression/data/latest.json`.
A Hugging Face Space is not needed for a once-daily precomputed snapshot. The
endpoint returns the latest authorized snapshot after the first publication.

## Verification

The private workflow can be dispatched manually to verify ingestion and
publication. When both gates are off, its permission-gate job succeeds with
the summary “Ingestion paused” and its ingest job is skipped. A local run of the
pipeline against the private store remains available through
`python -m scripts.run_live_eod --state-dir ../nifty50-live-state` when the
data source is authorized. The runner never refits the pinned model.

The current calendar covers 2026 only. Before 2027 or an exceptional calendar
change, add and review the next NSE equity calendar, update the pinned public
pipeline commit in the private workflow, and verify strict target-session
dates. The runner fails outside its reviewed calendar rather than guessing.
The [Phase 5 replay and recovery guide](phase5-verification.md) records the
production verification and repeatable check command.
