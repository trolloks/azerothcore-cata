-- playercreateinfo_action: correct the Human Warrior (race 1, class 1) starting action bar.
-- Buttons 72/73/82 still carry WotLK-era actions (Auto Attack/Charge/Every Man for Himself in
-- the wrong slots) and button 108 (auto attack) is missing entirely. Pinned Cata 4.3.4 reference
-- (issue #57 audit, TDB 434.22011): button 72 -> Heroic Strike (88163), 73 -> Charge (88161),
-- 81 -> Every Man for Himself (59752, moved from 82), 108 -> Auto Attack (6603, added).
DELETE FROM `playercreateinfo_action` WHERE `race` = 1 AND `class` = 1 AND `button` IN (72, 73, 81, 82, 108);
INSERT INTO `playercreateinfo_action` (`race`, `class`, `button`, `action`, `type`) VALUES
(1, 1, 72, 88163, 0),
(1, 1, 73, 88161, 0),
(1, 1, 81, 59752, 0),
(1, 1, 108, 6603, 0);
