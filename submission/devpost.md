# Devpost submission draft: Saat

> Draft for review. Nothing has been submitted. Links marked TODO are filled in once the repo is public and the video is uploaded.

**Project name:** Saat: hourly proof for clean energy

**Elevator pitch (≤200 chars):**
"100% renewable" is an annual sum. Saat makes the hour the unit of proof: AI-verified hourly certificates, traded peer to peer and retired on-chain only against same-hour load.

**Track:** Renewable Energy & Energy Trading (renewable certificate tracking, P2P trading, tokenized incentives). It also serves Carbon Markets & Emissions Transparency (smart contracts that prevent double counting).

**Built with:** solidity, foundry, openzeppelin, erc-1155, merkle-trees, python, scikit-learn, pvlib, web3.py, fastapi, chart.js, anvil, sepolia, epias, iea, ember, eia-930, era5, open-meteo, nrel-pvdaq

**Links:**
- Code: https://github.com/yy5652-hash/saat (TODO: make public)
- Demo video: TODO (YouTube, 3:57)
- Prototype: runs locally with `uv run uvicorn app.server:app` (instructions in the README)

---

## About the project

### Inspiration
Türkiye hosts COP31 in Antalya this November. Antalya's hotels, like data centres and factories everywhere, can call themselves "100% renewable" by buying certificates equal to their annual use. But a solar megawatt-hour from a June noon did not power a hotel at 9 p.m. in July. We wanted to measure how big that gap really is, and build the infrastructure that closes it: certificates per hour, which can be checked, traded and claimed honestly.

### The problem, measured
- **On Türkiye's real grid:** we used EPİAŞ real-time generation for every hour of 2025, via the IEA Real-Time Electricity Tracker, with solar scaled to Ember's national totals. A 24/7 consumer holding wind and solar certificates for 100% of its annual use is matched only **72% hour by hour**. The other 128 kg CO₂e/MWh is reported as zero.
- **Across U.S. grids:** we used EIA-930 hourly data for 2025 across 22 U.S. balancing authorities. A consumer that buys wind and solar certificates equal to 100% of its annual use is matched only **65.5% hour by hour** (median; range 41–82%).
- **Hidden emissions:** the rest, a median **124 kg CO₂e per MWh consumed**, is reported as zero.
- **Solar-only buyers** reach just 43.7%.
- **Where the rules are going:** the GHG Protocol's Scope 2 revision has put hourly matching out for consultation, and U.S. registries already issue hourly certificates.
- **Why that is hard:** hourly means 8,760 certificates per meter per year. Issuance has to be cheap, individual hours have to be tradeable, and claims have to be checkable by anyone.

### What it does
1. **AI issuance verifier.** Before a day of meter data can become certificates, every hour is checked against independent weather (ERA5 reanalysis) and the sun's position, using gradient-boosted quantile models trained on the plant's own history. Hours outside the weather envelope are held for review: power after sunset, impossible output, a day whose output has been moved later in time, a replayed sunny day. The hash of the decision report is committed on-chain.
2. **Merkle-committed hourly certificates.** One transaction per plant per day commits 24 hourly readings as a Merkle root (about 99k gas). Each hour is minted lazily as an ERC-1155 token (`id = plant ∥ hour`, 1 unit = 1 Wh), and **each plant-hour can be minted only once**. Registry days follow the local market day (Türkiye: UTC+3).
3. **Peer-to-peer market.** Generators list each surplus hour in escrow, so a listed hour cannot be sold twice. Hours with no sun and little wind are scarce, so they carry higher prices.
4. **AI day-ahead planner.** Using only the weather forecast issued the day before, gradient boosting forecasts tomorrow's certificates hour by hour. The planner moves flexible load (laundry, pool pumps, water heating, shuttle charging) into surplus hours and buys the remaining shortfall from the cheapest listings.
5. **Hourly retirement and on-chain score.** The utility commits the consumer's 24 meter readings. In **one transaction** (`settleDay`), the consumer proves every hour and burns certificates **from the same hour and the same zone, never above that hour's load**. The whole day's load counts toward the score even for hours the consumer never proves. `claimSummary` returns the hourly carbon-free score, computed on-chain. A noon certificate presented against 9 p.m. load reverts with `HourMismatch`.

### Results
- **Verifier on a real plant.** We tested on NREL PVDAQ system 9068, a 4.7 MW tracker plant in Colorado: trained on 2017–2022, calibrated on 2023, tested on 16 unseen months with fraud simulated on the real data. The verifier blocks:
  - 100% of power claimed after sunset;
  - 81% of output shifted two hours later;
  - 70% of one-hour spikes;
  - 63% of a +50% inflation.

  It holds back only **0.52%** of genuine energy, against 7.1% for physics rules alone. A capacity check, which is what registries typically apply today, blocks none of them. Its hourly error is 10.4% of capacity, against 18.0% for physics alone. A 30-day drift test catches 94% of persistent +30% inflation within 90 days, with no false alarms in 424 clean days.
