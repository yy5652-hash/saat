"""Annual versus hourly matching of clean-energy certificates against a consumer's load."""

import numpy as np
import pandas as pd

# Life-cycle emission factors, gCO2eq/kWh = kg/MWh. Medians from IPCC AR5 WG3 Annex III (2014);
# oil and unknown/other follow the defaults Electricity Maps publishes for sources AR5 does not list.
EMISSION_FACTORS = {
    "coal": 820,
    "gas": 490,
    "oil": 650,
    "nuclear": 12,
    "hydro": 24,
    "solar": 45,
    "solar_batt": 45,
    "wind": 11,
    "wind_batt": 11,
    "geothermal": 38,
    "other": 700,
    "unknown": 700,
}
VRE = ["solar", "solar_batt", "wind", "wind_batt"]


def grid_intensity(gen: pd.DataFrame) -> pd.Series:
    """Hourly average life-cycle intensity of a balancing authority's own generation, kg/MWh."""
    g = gen[list(EMISSION_FACTORS)].clip(lower=0)
    total = g.sum(axis=1)
    emis = sum(g[k] * v for k, v in EMISSION_FACTORS.items())
    return (emis / total.replace(0, np.nan)).ffill().bfill()


def match(load: np.ndarray, supply: np.ndarray, intensity: np.ndarray | None = None) -> dict:
    """Score a consumer that holds `supply` certificates against `load`, both in MWh per hour."""
    load = np.asarray(load, float)
    supply = np.asarray(supply, float)
    matched = np.minimum(load, supply)
    out = {
        "annual_claim": min(1.0, supply.sum() / load.sum()),
        "hourly_cfe": matched.sum() / load.sum(),
        "surplus_share": np.maximum(supply - load, 0).sum() / max(supply.sum(), 1e-9),
    }
    if intensity is not None:
        unmatched = load - matched
        # Emissions an annual claim reports as zero but the consumer actually drew from the grid.
        out["hidden_kg_per_mwh"] = float((unmatched * intensity).sum() / load.sum())
    return out


def coverage_for(load: np.ndarray, profile: np.ndarray, target: float, cap: float = 10.0) -> float | None:
    """Smallest multiple of annual load to buy from `profile` so hourly CFE reaches `target`."""
    profile = np.asarray(profile, float) / np.sum(profile) * np.sum(load)
    if np.minimum(load, profile * cap).sum() / np.sum(load) < target:
        return None
    lo, hi = 0.0, cap
    for _ in range(50):
        mid = (lo + hi) / 2
        if np.minimum(load, profile * mid).sum() / np.sum(load) >= target:
            hi = mid
        else:
            lo = mid
    return hi
