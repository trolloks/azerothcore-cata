-- #205: restore the Gilneas chapter 1/2 script bindings stripped from the clean world.
UPDATE `creature_template` SET `ScriptName`='npc_frightened_citizen'
WHERE `entry` IN (34981,35836) AND `ScriptName`='';
UPDATE `creature_template` SET `ScriptName`='npc_worgen_runt'
WHERE `entry` IN (35188,35456) AND `ScriptName`='';
UPDATE `creature_template` SET `ScriptName`='npc_josiah_avery'
WHERE `entry`=35370 AND `ScriptName`='';
UPDATE `creature_template` SET `ScriptName`='npc_greymanes_horse'
WHERE `entry`=35905 AND `ScriptName`='';
UPDATE `creature_template` SET `ScriptName`='npc_crowleys_horse'
WHERE `entry` IN (35231,44428) AND `ScriptName`='';
UPDATE `creature_template` SET `ScriptName`='npc_gilnean_crow'
WHERE `entry`=50260 AND `ScriptName`='';
UPDATE `creature_template` SET `ScriptName`='npc_gilneas_horrid_abomination'
WHERE `entry`=36231 AND `ScriptName`='';
UPDATE `creature_template` SET `ScriptName`='npc_gilneas_save_the_children'
WHERE `entry` IN (36287,36288,36289) AND `ScriptName`='';
UPDATE `creature_template` SET `ScriptName`='npc_gilneas_forsaken_catapult'
WHERE `entry`=36283 AND `ScriptName`='';
UPDATE `gameobject_template` SET `ScriptName`='go_gilneas_invasion_camera'
WHERE `entry`=197337 AND `ScriptName`='';

-- #205: spell and area-trigger hooks for the Gilneas chapter 2 scripts.
DELETE FROM `spell_script_names` WHERE `ScriptName` IN ('spell_gilneas_quest_save_the_children','spell_gilneas_launch','spell_gilneas_fiery_boulder','spell_gilneas_call_attack_mastiff','spell_gilneas_forcecast_cataclysm_1','spell_gilneas_worgen_intro_completion','spell_gilneas_save_drowning_milita_effect','spell_gilneas_drowning_vehicle_exit_dummy');
INSERT INTO `spell_script_names` (`spell_id`,`ScriptName`) VALUES
(68596,'spell_gilneas_quest_save_the_children'),
(68597,'spell_gilneas_quest_save_the_children'),
(68598,'spell_gilneas_quest_save_the_children'),
(68659,'spell_gilneas_launch'),
(68591,'spell_gilneas_fiery_boulder'),
(68682,'spell_gilneas_call_attack_mastiff'),
(69027,'spell_gilneas_forcecast_cataclysm_1'),
(68638,'spell_gilneas_worgen_intro_completion'),
(68737,'spell_gilneas_save_drowning_milita_effect'),
(68741,'spell_gilneas_drowning_vehicle_exit_dummy');

DELETE FROM `areatrigger_scripts` WHERE `entry` IN (5542,5543,5544,5545,5546,5547,5548,5549,5550,5551,5552,5553,5554,5567,5568,5574,5575,5576,5577);
INSERT INTO `areatrigger_scripts` (`entry`,`ScriptName`) VALUES
(5542,'at_gasping_for_breath'),(5543,'at_gasping_for_breath'),(5544,'at_gasping_for_breath'),
(5545,'at_gasping_for_breath'),(5546,'at_gasping_for_breath'),(5547,'at_gasping_for_breath'),
(5548,'at_gasping_for_breath'),(5549,'at_gasping_for_breath'),(5550,'at_gasping_for_breath'),
(5551,'at_gasping_for_breath'),(5552,'at_gasping_for_breath'),(5553,'at_gasping_for_breath'),
(5554,'at_gasping_for_breath'),(5567,'at_gasping_for_breath'),(5568,'at_gasping_for_breath'),
(5574,'at_gasping_for_breath'),(5575,'at_gasping_for_breath'),(5576,'at_gasping_for_breath'),
(5577,'at_gasping_for_breath');

