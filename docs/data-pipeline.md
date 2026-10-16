# Daily-price processing contract

Run from the repository root after installing `requirements.txt`:

```bash
python -m nifty50_ta.data_pipeline
python -m nifty50_ta.release_audit
python -m unittest discover -s tests
```

To reconstruct the raw source snapshots from NSE before rebuilding, run
`python -m nifty50_ta.fetch_bse`, `python -m nifty50_ta.fetch_all_actions`,
`python -m nifty50_ta.verify_actions`, and
`python -m nifty50_ta.verify_price_source` in that order. The BSE collector
fetches one dated bhavcopy per trading day and resumes from completed yearly
checkpoints. The original workbook and the official constituent CSV must be
available in `data/raw/`; the raw files and verification snapshots are not
tracked in Git, so teammates need the agreed data snapshot to reproduce the
same release without downloading it again.

The default inputs are the unchanged `data/raw/50Nifty50.xlsx` and the archived
NSE BSE supplement `data/raw/bse_nse_prices.csv`. The configuration is in
`config/data_pipeline.json`; the 45-event ledger is in `config/corporate_actions.csv`,
and 20% return reviews are in `config/outlier_reviews.csv`. The official
constituent snapshot is frozen in `config/nifty50_universe.json`.
The output directory is `data/processed/`. Both raw and processed bulk data are
ignored by Git. The command accepts `--raw`, `--bse`, `--actions`, `--config`,
`--outlier-reviews`, `--universe`, `--full-actions`, `--price-spotcheck`,
`--outlier-snapshot`, and `--output`
for another checkout or a controlled rebuild.
The versioned [release lock](data-release-2026-10-15.json) records the exact
input and generated-output SHA-256 checksums and row counts for this release.
The full generated quality report, manifest, and audit remain in the local
`data/processed/` directory.

## Input and selection

The source workbook has 157,400 observations from 13 October 2016 through
13 October 2026. The processing rule keeps EQ, plus the first ten BE trading
days each for JIOFIN and MAXHEALTH. Its initial filter keeps 119,646 rows and
excludes 37,754 other-series rows. The official Nifty 50 list on 15 October
2026 contains BSE instead of WIPRO; NSE Indices says the replacement took
effect on 30 September 2026. We exclude the workbook's 2,480 WIPRO rows and
add 2,401 BSE EQ rows, each extracted from its dated NSE bhavcopy. The final
price table has 119,567 rows and exactly matches the official 50-symbol list.
The script checks stock-date uniqueness, numeric OHLC values, previous closes,
and dates against the observed market calendar. It does not infer or fill
prices before a stock was listed. Six stocks have less than the full ten-year
window because they listed later: BSE, ETERNAL, HDFCLIFE, JIOFIN, MAXHEALTH,
and SBILIFE. Their exact start dates appear in `stock_dictionary.csv`.

`Source.Name` supplies the workbook's canonical `symbol`; the original filename
and source worksheet row remain in `daily_prices.csv.gz`. BSE instead has its
source NSE bhavcopy URL and a blank Excel row. `ETERNAL` was known as
Zomato, and `TMPV` covers the earlier Tata Motors history. The supplied
workbook does not include ISIN; the stock dictionary records current ISINs
from the official constituent list. Historical legal-security continuity for
name changes remains a documented limitation.

## Price basis and corporate actions

`raw_open/high/low/close` are unchanged. `adj_open/high/low/close` multiply all
four raw prices by one `adjustment_factor` for verified split or bonus ratios
occurring later **within the same analytical segment**. The event date itself is
on the new share basis. The factor is the product of applicable ratios; cash
dividends are not included. Raw volume is not adjusted.

The seven demergers and eight rights offerings start new `segment_id` values.
The reference workbook used an ex-date-open/previous-close factor for
demergers, but that ratio has not been accepted as a shareholder-value
adjustment. Rights entitlements have not been valued. No return, indicator
window, or label crosses either kind of event. This discards cross-event
examples while retaining both sides of each event. A later backtest must
close before such an event or separately account for shareholder entitlements.

