-- #378: restore the Merchant Square door spell and guard-assisted kill credit.
-- The door is a GOOBER; its goober spell was stripped from the clean world.
UPDATE `gameobject_template` SET `Data10`=67869 WHERE `entry`=195327;

-- Rampaging Worgen are quest targets fought alongside city guards. Allow a
-- tapped worgen to keep quest credit even when a guard deals the killing blow.
UPDATE `creature_template` SET `flags_extra`=`flags_extra`|0x200000 WHERE `entry` IN (34884,35660);

-- TrinityCore 4.3.4 spell_q14098_knocking_67869: cast one of the two summon
-- spells stored in effect 1/2 of Knocking (67869).
DELETE FROM `spell_script_names` WHERE `spell_id`=67869 AND `ScriptName`='spell_gilneas_knocking';
INSERT INTO `spell_script_names` (`spell_id`,`ScriptName`) VALUES
(67869,'spell_gilneas_knocking');