UPDATE `creature_text` SET `BroadcastTextId`=36418 WHERE `CreatureID`=36440 AND `GroupID`=0 AND `ID`=0;
UPDATE `creature_text` SET `GroupID`=0,`ID`=1,`BroadcastTextId`=36417 WHERE `CreatureID`=36440 AND `GroupID`=1 AND `ID`=0;
UPDATE `creature_text` SET `GroupID`=0,`ID`=3,`BroadcastTextId`=36419 WHERE `CreatureID`=36440 AND `GroupID`=2 AND `ID`=0;

DELETE FROM `npc_spellclick_spells` WHERE `npc_entry`=36440;
INSERT INTO `npc_spellclick_spells` (`npc_entry`,`spell_id`,`cast_flags`,`user_type`) VALUES
(36440,68735,1,0);

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=18 AND `SourceGroup`=36440;
INSERT INTO `conditions`
(`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,`ConditionTypeOrReference`,
 `ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,`NegativeCondition`,`Comment`) VALUES
(18,36440,68735,0,0,9,0,14395,0,0,0,'Clicker has quest Gasping for Breath (14395) active'),
(18,36440,68735,0,0,28,0,14395,0,0,1,'Clicker has not completed quest Gasping for Breath (14395)');

UPDATE `creature_template` SET `AIName`='SmartAI',`ScriptName`='',`npcflag`=`npcflag`|16777216 WHERE `entry`=36440;
DELETE FROM `smart_scripts` WHERE `source_type`=0 AND `entryOrGuid`=36440;
INSERT INTO `smart_scripts`
(`entryorguid`,`source_type`,`id`,`link`,`event_type`,`event_phase_mask`,`event_chance`,`event_flags`,
 `event_param1`,`event_param2`,`event_param3`,`event_param4`,`action_type`,`action_param1`,`action_param2`,
 `action_param3`,`action_param4`,`action_param5`,`action_param6`,`target_type`,`target_param1`,
 `target_param2`,`target_param3`,`target_x`,`target_y`,`target_z`,`target_o`,`comment`) VALUES
(36440,0,0,1,73,0,100,0,0,0,0,0,11,47020,0,0,0,0,0,7,0,0,0,0,0,0,0,'On spellclick - Self: Cast Ride Vehicle (47020) on Clicker'),
(36440,0,1,2,61,0,100,0,0,0,0,0,83,16777216,0,0,0,0,0,1,0,0,0,0,0,0,0,'On spellclick - Self: Remove NPC flag SPELLCLICK'),
(36440,0,2,0,61,0,100,0,0,0,0,0,28,68730,0,0,0,0,0,1,0,0,0,0,0,0,0,'On spellclick - Self: Remove Drowning aura'),
(36440,0,3,4,8,0,100,1,68739,0,0,0,41,10000,0,0,0,0,0,1,0,0,0,0,0,0,0,'On Drowning Militia Dummy hit - Self: Despawn in 10 s'),
(36440,0,4,5,61,0,100,0,0,0,0,0,67,1,1500,1500,0,0,0,0,0,0,0,0,0,0,0,'On Drowning Militia Dummy hit - Trigger timed event 1'),
(36440,0,5,0,61,0,100,0,0,0,0,0,67,2,3000,3000,0,0,0,0,0,0,0,0,0,0,0,'On Drowning Militia Dummy hit - Trigger timed event 2'),
(36440,0,6,0,59,0,30,0,1,0,0,0,90,8,0,0,0,0,0,1,0,0,0,0,0,0,0,'On timed event 1 - Self: Set stand state to KNEEL'),
(36440,0,7,0,59,0,100,0,2,0,0,0,1,0,0,0,0,0,0,1,0,0,0,0,0,0,0,'On timed event 2 - Self: Talk rescued text');

DELETE FROM `conditions` WHERE `SourceTypeOrReferenceId`=22 AND `SourceGroup`=1 AND `SourceEntry`=36440 AND `SourceId`=0;
INSERT INTO `conditions`
(`SourceTypeOrReferenceId`,`SourceGroup`,`SourceEntry`,`SourceId`,`ElseGroup`,`ConditionTypeOrReference`,
 `ConditionTarget`,`ConditionValue1`,`ConditionValue2`,`ConditionValue3`,`NegativeCondition`,`Comment`) VALUES
(22,1,36440,0,0,9,0,14395,0,0,0,'Drowning Watchman spellclick: quest active'),
(22,1,36440,0,0,28,0,14395,0,0,1,'Drowning Watchman spellclick: quest not rewarded');
