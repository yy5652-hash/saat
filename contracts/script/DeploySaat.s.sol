// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {SaatRegistry} from "../src/SaatRegistry.sol";
import {SaatMarket} from "../src/SaatMarket.sol";

/// @notice Deploys SaatRegistry(admin) and SaatMarket(registry); optionally runs the setup calls of
///         the compact Antalya market day (issuer, zone TR at UTC+3, solar device 1, hotel meter 7).
///
/// Environment (read inside the script, so no key ever appears on the command line):
///   SAAT_OPERATOR_KEY  deployer; becomes the registry admin, the issuer and the owner of device 1
///   SAAT_SETUP         optional, true to run the setup calls (default false)
///   SAAT_HOTEL         optional hotel address for meter 7 (else derived from SAAT_HOTEL_KEY)
///
/// From contracts/ (local Anvil):
///   forge script script/DeploySaat.s.sol --rpc-url http://127.0.0.1:8545 --broadcast
/// Public testnet (Sepolia):
///   set -a; source ../.env; set +a
///   SAAT_SETUP=true forge script script/DeploySaat.s.sol --rpc-url "$RPC_URL" --broadcast --skip-simulation --slow
/// --skip-simulation makes forge take every gas limit from the node's own eth_estimateGas: forge's
/// built-in EVM still prices contract creation the old way (about 4M gas for SaatRegistry) while
/// Sepolia now charges about 21.5M, so limits from forge's simulation would run out of gas.
/// --slow waits for each receipt, so the setup calls are estimated against the deployed registry.
/// Leave out --broadcast to send nothing. See docs/TESTNET.md for source verification.
contract DeploySaat is Script {
    bytes32 constant ZONE = bytes32("TR"); // same bytes as saat.chain.zone("TR")
    int8 constant UTC_OFFSET = 3; // Türkiye, no DST
    uint32 constant SOLAR_DEVICE = 1;
    uint64 constant SOLAR_CAPACITY_W = 2_000_000; // 2 MW AC, as in saat.scenario.SOLAR
    uint32 constant HOTEL_METER = 7;

    function run() external returns (SaatRegistry registry, SaatMarket market) {
        uint256 key = vm.envUint("SAAT_OPERATOR_KEY");
        address operator = vm.addr(key);

        vm.startBroadcast(key);
        registry = new SaatRegistry(operator);
        market = new SaatMarket(registry);
        if (vm.envOr("SAAT_SETUP", false)) {
            registry.setIssuer(operator, true);
            registry.setZoneOffset(ZONE, UTC_OFFSET);
            registry.registerDevice(SOLAR_DEVICE, operator, ZONE, SOLAR_CAPACITY_W, 1);
            address hotel = vm.envOr("SAAT_HOTEL", address(0));
            if (hotel == address(0) && vm.envExists("SAAT_HOTEL_KEY")) hotel = vm.addr(vm.envUint("SAAT_HOTEL_KEY"));
            if (hotel != address(0)) registry.registerMeter(HOTEL_METER, hotel, ZONE);
        }
        vm.stopBroadcast();

        console.log("admin / operator  ", operator);
        console.log("SaatRegistry      ", address(registry));
        console.log("SaatMarket        ", address(market));
        console.log("registry constructor args (abi.encode(admin)):");
        console.logBytes(abi.encode(operator));
        console.log("market constructor args (abi.encode(registry)):");
        console.logBytes(abi.encode(address(registry)));
    }
}
