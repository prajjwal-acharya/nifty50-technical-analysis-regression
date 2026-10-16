"""Shared keys, release checks and chronological folds for current models."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
KEY = ["date", "symbol", "segment_id"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expanding_folds(table: pd.DataFrame, years: list[int]):
    """Fit only labels matured before each scored calendar year."""
    training_rows = table.loc[table.split.eq("train")]
    folds = []
    for year in years:
        fit_end = pd.Timestamp(year - 1, 12, 31)
        score_start = pd.Timestamp(year, 1, 1)
        score_end = pd.Timestamp(year, 12, 31)
        fit = training_rows.loc[(training_rows.date <= fit_end)
                                & (training_rows.target_date <= fit_end)]
        score = training_rows.loc[training_rows.date.between(score_start, score_end)
                                  & (training_rows.target_date <= score_end)]
        if fit.empty or score.empty:
            raise ValueError(f"Empty chronological fold for {year}")
        folds.append((year, fit, score))
    return folds
