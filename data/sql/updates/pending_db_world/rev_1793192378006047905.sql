-- Map the TDB 434 script names of the aliased encounters onto the fork's registered classes and
-- restore the hover heights those scripts drive (#298). The clean-world builder applies the same
-- aliases before stripping unknown names; these five base entries were stripped in earlier
-- builds. Difficulty rows keep an empty ScriptName: ObjectMgr rejects a filled difficulty
-- ScriptName and always uses the base entry's binding.
UPDATE `creature_template` SET `ScriptName`='boss_taldaram' WHERE `entry`=29308;
UPDATE `creature_template` SET `ScriptName`='boss_sartharion_vesperon' WHERE `entry`=30449;
UPDATE `creature_template` SET `ScriptName`='boss_sartharion_shadron' WHERE `entry`=30451;
UPDATE `creature_template` SET `ScriptName`='boss_sartharion_tenebron' WHERE `entry`=30452;
UPDATE `creature_template` SET `ScriptName`='npc_auriaya_feral_defender' WHERE `entry`=34035;
UPDATE `creature_template` SET `HoverHeight`=1 WHERE `entry` IN (29308,30449,30451,30452,34035,31469,31535,31520,31534,34171);
