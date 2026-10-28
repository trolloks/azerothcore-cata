-- Echo Isles class practice quests: the Tiki Target credits the "Spell Practice Credit"
-- objective when hit by the spell the class trainer teaches (same pattern as the Gilneas dummy).
UPDATE `creature_template` SET `AIName` = 'SmartAI' WHERE `entry` = 38038;

DELETE FROM `smart_scripts` WHERE `entryorguid` = 38038 AND `source_type` = 0;
INSERT INTO `smart_scripts`
(`entryorguid`,`source_type`,`id`,`link`,`event_type`,`event_phase_mask`,`event_chance`,`event_flags`,
 `event_param1`,`event_param2`,`event_param3`,`event_param4`,`event_param5`,`event_param6`,
 `action_type`,`action_param1`,`action_param2`,`action_param3`,`action_param4`,`action_param5`,
 `action_param6`,`target_type`,`target_param1`,`target_param2`,`target_param3`,`target_param4`,
 `target_x`,`target_y`,`target_z`,`target_o`,`comment`)
VALUES
(38038,0,0,0,8,0,100,0,
 100,0,0,0,0,0,
 33,44175,0,0,0,0,
 0,7,0,0,0,0,
 0,0,0,0,
 'Tiki Target - Charge practice credit'),
(38038,0,1,0,8,0,100,0,
 5143,0,0,0,0,0,
 33,44175,0,0,0,0,
 0,7,0,0,0,0,
 0,0,0,0,
 'Tiki Target - Arcane Missiles practice credit'),
(38038,0,2,0,8,0,100,0,
 73899,0,0,0,0,0,
 33,44175,0,0,0,0,
 0,7,0,0,0,0,
 0,0,0,0,
 'Tiki Target - Primal Strike practice credit'),
(38038,0,3,0,8,0,100,0,
 2098,0,0,0,0,0,
 33,44175,0,0,0,0,
 0,7,0,0,0,0,
 0,0,0,0,
 'Tiki Target - Eviscerate practice credit'),
(38038,0,4,0,8,0,100,0,
 56641,0,0,0,0,0,
 33,44175,0,0,0,0,
 0,7,0,0,0,0,
 0,0,0,0,
 'Tiki Target - Steady Shot practice credit'),
(38038,0,5,0,8,0,100,0,
 348,0,0,0,0,0,
 33,44175,0,0,0,0,
 0,7,0,0,0,0,
 0,0,0,0,
 'Tiki Target - Immolate practice credit');
