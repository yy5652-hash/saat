// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {Hashes} from "@openzeppelin/contracts/utils/cryptography/Hashes.sol";
import {SaatRegistry} from "../src/SaatRegistry.sol";
import {SaatMarket} from "../src/SaatMarket.sol";

/// @dev The same tree the Python issuer builds: sorted-pair keccak, an odd node is promoted.
library Merkle {
    function next(bytes32[] memory level) internal pure returns (bytes32[] memory up) {
        uint256 n = level.length;
        up = new bytes32[]((n + 1) / 2);
        for (uint256 i = 0; i < n / 2; i++) {
            up[i] = Hashes.commutativeKeccak256(level[2 * i], level[2 * i + 1]);
        }
        if (n % 2 == 1) up[n / 2] = level[n - 1];
    }

    function root(bytes32[] memory leaves) internal pure returns (bytes32) {
        bytes32[] memory level = leaves;
        while (level.length > 1) level = next(level);
        return level[0];
    }

    function proof(bytes32[] memory leaves, uint256 index) internal pure returns (bytes32[] memory out) {
        bytes32[] memory tmp = new bytes32[](32);
        uint256 depth;
        bytes32[] memory level = leaves;
        while (level.length > 1) {
            uint256 sib = index ^ 1;
            if (sib < level.length) tmp[depth++] = level[sib];
            level = next(level);
            index /= 2;
        }
        out = new bytes32[](depth);
        for (uint256 i = 0; i < depth; i++) out[i] = tmp[i];
    }
}

