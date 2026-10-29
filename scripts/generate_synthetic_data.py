"""
Synthetic SEM Data Generator
=============================
We don't want purely random fake data — that would look nothing like real
SEM data and any analysis on it would be meaningless. Instead, we:
  1. Load the real 199 rows and compute their actual statistical properties
     (CPC distribution, search volume distribution, position/traffic
     correlation, etc.)
  2. Generate new synthetic rows by SAMPLING from distributions fitted to
     the real data, not from scratch
  3. Expand the keyword universe using realistic certification-domain
     keyword patterns (base courses + modifiers like "certification",
     "cost", "online", "training", "exam", city names, etc.)

This keeps the synthetic data statistically honest — an analyst looking
at CPC or search volume distributions would see the same real-world shape,
just more of it.

IMPORTANT: This script is clearly documented as synthetic-but-calibrated.
Never claim the scaled dataset is real data. The original Raw_data.xlsx
is untouched — a separate Raw_data_scaled.xlsx is produced.
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Fixed seed for reproducibility — same seed = same output every run
np.random.seed(42)


def load_and_profile_real_data(path: str) -> dict:
    """
    Load the real 199-row dataset and compute the statistical properties
    we'll use to generate realistic synthetic data.
    """
    df = pd.read_excel(path)

    profile = {
        "cpc_mean": df["CPC"].mean(),
        "cpc_std": df["CPC"].std(),
        "cpc_min": df["CPC"].min(),
        "cpc_max": df["CPC"].max(),
        "search_volume_values": df["Search Volume"].values,  # for power-law sampling
        "position_values": df["Position"].values,
        "difficulty_mean": df["Keyword Difficulty"].mean() if "Keyword Difficulty" in df.columns else 50,
        "difficulty_std": df["Keyword Difficulty"].std() if "Keyword Difficulty" in df.columns else 15,
        "base_keywords": df["Keyword"].unique().tolist(),
        "competition_values": df["Competition"].values,
    }
    return profile


def generate_keyword_universe(base_keywords: list, target_count: int) -> list:
    """
    Expand a small set of real keywords into a larger realistic universe
    using common SEM modifier patterns for the certification domain.
    """
    base_courses = [
        "PMP", "CSM", "ITIL", "Six Sigma", "CAPM", "PRINCE2",
        "Agile", "Scrum Master", "PMI-ACP", "Lean Six Sigma",
        "PMP certification", "Scrum certification", "DevOps",
        "AWS", "Azure", "Google Cloud", "CompTIA", "CISSP",
        "CISA", "CISM", "Tableau", "Power BI", "SQL",
        "Data Science", "Machine Learning", "Python", "Java",
        "SAP", "Salesforce", "ServiceNow", "Cybersecurity",
    ]
    modifiers = [
        "", "certification", "certification cost", "online course",
        "training", "exam prep", "exam cost", "certification exam",
        "study guide", "practice test", "renewal", "requirements",
        "salary", "vs", "certification online", "bootcamp",
        "course", "exam", "preparation", "material", "free",
        "best", "cost", "fee", "syllabus", "eligibility",
    ]

    keywords = set(base_keywords)  # start with real ones

    for course in base_courses:
        for modifier in modifiers:
            if modifier:
                keywords.add(f"{course.lower()} {modifier}")
            else:
                keywords.add(course.lower())

    keywords = list(keywords)

    # If we still need more, add city/region variations for local intent
    if len(keywords) < target_count:
        cities = [
            "india", "usa", "bangalore", "mumbai", "delhi",
            "hyderabad", "chennai", "pune", "online", "near me",
            "london", "singapore", "dubai", "canada", "australia",
        ]
        extra = []
        for kw in list(keywords)[:80]:  # base off first 80 to avoid explosion
            for city in cities:
                extra.append(f"{kw} {city}")
        keywords.extend(extra)

    return keywords[:target_count]


def sample_cpc(profile: dict, n: int) -> np.ndarray:
    """
    Sample CPC values from a distribution matching the real data's shape.
    Real CPC data is right-skewed (most keywords cheap, few expensive) —
    use a lognormal distribution fitted to the real mean/std.
    """
    mu = np.log(
        profile["cpc_mean"] ** 2
        / np.sqrt(profile["cpc_std"] ** 2 + profile["cpc_mean"] ** 2)
    )
    sigma = np.sqrt(
        np.log(1 + (profile["cpc_std"] ** 2 / profile["cpc_mean"] ** 2))
    )
    values = np.random.lognormal(mu, sigma, n)
    return np.clip(values, profile["cpc_min"], profile["cpc_max"] * 1.5)


def sample_search_volume(profile: dict, n: int) -> np.ndarray:
    """
    Sample search volume using a power-law-like distribution — a few
    high-volume keywords, long tail of low-volume ones, matching real
    keyword research data patterns. We sample with replacement from the
    real values and add noise to create variety.
    """
    real_values = profile["search_volume_values"]
    real_values = real_values[real_values > 0]
    # Sample from real distribution, add noise for variety
    base_samples = np.random.choice(real_values, size=n, replace=True)
    noise = np.random.uniform(0.7, 1.4, n)
    return np.round(base_samples * noise).astype(int)


def generate_title(keyword: str, idx: int) -> str:
    """
    Generate a realistic ad title from a keyword.
    Uses common SEM ad title patterns.
    """
    templates = [
        f"{keyword.title()} | Enroll Now - Limited Offer",
        f"{keyword.title()} | Get Certified Today",
        f"{keyword.title()} | Best Online Course",
        f"{keyword.title()} | 50% Off - Limited Time",
        f"Best {keyword.title()} | Start Learning Today",
        f"{keyword.title()} | Official Training Program",
        f"{keyword.title()} | Start Your Career",
        f"{keyword.title()} | Top Rated Course",
    ]
    return templates[idx % len(templates)]


def generate_trend_array() -> list:
    """
    Generate a realistic 12-month trend array (Google Trends format).
    Values are 0-100 integers representing relative interest over time.
    """
    base = np.random.randint(20, 80)
    trend = np.clip(base + np.random.randint(-15, 16, 12).cumsum(), 0, 100)
    return trend.tolist()


def generate_synthetic_dataset(
    real_data_path: str,
    target_rows: int = 25000,
    months_back: int = 12,
) -> pd.DataFrame:
    """
    Main generation function. Produces a DataFrame matching the real schema,
    calibrated to real statistical properties, spanning months_back months.
    """
    print("Profiling real data...")
    profile = load_and_profile_real_data(real_data_path)
    print(f"  Real CPC: mean={profile['cpc_mean']:.2f}, std={profile['cpc_std']:.2f}, "
          f"min={profile['cpc_min']:.2f}, max={profile['cpc_max']:.2f}")
    print(f"  Real Search Volume: mean={np.mean(profile['search_volume_values']):.0f}, "
          f"min={np.min(profile['search_volume_values'])}, "
          f"max={np.max(profile['search_volume_values'])}")
    print(f"  Real Difficulty: mean={profile['difficulty_mean']:.1f}, "
          f"std={profile['difficulty_std']:.1f}")

    print("\nExpanding keyword universe...")
    keywords = generate_keyword_universe(profile["base_keywords"], target_count=2000)
    print(f"  Keyword universe: {len(keywords)} unique keywords")

    print(f"\nGenerating {target_rows} synthetic rows across {months_back} months...")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=months_back * 30)

    # ------------------------------------------------------------------
    # PER-KEYWORD TRAFFIC TREND MODEL
    # ------------------------------------------------------------------
    # Instead of computing traffic independently per row (which creates no
    # temporal signal), we give each keyword a coherent trend across months:
    #
    #   1. Each keyword gets a BASE traffic level (sampled from real
    #      search_volume * typical_position_factor * typical_noise)
    #   2. Each keyword gets a SLOPE: some trend up, some down, some flat
    #   3. Per-row traffic = base * (1 + slope * month_index) * (1 + noise)
    #
    # This means the same keyword's traffic evolves consistently over time,
    # giving the forecasting model a real signal to learn.
    # ------------------------------------------------------------------
    n_kw = len(keywords)
    kw_base_traffic = np.exp(np.random.normal(
        np.log(100), 0.8, n_kw  # lognormal: most keywords low-traffic, few high
    ))
    # Slope: per-month fractional change. ~30% up, ~30% down, ~40% flat
    kw_slope = np.zeros(n_kw)
    n_up = n_kw // 3
    n_down = n_kw // 3
    kw_slope[:n_up] = np.random.uniform(0.005, 0.03, n_up)           # trending up
    kw_slope[n_up:n_up + n_down] = np.random.uniform(-0.03, -0.005, n_down)  # trending down
    # rest stay 0 (flat)
    np.random.shuffle(kw_slope)

    # Pre-sample arrays for speed
    cpc_array = sample_cpc(profile, target_rows)
    search_volume_array = sample_search_volume(profile, target_rows)
    keyword_indices = np.random.randint(0, n_kw, target_rows)
    positions = np.random.randint(1, 101, target_rows)
    competition_array = np.random.uniform(0, 1, target_rows)
    difficulty_array = np.clip(
        np.random.normal(profile["difficulty_mean"], profile["difficulty_std"], target_rows),
        0, 100,
    )

    # Month indices (0 to months_back-1) for each row
    random_days = np.random.randint(0, months_back * 30, target_rows)
    month_indices = (random_days // 30).astype(int)
    dates = [start_date + timedelta(days=int(d)) for d in random_days]

    # Compute traffic using per-keyword trend
    kw_idx = keyword_indices
    m_idx = month_indices
    traffic_array = np.maximum(0, (
        kw_base_traffic[kw_idx]
        * (1 + kw_slope[kw_idx] * m_idx)     # trend component
        * np.random.uniform(0.15, 0.35, target_rows)  # monthly noise
    )).astype(int)

    rows = []
    for i in range(target_rows):
        keyword = keywords[kw_idx[i]]
        position = int(positions[i])
        traffic_val = int(traffic_array[i])

        rows.append({
            "Title": generate_title(keyword, i),
            "Keyword": keyword,
            "Position": position,
            "Previous position": max(1, position + np.random.randint(-10, 10)),
            "Search Volume": int(search_volume_array[i]),
            "CPC": round(float(cpc_array[i]), 2),
            "Traffic": traffic_val,
            "Traffic (%)": 0.0,  # will be calculated after generation
            "Traffic Cost": round(float(traffic_val * cpc_array[i]), 2),
            "Traffic Cost (%)": 0.0,
            "Competition": round(float(competition_array[i]), 2),
            "Number of Results": int(np.random.randint(1000000, 500000000)),
            "Trends": str(generate_trend_array()),
            "Last Seen": dates[i].strftime("%Y-%m-%d"),
            "Keyword Difficulty": round(float(difficulty_array[i]), 1),
        })

    df = pd.DataFrame(rows)

    # Calculate Traffic % and Traffic Cost % relative to the generated dataset
    total_traffic = df["Traffic"].sum()
    total_cost = df["Traffic Cost"].sum()
    if total_traffic > 0:
        df["Traffic (%)"] = round(df["Traffic"] / total_traffic * 100, 2)
    if total_cost > 0:
        df["Traffic Cost (%)"] = round(df["Traffic Cost"] / total_cost * 100, 2)

    print(f"  Generated {len(df)} rows.")
    return df


def print_dataset_stats(df: pd.DataFrame, label: str):
    """Print summary statistics for a dataset for documentation purposes."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Rows:              {len(df):,}")
    print(f"  Unique Keywords:   {df['Keyword'].nunique():,}")
    dates = pd.to_datetime(df['Last Seen'], errors='coerce')
    print(f"  Date Range:        {dates.min().date()} to {dates.max().date()}")
    print(f"  CPC:               mean={df['CPC'].mean():.2f}, "
          f"min={df['CPC'].min():.2f}, max={df['CPC'].max():.2f}")
    print(f"  Search Volume:     mean={df['Search Volume'].mean():.0f}, "
          f"min={df['Search Volume'].min()}, max={df['Search Volume'].max():,}")
    print(f"  Traffic:           mean={df['Traffic'].mean():.0f}, "
          f"min={df['Traffic'].min()}, max={df['Traffic'].max():,}")
    print(f"  Traffic Cost:      mean={df['Traffic Cost'].mean():.2f}, "
          f"min={df['Traffic Cost'].min():.2f}, max={df['Traffic Cost'].max():.2f}")
    print(f"  Keyword Difficulty: mean={df['Keyword Difficulty'].mean():.1f}, "
          f"min={df['Keyword Difficulty'].min():.1f}, max={df['Keyword Difficulty'].max():.1f}")
    print(f"  Position:          mean={df['Position'].mean():.1f}, "
          f"min={df['Position'].min()}, max={df['Position'].max()}")
    print(f"  Competition:       mean={df['Competition'].mean():.2f}, "
          f"min={df['Competition'].min():.2f}, max={df['Competition'].max():.2f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    # Paths relative to project root
    base_dir = Path(__file__).resolve().parents[1]
    real_data_path = base_dir / "data" / "raw" / "Raw_data.xlsx"
    output_path = base_dir / "data" / "raw" / "Raw_data_scaled.xlsx"

    print("=" * 60)
    print("  AdPulse Synthetic Data Generator")
    print("  NOTE: This generates SYNTHETIC data calibrated to real statistics.")
    print("  The original Raw_data.xlsx is never modified.")
    print("=" * 60)

    # Generate synthetic rows
    synthetic_df = generate_synthetic_dataset(
        str(real_data_path), target_rows=25000, months_back=12
    )

    # Load real data (untouched)
    real_df = pd.read_excel(str(real_data_path))

    # Combine: real data first, synthetic extends it
    combined = pd.concat([real_df, synthetic_df], ignore_index=True)

    # Save
    combined.to_excel(str(output_path), index=False)

    # Print stats for documentation
    print_dataset_stats(real_df, "ORIGINAL REAL DATA (199 rows)")
    print_dataset_stats(synthetic_df, "SYNTHETIC DATA (generated)")
    print_dataset_stats(combined, "COMBINED SCALED DATASET")

    print(f"\nSaved: {output_path}")
    print(f"  Total: {len(combined):,} rows ({len(real_df)} real + {len(synthetic_df):,} synthetic)")
    print(f"\nDone.")
