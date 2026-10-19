"""Hourly Türkiye generation by source and demand: EPİAŞ real-time data republished by the IEA
Real-Time Electricity Tracker (CC BY 4.0), no key needed.
https://www.iea.org/data-and-statistics/data-tools/real-time-electricity-tracker

Two facts about this feed shape the loader:
- Timestamps end in "Z" but are Türkiye local time (UTC+3, no DST since 2016); we convert to UTC.
- EPİAŞ real-time generation covers licensed plants only. Most Turkish PV is unlicensed, so the
  feed's solar is about a fifth of the national total. We scale the licensed hourly shape to
  Ember's monthly national solar (CC BY 4.0), assuming unlicensed PV has the same shape within a
  month, and keep the licensed-only series as a lower bound.
"""

import json
import time

import pandas as pd
import requests

from saat.matching import EMISSION_FACTORS
from saat.paths import PROCESSED, RAW

API = "https://api.iea.org/rte"
EMBER_MONTHLY = "https://storage.googleapis.com/emb-prod-bkt-publicdata/public-downloads/monthly_full_release_long_format.csv"
FUELS = {
    "Solar": "solar_licensed", "Wind": "wind", "Hydro": "hydro", "Natural Gas": "gas", "Coal": "coal",
    "Other renewables": "geothermal", "Biomass/Waste": "biomass", "Thermal": "other_thermal",
    "Other combustibles": "other_combustibles", "Nuclear": "nuclear",
}
TR_DIR = RAW / "tr"
# Life-cycle kg CO2e per MWh by source (IPCC AR5 medians, as in saat.matching); biomass 230, other thermal as oil.
CARBON = {
    "coal": EMISSION_FACTORS["coal"], "gas": EMISSION_FACTORS["gas"], "hydro": EMISSION_FACTORS["hydro"],
    "geothermal": EMISSION_FACTORS["geothermal"], "biomass": 230, "wind": EMISSION_FACTORS["wind"],
    "solar": EMISSION_FACTORS["solar"], "other_thermal": EMISSION_FACTORS["oil"], "other_combustibles": EMISSION_FACTORS["oil"],
    "nuclear": EMISSION_FACTORS["nuclear"],
}


def intensity(df: pd.DataFrame) -> pd.Series:
    """Average intensity of Türkiye's own generation each hour, kg CO2e per MWh (imports excluded)."""
    g = df[list(CARBON)].clip(lower=0)
    return sum(g[k] * v for k, v in CARBON.items()) / g.sum(axis=1)


def _fetch_year(series: str, year: int) -> list[dict]:
    path = TR_DIR / f"iea_rte_TUR_{series}_hourly_{year}.json"
    if path.exists():
        return json.loads(path.read_text())["rows"]
    rows = []
    for month in range(1, 13):
        start = f"{year}-{month:02d}-01"
        end = f"{year + (month == 12)}-{month % 12 + 1:02d}-01"
        for attempt in range(4):
            try:
                r = requests.get(f"{API}/{series}/timeseries", params={"from": start, "to": end, "precision": "hour", "ISO3": "TUR"}, timeout=180)
                r.raise_for_status()
                chunk = r.json()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        rows += [c for c in chunk if c["Date"][:7] == start[:7]]  # "to" includes 00:00 of the next month
        time.sleep(1)
    TR_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "IEA Real-Time Electricity Tracker (EPİAŞ)", "licence": "CC BY 4.0", "units": "GW", "rows": rows}))
    return rows


def _ember_monthly_solar_mwh() -> pd.Series:
    path = TR_DIR / "ember_TUR_monthly_solar.csv"
    if not path.exists():
        e = pd.read_csv(EMBER_MONTHLY)
        e = e[(e["ISO 3 code"] == "TUR") & (e["Category"] == "Electricity generation") & (e["Variable"] == "Solar") & (e["Unit"] == "TWh")]
        TR_DIR.mkdir(parents=True, exist_ok=True)
        e[["Date", "Value"]].to_csv(path, index=False)
    e = pd.read_csv(path)
    return pd.Series(e.Value.values * 1e6, index=pd.PeriodIndex(e.Date, freq="M"))


def load(years=(2024, 2025)) -> pd.DataFrame:
    """Hourly MW (= MWh per hour) by source, demand, and national solar estimate; UTC index."""
    out = PROCESSED / f"tr_hourly_{years[0]}_{years[-1]}.parquet"
    if out.exists():
        return pd.read_parquet(out)
    frames = []
    for y in years:
        g = pd.DataFrame(_fetch_year("generation", y)).pivot_table(index="Date", columns="Fuels", values="Value", aggfunc="first")
        g = g.rename(columns=FUELS) * 1000.0
        d = pd.DataFrame(_fetch_year("demand", y)).set_index("Date")["Value"].rename("demand") * 1000.0
        frames.append(g.join(d, how="outer"))
    df = pd.concat(frames).sort_index()
    df["nuclear"] = df.get("nuclear", pd.Series(0.0, index=df.index)).fillna(0.0)
    df.index = pd.to_datetime(df.index.str.slice(0, 19)).tz_localize("Europe/Istanbul").tz_convert("UTC").tz_localize(None)
    df.index.name = "utc"
    df["solar_licensed"] = df["solar_licensed"].clip(lower=0)
    month = (df.index + pd.Timedelta(hours=3)).to_period("M")
    factor = (_ember_monthly_solar_mwh() / df["solar_licensed"].groupby(month).sum()).reindex(month).to_numpy()
    df["solar"] = df["solar_licensed"] * factor
    df.to_parquet(out)
    return df
