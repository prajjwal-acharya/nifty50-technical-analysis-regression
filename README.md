# Nifty 50 Technical Analysis and Regression Backtesting

This is a Business Analytics project about a practical question: **can technical indicators help predict future stock returns, and can a rule based on those predictions perform better than buy-and-hold after trading costs?** The proposed scope is daily data for 50 Nifty 50 stocks across ten years. We will use linear regression for prediction and a separate backtest for trading performance.

**Current status:** this repository contains the shared environment and folder structure. Data, models, results, and final research choices have **not** been created or confirmed yet. See [the decision list](docs/decisions.md) before treating any method as final.

## The three parts of the work

1. **Data collection and finalization:** agree on the stock list, dates, source, adjustments, and chronological training/validation/test periods; build a five-stock pilot, then the 50-stock dataset.
2. **Model creation and training:** calculate indicators, define the future-return target, train linear regression, and choose the model and trading rules using training and validation data only.
3. **Testing and evaluation:** freeze the choices, evaluate once on the later test period, compare the trading strategy with buy-and-hold, and prepare the Excel, Pine Script, report, and presentation evidence.

Prediction accuracy and trading performance are different results; we will report both. No profitable outcome is assumed.

## What you need

- A GitHub account and Git installed if you will contribute code.
- **Python 3.12** if you will run the project on your computer. Download it from [python.org](https://www.python.org/downloads/) if needed. The current project setup intentionally checks for Python 3.12 so teammates do not silently run different Python versions.
- Internet access for the initial package installation and, later, for collecting market data.
- A Google account if you want to use [Google Colab](https://colab.research.google.com/). Colab runs Python in a browser; you do not need to install Python locally for this route.
- TradingView only when working on the Pine Script demonstration. Microsoft Excel or a compatible spreadsheet app is used for selected independent checks.

You can do the main Python work in **Colab or on your own computer**. Both routes install this same repository. A GPU and paid Colab plan are not part of the setup.

## Option A: set up in Google Colab

1. Open [Google Colab](https://colab.research.google.com/) and choose **New notebook**.
2. Run this in a code cell to check the Python version:

   ```python
   import sys
   print(sys.version)
   ```

   It should start with `3.12`. If it does not, choose a Python 3.12 runtime in **Runtime → Change runtime type**, if available, or tell the project maintainer before proceeding.
3. Run these lines in separate code cells (or one cell in this order):

   ```python
   !git clone https://github.com/prajjwal-acharya/nifty50-technical-analysis-regression.git
   %cd nifty50-technical-analysis-regression
   %pip install -r requirements.txt
   !python -m nifty50_ta
   ```

4. The last command should finish with **`Environment OK. Project analysis has not started yet.`** Future notebooks will be kept in `notebooks/` and will use the same Python code in `src/`.

Colab's working machine is temporary. If a runtime is reset, repeat the clone and install steps. Do not keep your only copy of work or data in `/content`. Store accepted code in GitHub and the agreed data snapshot in a shared, persistent location. A personal Drive copy of a notebook is not the official project version.

## Option B: set up on your own computer

1. Install [Git](https://git-scm.com/downloads) and Python 3.12.
2. Open Terminal (macOS/Linux) or PowerShell (Windows), then download the repository:

   ```bash
   git clone https://github.com/prajjwal-acharya/nifty50-technical-analysis-regression.git
   cd nifty50-technical-analysis-regression
   ```

3. Create a **virtual environment**, a project-specific folder for Python packages. It keeps this project's packages separate from other Python projects.

   macOS/Linux:

   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

   Windows PowerShell:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

4. Install the project and check it:

   ```bash
   python -m pip install -r requirements.txt
   python -m nifty50_ta
   ```

   The check should finish with **`Environment OK. Project analysis has not started yet.`** Run the activation command again when you open a new terminal. Use `deactivate` to leave the environment.

**Do not change installed versions individually to fix one person's machine.** Record the problem in a GitHub issue so the team can make and verify one shared dependency change.

## Folder guide

| Path | What belongs there |
| --- | --- |
| `src/nifty50_ta/` | Reusable Python code for data, indicators, model training, backtesting, and evaluation. Currently it only contains the setup check. |
| `notebooks/` | Short Colab/Jupyter notebooks that call the reusable code and show results. |
| `config/` | Agreed dates, feature settings, costs, and other project choices once finalized. |
| `data/` | Instructions for raw and cleaned data. The actual `raw/` and `processed/` files are ignored by Git for now. |
| `docs/` | Project decisions and method notes. Start with `docs/decisions.md`. |
| `tests/` | Focused checks for date alignment, indicators, and backtest rules when those components exist. |
| `excel/` | Workbook for independent checks of selected calculations. |
| `pine/` | Pine Script strategy and notes for TradingView checks. |
| `reports/` | Final written report, selected figures, and presentation. |

The code will use repository-relative paths or a configurable data location. It must not require anyone's personal username, home folder, or Google Drive path.

## What Colab, Excel, and Pine Script each do

- **Colab/Python:** the main reproducible workflow across all selected stocks: preparation, indicators, model fitting, chronological validation, backtest, and summary tables.
- **Excel:** independently check a small, documented sample of important calculations. It is not the source of the full model.
- **Pine Script:** TradingView's chart scripting language. It will demonstrate and check the agreed trading rule on selected charts. A Pine strategy operates on a chart dataset, so the 50-stock summary stays in Python.

The exact evidence required from Excel and Pine Script must be confirmed with the evaluator; see `docs/decisions.md`.

## Working together on GitHub

1. Open or claim a GitHub issue for a defined task. Keep the three project parts visible as milestones or issue groups.
2. Create a short branch for the task, make a focused change, and open a pull request into `main`.
3. Ask a teammate to review the pull request. Explain what changed and how you checked it before merging.
4. Keep accepted code and method decisions in the repository. Do not commit passwords, API keys, personal `.env` files, downloaded datasets of uncertain redistribution rights, or generated bulk outputs.
5. Once each part is accepted, tag the corresponding code version and record the matching data snapshot and settings. This lets the final report identify exactly what produced its results.

There is **no automated CI workflow yet**, as requested. Reviews and local/Colab checks are the initial quality gate.