The teammate's `Technical Analysis.xlsx` `Data_Cleaning` sheet provided the
initial action list. A full-window NSE corporate-action search identified
one omitted BEL 1:10 bonus, the two BSE 2:1 bonuses, and eight rights issues;
three WIPRO actions were removed with that stock. The resulting 45-event
ledger has 30 verified split/bonus factors, seven verified demerger or
arrangement dates, and eight verified rights dates. The
full-window search is saved in `data/raw/nse_all_corporate_actions_snapshot.json`;
the event-specific NSE responses are in
`data/raw/nse_corporate_actions_snapshot.json`; rerun the check from that
snapshot with `python -m nifty50_ta.verify_actions --offline`. The API does
not establish the economic value of a demerger distribution or rights
entitlement, which is why the segment rule remains. The current constituent list is archived in
`data/raw/nifty50_constituents_retrieved_2026-10-15.csv` and its symbols are
in the versioned universe snapshot.

The workbook's exact original download URL/date were not supplied. To check
its exchange provenance, `python -m nifty50_ta.verify_price_source` compared
723 workbook stock-date observations on 15 dates with original NSE bhavcopies;
all had identical OHLCV and previous-close fields. Thirty-three comparisons
used documented historical ticker names; none were skipped. The original
workbook checksum and the spot-check source-file hashes are retained.

## Feature and target timing

Rows are ordered within each stock and analytical segment. Features use the
current and earlier closes only: daily price return, SMA 20/50, SMA-seeded
EMA 12/26, MACD 12/26/9, Wilder RSI 14, and price-normalized features.
`feature_ready` requires all core indicators; the first 49 rows of each
segment are not model-ready. For a flat 14-day window, RSI is set to the
neutral value 50 rather than dividing by zero.

The two forecast labels are distinct:

- `target_1d`: adjusted close at trading day `t+1` / adjusted close at `t` - 1.
- `target_5d`: adjusted close at trading day `t+5` / adjusted close at `t` - 1.

Both are **close-to-close forecast outcomes**. `reference_gross_executable_return`
is separately calculated using entry at `t+1` adjusted open and exit at
`t+1` or `t+5` adjusted close. It is a reference return before costs and is
not a completed strategy backtest. Targets ending outside their own time
split or analytical segment are excluded. The last one or five trading days of
each segment have no target. Five-day labels overlap; downstream evaluation
must respect this dependence.

The selected calendar splits are training through 2024-12-31, validation in
2025, and historical test from 2026-01-01 through 2026-10-13. Both current
models have been scored on 2026, so this period is no longer blind. A new model
must be selected using only earlier development data. Features at the
start of a split may use preceding historical prices, which were already
known. Results across stocks must use matching dates for fair comparison.

## Output files

| File | Role |
| --- | --- |
| `daily_prices.csv.gz` | Stock/date/segment key, raw OHLCV, adjusted OHLC, factor, workbook row or NSE source URL |
| `features.csv.gz` | Daily prices plus backward-looking indicators and readiness flag |
| `target_1d.csv.gz` | Model-ready one-day labels and next-open execution references |
| `target_5d.csv.gz` | Model-ready five-day labels and next-open execution references |
| `corporate_actions.csv` | Exact event ledger used in this run |
| `stock_dictionary.csv` | Symbol coverage, listing limitation, and naming notes |
| `outlier_review.csv` | Within-segment adjusted returns exceeding 20% in magnitude, joined to NSE action-date checks |
| `quality_report.json` | Reconciliations, coverage, split counts, release gates |
| `manifest.json` | Input/config/output checksums and price/target conventions |
| `release_audit.json` | Independent checks of output hashes, universe, feature alignment, event boundaries, and target values |

The `.csv.gz` files are ordinary gzip-compressed CSV, readable with pandas or
spreadsheet tools that support compressed CSV. Dates use ISO `YYYY-MM-DD` and
missing warm-up indicators are blank, never zero-filled.
Field definitions are in [the data dictionary](data-dictionary.md).
