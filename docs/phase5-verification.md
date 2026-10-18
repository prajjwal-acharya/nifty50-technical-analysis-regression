# Phase 5: end-to-end verification and operations

The replay command checks the production path without downloading from NSE or
changing either repository. It requires a checkout of the private state store
and authorized copies of the 14–16 October 2026 NSE security and UDiFF report
pairs named as in `data/dashboard/`. Those cached report files are intentionally
not part of the public repository.

```bash
python -m unittest discover -s tests -p 'test_*.py' -q
python -m scripts.verify_phase5_replay \
  --state-dir ../nifty50-live-state \
  --source-dir data/dashboard \
  --public-json site/data/latest.json \
  --public-url https://prajjwal-acharya.github.io/nifty50-technical-analysis-regression/data/latest.json
```

The replay reconstructs the three session files from the saved official report
pairs and confirms that their hashes and the frozen model's 100 forecast entries
match the immutable private records. It also checks that the forecast was added
to Git after its recorded issue time and before the next NSE session opened.
The original issue was on 18 October 2026 at 10:16 IST; its private Git commit
was at 10:17 IST, before the 19 October 09:00 open. The current projection and
the deployed Pages JSON match for all 50 stocks and both exact target dates.

| Failure case | Required behavior |
| --- | --- |
| Holiday or announced special session | `Calendar.forward` counts reviewed NSE sessions, including 8 November 2026, rather than calendar days. |
| Missing middle report pair | Stop at the missing session and write no partial session or forecast. |
| Duplicate run | Return `current` with byte-identical private records. |
| Changed ISIN or disagreeing report values | Reject the session before it can enter the store. |
| Known price-basis action after origin | Abstain only for the affected symbol and horizon. |
| Unreviewed price-basis action through origin | Stop before writing records. |
| Scoring crosses the next 09:00 open | Append verified prices, do not issue a retrospective forecast, then fail the hosted run after preserving the new records so it is visible in Actions. |
| Public push interrupted | Regenerate the same allowlisted JSON from private records on the next run; the private records stay unchanged. |

The full local replay took about 10 seconds on the 18 October 2026 checkout.
The successful hosted manual run took 3 seconds for the gate and 26 seconds for
the ingest/publish job, including setup. Those are measurements, not a promise
for future NSE response times or GitHub queue delays.

## Daily checks and recovery

The private [nightly workflow](nightly-automation.md) runs at 00:17 and 06:17
IST. A missing or invalid report fails the job; the backup run or a manual
dispatch retries the earliest missing session. A failed Pages push can be
retried by dispatching the same private workflow after fixing the cause. The
runner does not rewrite old sessions or issued forecasts. Check the workflow
summary, then compare the JSON endpoint's `latest_verified_session`,
`origin_session`, `issued_at_utc`, and target dates with the latest private
records. The dashboard's **Check update** button fetches the published file
again; it does not run ingestion.

The current calendar covers only 2026. Add and review the 2027 NSE equity
calendar before year end and update the pinned public pipeline commit in the
private workflow. If NSE announces a new exceptional holiday or session, review
that change before issuing further exact-session forecasts. Prospective
performance must use only immutable forecasts issued after their origin close
and committed before the next target opened; outcomes remain separate from the
original forecast files. There are no realized 19 or 26 October target closes
in this verification snapshot, so no prospective accuracy claim is made yet.
