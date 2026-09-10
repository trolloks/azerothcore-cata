-- Issue #59: native Cataclysm items for the Human Warrior starting outfit
-- (CharStartOutfit.dbc entry for race=1/class=1 requires items 58231, 39, 40, 49778, 6948).
-- Source: var/extractors/dbc-out3/dbc/Item.db2 + Item-sparse.db2, raw-extracted from the native
-- 4.3.4.15595 client (ItemEntry/ItemSparseEntry per TrinityCore-Cata's DB2Structure.h).

-- Item 58231 "Recruit's Vest" has no item_template row at all, so Player::LoadItemFromDB/Create
-- silently skips this outfit slot. Cloned from sibling item 38 "Recruit's Shirt" (same outfit set,
-- WotLK-era template layout), with entry/subclass/name/displayid/InventoryType/BuyPrice replaced by
-- the native record.
INSERT INTO `item_template` VALUES
(58231,4,1,-1,'Recruit\'s Vest',33310,1,0,0,1,7,1,5,-1,-1,1,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-1,0,-1,0,0,0,0,-1,0,-1,0,0,0,0,-1,0,-1,0,0,0,0,-1,0,-1,0,0,0,0,-1,0,-1,0,'',0,0,0,0,0,7,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-1,0,0,0,0,'',0,0,0,0,0,12340);

-- Item 39 "Recruit's Pants": AC's WotLK-era dump has Quality=0 (Poor) and BuyPrice=5, but native
-- ItemSparseEntry has Quality=1 (Common) and BuyPrice=7, matching its Recruit's-outfit siblings.
UPDATE `item_template` SET `Quality` = 1, `BuyPrice` = 7 WHERE `entry` = 39;

-- Item 49778 "Worn Greatsword": AC's WotLK-era dump has BuyPrice=47, SellPrice=9, ItemLevel=2, but
-- native ItemSparseEntry has BuyPrice=23, SellPrice=4, ItemLevel=1.
UPDATE `item_template` SET `BuyPrice` = 23, `SellPrice` = 4, `ItemLevel` = 1 WHERE `entry` = 49778;

-- Item 6948 "Hearthstone": AC's WotLK-era dump has Material=-1, but native ItemSparseEntry has
-- Material=0 (no weapon-swing sound override for this trinket-slot item).
UPDATE `item_template` SET `Material` = 0 WHERE `entry` = 6948;
