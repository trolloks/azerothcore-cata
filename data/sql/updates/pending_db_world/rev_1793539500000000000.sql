-- #205: phase 172 must cover the cathedral approach and the cathedral itself.
-- Tobias Mistmantle (35618), the Northgate Rebels and the cathedral worgen are
-- phase 172 in areas 4759/4761; without these rows they are invisible there.
DELETE FROM `phase_area` WHERE `PhaseId`=172 AND `AreaId` IN (4757,4758,4759,4761);
INSERT INTO `phase_area` (`AreaId`,`PhaseId`,`PhaseGroup`) VALUES
(4757,172,0),
(4758,172,0),
(4759,172,0),
(4761,172,0);
