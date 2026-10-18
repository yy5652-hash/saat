#!/usr/bin/env python
"""The full Antalya demo day (2025-07-10) on a public EVM testnet, every transaction signed locally.

Same steps and numbers as the dashboard and the film: verified issuance of both plants, contracted
deliveries, the surplus listed hour by hour, the AI plans and batch buys, one-transaction settlement
of each consumer's day, and a same-hour violation that reverts. Three keys: the operator (registry,
issuers and both plants: the only address to fund), the hotel and the campus (topped up by the
operator). Market prices are scaled down so the test ETH moved between wallets stays tiny.

    uv run python scripts/public_chain_day.py --local     # rehearse on a fresh Anvil, fresh keys
    uv run python scripts/public_chain_day.py             # Sepolia (asks before sending)

Environment (read from .env at the project root if present): RPC_URL, SAAT_OPERATOR_KEY,
SAAT_HOTEL_KEY, SAAT_CAMPUS_KEY. Keys are never printed or written anywhere; the result file holds
only addresses, hashes and numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eth_account import Account  # noqa: E402
from web3 import Web3  # noqa: E402

from saat.chain import LocalChain  # noqa: E402
from saat.demo import Demo  # noqa: E402
from saat.paths import RESULTS  # noqa: E402

SEPOLIA = 11155111
TESTNETS = {SEPOLIA: "sepolia", 17000: "holesky", 560048: "hoodi", 31337: "anvil"}
EXPLORER = {SEPOLIA: "https://sepolia.etherscan.io", 31337: None}
BLOCKSCOUT = {SEPOLIA: "https://eth-sepolia.blockscout.com"}
GAS_BUDGET = 150_000_000  # whole day incl. deployment, with Sepolia's current contract-creation pricing
PRICE_WEI_PER_WH = 10**6  # 1/1000 of the local demo price: 0.000001 test-ETH per MWh
CONSUMER_FLOAT_GAS = 25_000_000


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="rehearse on a fresh local Anvil chain")
    ap.add_argument("--yes", action="store_true", help="do not ask before sending")
    a = ap.parse_args()
    load_env()
    keys = {role: os.environ[f"SAAT_{role.upper()}_KEY"] for role in ("operator", "hotel", "campus")}
    addr = {k: Account.from_key(v).address for k, v in keys.items()}
    anvil = LocalChain() if a.local else None
    url = anvil.url if anvil else os.environ.get("RPC_URL", "https://ethereum-sepolia-rpc.publicnode.com")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))
    chain_id = w3.eth.chain_id
    if chain_id not in TESTNETS:
        sys.exit(f"chain {chain_id} is not a known testnet; refusing to send")
    if anvil:
        w3.provider.make_request("anvil_setBalance", [addr["operator"], hex(10**20)])
    base = w3.eth.get_block("latest").get("baseFeePerGas", w3.eth.gas_price)
    tip = w3.eth.max_priority_fee
    fee_cap = 2 * base + tip
    need = GAS_BUDGET * fee_cap + 2 * CONSUMER_FLOAT_GAS * fee_cap
    bal = w3.eth.get_balance(addr["operator"])
    print(f"network {TESTNETS[chain_id]} ({chain_id}) via {url}")
    print(f"operator {addr['operator']}  balance {Web3.from_wei(bal, 'ether')} ETH")
    print(f"base fee {base} wei, tip {tip} wei -> budget {GAS_BUDGET:,} gas needs at most {Web3.from_wei(need, 'ether'):.8f} ETH")
    if bal < need:
        sys.exit("operator balance too low for the budget at the current fee; fund it from a faucet and rerun")
    if not a.yes and not a.local and input("send the demo day to this network? [y/N] ").strip().lower() != "y":
        sys.exit("cancelled")

    d = Demo(rpc_url=url, keys=keys, consumer_gas_wei=CONSUMER_FLOAT_GAS * fee_cap + 10**15, base_price_wei_per_wh=PRICE_WEI_PER_WH)
    s = d.saat
    print(f"SaatRegistry {s.registry.address}\nSaatMarket   {s.market.address}")
    shifted = d.verify(1, "shift")
    print(f"tampered export: {shifted['held_hours']} hours held (not committed)")
    for dev in (1, 2):
        r = d.issue(dev)
        print(f"device {dev}: root {r['root'][:12]}…  commit {r['commit_gas']:,} gas, mint {r['mint_gas']:,} gas")
    d.deliver_contracts()
    d.list_surplus()
    d.plan(7)
    d.execute(7)
    d.plan(8)
    d.execute(8)
    camp = d.settle(8)
    hotel = d.settle(7)
    mismatch = d.attempt_mismatch(7)
    print(f"hotel hourly score on-chain: {hotel['cfe']:.1%}   campus: {camp['cfe']:.1%}   mismatch: {mismatch['error']}")

    ex = EXPLORER.get(chain_id)
    roles = {v: k for k, v in addr.items()}
    out = {
        "network": TESTNETS[chain_id], "chain_id": chain_id, "day": d.day,
        "contracts": {"SaatRegistry": s.registry.address, "SaatMarket": s.market.address},
        "explorer": {"registry": f"{ex}/address/{s.registry.address}" if ex else None, "market": f"{ex}/address/{s.market.address}" if ex else None,
                     "blockscout_registry": f"{BLOCKSCOUT[chain_id]}/address/{s.registry.address}" if chain_id in BLOCKSCOUT else None},
        "wallets": addr,
        "claim_summary": {"hotel": {k: hotel[k] for k in ("load_wh", "matched_wh", "cfe")}, "campus": {k: camp[k] for k in ("load_wh", "matched_wh", "cfe")}},
        "mismatch": mismatch,
        "transactions": [{"method": r.method, "sender": roles.get(r.sender, r.sender), "tx": r.tx, "gas": r.gas, "fee_wei": r.fee_wei, "block": r.block,
                          "url": f"{ex}/tx/{r.tx}" if ex else None} for r in s.history],
        "total_gas": sum(r.gas for r in s.history),
        "total_fee_eth": float(Web3.from_wei(sum(r.fee_wei for r in s.history), "ether")),
        "verify": [
            f"forge verify-contract --verifier sourcify --chain {TESTNETS[chain_id]} {s.registry.address} src/SaatRegistry.sol:SaatRegistry --constructor-args $(cast abi-encode 'constructor(address)' {addr['operator']})",
            f"forge verify-contract --verifier sourcify --chain {TESTNETS[chain_id]} {s.market.address} src/SaatMarket.sol:SaatMarket --constructor-args $(cast abi-encode 'constructor(address)' {s.registry.address})",
        ],
    }
    name = "public_chain_day_local.json" if a.local else "public_chain_day.json"
    (RESULTS / name).write_text(json.dumps(out, indent=2))
    print(f"{len(out['transactions'])} transactions, {out['total_gas']:,} gas, {out['total_fee_eth']:.10f} ETH in fees -> results/{name}")
    if ex:
        print(f"registry: {out['explorer']['registry']}")
    d.close()
    if anvil:
        anvil.close()


if __name__ == "__main__":
    main()