contract SaatTest is Test {
    SaatRegistry reg;
    SaatMarket market;

    address admin = makeAddr("admin");
    address issuer = makeAddr("issuer");
    address solarCoop = makeAddr("solarCoop");
    address hotel = makeAddr("hotel");
    address farAway = makeAddr("farAway");

    bytes32 constant ANTALYA = bytes32("TR-ANTALYA");
    bytes32 constant IZMIR = bytes32("TR-IZMIR");
    uint32 constant SOLAR = 1;
    uint32 constant DISTANT = 2;
    uint32 constant HOTEL_METER = 7;
    uint32 constant DAY = 20_000;
    uint64 constant CAPACITY_W = 500_000; // 500 kW

    function setUp() public {
        reg = new SaatRegistry(admin);
        market = new SaatMarket(reg);
        vm.startPrank(admin);
        reg.setIssuer(issuer, true);
        reg.registerDevice(SOLAR, solarCoop, ANTALYA, CAPACITY_W, 1);
        reg.registerDevice(DISTANT, farAway, IZMIR, CAPACITY_W, 1);
        reg.registerMeter(HOTEL_METER, hotel, ANTALYA);
        vm.stopPrank();
    }

    // ------------------------------------------------------------ helpers

    function hourOf(uint32 day, uint64 h) internal pure returns (uint64) {
        return uint64(day) * 24 + h;
    }

    function solarWh(uint64 h) internal pure returns (uint64) {
        if (h < 6 || h > 17) return 0;
        uint64 x = h < 12 ? h - 5 : 18 - h; // 1..6..1
        return x * 60_000;
    }

    function genLeaves(uint32 device, uint32 day, uint64[] memory whs) internal view returns (bytes32[] memory leaves) {
        leaves = new bytes32[](whs.length);
        for (uint64 h = 0; h < whs.length; h++) {
            leaves[h] = reg.leafHash(1, device, hourOf(day, h), whs[h]);
        }
    }

    function dayOfSolar() internal pure returns (uint64[] memory whs) {
        whs = new uint64[](24);
        for (uint64 h = 0; h < 24; h++) whs[h] = solarWh(h);
    }

    function commitSolarDay(uint32 device, uint32 day) internal returns (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) {
        whs = dayOfSolar();
        leaves = genLeaves(device, day, whs);
        root = Merkle.root(leaves);
        vm.prank(issuer);
        reg.commitGeneration(device, day, root, keccak256("report"), 2_160_000, 0);
    }

    function flatLoad() internal pure returns (uint64[] memory whs) {
        whs = new uint64[](24);
        for (uint64 h = 0; h < 24; h++) whs[h] = 200_000; // 200 kW all day
    }

    function commitHotelDay(uint32 day) internal returns (bytes32[] memory leaves, uint64[] memory whs) {
        whs = flatLoad();
        leaves = new bytes32[](24);
        for (uint64 h = 0; h < 24; h++) leaves[h] = reg.leafHash(2, HOTEL_METER, hourOf(day, h), whs[h]);
        vm.prank(issuer);
        reg.commitConsumption(HOTEL_METER, day, Merkle.root(leaves), keccak256("meter"), 24 * 200_000);
    }

    function mintHour(uint32 device, bytes32 root, bytes32[] memory leaves, uint64[] memory whs, uint64 h)
        internal
        returns (uint256 id)
    {
        id = reg.mint(device, hourOf(DAY, h), whs[h], root, Merkle.proof(leaves, h));
    }

    // ------------------------------------------------------------ issuance

    function test_MintFromCommittedRoot() public {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(SOLAR, DAY);
        uint256 id = mintHour(SOLAR, root, leaves, whs, 12);
        assertEq(reg.balanceOf(solarCoop, id), whs[12]);
        (uint32 dev, uint64 hour) = reg.decodeId(id);
        assertEq(dev, SOLAR);
        assertEq(hour, hourOf(DAY, 12));
    }

    function test_HourMintsOnlyOnce() public {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(SOLAR, DAY);
        mintHour(SOLAR, root, leaves, whs, 12);
        bytes32[] memory p = Merkle.proof(leaves, 12);
        vm.expectRevert(SaatRegistry.AlreadyMinted.selector);
        reg.mint(SOLAR, hourOf(DAY, 12), whs[12], root, p);
    }

    function test_SecondRootCannotRemintAnHour() public {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(SOLAR, DAY);
        mintHour(SOLAR, root, leaves, whs, 12);
        // An issuer re-commits the same hour inside a new root (larger amount); it still cannot mint twice.
        bytes32[] memory again = new bytes32[](1);
        again[0] = reg.leafHash(1, SOLAR, hourOf(DAY, 12), 400_000);
        vm.prank(issuer);
        reg.commitGeneration(SOLAR, DAY, again[0], keccak256("amend"), 400_000, 0);
        vm.expectRevert(SaatRegistry.AlreadyMinted.selector);
        reg.mint(SOLAR, hourOf(DAY, 12), 400_000, again[0], new bytes32[](0));
    }

    function test_HeldHourReleasedByAmendment() public {
        uint64[] memory whs = dayOfSolar();
        uint64 heldWh = whs[13];
        whs[13] = 0; // the verifier held hour 13 back
        bytes32[] memory leaves = genLeaves(SOLAR, DAY, whs);
        vm.prank(issuer);
        reg.commitGeneration(SOLAR, DAY, Merkle.root(leaves), keccak256("report"), 0, 1);

        bytes32 amendLeaf = reg.leafHash(1, SOLAR, hourOf(DAY, 13), heldWh);
        vm.prank(issuer);
        reg.commitGeneration(SOLAR, DAY, amendLeaf, keccak256("review"), heldWh, 0);
        uint256 id = reg.mint(SOLAR, hourOf(DAY, 13), heldWh, amendLeaf, new bytes32[](0));
        assertEq(reg.balanceOf(solarCoop, id), heldWh);
    }

    function test_TamperedAmountFailsProof() public {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(SOLAR, DAY);
        bytes32[] memory p = Merkle.proof(leaves, 12);
        vm.expectRevert(SaatRegistry.BadProof.selector);
        reg.mint(SOLAR, hourOf(DAY, 12), whs[12] + 1, root, p);
    }

    function test_HourOutsideRootDayReverts() public {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(SOLAR, DAY);
        bytes32[] memory p = Merkle.proof(leaves, 12);
        vm.expectRevert(SaatRegistry.WrongDay.selector);
        reg.mint(SOLAR, hourOf(DAY + 1, 12), whs[12], root, p);
    }

    function test_OverCapacityReverts() public {
        bytes32 leaf = reg.leafHash(1, SOLAR, hourOf(DAY, 12), CAPACITY_W + 1);
        vm.prank(issuer);
        reg.commitGeneration(SOLAR, DAY, leaf, keccak256("r"), CAPACITY_W + 1, 0);
        vm.expectRevert(SaatRegistry.OverCapacity.selector);
        reg.mint(SOLAR, hourOf(DAY, 12), CAPACITY_W + 1, leaf, new bytes32[](0));
    }

    function test_OnlyIssuerCommits() public {
        vm.expectRevert(SaatRegistry.NotIssuer.selector);
        vm.prank(solarCoop);
        reg.commitGeneration(SOLAR, DAY, keccak256("x"), bytes32(0), 1, 0);
    }

    function test_RootFromOtherDeviceRejected() public {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(SOLAR, DAY);
        bytes32[] memory p = Merkle.proof(leaves, 12);
        vm.expectRevert(SaatRegistry.BadBatch.selector);
        reg.mint(DISTANT, hourOf(DAY, 12), whs[12], root, p);
    }

    // ------------------------------------------------------------ claims

    function buyHourFromCoop(uint64 h, uint256 wh) internal returns (uint256 id) {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(SOLAR, DAY);
        id = mintHour(SOLAR, root, leaves, whs, h);
        vm.prank(solarCoop);
        reg.safeTransferFrom(solarCoop, hotel, id, wh, "");
    }

    function retireAs(uint64 h, uint64 loadWh, bytes32[] memory loadLeaves, uint256 id, uint256 wh) internal {
        uint256[] memory ids = new uint256[](1);
        uint256[] memory amounts = new uint256[](1);
        ids[0] = id;
        amounts[0] = wh;
        vm.prank(hotel);
        reg.proveAndRetire(HOTEL_METER, hourOf(DAY, h), loadWh, Merkle.proof(loadLeaves, h), ids, amounts);
    }

    function test_RetireSameHourSameZone() public {
        uint256 id = buyHourFromCoop(12, 150_000);
        (bytes32[] memory loadLeaves, uint64[] memory load) = commitHotelDay(DAY);
        retireAs(12, load[12], loadLeaves, id, 150_000);
        assertEq(reg.balanceOf(hotel, id), 0);
        (,, bool proven) = reg.slots(HOTEL_METER, hourOf(DAY, 12));
        assertTrue(proven);
        (uint256 loadWh, uint256 matchedWh, uint256 bps) = reg.claimSummary(HOTEL_METER, DAY, DAY);
        assertEq(loadWh, 24 * 200_000);
        assertEq(matchedWh, 150_000);
        // One matched hour out of a full day: unproven hours still count as load.
        assertEq(bps, uint256(150_000 * 10_000) / (24 * 200_000));
    }

    function test_NoonCertificateCannotCoverMidnight() public {
        uint256 id = buyHourFromCoop(12, 150_000);
        (bytes32[] memory loadLeaves, uint64[] memory load) = commitHotelDay(DAY);
        uint256[] memory ids = new uint256[](1);
        uint256[] memory amounts = new uint256[](1);
        ids[0] = id;
        amounts[0] = 150_000;
        bytes32[] memory p = Merkle.proof(loadLeaves, 0);
        vm.prank(hotel);
        vm.expectRevert(SaatRegistry.HourMismatch.selector);
        reg.proveAndRetire(HOTEL_METER, hourOf(DAY, 0), load[0], p, ids, amounts);
    }

    function test_OtherZoneRejected() public {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(DISTANT, DAY);
        uint256 id = reg.mint(DISTANT, hourOf(DAY, 12), whs[12], root, Merkle.proof(leaves, 12));
        vm.prank(farAway);
        reg.safeTransferFrom(farAway, hotel, id, 100_000, "");
        (bytes32[] memory loadLeaves, uint64[] memory load) = commitHotelDay(DAY);
        uint256[] memory ids = new uint256[](1);
        uint256[] memory amounts = new uint256[](1);
        ids[0] = id;
        amounts[0] = 100_000;
        bytes32[] memory p = Merkle.proof(loadLeaves, 12);
        vm.prank(hotel);
        vm.expectRevert(SaatRegistry.ZoneMismatch.selector);
        reg.proveAndRetire(HOTEL_METER, hourOf(DAY, 12), load[12], p, ids, amounts);
    }

    function test_CannotRetireMoreThanLoad() public {
        uint256 id = buyHourFromCoop(12, 360_000); // noon output 360 kWh, load is 200 kWh
        (bytes32[] memory loadLeaves, uint64[] memory load) = commitHotelDay(DAY);
        uint256[] memory ids = new uint256[](1);
        uint256[] memory amounts = new uint256[](1);
        ids[0] = id;
        amounts[0] = 200_001;
        bytes32[] memory p = Merkle.proof(loadLeaves, 12);
        vm.prank(hotel);
        vm.expectRevert(SaatRegistry.ExceedsLoad.selector);
        reg.proveAndRetire(HOTEL_METER, hourOf(DAY, 12), load[12], p, ids, amounts);
    }

    function test_OnlyMeterOwnerRetires() public {
        uint256 id = buyHourFromCoop(12, 100_000);
        (bytes32[] memory loadLeaves, uint64[] memory load) = commitHotelDay(DAY);
        reg.proveLoad(HOTEL_METER, hourOf(DAY, 12), load[12], Merkle.proof(loadLeaves, 12));
        uint256[] memory ids = new uint256[](1);
        uint256[] memory amounts = new uint256[](1);
        ids[0] = id;
        amounts[0] = 1;
        vm.prank(solarCoop);
        vm.expectRevert(SaatRegistry.NotMeterOwner.selector);
        reg.retire(HOTEL_METER, hourOf(DAY, 12), ids, amounts);
    }

    function test_ConsumptionCommittedOncePerDay() public {
        commitHotelDay(DAY);
        vm.prank(issuer);
        vm.expectRevert(SaatRegistry.AlreadyCommitted.selector);
        reg.commitConsumption(HOTEL_METER, DAY, keccak256("second"), bytes32(0), 1);
    }

    function test_TamperedLoadFailsProof() public {
        (bytes32[] memory loadLeaves,) = commitHotelDay(DAY);
        bytes32[] memory p = Merkle.proof(loadLeaves, 12);
        vm.expectRevert(SaatRegistry.BadProof.selector);
        reg.proveLoad(HOTEL_METER, hourOf(DAY, 12), 999_999, p);
    }

    function testFuzz_MatchedNeverExceedsLoad(uint64 a, uint64 b) public {
        uint256 id = buyHourFromCoop(12, 360_000);
        (bytes32[] memory loadLeaves, uint64[] memory load) = commitHotelDay(DAY);
        a = uint64(bound(a, 1, 360_000));
        b = uint64(bound(b, 1, 360_000));
        reg.proveLoad(HOTEL_METER, hourOf(DAY, 12), load[12], Merkle.proof(loadLeaves, 12));
        uint256[] memory ids = new uint256[](1);
        uint256[] memory amounts = new uint256[](1);
        ids[0] = id;
        amounts[0] = a;
        vm.prank(hotel);
        try reg.retire(HOTEL_METER, hourOf(DAY, 12), ids, amounts) {} catch {}
        if (a + b <= 360_000) {
            amounts[0] = b;
            vm.prank(hotel);
            try reg.retire(HOTEL_METER, hourOf(DAY, 12), ids, amounts) {} catch {}
        }
        (uint64 loadWh, uint64 matchedWh,) = reg.slots(HOTEL_METER, hourOf(DAY, 12));
        assertLe(matchedWh, loadWh);
    }

    // ------------------------------------------------------------ market

    function listNoon(uint256 wh, uint256 price) internal returns (uint256 id, uint256 listingId) {
        (bytes32 root, bytes32[] memory leaves, uint64[] memory whs) = commitSolarDay(SOLAR, DAY);
        id = mintHour(SOLAR, root, leaves, whs, 12);
        vm.startPrank(solarCoop);
        reg.setApprovalForAll(address(market), true);
        listingId = market.list(id, wh, price);
        vm.stopPrank();
    }

    function test_MarketListBuyCancel() public {
        (uint256 id, uint256 listingId) = listNoon(300_000, 1 gwei);
        assertEq(reg.balanceOf(address(market), id), 300_000);
        vm.deal(hotel, 1 ether);
        vm.prank(hotel);
        market.buy{value: 120_000 * 1 gwei}(listingId, 120_000);
        assertEq(reg.balanceOf(hotel, id), 120_000);
        assertEq(solarCoop.balance, 120_000 * 1 gwei);
        vm.prank(solarCoop);
        market.cancel(listingId);
        assertEq(reg.balanceOf(solarCoop, id), 360_000 - 120_000);
        assertEq(reg.balanceOf(address(market), id), 0);
    }

    function test_MarketRejectsWrongPayment() public {
        (, uint256 listingId) = listNoon(300_000, 1 gwei);
        vm.deal(hotel, 1 ether);
        vm.prank(hotel);
        vm.expectRevert(SaatMarket.WrongPayment.selector);
        market.buy{value: 1}(listingId, 120_000);
    }

    function test_MarketRejectsOverbuy() public {
        (, uint256 listingId) = listNoon(300_000, 1 gwei);
        vm.deal(hotel, 1 ether);
        vm.prank(hotel);
        vm.expectRevert(SaatMarket.NotEnough.selector);
        market.buy{value: 300_001 * 1 gwei}(listingId, 300_001);
    }

    function test_OnlySellerCancels() public {
        (, uint256 listingId) = listNoon(300_000, 1 gwei);
        vm.prank(hotel);
        vm.expectRevert(SaatMarket.NotSeller.selector);
        market.cancel(listingId);
    }
}
