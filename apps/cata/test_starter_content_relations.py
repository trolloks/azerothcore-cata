"""Offline loader-reference checks: python3 apps/cata/test_starter_content_relations.py."""

import re
import unittest
from unittest.mock import patch

import import_starter_content as importer
import starter_content_inventory as inventory


SCRIPT_COLS = (["entryorguid", "source_type", "id", "link", "event_type"]
               + [f"event_param{i}" for i in range(1, 5)] + ["action_type"]
               + [f"action_param{i}" for i in range(1, 7)] + ["target_type"]
               + [f"target_param{i}" for i in range(1, 3)])
CONDITION_COLS = ["SourceTypeOrReferenceId", "SourceGroup", "SourceEntry", "SourceId",
                  "ConditionTypeOrReference", "ConditionValue1", "ConditionValue2", "ConditionValue3", "ScriptName"]


def script(**values):
    return {**dict.fromkeys(SCRIPT_COLS, "0"), "entryorguid": "100", **{k: str(v) for k, v in values.items()}}


def condition(**values):
    return {**dict.fromkeys(CONDITION_COLS, "0"), "SourceTypeOrReferenceId": "19", "SourceEntry": "1",
            "ScriptName": "", **{k: str(v) for k, v in values.items()}}


class LoaderRelationsTest(unittest.TestCase):
    def audit(self, scripts=(), conditions=(), tables=None, answers=()):
        tables = {"quest_template": {"ID"}, "creature_template": {"entry"},
                  "gameobject_template": {"entry"}, "creature": {"guid", "id"},
                  "gameobject": {"guid", "id"}, "game_event": {"eventEntry"},
                  "areatrigger": {"entry"}, "spawn_group_template": {"groupId"},
                  "smart_scripts": set(SCRIPT_COLS), "conditions": set(CONDITION_COLS), **(tables or {})}
        self.queries = []

        def query(_container, _user, _password, _schema, sql):
            self.queries.append(sql)
            for fragment, rows in answers:
                if fragment in sql:
                    return rows
            if sql == f"SELECT {','.join(SCRIPT_COLS)} FROM smart_scripts":
                return [[r[c] for c in SCRIPT_COLS] for r in scripts]
            if sql == f"SELECT {','.join(CONDITION_COLS)} FROM conditions":
                return [[r[c] for c in CONDITION_COLS] for r in conditions]
            match = re.fullmatch(r"SELECT DISTINCT (\w+) FROM (\w+)", sql)
            if match:
                return {"quest_template": [["1"]], "creature_template": [["100"]],
                        "gameobject_template": [["200"]], "creature": [["10"]]}.get(match[2], [])
            return []

        with patch.object(inventory, "query", side_effect=query), \
                patch.object(inventory, "table_exists", side_effect=lambda *args: args[-1] in tables), \
                patch.object(inventory, "columns", side_effect=lambda *args: tables.get(args[-1], set())), \
                patch.object(inventory, "registered_script_names", return_value={"known_script"}):
            return inventory.check_loader_relations("", "", "", "", {100}, {200}, {10}, {20}, {"1"},
                                                     {"1": {"100"}, "10": {"500"}})

    def test_smartai_parameter_references(self):
        for values, kind in (
            ({"action_type": 12, "action_param1": 999}, "smartai_action_reference"),
            ({"action_type": 7, "action_param1": 999}, "smartai_action_reference"),
            ({"action_type": 53, "action_param4": 999}, "smartai_action_reference"),
            ({"event_type": 5, "event_param4": 999}, "smartai_event_reference"),
            ({"event_type": 35, "event_param1": 999}, "smartai_event_reference"),
            ({"event_type": 75, "event_param1": 999}, "smartai_event_reference"),
            ({"target_type": 10, "target_param2": 999}, "smartai_target_reference"),
            ({"target_type": 207, "target_param1": 2, "target_param2": 999}, "smartai_target_reference"),
        ):
            with self.subTest(values=values):
                problems = self.audit([script(**values)])
                self.assertIn((kind, "999"), {(p["kind"], p["id"]) for p in problems})

    def test_optional_zero_required_zero_and_install_template(self):
        self.assertEqual(self.audit([script(action_type=3), script(target_type=10)]), [])
        problems = self.audit([script(action_type=12)])
        self.assertEqual(problems[0]["id"], "0")
        self.assertEqual(self.audit([script(action_type=58, target_type=10, target_param2=999)]), [])

    def test_timed_lists_recurse_and_ignore_stored_event_type(self):
        rows = [script(action_type=80, action_param1=500),
                script(entryorguid=500, source_type=9, action_type=87, action_param1=501,
                       event_type=19, event_param1=999),
                script(entryorguid=501, source_type=9, action_type=80, action_param1=500, id=1),
                script(entryorguid=501, source_type=9, action_type=12, action_param1=998)]
        self.assertEqual([(p["kind"], p["id"]) for p in self.audit(rows)],
                         [("smartai_action_reference", "998")])
        problems = self.audit([script(action_type=88, action_param1=500, action_param2=501), rows[1]])
        self.assertIn(("smartai_timed_list", "501"), {(p["kind"], p["id"]) for p in problems})

    def test_empty_timed_list_is_a_relation_problem(self):
        problems = self.audit([script(action_type=80, action_param1=500)])
        self.assertEqual(problems, [{"kind": "smartai_timed_list", "id": "500",
                                     "from": "smart_scripts.100.0.0"}])

    def test_rejected_nested_timed_list_is_a_relation_problem(self):
        rows = [script(action_type=80, action_param1=500),
                script(entryorguid=500, source_type=9, action_type=80, action_param1=501),
                script(entryorguid=501, source_type=9, action_type=85, target_type=7)]
        answers = [("'smart_unsupported_action', 'smart_unused_target_param'",
                    [["501", "9", "0", "smart_unsupported_action"]])]
        problems = self.audit(rows, answers=answers)
        self.assertEqual(problems, [{"kind": "smart_unsupported_action", "id": "501.9.0"}])
        self.assertFalse(any("source_type<>9" in q for q in self.queries))

    def test_timed_links_use_each_calling_owners_events(self):
        rows = [script(action_type=80, action_param1=500), script(id=5, event_type=61),
                script(entryorguid=-10, action_type=80, action_param1=500),
                script(entryorguid=500, source_type=9, link=5)]
        problems = self.audit(rows)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["kind"], "smartai_link")
        self.assertTrue(problems[0]["from"].endswith("owner.-10.0"))
        rows[-1]["id"] = "5"  # A self link is ignored by ProcessAction.
        self.assertEqual(self.audit(rows), [])

    def test_equipment_random_id_and_text_group_zero(self):
        tables = {"creature_equip_template": {"CreatureID", "ID"}, "creature_text": {"CreatureID", "GroupID"}}
        answers = [("SELECT ID FROM creature_equip_template WHERE CreatureID=100", [["2"]]),
                   ("SELECT GroupID FROM creature_text WHERE CreatureID=100 AND GroupID=0", [["0"]])]
        self.assertEqual(self.audit([script(action_type=71, action_param1=255), script(action_type=1)],
                                    tables=tables, answers=answers), [])
        self.assertFalse(any("ID=-1" in q or "ID=255" in q for q in self.queries))

    def test_condition_templates_and_value_references(self):
        rows = [condition(ConditionTypeOrReference=-5),
                condition(SourceTypeOrReferenceId=-5, ConditionTypeOrReference=-6),
                condition(SourceTypeOrReferenceId=-6, ConditionTypeOrReference=-5),
                condition(SourceTypeOrReferenceId=-6, ConditionTypeOrReference=48, ConditionValue1=999)]
        self.assertEqual([(p["kind"], p["id"]) for p in self.audit(conditions=rows)],
                         [("condition_value_reference", "999")])
        self.assertEqual(self.audit(conditions=rows[:1])[0]["kind"], "condition_reference")
        self.assertEqual(self.audit(conditions=[condition(ConditionTypeOrReference=47, ConditionValue1=999)]), [])

    def test_condition_guid_sentinel_and_mismatch(self):
        self.assertEqual(self.audit(conditions=[condition(ConditionTypeOrReference=31, ConditionValue1=3,
                                                          ConditionValue3=1)]), [])
        rows = [condition(ConditionTypeOrReference=31, ConditionValue1=3, ConditionValue2=100, ConditionValue3=10)]
        problems = self.audit(conditions=rows, answers=[("SELECT id FROM creature WHERE guid=10", [["101"]])])
        self.assertEqual(problems[0]["kind"], "condition_object_entry_guid")

    def test_loot_reference_condition_does_not_require_item(self):
        rows = [condition(SourceTypeOrReferenceId=1, SourceGroup=100, SourceEntry=700,
                          ConditionTypeOrReference=31, ConditionValue1=3)]
        tables = {"creature_loot_template": {"Entry", "Item", "Reference"}, "item_template": {"entry"}}
        self.assertEqual(self.audit(conditions=rows, tables=tables,
                                    answers=[("SELECT Reference FROM creature_loot_template", [["500"]])]), [])
        self.assertEqual({p["kind"] for p in self.audit(conditions=rows, tables=tables)},
                         {"condition_source_loot", "condition_source_item"})

    def test_loot_source_membership_is_checked_for_condition_references(self):
        rows = [condition(SourceTypeOrReferenceId=1, SourceGroup=100, SourceEntry=700,
                          ConditionTypeOrReference=-5), condition(SourceTypeOrReferenceId=-5)]
        problems = self.audit(conditions=rows, tables={"creature_loot_template": {"Entry", "Item", "Reference"},
                                                       "item_template": {"entry"}})
        self.assertEqual({p["kind"] for p in problems}, {"condition_source_loot"})

    def test_missing_tables_and_columns_are_skipped(self):
        tables = {"creature_template_model": {"CreatureID"}, "waypoint_data": {"id"},
                  "creature_addon": {"guid", "path_id"}, "smart_scripts": {"entryorguid"}}
        self.audit([script(action_type=12, action_param1=999)], tables=tables)
        self.assertFalse(any("CreatureDisplayID" in q or "FROM smart_scripts" in q for q in self.queries))
        self.assertTrue(any("t.id=s.path_id" in q and "s.guid IN (10)" in q for q in self.queries))

    def test_model_gender_and_page_chains(self):
        tables = {"creature_template_model": {"CreatureID", "CreatureDisplayID"},
                  "creature_model_info": {"DisplayID", "DisplayID_Other_Gender"},
                  "gameobject_template": {"entry", "type", "Data7"}, "page_text": {"ID", "NextPageID"}}
        answers = [("SELECT CreatureDisplayID", [["50"]]),
                   ("SELECT DISTINCT DisplayID FROM creature_model_info", [["50"]]),
                   ("SELECT DisplayID, DisplayID_Other_Gender", [["50", "51"]]),
                   ("SELECT Data7", [["60"]]), ("SELECT DISTINCT ID FROM page_text", [["60"]]),
                   ("SELECT ID, NextPageID", [["60", "61"]])]
        self.assertEqual({p["kind"] for p in self.audit(tables=tables, answers=answers)},
                         {"creature_model_other_gender", "page_next"})


