# Private live-data store and EOD updater

The public repository remains the source for code, processing decisions, and
the frozen release lock. The separate **private**
[`nifty50-live-state`](https://github.com/prajjwal-acharya/nifty50-live-state)
repository holds the audited data needed by a nightly runner. Its contents must
never be copied wholesale into the public GitHub Pages branch.

## Bootstrap

From the code repository, with Python 3.12 and `requirements.txt` installed:

```bash
python -m scripts.bootstrap_live_store \
  --state-dir ../nifty50-live-state \
  --model-run reports/generated/one_day/20261017T140407459569Z
```

The command requires a new directory. It verifies the release audit, the
stored input/output checksums, the 119,567-row/50-stock price coverage
through 2026-10-13, the model's release-lock hash and 26-feature order, and
the saved model checksum. It then copies the checked files and writes
`store_manifest.json` with SHA-256 hashes for each copy. Re-running against an
existing directory fails rather than replacing any state.

After cloning the private repository, check every pinned asset against its
recorded hash:

```bash
python -m scripts.verify_live_store --state-dir ../nifty50-live-state
```

## Layout and invariants

| Private path | Purpose |
| --- | --- |
| `baseline/` | Immutable audited price and feature tables, release audit, release lock, and as-of configuration. |
| `models/one_day/` | Immutable saved LightGBM model, model manifest, and historical predictions used for parity checks. |
| `sessions/YYYY/MM/` | Append-only stock rows for each NSE session (`.csv.gz`) and source provenance (`.json`). |
| `reviews/YYYY/MM/` | Official constituent and corporate-action checks captured at issue time. |
| `forecasts/YYYY/MM/` | Immutable, timestamped forecasts by origin session. |
| `outcomes/YYYY/MM/` | Actual close and error records, separate from issued forecasts. |
| `store_manifest.json` | Baseline date, basket date, model identity, and copied-file hashes. |

Session records use `(session_date, symbol, series)` as a unique key. Provenance
records both NSE report URLs and SHA-256 hashes, the validation time, basket
hash, and calendar hash. Forecast records retain `issued_at_utc`, the origin,
each symbol, horizon **1 or 5 NSE sessions**, target date, price, policy and
model identity. A later correction must be recorded as a new revision; the EOD
command never overwrites an issued forecast.

## Phase 2: run the EOD command

From the public code checkout, after cloning the private state beside it:

```bash
python -m scripts.run_live_eod --state-dir ../nifty50-live-state
```

The command checks every due session after the last stored date. At midnight
IST, it checks through the previous date; on a trading day it checks the
current date only after 17:00 IST. It downloads NSE's daily security report
and CM UDiFF bhavcopy, requires both, matches all 50 EQ rows and ISINs, and
checks OHLCV and previous-close continuity. It compares the current official
Nifty 50 constituent CSV with the pinned basket and queries NSE corporate
actions through the fifth target session. It stops on a known price-basis
action through the origin or a changed basket. For a known future price-basis
action, it abstains for that symbol and affected horizon.

The calendar in `config/nse_equity_calendar_2026.json` comes from NSE's
[equity holiday page](https://www.nseindia.com/resources/exchange-communication-holidays?apiversion=v2).
It includes the announced 8 November 2026 Muhurat session. Add a reviewed new
calendar before the year changes or if NSE announces an exceptional session or
holiday; the runner stops outside the configured year. A missing report also
stops ingestion, so a delayed publication cannot move horizon counts.

The command reconstructs features from the immutable baseline plus dated
sessions, checks a saved 12 October model prediction for exact compatibility,
and scores the latest close with the pinned 26-feature LightGBM. The fifth
session uses the adopted unchanged-close policy. It issues only while the
next target session has not opened (09:00 IST). Older sessions backfilled
after that cutoff receive **no retrospective forecast**. Outcomes are written
only after a target session is ingested and never alter issued forecasts.

For the initial 14–16 October backfill, the verified NSE report pairs already
saved in `data/dashboard/` can be passed explicitly:

```bash
python -m scripts.run_live_eod --state-dir ../nifty50-live-state \
  --source-dir data/dashboard
```

The source mode is recorded in each session's provenance. Subsequent runs
should omit `--source-dir` and download directly from NSE. The command is
idempotent: an unchanged store returns `status: current`. `waiting_for_reports`
means the next scheduled exchange session lacks a complete report pair.

The action check captures what NSE reports **at issue time**. An action
announced later can still affect a previously issued forecast; the stored
review timestamp and immutable forecast expose that limitation. The pipeline
does not refit either model. The [Phase 3 Pages dashboard](pages-dashboard.md)
is implemented with a public-data gate. The [Phase 4 private workflow](nightly-automation.md)
is installed with separate collection and publication gates.

The audited release is a frozen starting point. New observations are separate
dated files, so adding them cannot change the release lock. The store does not
include the original workbook or full NSE daily-report archives; it is still
private because it holds market data and model artifacts. Public Pages output
must be built from an explicit allowlist after display rights are reviewed.
