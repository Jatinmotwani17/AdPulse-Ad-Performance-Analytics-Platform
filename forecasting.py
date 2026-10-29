"""
Traffic Forecasting Module
===========================
For each keyword, we have up to 12 months of traffic data. We use months
1-11 as TRAINING data to fit a simple linear trend line, then use that
line to PREDICT month 12. Since we also know the ACTUAL month 12 value
(it's data we're holding out, not future data), we can measure how wrong
our prediction was -- this is called a "holdout test" and it's the honest
way to evaluate a forecast: never test on data the model was trained on.

MAE (Mean Absolute Error): average of |predicted - actual| across all
keywords. If MAE is 50, our forecasts are off by an average of 50 units
of traffic -- directly interpretable, same units as the data itself.

WHY LINEAR REGRESSION (not Prophet/ARIMA):
With one year of synthetic data and no strong seasonality signal, a
simple trend line is honest and sufficient to establish direction. More
complex models would add complexity without necessarily adding accuracy
here -- this is a deliberate scope decision, not a limitation.

RUN MODES:
- MySQL mode (default): loads data from MySQL, stores results in MySQL
- CSV fallback (--csv flag or no MySQL): reads from Raw_data_scaled.xlsx,
  prints results to console only. Useful when MySQL isn't loaded yet.
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error

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
    """Load keyword + traffic + date from MySQL with a JOIN for readability."""
    conn = get_db_connection()
    query = """
        SELECT
            k.Keyword,
            w.Traffic,
            w.last_seen AS last_seen
        FROM website_traffic_data w
        JOIN keyword k ON w.keyword_ID = k.keyword_ID
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

    # Normalize column names
    df = df.rename(columns={
        "Last Seen": "last_seen",
    })
    df["last_seen"] = pd.to_datetime(df["last_seen"], errors="coerce")

    return df[["Keyword", "Traffic", "last_seen"]].dropna()


def load_traffic_data(csv_path: str = None) -> pd.DataFrame:
    """
    Smart loader: tries MySQL first, falls back to CSV if MySQL is
    unavailable or the --csv flag is passed.
    """
    if csv_path:
        print(f"  Loading from CSV: {csv_path}")
        return load_traffic_data_from_csv(csv_path)

    if MYSQL_AVAILABLE:
        try:
            print("  Connecting to MySQL...")
            df = load_traffic_data_from_mysql()
            if len(df) > 0:
                print(f"  Loaded {len(df):,} records from MySQL")
                return df
            else:
                print("  MySQL table is empty, falling back to CSV...")
        except Exception as e:
            print(f"  MySQL connection failed ({e}), falling back to CSV...")

    # CSV fallback
    base_dir = Path(__file__).resolve().parent
    fallback_path = base_dir / "data" / "raw" / "Raw_data_scaled.xlsx"
    if fallback_path.exists():
        print(f"  Loading from CSV fallback: {fallback_path}")
        return load_traffic_data_from_csv(str(fallback_path))

    original_path = base_dir / "data" / "raw" / "Raw_data.xlsx"
    if original_path.exists():
        print(f"  Loading original dataset: {original_path}")
        return load_traffic_data_from_csv(str(original_path))

    raise FileNotFoundError("No data source found. Run generate_synthetic_data.py first.")


# ---------------------------------------------------------------------------
# Monthly aggregation
# ---------------------------------------------------------------------------

def load_and_aggregate_monthly(df: pd.DataFrame, min_months: int = 6) -> pd.DataFrame:
    """
    Aggregate each keyword's data into monthly buckets (average traffic
    per keyword per month). Keywords with fewer than min_months of
    distinct months are excluded — can't forecast meaningfully with
    too few data points.

    WHY min_months=6:
    With 12 months total, we need at least 6 months of training data
    (months 1-5) + 1 test month (month 6) to do a meaningful holdout.
    Requiring 6 months ensures the linear regression has enough points
    to establish a trend direction.
    """
    df = df.copy()
    df["last_seen"] = pd.to_datetime(df["last_seen"])
    df["year_month"] = df["last_seen"].dt.to_period("M")

    monthly = df.groupby(["Keyword", "year_month"]).agg(
        avg_traffic=("Traffic", "mean")
    ).reset_index()

    # Count how many distinct months each keyword has data for
    month_counts = monthly.groupby("Keyword")["year_month"].count()
    valid_keywords = month_counts[month_counts >= min_months].index

    monthly_filtered = monthly[monthly["Keyword"].isin(valid_keywords)]

    return monthly_filtered


