"""Can the issuance verifier stop fake certificates without blocking real ones?

Real data: PVDAQ system 9068, a 4.7 MWdc tracker plant in Kersey, Colorado (NREL / DOE OEDI),
hourly AC energy 2017-2025, against ERA5 weather from Open-Meteo for the same coordinates.
Train <= 2022, calibrate on 2023, test on 2024-01 .. 2025-04 (16 months the model never saw).

Fraud is simulated on the real test data: inflated readings, output shifted into later (scarcer,
pricier) hours, a sunny day's readings replayed on a cloudy day, power claimed after sunset, and
single-hour spikes. Baselines are what registries check today (nameplate capacity) and a pure
physics bound (clear-sky potential).

    uv run python analysis/verifier_eval.py
"""

import json

import numpy as np
import pandas as pd

from saat.paths import RESULTS
from saat.pvmodel import Site, features, local_day
from saat.sources import openmeteo, pvdaq
from saat.verifier import CAPACITY_MARGIN, NIGHT_CF, IssuanceVerifier

SITE = Site("PVDAQ 9068, Kersey CO", 40.3864, -104.5512, 1407.0, dc_kw=4738.0, ac_kw=3820.0, tracking=True, utc_offset_h=-7)


def load():
    pv = pvdaq.hourly_energy().kwh.clip(lower=0)
    w = openmeteo.reanalysis(SITE.lat, SITE.lon, "2017-08-20", "2025-05-01")
    idx = pv.index.intersection(w.index)
    X = features(SITE, w.loc[idx])
    cf = (pv.loc[idx] / SITE.ac_kw).rename("cf")
    return X, cf


# ---------------------------------------------------------------- attacks on real test data


def attack(name: str, X: pd.DataFrame, cf: pd.Series) -> pd.Series:
    days = local_day(SITE, X.index)
    c = cf.copy()
    if name.startswith("inflate"):
        x = int(name.split("_")[1]) / 100
        return (cf * (1 + x)).clip(upper=1.0)
    if name.startswith("shift"):
        k = int(name.split("_")[1])
        return cf.shift(k).fillna(0)
    if name == "replay":
        daily = cf.groupby(days).sum()
        best = daily.rolling(7, min_periods=1).apply(lambda s: s.index[np.argmax(s.values)].value if len(s) else np.nan, raw=False)
        prof = {d: cf[days == d].values for d in daily.index}
        out = []
        for d in daily.index:
            src = pd.Timestamp(int(best.loc[d])) if not np.isnan(best.loc[d]) else d
            a, b = prof[d], prof.get(src, prof[d])
            out.append(b if len(b) == len(a) and b.sum() > a.sum() else a)
        return pd.Series(np.concatenate(out), index=cf.index)
    if name == "after_sunset":
        down = X["sun_down"].values == 1
        first = down & ~np.roll(down, 1)
        second = down & np.roll(first, 1)
        c[first | second] = c[first | second] + 0.05
        return c
    if name == "spike":
        exp = X["phys_cf"].groupby(days).transform(lambda s: s == s.max()).values.astype(bool)
        c[exp] = (cf[exp] * 1.5).clip(upper=1.0)
        return c
    raise ValueError(name)


ATTACKS = ["inflate_5", "inflate_10", "inflate_20", "inflate_50", "shift_1", "shift_2", "replay", "after_sunset", "spike"]


# ---------------------------------------------------------------- detectors


def detect_nameplate(X, claimed, _pred=None):
    return (claimed > 1 + CAPACITY_MARGIN).values


def detect_physics(X, claimed, _pred=None):
    bound = X["clear_cf"] * 1.05 + 0.01
    return ((claimed > bound) | ((X["sun_down"] == 1) & (claimed > NIGHT_CF))).values


def scores(held: np.ndarray, X, actual: pd.Series, claimed: pd.Series) -> dict:
    excess = (claimed - actual).clip(lower=0).values
    days = local_day(SITE, X.index)
    df = pd.DataFrame({"held": held, "excess": excess}, index=days)
    by_day = df.groupby(level=0).agg(held=("held", "any"), excess=("excess", "sum"))
    attacked = by_day[by_day.excess > 0.01]
    return {
        "attacked_days": int(len(attacked)),
        "days_caught": round(float(attacked.held.mean()), 4) if len(attacked) else None,
        "fake_energy_blocked": round(float(excess[held].sum() / excess.sum()), 4) if excess.sum() > 0 else None,
    }


def clean_scores(held: np.ndarray, X, cf: pd.Series) -> dict:
    day = (X["sun_down"] == 0).values
    days = local_day(SITE, X.index)
    return {
        "clean_energy_held": round(float(cf.values[held].sum() / cf.sum()), 4),
        "clean_daylight_hours_held": round(float(held[day].mean()), 4),
        "clean_days_with_any_hold": round(float(pd.Series(held, index=days).groupby(level=0).any().mean()), 4),
    }


