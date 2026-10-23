-- Plan 24 / issue #81: phase data model. `phase_area` maps AreaTable.dbc ids to Phase.dbc or
-- PhaseXPhaseGroup.dbc ids (real, pinned build-15595 records); actual membership is additionally
-- gated by any `condition` row with sourcetype=26 (CONDITION_SOURCE_TYPE_PHASE), sourcegroupid=PhaseId.
-- The table ships empty. The Plan 24 acceptance scenario (area 12 -> phase 125 "Test Phase",
-- area 1519 -> phase group 367) is seeded into the isolated run schema by the client harness, so
-- live players are not phase-shifted in Elwynn/Stormwind (#170). Native rows arrive with the
-- starter-content/phasing phase of the 1-20 epic (#175/#180).
CREATE TABLE IF NOT EXISTS `phase_area` (
    `AreaId`  INT UNSIGNED NOT NULL,
    `PhaseId` INT UNSIGNED NOT NULL,
    PRIMARY KEY (`AreaId`, `PhaseId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- An earlier revision of this update inserted the acceptance rows; the update is reapplied when its
-- hash changes (Updates.Redundancy), and this drops them (#170).
DELETE FROM `phase_area` WHERE (`AreaId` = 12 AND `PhaseId` = 125) OR (`AreaId` = 1519 AND `PhaseId` = 367);
