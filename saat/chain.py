"""Thin client for the Saat contracts on a local Anvil chain (or any JSON-RPC endpoint).

Two ways to send a transaction:
- unlocked accounts (Anvil's dev accounts): `fn.transact({"from": ...})`, the node signs;
- a `Signer` holding private keys (public networks): the client builds an EIP-1559 transaction,
  signs it locally and sends the raw bytes. Any sender whose key the signer holds goes this way.
"""

from __future__ import annotations  # the class defines a method named `list`

import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

from eth_account import Account
from eth_utils import keccak
from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError

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
    fee_wei: int = 0  # gasUsed * effectiveGasPrice
    method: str = ""
    sender: str = ""


class Signer:
    """Private keys for some addresses; signs EIP-1559 transactions locally and sends them raw.

    Gas is the node's estimate times `gas_margin`. Fees come from the node: the priority fee from
    eth_maxPriorityFeePerGas (or `priority_fee_wei`), the fee cap `fee_cap_multiplier` times the
    latest base fee plus the tip. Nonces are tracked locally as well, so a load-balanced RPC that
    lags by a block cannot hand out a used nonce. Keys are held in memory only and never printed.
    """

    def __init__(
        self,
        w3: Web3,
        keys=(),
        gas_margin: float = 1.25,
        fee_cap_multiplier: int = 2,
        priority_fee_wei: int | None = None,
        timeout: float = 300,
        poll_latency: float = 0.1,
        estimate_retries: int = 0,
        retry_delay: float = 4.0,
    ):
        self.w3 = w3
        self.gas_margin = gas_margin
        self.fee_cap_multiplier = fee_cap_multiplier
        self.priority_fee_wei = priority_fee_wei
        self.timeout = timeout
        self.poll_latency = poll_latency
        self.estimate_retries = estimate_retries
        self.retry_delay = retry_delay
        self._accounts: dict[str, object] = {}
        self._next_nonce: dict[str, int] = {}
        self._chain_id: int | None = None
        for key in keys:
            self.add(key)

    def add(self, key) -> str:
        """Hold one more private key; returns its checksum address."""
        acct = Account.from_key(key)
        self._accounts[acct.address] = acct
        return acct.address

    @property
    def addresses(self) -> list[str]:
        return list(self._accounts)

    def __contains__(self, address) -> bool:
        try:
            return Web3.to_checksum_address(address) in self._accounts
        except (TypeError, ValueError):
            return False

    @property
    def chain_id(self) -> int:
        if self._chain_id is None:
            self._chain_id = self.w3.eth.chain_id
        return self._chain_id

    def fees(self) -> dict:
        """EIP-1559 fee fields from the node (legacy gasPrice on a chain without a base fee)."""
        base = self.w3.eth.get_block("latest").get("baseFeePerGas")
        if base is None:
            return {"gasPrice": self.w3.eth.gas_price}
        tip = self.priority_fee_wei if self.priority_fee_wei is not None else self.w3.eth.max_priority_fee
        return {"maxPriorityFeePerGas": tip, "maxFeePerGas": self.fee_cap_multiplier * base + tip}

    def _nonce(self, sender: str) -> int:
        return max(self.w3.eth.get_transaction_count(sender, "pending"), self._next_nonce.get(sender, 0))

    def _estimate(self, estimate) -> int:
        for attempt in range(self.estimate_retries + 1):
            try:
                return int(estimate() * self.gas_margin)
            except Exception:
                # A load-balanced public RPC may answer from a node one block behind the receipt we
                # just saw; retry a few times before treating the revert as real.
                if attempt == self.estimate_retries:
                    raise
                time.sleep(self.retry_delay)

    def send(self, fn, sender: str, value: int = 0):
        """Sign and send a contract call or constructor (`fn` from web3) from `sender`."""
        sender = Web3.to_checksum_address(sender)
        params = {"from": sender, "value": value}
        gas = self._estimate(lambda: fn.estimate_gas(params))
        nonce = self._nonce(sender)
        tx = fn.build_transaction({**params, "gas": gas, "nonce": nonce, "chainId": self.chain_id, **self.fees()})
        return self._sign_and_send(sender, tx)

    def send_value(self, sender: str, to: str, value: int):
        """Plain ETH transfer."""
        sender = Web3.to_checksum_address(sender)
        params = {"from": sender, "to": Web3.to_checksum_address(to), "value": value}
        gas = self._estimate(lambda: self.w3.eth.estimate_gas(params))
        tx = {**params, "gas": gas, "nonce": self._nonce(sender), "chainId": self.chain_id, **self.fees()}
        return self._sign_and_send(sender, tx)

    def _sign_and_send(self, sender: str, tx: dict):
        tx = {k: v for k, v in tx.items() if k != "from"}
        signed = self._accounts[sender].sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self._next_nonce[sender] = tx["nonce"] + 1
        return h

    def wait(self, tx_hash):
        return self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self.timeout, poll_latency=self.poll_latency)


