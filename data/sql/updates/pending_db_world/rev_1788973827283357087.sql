-- Human Warrior starting spawn point and action bar: native Cataclysm values.
-- Source: verified SQL replay of the pinned TDB 434.22011 world update chain, disposable MySQL 8.4.
-- See apps/cata/fixtures/plan22-starting-data-audit.json (reference_spawn, reference_actions).

DELETE FROM `playercreateinfo` WHERE `race` = 1 AND `class` = 1;
INSERT INTO `playercreateinfo` (`race`, `class`, `map`, `zone`, `position_x`, `position_y`, `position_z`, `orientation`) VALUES
(1, 1, 0, 9, -8914.57, -133.909, 80.5378, 5.13806);

DELETE FROM `playercreateinfo_action` WHERE `race` = 1 AND `class` = 1;
INSERT INTO `playercreateinfo_action` (`race`, `class`, `button`, `action`, `type`) VALUES
(1, 1, 72, 88163, 0),
(1, 1, 73, 88161, 0),
(1, 1, 81, 59752, 0),
(1, 1, 84, 6603, 0),
(1, 1, 96, 6603, 0),
(1, 1, 108, 6603, 0);
