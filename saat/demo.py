"""One market day of the Antalya community, run for real on a local EVM chain.

issue -> verify -> commit + mint -> deliver contracted shares -> list surplus -> AI plan -> buy
-> utility commits consumption -> retire hour by hour -> on-chain hourly score.
"""

import pickle
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from eth_account import Account
from eth_utils import keccak
from web3 import Web3

from saat.chain import LocalChain, Saat, Signer
from saat.merkle import KIND_CONSUMPTION, KIND_GENERATION, DayTree
from saat.paths import PROCESSED
from saat.planner import SupplyForecaster, cfe, shift_load, solar_features, wind_features
from saat.pvmodel import features
from saat.scenario import CAMPUS, HOTEL, SOLAR, UTC_OFFSET, WIND, ZONE, Community, campus_kwh, contracted_supply, hotel_kwh, weather, wind_cf
from saat.verifier import IssuanceVerifier, report

DEMO_DAY = "2025-07-10"
DEVICES = {
    1: {"key": "solar", "name": SOLAR.name, "owner": "coop", "capacity_kw": SOLAR.ac_kw, "tech": 1, "col": "solar_kwh"},
    2: {"key": "wind", "name": WIND["name"], "owner": "wind", "capacity_kw": WIND["mw"] * 1000, "tech": 2, "col": "wind_kwh"},
}
METERS = {
    7: {"key": "hotel", "name": HOTEL["name"], "owner": "hotel", "col": "hotel_kwh", "flex": 0.15},
    8: {"key": "campus", "name": CAMPUS["name"], "owner": "campus", "col": "campus_kwh", "flex": 0.10},
}
BASE_PRICE = 10**9  # wei per Wh = 0.001 test-ETH per MWh
TAMPERS = {
    "none": "honest meter export",
    "shift": "output relabelled two hours later, into scarce evening hours",
    "inflate": "every hour inflated by 40%",
    "night": "250 kWh claimed after sunset",
}


def _local_hours(day: str) -> pd.DatetimeIndex:
    start = pd.Timestamp(day) - pd.Timedelta(hours=UTC_OFFSET)
    return pd.date_range(start, periods=24, freq="1h")


