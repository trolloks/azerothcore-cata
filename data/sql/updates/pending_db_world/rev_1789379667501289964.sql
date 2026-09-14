-- Issue #59: FlagsExtra parity for the Human Warrior starting outfit with native item-sparse data.
-- AC's WotLK-era dump has FlagsExtra=0 for these items, but native Item-sparse.db2 ItemSparseEntry
-- has Flags2=8192 (ITEM_FLAG2_SHOW_BEFORE_DISCOVERED) for the recruit gear and worn greatsword, and
-- Flags2=40960 (ITEM_FLAG2_SHOW_BEFORE_DISCOVERED | ITEM_FLAG2_IGNORE_DEFAULT_RATED_BG_RESTRICTIONS)
-- for the Hearthstone. Both flags are currently NYI in AC's item handling, so this is a data-parity
-- fix with no functional effect yet.
UPDATE `item_template` SET `FlagsExtra` = 8192 WHERE `entry` IN (39, 40, 49778, 58231);
UPDATE `item_template` SET `FlagsExtra` = 40960 WHERE `entry` = 6948;
