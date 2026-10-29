-- Restore the seven TDB 434 script aliases added for #298. The clean-world builder applies the same
-- map before stripping unknown names; these base entries were stripped in earlier builds. None of them
-- is a difficulty row, so the base binding is all the loader reads.
UPDATE `creature_template` SET `ScriptName`='npc_finklestein' WHERE `entry`=28205;
UPDATE `creature_template` SET `ScriptName`='npc_cos_chromie_start' WHERE `entry`=26527;
UPDATE `creature_template` SET `ScriptName`='npc_cos_chromie_middle' WHERE `entry`=27915;
UPDATE `creature_template` SET `ScriptName`='boss_sjonnir_iron_sludge' WHERE `entry`=28165;
UPDATE `creature_template` SET `ScriptName`='boss_sjonnir_malformed_ooze' WHERE `entry`=27981;
UPDATE `creature_template` SET `ScriptName`='npc_lk_spirit_bomb' WHERE `entry`=39189;
UPDATE `creature_template` SET `ScriptName`='npc_oculus_unstable_sphere' WHERE `entry`=28166;
