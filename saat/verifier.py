"""AI issuance verifier: decide which metered hours may become certificates.

A blockchain makes a certificate permanent; it cannot tell whether the meter reading behind it was
true. Before an issuer commits a day of generation, the verifier checks every hour against an
independent physical record (ERA5 reanalysis weather, sun position) with gradient-boosted quantile
models trained on the plant's own history. Over-claims are held back from the Merkle root; the
decision report is hashed and that hash is committed on-chain next to the root.
"""

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from saat.pvmodel import Site, local_day

VERSION = "saat-verifier/1.0"
NIGHT_CF = 0.005  # more than 0.5% of AC capacity while the sun is down
CAPACITY_MARGIN = 0.02
MIN_DAY_POTENTIAL = 0.5  # clear-sky capacity-factor hours below which the day-level test is skipped


def _model(q: float) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="quantile", quantile=q, max_iter=500, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40, random_state=0
    )


@dataclass
class IssuanceVerifier:
    site: Site
    target: float = 0.995  # share of clean calibration hours/days that must pass
    tau: float = field(default=np.nan)
    rho: float = field(default=np.nan)
    kappa: float = field(default=np.nan)
    drift_limit: float = field(default=np.nan)
    drift_window: int = 30

    def fit(self, X: pd.DataFrame, cf: pd.Series, X_cal: pd.DataFrame, cf_cal: pd.Series) -> "IssuanceVerifier":
        ok = cf.notna()
        self.columns = list(X.columns)
        self.median_model = _model(0.5).fit(X[ok], cf[ok])
        self.upper_model = _model(0.99).fit(X[ok], cf[ok])
        ok = cf_cal.notna()
        pred = self.predict(X_cal[ok])
        day = X_cal[ok]["sun_down"] == 0
        self.tau = float(np.quantile((cf_cal[ok] - pred["upper"])[day], self.target))
        d = self._day_excess(X_cal[ok], cf_cal[ok], pred)
        self.rho = float(np.quantile(d.dropna(), self.target))
        shift = self._day_shift(X_cal[ok], cf_cal[ok], pred)
        self.kappa = float(np.quantile(shift.dropna().abs(), self.target))
        # A persistent over-claim of a few percent hides in any single day's weather noise but not
        # in a month: the limit is the highest 30-day claimed/expected ratio seen in a clean year.
        self.drift_limit = float(self.drift(X_cal[ok], cf_cal[ok]).max()) + 0.01
        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X[self.columns]
        med = np.clip(self.median_model.predict(X), 0, 1)
        up = np.clip(self.upper_model.predict(X), 0, 1)
        night = X["sun_down"].values == 1
        med[night], up[night] = 0.0, 0.0
        return pd.DataFrame({"expected": med, "upper": np.maximum(up, med)}, index=X.index)

    def _day_excess(self, X, cf, pred) -> pd.Series:
        """Per local day: (claimed − expected) as a share of the day's clear-sky potential."""
        days = local_day(self.site, X.index)
        g = pd.DataFrame({"c": cf.values, "e": pred["expected"].values, "p": X["clear_cf"].values}, index=days).groupby(level=0).sum()
        out = (g.c - g.e) / g.p
        return out.where(g.p >= MIN_DAY_POTENTIAL)

    def _day_shift(self, X, cf, pred) -> pd.Series:
        """Per local day: hours by which the claimed output's centre of mass trails the expected."""
        days = local_day(self.site, X.index)
        h = (pd.DatetimeIndex(X.index) + pd.Timedelta(hours=self.site.utc_offset_h)).hour.values + 0.5
        df = pd.DataFrame({"c": cf.values, "ch": cf.values * h, "e": pred["expected"].values, "eh": pred["expected"].values * h, "p": X["clear_cf"].values}, index=days)
        g = df.groupby(level=0).sum()
        out = g.ch / g.c.where(g.c > 0) - g.eh / g.e.where(g.e > 0)
        return out.where(g.p >= MIN_DAY_POTENTIAL)

    def daily(self, X: pd.DataFrame, cf: pd.Series, pred: pd.DataFrame | None = None) -> pd.DataFrame:
        """Claimed and expected energy per local day (capacity-factor hours) and the hours seen."""
        pred = self.predict(X) if pred is None else pred
        days = local_day(self.site, X.index)
        return pd.DataFrame({"c": cf.values, "e": pred["expected"].values, "n": 1}, index=days).groupby(level=0).sum()

    def drift_stat(self, daily: pd.DataFrame) -> pd.Series:
        """Rolling median of daily claimed/expected over `drift_window` days. The median ignores
        outage days (which only pull claims down); a persistent over-claim moves it."""
        d = daily[(daily.n >= 20) & (daily.e > 2)]
        r = d.c / d.e
        return r.rolling(f"{self.drift_window}D", min_periods=8).median().dropna()

    def drift(self, X: pd.DataFrame, cf: pd.Series) -> pd.Series:
        return self.drift_stat(self.daily(X, cf))

    def assess(self, X: pd.DataFrame, cf: pd.Series) -> pd.DataFrame:
        """One row per hour: expected and upper capacity factor, decision and reason."""
        pred = self.predict(X)
        out = pred.assign(claimed=cf.values, held=False, reason="")
        rules = [
            ("over-capacity", out.claimed > 1 + CAPACITY_MARGIN),
            ("sun-down", (X["sun_down"].values == 1) & (out.claimed > NIGHT_CF)),
            ("above-weather-envelope", (out.claimed - out.upper) > self.tau),
        ]
        for reason, mask in rules:
            new = mask & ~out.held
            out.loc[new, "reason"] = reason
            out.loc[new, "held"] = True
        days = local_day(self.site, X.index)
        excess = self._day_excess(X, cf, pred)
        shift = self._day_shift(X, cf, pred)
        for reason, bad in (("day-total-anomaly", excess > self.rho), ("day-shape-anomaly", shift.abs() > self.kappa)):
            bad_days = set(bad[bad].index)
            mask = np.array([d in bad_days for d in days]) & (out.claimed > 0).values & ~out.held.values
            out.loc[mask, "reason"] = reason
            out.loc[mask, "held"] = True
        out.loc[out.claimed.isna(), ["held", "reason"]] = [True, "missing"]
        return out


def report(site: Site, device_id: int, day: int, assessed: pd.DataFrame, data_source: str) -> tuple[dict, bytes]:
    """The issuance report for one UTC day and its SHA-256 (the value committed on-chain)."""
    ac_wh = site.ac_kw * 1000
    hours = []
    for ts, r in assessed.iterrows():
        hours.append(
            {
                "utc": ts.strftime("%Y-%m-%dT%H:00Z"),
                "claimed_wh": int(round(0 if np.isnan(r.claimed) else r.claimed * ac_wh)),
                "expected_wh": int(round(r.expected * ac_wh)),
                "upper_wh": int(round(r.upper * ac_wh)),
                "status": "held" if r.held else "issued",
                "reason": r.reason,
            }
        )
    body = {
        "verifier": VERSION,
        "device_id": device_id,
        "site": site.name,
        "utc_day": day,
        "data_source": data_source,
        "hours": hours,
        "issued_wh": sum(h["claimed_wh"] for h in hours if h["status"] == "issued"),
        "held_hours": sum(h["status"] == "held" and h["claimed_wh"] > 0 for h in hours),
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return body, hashlib.sha256(blob).digest()
