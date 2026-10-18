"""End to end on a real EVM: Python-built Merkle roots, Solidity-verified proofs."""

import pytest
from eth_utils import keccak

from saat.chain import LocalChain, Saat
from saat.merkle import KIND_CONSUMPTION, KIND_GENERATION, DayTree

DAY = 20_100
HOURS = [DAY * 24 + h for h in range(24)]
SOLAR = [0] * 6 + [40_000, 120_000, 210_000, 290_000, 340_000, 360_000, 350_000, 300_000, 220_000, 130_000, 50_000, 5_000] + [0] * 6
LOAD = [180_000] * 24


@pytest.fixture(scope="module")
def env():
    with LocalChain() as chain:
        w3 = chain.w3
        admin, issuer, coop, hotel = w3.eth.accounts[:4]
        saat = Saat.deploy(w3, admin)
        saat.set_issuer(admin, issuer)
        saat.register_device(admin, 1, coop, "TR", 500_000)
        saat.register_meter(admin, 7, hotel, "TR")
        yield saat, admin, issuer, coop, hotel


def test_python_leaf_matches_solidity(env):
    saat, *_ = env
    from saat.merkle import leaf_hash

    assert saat.registry.functions.leafHash(1, 9068, 123, 456).call() == leaf_hash(1, 9068, 123, 456)


def test_full_flow(env):
    saat, admin, issuer, coop, hotel = env
    gen = DayTree(KIND_GENERATION, 1, HOURS, SOLAR)
    load = DayTree(KIND_CONSUMPTION, 7, HOURS, LOAD)
    saat.commit_generation(issuer, 1, DAY, gen, keccak(b"report"), 0)
    saat.commit_consumption(issuer, 7, DAY, load, keccak(b"meter"))
    r = saat.mint_day(coop, 1, gen)
    assert r.gas < 2_000_000  # 12 sunny hours in one transaction

    noon = HOURS[12]
    cid = saat.cert_id(1, noon)
    assert saat.balance(coop, cid) == 350_000
    listing, _ = saat.list(coop, cid, 300_000, 10**9)
    saat.buy(hotel, listing, 180_000)
    assert saat.balance(hotel, cid) == 180_000

    saat.prove_and_retire(hotel, 7, noon, load, [cid], [180_000])
    summary = saat.claim_summary(7, DAY, DAY)
    assert summary["matched_wh"] == 180_000
    assert summary["load_wh"] == sum(LOAD)
    assert summary["cfe"] == pytest.approx(1 / 24, abs=1e-4)
