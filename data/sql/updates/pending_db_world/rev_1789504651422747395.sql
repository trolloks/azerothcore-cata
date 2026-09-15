-- Plan 24 / issue #81: phase data model. `phase_area` maps AreaTable.dbc ids to Phase.dbc or
-- PhaseXPhaseGroup.dbc ids (real, pinned build-15595 records); actual membership is additionally
-- gated by any `condition` row with sourcetype=26 (CONDITION_SOURCE_TYPE_PHASE), sourcegroupid=PhaseId.
-- Frozen scenario: area 12 (Elwynn Forest) -> phase 125 ("Test Phase", single-phase case);
-- area 1519 (Stormwind City) -> phase group 367 (members 171, 172; phase-group case).
CREATE TABLE IF NOT EXISTS `phase_area` (
    `AreaId`  INT UNSIGNED NOT NULL,
    `PhaseId` INT UNSIGNED NOT NULL,
    PRIMARY KEY (`AreaId`, `PhaseId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DELETE FROM `phase_area` WHERE (`AreaId` = 12 AND `PhaseId` = 125) OR (`AreaId` = 1519 AND `PhaseId` = 367);
INSERT INTO `phase_area` (`AreaId`, `PhaseId`) VALUES
(12, 125),
(1519, 367);
