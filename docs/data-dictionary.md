# Processed-data dictionary

All dates are exchange trading dates in ISO `YYYY-MM-DD` format. Prices are
Indian rupees per share. Returns are decimal fractions: `0.01` means 1%.
Blank feature values mean the indicator has insufficient history; blank
targets mean the required future observation is unavailable and are omitted
from the model-ready target files. The processing method is specified in
[data-pipeline.md](data-pipeline.md).

## `daily_prices.csv.gz`

| Column | Meaning |
| --- | --- |
| `date` | Trading date |
| `symbol` | Official current constituent symbol; from the workbook filename for 49 stocks and NSE bhavcopy for BSE |
| `series` | Source NSE series, normally EQ; the specified 20 opening observations are BE |
| `raw_open`, `raw_high`, `raw_low`, `raw_close` | As-supplied daily prices, never overwritten |
| `raw_prev_close` | As-supplied prior close; checked against the preceding source close |
| `volume` | As-supplied shares traded; not adjusted |
| `source_name` | Original component filename from `Source.Name`; `NSE_BSE_bhavcopy` for BSE |
| `source_excel_row` | One-based row in the raw workbook; blank for BSE |
| `source_url` | Dated NSE bhavcopy URL for BSE; blank for workbook rows |
| `segment_number` | Starts at zero and increments at each listed demerger or rights issue for that symbol |
| `segment_id` | `symbol:segment_number`; analysis and targets stay within it |
| `adjustment_factor` | Product of future split/bonus price factors within this segment; positive, no demerger, rights, or dividend factor |
| `adj_open`, `adj_high`, `adj_low`, `adj_close` | Corresponding raw price multiplied by `adjustment_factor` |

## `features.csv.gz`

This file repeats the market values and segment keys from `daily_prices` but
omits the source filename, Excel row, and source URL. Every feature is calculated within
one `segment_id` from present and earlier closes only.

| Column | Meaning |
| --- | --- |
| `segment_day` | One-based trading-day number since the current segment began |
| `daily_price_return` | Adjusted close / preceding adjusted close - 1; blank on segment day 1 |
| `sma_20`, `sma_50` | Mean of the most recent 20 or 50 adjusted closes |
| `ema_12`, `ema_26` | EMA with first value seeded by the simple mean of 12 or 26 closes |
| `macd` | EMA 12 - EMA 26, in rupees per adjusted share |
| `macd_signal` | Nine-day EMA of MACD, seeded by its first nine available observations |
| `macd_histogram` | MACD - MACD signal |
| `macd_pct_close`, `macd_histogram_pct_close` | Corresponding MACD quantity / adjusted close × 100, for cross-stock comparison |
| `rsi_14` | Wilder RSI on 14 adjusted-close changes, 0–100; all-flat window maps to neutral 50 |
| `price_vs_sma_20_pct`, `price_vs_sma_50_pct` | (Adjusted close / corresponding SMA - 1) × 100 |
| `feature_ready` | True once SMA 20/50, MACD signal, and RSI 14 are all available |

## `target_1d.csv.gz` and `target_5d.csv.gz`

These files contain only rows with `feature_ready=True`, a future close in the
same analytical segment, and an endpoint inside the row's own time split.

| Column | Meaning |
| --- | --- |
| `date`, `symbol`, `segment_id` | Forecast-origin key: signal can be formed after this close |
| `target_date` | Next trading day for 1d; fifth subsequent trading day for 5d |
| `split` | Train, validation, or test, assigned by origin and endpoint dates |
| `eligible` | Always true in these filtered model-ready files |
| `forward_close_return` | Adjusted close on `target_date` / adjusted close on origin `date` - 1; prediction label |
| `reference_entry_date` | Next trading day after origin |
| `reference_entry_adj_open` | Next day's adjusted open; a possible executable entry reference |
| `reference_exit_adj_close` | Adjusted close on `target_date`; possible exit reference |
| `reference_gross_executable_return` | Exit close / entry open - 1, before trading costs; **not** a backtested strategy result |

## Other outputs

`stock_dictionary.csv` records each symbol's actual first and last date,
number of days, observed-calendar gaps, name-change note, current ISIN from
the official list, and fixed-universe rule. `corporate_actions.csv` records
the exact event ledger, teammate-proposed demerger factor for historical
reference, NSE verification status, and source URL. Demerger and rights
events have no applied adjustment factor. `outlier_review.csv` contains adjusted returns
above 20% in magnitude and the corresponding NSE action-date check.

`quality_report.json` gives count reconciliations, coverage, model-ready rows,
exchange verification, documented limitations, and release gates. `manifest.json`
records input/config/output checksums, the adjustment convention, targets,
and missing original workbook download metadata.
