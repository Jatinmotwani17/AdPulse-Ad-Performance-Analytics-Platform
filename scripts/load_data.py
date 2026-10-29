"""Auto-load Meta + Google campaign CSVs into MySQL.

This script is idempotent: it drops and recreates tables on every run,
then bulk-loads CSV records with executemany().

Credentials are loaded from a .env file via python-dotenv. Copy
.env.example to .env and fill in your MySQL credentials before running.
"""

import os
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from dotenv import load_dotenv
import pandas as pd
import mysql.connector
from mysql.connector import Error

# ---------------------------------------------------------------------------
# Load environment variables from .env (located at project root, one level up)
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Validate that all required env vars are set — fail loudly if not.
required_env_vars = ["MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
missing = [var for var in required_env_vars if not os.getenv(var)]
if missing:
    print(f"ERROR: Missing required environment variables: {missing}")
    print("Copy .env.example to .env and fill in your MySQL credentials.")
    sys.exit(1)

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER"),
    "password": os.getenv("MYSQL_PASSWORD"),
    "database": os.getenv("MYSQL_DATABASE"),
}

# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data" / "processed"

CSV_FILES = {
    "keyword": DATA_DIR / "keyword.csv",
    "search_volume": DATA_DIR / "search_volume.csv",
    "keyword_difficulty": DATA_DIR / "keyword_difficulty.csv",
    "website_traffic_data": DATA_DIR / "website_traffic_data.csv",
}

# ---------------------------------------------------------------------------
# Add scripts/ to path so we can import validate.py
# ---------------------------------------------------------------------------
sys.path.insert(0, str(BASE_DIR))
from validate import validate_keyword_data, print_validation_report, ValidationError


def validate_files() -> None:
    """Fail fast if any expected CSV file is missing."""
    missing = [str(path) for path in CSV_FILES.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing CSV files:\n- " + "\n- ".join(missing)
        )