def _execute(w3: Web3, signer: Signer | None, fn, sender: str, value: int = 0, method: str = ""):
    """Send `fn` from `sender` (signed locally if the signer holds its key, else via the node's
    unlocked account), wait for it, and return (web3 receipt, Receipt)."""
    if signer is not None and sender in signer:
        tx = signer.send(fn, sender, value)
        r = signer.wait(tx)
    else:
        tx = fn.transact({"from": sender, "value": value})
        r = w3.eth.wait_for_transaction_receipt(tx)
    if r.status != 1:
        raise RuntimeError(f"transaction reverted: {tx.to_0x_hex()}")
    fee = r.gasUsed * (r.get("effectiveGasPrice") or 0)
    return r, Receipt(tx.to_0x_hex(), r.gasUsed, r.blockNumber, fee, method or getattr(fn, "fn_name", ""), str(sender))


class Saat:
    def __init__(self, w3: Web3, registry_address: str, market_address: str, signer: Signer | None = None):
        self.w3 = w3
        self.signer = signer
        self.history: list[Receipt] = []  # every transaction this client sent, in order
        self.last_block = 0  # block of our latest receipt; public-network reads are pinned to it
        self.registry = w3.eth.contract(address=registry_address, abi=_artifact("SaatRegistry")["abi"])
        self.market = w3.eth.contract(address=market_address, abi=_artifact("SaatMarket")["abi"])

    @classmethod
    def deploy(cls, w3: Web3, admin: str, signer: Signer | None = None) -> "Saat":
        reg_art, mkt_art = _artifact("SaatRegistry"), _artifact("SaatMarket")
        reg = w3.eth.contract(abi=reg_art["abi"], bytecode=reg_art["bytecode"])
        r1, rec1 = _execute(w3, signer, reg.constructor(admin), admin, method="deploy SaatRegistry")
        mkt = w3.eth.contract(abi=mkt_art["abi"], bytecode=mkt_art["bytecode"])
        r2, rec2 = _execute(w3, signer, mkt.constructor(r1.contractAddress), admin, method="deploy SaatMarket")
        saat = cls(w3, r1.contractAddress, r2.contractAddress, signer=signer)
        saat.history += [rec1, rec2]
        saat.last_block = rec2.block
        return saat

    def read(self, fn, sender: str | None = None):
        """A contract read. On a public network (a signer is set) it is pinned to the block of our
        latest receipt and retried, because a load-balanced RPC may answer from a node a block
        behind; a revert is never retried."""
        params = {"from": sender} if sender else {}
        if self.signer is None:
            return fn.call(params) if params else fn.call()
        for attempt in range(10):
            try:
                return fn.call(params, block_identifier=self.last_block or "latest")
            except (ContractCustomError, ContractLogicError):
                raise
            except Exception:
                if attempt == 9:
                    raise
                time.sleep(2)

    def _send(self, fn, sender: str, value: int = 0) -> Receipt:
        _, rec = _execute(self.w3, self.signer, fn, sender, value)
        self.history.append(rec)
        self.last_block = max(self.last_block, rec.block)
        return rec

    def send_eth(self, sender: str, to: str, value_wei: int) -> Receipt:
        """Plain ETH transfer (for example, gas money for a consumer's wallet)."""
        if self.signer is not None and sender in self.signer:
            tx = self.signer.send_value(sender, to, value_wei)
            r = self.signer.wait(tx)
        else:
            tx = self.w3.eth.send_transaction({"from": sender, "to": to, "value": value_wei})
            r = self.w3.eth.wait_for_transaction_receipt(tx)
        if r.status != 1:
            raise RuntimeError(f"transaction reverted: {tx.to_0x_hex()}")
        rec = Receipt(tx.to_0x_hex(), r.gasUsed, r.blockNumber, r.gasUsed * (r.get("effectiveGasPrice") or 0), "transfer ETH", str(sender))
        self.history.append(rec)
        self.last_block = max(self.last_block, rec.block)
        return rec

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

    def _approve_market(self, seller):
        if not self.read(self.registry.functions.isApprovedForAll(seller, self.market.address)):
            self._send(self.registry.functions.setApprovalForAll(self.market.address, True), seller)

    def list(self, seller, cert_id, wh, price_wei_per_wh) -> tuple[int, Receipt]:
        self._approve_market(seller)
        listing_id = self.read(self.market.functions.listingCount())
        return listing_id, self._send(self.market.functions.list(cert_id, wh, price_wei_per_wh), seller)

    def list_batch(self, seller, cert_ids, whs, prices_wei_per_wh) -> tuple[int, Receipt]:
        """List many hours in one transaction; returns the first listing id (the rest follow)."""
        self._approve_market(seller)
        first = self.read(self.market.functions.listingCount())
        return first, self._send(self.market.functions.listBatch(cert_ids, whs, prices_wei_per_wh), seller)

    def buy_batch(self, buyer, listing_ids, whs) -> Receipt:
        prices = [self.read(self.market.functions.listings(l))[3] for l in listing_ids]
        value = sum(w * p for w, p in zip(whs, prices))
        return self._send(self.market.functions.buyBatch(listing_ids, whs), buyer, value=value)

    def buy(self, buyer, listing_id, wh) -> Receipt:
        price = self.read(self.market.functions.listings(listing_id))[3]
        return self._send(self.market.functions.buy(listing_id, wh), buyer, value=wh * price)

    # ---------------------------------------------------------- claims

    def prove_and_retire(self, owner, meter_id, hour, load_tree: DayTree, ids, amounts) -> Receipt:
        i = load_tree.hours.index(hour)
        fn = self.registry.functions.proveAndRetire(meter_id, hour, load_tree.whs[i], load_tree.proof(i), ids, amounts)
        return self._send(fn, owner)

    def settle_day(self, owner, meter_id, load_tree: DayTree, ids: list[list[int]], amounts: list[list[int]]) -> Receipt:
        """Prove all of a day's hourly loads and retire each hour's certificates in one transaction."""
        fn = self.registry.functions.settleDay(
            meter_id, load_tree.hours, load_tree.whs, [load_tree.proof(i) for i in range(len(load_tree.hours))], ids, amounts
        )
        return self._send(fn, owner)

    def balances(self, owner, cert_ids: list[int]) -> list[int]:
        return self.read(self.registry.functions.balanceOfBatch([owner] * len(cert_ids), cert_ids))

    def claim_summary(self, meter_id, from_day, to_day) -> dict:
        load, matched, bps = self.read(self.registry.functions.claimSummary(meter_id, from_day, to_day))
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
        return self.read(self.registry.functions.balanceOf(owner, cert_id))
