-- #205: complete Last Stand (14222) with the player-side reward spells.
-- Ported from the TrinityCore 4.3.4 Gilneas updatepack. TC action 85 was
-- invoker-cast; AzerothCore uses SMART_ACTION_INVOKER_CAST (134).
DELETE FROM `smart_scripts` WHERE `entryorguid`=35566 AND `source_type`=0;
INSERT INTO `smart_scripts`
(`entryorguid`,`source_type`,`id`,`link`,`event_type`,`event_phase_mask`,`event_chance`,`event_flags`,
 `event_param1`,`event_param2`,`event_param3`,`event_param4`,
 `action_type`,`action_param1`,`action_param2`,`action_param3`,`action_param4`,`action_param5`,`action_param6`,
 `target_type`,`target_param1`,`target_param2`,`target_param3`,`target_x`,`target_y`,`target_z`,`target_o`,`comment`)
VALUES
(35566,0,0,1,20,0,100,0,14222,0,0,0,134,98274,2,0,0,0,0,7,0,0,0,0,0,0,0,'On quest 14222 rewarded - cast Force Worgen Altered Form on player'),
(35566,0,1,2,61,0,100,0,0,0,0,0,134,68630,2,0,0,0,0,7,0,0,0,0,0,0,0,'Linked - cast Curse of the Worgen on player'),
(35566,0,2,0,61,0,100,0,0,0,0,0,134,72788,2,0,0,0,0,7,0,0,0,0,0,0,0,'Linked - cast Last Stand Complete on player');

UPDATE `creature_template` SET `AIName`='SmartAI' WHERE `entry`=35566;
