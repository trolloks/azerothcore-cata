"""Offline checks for native spell support copied into the clean world."""

import sqlite3
import unittest
from unittest.mock import patch

import build_clean_world as builder


class SpellSupportTest(unittest.TestCase):
    def test_spell_destinations_and_guid_target_conditions_are_copied(self):
        db = sqlite3.connect(":memory:")
        db.create_function("IF", 3, lambda test, yes, no: yes if test else no)
        for schema in ("source", "target"):
            db.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
            db.execute(f"CREATE TABLE {schema}.spell_target_position (ID INTEGER, EffectIndex INTEGER, MapID INTEGER)")
            db.execute(f"CREATE TABLE {schema}.conditions (SourceTypeOrReferenceId INTEGER, "
                       "ConditionTypeOrReference INTEGER, ConditionValue1 INTEGER, ConditionValue3 INTEGER)")
        db.execute("INSERT INTO source.spell_target_position VALUES (68630, 0, 654)")
        db.executemany("INSERT INTO source.conditions VALUES (?, ?, ?, ?)",
                       [(13, 31, 3, 42), (13, 31, 5, 43), (13, 31, 6, 44), (13, 31, 3, 0),
                        (18, 31, 3, 45)])

        def columns(_container, _user, _password, schema, table):
            return [(row[1], False) for row in db.execute(f"PRAGMA {schema}.table_info({table})")]

        def insert(_container, _user, _password, sql):
            return db.execute(sql.replace("INSERT IGNORE", "INSERT OR IGNORE")).rowcount

        with patch.object(builder, "columns", side_effect=columns), \
                patch.object(builder, "insert_count", side_effect=insert):
            builder.build_spell_support("", "", "", "target", "source")

        self.assertEqual(db.execute("SELECT * FROM target.spell_target_position").fetchall(), [(68630, 0, 654)])
        self.assertEqual(db.execute("SELECT ConditionValue3 FROM target.conditions").fetchall(),
                         [(10_000_042,), (10_000_043,), (44,), (0,)])

    def test_spell_area_autocast_uses_tdb_flags(self):
        db = sqlite3.connect(":memory:")
        db.execute("ATTACH DATABASE ':memory:' AS source")
        db.execute("ATTACH DATABASE ':memory:' AS target")
        db.execute("CREATE TABLE source.spell_area (spell INTEGER, flags INTEGER)")
        db.execute("CREATE TABLE target.spell_area (spell INTEGER, autocast INTEGER DEFAULT 0)")
        db.executemany("INSERT INTO source.spell_area VALUES (?, ?)", [(68481, 3), (68482, 1), (68483, 2)])

        def columns(_container, _user, _password, schema, table):
            return [(row[1], False) for row in db.execute(f"PRAGMA {schema}.table_info({table})")]

        def insert(_container, _user, _password, sql):
            return db.execute(sql.replace("INSERT IGNORE", "INSERT OR IGNORE")).rowcount

        with patch.object(builder, "columns", side_effect=columns), \
                patch.object(builder, "insert_count", side_effect=insert):
            builder.copy_table("", "", "", "target", "source", "spell_area")

        self.assertEqual(db.execute("SELECT * FROM target.spell_area ORDER BY spell").fetchall(),
                         [(68481, 1), (68482, 1), (68483, 0)])


if __name__ == "__main__":
    unittest.main()
