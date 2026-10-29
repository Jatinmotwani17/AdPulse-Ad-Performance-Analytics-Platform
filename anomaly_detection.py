"""
Anomaly Detection Layer
========================
For each keyword, we calculate its own normal range for CPC and Traffic
based on its historical values (mean +/- 2 standard deviations). Any data
point outside that range is flagged as anomalous. This is per-keyword
because what's "normal" varies hugely between keywords — a CPC of $15
is normal for one keyword, alarming for another.

WHY 2 STANDARD DEVIATIONS:
In a normal distribution, ~95% of values fall within 2 std devs of the
mean. Flagging beyond that catches genuinely unusual events without
drowning the report in noise from ordinary variation. This threshold
is configurable — 2 sigma is a reasonable default balancing sensitivity
against false-alarm rate.

RUN MODES:
- MySQL mode (default): loads data from MySQL, stores results in MySQL
- CSV fallback (--csv flag or no MySQL): reads from Raw_data_scaled.xlsx,
  prints results to console only. Useful when MySQL isn't loaded yet.

Usage:
    python anomaly_detection.py              # MySQL mode
    python anomaly_detection.py --csv        # CSV fallback mode
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

# Optional MySQL imports — only needed in MySQL mode
try:
    import mysql.connector
    from dotenv import load_dotenv
    import os
    load_dotenv(Path(__file__).resolve().parent / ".env")
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Database connection (MySQL mode only)
# ---------------------------------------------------------------------------

def get_db_connection():
    """Create a MySQL connection using environment variables."""
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
    )


# ---------------------------------------------------------------------------
# Data loading — supports both MySQL and CSV
# ---------------------------------------------------------------------------

def load_traffic_data_from_mysql() -> pd.DataFrame:
    """Load the full fact table with keyword info joined in for readability."""
    conn = get_db_connection()
    query = """
        SELECT
            w.keyword_ID,
            k.keyword AS Keyword,
            w.CPC,
            w.Search_Volume AS search_volume,
            w.Traffic,
            w.last_seen AS last_seen
        FROM website_traffic_data w
        JOIN keyword k ON w.keyword_ID = k.keyword_ID
        ORDER BY w.keyword_ID, w.last_seen
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df


def load_traffic_data_from_csv(csv_path: str) -> pd.DataFrame:
    """Load data directly from the scaled Excel/CSV file (fallback mode)."""
    path = Path(csv_path)
    if path.suffix == ".xlsx":
        df = pd.read_excel(str(path))
    else:
        df = pd.read_csv(str(path))

    # Normalize column names to match what the rest of the script expects
    df = df.rename(columns={
        "Keyword ID": "keyword_ID",
        "Search Volume": "search_volume",
        "Last Seen": "last_seen",
    })

    # Ensure keyword_ID exists — generate from keyword if missing
    if "keyword_ID" not in df.columns:
        df["keyword_ID"] = df["Keyword"].astype("category").cat.codes

    # Ensure last_seen is datetime
    df["last_seen"] = pd.to_datetime(df["last_seen"], errors="coerce")

    return df[["keyword_ID", "Keyword", "CPC", "search_volume", "Traffic", "last_seen"]].dropna()


def load_traffic_data(csv_path: str = None) -> pd.DataFrame:
    """
    Smart loader: tries MySQL first, falls back to CSV if MySQL is
    unavailable or the --csv flag is passed.
    """
    if csv_path:
        print(f"Loading from CSV: {csv_path}")
        return load_traffic_data_from_csv(csv_path)

    if MYSQL_AVAILABLE:
        try:
            print("Connecting to MySQL...")
            df = load_traffic_data_from_mysql()
            if len(df) > 0:
                print(f"Loaded {len(df)} records from MySQL")
                return df
            else:
                print("MySQL table is empty, falling back to CSV...")
        except Exception as e:
            print(f"MySQL connection failed ({e}), falling back to CSV...")

    # CSV fallback — look for the scaled dataset
    base_dir = Path(__file__).resolve().parent
    fallback_path = base_dir / "data" / "raw" / "Raw_data_scaled.xlsx"
    if fallback_path.exists():
        print(f"Loading from CSV fallback: {fallback_path}")
        return load_traffic_data_from_csv(str(fallback_path))

    # Last resort — original small dataset
    original_path = base_dir / "data" / "raw" / "Raw_data.xlsx"
    if original_path.exists():
        print(f"Loading original dataset: {original_path}")
        return load_traffic_data_from_csv(str(original_path))

    raise FileNotFoundError("No data source found. Run generate_synthetic_data.py first.")


# ---------------------------------------------------------------------------
# Baseline computation
# ---------------------------------------------------------------------------

