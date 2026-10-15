"""Rebuild the auditable Nifty 50 price, feature, and forecast-target tables.

The raw workbook is immutable. Split and bonus ratios adjust OHLC within each
continuous security segment. Demergers and rights offerings start new segments:
no return, indicator, or target crosses one without entitlement treatment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RAW_COLUMNS = (
    "Source.Name", "DATE", "SERIES", "OPEN", "HIGH", "LOW", "PREV. CLOSE",
    "LTP", "CLOSE", "VWAP", "VOLUME", "VALUE", "NO OF TRADES",
    "DELIVERY QTY", "DELIVERY %",
)
PRICE_COLUMNS = ("open", "high", "low", "close")
ACTION_TYPES = {"Bonus", "Split", "Split+Bonus", "Demerger", "Rights"}
SEGMENT_BOUNDARY_TYPES = {"Demerger", "Rights"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["demergers"] == "segment_boundary_no_cross_event_labels"
    assert config["rights_issues"] == "segment_boundary_no_cross_event_labels"
    assert config["cash_dividends"] == "excluded"
    return config


def check_universe(actual_symbols: set[str], universe_path: Path) -> dict:
    """Compare the source workbook with a frozen official constituent snapshot."""
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    expected = universe["symbols"]
    if len(expected) != 50 or len(set(expected)) != 50:
        raise ValueError("Constituent snapshot must contain 50 unique symbols")
    return {
        "as_of_date": universe["as_of_date"],
        "source_url": universe["source_url"],
        "replacement_press_release_url": universe["replacement_press_release_url"],
        "matches_official_snapshot": actual_symbols == set(expected),
        "missing_from_processed": sorted(set(expected) - actual_symbols),
        "extra_in_processed": sorted(actual_symbols - set(expected)),
    }


def check_action_coverage(actions: pd.DataFrame, snapshot_path: Path,
                          official_symbols: set[str]) -> dict:
    """Ensure the saved full-window NSE action search has no omitted events."""
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    listed = snapshot["symbols"]
    if set(listed) != official_symbols:
        raise ValueError("Full-window NSE action snapshot does not cover the official universe")
    terms = ("bonus", "split", "splt", "demerg", "arrangement", "rights",
             "merger", "amalgam", "consolidat")
    source_event_keys = set()
    for symbol, source in listed.items():
        for record in source["records"]:
            if record.get("series") != "EQ" or not any(
                term in str(record.get("subject", "")).lower() for term in terms
            ):
                continue
            event_date = pd.to_datetime(record["exDate"], format="%d-%b-%Y")
            source_event_keys.add((symbol, event_date))
    ledger_keys = set(zip(actions.symbol, actions.ex_date))
    missing = sorted(f"{symbol}:{date.date()}" for symbol, date in source_event_keys - ledger_keys)
    if missing:
        raise ValueError(f"Corporate-action ledger omits NSE events: {missing}")
    return {
        "official_symbols_checked": len(listed),
        "distinct_nse_action_dates": len(source_event_keys),
        "ledger_action_dates": len(ledger_keys),
        "missing_nse_action_dates": missing,
        "snapshot_sha256": sha256(snapshot_path),
    }


def check_workbook_spotcheck(raw_path: Path, spotcheck_path: Path) -> dict:
    report = json.loads(spotcheck_path.read_text(encoding="utf-8"))
    if report["workbook_sha256"] != sha256(raw_path):
        raise ValueError("Workbook price spot-check belongs to another raw file")
    if report["matched_symbol_dates"] < 500 or report["field_mismatches"]:
        raise ValueError("Workbook price spot-check did not pass")
    return {
        "dates_checked": len(report["dates_checked"]),
        "matched_symbol_dates": report["matched_symbol_dates"],
        "matched_via_historical_symbol": report["matched_via_historical_symbol"],
        "skipped_historical_alias_or_series": report[
            "skipped_symbol_dates_due_to_historical_symbol_or_series"
        ],
        "field_mismatches": len(report["field_mismatches"]),
        "snapshot_sha256": sha256(spotcheck_path),
    }


def check_outlier_snapshot(outliers: pd.DataFrame, snapshot_path: Path) -> dict:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    verified = {(row["symbol"], row["date"]): row for row in snapshot}
    observed = {(row.symbol, row.date.date().isoformat()) for row in outliers.itertuples(index=False)}
    if observed != set(verified):
        raise ValueError("NSE outlier-action snapshot does not match current outliers")
    if any(row["nse_actions_on_date"] for row in verified.values()):
        raise ValueError("An adjusted-return outlier has an NSE corporate action to review")
    return {"outliers_checked": len(verified), "snapshot_sha256": sha256(snapshot_path)}


def load_actions(path: Path) -> pd.DataFrame:
    actions = pd.read_csv(path, parse_dates=["ex_date"])
    required = {
        "action_id", "symbol", "ex_date", "action_type", "details",
        "split_bonus_factor", "workbook_suggested_demerger_factor",
        "source", "verification_status", "source_url",
    }
    if set(actions.columns) != required:
        raise ValueError(f"Action-ledger columns differ: {set(actions.columns) ^ required}")
    if actions["action_id"].duplicated().any() or actions[["symbol", "ex_date"]].duplicated().any():
        raise ValueError("Duplicate corporate-action key")
    if not set(actions["action_type"]).issubset(ACTION_TYPES):
        raise ValueError("Unknown corporate-action type")
    regular = ~actions["action_type"].isin(SEGMENT_BOUNDARY_TYPES)
    if actions.loc[regular, "split_bonus_factor"].isna().any():
        raise ValueError("Missing split or bonus factor")
    if (actions.loc[regular, "split_bonus_factor"] <= 0).any():
        raise ValueError("Nonpositive split or bonus factor")
    if actions.loc[~regular, "split_bonus_factor"].notna().any():
        raise ValueError("Entitlement-event factor must not enter the approved price series")
    return actions.sort_values(["symbol", "ex_date"]).reset_index(drop=True)


def read_raw_prices(path: Path, be_initial_days: dict[str, int]) -> tuple[pd.DataFrame, dict]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if len(workbook.worksheets) != 1:
        raise ValueError("Expected one raw-data worksheet")
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    if tuple(next(rows)[:15]) != RAW_COLUMNS:
        raise ValueError("Raw-data schema changed")
    kept = []
    series_counts: Counter[str] = Counter()
    excluded_counts: Counter[str] = Counter()
    be_dates: dict[str, list[datetime]] = {symbol: [] for symbol in be_initial_days}
    for excel_row, row in enumerate(rows, 2):
        if all(value is None for value in row[:15]):
            continue
        source, date, series, op, hi, lo, prev_close, _, close, _, volume = row[:11]
        if not isinstance(source, str) or "_Historical_" not in source:
            raise ValueError(f"Cannot derive symbol at raw row {excel_row}")
        symbol = source.split("_Historical_", 1)[0]
        series_counts[str(series)] += 1
        keep = series == "EQ" or (series == "BE" and symbol in be_initial_days)
        if not keep:
            excluded_counts[str(series)] += 1
            continue
        if not isinstance(date, datetime):
            raise ValueError(f"Invalid date at raw row {excel_row}")
        if series == "BE":
            be_dates[symbol].append(date)
        kept.append((date, symbol, series, op, hi, lo, prev_close, close, volume, source, excel_row))
    workbook.close()
    for symbol, expected in be_initial_days.items():
        dates = sorted(be_dates[symbol])
        if len(dates) != expected or len(set(dates)) != expected:
            raise ValueError(f"Unexpected BE history for {symbol}: {len(dates)} rows")
    frame = pd.DataFrame(
        kept,
        columns=["date", "symbol", "series", "raw_open", "raw_high", "raw_low",
                 "raw_prev_close", "raw_close", "volume", "source_name", "source_excel_row"],
    ).sort_values(["symbol", "date"]).reset_index(drop=True)
    for symbol, expected in be_initial_days.items():
        first = frame.loc[frame.symbol.eq(symbol)].head(expected)
        if not first.series.eq("BE").all():
            raise ValueError(f"BE rows are not {symbol}'s first {expected} trading days")
    return frame, {
        "raw_rows": sum(series_counts.values()),
        "kept_rows": len(frame),
        "excluded_rows": sum(excluded_counts.values()),
        "raw_series_counts": dict(sorted(series_counts.items())),
        "excluded_series_counts": dict(sorted(excluded_counts.items())),
        "be_kept_counts": {key: len(value) for key, value in be_dates.items()},
    }


def read_bse_prices(path: Path) -> pd.DataFrame:
    """Load the archived NSE BSE EQ extraction without changing source prices."""
    source = pd.read_csv(path, dtype={"symbol": str, "series": str}, parse_dates=["date"])
    required = {
        "date", "symbol", "series", "open", "high", "low", "prev_close",
        "close", "volume", "source_url", "source_sha256", "source_raw_row_json",
    }
    if not required.issubset(source.columns):
        raise ValueError(f"BSE source columns missing: {required - set(source.columns)}")
    if not source.symbol.eq("BSE").all() or not source.series.eq("EQ").all():
        raise ValueError("BSE supplement must contain only BSE EQ rows")
    if source.date.duplicated().any() or source.source_url.isna().any() or source.source_sha256.isna().any():
        raise ValueError("Invalid or duplicate BSE source provenance")
    if not source.source_url.str.startswith("https://nsearchives.nseindia.com/").all():
        raise ValueError("BSE source URL is not an NSE archive")
    if not source.source_sha256.str.fullmatch(r"[0-9a-f]{64}").all():
        raise ValueError("Invalid BSE source-file hash")
    for row in source.itertuples(index=False):
        raw = json.loads(row.source_raw_row_json)
        original = {key.strip(): value.strip() for key, value in raw.items() if key is not None}
        if original.get("SYMBOL") != "BSE" or original.get("SERIES") != "EQ":
            raise ValueError("BSE extracted row differs from archived NSE row")
        original_date = pd.to_datetime(
            original.get("DATE1", original.get("TIMESTAMP")), dayfirst=True
        )
        if original_date != row.date:
            raise ValueError("BSE extracted date differs from archived NSE row")
        nse_fields = ({"open": "OPEN_PRICE", "high": "HIGH_PRICE", "low": "LOW_PRICE",
                       "prev_close": "PREV_CLOSE", "close": "CLOSE_PRICE", "volume": "TTL_TRD_QNTY"}
                      if "OPEN_PRICE" in original else
                      {"open": "OPEN", "high": "HIGH", "low": "LOW",
                       "prev_close": "PREVCLOSE", "close": "CLOSE", "volume": "TOTTRDQTY"})
        if any(abs(float(getattr(row, field)) - float(original[nse_field])) > 1e-9
               for field, nse_field in nse_fields.items()):
            raise ValueError("BSE numeric value differs from archived NSE row")
    prices = source.rename(columns={
        "open": "raw_open", "high": "raw_high", "low": "raw_low",
        "prev_close": "raw_prev_close", "close": "raw_close",
    })
    prices["source_name"] = "NSE_BSE_bhavcopy"
    prices["source_excel_row"] = pd.NA
    return prices[
        ["date", "symbol", "series", "raw_open", "raw_high", "raw_low",
         "raw_prev_close", "raw_close", "volume", "source_name", "source_excel_row", "source_url"]
    ]


def validate_prices(prices: pd.DataFrame, config: dict) -> dict:
    if prices.empty or prices[["symbol", "date"]].duplicated().any():
        raise ValueError("Empty price data or duplicate symbol-date keys")
    if prices["symbol"].nunique() != 50:
        raise ValueError(f"Expected 50 symbols, got {prices.symbol.nunique()}")
    if prices.date.min() != pd.Timestamp(config["source_first_date"]):
        raise ValueError("First date differs from the configured input window")
    if prices.date.max() != pd.Timestamp(config["source_last_date"]):
        raise ValueError("Last date differs from the configured input window")
    numerical = ["raw_open", "raw_high", "raw_low", "raw_prev_close", "raw_close", "volume"]
    if prices[numerical].isna().any().any():
        raise ValueError("Required market-data value is blank")
    if (prices[["raw_open", "raw_high", "raw_low", "raw_close"]] <= 0).any().any():
        raise ValueError("Nonpositive price")
    if (prices.volume < 0).any():
        raise ValueError("Negative volume")
    previous = prices.groupby("symbol", sort=False).raw_close.shift(1)
    inconsistent_previous_close = previous.notna() & (prices.raw_prev_close - previous).abs().gt(0.011)
    if inconsistent_previous_close.any():
        raise ValueError(f"Raw previous-close mismatch on {int(inconsistent_previous_close.sum())} rows")
    if ((prices.raw_high < prices[["raw_open", "raw_close", "raw_low"]].max(axis=1)) |
        (prices.raw_low > prices[["raw_open", "raw_close", "raw_high"]].min(axis=1))).any():
        raise ValueError("OHLC ordering violation")
    calendar = set(prices.date.unique())
    gaps = {}
    coverage = []
    for symbol, group in prices.groupby("symbol", sort=True):
        observed = set(group.date)
        between = {day for day in calendar if group.date.iloc[0] <= day <= group.date.iloc[-1]}
        gaps[symbol] = len(between - observed)
        coverage.append({
            "symbol": symbol, "first_date": group.date.iloc[0].date().isoformat(),
            "last_date": group.date.iloc[-1].date().isoformat(), "trading_days": len(group),
            "missing_dates_within_observed_calendar": gaps[symbol],
        })
    if any(gaps.values()):
        raise ValueError(f"Missing dates within observed exchange calendar: {gaps}")
    return {"coverage": coverage, "observed_calendar_days": len(calendar), "internal_gaps": gaps}


def apply_actions(prices: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    prices = prices.copy()
    prices["segment_number"] = 0
    prices["adjustment_factor"] = 1.0
    for symbol, group in prices.groupby("symbol", sort=False):
        symbol_actions = actions.loc[actions.symbol.eq(symbol)]
        boundary_dates = np.array(
            symbol_actions.loc[symbol_actions.action_type.isin(SEGMENT_BOUNDARY_TYPES), "ex_date"].values,
            dtype="datetime64[ns]",
        )
        dates = group.date.to_numpy(dtype="datetime64[ns]")
        segments = np.searchsorted(boundary_dates, dates, side="right")
        prices.loc[group.index, "segment_number"] = segments
        factor = np.ones(len(group), dtype=float)
        for action in symbol_actions.itertuples(index=False):
            if action.action_type in SEGMENT_BOUNDARY_TYPES:
                continue
            event_segment = int(np.searchsorted(boundary_dates, np.datetime64(action.ex_date), side="right"))
            factor[(dates < np.datetime64(action.ex_date)) & (segments == event_segment)] *= float(action.split_bonus_factor)
        prices.loc[group.index, "adjustment_factor"] = factor
    prices["segment_id"] = prices.symbol + ":" + prices.segment_number.astype(str)
    for col in PRICE_COLUMNS:
        prices[f"adj_{col}"] = prices[f"raw_{col}"] * prices.adjustment_factor
    if (prices.adjustment_factor <= 0).any():
        raise ValueError("Invalid adjustment factor")
    return prices


def seeded_ema(values: np.ndarray, window: int) -> np.ndarray:
    result = np.full(len(values), np.nan)
    if len(values) < window:
        return result
    result[window - 1] = np.mean(values[:window])
    alpha = 2 / (window + 1)
    for i in range(window, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def wilder_rsi(values: np.ndarray, window: int = 14) -> np.ndarray:
    result = np.full(len(values), np.nan)
    if len(values) <= window:
        return result
    delta = np.diff(values)
    avg_gain = np.maximum(delta[:window], 0).mean()
    avg_loss = np.maximum(-delta[:window], 0).mean()
    for i in range(window, len(values)):
        if i > window:
            avg_gain = (avg_gain * (window - 1) + max(delta[i - 1], 0)) / window
            avg_loss = (avg_loss * (window - 1) + max(-delta[i - 1], 0)) / window
        result[i] = 50 if avg_gain == avg_loss == 0 else (
            100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
        )
    return result


def create_features(prices: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, group in prices.groupby("segment_id", sort=False):
        group = group.copy()
        close = group.adj_close.to_numpy(dtype=float)
        group["segment_day"] = np.arange(1, len(group) + 1)
        group["daily_price_return"] = group.adj_close.pct_change(fill_method=None)
        group["sma_20"] = group.adj_close.rolling(20, min_periods=20).mean()
        group["sma_50"] = group.adj_close.rolling(50, min_periods=50).mean()
        ema_12 = seeded_ema(close, 12)
        ema_26 = seeded_ema(close, 26)
        macd = ema_12 - ema_26
        signal = np.full(len(group), np.nan)
        if len(group) >= 34:
            signal[33:] = seeded_ema(macd[25:], 9)[8:]
        group["ema_12"] = ema_12
        group["ema_26"] = ema_26
        group["macd"] = macd
        group["macd_signal"] = signal
        group["macd_histogram"] = macd - signal
        group["macd_pct_close"] = 100 * group.macd / group.adj_close
        group["macd_histogram_pct_close"] = 100 * group.macd_histogram / group.adj_close
        group["rsi_14"] = wilder_rsi(close)
        group["price_vs_sma_20_pct"] = 100 * (group.adj_close / group.sma_20 - 1)
        group["price_vs_sma_50_pct"] = 100 * (group.adj_close / group.sma_50 - 1)
        group["feature_ready"] = group[["sma_20", "sma_50", "macd_signal", "rsi_14"]].notna().all(axis=1)
        parts.append(group)
    return pd.concat(parts, ignore_index=True)


def split_for_date(date: pd.Series, config: dict) -> pd.Series:
    result = pd.Series("outside_window", index=date.index, dtype="string")
    result.loc[date.le(pd.Timestamp(config["train_end"]))] = "train"
    result.loc[date.gt(pd.Timestamp(config["train_end"])) & date.le(pd.Timestamp(config["validation_end"]))] = "validation"
    result.loc[date.gt(pd.Timestamp(config["validation_end"])) & date.le(pd.Timestamp(config["test_end"]))] = "test"
    return result


def create_targets(features: pd.DataFrame, horizon: int, config: dict) -> pd.DataFrame:
    parts = []
    for _, group in features.groupby("segment_id", sort=False):
        group = group.copy()
        group["target_date"] = group.date.shift(-horizon)
        group["forward_close_return"] = group.adj_close.shift(-horizon) / group.adj_close - 1
        group["reference_entry_date"] = group.date.shift(-1)
        group["reference_entry_adj_open"] = group.adj_open.shift(-1)
        group["reference_exit_adj_close"] = group.adj_close.shift(-horizon)
        group["reference_gross_executable_return"] = (
            group.reference_exit_adj_close / group.reference_entry_adj_open - 1
        )
        parts.append(group)
    targets = pd.concat(parts, ignore_index=True)
    targets["split"] = split_for_date(targets.date, config)
    target_split = split_for_date(targets.target_date, config)
    targets["eligible"] = (
        targets.feature_ready & targets.forward_close_return.notna() &
        targets.split.eq(target_split) & targets.split.ne("outside_window")
    )
    columns = [
        "date", "symbol", "segment_id", "target_date", "split", "eligible",
        "forward_close_return", "reference_entry_date", "reference_entry_adj_open",
        "reference_exit_adj_close", "reference_gross_executable_return",
    ]
    return targets.loc[targets.eligible, columns].reset_index(drop=True)


def outlier_report(prices: pd.DataFrame, threshold: float) -> pd.DataFrame:
    parts = []
    for _, group in prices.groupby("segment_id", sort=False):
        group = group.copy()
        group["adjusted_close_return"] = group.adj_close.pct_change(fill_method=None)
        parts.append(group.loc[group.adjusted_close_return.abs().gt(threshold),
                               ["date", "symbol", "segment_id", "raw_close", "adj_close", "adjusted_close_return"]])
    return pd.concat(parts, ignore_index=True)


def save_csv(frame: pd.DataFrame, path: Path) -> None:
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(path, index=False, compression=compression,
                 date_format="%Y-%m-%d", float_format="%.12g", na_rep="")


def build(raw_path: Path, actions_path: Path, config_path: Path, output_dir: Path,
          outlier_reviews_path: Path = ROOT / "config/outlier_reviews.csv",
          universe_path: Path = ROOT / "config/nifty50_universe.json",
          bse_path: Path = ROOT / "data/raw/bse_nse_prices.csv",
          full_actions_path: Path = ROOT / "data/raw/nse_all_corporate_actions_snapshot.json",
          price_spotcheck_path: Path = ROOT / "data/raw/nse_workbook_price_spotcheck.json",
          outlier_snapshot_path: Path = ROOT / "data/raw/nse_outlier_action_snapshot.json") -> dict:
    config = load_config(config_path)
    actions = load_actions(actions_path)
    prices, counts = read_raw_prices(raw_path, config["be_initial_days"])
    official_universe = json.loads(universe_path.read_text(encoding="utf-8"))
    official_symbols = set(official_universe["symbols"])
    workbook_kept = len(prices)
    excluded_by_universe = int((~prices.symbol.isin(official_symbols)).sum())
    prices = prices.loc[prices.symbol.isin(official_symbols)].copy()
    prices["source_url"] = pd.NA
    supplement = read_bse_prices(bse_path)
    if "BSE" in set(prices.symbol):
        raise ValueError("BSE exists in both the workbook and supplemental NSE source")
    prices = pd.concat([prices, supplement], ignore_index=True).sort_values(
        ["symbol", "date"]
    ).reset_index(drop=True)
    counts["workbook_raw_rows"] = counts["raw_rows"]
    counts["workbook_kept_before_universe_filter"] = workbook_kept
    counts["workbook_excluded_by_universe"] = excluded_by_universe
    counts["supplemental_nse_bse_rows"] = len(supplement)
    counts["raw_rows"] += len(supplement)
    counts["excluded_rows"] += excluded_by_universe
    counts["kept_rows"] = len(prices)
    quality = validate_prices(prices, config)
    universe_check = check_universe(set(prices.symbol), universe_path)
    action_coverage = check_action_coverage(actions, full_actions_path, official_symbols)
    workbook_spotcheck = check_workbook_spotcheck(raw_path, price_spotcheck_path)
    if counts["raw_rows"] != counts["kept_rows"] + counts["excluded_rows"]:
        raise ValueError("Raw-record counts do not reconcile")
    if not set(actions.symbol).issubset(set(prices.symbol)):
        raise ValueError("Corporate-action ledger contains unknown symbols")
    missing_ex_dates = actions.merge(
        prices[["symbol", "date"]], left_on=["symbol", "ex_date"],
        right_on=["symbol", "date"], how="left",
    ).date.isna()
    if missing_ex_dates.any():
        raise ValueError("Corporate-action ex-date missing from raw stock history")
    prices = apply_actions(prices, actions)
    features = create_features(prices)
    if not features[["date", "symbol"]].equals(prices[["date", "symbol"]]):
        raise ValueError("Feature alignment changed")
    one_day = create_targets(features, 1, config)
    five_day = create_targets(features, 5, config)
    outliers = outlier_report(prices, config["outlier_absolute_return_threshold"])
    reviews = pd.read_csv(outlier_reviews_path, parse_dates=["date"])
    if reviews[["symbol", "date"]].duplicated().any():
        raise ValueError("Duplicate outlier-review key")
    outliers = outliers.merge(reviews, on=["symbol", "date"], how="left", validate="one_to_one")
    outlier_snapshot_check = check_outlier_snapshot(outliers, outlier_snapshot_path)
    unresolved_outliers = int(outliers.review_status.isna().sum() + outliers.review_status.eq("nse_action_requires_review").sum())
    regular_actions = ~actions.action_type.isin(SEGMENT_BOUNDARY_TYPES)
    actions_verified = bool(
        actions.loc[regular_actions, "verification_status"].eq("nse_ratio_verified").all()
        and actions.loc[~regular_actions, "verification_status"].eq("nse_event_date_verified").all()
    )
    stock_dictionary = pd.DataFrame(quality["coverage"])
    stock_dictionary["historical_name_note"] = stock_dictionary.symbol.map({
        "ETERNAL": "Previously Zomato; verify continuity by security identifier",
        "TMPV": "Earlier Tata Motors; demerger creates a new analytical segment",
    }).fillna("")
    stock_dictionary["universe_rule"] = "Official Nifty 50 symbols as of 2026-10-15; historical membership not asserted"
    stock_dictionary["current_isin"] = stock_dictionary.symbol.map(official_universe["current_isin_by_symbol"])
    stock_dictionary["security_identifier_status"] = "Current ISIN from official constituent list; historical continuity not independently established"
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "daily_prices.csv.gz": prices,
        "features.csv.gz": features.drop(columns=["source_name", "source_excel_row", "source_url"]),
        "target_1d.csv.gz": one_day,
        "target_5d.csv.gz": five_day,
        "stock_dictionary.csv": stock_dictionary,
        "corporate_actions.csv": actions,
        "outlier_review.csv": outliers,
    }
    for filename, frame in outputs.items():
        save_csv(frame, output_dir / filename)
    release_gates = []
    if not universe_check["matches_official_snapshot"]:
        release_gates.insert(0, "Resolve constituent mismatch: missing "
                             f"{universe_check['missing_from_processed']}; extra "
                             f"{universe_check['extra_in_processed']}; revalidate any replacement price/action history")
    if not actions_verified:
        release_gates.append("Resolve corporate-action ledger entries without NSE verification")
    if unresolved_outliers:
        release_gates.append(f"Review {unresolved_outliers} price outliers without a clean NSE action check")
    quality_report = {
        "status": ("blocked_universe_mismatch" if not universe_check["matches_official_snapshot"]
                   else "finalized_for_downstream" if actions_verified and not unresolved_outliers
                   else "provisional_review_required"),
        "counts": counts,
        "stock_count": int(prices.symbol.nunique()),
        "price_rows": len(prices),
        "feature_ready_rows": int(features.feature_ready.sum()),
        "target_1d_rows": len(one_day),
        "target_5d_rows": len(five_day),
        "target_1d_by_split": one_day.split.value_counts().to_dict(),
        "target_5d_by_split": five_day.split.value_counts().to_dict(),
        "actions_by_type": actions.action_type.value_counts().to_dict(),
        "action_verification_status": actions.verification_status.value_counts().to_dict(),
        "actions_verified_under_segment_policy": actions_verified,
        "demerger_policy": config["demergers"],
        "rights_issue_policy": config["rights_issues"],
        "cash_dividends": config["cash_dividends"],
        "outliers_for_review": len(outliers),
        "outliers_unresolved": unresolved_outliers,
        "raw_source_provenance": "Original 49-symbol workbook download URL/date not supplied; BSE rows independently archived from dated NSE bhavcopies",
        "universe_check": universe_check,
        "action_coverage_check": action_coverage,
        "workbook_nse_price_spotcheck": workbook_spotcheck,
        "outlier_nse_action_check": outlier_snapshot_check,
        "documented_limitations": [
            "Original workbook download URL/date were not supplied; "
            f"{workbook_spotcheck['matched_symbol_dates']} workbook stock-date records match archived "
            f"NSE bhavcopies across {workbook_spotcheck['dates_checked']} dates",
            "Fixed 2026-10-15 constituents introduce survivorship bias in earlier years",
            "Cash dividends are excluded; targets are price returns",
            "Demerger and rights events terminate segments; any later backtest must close before an event or account for shareholder entitlements",
        ],
        "release_gates": release_gates,
        **quality,
    }
    (output_dir / "quality_report.json").write_text(
        json.dumps(quality_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "pipeline_version": config["version"],
        "generated_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "raw_path": str(raw_path.resolve()),
        "raw_sha256": sha256(raw_path),
        "bse_nse_raw_sha256": sha256(bse_path),
        "nse_full_action_snapshot_sha256": sha256(full_actions_path),
        "nse_workbook_spotcheck_sha256": sha256(price_spotcheck_path),
        "nse_outlier_snapshot_sha256": sha256(outlier_snapshot_path),
        "action_ledger_sha256": sha256(actions_path),
        "outlier_review_ledger_sha256": sha256(outlier_reviews_path),
        "nse_action_snapshot_sha256": sha256(ROOT / "data/raw/nse_corporate_actions_snapshot.json")
            if (ROOT / "data/raw/nse_corporate_actions_snapshot.json").exists() else None,
        "config_sha256": sha256(config_path),
        "universe_snapshot_sha256": sha256(universe_path),
        "download_date": None,
        "exact_download_url": None,
        "price_basis": "split/bonus-adjusted OHLC within demerger/rights segments; cash dividends excluded",
        "forecast_targets": {
            "1d": "adjusted close t+1 / adjusted close t - 1, within segment",
            "5d": "adjusted close t+5 / adjusted close t - 1, within segment",
        },
        "outputs_sha256": {
            name: sha256(output_dir / name) for name in [*outputs, "quality_report.json"]
        },
        "release_note": ("Official current-constituent symbols are complete" if universe_check["matches_official_snapshot"]
                         else "Current-constituent symbols remain incomplete") +
                        "; the original workbook download URL/date and demerger/rights entitlement treatment are documented limitations.",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return quality_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw/50Nifty50.xlsx")
    parser.add_argument("--actions", type=Path, default=ROOT / "config/corporate_actions.csv")
    parser.add_argument("--config", type=Path, default=ROOT / "config/data_pipeline.json")
    parser.add_argument("--outlier-reviews", type=Path, default=ROOT / "config/outlier_reviews.csv")
    parser.add_argument("--universe", type=Path, default=ROOT / "config/nifty50_universe.json")
    parser.add_argument("--bse", type=Path, default=ROOT / "data/raw/bse_nse_prices.csv")
    parser.add_argument("--full-actions", type=Path, default=ROOT / "data/raw/nse_all_corporate_actions_snapshot.json")
    parser.add_argument("--price-spotcheck", type=Path, default=ROOT / "data/raw/nse_workbook_price_spotcheck.json")
    parser.add_argument("--outlier-snapshot", type=Path, default=ROOT / "data/raw/nse_outlier_action_snapshot.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed")
    args = parser.parse_args()
    report = build(args.raw, args.actions, args.config, args.output, args.outlier_reviews,
                   args.universe, args.bse, args.full_actions, args.price_spotcheck,
                   args.outlier_snapshot)
    print(json.dumps({key: report[key] for key in (
        "status", "price_rows", "feature_ready_rows", "target_1d_rows",
        "target_5d_rows", "outliers_for_review",
    )}, indent=2))


if __name__ == "__main__":
    main()
