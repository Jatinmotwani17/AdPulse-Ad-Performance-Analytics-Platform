#!/usr/bin/env python3
"""Prepare data/processed/*.csv from a raw Excel export.

The four processed CSVs are the bridge between data/raw/*.xlsx and MySQL:
scripts/load_data.py validates them, then bulk-loads them into the star
schema. They were originally produced once by Keyword_Script.ipynb from
the 199-row real export; this script regenerates them from
Raw_data_scaled.xlsx so the full pipeline (validate -> load -> anomaly
detection -> forecasting) runs against the scaled 25,199-row dataset.

Outputs (schemas match load_data.py exactly):
    website_traffic_data.csv   fact rows + assigned keyword_ID
    keyword.csv                one row per unique keyword
    search_volume.csv          per-keyword mean search volume
    keyword_difficulty.csv     per-keyword mean difficulty + level label

Usage:
    python scripts/prepare_processed_csvs.py
    python scripts/prepare_processed_csvs.py --source data/raw/Raw_data.xlsx
"""

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw"
OUT_DIR = BASE_DIR / "data" / "processed"
DEFAULT_SOURCE = RAW_DIR / "Raw_data_scaled.xlsx"

# Columns expected in the source Excel (Trends is intentionally ignored).
SOURCE_COLUMNS = [
    "Title", "Keyword", "Position", "Previous position", "Search Volume",
    "CPC", "Traffic", "Traffic (%)", "Traffic Cost", "Traffic Cost (%)",
    "Competition", "Number of Results", "Last Seen", "Keyword Difficulty",
]

NUMERIC_COLUMNS = [
    "Position", "Previous position", "Search Volume", "CPC", "Traffic",
    "Traffic (%)", "Traffic Cost", "Traffic Cost (%)", "Competition",
    "Number of Results", "Keyword Difficulty",
]

# MySQL fact-table columns are declared INT (positions, Search_Volume,
# Traffic, Traffic_Cost, Number_of_Results, Keyword_difficulty), so these
# must be whole numbers or strict-mode MySQL rejects the row.
INTEGER_COLUMNS = [
    "Position", "Previous position", "Search Volume", "Traffic",
    "Traffic Cost", "Number of Results", "Keyword Difficulty",
]


def parse_date(value):
    """Parse one date value.

    The scaled Excel mixes real rows (Excel datetime cells) with
    synthetic rows (ISO 'YYYY-MM-DD' strings), and older exports used
    'DD-MM-YYYY' text — handle all of them.
    """
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return pd.NaT


def difficulty_level(value: float) -> str:
    """Bucket a 0-100 difficulty score into a label.

    The 50-point Hard boundary is inferred from the original processed
    data (50.09 -> Hard, 48.0 -> Moderate); 25 is the conventional
    Easy/Moderate split below it.
    """
    if value >= 50:
        return "Hard"
    if value >= 25:
        return "Moderate"
    return "Easy"


def prepare(source: Path) -> None:
    print("=" * 60)
    print("  AdPulse Processed-CSV Preparer")
    print(f"  Source: {source}")
    print("=" * 60)

    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")

    df = pd.read_excel(source)
    print(f"\n  Read {len(df):,} rows.")

    missing = [c for c in SOURCE_COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(f"Source is missing required columns: {missing}")

    # --- Dates: mixed datetime cells and string formats ---
    df["Last Seen"] = df["Last Seen"].map(parse_date)
    bad_dates = int(df["Last Seen"].isna().sum())
    if bad_dates:
        raise SystemExit(f"{bad_dates} rows have unparseable Last Seen dates.")

    # --- Keyword cleanup + stable IDs (order of first appearance) ---
    df["Keyword"] = df["Keyword"].astype(str).str.strip()
    unique_keywords = list(dict.fromkeys(df["Keyword"]))
    keyword_ids = {kw: i + 1 for i, kw in enumerate(unique_keywords)}

    # --- Numeric coercion, drop rows missing required values ---
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    before = len(df)
    df = df.dropna(subset=NUMERIC_COLUMNS + ["Last Seen"])
    df = df[df["Keyword"] != ""]
    dropped = before - len(df)
    if dropped:
        print(f"  Dropped {dropped} rows with missing required values.")

    for col in INTEGER_COLUMNS:
        df[col] = df[col].round().astype("int64")

    # --- Fact table (column names/order must match load_data.py) ---
    fact = pd.DataFrame({
        "Title": df["Title"].fillna("").astype(str).str.slice(0, 250),
        "Keyword": df["Keyword"].str.slice(0, 250),
        "Keyword ID": df["Keyword"].map(keyword_ids).astype("int64"),
        "Position": df["Position"],
        "Previous position": df["Previous position"],
        "Last Seen": df["Last Seen"].dt.strftime("%d-%m-%Y"),
        "Search Volume": df["Search Volume"],
        "CPC": df["CPC"].round(2),
        "Traffic": df["Traffic"],
        "Traffic (%)": df["Traffic (%)"].round(2),
        "Traffic Cost": df["Traffic Cost"],
        "Traffic Cost (%)": df["Traffic Cost (%)"].round(2),
        "Competition": df["Competition"].round(2),
        "Number of Results": df["Number of Results"],
        "Keyword Difficulty": df["Keyword Difficulty"],
    })

    # --- Dimension tables ---
    keyword_dim = pd.DataFrame({
        "Keyword ID": range(1, len(unique_keywords) + 1),
        "Keyword": unique_keywords,
    })

    # One search-volume value per keyword (fact rows vary per month).
    search_volume_dim = (
        fact.groupby("Keyword ID")["Search Volume"]
        .mean()
        .round()
        .astype("int64")
        .rename("Search Volume")
        .reset_index()
        .rename(columns={"Keyword ID": "keyword ID"})
    )

    difficulty_mean = fact.groupby("Keyword ID")["Keyword Difficulty"].mean().round(2)
    difficulty_dim = pd.DataFrame({
        "keyword ID": difficulty_mean.index.astype("int64"),
        "Keyword Difficulty": difficulty_mean.values,
        "Difficulty Level": [difficulty_level(v) for v in difficulty_mean.values],
    })

    # --- Write ---
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fact.to_csv(OUT_DIR / "website_traffic_data.csv", index=False)
    keyword_dim.to_csv(OUT_DIR / "keyword.csv", index=False)
    search_volume_dim.to_csv(OUT_DIR / "search_volume.csv", index=False)
    difficulty_dim.to_csv(OUT_DIR / "keyword_difficulty.csv", index=False)

    print(f"\n  Fact rows:          {len(fact):,}")
    print(f"  Unique keywords:    {len(keyword_dim):,}")
    print(f"  Date range:         {df['Last Seen'].min().date()} to {df['Last Seen'].max().date()}")
    print(f"  Difficulty buckets: {difficulty_dim['Difficulty Level'].value_counts().to_dict()}")
    print(f"\nWrote 4 CSVs to {OUT_DIR}")
    print("Next step: python scripts/load_data.py")


def main():
    parser = argparse.ArgumentParser(description="Prepare processed CSVs from raw Excel")
    parser.add_argument(
        "--source", type=str, default=str(DEFAULT_SOURCE),
        help="Source Excel file (default: data/raw/Raw_data_scaled.xlsx)",
    )
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_absolute() and not source.exists():
        source = BASE_DIR / source

    prepare(source)
    print("\nDone.")


if __name__ == "__main__":
    main()
