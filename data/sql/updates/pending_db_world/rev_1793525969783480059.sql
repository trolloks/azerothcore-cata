-- #385: Bloodfang Worgen must credit the shared Spell Practice Credit objective for Charge (100).
-- The training-dummy AI now handles this generically for every race; these
-- SmartAI targets are the exception because they do not use npc_training_dummy.
DELETE FROM `smart_scripts` WHERE `source_type`=0 AND `entryorguid` IN (35118,-10376661,-10376657);
INSERT INTO `smart_scripts`
(`entryorguid`,`source_type`,`id`,`link`,`event_type`,`event_phase_mask`,`event_chance`,`event_flags`,`event_param1`,`event_param2`,`event_param3`,`event_param4`,`event_param5`,`event_param6`,`action_type`,`action_param1`,`action_param2`,`action_param3`,`action_param4`,`action_param5`,`action_param6`,`target_type`,`target_param1`,`target_param2`,`target_param3`,`target_param4`,`target_x`,`target_y`,`target_z`,`target_o`,`comment`) VALUES
(-10376661,0,0,0,2,0,100,1,0,30,0,0,0,0,11,8599,0,0,0,0,0,1,0,0,0,0,0,0,0,0,'When health between 0%-30%% (check once) - Self: Cast spell Enrage (8599) on Self // Bloodfang Worgen - Between 0-30% Health - Cast ''Enrage'' (No Repeat)'),
(-10376661,0,1,0,63,0,100,0,0,0,0,0,0,0,49,0,0,0,0,0,0,10,10376501,0,0,0,0,0,0,0,'When just created - Self: Attack Creature with guid 376501'),
(-10376661,0,2,0,8,0,100,0,5143,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Arcane Missiles (5143) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(-10376661,0,3,0,8,0,100,0,2098,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Eviscerate (2098) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(-10376661,0,4,0,8,0,100,0,56641,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Steady Shot (56641) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(-10376661,0,5,0,8,0,100,0,348,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Immolate (348) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(-10376661,0,6,0,8,0,100,0,100,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'Bloodfang Worgen - Charge practice credit'),
(-10376657,0,0,0,2,0,100,1,0,30,0,0,0,0,11,8599,0,0,0,0,0,1,0,0,0,0,0,0,0,0,'When health between 0%-30%% (check once) - Self: Cast spell Enrage (8599) on Self // Bloodfang Worgen - Between 0-30% Health - Cast ''Enrage'' (No Repeat)'),
(-10376657,0,1,0,63,0,100,0,0,0,0,0,0,0,49,0,0,0,0,0,0,10,10376501,0,0,0,0,0,0,0,'When just created - Self: Attack Creature with guid 376501'),
(-10376657,0,2,0,8,0,100,0,5143,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Arcane Missiles (5143) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(-10376657,0,3,0,8,0,100,0,2098,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Eviscerate (2098) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(-10376657,0,4,0,8,0,100,0,56641,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Steady Shot (56641) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(-10376657,0,5,0,8,0,100,0,348,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Immolate (348) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(-10376657,0,6,0,8,0,100,0,100,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'Bloodfang Worgen - Charge practice credit'),
(35118,0,0,0,2,0,100,1,0,30,0,0,0,0,11,8599,0,0,0,0,0,1,0,0,0,0,0,0,0,0,'When health between 0%-30%% (check once) - Self: Cast spell Enrage (8599) on Self // Bloodfang Worgen - Between 0-30% Health - Cast ''Enrage'' (No Repeat)'),
(35118,0,1,0,8,0,100,0,5143,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Arcane Missiles (5143) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(35118,0,2,0,8,0,100,0,2098,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Eviscerate (2098) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(35118,0,3,0,8,0,100,0,56641,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Steady Shot (56641) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(35118,0,4,0,8,0,100,0,348,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'On spell Immolate (348) hit - Caster: Give kill credit Spell Practice Credit (44175)'),
(35118,0,5,0,8,0,100,0,100,0,0,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'Bloodfang Worgen - Charge practice credit');
