"""
Data Validation Layer
=====================
Before loading data into MySQL, we validate it. This catches problems
early -- corrupted rows, missing values, duplicates, out-of-range values --
instead of silently loading bad data into the database.

Each check returns a pass/warning/fail status. Critical failures
(negative CPC, negative search volume, missing columns) raise
ValidationError and halt the pipeline. Warnings (duplicates, nulls,
out-of-range positions) are logged but allow loading to continue.
"""

import pandas as pd


class ValidationError(Exception):
    """Raised when data fails a critical validation check."""
    pass


def validate_keyword_data(df: pd.DataFrame) -> dict:
    """
    Runs a series of checks on the raw keyword dataframe.
    Returns a report dict. Raises ValidationError on critical failures.

    Checks performed:
        1. Required columns exist
        2. No fully-null Keyword fields
        3. No duplicate Keyword entries (warns, doesn't block)
        4. CPC values within [0, 1000] range
        5. Search Volume is non-negative
        6. Position is within [1, 100] range
    """
    report = {
        "total_rows": len(df),
        "checks": []
    }

    # --- Check 1: Required columns exist ---
    required_cols = ["Keyword", "Position", "Search Volume", "CPC", "Traffic"]
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        raise ValidationError(f"Missing required columns: {missing_cols}")
    report["checks"].append({"check": "required_columns", "status": "pass"})

    # --- Check 2: No fully-null critical fields ---
    null_keywords = df["Keyword"].isnull().sum()
    report["checks"].append({
        "check": "null_keywords",
        "count": int(null_keywords),
        "status": "pass" if null_keywords == 0 else "warning"
    })

    # --- Check 3: No duplicate records (same keyword + position + date) ---
    # A true duplicate means the same keyword appeared at the same position
    # on the same date — likely a data feed error. The same keyword can
    # legitimately recur across different campaigns, positions, or dates.
    dup_cols = ["Keyword", "Position", "Last Seen"]
    missing_dup_cols = [c for c in dup_cols if c not in df.columns]
    if not missing_dup_cols:
        duplicates = df.duplicated(subset=dup_cols).sum()
        report["checks"].append({
            "check": "duplicate_records",
            "count": int(duplicates),
            "status": "pass" if duplicates == 0 else "warning"
        })
    else:
        # Fallback: check on Keyword alone if columns are missing
        duplicates = df.duplicated(subset=["Keyword"]).sum()
        report["checks"].append({
            "check": "duplicate_keywords",
            "count": int(duplicates),
            "status": "pass" if duplicates == 0 else "warning"
        })

    # --- Check 4: CPC values are non-negative and within reasonable range ---
    invalid_cpc = df[(df["CPC"] < 0) | (df["CPC"] > 1000)].shape[0]
    report["checks"].append({
        "check": "cpc_range",
        "invalid_count": int(invalid_cpc),
        "status": "pass" if invalid_cpc == 0 else "fail"
    })
    if invalid_cpc > 0:
        raise ValidationError(
            f"{invalid_cpc} rows have CPC outside valid range [0, 1000]"
        )

    # --- Check 5: Search Volume is non-negative ---
    invalid_volume = df[df["Search Volume"] < 0].shape[0]
    report["checks"].append({
        "check": "search_volume_non_negative",
        "invalid_count": int(invalid_volume),
        "status": "pass" if invalid_volume == 0 else "fail"
    })
    if invalid_volume > 0:
        raise ValidationError(
            f"{invalid_volume} rows have negative Search Volume"
        )

    # --- Check 6: Position is a reasonable rank (1-100 typically) ---
    invalid_position = df[(df["Position"] < 1) | (df["Position"] > 100)].shape[0]
    report["checks"].append({
        "check": "position_range",
        "invalid_count": int(invalid_position),
        "status": "pass" if invalid_position == 0 else "warning"
    })

    # --- Overall status: fail if any check has status "fail" ---
    report["overall_status"] = "pass" if all(
        c["status"] != "fail" for c in report["checks"]
    ) else "fail"

    return report


def print_validation_report(report: dict):
    """Pretty-print the validation report to console."""
    print(f"\nValidation Report — {report['total_rows']} rows")
    print("-" * 50)
    for check in report["checks"]:
        if check["status"] == "pass":
            status_icon = "[PASS]"
        elif check["status"] == "warning":
            status_icon = "[WARN]"
        else:
            status_icon = "[FAIL]"
        detail = check.get("count", check.get("invalid_count", "ok"))
        print(f"{status_icon} {check['check']}: {detail}")
    print(f"\nOverall: {report['overall_status'].upper()}")
