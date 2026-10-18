# Private live-data store: Step 1

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
| `sessions/YYYY/MM/` | Future append-only, one-file-per-NSE-session stock rows and matching source provenance. |
| `forecasts/YYYY/MM/` | Future immutable, timestamped forecasts by origin session. |
| `outcomes/YYYY/MM/` | Future actual close and error records, separate from issued forecasts. |
| `store_manifest.json` | Baseline date, basket date, model identity, and copied-file hashes. |

Future session records should use `(session_date, symbol, series)` as a unique
key. Provenance should retain both NSE report URLs and SHA-256 hashes, the
retrieval time, the universe version, and validation result. Forecast records
should retain `issued_at_utc`, `origin_session`, `symbol`, horizon **1 or 5
trading observations**, price, policy/model version, and source hashes. A later
correction must be recorded as a new revision, not an overwrite of the issued
forecast. The nightly writer and exact record schema belong to Step 2.

The audited release is a frozen starting point. New observations are separate
dated files, so adding them cannot change the release lock. The store does not
include the original workbook or full NSE daily-report archives; it is still
private because it holds market data and model artifacts. Public Pages output
must be built from an explicit allowlist after display rights are reviewed.