# ---------------------------------------------------------------------------
# Train/test split
# ---------------------------------------------------------------------------

def prepare_train_test_split(monthly_df: pd.DataFrame) -> dict:
    """
    For each keyword, split into training months (all but the last) and
    a single test month (the last, most recent one) — this is our holdout.

    The test month is NEVER seen during training. This is what makes the
    MAE an honest accuracy metric — we're predicting truly unseen data.

    Returns a dict: {keyword: {"train": df, "test_month": period, "test_actual": value}}
    """
    keyword_data = {}

    for keyword, group in monthly_df.groupby("Keyword"):
        group = group.sort_values("year_month").reset_index(drop=True)
        if len(group) < 2:
            continue  # need at least 1 train point + 1 test point

        train = group.iloc[:-1]
        test_row = group.iloc[-1]

        keyword_data[keyword] = {
            "train": train,
            "test_month": test_row["year_month"],
            "test_actual": test_row["avg_traffic"],
        }

    return keyword_data


# ---------------------------------------------------------------------------
# Model training and prediction
# ---------------------------------------------------------------------------

def train_and_predict(keyword_data: dict) -> pd.DataFrame:
    """
    For each keyword, fit linear regression on training months
    (X = month index 0, 1, 2..., y = traffic), predict the next month,
    compare to actual.

    WHY month index instead of date:
    Linear regression needs numeric features. Converting dates to a simple
    sequential index (0, 1, 2...) captures the trend direction without
    needing to handle date math. The model learns: "traffic goes up/down
    by X per month."
    """
    results = []

    for keyword, data in keyword_data.items():
        train = data["train"]

        X = np.arange(len(train)).reshape(-1, 1)  # month index: 0, 1, 2...
        y = train["avg_traffic"].values

        model = LinearRegression()
        model.fit(X, y)

        next_month_index = np.array([[len(train)]])  # the month right after training
        predicted = model.predict(next_month_index)[0]
        predicted = max(0, predicted)  # traffic can't be negative

        actual = data["test_actual"]

        results.append({
            "keyword": keyword,
            "months_trained_on": len(train),
            "predicted_traffic": round(predicted, 1),
            "actual_traffic": round(actual, 1),
            "absolute_error": round(abs(predicted - actual), 1),
            "test_month": str(data["test_month"]),
        })

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

def compute_summary_metrics(results_df: pd.DataFrame) -> dict:
    """
    Compute overall MAE and related summary stats.

    MAE (Mean Absolute Error): the average of |predicted - actual| across
    all keywords. Directly interpretable: "our forecasts are off by an
    average of X traffic units."

    MAE as % of average traffic gives context: a MAE of 50 means very
    different things if average traffic is 100 (50% error) vs 10,000
    (0.5% error).
    """
    mae = mean_absolute_error(results_df["actual_traffic"], results_df["predicted_traffic"])

    # MAE as % of average actual traffic (only for non-zero actuals)
    nonzero = results_df[results_df["actual_traffic"] > 0]
    avg_actual = nonzero["actual_traffic"].mean()
    mae_pct_of_avg = (mae / avg_actual * 100) if avg_actual > 0 else None

    return {
        "keywords_forecasted": len(results_df),
        "mae": round(mae, 2),
        "avg_actual_traffic": round(avg_actual, 2),
        "mae_as_pct_of_avg_traffic": round(mae_pct_of_avg, 1) if mae_pct_of_avg else None,
    }


# ---------------------------------------------------------------------------
# Storage — MySQL only
# ---------------------------------------------------------------------------

