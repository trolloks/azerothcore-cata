-- Remove stale spell_script_names entry for spell_sha_t8_electrified.
DELETE FROM `spell_script_names` WHERE `spell_id` = 64928 AND `ScriptName` = 'spell_sha_t8_electrified';
