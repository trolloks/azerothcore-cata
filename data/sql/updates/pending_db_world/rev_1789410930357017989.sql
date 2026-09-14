-- achievement_dbc: remove stale WotLK-era override rows for achievement IDs that no longer
-- exist in the Cata 4.3.4 Achievement.dbc. The table's 62-column legacy schema (16 language
-- slots per string field) mismatches Achievementfmt now that it reads a single collapsed
-- string field, and DBCDatabaseLoader::Load asserts on any column-count mismatch. Every other
-- *_dbc override table already has 0 rows; this table's leftover rows target dead IDs, so
-- deleting them (rather than migrating the schema) restores the same "empty override" state.
DELETE FROM `achievement_dbc` WHERE `ID` IN (3696, 4788, 4789);
