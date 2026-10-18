"""Hourly weather from Open-Meteo (https://open-meteo.com), free for non-commercial use.

- `reanalysis`: ERA5-based historical weather (archive API). This is the independent physical
  record the verifier checks meter data against.
- `day_ahead`: forecasts issued the day before (previous-runs API, `*_previous_day1`), i.e. what a
  planner could actually have known when buying certificates for tomorrow.
Radiation values are means over the preceding hour; we relabel every row by the hour's start.
"""

import json

import pandas as pd

from saat.paths import RAW
from saat.sources.fetch import download

VARS = [
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "temperature_2m",
    "cloud_cover",
    "wind_speed_10m",
    "snow_depth",
]

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"


def _get(base: str, tag: str, lat: float, lon: float, start: str, end: str, variables: list[str]) -> pd.DataFrame:
    url = (
        f"{base}?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
        f"&hourly={','.join(variables)}&timezone=UTC"
    )
    name = f"openmeteo_{tag}_{lat:.4f}_{lon:.4f}_{start}_{end}.json"
    data = json.loads(download(url, name).read_text())
    df = pd.DataFrame(data["hourly"])
    df["utc"] = pd.to_datetime(df.pop("time")) - pd.Timedelta(hours=1)
    return df.set_index("utc").astype(float)


def reanalysis(lat: float, lon: float, start: str, end: str, variables: list[str] = VARS) -> pd.DataFrame:
    return _get(ARCHIVE, "era5", lat, lon, start, end, variables)


def day_ahead(lat: float, lon: float, start: str, end: str, variables: list[str] = VARS) -> pd.DataFrame:
    df = _get(PREVIOUS_RUNS, "dayahead", lat, lon, start, end, [f"{v}_previous_day1" for v in variables])
    return df.rename(columns=lambda c: c.removesuffix("_previous_day1"))
