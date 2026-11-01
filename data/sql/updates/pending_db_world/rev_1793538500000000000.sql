-- #341/#205: summon Crowley's Horse when Sacrifices (14212) is accepted.
-- Data-only replacement for the temporary static test spawn.
DELETE FROM `smart_scripts` WHERE `entryorguid`=35552 AND `source_type`=0;
INSERT INTO `smart_scripts`
(`entryorguid`,`source_type`,`id`,`link`,`event_type`,`event_phase_mask`,`event_chance`,`event_flags`,
 `event_param1`,`event_param2`,`event_param3`,`event_param4`,
 `action_type`,`action_param1`,`action_param2`,`action_param3`,`action_param4`,`action_param5`,`action_param6`,
 `target_type`,`target_param1`,`target_param2`,`target_param3`,`target_x`,`target_y`,`target_z`,`target_o`,`comment`)
VALUES
(35552,0,0,0,19,0,100,0,14212,0,0,0,12,35231,1,600000,0,0,0,1,0,0,0,0,0,0,0,'On quest 14212 accepted - summon Crowley''s Horse');

UPDATE `creature_template` SET `AIName`='SmartAI' WHERE `entry`=35552;