class RelationClosureTest(unittest.TestCase):
    def test_quest_only_templates_signed_loot_and_zero_items(self):
        tables = {"quest_template": {"ID", "QuestSortID", "RequiredNpcOrGo1"},
                  "creature_template": {"entry", "difficulty_entry_1", "lootid"},
                  "gameobject_template": {"entry"}, "creature_loot_template": {"Entry", "Item", "Reference"},
                  "reference_loot_template": {"Entry", "Item", "Reference"}, "item_template": {"entry"}}
        statements = []

        def query(_container, _user, _password, _schema, sql):
            statements.append(sql)
            if sql.startswith("SELECT ID,RequiredNpcOrGo1 FROM quest_template"):
                return [["1", "100"]]
            if sql == "SELECT ID FROM quest_template":
                return [["1"]]
            if sql == "SELECT entry FROM creature_template":
                return [["100"], ["101"]]
            if sql.startswith("SELECT difficulty_entry_1 FROM creature_template"):
                return [["101" if "IN (100)" in sql else "0"]]
            if sql.startswith("SELECT DISTINCT lootid") or sql.startswith("SELECT DISTINCT Entry"):
                return [["7"]]
            if sql == "SELECT Entry FROM reference_loot_template":
                return [["50"], ["51"]]
            if sql.startswith("SELECT DISTINCT ABS(Reference)"):
                return [["50"]]  # The stored reference may be -50; the loader uses its absolute ID.
            if sql.startswith("SELECT Entry, ABS(Reference)"):
                return [["50", "51"]]
            if sql.startswith("SELECT DISTINCT Item"):
                self.assertNotIn("Item>0", sql)
                return [["0"]]
            return []

        with patch.object(inventory, "query", side_effect=query), \
                patch.object(inventory, "table_exists", side_effect=lambda *args: args[-1] in tables), \
                patch.object(inventory, "columns", side_effect=lambda *args: tables.get(args[-1], set())), \
                patch.object(inventory, "zone_creature_entries", return_value=set()), \
                patch.object(inventory, "zone_gameobject_entries", return_value=set()), \
                patch.object(inventory, "zone_guids", return_value=set()), \
                patch.object(inventory, "check_loader_relations", return_value=[]) as loader:
            problems = inventory.check_relations("", "", "", "", 12, 0)
        self.assertEqual({p["kind"] for p in problems}, {"loot_item"})
        self.assertEqual({p["id"] for p in problems}, {"0"})
        self.assertEqual(loader.call_args.args[4], {100, 101})
        self.assertEqual(loader.call_args.args[-1]["10"], {"50", "51"})
        self.assertTrue(any("SELECT Entry, ABS(Reference)" in sql for sql in statements))


