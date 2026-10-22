--
ALTER TABLE `account` ALTER COLUMN `expansion` SET DEFAULT 3;
UPDATE `account` SET `expansion` = 3 WHERE `expansion` < 3;
