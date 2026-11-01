-- #387: class trainers must expose exactly one trainer gossip option.
-- Sergeant Cleese's duplicate warrior row and Myriam Spellwaker's stray druid row.
DELETE FROM `gossip_menu_option` WHERE `MenuID`=10694 AND `OptionID`=1;
DELETE FROM `gossip_menu_option` WHERE `MenuID`=10698 AND `OptionID`=1;
