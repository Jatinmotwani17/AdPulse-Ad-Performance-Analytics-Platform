"""
Unit tests for anomaly_detection.py
=====================================
Each test builds small in-memory DataFrames with known values and checks
that baselines and anomaly flags are computed correctly. Z-scores are
computed in log space (np.log1p) to handle right-skewed CPC/Traffic data.
No database calls, no file reads — pure data-driven tests.
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
import pytest

# Add parent directory to path so we can import anomaly_detection
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anomaly_detection import compute_keyword_baselines, detect_anomalies


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Helper to build a DataFrame from row dicts."""
    return pd.DataFrame(rows)


# --- Test 1: Baseline computation produces correct log-space mean and std ---
def test_baseline_computation():
    """A keyword with known CPC values should produce correct log-space stats."""
    df = _make_df([
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 2.0, "search_volume": 1000, "Traffic": 50, "last_seen": "2025-01-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 4.0, "search_volume": 1000, "Traffic": 60, "last_seen": "2025-02-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 6.0, "search_volume": 1000, "Traffic": 70, "last_seen": "2025-03-01"},
    ])
    baselines = compute_keyword_baselines(df, min_data_points=3)

    assert len(baselines) == 1
    row = baselines.iloc[0]
    assert row["Keyword"] == "pmp"

    # Log-space mean of log1p([2, 4, 6]) = mean([1.099, 1.609, 1.946])
    log_cpc_values = np.log1p(np.array([2.0, 4.0, 6.0]))
    assert row["log_cpc_mean"] == pytest.approx(log_cpc_values.mean(), abs=0.01)
    assert row["log_cpc_std"] == pytest.approx(log_cpc_values.std(ddof=1), abs=0.01)

    # Original-scale means still reported for readability
    assert row["cpc_mean"] == pytest.approx(4.0, abs=0.01)
    assert row["traffic_mean"] == pytest.approx(60.0, abs=0.01)
    assert row["data_points"] == 3


# --- Test 2: Normal value within 2 log-space std devs is NOT flagged ---
def test_normal_value_not_flagged():
    """A CPC value close to the mean should not be flagged as anomalous."""
    df = _make_df([
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.0, "search_volume": 1000, "Traffic": 50, "last_seen": "2025-01-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 4.0, "search_volume": 1000, "Traffic": 60, "last_seen": "2025-02-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 5.0, "search_volume": 1000, "Traffic": 70, "last_seen": "2025-03-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 4.2, "search_volume": 1000, "Traffic": 62, "last_seen": "2025-04-01"},
    ])
    baselines = compute_keyword_baselines(df, min_data_points=3)
    anomalies = detect_anomalies(df, baselines, sigma_threshold=2.0)

    # CPC 4.2 is within 2 log-space std devs of the baseline — should NOT be flagged
    assert len(anomalies) == 0


# --- Test 3: CPC spike correctly flagged in log space ---
def test_spike_correctly_flagged_as_cpc_spike():
    """A CPC value far above the baseline should be flagged as CPC_SPIKE."""
    # Build baselines from stable history data (no anomalies)
    history_df = _make_df([
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.0, "search_volume": 1000, "Traffic": 50, "last_seen": "2025-01-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.2, "search_volume": 1000, "Traffic": 55, "last_seen": "2025-02-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.1, "search_volume": 1000, "Traffic": 52, "last_seen": "2025-03-01"},
    ])
    baselines = compute_keyword_baselines(history_df, min_data_points=3)

    # Now detect on a dataset that includes the spike row
    full_df = pd.concat([history_df, _make_df([
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 20.0, "search_volume": 1000, "Traffic": 50, "last_seen": "2025-04-01"},
    ])], ignore_index=True)
    anomalies = detect_anomalies(full_df, baselines, sigma_threshold=2.0)

    assert len(anomalies) >= 1
    cpc_spikes = anomalies[anomalies["anomaly_type"] == "CPC_SPIKE"]
    assert len(cpc_spikes) >= 1
    assert cpc_spikes.iloc[0]["Keyword"] == "pmp"
    # Original-scale CPC is still 20.0 in the output
    assert cpc_spikes.iloc[0]["CPC"] == 20.0


# --- Test 4: Traffic drop correctly flagged in log space ---
def test_drop_correctly_flagged_as_traffic_drop():
    """A traffic value far below the baseline should be flagged as TRAFFIC_DROP."""
    # Build baselines from stable history data
    history_df = _make_df([
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.0, "search_volume": 1000, "Traffic": 500, "last_seen": "2025-01-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.1, "search_volume": 1000, "Traffic": 510, "last_seen": "2025-02-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.2, "search_volume": 1000, "Traffic": 490, "last_seen": "2025-03-01"},
    ])
    baselines = compute_keyword_baselines(history_df, min_data_points=3)

    # Now detect on a dataset that includes the drop row
    full_df = pd.concat([history_df, _make_df([
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.0, "search_volume": 1000, "Traffic": 10, "last_seen": "2025-04-01"},
    ])], ignore_index=True)
    anomalies = detect_anomalies(full_df, baselines, sigma_threshold=2.0)

    assert len(anomalies) >= 1
    traffic_drops = anomalies[anomalies["anomaly_type"] == "TRAFFIC_DROP"]
    assert len(traffic_drops) >= 1
    assert traffic_drops.iloc[0]["Traffic"] == 10


# --- Test 5: Keyword with insufficient history excluded from baselines ---
def test_keyword_with_insufficient_history_excluded():
    """A keyword with only 1-2 data points should be excluded from baselines."""
    df = _make_df([
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.0, "search_volume": 1000, "Traffic": 50, "last_seen": "2025-01-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.2, "search_volume": 1000, "Traffic": 55, "last_seen": "2025-02-01"},
        {"keyword_ID": 1, "Keyword": "pmp", "CPC": 3.1, "search_volume": 1000, "Traffic": 52, "last_seen": "2025-03-01"},
        {"keyword_ID": 2, "Keyword": "new_keyword", "CPC": 5.0, "search_volume": 500, "Traffic": 20, "last_seen": "2025-04-01"},
        # new_keyword has only 1 data point — should be excluded
    ])
    baselines = compute_keyword_baselines(df, min_data_points=3)

    # Only "pmp" should have a baseline (3 data points)
    assert len(baselines) == 1
    assert baselines.iloc[0]["Keyword"] == "pmp"
    assert "new_keyword" not in baselines["Keyword"].values
