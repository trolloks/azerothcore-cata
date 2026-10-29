-- 4.3.4 player currency storage (#312): quest reward currencies are stored per character instead of
-- being dropped on login.
CREATE TABLE IF NOT EXISTS `character_currency` (
  `guid` int unsigned NOT NULL,
  `Currency` smallint unsigned NOT NULL,
  `Quantity` int unsigned NOT NULL,
  PRIMARY KEY (`guid`,`Currency`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
