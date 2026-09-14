-- Issue #59: item-spell relation for the Human Warrior starting outfit's Hearthstone (6948).
-- AC's WotLK-era dump has spellcooldown_1=-1/spellcategory_1=0/spellcategorycooldown_1=-1 for the
-- bound Hearthstone spell (8690), but native Item-sparse.db2 ItemSparseEntry has
-- SpellCooldown0=1800000/SpellCategory0=1176/SpellCategoryCooldown0=1800000 (the 30-minute Hearthstone
-- cooldown, tracked by shared cooldown category 1176). SpellID0/Bonding already matched.
UPDATE `item_template` SET `spellcooldown_1` = 1800000, `spellcategory_1` = 1176, `spellcategorycooldown_1` = 1800000
WHERE `entry` = 6948;