def compute_keyword_baselines(df: pd.DataFrame, min_data_points: int = 3) -> pd.DataFrame:
    """
    For each keyword, compute mean and std dev of CPC and Traffic.
    Keywords with fewer than min_data_points historical records are
    excluded — can't establish a meaningful baseline from 1-2 data points.

    LOG TRANSFORM: CPC and Traffic are right-skewed real-world metrics
    (most keywords are cheap/low-traffic, a few are expensive/high-traffic).
    Z-score anomaly detection assumes roughly normal distributions. By
    applying np.log1p() before computing mean/std, we correct for this
    skew so the threshold behaves symmetrically — catching both unusually
    high AND unusually low values at the expected ~5% rate for 2-sigma.

    WHY min_data_points=3:
    With only 1-2 observations, the standard deviation is unreliable or
    undefined. Three points is the minimum for a rough but meaningful
    estimate of variance.
    """
    # Work on log-transformed values for baseline computation
    df = df.copy()
    df["log_cpc"] = np.log1p(df["CPC"])
    df["log_traffic"] = np.log1p(df["Traffic"])

    grouped = df.groupby("Keyword").agg(
        # Log-space stats (used for z-score computation)
        log_cpc_mean=("log_cpc", "mean"),
        log_cpc_std=("log_cpc", "std"),
        log_traffic_mean=("log_traffic", "mean"),
        log_traffic_std=("log_traffic", "std"),
        # Original-scale stats (for human-readable output)
        cpc_mean=("CPC", "mean"),
        traffic_mean=("Traffic", "mean"),
        data_points=("CPC", "count"),
    ).reset_index()

    grouped = grouped[grouped["data_points"] >= min_data_points]

    # Handle keywords with zero variance (std=0) — replace with small
    # epsilon to avoid division by zero in z-score calculation
    grouped["log_cpc_std"] = grouped["log_cpc_std"].fillna(0).replace(0, 0.01)
    grouped["log_traffic_std"] = grouped["log_traffic_std"].fillna(0).replace(0, 0.01)

    return grouped


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

def detect_anomalies(
    df: pd.DataFrame,
    baselines: pd.DataFrame,
    sigma_threshold: float = 2.0,
) -> pd.DataFrame:
    """
    Compare every data point against its keyword's baseline. Flag anything
    beyond sigma_threshold standard deviations as anomalous.

    Z-scores are computed in LOG SPACE (see compute_keyword_baselines)
    because CPC and Traffic are right-skewed. The reported CPC/Traffic
    values and means are in original scale for readability.

    Anomaly types:
        CPC_SPIKE    — CPC significantly above the keyword's normal range
        CPC_DROP     — CPC significantly below normal (unusually cheap)
        TRAFFIC_DROP — Traffic significantly below normal (unexpected decline)
        TRAFFIC_SPIKE — Traffic significantly above normal (unexpected surge)

    A single record can theoretically be both a CPC spike and a traffic drop
    (e.g., keyword got expensive but traffic tanked). In practice this is rare
    and both flags are kept for maximum visibility.
    """
    merged = df.merge(baselines, on="Keyword", how="inner")

    # Compute z-scores in LOG SPACE: log1p(value) vs log-space mean/std
    # This corrects for right-skew in CPC and Traffic distributions
    merged["cpc_z_score"] = (
        (np.log1p(merged["CPC"]) - merged["log_cpc_mean"]) / merged["log_cpc_std"]
    )
    merged["traffic_z_score"] = (
        (np.log1p(merged["Traffic"]) - merged["log_traffic_mean"]) / merged["log_traffic_std"]
    )

    # Flag anomalies: |z| > threshold
    merged["cpc_anomaly"] = merged["cpc_z_score"].abs() > sigma_threshold
    merged["traffic_anomaly"] = merged["traffic_z_score"].abs() > sigma_threshold

    # Classify by direction (spike vs drop)
    merged["anomaly_type"] = None
    merged.loc[merged["cpc_anomaly"] & (merged["cpc_z_score"] > 0), "anomaly_type"] = "CPC_SPIKE"
    merged.loc[merged["cpc_anomaly"] & (merged["cpc_z_score"] < 0), "anomaly_type"] = "CPC_DROP"
    merged.loc[merged["traffic_anomaly"] & (merged["traffic_z_score"] < 0), "anomaly_type"] = "TRAFFIC_DROP"
    merged.loc[merged["traffic_anomaly"] & (merged["traffic_z_score"] > 0), "anomaly_type"] = "TRAFFIC_SPIKE"

    # Keep only anomalous rows — report CPC/Traffic in original scale
    anomalies = merged[merged["cpc_anomaly"] | merged["traffic_anomaly"]].copy()

    return anomalies[[
        "keyword_ID", "Keyword", "last_seen", "CPC", "cpc_mean",
        "cpc_z_score", "Traffic", "traffic_mean", "traffic_z_score",
        "anomaly_type",
    ]]


# ---------------------------------------------------------------------------
# Storage — MySQL only
# ---------------------------------------------------------------------------

