-- Proving Pit (Durotar): the Jailor's gossip select is handled by npc_darkspear_jailor.
-- Drop the duplicate option that had no action and shadowed the scripted one.
DELETE FROM `gossip_menu_option` WHERE `MenuID` = 10974 AND `OptionID` = 0;
UPDATE `creature_template` SET `ScriptName` = 'npc_darkspear_jailor' WHERE `entry` = 39062;
