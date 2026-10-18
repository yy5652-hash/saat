// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {ERC1155Holder} from "@openzeppelin/contracts/token/ERC1155/utils/ERC1155Holder.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title Saat peer-to-peer market for hourly certificates
/// @notice A generator lists the certificates of one hour at a price per Wh; the certificates sit in
///         escrow until bought or withdrawn, so a listed hour cannot also be sold elsewhere.
contract SaatMarket is ERC1155Holder, ReentrancyGuard {
    struct Listing {
        address seller;
        uint256 id;
        uint256 remainingWh;
        uint256 priceWeiPerWh;
    }

    IERC1155 public immutable registry;
    Listing[] public listings;

    event Listed(uint256 indexed listingId, address indexed seller, uint256 indexed id, uint256 wh, uint256 priceWeiPerWh);
    event Bought(uint256 indexed listingId, address indexed buyer, uint256 wh, uint256 paidWei);
    event Cancelled(uint256 indexed listingId, uint256 returnedWh);

    error NotSeller();
    error NotEnough();
    error WrongPayment();
    error PayoutFailed();

    constructor(IERC1155 registry_) {
        registry = registry_;
    }

    function list(uint256 id, uint256 wh, uint256 priceWeiPerWh) external returns (uint256 listingId) {
        listingId = listings.length;
        listings.push(Listing(msg.sender, id, wh, priceWeiPerWh));
        registry.safeTransferFrom(msg.sender, address(this), id, wh, "");
        emit Listed(listingId, msg.sender, id, wh, priceWeiPerWh);
    }

    function buy(uint256 listingId, uint256 wh) external payable nonReentrant {
        Listing storage l = listings[listingId];
        if (wh == 0 || wh > l.remainingWh) revert NotEnough();
        uint256 cost = wh * l.priceWeiPerWh;
        if (msg.value != cost) revert WrongPayment();
        l.remainingWh -= wh;
        registry.safeTransferFrom(address(this), msg.sender, l.id, wh, "");
        (bool ok,) = l.seller.call{value: cost}("");
        if (!ok) revert PayoutFailed();
        emit Bought(listingId, msg.sender, wh, cost);
    }

    function cancel(uint256 listingId) external nonReentrant {
        Listing storage l = listings[listingId];
        if (msg.sender != l.seller) revert NotSeller();
        uint256 wh = l.remainingWh;
        l.remainingWh = 0;
        registry.safeTransferFrom(address(this), msg.sender, l.id, wh, "");
        emit Cancelled(listingId, wh);
    }

    function listingCount() external view returns (uint256) {
        return listings.length;
    }
}
