"""EIA-930 hourly balancing-authority operations (U.S. Energy Information Administration).

Public six-month CSVs from the EIA Grid Monitor; no API key needed.
https://www.eia.gov/electricity/gridmonitor/
"""

import pandas as pd

from saat.paths import PROCESSED
from saat.sources.fetch import download

BASE = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_{year}_{half}.csv"

FUELS = {
    "coal": "Coal",
    "gas": "Natural Gas",
    "nuclear": "Nuclear",
    "oil": "All Petroleum Products",
    "hydro": "Hydropower Excluding Pumped Storage",
    "solar": "Solar without Integrated Battery Storage",
    "solar_batt": "Solar with Integrated Battery Storage",
    "wind": "Wind without Integrated Battery Storage",
    "wind_batt": "Wind with Integrated Battery Storage",
    "geothermal": "Geothermal",
    "other": "Other Fuel Sources",
    "unknown": "Unknown Fuel Sources",
}


# EIA's 2025 header spells this adjusted column "Solar witho Integrated Battery Storage".
ADJUSTED_ALIASES = {
    "solar_batt": "Net Generation (MW) from Solar witho Integrated Battery Storage (Adjusted)",
}


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")


def load_year(year: int = 2025) -> pd.DataFrame:
    """Hourly demand and generation by fuel for every balancing authority, in MW (= MWh per hour).

    Uses EIA's adjusted columns, which fill reporting gaps; the raw ones are kept nowhere.
    """
    out = PROCESSED / f"eia930_{year}.parquet"
    if out.exists():
        return pd.read_parquet(out)
    frames = []
    for half in ("Jan_Jun", "Jul_Dec"):
        path = download(BASE.format(year=year, half=half), f"EIA930_BALANCE_{year}_{half}.csv", 10_000_000)
        raw = pd.read_csv(path, dtype=str, low_memory=False)
        raw.columns = [c.replace("  ", " ").strip() for c in raw.columns]
        df = pd.DataFrame(
            {
                "ba": raw["Balancing Authority"],
                "utc": pd.to_datetime(raw["UTC Time at End of Hour"], format="%m/%d/%Y %I:%M:%S %p"),
                "demand": _num(raw["Demand (MW) (Adjusted)"]),
                "region": raw["Region"],
            }
        )
        for key, label in FUELS.items():
            col = f"Net Generation (MW) from {label} (Adjusted)"
            if col not in raw.columns:
                col = ADJUSTED_ALIASES[key]
            df[key] = _num(raw[col]).fillna(0.0)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["utc"] = df["utc"] - pd.Timedelta(hours=1)  # label each hour by its start
    df = df.drop_duplicates(["ba", "utc"]).sort_values(["ba", "utc"]).reset_index(drop=True)
    df.to_parquet(out)
    return df
