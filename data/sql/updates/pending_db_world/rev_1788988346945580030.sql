-- Item 40 "Recruit's Boots" has subclass=0 (Cloth) in AC's WotLK-era item_template dump, but
-- native Cataclysm 4.3.4.15595 Item.db2 (ItemEntry) has SubclassID=1 (Leather) for this item,
-- matching its Recruit's-outfit siblings (item 39 "Recruit's Pants" is already subclass=1 here).
-- Source: var/extractors/dbc-out3/dbc/Item.db2, raw-extracted from the native client MPQs.

UPDATE `item_template` SET `subclass` = 1 WHERE `entry` = 40;
