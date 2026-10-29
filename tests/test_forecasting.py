"""
Unit tests for forecasting.py
================================
Each test builds small in-memory DataFrames to verify train/test splitting,
prediction on known trends, negative clamping, and MAE computation. No
database calls, no file reads — pure data-driven tests.
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
import pytest

# Add parent directory to path so we can import forecasting
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forecasting import (
    prepare_train_test_split,
    train_and_predict,
    compute_summary_metrics,
    load_and_aggregate_monthly,
    main as forecasting_main,
)


def _make_monthly_df(rows: list[dict]) -> pd.DataFrame:
    """Helper to build a monthly aggregation DataFrame."""
    df = pd.DataFrame(rows)
    df["year_month"] = pd.to_datetime(df["year_month"]).dt.to_period("M")
    return df


# --- Test 1: Train/test split holds out the last month ---
def test_train_test_split_holds_out_last_month():
    """The split should use all-but-last for training, last for test."""
    df = _make_monthly_df([
        {"Keyword": "pmp", "year_month": "2025-01", "avg_traffic": 100},
        {"Keyword": "pmp", "year_month": "2025-02", "avg_traffic": 110},
        {"Keyword": "pmp", "year_month": "2025-03", "avg_traffic": 120},
        {"Keyword": "pmp", "year_month": "2025-04", "avg_traffic": 130},
    ])
    keyword_data = prepare_train_test_split(df)

    assert "pmp" in keyword_data
    data = keyword_data["pmp"]

    # Training should have 3 months, test should be month 4
    assert len(data["train"]) == 3
    assert str(data["test_month"]) == "2025-04"
    assert data["test_actual"] == 130

    # Training months should be Jan, Feb, Mar (not Apr)
    train_months = data["train"]["year_month"].tolist()
    assert pd.Period("2025-04") not in train_months
    assert pd.Period("2025-01") in train_months


# --- Test 2: Prediction on a perfectly linear trend should be accurate ---
def test_prediction_on_perfectly_linear_trend():
    """Traffic increasing by exactly 10 each month should be predicted accurately."""
    df = _make_monthly_df([
        {"Keyword": "linear", "year_month": "2025-01", "avg_traffic": 100},
        {"Keyword": "linear", "year_month": "2025-02", "avg_traffic": 110},
        {"Keyword": "linear", "year_month": "2025-03", "avg_traffic": 120},
        {"Keyword": "linear", "year_month": "2025-04", "avg_traffic": 130},
        {"Keyword": "linear", "year_month": "2025-05", "avg_traffic": 140},
        {"Keyword": "linear", "year_month": "2025-06", "avg_traffic": 150},
    ])
    keyword_data = prepare_train_test_split(df)
    results = train_and_predict(keyword_data)

    assert len(results) == 1
    row = results.iloc[0]

    # Perfect linear trend: months 1-5 = [100,110,120,130,140], predict month 6 = 150
    # LinearRegression on [0,1,2,3,4] -> [100,110,120,130,140] should predict ~150 for x=5
    assert row["actual_traffic"] == 150
    assert abs(row["predicted_traffic"] - 150) < 1  # should be very close
    assert row["absolute_error"] < 1


# --- Test 3: Negative predictions are clamped to zero ---
def test_negative_predictions_clamped_to_zero():
    """Predicted traffic should never be negative — clamp to 0."""
    # Trend going sharply DOWN then suddenly UP — the down-trend model
    # will predict a negative value for the test month
    df = _make_monthly_df([
        {"Keyword": "declining", "year_month": "2025-01", "avg_traffic": 100},
        {"Keyword": "declining", "year_month": "2025-02", "avg_traffic": 50},
        {"Keyword": "declining", "year_month": "2025-03", "avg_traffic": 20},
        {"Keyword": "declining", "year_month": "2025-04", "avg_traffic": 5},  # test month
    ])
    keyword_data = prepare_train_test_split(df)
    results = train_and_predict(keyword_data)

    assert len(results) == 1
    row = results.iloc[0]
    # Predicted should be >= 0 (clamped), even though the trend was negative
    assert row["predicted_traffic"] >= 0


# --- Test 4: MAE computation is correct ---
def test_mae_computation():
    """With known predicted/actual values, MAE should be computed correctly."""
    results_df = pd.DataFrame([
        {"keyword": "a", "months_trained_on": 5, "predicted_traffic": 100,
         "actual_traffic": 80, "absolute_error": 20, "test_month": "2025-06"},
        {"keyword": "b", "months_trained_on": 6, "predicted_traffic": 200,
         "actual_traffic": 250, "absolute_error": 50, "test_month": "2025-06"},
        {"keyword": "c", "months_trained_on": 7, "predicted_traffic": 50,
         "actual_traffic": 60, "absolute_error": 10, "test_month": "2025-06"},
    ])
    summary = compute_summary_metrics(results_df)

    # MAE = mean(|100-80|, |200-250|, |50-60|) = mean(20, 50, 10) = 26.67
    assert summary["mae"] == pytest.approx(26.67, abs=0.01)
    assert summary["keywords_forecasted"] == 3
    # Avg actual = (80 + 250 + 60) / 3 = 130
    assert summary["avg_actual_traffic"] == pytest.approx(130.0, abs=0.01)
    # MAE as % = 26.67 / 130 * 100 ≈ 20.5%
    assert summary["mae_as_pct_of_avg_traffic"] == pytest.approx(20.5, abs=0.1)


# --- Test 5: main() exits gracefully when history is too short ---
def test_main_exits_gracefully_when_history_is_insufficient(tmp_path, monkeypatch):
    """A data source with fewer months than --min-months must warn and
    return, not crash on empty metrics (regression: KeyError on
    'actual_traffic' when zero keywords were forecastable)."""
    csv_path = tmp_path / "short_history.csv"
    pd.DataFrame({
        "Keyword": ["alpha", "alpha", "beta", "beta"],
        "Traffic": [100, 110, 50, 60],
        "Last Seen": ["2025-01-05", "2025-02-05", "2025-01-05", "2025-02-05"],
    }).to_csv(csv_path, index=False)

    monkeypatch.setattr(sys, "argv", ["forecasting.py", "--csv", str(csv_path)])
    forecasting_main()  # must return without raising
