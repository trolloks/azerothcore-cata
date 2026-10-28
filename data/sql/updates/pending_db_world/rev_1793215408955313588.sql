-- 4.3.4 quest fields the 3.3.5a schema lacked: the required spell and the quest currencies (#179).
ALTER TABLE `quest_template`
    ADD COLUMN RequiredSpell int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RewardCurrencyId1 smallint unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RewardCurrencyCount1 int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RewardCurrencyId2 smallint unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RewardCurrencyCount2 int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RewardCurrencyId3 smallint unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RewardCurrencyCount3 int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RewardCurrencyId4 smallint unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RewardCurrencyCount4 int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RequiredCurrencyId1 smallint unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RequiredCurrencyCount1 int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RequiredCurrencyId2 smallint unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RequiredCurrencyCount2 int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RequiredCurrencyId3 smallint unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RequiredCurrencyCount3 int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RequiredCurrencyId4 smallint unsigned NOT NULL DEFAULT 0,
    ADD COLUMN RequiredCurrencyCount4 int unsigned NOT NULL DEFAULT 0;
