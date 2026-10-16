# Nifty 50 closing-price forecasts

This repository reproduces two historical forecasts for the current 50-stock Nifty 50 basket: the **next trading-session** close and the **fifth subsequent trading-session** close. Forecasts and errors use NSE quoted closing prices in rupees. The unchanged current close is the required baseline. The five-session fitted candidate did not pass its 2025 validation gate, so its adopted forecast remains the unchanged close.

The fixed data release ends on **13 October 2026**. Both models have already been evaluated on 2026 data; those scores are historical evidence, not a fresh blind test or live forecast. See [model results](reports/model-results-2026-10-16.md), [the forecasting contract](docs/price-forecasting-plan.md), and [the release decisions](docs/decisions.md).

## Set up

Use Python 3.12. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m nifty50_ta
```

On Windows PowerShell, create the environment with `py -3.12 -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1`. The project is portable: commands and code use repository-relative paths. No GPU, personal Drive path, or CI service is required.

## Obtain or rebuild the data

The raw workbook and processed market-data files are not in Git. Obtain the agreed raw snapshot, place it as described in [data/README.md](data/README.md), then run:

```bash
python -m nifty50_ta.data_pipeline
python -m nifty50_ta.release_audit
```

[The data-pipeline contract](docs/data-pipeline.md) lists the NSE source collectors, verification commands, input files, corporate-action rules, and exact release checksums. If a teammate has the verified processed snapshot, it can be placed in `data/processed/` instead of rebuilding. The model feature builder checks `features.csv.gz` against the versioned release lock.

## Rerun the two models

Run these from the repository root after the processed release is available:

```bash
python -m scripts.build_model_features
python -m scripts.run_one_day_model
python -m scripts.evaluate_5d_2025
python -m scripts.evaluate_corrected_5d_2026
```

| Command | Output |
| --- | --- |
| `build_model_features` | Rebuilds and verifies the 26-feature next-session and 30-feature corrected fifth-session stores under ignored `data/processed/`. |
| `run_one_day_model` | Fits the frozen 26-feature LightGBM through 2024; saves its model, 2025 validation and historical 2026 predictions under `reports/generated/one_day/`. |
| `evaluate_5d_2025` | Selects the corrected 30-feature LightGBM on 2021–2024 folds, fits through 2024, and saves its model and 2025 validation results under `reports/generated/price_forecasting_5d_2025/`. The unchanged-price gate is retained. |
| `evaluate_corrected_5d_2026` | Loads the latest saved fifth-session model without refitting and scores the already-inspected 2026 period under `reports/generated/price_forecasting_5d_2026_corrected/`. Pass `--model-run PATH` to choose a specific saved run. |

Each run creates a timestamped directory and leaves earlier outputs intact. To check code and data boundaries:

```bash
python -m unittest discover -s tests -q
```

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/nifty50_ta/data_pipeline.py` and `fetch_*.py`, `verify_*.py`, `release_audit.py` | Raw-data collection, processing, source verification, and release audit. |
| `src/nifty50_ta/model_common.py`, `feature_expansion.py`, `cross_sectional_expansion.py`, `one_day_model.py`, `five_day_model.py`, `price_forecasting.py` | Reusable code required by the two models. |
| `scripts/` | Four entry points shown above; no exploratory model-comparison scripts. |
| `config/` | Frozen universe, events, and processing settings. |
| `data/` | Instructions and ignored raw/processed market data. |
| `docs/` | Data contract, field definitions, current forecast objective, and decisions. |
| `reports/` | Current results summary; generated run outputs are ignored by Git. |
| `tests/` | Checks for processing, feature timing, and forecast boundaries. |

GitHub is the shared source for code and decisions. Do not commit raw market data, generated model runs, credentials, or personal runtime paths. No automated CI workflow is configured.
