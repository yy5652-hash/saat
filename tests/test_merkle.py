import random

import pytest

from saat.merkle import KIND_GENERATION, DayTree, leaf_hash, verify


@pytest.mark.parametrize("n", [1, 2, 3, 5, 23, 24])
def test_every_proof_verifies(n):
    rng = random.Random(n)
    hours = [480_000 + h for h in range(n)]
    whs = [rng.randrange(0, 4_000_000) for _ in range(n)]
    tree = DayTree(KIND_GENERATION, 9068, hours, whs)
    for i, (h, w) in enumerate(zip(hours, whs)):
        assert verify(tree.proof(i), tree.root, leaf_hash(KIND_GENERATION, 9068, h, w))


def test_changed_amount_fails():
    tree = DayTree(KIND_GENERATION, 1, list(range(24)), [1000 * h for h in range(24)])
    assert not verify(tree.proof(12), tree.root, leaf_hash(KIND_GENERATION, 1, 12, 12_001))


def test_leaf_is_domain_separated_by_kind():
    assert leaf_hash(1, 7, 100, 5) != leaf_hash(2, 7, 100, 5)
