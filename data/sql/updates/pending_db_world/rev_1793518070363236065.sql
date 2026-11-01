-- #377 / #205: switch Gilneas City to phase 170 after Lockdown! (14078).
-- Adapted from the TrinityCore 4.3.4 phase fix discussion:
-- https://github.com/TrinityCore/TrinityCore/issues/11201
-- https://github.com/TrinityCore/TrinityCore/issues/25824 (credit @shamage)

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup` IN (169,170) AND `SourceEntry` IN (0,4756);
INSERT INTO `conditions`
(`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,`ConditionTypeOrReference`,
 `ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,`NegativeCondition`,`Comment`) VALUES
(26,169,4756,0,0,8,0,14078,0,0,1,'Phase 169 until quest 14078 is rewarded'),
(26,170,0,0,0,8,0,14078,0,0,0,'Phase 170 after quest 14078 is rewarded');

DELETE FROM `spell_area` WHERE `area`=4756 AND `spell` IN (59073,49416);
INSERT INTO `spell_area`
(`spell`,`area`,`quest_start`,`quest_end`,`aura_spell`,`racemask`,`gender`,`autocast`,`quest_start_status`,`quest_end_status`) VALUES
(59073,4756,14078,14159,0,0,2,1,64,43),
(49416,4756,14078,14099,0,0,2,1,64,41);
