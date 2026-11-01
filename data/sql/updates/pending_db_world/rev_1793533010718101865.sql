-- #395: keep phase 171 only until Time to Regroup (14294) is complete/rewarded.
DELETE FROM `phase_area` WHERE `PhaseGroup`=367 AND `AreaId` IN (4757,4758);
DELETE FROM `phase_area` WHERE `PhaseId`=172 AND `AreaId` IN (4757,4758);
INSERT INTO `phase_area` (`AreaId`,`PhaseId`,`PhaseGroup`) VALUES
(4757,172,0),
(4758,172,0);

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup`=171 AND `SourceEntry`=0;
DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup`=172 AND `SourceEntry`=0;
DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup`=367 AND `SourceEntry`=0;
INSERT INTO `conditions`
(`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,
 `ConditionTypeOrReference`,`ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,
 `NegativeCondition`,`ErrorType`,`ErrorTextId`,`ScriptName`,`Comment`)
VALUES
(26,171,0,0,0,47,0,14159,66,0,0,0,0,'','Phase 171 after quest 14159 is complete or rewarded'),
(26,171,0,0,0,47,0,14294,66,0,1,0,0,'','Phase 171 excluded after quest 14294 is complete or rewarded'),
(26,172,0,0,0,47,0,14293,66,0,0,0,0,'','Phase 172 after quest 14293 is complete or rewarded');
