"""Merkle commitments for one meter-day, byte-compatible with SaatRegistry.

Leaf:  keccak256(keccak256(abi.encode(uint8 kind, uint32 subject, uint64 hour, uint64 wh)))
Node:  keccak256 of the two children in sorted order (OpenZeppelin MerkleProof).
An odd node at the end of a level is promoted unchanged.
"""

from dataclasses import dataclass

from eth_abi import encode
from eth_utils import keccak

KIND_GENERATION = 1
KIND_CONSUMPTION = 2


def leaf_hash(kind: int, subject: int, hour: int, wh: int) -> bytes:
    return keccak(keccak(encode(["uint8", "uint32", "uint64", "uint64"], [kind, subject, hour, wh])))


def _pair(a: bytes, b: bytes) -> bytes:
    return keccak(a + b) if a < b else keccak(b + a)


def _up(level: list[bytes]) -> list[bytes]:
    nxt = [_pair(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
    if len(level) % 2:
        nxt.append(level[-1])
    return nxt


@dataclass
class DayTree:
    kind: int
    subject: int
    hours: list[int]
    whs: list[int]

    def __post_init__(self):
        if not self.hours:
            raise ValueError("a day commitment needs at least one hour")
        self.leaves = [leaf_hash(self.kind, self.subject, h, w) for h, w in zip(self.hours, self.whs)]
        self.levels = [self.leaves]
        while len(self.levels[-1]) > 1:
            self.levels.append(_up(self.levels[-1]))

    @property
    def root(self) -> bytes:
        return self.levels[-1][0]

    @property
    def total_wh(self) -> int:
        return sum(self.whs)

    def proof(self, index: int) -> list[bytes]:
        out = []
        for level in self.levels[:-1]:
            sib = index ^ 1
            if sib < len(level):
                out.append(level[sib])
            index //= 2
        return out

    def proof_for_hour(self, hour: int) -> list[bytes]:
        return self.proof(self.hours.index(hour))


def verify(proof: list[bytes], root: bytes, leaf: bytes) -> bool:
    node = leaf
    for p in proof:
        node = _pair(node, p)
    return node == root
