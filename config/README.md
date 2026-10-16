# Configuration

The current data-release settings are versioned here:

- `data_pipeline.json`: source window, prediction horizons, split dates, and event handling.
- `nifty50_universe.json`: frozen 2026-10-15 constituent list and current ISINs.
- `corporate_actions.csv`: the 45 verified events and applicable split/bonus factors.
- `outlier_reviews.csv`: documented reviews of large adjusted price moves.

The matching input and output checksums are recorded in
[`docs/data-release-2026-10-15.json`](../docs/data-release-2026-10-15.json).
The quoted-price forecasting target is recorded in
[`docs/price-forecasting-plan.md`](../docs/price-forecasting-plan.md).
Both current model configurations are frozen in their Python modules and
recorded with each generated run. The older adjusted-return model settings
file was removed because that model is outside the current rerun scope.
Evaluator-specific deliverables remain open; see
[`docs/decisions.md`](../docs/decisions.md).
