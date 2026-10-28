-- Proving Pit (Durotar): the Jailor's gossip select is handled by npc_darkspear_jailor.
-- Drop the duplicate option that had no action and shadowed the scripted one.
DELETE FROM `gossip_menu_option` WHERE `MenuID` = 10974 AND `OptionID` = 0;
UPDATE `creature_template` SET `ScriptName` = 'npc_darkspear_jailor' WHERE `entry` = 39062;

-- Restore the captive scout's captivity when its encounter resets.
UPDATE `creature_template` SET `AIName` = 'SmartAI' WHERE `entry` = 38142;
DELETE FROM `smart_scripts` WHERE `entryorguid` = 38142 AND `source_type` = 0;
INSERT INTO `smart_scripts`
(`entryorguid`,`source_type`,`id`,`link`,`event_type`,`event_phase_mask`,`event_chance`,`event_flags`,
 `event_param1`,`event_param2`,`event_param3`,`event_param4`,`event_param5`,`event_param6`,
 `action_type`,`action_param1`,`action_param2`,`action_param3`,`action_param4`,`action_param5`,`action_param6`,
 `target_type`,`target_param1`,`target_param2`,`target_param3`,`target_param4`,
 `target_x`,`target_y`,`target_z`,`target_o`,`comment`)
VALUES
(38142,0,0,0,25,0,100,0, 0,0,0,0,0,0, 18,256,0,0,0,0,0, 1,0,0,0,0, 0,0,0,0, 'Captive Spitescale Scout - restore immunity to players on reset'),
(38142,0,1,0,7,0,100,0,  0,0,0,0,0,0, 18,256,0,0,0,0,0, 1,0,0,0,0, 0,0,0,0, 'Captive Spitescale Scout - restore immunity to players on evade');
