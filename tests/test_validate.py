"""
Unit tests for validate.py
===========================
Each test builds a small in-memory DataFrame with a specific problem
and checks that validation correctly catches (or warns about) it.
No files are read from disk — these are pure data-driven tests.
"""

import pandas as pd
import pytest
import sys
from pathlib import Path

# Add parent directory to path so we can import validate.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validate import validate_keyword_data, ValidationError


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Helper to build a DataFrame from a list of row dicts."""
    return pd.DataFrame(rows)


# --- Test 1: Clean data passes validation ---
def test_valid_data_passes():
    df = _make_df([
        {"Keyword": "pmp certification", "Position": 1, "Search Volume": 90500, "CPC": 3.76, "Traffic": 4253},
        {"Keyword": "scrum master", "Position": 3, "Search Volume": 18100, "CPC": 3.21, "Traffic": 850},
        {"Keyword": "itil certification", "Position": 2, "Search Volume": 14800, "CPC": 2.33, "Traffic": 695},
    ])
    report = validate_keyword_data(df)
    assert report["overall_status"] == "pass"
    assert report["total_rows"] == 3


# --- Test 2: Missing required column raises ValidationError ---
def test_missing_column_raises():
    df = _make_df([
        {"Keyword": "pmp", "Position": 1, "Search Volume": 90500, "Traffic": 4253},
        # Missing "CPC" column
    ])
    with pytest.raises(ValidationError, match="Missing required columns"):
        validate_keyword_data(df)


# --- Test 3: Negative CPC raises ValidationError ---
def test_negative_cpc_raises():
    df = _make_df([
        {"Keyword": "pmp", "Position": 1, "Search Volume": 90500, "CPC": 3.76, "Traffic": 4253},
        {"Keyword": "scrum", "Position": 2, "Search Volume": 18100, "CPC": -5, "Traffic": 850},
    ])
    with pytest.raises(ValidationError, match="CPC outside valid range"):
        validate_keyword_data(df)


# --- Test 4: Negative Search Volume raises ValidationError ---
def test_negative_search_volume_raises():
    df = _make_df([
        {"Keyword": "pmp", "Position": 1, "Search Volume": 90500, "CPC": 3.76, "Traffic": 4253},
        {"Keyword": "scrum", "Position": 2, "Search Volume": -100, "CPC": 3.21, "Traffic": 850},
    ])
    with pytest.raises(ValidationError, match="negative Search Volume"):
        validate_keyword_data(df)


# --- Test 5: Null keyword is flagged as warning but doesn't raise ---
def test_null_keyword_flagged_as_warning():
    df = _make_df([
        {"Keyword": "pmp", "Position": 1, "Search Volume": 90500, "CPC": 3.76, "Traffic": 4253},
        {"Keyword": None, "Position": 2, "Search Volume": 18100, "CPC": 3.21, "Traffic": 850},
    ])
    report = validate_keyword_data(df)
    null_check = next(c for c in report["checks"] if c["check"] == "null_keywords")
    assert null_check["status"] == "warning"
    assert null_check["count"] == 1
    # Should not raise — warnings don't block loading
    assert report["overall_status"] == "pass"


# --- Test 6: Duplicate records (same keyword + position + date) flagged as warning ---
def test_duplicate_keywords_flagged_as_warning():
    df = _make_df([
        {"Keyword": "pmp", "Position": 1, "Last Seen": "2025-01-01", "Search Volume": 90500, "CPC": 3.76, "Traffic": 4253},
        {"Keyword": "pmp", "Position": 1, "Last Seen": "2025-01-01", "Search Volume": 90500, "CPC": 3.76, "Traffic": 4253},  # exact duplicate
        {"Keyword": "pmp", "Position": 3, "Last Seen": "2025-01-01", "Search Volume": 90500, "CPC": 3.82, "Traffic": 814},  # same keyword, different position
        {"Keyword": "scrum", "Position": 1, "Last Seen": "2025-01-01", "Search Volume": 18100, "CPC": 3.21, "Traffic": 850},
    ])
    report = validate_keyword_data(df)
    dup_check = next(c for c in report["checks"] if c["check"] == "duplicate_records")
    assert dup_check["status"] == "warning"
    assert dup_check["count"] == 1  # only the exact duplicate is flagged
    assert report["overall_status"] == "pass"


# --- Test 7: Out-of-range position flagged as warning but doesn't raise ---
def test_out_of_range_position_flagged_as_warning():
    df = _make_df([
        {"Keyword": "pmp", "Position": 1, "Search Volume": 90500, "CPC": 3.76, "Traffic": 4253},
        {"Keyword": "scrum", "Position": 150, "Search Volume": 18100, "CPC": 3.21, "Traffic": 850},
    ])
    report = validate_keyword_data(df)
    pos_check = next(c for c in report["checks"] if c["check"] == "position_range")
    assert pos_check["status"] == "warning"
    assert pos_check["invalid_count"] == 1
    assert report["overall_status"] == "pass"
