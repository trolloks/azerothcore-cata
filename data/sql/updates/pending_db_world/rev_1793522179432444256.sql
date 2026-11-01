-- #381: the phase-170 aura must be autocast in every Gilneas area it covers.
-- Merchant Square (4756) already had autocast=1; Military District (4755/4757/4714)
-- and the remaining area had autocast=0, leaving Gwen and King Genn invisible.
UPDATE `spell_area` SET `autocast`=1 WHERE `spell`=59073;
