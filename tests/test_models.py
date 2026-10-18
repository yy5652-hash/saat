import numpy as np
import pandas as pd
import pytest

from saat.matching import coverage_for, match
from saat.planner import cfe, shift_load
from saat.scenario import wind_cf


def test_annual_match_hides_hourly_gap():
    load = np.ones(24)
    supply = np.r_[np.zeros(12), np.full(12, 2.0)]  # same daily total, all in the afternoon
    m = match(load, supply)
    assert m["annual_claim"] == 1.0
    assert m["hourly_cfe"] == pytest.approx(0.5)
    assert m["surplus_share"] == pytest.approx(0.5)


def test_coverage_for_target_and_unreachable():
    load = np.ones(24)
    solar = np.r_[np.zeros(6), np.ones(12), np.zeros(6)]
    # Half the annual volume already fills the 12 daylight hours: 50% hourly.
    assert coverage_for(load, solar, 0.5) == pytest.approx(0.5, abs=1e-6)
    assert coverage_for(load, solar, 0.9) is None  # nights cannot be covered by solar


def _day(values, start="2025-07-10"):
    idx = pd.date_range(pd.Timestamp(start) - pd.Timedelta(hours=3), periods=len(values), freq="1h")
    return pd.Series(values, index=idx, dtype=float)


def test_shift_conserves_energy_and_raises_cfe():
    load = _day([100.0] * 24)
    supply = _day([0.0] * 9 + [250.0] * 6 + [0.0] * 9)
    flex = load * 0.2
    shifted = shift_load(load, supply, flex)
    assert shifted.sum() == pytest.approx(load.sum())
    assert cfe(shifted, supply) > cfe(load, supply)
    assert (load - shifted).clip(lower=0).max() <= 20.0 + 1e-9  # never moves more than the flexible part


def test_shift_does_nothing_without_surplus():
    load = _day([100.0] * 24)
    supply = _day([50.0] * 24)
    assert shift_load(load, supply, load * 0.2).equals(load)


def test_wind_power_curve_bounds():
    cf = wind_cf([0, 10.8, 30, 36, 60, 100])  # km/h
    assert cf[0] == 0 and cf[1] == 0  # below cut-in (3 m/s)
    assert 0 < cf[2] < cf[3] < 0.9
    assert cf[4] == pytest.approx(0.9)  # rated, after 10% losses
    assert cf[5] == 0  # above cut-out


def test_market_buy_bids_shortfall_and_caps_at_listing():
    from saat.planner import market_buy

    planned = _day([100.0] * 24)
    own_fc = _day([0.0] * 6 + [150.0] * 12 + [0.0] * 6)
    listed = _day([30.0] * 24)
    bid, got = market_buy(planned, own_fc, listed)
    assert bid.iloc[0] == 100.0 and bid.iloc[12] == 0.0  # no bid in a forecast-surplus hour
    assert got.max() == 30.0  # never more than was actually listed
    assert (got <= bid).all()


def test_plan_day_ahead_keeps_daily_energy():
    from saat.planner import plan_day_ahead

    load_fc = _day([100.0] * 24)
    own_fc = _day([0.0] * 9 + [250.0] * 6 + [0.0] * 9)
    planned = plan_day_ahead(load_fc, own_fc, 0.15)
    assert planned.sum() == pytest.approx(load_fc.sum())
    assert planned.iloc[9:15].sum() > load_fc.iloc[9:15].sum()  # energy moved into the surplus window
