# Current quoted-price forecasting contract

For each stock after trading-session close `t`, estimate its actual NSE quoted close in rupees at `t+1` and `t+5` trading observations. Holidays do not count. This project evaluates price error; it does not implement a trading rule.

Both models use the [audited 15 October 2026 data release](data-release-2026-10-15.json). Training labels end by 31 December 2024. Calendar year 2025 is validation. The 2026 snapshot through 13 October has already been inspected for both models and cannot be called a blind final test for future revisions.

| Horizon | Frozen candidate | Predictor store | Current policy |
| --- | --- | --- | --- |
| Next session | Pooled 26-feature LightGBM, 60 trees, seven leaves, depth three, weighted equally by stock history | `features_cross_sectional_pre2026.csv.gz`, using the original OHLCV formulas so the saved 2026 result is reproducible | Model estimate; its historical 2025 and 2026 equal-stock rupee MAE was slightly below unchanged price. |
| Fifth session | Pooled corrected 30-feature LightGBM, with origin-price/equal-stock weights and parameters selected on 2021–2024 expanding folds | `features_5d_corrected_pre2026.csv.gz`, using share-consistent OHLCV and four additional historical features | Unchanged close: the fitted candidate was worse on 2025 validation. The rejected candidate remains saved and scored for analysis. |

The fitted models predict raw close return internally. The reported price is `origin_raw_close × (1 + predicted_return)`. Primary error is absolute rupee difference from `target_raw_close`, averaged first within each stock and then equally over the 50 stocks. Row-weighted MAE and RMSE are secondary. Every comparison includes unchanged current close.

Targets and features stay within each `segment_id`, so demergers and rights issues break their histories. Origin-target paths crossing split/bonus share bases are excluded from the quoted-price evaluation. That historical exclusion uses the eventual event boundary; a live event-aware forecast needs announcement timestamps and a declared coverage rule. The fixed October 2026 basket also carries survivorship bias for historical-index claims.

Run commands and outputs are in the [root README](../README.md). Historical scores and their limits are in [model results](../reports/model-results-2026-10-16.md). New feature choices require a declared chronological development protocol and a later prospective period; the already-inspected 2025 and 2026 results cannot validate a newly tuned method independently.