def store_anomalies(anomalies: pd.DataFrame):
    """
    Create the detected_anomalies table if needed, then insert all
    detected anomalies. The table is truncated before each run so
    only the latest detection results are kept (idempotent).
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detected_anomalies (
            anomaly_id INT AUTO_INCREMENT PRIMARY KEY,
            keyword_ID INT,
            keyword_text VARCHAR(255),
            detected_date DATE,
            cpc FLOAT,
            cpc_baseline_mean FLOAT,
            cpc_z_score FLOAT,
            traffic INT,
            traffic_baseline_mean FLOAT,
            traffic_z_score FLOAT,
            anomaly_type VARCHAR(50),
            detection_run_at DATETIME
        )
    """)

    # Clear previous run's results before inserting new ones
    cursor.execute("TRUNCATE TABLE detected_anomalies")

    run_time = datetime.now()
    for _, row in anomalies.iterrows():
        cursor.execute("""
            INSERT INTO detected_anomalies
            (keyword_ID, keyword_text, detected_date, cpc, cpc_baseline_mean,
             cpc_z_score, traffic, traffic_baseline_mean, traffic_z_score,
             anomaly_type, detection_run_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            int(row["keyword_ID"]), row["Keyword"], row["last_seen"],
            float(row["CPC"]), float(row["cpc_mean"]), float(row["cpc_z_score"]),
            int(row["Traffic"]), float(row["traffic_mean"]), float(row["traffic_z_score"]),
            row["anomaly_type"], run_time,
        ))

    conn.commit()
    cursor.close()
    conn.close()
    print(f"  Stored {len(anomalies)} anomalies in MySQL (detected_anomalies table)")


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_summary_report(anomalies: pd.DataFrame, total_records: int, total_keywords: int):
    """Print a clear console summary of the anomaly detection run."""
    print("\n" + "=" * 60)
    print("  ANOMALY DETECTION REPORT")
    print("=" * 60)
    print(f"  Total records scanned:       {total_records:,}")
    print(f"  Total keywords analyzed:     {total_keywords:,}")
    print(f"  Anomalies detected:          {len(anomalies):,}")
    print(f"  Anomaly rate:                {len(anomalies) / total_records * 100:.2f}%")

    if len(anomalies) > 0:
        print(f"\n  Breakdown by type:")
        for atype, count in anomalies["anomaly_type"].value_counts().items():
            print(f"    {atype:20s} {count:,}")

        print(f"\n  Top 5 most extreme anomalies (by absolute z-score):")
        anomalies = anomalies.copy()
        anomalies["max_abs_z"] = anomalies[["cpc_z_score", "traffic_z_score"]].abs().max(axis=1)
        top5 = anomalies.nlargest(5, "max_abs_z")
        for i, (_, row) in enumerate(top5.iterrows(), 1):
            print(f"    {i}. [{row['anomaly_type']}] {row['Keyword']}")
            print(f"       CPC: ${row['CPC']:.2f} (baseline ${row['cpc_mean']:.2f}, z={row['cpc_z_score']:.2f})")
            print(f"       Traffic: {int(row['Traffic']):,} (baseline {int(row['traffic_mean']):,}, z={row['traffic_z_score']:.2f})")
            print(f"       Date: {row['last_seen']}")

    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AdPulse Anomaly Detection")
    parser.add_argument(
        "--csv", type=str, default=None, nargs="?",
        const="auto",
        help="Use CSV/Excel file instead of MySQL. Pass a path or use default.",
    )
    parser.add_argument(
        "--sigma", type=float, default=2.0,
        help="Z-score threshold for anomaly flagging (default: 2.0)",
    )
    args = parser.parse_args()

    # Determine CSV path
    csv_path = None
    if args.csv == "auto" or args.csv is None:
        if not MYSQL_AVAILABLE:
            base_dir = Path(__file__).resolve().parent
            csv_path = str(base_dir / "data" / "raw" / "Raw_data_scaled.xlsx")
        elif args.csv:
            csv_path = args.csv
    else:
        csv_path = args.csv

    print("=" * 60)
    print("  AdPulse Anomaly Detection")
    print(f"  Threshold: {args.sigma} standard deviations")
    print("=" * 60)

    # Step 1: Load data
    df = load_traffic_data(csv_path)
    print(f"\n  Loaded {len(df):,} records across {df['Keyword'].nunique():,} keywords")

    # Step 2: Compute baselines
    print("\n  Computing per-keyword baselines...")
    baselines = compute_keyword_baselines(df)
    excluded = df["Keyword"].nunique() - len(baselines)
    print(f"  Baselines established for {len(baselines):,} keywords")
    if excluded > 0:
        print(f"  Excluded {excluded:,} keywords with < 3 data points")

    # Step 3: Detect anomalies
    print(f"\n  Detecting anomalies (threshold: {args.sigma} sigma)...")
    anomalies = detect_anomalies(df, baselines, sigma_threshold=args.sigma)

    # Step 4: Print report
    print_summary_report(anomalies, len(df), df["Keyword"].nunique())

    # Step 5: Store to MySQL (only if MySQL is available and we loaded from it)
    if MYSQL_AVAILABLE and csv_path is None:
        print("\n  Storing anomalies in MySQL...")
        store_anomalies(anomalies)
    else:
        print("\n  (MySQL not used — results printed to console only)")

    print("\nDone.")


if __name__ == "__main__":
    main()
