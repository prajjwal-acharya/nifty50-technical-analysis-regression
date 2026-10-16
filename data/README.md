# Data area

The supplied workbook has been copied unchanged to `raw/50Nifty50.xlsx`.
The versioned processing rules are in `config/` and
[the pipeline contract](../docs/data-pipeline.md). Run
`python -m nifty50_ta.data_pipeline` to rebuild the processed tables.

- `raw/`: original downloaded files, unchanged.
- `processed/`: cleaned tables created from the raw files by project code.

Both folders are ignored by Git. The raw folder also holds `bse_nse_prices.csv`,
2,401 exact BSE EQ rows extracted from dated NSE bhavcopies with source URLs
and file hashes, plus the official constituent snapshot and verification
snapshots. The generated manifest records file checksums and processing rules.
The versioned [release lock](../docs/data-release-2026-10-15.json) records
the checksums and row counts for this finalized local snapshot.
The processed universe matches the official Nifty 50 list as of 2026-10-15.
The exact original workbook download URL/date remain unknown, but a separate
NSE bhavcopy spot-check found no price differences across 723 matched
stock-date observations. Only publish
market data if its source permits redistribution.

If using the local `processed.zip` handoff, extract it from the `data/`
directory so its `processed/` entry becomes `data/processed/`, then run
`python -m nifty50_ta.release_audit` before training.
The archive itself is ignored by Git.
