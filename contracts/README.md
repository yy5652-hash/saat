# Saat contracts

- `src/SaatRegistry.sol`: hourly certificates (ERC-1155, 1 unit = 1 Wh, `id = plantId << 64 | hour`), Merkle-committed plant-days, lazy minting once per plant-hour, same-hour same-zone retirement capped by proven load, on-chain hourly score.
- `src/SaatMarket.sol`: escrowed peer-to-peer listings of one hour's certificates.

```bash
forge test            # 22 tests, incl. a fuzz test
forge test --gas-report
```

Merkle leaves are `keccak256(keccak256(abi.encode(uint8 kind, uint32 subject, uint64 hour, uint64 wh)))` with sorted-pair hashing, matching `saat/merkle.py`.
