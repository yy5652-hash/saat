"""The verifier's rules on a synthetic plant whose output is a noisy function of irradiance."""

import numpy as np
import pandas as pd
import pytest

from saat.pvmodel import Site, solar_geometry
from saat.verifier import IssuanceVerifier, report

SITE = Site("test plant", 36.9, 30.7, 50.0, dc_kw=1200.0, ac_kw=1000.0, tracking=False, tilt=28.0, utc_offset_h=3)


def synthetic(start, days, seed):
    idx = pd.date_range(start, periods=24 * days, freq="1h")
    geo = solar_geometry(SITE, idx)
    rng = np.random.default_rng(seed)
    cloud = np.repeat(rng.uniform(0.3, 1.0, days), 24)
    ghi = geo.cs_ghi.values * cloud
    X = pd.DataFrame({
        "ghi": ghi,
        "clear_cf": geo.cs_ghi.values / 1000 * 0.85,
        "sun_down": geo.sun_down.values.astype(int),
        "hour": (idx.hour + 3) % 24,
    }, index=idx)
    cf = pd.Series(np.clip(ghi / 1000 * 0.85 * rng.normal(1, 0.03, len(idx)), 0, 1), index=idx)
    cf[X.sun_down == 1] = 0.0
    return X, cf


@pytest.fixture(scope="module")
def fitted():
    X, cf = synthetic("2024-03-01", 120, 1)
    Xc, cfc = synthetic("2024-07-01", 40, 2)
    return IssuanceVerifier(SITE).fit(X, cf, Xc, cfc)


def test_honest_day_mostly_issued(fitted):
    X, cf = synthetic("2024-09-01", 20, 3)
    a = fitted.assess(X, cf)
    assert a.held.mean() < 0.03


def test_night_claims_are_held(fitted):
    X, cf = synthetic("2024-09-01", 5, 4)
    bad = cf.copy()
    bad[X.sun_down == 1] = 0.2
    a = fitted.assess(X, bad)
    night = (X.sun_down == 1).values
    assert a.held[night].all()
    assert set(a.reason[night]) == {"sun-down"}


def test_big_inflation_is_held(fitted):
    X, cf = synthetic("2024-09-01", 10, 5)
    a = fitted.assess(X, (cf * 1.6).clip(upper=1))
    day = (X.sun_down == 0).values & (cf.values > 0.1)
    assert a.held[day].mean() > 0.5


def test_report_hash_is_deterministic(fitted):
    X, cf = synthetic("2024-09-01", 1, 6)
    a = fitted.assess(X, cf)
    b1, h1 = report(SITE, 1, 20_000, a, "test")
    b2, h2 = report(SITE, 1, 20_000, a, "test")
    assert h1 == h2 and len(h1) == 32
    assert b1["held_hours"] == sum(1 for h in b1["hours"] if h["status"] == "held" and h["claimed_wh"] > 0)