def _hour_index(ts: pd.Timestamp) -> int:
    return int(ts.value // 3_600_000_000_000)


def _market_day(day: str) -> int:
    return int(pd.Timestamp(day).value // 86_400_000_000_000)


# ------------------------------------------------------------------ models (cached)


def _simulated_meter(kwh: pd.Series, seed: int = 7) -> pd.Series:
    """Meter history for the demo plant: modelled output with 1.5% noise, soiling that is washed off
    every few weeks, and a few partial outages. Labelled as simulated everywhere it is shown."""
    rng = np.random.default_rng(seed)
    n = len(kwh)
    soil = 1 - 0.04 * ((np.arange(n) // 24) % 30) / 30
    out = np.ones(n)
    for start in rng.choice(n - 48, size=6, replace=False):
        out[start : start + rng.integers(6, 48)] = rng.uniform(0.4, 0.8)
    noise = rng.normal(1.0, 0.015, n)
    return (kwh * soil * out * noise).clip(lower=0)


def build_models() -> dict:
    path = PROCESSED / "demo_models.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    actual = Community.build("2024-02-01", "2025-12-31").frame
    ws = weather(SOLAR.lat, SOLAR.lon, "2024-02-01", "2025-12-31")
    meter = _simulated_meter(actual.solar_kwh)
    X = features(SOLAR, ws.loc[actual.index])
    cf = meter / SOLAR.ac_kw
    train, cal = actual.index < "2025-01-01", (actual.index >= "2025-01-01") & (actual.index < "2025-04-01")
    verifier = IssuanceVerifier(SOLAR).fit(X[train], cf[train], X[cal], cf[cal])

    fc_w = {k: weather(*loc, "2024-02-01", "2025-12-31", day_ahead=True) for k, loc in {
        "solar": (SOLAR.lat, SOLAR.lon), "wind": (WIND["lat"], WIND["lon"]), "hotel": (HOTEL["lat"], HOTEL["lon"]), "campus": (CAMPUS["lat"], CAMPUS["lon"])}.items()}
    idx = actual.index.intersection(fc_w["solar"].dropna(subset=["shortwave_radiation"]).index)
    tr = idx < "2025-01-01"
    fs, fw = solar_features(fc_w["solar"].loc[idx].ffill()), wind_features(fc_w["wind"].loc[idx].ffill())
    solar_fc = SupplyForecaster().fit(fs[tr], actual.solar_kwh.loc[idx][tr]).predict(fs)
    wind_fc = SupplyForecaster().fit(fw[tr], actual.wind_kwh.loc[idx][tr]).predict(fw)
    forecast = pd.DataFrame({
        "solar_kwh": solar_fc, "wind_kwh": wind_fc,
        "hotel_kwh": hotel_kwh(fc_w["hotel"].loc[idx]), "campus_kwh": campus_kwh(fc_w["campus"].loc[idx]),
    })
    year = actual.loc["2025"]
    shares = {}
    for m in METERS.values():
        _, a, b = contracted_supply(year, m["col"])
        shares[m["key"]] = {"solar": a, "wind": b}
    models = {"actual": actual, "meter": meter, "X": X, "verifier": verifier, "forecast": forecast, "shares": shares, "weather_solar": ws}
    path.write_bytes(pickle.dumps(models))
    return models


# ------------------------------------------------------------------ the demo session


class RemoteChain:
    """A JSON-RPC endpoint we do not own (a public testnet): nothing to start or stop."""

    def __init__(self, url: str):
        self.url = url
        self.w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))

    def close(self):
        pass


@dataclass
class Demo:
    """One market day. Locally it starts its own Anvil chain with seven role accounts. With `rpc_url`
    and `keys` ({"operator", "hotel", "campus"} private keys) it runs on that network instead, every
    transaction signed locally: the operator is registry, issuers and both plants."""

    day: str = DEMO_DAY
    log: list = field(default_factory=list)
    rpc_url: str | None = None
    keys: dict | None = None
    consumer_gas_wei: int = 0  # top up hotel and campus to this balance (public networks)
    base_price_wei_per_wh: int = BASE_PRICE

    def __post_init__(self):
        self.models = build_models()
        if self.rpc_url:
            self.chain = RemoteChain(self.rpc_url)
            w3 = self.chain.w3
            signer = Signer(w3, self.keys.values(), estimate_retries=4)
            addr = {k: Account.from_key(v).address for k, v in self.keys.items()}
            op = addr["operator"]
            self.who = {"registry": op, "issuer": op, "utility": op, "coop": op, "wind": op, "hotel": addr["hotel"], "campus": addr["campus"]}
            self.saat = Saat.deploy(w3, op, signer=signer)
            for c in ("hotel", "campus"):
                short = self.consumer_gas_wei - w3.eth.get_balance(addr[c])
                if short > 0:
                    self.saat.send_eth(op, addr[c], short)
        else:
            self.chain = LocalChain()
            w3 = self.chain.w3
            a = w3.eth.accounts
            self.who = {"registry": a[0], "issuer": a[1], "utility": a[2], "coop": a[3], "wind": a[4], "hotel": a[5], "campus": a[6]}
            self.saat = Saat.deploy(w3, self.who["registry"])
        self._tx("registry", "deploy SaatRegistry + SaatMarket", self.saat.history[-1] if self.saat.history else None)
        s, r = self.saat, self.who["registry"]
        for issuer in dict.fromkeys([self.who["issuer"], self.who["utility"]]):
            s.set_issuer(r, issuer)
        s.set_zone_offset(r, ZONE, UTC_OFFSET)
        for did, d in DEVICES.items():
            s.register_device(r, did, self.who[d["owner"]], ZONE, int(d["capacity_kw"] * 1000), d["tech"])
        for mid, m in METERS.items():
            s.register_meter(r, mid, self.who[m["owner"]], ZONE)
        self._tx("registry", "accredit issuers, register 2 devices and 2 meters in zone TR (UTC+3)", None)
        self.hours = _local_hours(self.day)
        self.hour_idx = [_hour_index(t) for t in self.hours]
        self.mday = _market_day(self.day)
        self.trees: dict[int, DayTree] = {}
        self.issued: dict[int, list[int]] = {}
        self.listings: dict[tuple[int, int], int] = {}
        self.plans: dict[str, dict] = {}
        self.final_load: dict[int, list[int]] = {}

    def close(self):
        self.chain.close()

    def _tx(self, actor: str, action: str, receipt, **extra):
        self.log.append({"t": time.time(), "actor": actor, "action": action, "tx": getattr(receipt, "tx", None), "gas": getattr(receipt, "gas", None), "block": self.chain.w3.eth.block_number, **extra})

    # ---------------------------------------------------------- day data

    def series(self) -> dict:
        act = self.models["actual"].loc[self.hours]
        fc = self.models["forecast"].reindex(self.hours)
        sh = self.models["shares"]
        out = {"labels": [f"{(t + pd.Timedelta(hours=UTC_OFFSET)).hour:02d}:00" for t in self.hours], "day": self.day}
        for k in ("solar_kwh", "wind_kwh", "hotel_kwh", "campus_kwh"):
            out[k] = act[k].round(1).tolist()
            out[k + "_fc"] = fc[k].round(1).tolist()
        for m in METERS.values():
            own = sh[m["key"]]["solar"] * act.solar_kwh + sh[m["key"]]["wind"] * act.wind_kwh
            out[m["key"] + "_contract_kwh"] = own.round(1).tolist()
            out[m["key"] + "_contract_solar_kwh"] = (sh[m["key"]]["solar"] * act.solar_kwh).round(1).tolist()
            out[m["key"] + "_contract_wind_kwh"] = (sh[m["key"]]["wind"] * act.wind_kwh).round(1).tolist()
            out[m["key"] + "_cfe_contract_only"] = round(cfe(act[m["col"]], own), 4)
        return out

    # ---------------------------------------------------------- issuance

    def meter_reading(self, device_id: int, tamper: str = "none") -> pd.Series:
        d = DEVICES[device_id]
        if d["key"] == "solar":
            kwh = self.models["meter"].loc[self.hours].copy()
        else:
            kwh = self.models["actual"].wind_kwh.loc[self.hours].copy()
        if tamper == "shift":
            kwh = kwh.shift(2).fillna(0)
        elif tamper == "inflate":
            kwh = (kwh * 1.4).clip(upper=d["capacity_kw"])
        elif tamper == "night":
            loc_h = (self.hours + pd.Timedelta(hours=UTC_OFFSET)).hour
            kwh[(loc_h >= 21) & (loc_h <= 22)] += 125.0
        return kwh

    def verify(self, device_id: int, tamper: str = "none") -> dict:
        d = DEVICES[device_id]
        kwh = self.meter_reading(device_id, tamper)
        if d["key"] == "solar":
            X = self.models["X"].loc[self.hours]
            assessed = self.models["verifier"].assess(X, kwh / SOLAR.ac_kw)
            cap = SOLAR.ac_kw
        else:
            # Wind: the power-curve envelope of ERA5 100 m wind at the turbine site.
            speed = weather(WIND["lat"], WIND["lon"], "2024-02-01", "2025-12-31").wind_speed_100m.loc[self.hours]
            cap = d["capacity_kw"]
            exp = wind_cf(speed.values)
            upper = np.minimum(1.0, exp * 1.25 + 0.08)
            claimed = kwh.values / cap
            held = claimed > upper + 1e-9
            assessed = pd.DataFrame({"expected": exp, "upper": upper, "claimed": claimed, "held": held, "reason": np.where(held, "above-power-curve", "")}, index=self.hours)
        rep, rhash = report(SOLAR if d["key"] == "solar" else SOLAR.__class__(**{**SOLAR.__dict__, "name": d["name"], "ac_kw": cap}), device_id, self.mday, assessed, "simulated meter (demo)" if d["key"] == "solar" else "modelled from ERA5 (demo)")
        hours = []
        for i, (t, r) in enumerate(assessed.iterrows()):
            hours.append({
                "label": f"{(t + pd.Timedelta(hours=UTC_OFFSET)).hour:02d}:00",
                "hour": self.hour_idx[i],
                "claimed_kwh": round(float(r.claimed * cap), 1),
                "expected_kwh": round(float(r.expected * cap), 1),
                "upper_kwh": round(float(r.upper * cap), 1),
                "held": bool(r.held),
                "reason": r.reason,
            })
        return {"device": device_id, "tamper": tamper, "tamper_text": TAMPERS[tamper], "hours": hours, "report_hash": "0x" + rhash.hex(),
                "held_hours": sum(h["held"] and h["claimed_kwh"] > 0 for h in hours), "issued_kwh": round(sum(h["claimed_kwh"] for h in hours if not h["held"]), 1)}

    def issue(self, device_id: int, tamper: str = "none") -> dict:
        v = self.verify(device_id, tamper)
        whs = [0 if h["held"] else int(round(h["claimed_kwh"] * 1000)) for h in v["hours"]]
        tree = DayTree(KIND_GENERATION, device_id, self.hour_idx, whs)
        r1 = self.saat.commit_generation(self.who["issuer"], device_id, self.mday, tree, bytes.fromhex(v["report_hash"][2:]), v["held_hours"])
        self._tx("issuer", f"commit day root for device {device_id} ({v['held_hours']} hours held)", r1, root="0x" + tree.root.hex())
        r2 = self.saat.mint_day(self.who[DEVICES[device_id]["owner"]], device_id, tree)
        self._tx(DEVICES[device_id]["owner"], f"mint {sum(w > 0 for w in whs)} hourly certificates from the root", r2)
        self.trees[device_id] = tree
        self.issued[device_id] = whs
        return {**v, "root": "0x" + tree.root.hex(), "commit_tx": r1.tx, "commit_gas": r1.gas, "mint_tx": r2.tx, "mint_gas": r2.gas,
                "leaves": len(whs), "proof_len": len(tree.proof(12))}

    # ---------------------------------------------------------- delivery and market

    def deliver_contracts(self) -> dict:
        """Generators deliver each consumer's contracted share of every hour (bilateral PPAs)."""
        out = {}
        sh = self.models["shares"]
        for did, d in DEVICES.items():
            whs = self.issued.get(did, [0] * 24)
            for mid, m in METERS.items():
                share = sh[m["key"]][d["key"]]
                ids, amts = [], []
                for h, w in zip(self.hour_idx, whs):
                    amt = int(w * share)
                    if amt > 0:
                        ids.append(self.saat.cert_id(did, h))
                        amts.append(amt)
                if ids:
                    r = self.saat.batch_transfer(self.who[d["owner"]], self.who[m["owner"]], ids, amts)
                    self._tx(d["owner"], f"deliver contracted {d['key']} share to {m['key']} ({len(ids)} hours)", r)
                out[f"{d['key']}->{m['key']}"] = round(sum(amts) / 1000, 1)
        return out

    def list_surplus(self) -> list[dict]:
        """Generators list every certificate they still hold. Price rises with the forecast shortage
        of that hour across the community (evenings and nights cost more)."""
        fc = self.models["forecast"].reindex(self.hours)
        demand = (fc.hotel_kwh + fc.campus_kwh).values
        supply = (fc.solar_kwh + fc.wind_kwh).values
        scarcity = np.clip((demand - supply) / np.maximum(demand, 1), 0, 1)
        listed = []
        for did, d in DEVICES.items():
            owner = self.who[d["owner"]]
            cids = [self.saat.cert_id(did, h) for h in self.hour_idx]
            lots = [(i, cid, bal) for i, (cid, bal) in enumerate(zip(cids, self.saat.balances(owner, cids))) if bal > 0]
            if not lots:
                continue
            prices = [int(self.base_price_wei_per_wh * (1 + 3 * scarcity[i])) for i, _, _ in lots]
            first, r = self.saat.list_batch(owner, [c for _, c, _ in lots], [b for _, _, b in lots], prices)
            self._tx(d["owner"], f"list {len(lots)} hourly {d['key']} lots on SaatMarket in one transaction", r)
            for k, ((i, cid, bal), price) in enumerate(zip(lots, prices)):
                self.listings[(did, self.hour_idx[i])] = first + k
                listed.append({"device": did, "hour": self.hour_idx[i], "label": f"{(self.hours[i] + pd.Timedelta(hours=UTC_OFFSET)).hour:02d}:00", "kwh": round(bal / 1000, 1), "price_eth_per_mwh": price * 1_000_000 / 1e18})
        return listed

    def order_book(self) -> list[dict]:
        rows = []
        for (did, h), lid in self.listings.items():
            seller, cid, remaining, price = self.saat.read(self.saat.market.functions.listings(lid))
            i = self.hour_idx.index(h)
            rows.append({"listing": lid, "device": did, "label": f"{(self.hours[i] + pd.Timedelta(hours=UTC_OFFSET)).hour:02d}:00", "i": i, "kwh": round(remaining / 1000, 1), "price_eth_per_mwh": price * 1_000_000 / 1e18})
        return sorted(rows, key=lambda r: (r["i"], r["device"]))

    # ---------------------------------------------------------- AI plan

    def plan(self, meter_id: int) -> dict:
        """Day-ahead plan from forecasts only: shift flexible load into forecast surplus hours, then
        buy forecast shortfalls hour by hour from the cheapest listings."""
        m = METERS[meter_id]
        fc = self.models["forecast"].reindex(self.hours)
        sh = self.models["shares"][m["key"]]
        own_fc = sh["solar"] * fc.solar_kwh + sh["wind"] * fc.wind_kwh
        load_fc = fc[m["col"]]
        flex = m["flex"] * load_fc
        shifted_fc = shift_load(load_fc, own_fc, flex)
        book = self.order_book()
        buys = []
        for i in range(24):
            need = max(shifted_fc.iloc[i] - own_fc.iloc[i], 0) * 1000
            for lot in sorted([b for b in book if b["i"] == i], key=lambda b: b["price_eth_per_mwh"]):
                if need <= 0:
                    break
                take = int(min(need, lot["kwh"] * 1000))
                if take > 0:
                    buys.append({"listing": lot["listing"], "i": i, "label": lot["label"], "device": lot["device"], "wh": take, "price_eth_per_mwh": lot["price_eth_per_mwh"]})
                    need -= take
        plan = {"meter": meter_id, "load_fc": load_fc.round(1).tolist(), "own_fc": own_fc.round(1).tolist(), "shifted_fc": shifted_fc.round(1).tolist(),
                "shift_delta": (shifted_fc - load_fc).round(1).tolist(), "buys": buys, "buy_kwh": round(sum(b["wh"] for b in buys) / 1000, 1),
                "cost_eth": round(sum(b["wh"] * b["price_eth_per_mwh"] / 1e6 for b in buys), 6), "shifted_kwh": round(float((shifted_fc - load_fc).clip(lower=0).sum()), 1)}
        self.plans[m["key"]] = plan
        return plan

    def execute(self, meter_id: int) -> dict:
        m = METERS[meter_id]
        plan = self.plans[m["key"]]
        lids, whs = [], []
        for b in plan["buys"]:
            remaining = self.saat.read(self.saat.market.functions.listings(b["listing"]))[2]
            wh = min(b["wh"], remaining)
            if wh > 0:
                lids.append(b["listing"])
                whs.append(wh)
        bought = sum(whs)
        r = self.saat.buy_batch(self.who[m["owner"]], lids, whs) if lids else None
        self._tx(m["owner"], f"buy {len(lids)} hourly lots ({bought / 1000:.0f} kWh) on SaatMarket in one transaction", r)
        actual = self.models["actual"][m["col"]].loc[self.hours]
        delta = pd.Series(plan["shift_delta"], index=self.hours)
        self.final_load[meter_id] = [int(round(x * 1000)) for x in (actual + delta).clip(lower=0)]
        return {"bought_kwh": round(bought / 1000, 1)}

    # ---------------------------------------------------------- settlement

    def settle(self, meter_id: int) -> dict:
        m = METERS[meter_id]
        owner = self.who[m["owner"]]
        load = self.final_load.get(meter_id) or [int(round(x * 1000)) for x in self.models["actual"][m["col"]].loc[self.hours]]
        tree = DayTree(KIND_CONSUMPTION, meter_id, self.hour_idx, load)
        r = self.saat.commit_consumption(self.who["utility"], meter_id, self.mday, tree, keccak(text=f"meter-{meter_id}-{self.day}"))
        self._tx("utility", f"commit {m['key']} consumption root (24 hourly readings)", r, root="0x" + tree.root.hex())
        # One batched read of every (plant, hour) balance, then one transaction for the whole day.
        cids = [self.saat.cert_id(did, h) for h in self.hour_idx for did in DEVICES]
        held = dict(zip(cids, self.saat.balances(owner, cids)))
        ids, amounts, per_hour = [], [], []
        for i, h in enumerate(self.hour_idx):
            room, hid, hamt = load[i], [], []
            for did in DEVICES:
                cid = self.saat.cert_id(did, h)
                take = min(held[cid], room)
                if take > 0:
                    hid.append(cid)
                    hamt.append(take)
                    room -= take
            ids.append(hid)
            amounts.append(hamt)
            per_hour.append({"label": f"{(self.hours[i] + pd.Timedelta(hours=UTC_OFFSET)).hour:02d}:00", "load_kwh": round(load[i] / 1000, 1), "matched_kwh": round(sum(hamt) / 1000, 1)})
        rr = self.saat.settle_day(owner, meter_id, tree, ids, amounts)
        gas = rr.gas
        summary = self.saat.claim_summary(meter_id, self.mday, self.mday)
        self._tx(m["owner"], f"settle the day in one transaction (24 proofs, {sum(len(x) for x in ids)} retirements); on-chain hourly score {summary['cfe']:.1%}", rr)
        return {"meter": meter_id, "hours": per_hour, **summary, "retire_gas": gas}

    def attempt_mismatch(self, meter_id: int) -> dict:
        """Try to cover a 21:00 load with a 12:00 solar certificate: the contract must refuse."""
        m = METERS[meter_id]
        owner = self.who[m["owner"]]
        noon = self.hour_idx[12]
        evening = self.hour_idx[21]
        cid = self.saat.cert_id(1, noon)
        try:
            self.saat.read(self.saat.registry.functions.retire(meter_id, evening, [cid], [1]), sender=owner)
            return {"reverted": False}
        except Exception as e:
            return {"reverted": True, "error": self.saat.explain_revert(e), "certificate": "12:00 solar", "load_hour": "21:00"}
