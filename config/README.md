# Configuration

The current data-release settings are versioned here:

- `data_pipeline.json`: source window, prediction horizons, split dates, and event handling.
- `nifty50_universe.json`: frozen 2026-10-15 constituent list and current ISINs.
- `corporate_actions.csv`: the 45 verified events and applicable split/bonus factors.
- `outlier_reviews.csv`: documented reviews of large adjusted price moves.

The matching input and output checksums are recorded in
[`docs/data-release-2026-10-15.json`](../docs/data-release-2026-10-15.json).
Trading rules, costs, and evaluator-specific deliverables remain open; see
[`docs/decisions.md`](../docs/decisions.md).
