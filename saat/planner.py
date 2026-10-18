"""AI procurement planner: forecast tomorrow's certificates hour by hour, then move flexible load
into the hours where certificates will be in surplus.

The forecaster sees only what was knowable the day before (Open-Meteo previous-day forecasts);
gradient boosting corrects the physical model's systematic forecast errors.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from saat.scenario import UTC_OFFSET, solar_kwh, wind_cf, wind_kwh


def _calendar(idx: pd.DatetimeIndex) -> pd.DataFrame:
    loc = pd.DatetimeIndex(idx) + pd.Timedelta(hours=UTC_OFFSET)
    doy = loc.dayofyear.values
    return pd.DataFrame({"hour": loc.hour.values, "doy_sin": np.sin(2 * np.pi * doy / 366), "doy_cos": np.cos(2 * np.pi * doy / 366)}, index=idx)


def solar_features(fc: pd.DataFrame) -> pd.DataFrame:
    X = _calendar(fc.index)
    X["phys_kwh"] = solar_kwh(fc).values
    for c in ["shortwave_radiation", "cloud_cover", "temperature_2m"]:
        X[c] = fc[c].values
    return X


def wind_features(fc: pd.DataFrame) -> pd.DataFrame:
    X = _calendar(fc.index)
    X["phys_kwh"] = wind_kwh(fc).values
    X["speed"] = fc.wind_speed_100m.values
    X["speed_lag"] = fc.wind_speed_100m.shift(1).bfill().values
    X["speed_lead"] = fc.wind_speed_100m.shift(-1).ffill().values
    return X


class SupplyForecaster:
    def __init__(self):
        self.model = HistGradientBoostingRegressor(loss="absolute_error", max_iter=400, learning_rate=0.05, min_samples_leaf=30, random_state=0)

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "SupplyForecaster":
        self.model.fit(X, y)
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(np.clip(self.model.predict(X), 0, None), index=X.index)


def shift_load(load: pd.Series, supply: pd.Series, flex: pd.Series, window=(6, 23), cap: float = 0.6) -> pd.Series:
    """Move flexible energy within each local day from forecast-deficit hours to forecast-surplus
    hours. `flex` is the energy per hour that can move; receiving hours lie in the local `window`
    and may rise by at most `cap` x their own load. Each day's total energy is unchanged."""
    out = np.array(load.values, dtype=float)
    loc = pd.DatetimeIndex(load.index) + pd.Timedelta(hours=UTC_OFFSET)
    for _, idx in pd.Series(range(len(load)), index=loc.normalize()).groupby(level=0):
        i = idx.values
        L, S, F = load.values[i], supply.values[i], flex.values[i]
        hours = loc.hour.values[i]
        give = np.minimum(np.maximum(L - S, 0), F)
        room = np.where((hours >= window[0]) & (hours < window[1]), np.minimum(np.maximum(S - L, 0), cap * L), 0)
        amount = min(give.sum(), room.sum())
        if amount <= 0:
            continue
        new = L.copy()
        left = amount
        for j in np.argsort(-give):  # take from the deepest deficits first
            t = min(give[j], left)
            new[j] -= t
            left -= t
            if left <= 1e-9:
                break
        left = amount
        for j in np.argsort(-room):  # fill the largest surpluses first
            t = min(room[j], left)
            new[j] += t
            left -= t
            if left <= 1e-9:
                break
        out[i] = new
    return pd.Series(out, index=load.index)


def plan_day_ahead(load_fc: pd.Series, own_fc: pd.Series, flex_share: float) -> pd.Series:
    """The planner's load schedule from forecasts only: flexible load moved into forecast surplus."""
    return shift_load(load_fc, own_fc, flex_share * load_fc)


def market_buy(planned_fc: pd.Series, own_fc: pd.Series, listed: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Bid each hour's forecast shortfall; delivery is capped by the certificates actually listed for
    that hour (other plants' uncontracted output). Returns (bid, delivered), both in kWh."""
    bid = (planned_fc - own_fc).clip(lower=0)
    return bid, np.minimum(bid, listed.clip(lower=0))


def cfe(load, supply) -> float:
    load, supply = np.asarray(load, float), np.asarray(supply, float)
    return float(np.minimum(load, supply).sum() / load.sum())


__all__ = ["SupplyForecaster", "shift_load", "plan_day_ahead", "market_buy", "cfe", "solar_features", "wind_features", "wind_cf"]
