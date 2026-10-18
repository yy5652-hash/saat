"""Measured output of a real PV plant from NREL's PVDAQ (DOE Open Energy Data Initiative).

System 9068 ("SR_CO"): 4,738 kW DC single-axis tracker plant in Kersey, Colorado, DOE Solar Data
Prize 2023 participant, 5-minute inverter AC power. Public S3 bucket, no key needed.
"""

import pandas as pd

from saat.paths import PROCESSED
from saat.sources.fetch import download

BUCKET = "https://oedi-data-lake.s3.amazonaws.com/pvdaq/2023-solar-data-prize/9068_OEDI"
SYSTEM = {
    "system_id": 9068,
    "name": "SR_CO",
    "location": "Kersey, CO",
    "lat": 40.3864,
    "lon": -104.5512,
    "elevation_m": 1407.0,
    "dc_kw": 4738.0,
    "tracking": "single-axis",
}
FILES = {
    "history": "9068_ac_power_data.csv",
    "recent": "9068_ac_power_data_20240101_20250430.csv",
}


def _hourly(path) -> pd.Series:
    df = pd.read_csv(path)
    ts = pd.to_datetime(df.pop("measured_on"))
    totals = [c for c in df.columns if c.startswith("inverter_") and "_ac_power_" in c and "module" not in c]
    kw = df[totals].apply(pd.to_numeric, errors="coerce")
    s = pd.Series(kw.sum(axis=1, min_count=1).values, index=ts).sort_index()
    s = s[~s.index.duplicated()]
    # 5-minute samples labelled at interval end; shift to interval start before averaging an hour.
    s.index = s.index - pd.Timedelta(minutes=5)
    counts = s.resample("1h").count()
    kwh = s.resample("1h").mean()
    kwh[counts < 9] = float("nan")  # fewer than 45 minutes of data: hour is missing
    return kwh


def hourly_energy() -> pd.DataFrame:
    """Hourly AC energy (kWh) with a UTC index. Local timestamps are fixed UTC-7 (no DST)."""
    out = PROCESSED / "pvdaq_9068_hourly.parquet"
    if out.exists():
        return pd.read_parquet(out)
    parts = [_hourly(download(f"{BUCKET}/data/{name}", name, 1_000_000)) for name in FILES.values()]
    s = pd.concat(parts).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    df = s.rename("kwh").to_frame()
    df.index = (df.index + pd.Timedelta(hours=7)).rename("utc")
    df.to_parquet(out)
    return df
