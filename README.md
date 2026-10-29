# AdPulse

Automated ETL pipeline for Google Ads / SEM keyword campaign analytics.
Loads raw Excel exports → validates & cleans with Python → stores in a normalized MySQL star schema → analyzes with SQL → visualizes in Power BI.

## What This Project Does

- **Load**: Import raw ad data from Excel
- **Validate**: Catch data quality issues before they hit the database
- **Clean**: Process data with Python to remove errors and add IDs
- **Store**: Load into MySQL database with proper relationships
- **Analyze**: Run SQL queries for performance insights
- **Visualize**: Create interactive Power BI dashboards

## Tech Stack

| Layer | Technology |
|-------|-----------|
| ETL | Python, Pandas, openpyxl |
| Validation | Custom validation layer (validate.py) |
| Database | MySQL (star schema, FK constraints) |
| Analytics | Advanced SQL (CTEs, subqueries, segmentation) |
| Visualization | Power BI (DAX measures, star-schema data model) |
| Testing | pytest, GitHub Actions CI |

## Quick Start

### 1. Clone & Install

```bash
git clone <repo-url>
cd AdPulse
pip install -r requirements.txt
```

### 2. Configure MySQL Credentials

```bash
cp .env.example .env
# Edit .env and fill in your MySQL credentials
```

### 3. Prepare & Load Data

```bash
python scripts/prepare_processed_csvs.py   # scaled Excel -> data/processed/*.csv
python scripts/load_data.py                # validate + load into MySQL
```

The first command builds the four processed CSVs (fact + dimension tables) from `Raw_data_scaled.xlsx`, assigning keyword IDs and normalizing dates. The second validates before loading — if validation fails, it prints exactly what's wrong and exits without touching the database.

### 4. Run Tests

```bash
pytest tests/ -v
```

### 5. Run SQL Analytics

Open MySQL client and run:

```sql
SOURCE scripts/advanced_analytics.sql;
```

### 6. Open Power BI Dashboard

- File: `dashboard/Traffic Project dashboard.pbix`
- Or connect manually via: `Home -> Get Data -> MySQL`

## Dataset

| File | Rows | Description |
|------|------|-------------|
| `data/raw/Raw_data.xlsx` | 199 | Original real SEM export (do not modify) |
| `data/raw/Raw_data_scaled.xlsx` | 25,199 | 199 real + ~25,000 synthetic rows |

**Synthetic data is calibrated to real statistics** — the generation script (`scripts/generate_synthetic_data.py`) profiles the real 199 rows' CPC distribution (lognormal fit), search volume (power-law sampling), keyword difficulty, and competition values, then generates synthetic rows by sampling from those fitted distributions.

**Traffic follows a per-keyword trend + noise model** — rather than assigning traffic independently per row (which would produce no temporal signal), each keyword gets a coherent trend across the 12-month span: a base traffic level plus a per-keyword linear drift (some trending up, some down, some flat) with +/- 15-35% monthly noise on top. This gives the forecasting model a real signal to learn, while the noise keeps it from being perfectly predictable.

The synthetic data extends the same certification-course domain (PMP, CSM, ITIL, Six Sigma, AWS, Azure, etc.) across 2,000 unique keywords and a 12-month date range. All documentation and discussions clearly identify this as synthetic data — never claim it is real.

To regenerate: `python scripts/generate_synthetic_data.py`

The processed CSVs in `data/processed/` are derived from the scaled dataset — regenerate them with `python scripts/prepare_processed_csvs.py` before loading into MySQL (the Jupyter notebook produced them once from the 199-row original; the script now rebuilds them from any raw Excel source).

## Project Structure