def drift_eval(ver: IssuanceVerifier, Xt, cft, pred) -> dict:
    """Persistent over-claims that start on many different days of the test period."""
    days = local_day(SITE, Xt.index)
    base = ver.daily(Xt, cft, pred)
    clean = ver.drift_stat(base)
    starts = base.index[ver.drift_window : len(base) - 90 : 7]
    out = {
        "window_days": ver.drift_window,
        "statistic": "rolling median of daily claimed/expected energy",
        "limit": round(ver.drift_limit, 4),
        "clean_max": round(float(clean.max()), 4),
        "clean_alarm_days": int((clean > ver.drift_limit).sum()),
        "clean_days_checked": int(len(clean)),
        "persistent_inflation": {},
    }
    for x in (0.05, 0.10, 0.20, 0.30):
        delays = []
        for s0 in starts:
            claimed = cft.where(days < s0, (cft * (1 + x)).clip(upper=1.0))
            daily = base.assign(c=pd.Series(claimed.values, index=days).groupby(level=0).sum())
            r = ver.drift_stat(daily)
            hit = r[(r.index >= s0) & (r > ver.drift_limit)]
            delays.append((hit.index[0] - s0).days if len(hit) else None)
        found = [d for d in delays if d is not None]
        out["persistent_inflation"][f"{int(x * 100)}%"] = {
            "trials": len(delays),
            "caught_within_30d": round(sum(d <= 30 for d in found) / len(delays), 3),
            "caught_within_90d": round(sum(d <= 90 for d in found) / len(delays), 3),
            "median_days_to_catch": float(np.median(found)) if found else None,
        }
    return out


def main():
    X, cf = load()
    train = X.index < "2023-01-01"
    cal = (X.index >= "2023-01-01") & (X.index < "2024-01-01")
    test = X.index >= "2024-01-01"
    ver = IssuanceVerifier(SITE).fit(X[train], cf[train], X[cal], cf[cal])

    Xt, cft = X[test], cf[test]
    keep = cft.notna().values
    Xt, cft = Xt[keep], cft[keep]
    pred = ver.predict(Xt)
    day = (Xt["sun_down"] == 0).values
    err = (pred.expected - cft)[day]
    phys_err = (Xt["phys_cf"] - cft)[day]
    accuracy = {
        "test_hours": int(len(cft)),
        "ml_mae_pct_of_ac": round(float(err.abs().mean() * 100), 2),
        "physics_mae_pct_of_ac": round(float(phys_err.abs().mean() * 100), 2),
        "ml_r2": round(float(1 - (err**2).sum() / ((cft[day] - cft[day].mean()) ** 2).sum()), 3),
        "physics_r2": round(float(1 - (phys_err**2).sum() / ((cft[day] - cft[day].mean()) ** 2).sum()), 3),
        "upper_coverage": round(float((cft[day] <= pred.upper[day]).mean()), 4),
        "tau": round(ver.tau, 4),
        "rho": round(ver.rho, 4),
    }

    def saat(Xa, claimed):
        return ver.assess(Xa, claimed).held.values

    def saat_hourly_only(Xa, claimed):
        a = ver.assess(Xa, claimed)
        return (a.held & ~a.reason.isin(["day-total-anomaly", "day-shape-anomaly"])).values

    detectors = {"nameplate check": detect_nameplate, "physics bound": detect_physics, "Saat AI (hourly rules)": saat_hourly_only, "Saat AI": saat}
    results = {"accuracy": accuracy, "clean": {}, "attacks": {}}
    for dname, det in detectors.items():
        results["clean"][dname] = clean_scores(det(Xt, cft), Xt, cft)
    for a in ATTACKS:
        claimed = attack(a, Xt, cft)
        results["attacks"][a] = {dname: scores(det(Xt, claimed), Xt, cft, claimed) for dname, det in detectors.items()}

    results["drift"] = drift_eval(ver, Xt, cft, pred)
    results["data"] = {
        "plant": "PVDAQ system 9068 (SR_CO), 4,738 kWdc / 3,820 kWac single-axis tracker, Kersey CO",
        "weather": "ERA5 via Open-Meteo archive API, hourly",
        "train": "2017-08 .. 2022-12",
        "calibrate": "2023",
        "test": "2024-01 .. 2025-04",
    }
    (RESULTS / "verifier_eval.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results["accuracy"], indent=2))
    print(pd.DataFrame(results["clean"]).T.to_string())
    rows = []
    for a, per in results["attacks"].items():
        for dname, s in per.items():
            rows.append({"attack": a, "detector": dname, **s})
    t = pd.DataFrame(rows).pivot(index="attack", columns="detector", values="fake_energy_blocked")
    print("\nfake energy blocked\n", t[list(detectors)].loc[ATTACKS].to_string())
    t = pd.DataFrame(rows).pivot(index="attack", columns="detector", values="days_caught")
    print("\ndays caught\n", t[list(detectors)].loc[ATTACKS].to_string())
    print(json.dumps(results["drift"], indent=2))


if __name__ == "__main__":
    main()
