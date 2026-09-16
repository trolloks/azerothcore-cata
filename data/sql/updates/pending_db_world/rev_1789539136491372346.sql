-- Plan 24 / issue #82: phase-based visibility and interaction gating. `PhaseId` gates a
-- creature/gameobject spawn to a Cata Phase.dbc or PhaseXPhaseGroup.dbc id (see `phase_area`,
-- added in #81); 0 means the spawn is not phase-gated (always visible, matching all existing spawns).
ALTER TABLE `creature` ADD COLUMN `PhaseId` int unsigned NOT NULL DEFAULT '0' AFTER `phaseMask`;
ALTER TABLE `gameobject` ADD COLUMN `PhaseId` int unsigned NOT NULL DEFAULT '0' AFTER `phaseMask`;
