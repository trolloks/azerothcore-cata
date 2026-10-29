-- Restore the two Cata zone scripts ported for #211. The builder keeps the TDB bindings once the
-- class names are registered; these rows restore worlds built while they were still stripped.
UPDATE `creature_template` SET `ScriptName`='npc_blackrock_battle_worg' WHERE `entry`=49871;
UPDATE `creature_template` SET `ScriptName`='npc_mulgore_chief_squealer_thornmantle' WHERE `entry`=36712;
