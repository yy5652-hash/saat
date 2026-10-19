"""The hourly matching gap on Türkiye's real 2025 grid, and a check of the Antalya models against it.

Data: EPİAŞ real-time generation by source and demand, via the IEA Real-Time Electricity Tracker
(CC BY 4.0); national solar scaled from licensed plants to Ember's monthly totals (see
saat/sources/iea_rte.py). A consumer buys wind and solar certificates equal to 100% of its annual
use as a pro-rata slice of Türkiye's real hourly fleet output.

    uv run python analysis/turkiye_gap.py
"""

import json

import numpy as np
import pandas as pd

from saat.matching import coverage_for, match
from saat.paths import RESULTS
from saat.scenario import Community
from saat.sources import eia930, iea_rte

def main():
    df = iea_rte.load((2024, 2025))
    year = df[(df.index >= "2024-12-31 21:00") & (df.index < "2025-12-31 21:00")]  # 2025 in Türkiye's local time
    ci = iea_rte.intensity(year).values
    loads = {"flat 24/7": np.ones(len(year)), "national demand shape": year.demand.values}
    portfolios = {
        "wind+solar": (year.wind + year.solar).values,
        "solar": year.solar.values,
        "wind": year.wind.values,
        "wind+solar, licensed solar only (wind-heavy sensitivity)": (year.wind + year.solar_licensed).values,
    }
    rows = []
    for lname, shape in loads.items():
        load = shape / shape.sum() * 1e6
        for pname, prof in portfolios.items():
            m = match(load, prof / prof.sum() * load.sum(), ci)
            rows.append({"load": lname, "portfolio": pname, **{k: round(float(v), 4) for k, v in m.items()},
                         "volume_for_90pct": coverage_for(load, prof, 0.9)})
    table = pd.DataFrame(rows)
    core = table[(table.load == "flat 24/7") & (table.portfolio == "wind+solar")].iloc[0]

    # The Antalya models against Türkiye's real 2025 profiles.
    com = Community.build("2025-01-01", "2025-12-31").frame
    com.index = pd.DatetimeIndex(com.index)
    j = com.join(year[["wind", "solar"]], how="inner")
    daily = j.groupby((j.index + pd.Timedelta(hours=3)).normalize()).sum()
    validation = {
        "hourly_r_modelled_aegean_wind_vs_national_wind": round(float(j.wind_kwh.corr(j.wind)), 3),
        "daily_r_modelled_aegean_wind_vs_national_wind": round(float(daily.wind_kwh.corr(daily.wind)), 3),
        "hourly_r_modelled_antalya_solar_vs_national_solar": round(float(j.solar_kwh.corr(j.solar)), 3),
        "daily_r_modelled_antalya_solar_vs_national_solar": round(float(daily.solar_kwh.corr(daily.solar)), 3),
    }
    # The hotel's contract, if its certificates followed Türkiye's real national wind and solar.
    h = j.hotel_kwh
    real = 0.5 * h.sum() * j.solar / j.solar.sum() + 0.5 * h.sum() * j.wind / j.wind.sum()
    validation["hotel_contract_only_cfe_with_real_national_profiles"] = round(float(np.minimum(h, real).sum() / h.sum()), 4)

    us = json.loads((RESULTS / "hourly_gap.json").read_text())
    summary = {
        "data": "EPİAŞ real-time generation by source and demand via the IEA Real-Time Electricity Tracker (CC BY 4.0), 2025, hourly; national solar scaled to Ember monthly totals",
        "annual_twh": {k: round(float(year[k].sum() / 1e6), 2) for k in ["wind", "solar", "solar_licensed", "hydro", "gas", "coal", "geothermal", "demand"]},
        "grid_kg_per_mwh": round(float((year.demand.values * ci).sum() / year.demand.sum()), 0),
        "flat_wind_solar_hourly_cfe": core.hourly_cfe,
        "flat_wind_solar_hidden_kg_per_mwh": round(core.hidden_kg_per_mwh, 0),
        "flat_wind_solar_surplus_share": core.surplus_share,
        "flat_wind_solar_volume_for_90pct": core.volume_for_90pct,
        "us_median_hourly_cfe": us["median_hourly_cfe"],
        "table": table.to_dict("records"),
        "validation": validation,
    }
    summary["table"] = [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()} for r in summary["table"]]
    (RESULTS / "turkiye_gap.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in summary.items() if k != "table"}, indent=2, ensure_ascii=False))
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
