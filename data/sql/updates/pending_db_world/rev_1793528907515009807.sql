-- #391: restore Gilneas City phase 170 on login, not only through the legacy
-- spell_area aura. The phase condition gates it on Lockdown! (14078).
DELETE FROM `phase_area` WHERE `PhaseId`=170 AND `AreaId` IN (4755,4756,4757);
INSERT INTO `phase_area` (`AreaId`,`PhaseId`,`PhaseGroup`) VALUES
(4755,170,0),
(4756,170,0),
(4757,170,0);

-- 171 is the post-Josiah wounded state; Lorna Crowley and quest 14204 live there.
DELETE FROM `phase_area` WHERE `PhaseId`=171 AND `AreaId` IN (4755,4756,4757);
INSERT INTO `phase_area` (`AreaId`,`PhaseId`,`PhaseGroup`) VALUES
(4755,171,0),
(4756,171,0),
(4757,171,0);

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup`=170 AND `SourceEntry`=0;
INSERT INTO `conditions`
(`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,
 `ConditionTypeOrReference`,`ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,
 `NegativeCondition`,`ErrorType`,`ErrorTextId`,`ScriptName`,`Comment`)
VALUES
(26,170,0,0,0,8,0,14078,0,0,0,0,0,'','Phase 170 after quest 14078 is rewarded'),
(26,170,0,0,0,47,0,14159,66,0,1,0,0,'','Phase 170 excluded after quest 14159 is complete or rewarded');

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup`=171 AND `SourceEntry`=0;
INSERT INTO `conditions`
(`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,
 `ConditionTypeOrReference`,`ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,
 `NegativeCondition`,`ErrorType`,`ErrorTextId`,`ScriptName`,`Comment`)
VALUES
(26,171,0,0,0,47,0,14159,66,0,0,0,0,'','Phase 171 after quest 14159 is complete or rewarded');
