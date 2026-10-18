"""The Antalya demonstration community (COP31 host city), Türkiye's single bidding zone "TR".

Generation is modelled from real hourly ERA5 weather at the real coordinates; the two consumer
load profiles are synthetic (temperature-driven, with Antalya's tourist season) and labelled so.
Nothing here describes a real company.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from saat.pvmodel import Site, ac_estimate, solar_geometry
from saat.sources import openmeteo

ZONE = "TR"
UTC_OFFSET = 3  # Türkiye, no DST

SOLAR = Site("Antalya community solar", 36.90, 30.70, 50.0, dc_kw=2400.0, ac_kw=2000.0, tracking=False, tilt=28.0, azimuth=180.0, utc_offset_h=UTC_OFFSET)
WIND = {"name": "Aegean wind share (Karaburun)", "lat": 38.60, "lon": 26.50, "mw": 6.0}
HOTEL = {"name": "Seaside hotel, Lara (synthetic load)", "lat": 36.86, "lon": 30.82}
CAMPUS = {"name": "Technopark office campus (synthetic load)", "lat": 36.90, "lon": 30.65}

WEATHER_VARS = openmeteo.VARS + ["wind_speed_100m"]


def weather(lat: float, lon: float, start: str, end: str, day_ahead: bool = False) -> pd.DataFrame:
    fn = openmeteo.day_ahead if day_ahead else openmeteo.reanalysis
    return fn(lat, lon, start, end, WEATHER_VARS)


# ------------------------------------------------------------------ generation (kWh per hour)


def solar_kwh(w: pd.DataFrame) -> pd.Series:
    w = w.ffill().fillna(0)
    geo = solar_geometry(SOLAR, w.index)
    kwh = ac_estimate(SOLAR, geo, w.shortwave_radiation, w.direct_normal_irradiance, w.diffuse_radiation, w.temperature_2m, w.wind_speed_10m)
    kwh = np.where(geo.sun_down.values, 0.0, kwh * 0.97)  # soiling and availability
    return pd.Series(kwh, index=w.index, name="solar_kwh")


def wind_cf(speed_kmh) -> np.ndarray:
    """Generic 3.5 MW-class turbine power curve at 100 m hub height, 10% wake and availability loss."""
    v = np.asarray(speed_kmh, float) / 3.6
    cut_in, rated, cut_out = 3.0, 11.5, 25.0
    cf = np.clip((v**3 - cut_in**3) / (rated**3 - cut_in**3), 0, 1)
    cf[(v < cut_in) | (v >= cut_out)] = 0.0
    return cf * 0.90


def wind_kwh(w: pd.DataFrame) -> pd.Series:
    return pd.Series(wind_cf(w.wind_speed_100m.ffill().fillna(0)) * WIND["mw"] * 1000, index=w.index, name="wind_kwh")


# ------------------------------------------------------------------ synthetic loads (kWh per hour)

# Share of rooms occupied by month: Antalya's season runs April to October.
OCCUPANCY = {1: 0.35, 2: 0.38, 3: 0.50, 4: 0.70, 5: 0.90, 6: 1.0, 7: 1.0, 8: 1.0, 9: 0.97, 10: 0.85, 11: 0.50, 12: 0.40}
HOTEL_SHAPE = np.array([0.62, 0.58, 0.56, 0.55, 0.56, 0.62, 0.80, 0.98, 1.05, 0.98, 0.92, 0.90,
                        0.95, 0.93, 0.90, 0.90, 0.95, 1.05, 1.20, 1.28, 1.25, 1.12, 0.92, 0.75])
OFFICE_SHAPE = np.array([0.30, 0.28, 0.28, 0.28, 0.28, 0.30, 0.45, 0.75, 1.10, 1.25, 1.30, 1.30,
                         1.25, 1.30, 1.30, 1.25, 1.10, 0.85, 0.60, 0.45, 0.40, 0.36, 0.33, 0.31])


def _local(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(idx) + pd.Timedelta(hours=UTC_OFFSET)


def hotel_kwh(w: pd.DataFrame) -> pd.Series:
    loc = _local(w.index)
    occ = np.array([OCCUPANCY[m] for m in loc.month])
    t = w.temperature_2m.ffill().values
    base = 210 * HOTEL_SHAPE[loc.hour] * (0.45 + 0.55 * occ)
    cooling = 14.0 * np.clip(t - 22, 0, None) * occ  # chillers
    heating = 6.0 * np.clip(15 - t, 0, None)  # heat pumps
    return pd.Series(base + cooling + heating, index=w.index, name="hotel_kwh")


def campus_kwh(w: pd.DataFrame) -> pd.Series:
    loc = _local(w.index)
    weekday = loc.dayofweek < 5
    t = w.temperature_2m.ffill().values
    shape = np.where(weekday, OFFICE_SHAPE[loc.hour], 0.35 + 0.1 * (OFFICE_SHAPE[loc.hour] > 1))
    cooling = 7.0 * np.clip(t - 24, 0, None) * np.where(weekday & (loc.hour >= 8) & (loc.hour < 19), 1.0, 0.25)
    return pd.Series(120 * shape + cooling, index=w.index, name="campus_kwh")


@dataclass
class Community:
    """Hourly supply and demand of the demo community, kWh, UTC index."""

    frame: pd.DataFrame

    @classmethod
    def build(cls, start: str, end: str, day_ahead: bool = False) -> "Community":
        ws = weather(SOLAR.lat, SOLAR.lon, start, end, day_ahead)
        ww = weather(WIND["lat"], WIND["lon"], start, end, day_ahead)
        wh = weather(HOTEL["lat"], HOTEL["lon"], start, end, day_ahead)
        wc = weather(CAMPUS["lat"], CAMPUS["lon"], start, end, day_ahead)
        df = pd.concat([solar_kwh(ws), wind_kwh(ww), hotel_kwh(wh), campus_kwh(wc)], axis=1).dropna()
        return cls(df)


def contracted_supply(year: pd.DataFrame, load_col: str, solar_share: float = 0.5) -> tuple[pd.Series, float, float]:
    """A consumer's certificate stream: fixed shares of the solar and wind plants sized so the year's
    certificates equal 100% of its annual load, split `solar_share` solar / rest wind by energy."""
    need = year[load_col].sum()
    a = solar_share * need / year.solar_kwh.sum()
    b = (1 - solar_share) * need / year.wind_kwh.sum()
    return a * year.solar_kwh + b * year.wind_kwh, a, b
