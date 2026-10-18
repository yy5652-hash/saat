"""Physics of a PV plant from weather: sun position, clear sky, and a PVWatts-style AC estimate."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pvlib


@dataclass(frozen=True)
class Site:
    name: str
    lat: float
    lon: float
    elevation_m: float
    dc_kw: float
    ac_kw: float
    tracking: bool  # single-axis, north-south axis
    tilt: float = 0.0
    azimuth: float = 180.0
    utc_offset_h: float = 0.0  # local standard time, used only to group solar days

    @property
    def location(self) -> pvlib.location.Location:
        return pvlib.location.Location(self.lat, self.lon, altitude=self.elevation_m)


def solar_geometry(site: Site, hours: pd.DatetimeIndex) -> pd.DataFrame:
    """Sun position at mid-hour and clear-sky irradiance for hours labelled by their UTC start."""
    mid = pd.DatetimeIndex(hours).tz_localize("UTC") + pd.Timedelta(minutes=30)
    sp = site.location.get_solarposition(mid)
    cs = site.location.get_clearsky(mid, model="ineichen", solar_position=sp)
    # The sun is down for the whole hour if it is below the horizon at both ends.
    start = site.location.get_solarposition(mid - pd.Timedelta(minutes=30)).apparent_elevation.values
    end = site.location.get_solarposition(mid + pd.Timedelta(minutes=30)).apparent_elevation.values
    out = pd.DataFrame(
        {
            "zenith": sp.apparent_zenith.values,
            "azimuth": sp.azimuth.values,
            "cs_ghi": cs.ghi.values,
            "cs_dni": cs.dni.values,
            "cs_dhi": cs.dhi.values,
            "sun_down": (start < -0.5) & (end < -0.5),
        },
        index=hours,
    )
    out["dni_extra"] = pvlib.irradiance.get_extra_radiation(mid).values
    return out


def ac_estimate(site: Site, geo: pd.DataFrame, ghi, dni, dhi, temp_air, wind) -> np.ndarray:
    """Hourly AC energy (kWh) from irradiance via plane-of-array transposition and PVWatts."""
    if site.tracking:
        tr = pvlib.tracking.singleaxis(
            geo.zenith, geo.azimuth, axis_tilt=0, axis_azimuth=site.azimuth, max_angle=60, backtrack=True, gcr=0.35
        )
        tilt, azim = tr.surface_tilt.fillna(0), tr.surface_azimuth.fillna(site.azimuth)
    else:
        tilt, azim = site.tilt, site.azimuth
    poa = pvlib.irradiance.get_total_irradiance(
        tilt, azim, geo.zenith, geo.azimuth, dni, ghi, dhi, dni_extra=geo.dni_extra, model="haydavies"
    )["poa_global"].fillna(0).clip(lower=0)
    t_cell = pvlib.temperature.sapm_cell(poa, temp_air, wind / 3.6, a=-3.56, b=-0.075, deltaT=3)
    pdc = pvlib.pvsystem.pvwatts_dc(poa, t_cell, pdc0=site.dc_kw, gamma_pdc=-0.004)
    return np.clip(np.asarray(pdc) * 0.96, 0, site.ac_kw)


def features(site: Site, weather: pd.DataFrame) -> pd.DataFrame:
    """Model inputs for each UTC hour: weather, geometry, physics estimate and calendar."""
    geo = solar_geometry(site, weather.index)
    w = weather
    X = pd.DataFrame(index=weather.index)
    for c in ["shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation", "temperature_2m", "cloud_cover", "wind_speed_10m", "snow_depth"]:
        X[c] = w[c].values
    X["zenith"] = geo.zenith.values
    X["cos_zenith"] = np.cos(np.radians(geo.zenith.clip(upper=90))).values
    X["azimuth"] = geo.azimuth.values
    X["cs_ghi"] = geo.cs_ghi.values
    X["kt"] = (w.shortwave_radiation / geo.cs_ghi.where(geo.cs_ghi > 20)).clip(0, 1.5).fillna(0).values
    X["phys_cf"] = ac_estimate(site, geo, w.shortwave_radiation, w.direct_normal_irradiance, w.diffuse_radiation, w.temperature_2m, w.wind_speed_10m) / site.ac_kw
    clear = ac_estimate(site, geo, geo.cs_ghi, geo.cs_dni, geo.cs_dhi, w.temperature_2m, w.wind_speed_10m) / site.ac_kw
    X["clear_cf"] = clear
    for lag in (-1, 1):
        X[f"ghi_{'lead' if lag < 0 else 'lag'}"] = w.shortwave_radiation.shift(lag).bfill().ffill().values
        X[f"phys_{'lead' if lag < 0 else 'lag'}"] = X["phys_cf"].shift(lag).bfill().ffill().values
    doy = weather.index.dayofyear.values
    X["doy_sin"], X["doy_cos"] = np.sin(2 * np.pi * doy / 366), np.cos(2 * np.pi * doy / 366)
    X["sun_down"] = geo.sun_down.values.astype(int)
    return X


def local_day(site: Site, hours: pd.DatetimeIndex) -> pd.Index:
    return (pd.DatetimeIndex(hours) + pd.Timedelta(hours=site.utc_offset_h)).normalize()
