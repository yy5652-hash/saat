"""Thin client for the Saat contracts on a local Anvil chain (or any JSON-RPC endpoint)."""

import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

from eth_utils import keccak
from web3 import Web3

from saat.merkle import DayTree
from saat.paths import CONTRACTS

ANVIL = shutil.which("anvil") or os.path.expanduser("~/.foundry/bin/anvil")
FORGE = shutil.which("forge") or os.path.expanduser("~/.foundry/bin/forge")


def zone(code: str) -> bytes:
    return code.encode().ljust(32, b"\0")


def zone_name(raw: bytes) -> str:
    return raw.rstrip(b"\0").decode()


def _artifact(name: str) -> dict:
    path = CONTRACTS / "out" / f"{name}.sol" / f"{name}.json"
    if not path.exists():
        subprocess.run([FORGE, "build"], cwd=CONTRACTS, check=True, capture_output=True)
    data = json.loads(path.read_text())
    return {"abi": data["abi"], "bytecode": data["bytecode"]["object"]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalChain:
    """An Anvil process with its ten funded dev accounts. Stops when closed."""

    def __init__(self, port: int | None = None, block_time: int | None = None):
        self.port = port or _free_port()
        args = [ANVIL, "--port", str(self.port), "--silent", "--chain-id", "31337"]
        if block_time:
            args += ["--block-time", str(block_time)]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.url = f"http://127.0.0.1:{self.port}"
        self.w3 = Web3(Web3.HTTPProvider(self.url))
        for _ in range(100):
            try:
                if self.w3.is_connected():
                    break
            except Exception:
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("anvil did not start")

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@dataclass
class Receipt:
    tx: str
    gas: int
    block: int


class Saat:
    def __init__(self, w3: Web3, registry_address: str, market_address: str):
        self.w3 = w3
        self.registry = w3.eth.contract(address=registry_address, abi=_artifact("SaatRegistry")["abi"])
        self.market = w3.eth.contract(address=market_address, abi=_artifact("SaatMarket")["abi"])

    @classmethod
    def deploy(cls, w3: Web3, admin: str) -> "Saat":
        reg_art, mkt_art = _artifact("SaatRegistry"), _artifact("SaatMarket")
        reg = w3.eth.contract(abi=reg_art["abi"], bytecode=reg_art["bytecode"])
        r1 = w3.eth.wait_for_transaction_receipt(reg.constructor(admin).transact({"from": admin}))
        mkt = w3.eth.contract(abi=mkt_art["abi"], bytecode=mkt_art["bytecode"])
        r2 = w3.eth.wait_for_transaction_receipt(mkt.constructor(r1.contractAddress).transact({"from": admin}))
        return cls(w3, r1.contractAddress, r2.contractAddress)

    def _send(self, fn, sender: str, value: int = 0) -> Receipt:
        tx = fn.transact({"from": sender, "value": value})
        r = self.w3.eth.wait_for_transaction_receipt(tx)
        if r.status != 1:
            raise RuntimeError(f"transaction reverted: {tx.hex()}")
        return Receipt(tx.to_0x_hex(), r.gasUsed, r.blockNumber)

    # ---------------------------------------------------------- governance

    def set_issuer(self, admin: str, issuer: str) -> Receipt:
        return self._send(self.registry.functions.setIssuer(issuer, True), admin)

    def set_zone_offset(self, admin: str, zone_code: str, hours: int) -> Receipt:
        return self._send(self.registry.functions.setZoneOffset(zone(zone_code), hours), admin)

    def register_device(self, admin, device_id, owner, zone_code, capacity_w, technology=1) -> Receipt:
        fn = self.registry.functions.registerDevice(device_id, owner, zone(zone_code), capacity_w, technology)
        return self._send(fn, admin)

    def register_meter(self, admin, meter_id, owner, zone_code) -> Receipt:
        return self._send(self.registry.functions.registerMeter(meter_id, owner, zone(zone_code)), admin)

    # ---------------------------------------------------------- issuance

    def commit_generation(self, issuer, device_id, day, tree: DayTree, report_hash: bytes, held_hours: int) -> Receipt:
        fn = self.registry.functions.commitGeneration(device_id, day, tree.root, report_hash, tree.total_wh, held_hours)
        return self._send(fn, issuer)

    def commit_consumption(self, issuer, meter_id, day, tree: DayTree, report_hash: bytes) -> Receipt:
        fn = self.registry.functions.commitConsumption(meter_id, day, tree.root, report_hash, tree.total_wh)
        return self._send(fn, issuer)

    def mint_day(self, payer, device_id, tree: DayTree) -> Receipt:
        idx = [i for i, w in enumerate(tree.whs) if w > 0]
        fn = self.registry.functions.mintMany(
            device_id,
            [tree.hours[i] for i in idx],
            [tree.whs[i] for i in idx],
            tree.root,
            [tree.proof(i) for i in idx],
        )
        return self._send(fn, payer)

    def batch_transfer(self, sender, to, ids, amounts) -> Receipt:
        return self._send(self.registry.functions.safeBatchTransferFrom(sender, to, ids, amounts, b""), sender)

    # ---------------------------------------------------------- market

    def list(self, seller, cert_id, wh, price_wei_per_wh) -> tuple[int, Receipt]:
        if not self.registry.functions.isApprovedForAll(seller, self.market.address).call():
            self._send(self.registry.functions.setApprovalForAll(self.market.address, True), seller)
        listing_id = self.market.functions.listingCount().call()
        return listing_id, self._send(self.market.functions.list(cert_id, wh, price_wei_per_wh), seller)

    def buy(self, buyer, listing_id, wh) -> Receipt:
        price = self.market.functions.listings(listing_id).call()[3]
        return self._send(self.market.functions.buy(listing_id, wh), buyer, value=wh * price)

    # ---------------------------------------------------------- claims

    def prove_and_retire(self, owner, meter_id, hour, load_tree: DayTree, ids, amounts) -> Receipt:
        i = load_tree.hours.index(hour)
        fn = self.registry.functions.proveAndRetire(meter_id, hour, load_tree.whs[i], load_tree.proof(i), ids, amounts)
        return self._send(fn, owner)

    def claim_summary(self, meter_id, from_day, to_day) -> dict:
        load, matched, bps = self.registry.functions.claimSummary(meter_id, from_day, to_day).call()
        return {"load_wh": load, "matched_wh": matched, "cfe": bps / 10_000}

    def explain_revert(self, exc: Exception) -> str:
        """Name of the contract's custom error behind a failed call, e.g. 'HourMismatch'."""
        data = getattr(exc, "data", None) or str(exc)
        if isinstance(data, dict):
            data = data.get("data", "")
        for abi in (self.registry.abi, self.market.abi):
            for item in abi:
                if item.get("type") == "error":
                    sig = f"{item['name']}({','.join(i['type'] for i in item.get('inputs', []))})"
                    if keccak(text=sig)[:4].hex() in str(data):
                        return item["name"]
        return str(exc)

    def cert_id(self, device_id, hour) -> int:
        return (device_id << 64) | hour

    def balance(self, owner, cert_id) -> int:
        return self.registry.functions.balanceOf(owner, cert_id).call()