```
AdPulse/
├── data/
│   ├── raw/                    ← Raw Excel/CSV files
│   │   ├── Raw_data.xlsx           ← Original real data (199 rows)
│   │   └── Raw_data_scaled.xlsx    ← Scaled dataset (25,199 rows)
│   └── processed/              ← Clean CSV files after processing
│       ├── website_traffic_data.csv
│       ├── keyword.csv
│       ├── keyword_difficulty.csv
│       └── search_volume.csv
├── scripts/
│   ├── load_data.py                ← ETL: loads CSVs into MySQL
│   ├── generate_synthetic_data.py  ← Generates scaled dataset
│   ├── prepare_processed_csvs.py   ← Builds processed CSVs from scaled Excel
│   ├── advanced_analytics.sql      ← 8 SQL analysis queries
│   ├── Traffic Data SQL script.sql
│   └── Keyword_Script.ipynb        ← Jupyter notebook for exploration
├── dashboard/
│   └── Traffic Project dashboard.pbix
├── tests/
│   └── test_validate.py        ← Unit tests for data validation
├── docs/                       ← Schema diagrams & screenshots
├── .env.example                ← Template for MySQL credentials
├── .gitignore                  ← Excludes .env, __pycache__, etc.
├── validate.py                 ← Data validation layer
├── requirements.txt
└── README.md
```

## Data Validation

Before loading data into MySQL, the pipeline runs `validate.py` which checks for:

| Check | Severity | Blocks Load? |
|-------|----------|-------------|
| Required columns exist | Critical | ✅ Yes |
| No null keywords | Warning | ❌ No |
| No duplicate keywords | Warning | ❌ No |
| CPC in valid range [0, 1000] | Critical | ✅ Yes |
| Search Volume non-negative | Critical | ✅ Yes |
| Position in range [1, 100] | Warning | ❌ No |

This catches corrupted rows, missing values, and out-of-range data **before** they reach the database.

## Star Schema

```
keyword (dimension)  ──────┐
                           ├── website_traffic_data (fact table)
search_volume (dimension) ─┤
                           │
keyword_difficulty (dim) ──┘
```

- **Fact table**: 15 columns — title, keyword, position, CPC, traffic, cost, competition, difficulty
- **Dimension tables**: keyword, search_volume, keyword_difficulty — linked via `keyword_ID` (FK)

## SQL Analytics (8 Queries)

1. Top 10 traffic-to-cost efficient keywords
2. Position improvement tracking
3. CPC tier segmentation (low/medium/high)
4. Monthly traffic trend
5. Missed opportunity keywords (high volume, low traffic)
6. Difficulty vs performance analysis
7. Top 5 most expensive keywords
8. Competition bucket analysis

## CI/CD

GitHub Actions runs `pytest tests/ -v` on every push and PR to `main`.
See `.github/workflows/test.yml` for the full workflow.

## Anomaly Detection

Flags CPC spikes/drops and traffic anomalies per keyword using a per-keyword z-score approach:

1. Computes each keyword's own historical mean and standard deviation for CPC and Traffic
2. Flags any data point beyond 2 standard deviations from that keyword's own baseline
3. Classifies anomalies as: CPC_SPIKE, CPC_DROP, TRAFFIC_DROP, TRAFFIC_SPIKE

```bash
python anomaly_detection.py            # MySQL mode
python anomaly_detection.py --csv      # CSV fallback mode
```

Results stored in: `detected_anomalies` table (MySQL) or printed to console.

## Traffic Forecasting

Predicts next-month traffic per keyword using linear regression trained on each keyword's historical monthly average traffic (minimum 6 months of history required). Evaluated via holdout testing: the most recent month is never used in training, only for measuring prediction accuracy.

Metric: Mean Absolute Error (MAE) — average absolute difference between predicted and actual traffic, in the same units as traffic itself.

```bash
python forecasting.py            # MySQL mode
python forecasting.py --csv      # CSV fallback mode
```

Results stored in: `traffic_forecasts` table (MySQL) or printed to console.

If no keyword has at least `--min-months` (default 6) distinct months of history, the script exits gracefully with a warning instead of computing metrics on empty data.
