import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

import workbook_discovery as discovery


class DiscoveryFunctionTests(unittest.TestCase):
    def test_profile_counts_nulls_and_duplicate_groups(self):
        frame = pd.DataFrame({"record_id": ["a", "a", "b", None, ""],
                              "name": ["A", "B", "C", "D", "E"]})
        profile = discovery.profile_table(frame)
        key = profile["columns"][0]
        self.assertEqual(profile["row_count"], 5)
        self.assertEqual(profile["candidate_id_columns"], ["record_id"])
        self.assertEqual(key["null_rows"], 2)
        self.assertEqual(key["distinct_non_null_keys"], 2)
        self.assertEqual(key["duplicate_key_count"], 1)
        self.assertEqual(key["duplicate_rows"], 2)
        self.assertEqual(key["duplicate_excess_rows"], 1)
        # A unique name alone is not proposed as an identifier.
        self.assertEqual(profile["potential_primary_keys"], [])

    def test_unique_nullable_and_empty_keys_are_not_primary(self):
        for values in ([], [None], ["a", None]):
            with self.subTest(values=values):
                self.assertFalse(discovery.profile_key(
                    pd.DataFrame({"id": values}), "id")["is_primary_key_candidate"])

    def test_minimal_composite_candidate(self):
        frame = pd.DataFrame({"slack_user_id": ["a", "a", "b"],
                              "timestamp": [1, 2, 1], "channel": ["x", "x", "x"]})
        result = discovery.profile_table(frame)
        self.assertEqual(result["potential_composite_keys"],
                         [["slack_user_id", "timestamp"]])
        self.assertTrue(discovery.profile_key(frame, ["slack_user_id", "timestamp"])
                        ["is_primary_key_candidate"])

    def test_exact_many_to_many_counts_without_join(self):
        left = pd.DataFrame({"id": ["a", "a", "b", "orphan", None]})
        right = pd.DataFrame({"id": ["a", "a", "a", "b", "unused", None]})
        result = discovery.test_relationship(left, right, "id", "id")
        self.assertEqual(result["cardinality"], "many_to_many")
        self.assertEqual(result["inner_join_rows"], 7)
        self.assertEqual(result["left_join_rows"], 9)
        self.assertEqual(result["right_join_rows"], 9)
        self.assertEqual(result["left_unmatched_rows"], 1)
        self.assertEqual(result["right_unmatched_rows"], 1)
        self.assertEqual(result["status"], "needs_investigation")

    def test_nullable_foreign_key_and_unused_parent(self):
        result = discovery.test_relationship(
            pd.DataFrame({"child_id": ["a", "a", None]}),
            pd.DataFrame({"id": ["a", "b"]}), "child_id", "id")
        self.assertTrue(result["left_fk_to_right"])
        self.assertEqual(result["status"], "verified_data_relationship")
        self.assertEqual(result["left"]["null_rows"], 1)
        self.assertEqual(result["right_unmatched_rows"], 1)
        self.assertEqual(result["left_join_rows"], 3)

    def test_nulls_do_not_match_and_target_nulls_do_not_qualify_as_pk(self):
        result = discovery.test_relationship(pd.DataFrame({"id": ["a", None]}),
                                             pd.DataFrame({"id": ["a", None]}), "id", "id")
        self.assertEqual(result["inner_join_rows"], 1)
        self.assertEqual(result["left_join_rows"], 2)
        self.assertFalse(result["left_fk_to_right"])
        self.assertEqual(result["status"], "needs_investigation")

    def test_orphans_prevent_verified_fk(self):
        result = discovery.test_relationship(pd.DataFrame({"id": ["a", "a", "x"]}),
                                             pd.DataFrame({"id": ["a", "b"]}), "id", "id")
        self.assertEqual(result["left_unmatched_rows"], 1)
        self.assertFalse(result["left_fk_to_right"])
        self.assertEqual(result["status"], "needs_investigation")

    def test_composite_joins_require_all_components_and_no_nulls(self):
        result = discovery.test_relationship(
            pd.DataFrame({"account": ["a", "a", "a", "b"], "month": [1, 2, None, 1]}),
            pd.DataFrame({"customer": ["a", "a"], "period": [1, 2]}),
            ["account", "month"], ["customer", "period"])
        self.assertEqual(result["matched_distinct_keys"], 2)
        self.assertEqual(result["left_unmatched_rows"], 1)
        self.assertEqual(result["left"]["null_rows"], 1)

    def test_no_implicit_string_numeric_boolean_or_whitespace_coercion(self):
        result = discovery.test_relationship(
            pd.DataFrame({"id": ["1", True, " a"]}),
            pd.DataFrame({"id": [1, "a"]}), "id", "id")
        self.assertEqual(result["matched_distinct_keys"], 0)
        self.assertEqual(result["status"], "rejected_no_overlap")

    def test_numpy_and_python_scalars_match_without_boolean_numeric_collision(self):
        result = discovery.test_relationship(
            pd.DataFrame({"id": [True, False]}).convert_dtypes(),
            pd.DataFrame({"id": [True, False]}), "id", "id")
        self.assertEqual(result["matched_distinct_keys"], 2)
        self.assertEqual(result["status"], "verified_data_relationship")
        numeric = discovery.test_relationship(
            pd.DataFrame({"id": [True, False]}).convert_dtypes(),
            pd.DataFrame({"id": [1, 0]}), "id", "id")
        self.assertEqual(numeric["matched_distinct_keys"], 0)

    def test_invalid_keys_are_actionable(self):
        frame = pd.DataFrame({"id": ["a"]})
        for left_on, right_on in [("missing", "id"), ([], []),
                                  (["id", "id"], ["id", "id"]), (["id"], ["id", "x"])]:
            with self.subTest(left_on=left_on, right_on=right_on):
                with self.assertRaises(ValueError):
                    discovery.test_relationship(frame, frame, left_on, right_on)

    def test_candidates_require_header_or_id_evidence(self):
        tables = {"left": pd.DataFrame({"employee_id": ["a"], "amount": [5], "name": ["Z"]}),
                  "right": pd.DataFrame({"owner_id": ["a"], "amount": [5], "name": ["Z"]}),
                  "other": pd.DataFrame({"employee_id": ["x"]})}
        before = {name: frame.copy(deep=True) for name, frame in tables.items()}
        results = discovery.discover_relationships(tables)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["left_on"], ["employee_id"])
        self.assertEqual(results[0]["right_on"], ["owner_id"])
        self.assertEqual(results[1]["status"], "rejected_no_overlap")
        for name, frame in tables.items():
            pd.testing.assert_frame_equal(frame, before[name])


class LocalWorkbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before_hash = hashlib.sha256(discovery.DEFAULT_WORKBOOK.read_bytes()).hexdigest()
        cls.audit = discovery.discover_workbook()

    def relationship(self, lt, lc, rt, rc):
        for relationship in self.audit["relationships"]:
            if (relationship["left_table"], relationship["left_on"],
                relationship["right_table"], relationship["right_on"]) == (lt, [lc], rt, [rc]):
                return relationship
        self.fail(f"Missing tested candidate {lt}.{lc} -> {rt}.{rc}")

    def test_all_sheets_profiled_with_correct_rows(self):
        expected = [80, 1080, 196, 17, 17, 4, 761, 815, 107052, 1089, 59]
        self.assertEqual([s["data_rows"] for s in self.audit["sheets"]], expected)
        self.assertEqual(len(self.audit["tables"]), 12)
        for table in self.audit["tables"].values():
            self.assertTrue(table["columns"])
            for column in table["columns"]:
                self.assertIn("dtype", column)
                self.assertIn("null_rows", column)
                self.assertIn("candidate_id", column)

    def test_mixed_accelerator_tables_do_not_include_notes_as_rows(self):
        sheet = self.audit["sheets"][5]
        self.assertEqual([t["header_row"] for t in sheet["tables"]], [2, 8])
        self.assertEqual([t["row_count"] for t in sheet["tables"]], [2, 2])
        self.assertEqual(sheet["note_or_unclassified_rows"], [1, 6, 7])
        self.assertEqual(sheet["blank_rows"], 1)
        for table in sheet["tables"]:
            profile = self.audit["tables"][table["table_id"]]
            self.assertEqual(profile["columns"][0]["name"], "Role")
            self.assertEqual(profile["potential_primary_keys"], [["Role"]])

    def test_na_country_is_preserved_and_currency_not_a_pk(self):
        table = self.audit["tables"]["3. Exchange Rates"]
        country = next(c for c in table["columns"] if c["name"] == "Country Code")
        self.assertEqual(country["null_rows"], 0)
        self.assertEqual(country["distinct_non_null_keys"], 196)
        self.assertIn(["Country Code"], table["potential_primary_keys"])
        self.assertNotIn(["Code"], table["potential_primary_keys"])
        ramp = self.audit["tables"]["4. Ramp Terms"]
        trigger = next(c for c in ramp["columns"] if c["name"] == "New Report Trigger")
        self.assertEqual(trigger["null_rows"], 0)  # literal 'na' is not a blank

    def test_known_joins_and_manager_orphans(self):
        roster = self.relationship("1. Sales Roster", "hr_id", "2. HR Export", "hr_id")
        self.assertEqual(roster["status"], "verified_data_relationship")
        self.assertEqual(roster["cardinality"], "one_to_one")
        self.assertEqual(roster["left_matched_rows"], 80)
        self.assertEqual(roster["right_unmatched_rows"], 1000)
        manager = self.relationship("2. HR Export", "hr_id", "2. HR Export", "Manager - ID")
        self.assertEqual(manager["right_unmatched_rows"], 2)
        self.assertEqual(manager["right"]["null_rows"], 999)
        self.assertEqual(manager["status"], "needs_investigation")

    def test_account_and_shift_grain_and_consistency(self):
        relationship = self.relationship("9. App Export", "shift_id", "10. Billing Export", "shift_id")
        self.assertTrue(relationship["right_fk_to_left"])
        self.assertEqual(relationship["inner_join_rows"], 1089)
        app = self.relationship("7. Opportunity to ID Mapping", "app_account_id",
                                "9. App Export", "app_account_id")
        self.assertEqual(app["left_unmatched_distinct_keys"], 4)
        self.assertTrue(app["right_fk_to_left"])
        check = self.audit["path_checks"]["billing_shift_account_consistency"]
        self.assertEqual(check, {"status": "verified", "tested_rows": 1089,
                                 "mismatched_account_rows": 0})
        scoped = self.audit["path_checks"]["sales_roster_current_role_to_compensation"]
        self.assertEqual(scoped["left_matched_rows"], 80)
        self.assertEqual(scoped["left_unmatched_rows"], 0)
        account_join = self.audit["path_checks"]["opportunity_to_shift_account_join"]
        self.assertEqual(account_join["cardinality"], "many_to_many")
        self.assertGreater(account_join["inner_join_rows"], 107052)

    def test_report_and_serialization_and_source_unchanged(self):
        json.dumps(self.audit, allow_nan=False)
        text = discovery.render_report(self.audit)
        self.assertIn("Verified in this snapshot", text)
        self.assertIn("Partial or ambiguous candidates", text)
        self.assertIn("Data grain", text)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.md"
            discovery.write_report(self.audit, output)
            self.assertEqual(output.read_text(), text)
        with self.assertRaises(ValueError):
            discovery.write_report(self.audit, discovery.DEFAULT_WORKBOOK)
        self.assertEqual(hashlib.sha256(discovery.DEFAULT_WORKBOOK.read_bytes()).hexdigest(),
                         self.before_hash)
        self.assertEqual(self.audit["sha256"], self.before_hash)


if __name__ == "__main__":
    unittest.main()
