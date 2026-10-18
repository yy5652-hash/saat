"""How much of an 'annual 100% renewable' claim survives hour-by-hour accounting?

Real data: EIA-930 hourly generation by fuel and demand for U.S. balancing authorities, 2025.
A consumer buys certificates equal to 100% of its annual load from its own balancing authority's
wind and solar fleet (a pro-rata slice of the real hourly output). Under annual matching it reports
100% renewable; under hourly matching only the overlap counts.

    uv run python analysis/hourly_gap.py
"""

import json

import numpy as np
import pandas as pd

from saat.matching import VRE, coverage_for, grid_intensity, match
from saat.paths import RESULTS
from saat.sources import eia930

MIN_DEMAND_MWH = 20e6
MIN_VRE_SHARE = 0.03


def main():
    df = eia930.load_year(2025)
    df = df[(df.utc >= "2025-01-01") & (df.utc < "2026-01-01")]
    rows = []
    for ba, g in df.groupby("ba"):
        g = g.set_index("utc").sort_index()
        g = g[~g.index.duplicated()].asfreq("1h")
        demand = g["demand"].interpolate(limit=6)
        if demand.isna().any() or demand.sum() < MIN_DEMAND_MWH:
            continue
        vre = g[VRE].clip(lower=0).sum(axis=1).fillna(0)
        if vre.sum() / demand.sum() < MIN_VRE_SHARE:
            continue
        intensity = grid_intensity(g.fillna(0)).values
        portfolios = {
            "wind+solar": vre.values,
            "solar": g[["solar", "solar_batt"]].clip(lower=0).sum(axis=1).fillna(0).values,
            "wind": g[["wind", "wind_batt"]].clip(lower=0).sum(axis=1).fillna(0).values,
        }
        loads = {"flat 24/7": np.ones(len(g)), "regional shape": demand.values}
        for load_name, shape in loads.items():
            load = shape / shape.sum() * 1_000_000  # 1 TWh/yr consumer; results are scale-free
            for pname, prof in portfolios.items():
                if prof.sum() <= 0:
                    continue
                supply = prof / prof.sum() * load.sum()  # exactly 100% of annual load
                m = match(load, supply, intensity)
                rows.append(
                    {
                        "ba": ba,
                        "region": g["region"].dropna().iloc[0],
                        "load": load_name,
                        "portfolio": pname,
                        "demand_twh": round(demand.sum() / 1e6, 1),
                        "grid_kg_per_mwh": round(float((demand.values * intensity).sum() / demand.sum()), 0),
                        **{k: round(float(v), 4) for k, v in m.items()},
                        "volume_for_90pct": coverage_for(load, prof, 0.9),
                    }
                )
    table = pd.DataFrame(rows)
    table.to_csv(RESULTS / "hourly_gap.csv", index=False)

    core = table[(table.load == "flat 24/7") & (table.portfolio == "wind+solar")].sort_values("demand_twh", ascending=False)
    summary = {
        "data": "EIA-930 BALANCE 2025 (adjusted), hourly, UTC",
        "consumer": "flat 24/7 load buying 100% of annual volume from its balancing authority's wind+solar fleet",
        "balancing_authorities": int(core.ba.nunique()),
        "median_hourly_cfe": round(float(core.hourly_cfe.median()), 3),
        "range_hourly_cfe": [round(float(core.hourly_cfe.min()), 3), round(float(core.hourly_cfe.max()), 3)],
        "median_hidden_kg_per_mwh": round(float(core.hidden_kg_per_mwh.median()), 0),
        "median_surplus_share": round(float(core.surplus_share.median()), 3),
        "by_ba": [
            {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()}
            for r in core[["ba", "demand_twh", "hourly_cfe", "hidden_kg_per_mwh", "surplus_share", "volume_for_90pct"]].to_dict("records")
        ],
        "solar_only_flat_median_cfe": round(float(table[(table.load == "flat 24/7") & (table.portfolio == "solar")].hourly_cfe.median()), 3),
        "wind_only_flat_median_cfe": round(float(table[(table.load == "flat 24/7") & (table.portfolio == "wind")].hourly_cfe.median()), 3),
    }
    (RESULTS / "hourly_gap.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "by_ba"}, indent=2))
    print(core[["ba", "demand_twh", "grid_kg_per_mwh", "hourly_cfe", "hidden_kg_per_mwh", "surplus_share", "volume_for_90pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