- **Antalya, all of 2025.** The community is a 2 MW community solar plant and a 6 MW Aegean wind share supplying a seaside hotel and an office campus. Supply is modelled from real weather; the loads are synthetic. The hotel does every day what it does on the demo day:
  - contract only: 62.9% matched hour by hour;
  - with AI day-ahead shifting: 67.5%;
  - plus buying its forecast shortfall on the hourly market: **82.8%**, buying 18% more certificates, only in the hours it is short.

  Its median day is 85%, so the demo day is typical, not lucky. The wind forecaster's error is 4.9% of capacity, against 11.2% for persistence. Even with access to only a quarter of the plants' spare output, the community reaches 77.6%.
- **Demo day (10 July 2025) on a live chain.** The hotel's contract alone covers 59.9% of its load hour by hour. After trading and planning, the on-chain score is **84.6%**.

### Who uses it, and how it scales
- **Registries and grid operators:** in Türkiye, EPİAŞ already issues YEK-G guarantees of origin, one per MWh. Saat lets them commit one root per plant-day from the settlement data they already hold. It adds hourly granularity and a public guard against double issuance and double claims.
- **Generators and energy cooperatives:** sell the hours that are worth most. Rooftop and community solar without settlement-grade meters are where the AI verifier matters most.
- **Corporate buyers** (hotels, data centres, exporters facing carbon border rules): buy the hours they are short of, shift load, and publish a claim anyone can recompute from the chain.
- **Scale:** one transaction per plant per day, lazy minting, and plain Solidity/OpenZeppelin deployable on any EVM chain, including low-fee rollups or a registry-run permissioned network. Measured gas: commit 99k per plant-day; a consumer's whole day settles in one transaction (1.17M gas for 24 hours and 35 retirements, 30% less than hour by hour).

### How we built it
- **Contracts:** Solidity 0.8.28 with OpenZeppelin 5.4 (ERC-1155, MerkleProof, ReentrancyGuard), tested with Foundry. 25 tests, including a fuzz test that matched energy never exceeds load.
- **Off-chain:** a Python Merkle builder that is byte-compatible with the contracts, and a web3.py client that signs locally, for Anvil and public testnets (`scripts/public_chain_day.py`).
- **AI:** pvlib physics features, scikit-learn quantile gradient boosting for the verifier and gradient boosting for the planner. 21 Python tests, including an end-to-end run on a local chain.
- **Data:**
  - EPİAŞ hourly generation via the IEA Real-Time Electricity Tracker, with Ember monthly solar (2024–2025);
  - EIA-930 six-month files (2025);
  - PVDAQ via the DOE Open Energy Data Initiative;
  - ERA5 reanalysis and the previous-day forecast archive via Open-Meteo.
- **Dashboard:** a FastAPI server and a dark "control room" dashboard. Its signature 24-hour ring shows each hour's certificates against load, and it runs a whole market day on a fresh chain.

### Challenges
- **Proving the gap.** Annual claims and hourly matching are easy to talk about but need real hourly data. We processed every 2025 EIA-930 hour for 22 regions.
- **Honest verification.** A model is only as good as its independent record. We found that an Open-Meteo "satellite" series was identical to ERA5 at our sites, so we did not count it as a second source.
- **No double counting.** Committing whole days while minting each hour only once took care. So did counting the full day's load even when a consumer proves only its good hours.
- **Time zones.** Certificates are hourly in UTC, but markets settle on local days. The registry maps zones to local market days on-chain.

### What we learned
- Blockchain solves the double-counting part well. The hard part is the data entering it, which is why the AI verifier and the evidence matter.
- Hourly matching changes procurement: the hours you are short of cost more, and flexibility is worth real money.

### What's next
- Run a pilot with a registry's settlement data, such as YEK-G's hourly metering.
- Add peer comparison across neighbouring plants, alongside the 30-day drift test.
- Support storage-shifted certificates and cross-zone deliverability rules.
- Move from the public testnet to a low-fee rollup or a registry-run network.

### Data notes
- The Antalya plants are modelled from real weather, and the hotel and campus loads are synthetic. No real company is depicted.
- The real-data claims come from the EPİAŞ, EIA-930 and PVDAQ studies.
- The dashboard runs the day on a local chain; `scripts/public_chain_day.py` runs the same day on a public testnet.
- The video narration uses a synthetic voice (Chatterbox, MIT licence). Footage is Pexels-licensed stock and NASA imagery, credited in the video.
