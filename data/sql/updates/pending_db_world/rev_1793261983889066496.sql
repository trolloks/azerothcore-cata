-- 4.3.4 creature base stats carry a fourth expansion column. LoadCreatureClassLevelStats reads
-- basehp3/damage_exp3 for Cataclysm (exp=3) templates; TDB 434 leaves both zero below level 80 and
-- the loader bridges those from the WotLK column (#315).
ALTER TABLE `creature_classlevelstats`
    ADD COLUMN `basehp3` int unsigned NOT NULL DEFAULT '0' AFTER `basehp2`,
    ADD COLUMN `damage_exp3` float NOT NULL DEFAULT '0' AFTER `damage_exp2`;
