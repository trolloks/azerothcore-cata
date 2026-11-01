-- #205: complete the Gilneas phase chain after the city retreat.
-- 179: Last Stand is active; the Frenzied Stalkers and Crowley use this phase.
-- 105: Duskhaven after Last Stand is rewarded.
-- 181: the later Gilneas world after quest 14375.
DELETE FROM `phase_area` WHERE (`PhaseId`=179 AND `AreaId` IN (4757,4758,4759,4761)) OR (`PhaseId` IN (105,181) AND `AreaId`=4714);
INSERT INTO `phase_area` (`AreaId`,`PhaseId`,`PhaseGroup`) VALUES
(4757,179,0),
(4758,179,0),
(4759,179,0),
(4761,179,0),
(4714,105,0),
(4714,181,0);

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup` IN (105,179,181) AND `SourceEntry` IN (0,4714);
INSERT INTO `conditions`
(`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,
 `ConditionTypeOrReference`,`ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,
 `NegativeCondition`,`ErrorType`,`ErrorTextId`,`ScriptName`,`Comment`)
VALUES
(26,179,0,0,0,9,0,14222,0,0,0,0,0,'','Phase 179 while Last Stand is active'),
(26,105,4714,0,0,8,0,14222,0,0,0,0,0,'','Phase 105 after Last Stand is rewarded'),
(26,181,4714,0,0,8,0,14375,0,0,0,0,0,'','Phase 181 after quest 14375 is rewarded');