def create_database_if_missing(connection_config: dict) -> None:
    """Ensure the target database exists before connecting to it."""
    bootstrap_config = dict(connection_config)
    database_name = bootstrap_config.pop("database")

    connection = mysql.connector.connect(**bootstrap_config)
    try:
        cursor = connection.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}`")
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def drop_and_create_tables(cursor) -> None:
    """Drop and recreate schema in a FK-safe order."""
    drop_statements = [
        "DROP TABLE IF EXISTS website_traffic_data",
        "DROP TABLE IF EXISTS keyword_difficulty",
        "DROP TABLE IF EXISTS search_volume",
        "DROP TABLE IF EXISTS keyword",
    ]

    create_statements = [
        """
        CREATE TABLE keyword (
            keyword_ID INT NOT NULL,
            keyword VARCHAR(250) NOT NULL,
            PRIMARY KEY (keyword_ID)
        )
        """,
        """
        CREATE TABLE search_volume (
            keyword_ID INT NOT NULL,
            search_volume INT NOT NULL,
            PRIMARY KEY (keyword_ID),
            CONSTRAINT fk_search_volume_keyword
                FOREIGN KEY (keyword_ID) REFERENCES keyword(keyword_ID)
        )
        """,
        """
        CREATE TABLE keyword_difficulty (
            keyword_ID INT NOT NULL,
            avg_difficulty DECIMAL(10, 2) NOT NULL,
            difficulty_level VARCHAR(50) NOT NULL,
            PRIMARY KEY (keyword_ID),
            CONSTRAINT fk_keyword_difficulty_keyword
                FOREIGN KEY (keyword_ID) REFERENCES keyword(keyword_ID)
        )
        """,
        """
        CREATE TABLE website_traffic_data (
            title VARCHAR(250) NOT NULL,
            keyword VARCHAR(250) NOT NULL,
            keyword_ID INT NOT NULL,
            positions INT NOT NULL,
            previous_positions INT NOT NULL,
            last_seen DATE NOT NULL,
            Search_Volume INT NOT NULL,
            CPC DECIMAL(10, 2) NOT NULL,
            Traffic INT NOT NULL,
            Traffic_Percent DECIMAL(10, 2) NOT NULL,
            Traffic_Cost INT NOT NULL,
            Traffic_Cost_Percent DECIMAL(10, 2) NOT NULL,
            Competition DECIMAL(10, 2) NOT NULL,
            Number_of_Results INT NOT NULL,
            Keyword_difficulty INT NOT NULL,
            INDEX idx_keyword_id (keyword_ID),
            CONSTRAINT fk_traffic_keyword
                FOREIGN KEY (keyword_ID) REFERENCES keyword(keyword_ID)
        )
        """,
    ]

    for statement in drop_statements:
        cursor.execute(statement)

    for statement in create_statements:
        cursor.execute(statement)


def clean_number_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Coerce columns to numeric, turning invalid strings into NaN."""
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_keyword_df() -> pd.DataFrame:
    """Load and clean the keyword dimension CSV."""
    df = pd.read_csv(CSV_FILES["keyword"])
    df = df.rename(columns={"Keyword ID": "keyword_ID", "Keyword": "keyword"})
    df = clean_number_columns(df, ["keyword_ID"])
    return df[["keyword_ID", "keyword"]].dropna()


def load_search_volume_df() -> pd.DataFrame:
    """Load and clean the search_volume dimension CSV."""
    df = pd.read_csv(CSV_FILES["search_volume"])
    df = df.rename(
        columns={"keyword ID": "keyword_ID", "Search Volume": "search_volume"}
    )
    df = clean_number_columns(df, ["keyword_ID", "search_volume"])
    return df[["keyword_ID", "search_volume"]].dropna()


def load_keyword_difficulty_df() -> pd.DataFrame:
    """Load and clean the keyword_difficulty dimension CSV."""
    df = pd.read_csv(CSV_FILES["keyword_difficulty"])
    df = df.rename(
        columns={
            "keyword ID": "keyword_ID",
            "Keyword Difficulty": "avg_difficulty",
            "Difficulty Level": "difficulty_level",
        }
    )
    df = clean_number_columns(df, ["keyword_ID", "avg_difficulty"])
    return df[["keyword_ID", "avg_difficulty", "difficulty_level"]].dropna()


def load_traffic_df() -> pd.DataFrame:
    """Load and clean the website_traffic_data fact table CSV."""
    df = pd.read_csv(CSV_FILES["website_traffic_data"])

    df = df.rename(
        columns={
            "Title": "title",
            "Keyword": "keyword",
            "Keyword ID": "keyword_ID",
            "Position": "positions",
            "Previous position": "previous_positions",
            "Last Seen": "last_seen",
            "Search Volume": "Search_Volume",
            "CPC": "CPC",
            "Traffic": "Traffic",
            "Traffic (%)": "Traffic_Percent",
            "Traffic Cost": "Traffic_Cost",
            "Traffic Cost (%)": "Traffic_Cost_Percent",
            "Competition": "Competition",
            "Number of Results": "Number_of_Results",
            "Keyword Difficulty": "Keyword_difficulty",
        }
    )

    numeric_columns = [
        "keyword_ID",
        "positions",
        "previous_positions",
        "Search_Volume",
        "CPC",
        "Traffic",
        "Traffic_Percent",
        "Traffic_Cost",
        "Traffic_Cost_Percent",
        "Competition",
        "Number_of_Results",
        "Keyword_difficulty",
    ]
    df = clean_number_columns(df, numeric_columns)

    # Source date format is DD-MM-YYYY in the provided CSV.
    df["last_seen"] = pd.to_datetime(df["last_seen"], format="%d-%m-%Y", errors="coerce")

    ordered_columns = [
        "title",
        "keyword",
        "keyword_ID",
        "positions",
        "previous_positions",
        "last_seen",
        "Search_Volume",
        "CPC",
        "Traffic",
        "Traffic_Percent",
        "Traffic_Cost",
        "Traffic_Cost_Percent",
        "Competition",
        "Number_of_Results",
        "Keyword_difficulty",
    ]

    return df[ordered_columns].dropna()


def dataframe_to_records(df: pd.DataFrame) -> List[Tuple]:
    """Convert a DataFrame to a list of tuples for executemany()."""
    return [tuple(row) for row in df.itertuples(index=False, name=None)]


def bulk_insert(cursor, table_name: str, columns: Sequence[str], records: List[Tuple]) -> None:
    """Bulk insert records into a table using executemany()."""
    placeholders = ", ".join(["%s"] * len(columns))
    column_list = ", ".join(columns)
    query = f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders})"
    cursor.executemany(query, records)


def print_table_count(cursor, table_name: str) -> None:
    """Print the row count for a table after loading."""
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    row_count = cursor.fetchone()[0]
    print(f"Loaded {row_count} rows into {table_name}.")


def main() -> None:
    try:
        # Step 1: Verify all CSV files exist
        validate_files()

        # Step 2: Read the raw traffic CSV for validation BEFORE loading
        raw_df = pd.read_csv(CSV_FILES["website_traffic_data"])
        print("Running data validation...")
        report = validate_keyword_data(raw_df)
        print_validation_report(report)

        if report["overall_status"] == "fail":
            print("\n[FAIL] Validation failed. Aborting load. Fix the data and retry.")
            sys.exit(1)

        print("\n[PASS] Validation passed. Proceeding with data load...\n")

        # Step 3: Create database if it doesn't exist
        create_database_if_missing(DB_CONFIG)

        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()

        # Step 4: Drop and recreate tables
        drop_and_create_tables(cursor)

        # Step 5: Load all dataframes
        keyword_df = load_keyword_df()
        search_volume_df = load_search_volume_df()
        keyword_difficulty_df = load_keyword_difficulty_df()
        traffic_df = load_traffic_df()

        bulk_insert(
            cursor,
            "keyword",
            ["keyword_ID", "keyword"],
            dataframe_to_records(keyword_df),
        )
        print_table_count(cursor, "keyword")

        bulk_insert(
            cursor,
            "search_volume",
            ["keyword_ID", "search_volume"],
            dataframe_to_records(search_volume_df),
        )
        print_table_count(cursor, "search_volume")

        bulk_insert(
            cursor,
            "keyword_difficulty",
            ["keyword_ID", "avg_difficulty", "difficulty_level"],
            dataframe_to_records(keyword_difficulty_df),
        )
        print_table_count(cursor, "keyword_difficulty")

        bulk_insert(
            cursor,
            "website_traffic_data",
            [
                "title",
                "keyword",
                "keyword_ID",
                "positions",
                "previous_positions",
                "last_seen",
                "Search_Volume",
                "CPC",
                "Traffic",
                "Traffic_Percent",
                "Traffic_Cost",
                "Traffic_Cost_Percent",
                "Competition",
                "Number_of_Results",
                "Keyword_difficulty",
            ],
            dataframe_to_records(traffic_df),
        )
        print_table_count(cursor, "website_traffic_data")

        connection.commit()
        print("\nData load completed successfully.")

    except FileNotFoundError as file_error:
        print(f"File error: {file_error}")
    except ValidationError as val_error:
        print(f"Validation error: {val_error}")
        print("Aborting load. Fix the data and retry.")
        sys.exit(1)
    except Error as db_error:
        print(f"MySQL error: {db_error}")
    except Exception as unexpected_error:
        print(f"Unexpected error: {unexpected_error}")
    finally:
        if "cursor" in locals() and cursor:
            cursor.close()
        if "connection" in locals() and connection and connection.is_connected():
            connection.close()
            print("MySQL connection closed.")


if __name__ == "__main__":
    main()
