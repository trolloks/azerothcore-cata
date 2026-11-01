-- #395: grant Gilneas phase 172 for the post-Josiah city state without gating 171.
DELETE FROM `phase_area` WHERE `PhaseGroup`=367 AND `AreaId` IN (4757,4758);
DELETE FROM `phase_area` WHERE `PhaseId`=172 AND `AreaId` IN (4757,4758);
INSERT INTO `phase_area` (`AreaId`,`PhaseId`,`PhaseGroup`) VALUES
(4757,172,0),
(4758,172,0);

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup`=367 AND `SourceEntry`=0;
DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup`=172 AND `SourceEntry`=0;
INSERT INTO `conditions`
(`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,
 `ConditionTypeOrReference`,`ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,
 `NegativeCondition`,`ErrorType`,`ErrorTextId`,`ScriptName`,`Comment`)
VALUES
(26,172,0,0,0,47,0,14293,66,0,0,0,0,'','Phase 172 after quest 14293 is complete or rewarded');
