// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC1155} from "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

/// @title Saat: hourly clean-energy certificates
/// @notice A certificate is one ERC-1155 token id per (generator, UTC hour); one unit is 1 Wh.
///         Accredited issuers commit one Merkle root per meter-day instead of 24 transactions.
///         Certificates are minted lazily from those roots, at most once per generator-hour, and
///         can only be retired against a consumer's load in the same hour and the same grid zone.
contract SaatRegistry is ERC1155, Ownable {
    uint8 public constant KIND_GENERATION = 1;
    uint8 public constant KIND_CONSUMPTION = 2;

    struct Device {
        address owner;
        bytes32 zone;
        uint64 capacityW;
        uint8 technology; // 1 solar, 2 wind, 3 hydro, 4 geothermal
        bool active;
    }

    struct Meter {
        address owner;
        bytes32 zone;
        bool active;
    }

    /// @dev A committed day of hourly meter data. `reportHash` is the hash of the AI verification
    ///      report; `heldHours` counts hours the verifier withheld from this root.
    struct Batch {
        uint8 kind;
        uint32 subject;
        uint32 day;
        address issuer;
        bytes32 reportHash;
        uint64 totalWh;
        uint16 heldHours;
    }

    struct HourSlot {
        uint64 loadWh;
        uint64 matchedWh;
        bool proven;
    }

    mapping(address => bool) public isIssuer;
    mapping(uint32 => Device) public devices;
    mapping(uint32 => Meter) public meters;
    mapping(bytes32 => Batch) public batches;
    mapping(uint256 => bool) public minted;
    mapping(uint32 => mapping(uint32 => bytes32)) public consumptionRoot;
    mapping(uint32 => mapping(uint32 => uint64)) public dayLoadWh;
    mapping(uint32 => mapping(uint32 => uint64)) public dayMatchedWh;
    mapping(uint32 => mapping(uint64 => HourSlot)) public slots;

    event IssuerSet(address indexed issuer, bool allowed);
    event DeviceRegistered(uint32 indexed deviceId, address indexed owner, bytes32 zone, uint64 capacityW, uint8 technology);
    event MeterRegistered(uint32 indexed meterId, address indexed owner, bytes32 zone);
    event BatchCommitted(
        bytes32 indexed root, uint8 kind, uint32 indexed subject, uint32 day, bytes32 reportHash, uint64 totalWh, uint16 heldHours
    );
    event CertificateMinted(uint256 indexed id, uint32 indexed deviceId, uint64 hour, uint64 wh, address owner);
    event LoadProven(uint32 indexed meterId, uint64 hour, uint64 wh);
    event Retired(uint32 indexed meterId, uint64 indexed hour, uint256 indexed id, address beneficiary, uint256 wh);

    error NotIssuer();
    error UnknownSubject();
    error BadBatch();
    error AlreadyCommitted();
    error WrongDay();
    error OverCapacity();
    error BadProof();
    error AlreadyMinted();
    error NotMeterOwner();
    error LoadNotProven();
    error HourMismatch();
    error ZoneMismatch();
    error ExceedsLoad();
    error LengthMismatch();

    constructor(address admin) ERC1155("") Ownable(admin) {}

    // ---------------------------------------------------------------- governance

    function setIssuer(address issuer, bool allowed) external onlyOwner {
        isIssuer[issuer] = allowed;
        emit IssuerSet(issuer, allowed);
    }

    function registerDevice(uint32 deviceId, address owner_, bytes32 zone, uint64 capacityW, uint8 technology)
        external
        onlyOwner
    {
        if (devices[deviceId].active) revert AlreadyCommitted();
        devices[deviceId] = Device(owner_, zone, capacityW, technology, true);
        emit DeviceRegistered(deviceId, owner_, zone, capacityW, technology);
    }

    function registerMeter(uint32 meterId, address owner_, bytes32 zone) external onlyOwner {
        if (meters[meterId].active) revert AlreadyCommitted();
        meters[meterId] = Meter(owner_, zone, true);
        emit MeterRegistered(meterId, owner_, zone);
    }

    // ---------------------------------------------------------------- issuance

    /// @notice Commit one day of verified hourly generation for a device. Several roots may cover
    ///         the same day (for example, hours released after review); each hour still mints once.
    function commitGeneration(uint32 deviceId, uint32 day, bytes32 root, bytes32 reportHash, uint64 totalWh, uint16 heldHours)
        external
    {
        if (!isIssuer[msg.sender]) revert NotIssuer();
        if (!devices[deviceId].active) revert UnknownSubject();
        if (root == bytes32(0) || batches[root].kind != 0) revert BadBatch();
        batches[root] = Batch(KIND_GENERATION, deviceId, day, msg.sender, reportHash, totalWh, heldHours);
        emit BatchCommitted(root, KIND_GENERATION, deviceId, day, reportHash, totalWh, heldHours);
    }

    /// @notice Commit one day of hourly consumption for a meter. Exactly one root per meter-day, and
    ///         its total enters the meter's denominator at once, so unproven hours still count as load.
    function commitConsumption(uint32 meterId, uint32 day, bytes32 root, bytes32 reportHash, uint64 totalWh) external {
        if (!isIssuer[msg.sender]) revert NotIssuer();
        if (!meters[meterId].active) revert UnknownSubject();
        if (root == bytes32(0) || batches[root].kind != 0) revert BadBatch();
        if (consumptionRoot[meterId][day] != bytes32(0)) revert AlreadyCommitted();
        consumptionRoot[meterId][day] = root;
        dayLoadWh[meterId][day] = totalWh;
        batches[root] = Batch(KIND_CONSUMPTION, meterId, day, msg.sender, reportHash, totalWh, 0);
        emit BatchCommitted(root, KIND_CONSUMPTION, meterId, day, reportHash, totalWh, 0);
    }

    /// @notice Mint the certificate for one generator-hour from a committed root. Anyone may pay the
    ///         gas; the tokens always go to the device owner.
    function mint(uint32 deviceId, uint64 hour, uint64 wh, bytes32 root, bytes32[] calldata proof)
        public
        returns (uint256 id)
    {
        Batch storage b = batches[root];
        if (b.kind != KIND_GENERATION || b.subject != deviceId) revert BadBatch();
        if (hour / 24 != b.day) revert WrongDay();
        Device storage d = devices[deviceId];
        if (wh > d.capacityW) revert OverCapacity();
        if (!MerkleProof.verifyCalldata(proof, root, leafHash(KIND_GENERATION, deviceId, hour, wh))) revert BadProof();
        id = certificateId(deviceId, hour);
        if (minted[id]) revert AlreadyMinted();
        minted[id] = true;
        _mint(d.owner, id, wh, "");
        emit CertificateMinted(id, deviceId, hour, wh, d.owner);
    }

    function mintMany(
        uint32 deviceId,
        uint64[] calldata hours_,
        uint64[] calldata whs,
        bytes32 root,
        bytes32[][] calldata proofs
    ) external {
        if (hours_.length != whs.length || hours_.length != proofs.length) revert LengthMismatch();
        for (uint256 i = 0; i < hours_.length; i++) {
            mint(deviceId, hours_[i], whs[i], root, proofs[i]);
        }
    }

    // ---------------------------------------------------------------- claims

    function proveLoad(uint32 meterId, uint64 hour, uint64 wh, bytes32[] calldata proof) public {
        bytes32 root = consumptionRoot[meterId][uint32(hour / 24)];
        if (root == bytes32(0)) revert BadBatch();
        if (!MerkleProof.verifyCalldata(proof, root, leafHash(KIND_CONSUMPTION, meterId, hour, wh))) revert BadProof();
        HourSlot storage s = slots[meterId][hour];
        if (s.proven) return;
        s.loadWh = wh;
        s.proven = true;
        emit LoadProven(meterId, hour, wh);
    }

    /// @notice Burn certificates against the meter's load in the same hour. Every certificate must
    ///         come from that hour and from a generator in the meter's grid zone, and the hour's total
    ///         can never exceed the metered load. This is the rule annual certificates do not enforce.
    function retire(uint32 meterId, uint64 hour, uint256[] calldata ids, uint256[] calldata amounts) public {
        if (ids.length != amounts.length) revert LengthMismatch();
        Meter storage m = meters[meterId];
        if (msg.sender != m.owner) revert NotMeterOwner();
        HourSlot storage s = slots[meterId][hour];
        if (!s.proven) revert LoadNotProven();
        uint256 total;
        for (uint256 i = 0; i < ids.length; i++) {
            (uint32 deviceId, uint64 certHour) = decodeId(ids[i]);
            if (certHour != hour) revert HourMismatch();
            if (devices[deviceId].zone != m.zone) revert ZoneMismatch();
            total += amounts[i];
            emit Retired(meterId, hour, ids[i], msg.sender, amounts[i]);
        }
        if (s.matchedWh + total > s.loadWh) revert ExceedsLoad();
        s.matchedWh += uint64(total);
        dayMatchedWh[meterId][uint32(hour / 24)] += uint64(total);
        _burnBatch(msg.sender, ids, amounts);
    }

    function proveAndRetire(
        uint32 meterId,
        uint64 hour,
        uint64 loadWh,
        bytes32[] calldata proof,
        uint256[] calldata ids,
        uint256[] calldata amounts
    ) external {
        proveLoad(meterId, hour, loadWh, proof);
        retire(meterId, hour, ids, amounts);
    }

    /// @notice Hourly carbon-free energy score over committed days, in basis points.
    function claimSummary(uint32 meterId, uint32 fromDay, uint32 toDay)
        external
        view
        returns (uint256 loadWh, uint256 matchedWh, uint256 cfeBps)
    {
        for (uint32 d = fromDay; d <= toDay; d++) {
            loadWh += dayLoadWh[meterId][d];
            matchedWh += dayMatchedWh[meterId][d];
        }
        cfeBps = loadWh == 0 ? 0 : (matchedWh * 10_000) / loadWh;
    }

    // ---------------------------------------------------------------- encoding

    function leafHash(uint8 kind, uint32 subject, uint64 hour, uint64 wh) public pure returns (bytes32) {
        return keccak256(bytes.concat(keccak256(abi.encode(kind, subject, hour, wh))));
    }

    function certificateId(uint32 deviceId, uint64 hour) public pure returns (uint256) {
        return (uint256(deviceId) << 64) | uint256(hour);
    }

    function decodeId(uint256 id) public pure returns (uint32 deviceId, uint64 hour) {
        deviceId = uint32(id >> 64);
        hour = uint64(id);
    }
}
