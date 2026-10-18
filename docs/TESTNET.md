# Running the demo day on a public testnet

`scripts/public_chain_day.py` runs the same Antalya market day as the dashboard and the film on a public EVM testnet (Ethereum Sepolia by default). Every transaction is signed locally:
- **Issuance:** both plants' days are verified and issued.
- **Market:** contracted deliveries go out, the surplus is listed in one transaction per plant, and each consumer buys in one transaction.
- **Settlement:** each consumer's day settles in one `settleDay` transaction.
- **Hour rule:** a same-hour violation reverts.

The hotel's on-chain score is 84.6%, the same as in the film.

## Wallets

There are three keys in `.env` at the project root. The file is git-ignored and readable only by its owner, and the keys are never printed:

| Role | What it does | Fund it? |
|---|---|---|
| operator | registry admin, meter issuers, both plants | **yes, the only one** |
| hotel | buys and settles | topped up by the operator |
| campus | buys and settles | topped up by the operator |

To make new ones: `cast wallet new`, then put `SAAT_OPERATOR_KEY`, `SAAT_HOTEL_KEY` and `SAAT_CAMPUS_KEY` in `.env`. `RPC_URL` defaults to `https://ethereum-sepolia-rpc.publicnode.com`.

## Steps

```bash
uv run python scripts/public_chain_day.py --local   # rehearsal on a fresh Anvil with the same keys
uv run python scripts/public_chain_day.py           # Sepolia: checks fees and balance, then asks
```

1. **Fund the operator.** Use any Sepolia faucet (for example the Google Cloud Web3 faucet, or the proof-of-work faucet at sepolia-faucet.pk910.de). The whole day uses about 150M gas on Sepolia's current pricing. At today's base fee of about 15 wei that is a fraction of a cent of test ETH, so the smallest faucet drip is enough. The script prints the exact budget at the current fee and refuses to start if the balance is short.
2. **Run** the script. It refuses any chain that is not a known testnet and asks before sending. It writes `results/public_chain_day.json`, which holds the contract addresses, every transaction hash with an explorer link, gas and fees, and both consumers' `claimSummary`.
3. **Verify the source on Sourcify** (no API key needed). The result file prints the exact commands; run them in `contracts/`:
   ```bash
   forge verify-contract --verifier sourcify --chain sepolia <registry> src/SaatRegistry.sol:SaatRegistry --constructor-args $(cast abi-encode 'constructor(address)' <operator>)
   forge verify-contract --verifier sourcify --chain sepolia <market> src/SaatMarket.sol:SaatMarket --constructor-args $(cast abi-encode 'constructor(address)' <registry>)
   ```
4. **Check it yourself.** On the explorer, call `claimSummary(7, 20279, 20279)` on SaatRegistry, where 20279 is the market day of 2025-07-10. It returns the hotel's load, its matched energy and 8460 basis points.

## Deploying only

`contracts/script/DeploySaat.s.sol` deploys the two contracts with Foundry. On Sepolia, contract creation now costs far more gas than Foundry's local simulation predicts, so pass `--skip-simulation --slow`, as the script header explains.
