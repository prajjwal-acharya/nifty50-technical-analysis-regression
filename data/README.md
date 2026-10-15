# Data area

The project data source and stock-selection rule are not finalized yet. Do not treat a newly downloaded file as the final dataset.

- `raw/`: original downloaded files, unchanged.
- `processed/`: cleaned tables created from the raw files by project code.

Both folders are ignored by Git. When the dataset is finalized, record its source, download date, date range, stock list, adjustment method, and SHA-256 checksums in this repository. The team will agree on one shared, versioned snapshot before training. Only publish market data if its source permits redistribution.