def store_forecasts(results_df: pd.DataFrame):
    """Store forecast results in a new MySQL table."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS traffic_forecasts (
            forecast_id INT AUTO_INCREMENT PRIMARY KEY,
            keyword_text VARCHAR(255),
            months_trained_on INT,
            test_month VARCHAR(20),
            predicted_traffic FLOAT,
            actual_traffic FLOAT,
            absolute_error FLOAT,
            forecast_run_at DATETIME
        )
    """)
    cursor.execute("TRUNCATE TABLE traffic_forecasts")

    run_time = datetime.now()
    for _, row in results_df.iterrows():
        cursor.execute("""
            INSERT INTO traffic_forecasts
            (keyword_text, months_trained_on, test_month, predicted_traffic,
             actual_traffic, absolute_error, forecast_run_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            row["keyword"], int(row["months_trained_on"]), row["test_month"],
            float(row["predicted_traffic"]), float(row["actual_traffic"]),
            float(row["absolute_error"]), run_time,
        ))

    conn.commit()
    cursor.close()
    conn.close()
    print(f"  Stored {len(results_df)} forecasts in MySQL (traffic_forecasts table)")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results_df: pd.DataFrame, summary: dict):
    """Print a clear console summary of the forecasting run."""
    print("\n" + "=" * 60)
    print("  TRAFFIC FORECASTING REPORT")
    print("=" * 60)
    print(f"  Keywords forecasted:       {summary['keywords_forecasted']:,}")
    print(f"  Mean Absolute Error (MAE): {summary['mae']} traffic units")
    print(f"  Average actual traffic:    {summary['avg_actual_traffic']} traffic units")
    if summary["mae_as_pct_of_avg_traffic"]:
        print(f"  MAE as % of avg traffic:   {summary['mae_as_pct_of_avg_traffic']}%")

    print(f"\n  Best 5 predictions (lowest error):")
    best = results_df.nsmallest(5, "absolute_error")
    for _, row in best.iterrows():
        print(f"    {row['keyword']}: predicted {row['predicted_traffic']:.0f}, "
              f"actual {row['actual_traffic']:.0f} (error: {row['absolute_error']:.0f})")

    print(f"\n  Worst 5 predictions (highest error):")
    worst = results_df.nlargest(5, "absolute_error")
    for _, row in worst.iterrows():
        print(f"    {row['keyword']}: predicted {row['predicted_traffic']:.0f}, "
              f"actual {row['actual_traffic']:.0f} (error: {row['absolute_error']:.0f})")

    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AdPulse Traffic Forecasting")
    parser.add_argument(
        "--csv", type=str, default=None, nargs="?",
        const="auto",
        help="Use CSV/Excel file instead of MySQL.",
    )
    parser.add_argument(
        "--min-months", type=int, default=6,
        help="Minimum months of history required per keyword (default: 6)",
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
    print("  AdPulse Traffic Forecasting")
    print(f"  Holdout test: predict month 12, compare to actual")
    print(f"  Min months required: {args.min_months}")
    print("=" * 60)

    # Step 1: Load and aggregate
    print("\n  Loading and aggregating monthly traffic data...")
    raw_df = load_traffic_data(csv_path)
    print(f"  Raw records: {len(raw_df):,}")

    monthly_df = load_and_aggregate_monthly(raw_df, min_months=args.min_months)
    print(f"  Keywords with >= {args.min_months} months of history: {monthly_df['Keyword'].nunique():,}")

    # Step 2: Train/test split
    print("\n  Preparing train/test split (holdout: most recent month)...")
    keyword_data = prepare_train_test_split(monthly_df)
    print(f"  Keywords ready for forecasting: {len(keyword_data):,}")

    # Step 3: Train and predict
    print("\n  Training linear regression models and generating forecasts...")
    results_df = train_and_predict(keyword_data)

    # Guard: no keyword had enough history (e.g. the raw 199-row export
    # spans only ~2 months). Report clearly instead of crashing on
    # empty metrics / wiping the results table with nothing.
    if results_df.empty:
        print(f"\n  [WARNING] No keywords had at least {args.min_months} distinct months")
        print("  of traffic history, so there is nothing to forecast.")
        print("  Check that the data source spans enough months per keyword,")
        print("  or lower --min-months. Exiting without computing metrics.")
        return

    # Step 4: Compute metrics
    summary = compute_summary_metrics(results_df)

    # Step 5: Print report
    print_report(results_df, summary)

    # Step 6: Store to MySQL (if available)
    if MYSQL_AVAILABLE and csv_path is None:
        print("\n  Storing forecasts in MySQL...")
        store_forecasts(results_df)
    else:
        print("\n  (MySQL not used — results printed to console only)")

    print("\nDone.")


if __name__ == "__main__":
    main()
