-- Restore the Stormwind Infantry binding now that npc_stormwind_infantry is ported (#331). The class
-- name equals the TDB ScriptName, so the builder keeps it on a rebuild.
UPDATE `creature_template` SET `ScriptName`='npc_stormwind_infantry' WHERE `entry`=49869;
