--
DELETE FROM `player_xp_for_level` WHERE `Level` BETWEEN 80 AND 84;
INSERT INTO `player_xp_for_level` (`Level`, `Experience`) VALUES
(80, 1686300),
(81, 2121500),
(82, 4004000),
(83, 5203400),
(84, 9165100);