class ImportDependenciesTest(unittest.TestCase):
    def test_missing_rows_follow_native_links_and_stop_cycles(self):
        inserts = []

        def fetch(_container, _user, _password, sql):
            if sql == "SELECT ID FROM target.page_text":
                return ["1"]
            if "FROM target.page_text WHERE ID IN (1)" in sql:
                return ["2"]
            if "FROM source.page_text WHERE ID IN (2)" in sql:
                return ["1"]
            self.fail(sql)

        def insert(*args):
            inserts.append(args)
            return 1, ""

        with patch.object(importer, "table_exists", return_value=True), \
                patch.object(importer, "columns", return_value=[("ID", ""), ("NextPageID", "")]), \
                patch.object(importer, "fetch", side_effect=fetch), \
                patch.object(importer, "import_table", side_effect=insert):
            count = importer.import_missing_rows("", "", "", "target", "source", "page_text", "ID",
                                                 ["1"], False, "NextPageID")
        self.assertEqual(count, 1)
        self.assertEqual(inserts[0][-2], ["2"])
        self.assertEqual(len(inserts), 1)

    def test_insert_ignore_and_missing_key_guard(self):
        with patch.object(importer, "common_columns", return_value=["ID", "Text"]), \
                patch.object(importer, "insert_count", side_effect=AssertionError("dry-run wrote data")):
            count, sql = importer.import_table("", "", "", "target", "source", "page_text", "ID", ["1"], True)
            self.assertEqual(count, 0)
            self.assertIn("INSERT IGNORE INTO", sql)
            count, sql = importer.import_table("", "", "", "target", "source", "page_text", "entry", ["1"], False)
            self.assertEqual(count, 0)
            self.assertTrue(sql.startswith("-- no compatible key"))


if __name__ == "__main__":
    with patch("subprocess.run", side_effect=AssertionError("offline checks must not launch subprocesses")):
        unittest.main()
