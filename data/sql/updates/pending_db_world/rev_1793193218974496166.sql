-- TDB 4.3.4 leaves many trainer/vendor gossip menus without the standard option; the worldserver
-- falls back to menu 0 and the trainer/vendor button is missing (#258). Generate the standard
-- option for every trainer/vendor menu that lacks it, appended after any existing options.
INSERT IGNORE INTO `gossip_menu_option`
(MenuID, OptionID, OptionIcon, OptionText, OptionBroadcastTextID, OptionType, OptionNpcFlag,
 ActionMenuID, ActionPoiID, BoxCoded, BoxMoney, BoxText, BoxBroadcastTextID, VerifiedBuild)
SELECT t.gossip_menu_id,
       (SELECT COALESCE(MAX(o.OptionID)+1,0) FROM `gossip_menu_option` o WHERE o.MenuID=t.gossip_menu_id),
       3, 'I would like to train.', 8221, 5, 16, 0, 0, 0, 0, '', 0, 0
FROM `creature_template` t
WHERE t.gossip_menu_id<>0 AND (t.npcflag & 0x70)<>0
  AND NOT EXISTS (SELECT 1 FROM `gossip_menu_option` o WHERE o.MenuID=t.gossip_menu_id AND o.OptionType=5);
INSERT IGNORE INTO `gossip_menu_option`
(MenuID, OptionID, OptionIcon, OptionText, OptionBroadcastTextID, OptionType, OptionNpcFlag,
 ActionMenuID, ActionPoiID, BoxCoded, BoxMoney, BoxText, BoxBroadcastTextID, VerifiedBuild)
SELECT t.gossip_menu_id,
       (SELECT COALESCE(MAX(o.OptionID)+1,0) FROM `gossip_menu_option` o WHERE o.MenuID=t.gossip_menu_id),
       1, 'I want to browse your goods', 3370, 3, 128, 0, 0, 0, 0, '', 0, 0
FROM `creature_template` t
WHERE t.gossip_menu_id<>0 AND (t.npcflag & 0x80)<>0
  AND NOT EXISTS (SELECT 1 FROM `gossip_menu_option` o WHERE o.MenuID=t.gossip_menu_id AND o.OptionType=3);
