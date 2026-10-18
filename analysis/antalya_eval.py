"""Antalya community, all of 2025: annual claims versus hourly reality, and what P2P trading of
hourly certificates and AI day-ahead load shifting recover.

Supply is modelled from real ERA5 weather; day-ahead plans use the forecasts Open-Meteo archived
the day before (previous_day1). Forecasters are trained on Feb-Dec 2024 and scored on 2025.
Loads are synthetic (see saat/scenario.py).

    uv run python analysis/antalya_eval.py
"""

import json

import numpy as np
import pandas as pd

from saat.paths import RESULTS
from saat.planner import SupplyForecaster, cfe, shift_load, solar_features, wind_features
from saat.scenario import CAMPUS, HOTEL, SOLAR, WIND, Community, campus_kwh, contracted_supply, hotel_kwh, weather

FLEX = {"hotel_kwh": 0.15, "campus_kwh": 0.10}  # laundry, pool pumps, hot water, EV shuttles / pre-cooling


def main():
    actual = Community.build("2024-02-01", "2025-12-31").frame
    sw_fc = weather(SOLAR.lat, SOLAR.lon, "2024-02-01", "2025-12-31", day_ahead=True)
    ww_fc = weather(WIND["lat"], WIND["lon"], "2024-02-01", "2025-12-31", day_ahead=True)
    wh_fc = weather(HOTEL["lat"], HOTEL["lon"], "2024-02-01", "2025-12-31", day_ahead=True)
    wc_fc = weather(CAMPUS["lat"], CAMPUS["lon"], "2024-02-01", "2025-12-31", day_ahead=True)
    idx = actual.index.intersection(sw_fc.dropna(subset=["shortwave_radiation"]).index).intersection(ww_fc.dropna(subset=["wind_speed_100m"]).index)
    actual = actual.loc[idx]
    train, test = idx < "2025-01-01", idx >= "2025-01-01"

    forecasts, skill = {}, {}
    for col, feats, w in (("solar_kwh", solar_features, sw_fc), ("wind_kwh", wind_features, ww_fc)):
        X = feats(w.loc[idx].ffill())
        model = SupplyForecaster().fit(X[train], actual[col][train])
        fc = model.predict(X)
        forecasts[col] = fc
        y = actual[col][test]
        persistence = actual[col].shift(24)[test]
        cap = SOLAR.ac_kw if col == "solar_kwh" else WIND["mw"] * 1000
        skill[col] = {
            "ai_mae_pct_of_capacity": round(float((fc[test] - y).abs().mean() / cap * 100), 2),
            "physics_on_forecast_mae_pct": round(float((X["phys_kwh"][test] - y).abs().mean() / cap * 100), 2),
            "persistence_mae_pct": round(float((persistence - y).abs().mean() / cap * 100), 2),
        }
    load_fc = pd.DataFrame({"hotel_kwh": hotel_kwh(wh_fc.loc[idx]), "campus_kwh": campus_kwh(wc_fc.loc[idx])})

    year = actual[test]
    fc_year = pd.DataFrame({k: v[test] for k, v in forecasts.items()})
    lfc = load_fc[test]
    res = {"year": 2025, "hours": int(len(year)), "forecast_skill": skill, "consumers": {}}
    supplies, supplies_fc = {}, {}
    for col in ("hotel_kwh", "campus_kwh"):
        s, a, b = contracted_supply(year, col)
        supplies[col] = s
        supplies_fc[col] = a * fc_year.solar_kwh + b * fc_year.wind_kwh
        res["consumers"][col] = {
            "annual_mwh": round(float(year[col].sum() / 1000), 1),
            "annual_claim": 1.0,
            "hourly_cfe_alone": round(cfe(year[col], s), 4),
        }
    L = year.hotel_kwh + year.campus_kwh
    S = supplies["hotel_kwh"] + supplies["campus_kwh"]
    S_fc = supplies_fc["hotel_kwh"] + supplies_fc["campus_kwh"]
    flex = FLEX["hotel_kwh"] * year.hotel_kwh + FLEX["campus_kwh"] * year.campus_kwh
    L_fc = lfc.hotel_kwh + lfc.campus_kwh
    separate = (np.minimum(year.hotel_kwh, supplies["hotel_kwh"]).sum() + np.minimum(year.campus_kwh, supplies["campus_kwh"]).sum()) / L.sum()
    # Plan on forecasts, then score the plan against what actually happened.
    planned = shift_load(L_fc, S_fc, flex)
    shifted_ai = L + (planned - L_fc)
    shifted_perfect = shift_load(L, S, flex)
    res["community"] = {
        "annual_claim": 1.0,
        "hourly_cfe_separate": round(float(separate), 4),
        "hourly_cfe_p2p_pooled": round(cfe(L, S), 4),
        "hourly_cfe_p2p_plus_ai_shift": round(cfe(shifted_ai, S), 4),
        "hourly_cfe_p2p_plus_perfect_shift": round(cfe(shifted_perfect, S), 4),
        "energy_shifted_share": round(float((shifted_ai - L).clip(lower=0).sum() / L.sum()), 4),
        "flexible_share_of_load": round(float(flex.sum() / L.sum()), 4),
    }
    res["notes"] = {
        "supply": "modelled from ERA5 hourly weather at the plant coordinates (pvlib PVWatts; generic turbine curve)",
        "forecasts": "Open-Meteo previous-runs API, *_previous_day1, i.e. issued the day before",
        "loads": "synthetic, temperature-driven hotel and office profiles",
    }
    (RESULTS / "antalya_eval.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
