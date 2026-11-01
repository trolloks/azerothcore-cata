-- #205: restore native Gilneas spell destinations, target conditions and automatic phase auras.
-- Source: TDB 434.22011 at TrinityCore c699217775d90794158422387b07a917e161b582.
-- Two Forms / Darkflight: Shauren a3d07b5e6ac45c23ec4116028c4bccae9307083b.

DELETE FROM `spell_target_position` WHERE `ID` IN (66836,66925,68630,68639,72788,72799,76678,82892,94260,95679,101644);
INSERT INTO `spell_target_position` (`ID`,`EffectIndex`,`MapID`,`PositionX`,`PositionY`,`PositionZ`,`Orientation`,
`VerifiedBuild`) VALUES
(66836,0,654,-1620.98,1509.19,67.1041,3.91921,0),
(66925,0,654,-1636.29,1481.84,70.948,4.08123,0),
(68630,0,654,-1818.4,2294.25,42.2135,3.24666,0),
(68639,2,654,-1817.41,2294.35,42.1927,3.23881,0),
(72788,0,654,-1818.4,2294.25,42.2135,3.24666,0),
(72799,1,654,-1818.4,2294.25,42.2135,3.24666,0),
(76678,0,654,-1578.49,1628.04,20.6102,1.12382,0),
(82892,0,654,-1587.95,2548,130.218,0.58027,0),
(94260,1,654,-1724.18,1872.09,17.7994,3.22886,0),
(95679,0,654,-2225.7,1827.34,13.194,3.31613,0),
(101644,0,654,-1550.37,1573.4,20.4521,0.58027,0);

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=13 AND `SourceEntry` IN (30221,43333,67063,67350);
DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=13 AND `SourceEntry` IN (67805,68070,68087,68228);
DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=13 AND `SourceEntry` IN (68235,68558,68591,68638);
DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=13 AND `SourceEntry` IN (68682,69123,80281,93275);
INSERT INTO `conditions` (`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,
`ConditionTypeOrReference`,`ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,`NegativeCondition`,
`ErrorType`,`ErrorTextId`,`ScriptName`,`Comment`) VALUES
(13,1,30221,0,0,31,0,3,620,0,0,0,0,'','Shoot Targets Chicken'),
(13,1,43333,0,0,31,0,3,24042,0,0,0,0,'','Spell 43333 can target Generic Trigger LAB - OLD'),
(13,1,67350,0,0,31,0,3,35374,0,0,0,0,'','Summon Josiah - Target Lab Trigger'),
(13,1,67805,0,0,31,0,3,35463,0,0,0,0,'','Attack Lurker - Target Bloodfang Lurker'),
(13,1,68087,0,0,31,0,3,35374,0,0,0,0,'','Just Citizen'),
(13,1,68228,0,0,31,0,3,35753,0,0,0,0,'','Rescue Krennan - Target Krennan'),
(13,1,68235,0,0,31,0,3,35916,0,0,0,0,'','Cannon Fire - Target Bloodfang Ripper'),
(13,1,68558,0,0,31,0,3,36140,0,0,0,0,'','Gilneas - Quest - Abomination Kill Me - Target Prince Liam Greymane'),
(13,1,68591,0,0,31,0,3,36286,0,0,0,0,'','Fiery Boulder - Target Generic Trigger LAB - Multiphase'),
(13,1,68682,0,0,31,0,3,36312,0,0,0,0,'','Call Attack Mastiff - Target Dark Ranger Thyala'),
(13,1,69123,0,0,31,0,3,36198,0,0,0,0,'','Curse of the Worgen - Target Generic Trigger LAB (Multiphase - Ground)'),
(13,1,80281,0,0,31,0,3,35374,0,0,0,0,'','Summon Citizen and Worgen'),
(13,1,93275,0,0,31,0,3,50260,0,0,0,0,'','Ping Gilnean Crow - Target Gilnean Crow'),
(13,2,68638,0,0,31,0,3,36331,0,0,0,0,'','Worgen Intro Completion - Target Krennan Aranas'),
(13,2,68638,0,1,31,0,3,36330,0,0,0,0,'','Worgen Intro Completion - Target Lord Godfrey'),
(13,2,68638,0,2,31,0,3,36332,0,0,0,0,'','Worgen Intro Completion - Target King Genn Greymane'),
(13,3,68070,0,0,31,0,3,35374,0,0,0,0,'','Summon Citizen and Worgen'),
(13,4,67350,0,0,31,0,3,35374,0,0,0,0,'','Summon Josiah - Target Lab Trigger'),
(13,7,67063,0,0,31,0,3,35229,0,0,0,0,'','Throw Torch - Target Bloodfang Stalker');

UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=49416 AND `area`=4756 AND `quest_start`=14078 AND `quest_end`=14099
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=41;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=49416 AND `area`=4757 AND `quest_start`=14159 AND `quest_end`=14221
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=59073 AND `area`=4714 AND `quest_start`=14099 AND `quest_end`=14159
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=66 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=59073 AND `area`=4755 AND `quest_start`=14099 AND `quest_end`=14159
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=66 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=59073 AND `area`=4756 AND `quest_start`=14078 AND `quest_end`=14159
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=59073 AND `area`=4757 AND `quest_start`=14099 AND `quest_end`=14159
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=66 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=68481 AND `area`=4714 AND `quest_start`=14375 AND `quest_end`=14321
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=68482 AND `area`=4714 AND `quest_start`=14321 AND `quest_end`=14386
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=68483 AND `area`=4714 AND `quest_start`=14386 AND `quest_end`=14466
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=72870 AND `area`=4757 AND `quest_start`=14159 AND `quest_end`=14293
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=72870 AND `area`=4758 AND `quest_start`=14159 AND `quest_end`=14293
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=72872 AND `area`=4755 AND `quest_start`=14293 AND `quest_end`=14221
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=72872 AND `area`=4757 AND `quest_start`=14293 AND `quest_end`=14221
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=72872 AND `area`=4758 AND `quest_start`=14293 AND `quest_end`=14221
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=72872 AND `area`=4759 AND `quest_start`=14293 AND `quest_end`=14221
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=72872 AND `area`=4761 AND `quest_start`=14293 AND `quest_end`=14221
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=76642 AND `area`=4757 AND `quest_start`=14221 AND `quest_end`=14222
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=76642 AND `area`=4758 AND `quest_start`=14221 AND `quest_end`=14222
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=76642 AND `area`=4759 AND `quest_start`=14221 AND `quest_end`=14222
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=76642 AND `area`=4761 AND `quest_start`=14221 AND `quest_end`=14222
AND `aura_spell`=0 AND `racemask`=0 AND `gender`=2 AND `quest_start_status`=64 AND `quest_end_status`=43;

-- The direct phase-181 login grant must end with the native aura window.
DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=26 AND `SourceGroup`=181 AND `SourceEntry`=4714;
INSERT INTO `conditions` (`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,
`ConditionTypeOrReference`,`ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,`NegativeCondition`,
`Comment`) VALUES
(26,181,4714,0,0,8,0,14375,0,0,0,'Phase 181 after quest 14375 is rewarded'),
(26,181,4714,0,0,8,0,14321,0,0,1,'Phase 181 until Invasion is rewarded');

-- Replace scratch-only bindings; Toss Keg uses the existing abomination/Liam chain.
DELETE FROM `spell_script_names` WHERE `ScriptName`='spell_worgen_two_forms';
DELETE FROM `spell_script_names` WHERE `ScriptName`='spell_black_gunpowder_keg';
DELETE FROM `spell_script_names` WHERE `ScriptName`='spell_gen_two_forms';
DELETE FROM `spell_script_names` WHERE `ScriptName`='spell_gen_darkflight';
DELETE FROM `spell_script_names` WHERE `ScriptName`='spell_gen_enable_worgen_altered_form';
INSERT INTO `spell_script_names` (`spell_id`,`ScriptName`) VALUES
(68996,'spell_gen_two_forms'),
(68992,'spell_gen_darkflight');

-- Remove the earlier scratch link that applied the crit passive as a form.
DELETE FROM `spell_linked_spell` WHERE `spell_trigger`=68996 AND `spell_effect`=68975;
