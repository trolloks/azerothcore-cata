-- #340: 4.3.4 terrain-swap model. A parent map swaps in child terrain maps by default; each
-- child maps its UI world-map areas, which the phase-shift packet reports separately.
CREATE TABLE IF NOT EXISTS `terrain_swap_defaults` (
  `MapId` int unsigned NOT NULL DEFAULT '0',
  `TerrainSwapMap` int unsigned NOT NULL DEFAULT '0',
  `Comment` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`MapId`,`TerrainSwapMap`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `terrain_worldmap` (
  `TerrainSwapMap` int unsigned NOT NULL DEFAULT '0',
  `WorldMapArea` int unsigned NOT NULL DEFAULT '0',
  `Comment` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`TerrainSwapMap`,`WorldMapArea`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
