"""Independent reliability regressions: real tools, explicit oracles, scripted control flow.

No Ollama inference is claimed here. Live results are produced by examples.reliability_audit.
"""
import copy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from local_analyst import Workspace
from local_analyst.agent import AnalystAgent
from local_analyst.exporting import export_query
from local_analyst.modeling import run_plan, scenario
from examples.reliability_audit import grade


def call(tool_name, **arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": tool_name, "arguments": arguments}}]}


def final(content):
    return {"role": "assistant", "content": content}


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.w = Workspace(self.root / "workspace", restore=False)
        self.addCleanup(self.w.db.close)
        self.w.register(pd.DataFrame({"id": [1, 2, 2, 4], "customer": ["A", "B", "B", None],
                                      "posted": ["2026-06-01", "2026-06-02", "2026-07-01", "2026-07-02"],
                                      "amount": [Decimal("10.10"), Decimal("20.20"), Decimal("30.30"), Decimal("40.40")]}),
                        "sales", {"label": "Unfamiliar invoices", "kind": "fixture"})
        self.w.register(pd.DataFrame({"customer": ["A", "B", "B", "C"], "region": ["W", "E", "N", "S"]}),
                        "customers", {"label": "Customer registry", "kind": "fixture"})
        self.agent = AnalystAgent(self.w)

    def ask(self, question, messages, agent=None):
        sent = []
        def respond(*args, **kwargs):
            sent.append(copy.deepcopy(kwargs["json"]))
            item = messages.pop(0)
            response = Mock()
            response.json.return_value = {"message": item() if callable(item) else item}
            return response
        with patch("local_analyst.agent.requests.post", side_effect=respond):
            result = (agent or self.agent).ask(question)
        return result, sent

    def test_a_inventory_uses_metadata_without_inference_or_sql(self):
        with patch("local_analyst.agent.requests.post", side_effect=AssertionError("Metadata inventory must not invoke inference")) as post:
            result = self.agent.ask("List all registered tables and their row counts. Do not run SQL.")
        post.assert_not_called()
        self.assertEqual(result["status"], "answered")
        self.assertTrue(all(t in result["answer"] for t in self.w.catalog))
        self.assertEqual(grade("inventory", result, self.w)["status"], "PASS")
        self.assertFalse(any(t["tool"] == "query_sql" for t in result["trace"]))
        self.assertTrue(result["evidence"])

    def test_a_inventory_original_request_and_chinese(self):
        for question in ["Use the available tools to report the row counts of all registered tables. Do not guess any values.",
                         "列出所有已注册表及各表行数，不要运行 SQL。"]:
            with self.subTest(question=question), patch("local_analyst.agent.requests.post", side_effect=AssertionError("Metadata inventory must not invoke inference")) as post:
                result = self.agent.ask(question)
                post.assert_not_called()
                self.assertEqual(result["status"], "answered")
                self.assertIn("sales", result["answer"])

    def test_a_inventory_does_not_swallow_additional_analysis(self):
        _, sent = self.ask("List all registered tables and their row counts, then compute total amount.", [
            call("inspect_schema"), call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total amount is 101.00.")])
        self.assertTrue(sent)

    def test_a_schema_samples_and_defensive_copy(self):
        result = self.w.schema("sales")
        self.assertEqual(result["row_count"], 4)
        self.assertEqual(result["rows"], 4)
        self.assertIn("sample", result)
        self.assertLessEqual(len(result["sample"]), 3)
        all_tables = self.w.schema()
        all_tables["tables"][0]["row_count"] = 999
        self.assertEqual(self.w.catalog["sales"]["row_count"], 4)

    def test_a_loaded_metadata_is_a_defensive_copy(self):
        path = self.root / "new.csv"
        path.write_text("code,value\na,1\nb,2\n")
        loaded = self.w.load(path)
        table = loaded["tables"][0]["table_id"]
        loaded["tables"][0]["row_count"] = 900
        self.assertEqual(self.w.catalog[table]["row_count"], 2)

    def test_a_empty_inventory_is_complete(self):
        w = Workspace(self.root / "empty", restore=False)
        self.addCleanup(w.db.close)
        with patch("local_analyst.agent.requests.post", side_effect=AssertionError("Metadata inventory must not invoke inference")) as post:
            result = AnalystAgent(w).ask("List all registered tables and their row counts. Do not run SQL.")
        post.assert_not_called()
        self.assertEqual(result["status"], "answered")
        self.assertIn("0", result["answer"])

    def test_b_preview_bounds_reject_zero_bool_float_and_string(self):
        for limit in [0, -1, 101, True, 1.5, "3"]:
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, "max_rows"):
                self.w.query("SELECT id FROM sales", max_rows=limit)

    def test_b_preview_and_full_result_are_distinct(self):
        q = self.w.query("SELECT id FROM sales ORDER BY id", max_rows=1)
        self.assertEqual(q["result_digest"]["row_count"], 4)
        self.assertEqual(len(q["rows"]), 1)
        self.assertTrue(q["truncated"])
        self.assertEqual(len(self.w.query_result_frame(q["query_id"])), 4)

    def test_b_multiple_select_error_explains_separate_call_recovery(self):
        with self.assertRaisesRegex(ValueError, "separate query_sql calls"):
            self.w.query("SELECT COUNT(*) FROM sales; SELECT SUM(amount) FROM sales")

    def test_b_aggregate_oracle(self):
        q = self.w.query("SELECT COUNT(*),SUM(amount),AVG(amount),MIN(amount),MAX(amount) FROM sales")
        self.assertEqual(q["rows"][0], [4, Decimal("101.00"), 25.25, Decimal("10.10"), Decimal("40.40")])
        groups = self.w.query("SELECT customer,COUNT(*) AS n,SUM(amount) AS total FROM sales WHERE customer IS NOT NULL GROUP BY customer HAVING COUNT(*)>1 ORDER BY total DESC")
        self.assertEqual(groups["rows"], [["B", 2, Decimal("50.50")]])

    def test_b_dates_conditional_and_window_oracle(self):
        q = self.w.query("SELECT id,amount,SUM(amount) OVER(ORDER BY posted,id ROWS UNBOUNDED PRECEDING) AS cumulative FROM sales ORDER BY posted,id")
        self.assertEqual([r[2] for r in q["rows"]], [Decimal(x) for x in ("10.10", "30.30", "60.60", "101.00")])
        q = self.w.query("SELECT SUM(CASE WHEN CAST(posted AS DATE) < CAST($end AS DATE) THEN amount ELSE 0 END), COUNT(*) FROM sales", {"end": "2026-07-01"})
        self.assertEqual(q["rows"], [[Decimal("30.30"), 4]])

    def test_b_wrong_literal_counts_are_not_source_evidence(self):
        q = self.w.query("SELECT table_id,COUNT(*) AS row_count FROM (VALUES ('sales'),('customers')) AS t(table_id) GROUP BY table_id")
        self.assertEqual(q["dataset_versions"], {})
        result, _ = self.ask("Calculate sales row count.", [call("query_sql", sql="SELECT COUNT(*) AS row_count FROM (VALUES ('sales')) AS t(table_id)"), final("Sales has 1 row.")])
        self.assertEqual(result["status"], "incomplete")

    def test_c_join_multiplication_and_missing_keys_oracle(self):
        j = self.w.join_check("sales", "customers", ["customer"], ["customer"], persist=False)
        self.assertEqual(j["inner_join_rows"], 5)
        self.assertEqual(j["left_join_rows"], 6)
        self.assertEqual(j["left"]["null_rows"], 1)
        self.assertEqual(j["right_unmatched_rows"], 1)
        self.assertEqual(j["cardinality"], "many_to_many")
        q = self.w.query("SELECT SUM(s.amount) AS inflated FROM sales s JOIN customers c ON s.customer=c.customer")
        self.assertEqual(q["rows"], [[Decimal("111.10")]])
        self.assertTrue(q["warnings"])

    def test_c_unrelated_keys_do_not_create_foreign_key(self):
        self.w.register(pd.DataFrame({"customer": ["X", "Y"]}), "unrelated", {"kind": "fixture"})
        j = self.w.join_check("sales", "unrelated", ["customer"], ["customer"], persist=False)
        self.assertEqual(j["status"], "rejected_no_overlap")
        self.assertFalse(j["left_fk_to_right"])

    def test_d_reconciliation_filters_parameters_per_statement(self):
        result = self.w.reconcile("SELECT SUM(amount) FROM sales WHERE amount > $minimum", "SELECT CAST($control AS DECIMAL(18,2))", {"minimum": 0, "control": "101.00"})
        self.assertTrue(result["passed"])
        self.assertEqual(result["difference"], "0.00")

    def test_d_reconciliation_rejects_nonfinite_tolerance(self):
        for tolerance in ["NaN", "Infinity", "-Infinity"]:
            with self.subTest(tolerance=tolerance), self.assertRaisesRegex(ValueError, "finite|tolerance"):
                self.w.reconcile("SELECT 1", "SELECT 999", tolerance=tolerance)

    def test_d_reconciliation_does_not_silently_drop_unknown_parameters(self):
        with self.assertRaisesRegex(ValueError, "Unused|unused"):
            self.w.reconcile("SELECT 1", "SELECT $control", {"control": 1, "typo": 123})

    def test_d_plan_parameter_detection_ignores_sql_string_literals(self):
        plan = {"bindings": {"s": "sales"}, "parameters": {}, "steps": [{"id": "literal", "kind": "sql", "rule_ref": "Explicit fixture rule", "sql": "SELECT '$not_a_parameter' AS note,SUM(amount) AS total FROM {{s}}"}]}
        result = run_plan(self.w, plan)
        self.assertEqual(result["validation"]["status"], "validated")
        self.assertEqual(self.w.frames[result["outputs"]["literal"]].total.iloc[0], Decimal("101.00"))

    def test_d_scenario_bad_metric_has_actionable_output_schemas(self):
        source = Path(__file__).parent / "examples/collections/invoices.csv"
        self.w.load(source, "collections")
        plan = json.loads((source.parent / "plan.json").read_text())
        with self.assertRaisesRegex(ValueError, "local_fee.*converted|converted.*local_fee"):
            scenario(self.w, plan, {"rate": "0.2"}, "fees", "local_fee")
        self.assertFalse(any(t.startswith("model_scenario_") for t in self.w.catalog))

    def test_d_model_outputs_expose_column_names_and_scenario_metric_versions(self):
        source = Path(__file__).parent / "examples/collections/invoices.csv"
        self.w.load(source, "collections")
        plan = json.loads((source.parent / "plan.json").read_text())
        result = run_plan(self.w, plan)
        self.assertIn("local_fee", result["output_schemas"]["converted"]["columns"])
        result = scenario(self.w, plan, {"rate": "0.2"}, "converted", "local_fee")
        self.assertEqual([Decimal(result[k]) for k in ("baseline", "scenario", "delta")], [35,70,35])
        self.assertEqual(result["metric"]["column"], "local_fee")
        self.assertTrue(result["dataset_versions"])

    def test_d_missing_rules_return_blocked_without_success(self):
        plan = {"parameters": {"rate": {"type": "number"}}, "steps": [{"id": "x", "kind": "sql", "rule_ref": "unapproved", "sql": "SELECT 1"}]}
        path = self.root / "plan.json"
        path.write_text(json.dumps(plan))
        plan_id = self.w.register_plan(path)
        result, _ = self.ask("Run the registered commission plan.", [call("run_model", plan_id=plan_id), final("Commission calculation completed successfully.")])
        self.assertIn(result["status"], {"blocked", "incomplete"})
        self.assertNotIn("completed successfully", result["answer"])

    def test_e_missing_quality_step_prevents_completion(self):
        result, _ = self.ask("Calculate total amount and check missing customer values.", [
            call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 101.00. Analysis complete.")])
        self.assertEqual(result["status"], "incomplete")

    def test_e_completed_quality_step_allows_answer(self):
        result, _ = self.ask("Calculate total amount and check missing customer values.", [
            call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"),
            call("check_quality", table_id="sales", kind="missing", columns=["customer"]), final("Total is 101.00; 1 row has a missing customer.")])
        self.assertEqual(result["status"], "answered")

    def test_e_false_scalar_claim_is_withheld(self):
        result, _ = self.ask("Calculate total amount.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total amount is 999.00.")])
        self.assertEqual(result["status"], "incomplete")
        self.assertNotIn("999.00", result["answer"])

    def test_e_technical_identifier_does_not_block_verified_scalar(self):
        result, _ = self.ask("Calculate total amount.", [call("inspect_schema", table_id="sales"), call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 101.00. Dates shown use ISO-8601 notation.")])
        self.assertEqual(result["status"], "answered")

    def test_e_null_scalar_cannot_be_reported_as_zero(self):
        result, _ = self.ask("Calculate the total amount for July 2027.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales WHERE posted >= '2027-07-01'"), final("The total amount is 0.")])
        self.assertEqual(result["status"], "incomplete")

    def test_e_result_row_count_cannot_replace_source_count(self):
        result, _ = self.ask("Count sales records.", [call("inspect_schema", table_id="sales"), call("query_sql", sql="SELECT COUNT(*) AS n FROM sales"), final("Sales has 1 record.")])
        self.assertEqual(result["status"], "incomplete")

    def test_e_verified_schema_statistics_and_samples_remain_valid_evidence(self):
        result, _ = self.ask("Calculate total amount and summarize the schema statistics.", [
            call("inspect_schema", table_id="sales"), call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"),
            final("Total amount is 101.00. Customer has 1 missing value and 2 distinct non-null values. A sampled amount is 20.20.")])
        self.assertEqual(result["status"], "answered")

    def test_e_schema_null_count_cannot_replace_registered_row_count(self):
        result, _ = self.ask("Count sales records using schema metadata.", [call("inspect_schema", table_id="sales"), final("Sales has 1 record.")])
        self.assertEqual(result["status"], "incomplete")

    def test_e_count_answer_can_include_verified_missing_stats(self):
        result, _ = self.ask("Count sales records and report schema statistics.", [
            call("inspect_schema", table_id="sales"), call("query_sql", sql="SELECT COUNT(*) AS n FROM sales"),
            final("Sales has 4 records. Customer has 1 missing value and 2 distinct non-null values.")])
        self.assertEqual(result["status"], "answered")

    def test_e_multi_step_period_and_ranking_cannot_be_omitted(self):
        result, _ = self.ask("Calculate monthly revenue, find the top five customers, and check missing customer values.", [
            call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"),
            call("check_quality", table_id="sales", kind="missing", columns=["customer"]),
            final("Total is 101.00. There is 1 missing customer. Analysis completed.")])
        self.assertEqual(result["status"], "incomplete")

    def test_e_month_labels_are_not_unverified_money_claims(self):
        result, _ = self.ask("Calculate monthly amount totals.", [call("query_sql", sql="SELECT STRFTIME(CAST(posted AS DATE),'%Y-%m') AS month,SUM(amount) AS total FROM sales GROUP BY month ORDER BY month"), final("2026-06: 30.30\n2026-07: 70.70")])
        self.assertEqual(result["status"], "answered")

    def test_e_explicit_sql_request_cannot_be_satisfied_by_metadata_only(self):
        self.agent.max_steps = 2
        result, _ = self.ask("Count sales records; execute SQL to verify.", [call("inspect_schema", table_id="sales"), final("Sales has 4 records.")])
        self.assertEqual(result["status"], "incomplete")

    def test_e_sql_omission_gets_one_bounded_repair(self):
        result, _ = self.ask("Count sales records; execute SQL to verify.", [call("inspect_schema", table_id="sales"), final("Sales has 4 records."), call("query_sql", sql="SELECT COUNT(*) AS n FROM sales"), final("Sales has 4 records.")])
        self.assertEqual(result["status"], "answered")
        self.assertTrue(any(t["tool"] == "query_sql" for t in result["trace"]))
        self.assertLessEqual(result["steps"], 8)

    def test_f_unknown_citation_is_not_accepted(self):
        result, _ = self.ask("Calculate total amount.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 101.00. Evidence: deadbeefdeadbeef")])
        self.assertEqual(result["status"], "incomplete")

    def test_f_noncanonical_fabricated_query_citation_is_not_accepted(self):
        result, _ = self.ask("Calculate total amount.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 101.00. Query_id: query_12345")])
        self.assertEqual(result["status"], "incomplete")

    def test_f_current_query_evidence_is_usable_by_a_fresh_agent(self):
        query = self.w.query("SELECT SUM(amount) AS total FROM sales")
        result, _ = self.ask("Report the total from this current query evidence.", [call("inspect_query_evidence", query_id=query["query_id"]), final("Total is 101.00. Evidence: " + query["query_id"])])
        self.assertEqual(result["status"], "answered")
        self.assertTrue(result["evidence"])

    def test_f_actual_digest_prefix_is_not_an_unknown_query_id(self):
        result, _ = self.ask("Calculate total amount.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), lambda: final("Total is 101.00, digest `" + self.w.query_evidence(self.agent._query_ids[-1])["result_digest"]["sha256"][:16] + "…`.")])
        self.assertEqual(result["status"], "answered")

    def test_f_digest_prefix_cannot_be_substituted_for_query_id(self):
        result, _ = self.ask("Calculate total amount.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), lambda: final("Total is 101.00. Query_id: " + self.w.query_evidence(self.agent._query_ids[-1])["result_digest"]["sha256"][:16])])
        self.assertEqual(result["status"], "incomplete")

    def test_e_executed_sql_expression_arguments_are_not_result_claims(self):
        result, _ = self.ask("Calculate monthly amount totals.", [call("query_sql", sql="SELECT SUBSTR(posted,1,7) AS month,SUM(amount) AS total FROM sales GROUP BY month ORDER BY month"), final("2026-06: 30.30\n2026-07: 70.70\nMonth uses `substr(posted,1,7)`.")])
        self.assertEqual(result["status"], "answered")

    def test_f_truncated_all_rows_answer_is_incomplete(self):
        result, _ = self.ask("Show all sales rows.", [call("query_sql", sql="SELECT id,amount FROM sales", max_rows=1), final("Here are all rows: 1, 10.10.")])
        self.assertEqual(result["status"], "incomplete")

    def test_g_no_automatic_exports_and_overwrite_rejected(self):
        result, _ = self.ask("Calculate total amount.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 101.00.")])
        self.assertEqual(result["status"], "answered")
        self.assertFalse((self.w.root / "outputs").exists())
        q = result["trace"][0]["result"]
        out = export_query(self.w, q["query_id"], "fresh", excel=False)
        before = Path(out["csv"]).read_bytes()
        with self.assertRaisesRegex(ValueError, "exists"):
            export_query(self.w, q["query_id"], "fresh", excel=False)
        self.assertEqual(Path(out["csv"]).read_bytes(), before)

    def test_g_explicit_export_in_dependent_clause_is_authorized(self):
        from local_analyst.agent import export_intent
        formats, _ = export_intent("Use the registered plan. Query the converted rows and export those rows to CSV, Excel and PDF.")
        self.assertEqual(set(formats), {"csv", "excel", "pdf"})
        self.assertEqual(export_intent("Explain how I can query data and export to CSV.")[0], [])

    def test_g_embedded_export_creates_actual_artifact(self):
        result, _ = self.ask("Calculate total amount, then export the result to CSV.", [
            call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"),
            lambda: call("export_result", query_id=self.agent._query_ids[-1], formats=["csv"], name="embedded"),
            final("Exported total amount 101.00 to CSV.")])
        export = result["trace"][1]["result"]
        self.assertEqual(export.get("status"), "exported")
        self.assertEqual(pd.read_csv(export["csv"]).total.iloc[0], 101)
        self.assertEqual(result["status"], "answered")

    def test_g_embedded_unperformed_export_is_incomplete(self):
        result, _ = self.ask("Calculate total amount, then export the result to CSV.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total amount is 101.00.")])
        self.assertEqual(result["status"], "incomplete")

    def test_b_explicit_decimal_calculation_rejects_uncast_float_aggregation(self):
        self.w.register(pd.DataFrame({"gross": [0.1, 0.2]}), "floating", {"kind": "fixture"})
        result, _ = self.ask("Use exact decimal amounts to calculate total gross.", [
            call("query_sql", sql="SELECT SUM(gross) AS total FROM floating"),
            call("query_sql", sql="SELECT SUM(CAST(gross AS DECIMAL(18,2))) AS total FROM floating"), final("Total gross is 0.30.")])
        self.assertEqual(result["trace"][0]["result"]["status"], "error")
        self.assertIn("DECIMAL", result["trace"][0]["result"]["error"])
        self.assertEqual(result["trace"][1]["result"]["rows"], [[Decimal("0.30")]])
        self.assertEqual(result["status"], "answered")

    def test_h_unexecuted_promise_is_incomplete(self):
        result, _ = self.ask("Calculate total amount.", [final("I will run SQL to calculate the total. Would you like me to proceed?")])
        self.assertEqual(result["status"], "incomplete")

    def test_h_repeated_identical_errors_stop_before_step_budget(self):
        messages = [call("query_sql", sql="SELECT * FROM imaginary")] * 8
        result, sent = self.ask("Calculate total amount.", messages)
        self.assertEqual(result["status"], "incomplete")
        self.assertLess(len(sent), 8)
        self.assertIn("progress", result["answer"].lower())

    def test_h_call_budget_returns_structured_incomplete(self):
        excessive = final("")
        excessive["tool_calls"] = call("inspect_schema")["tool_calls"] * 9
        result, _ = self.ask("Inspect schemas.", [excessive])
        self.assertEqual(result["status"], "incomplete")

    def test_h_model_timeout_returns_incomplete_and_preserves_trace(self):
        response = Mock()
        response.json.return_value = {"message": call("query_sql", sql="SELECT COUNT(*) AS n FROM sales")}
        with patch("local_analyst.agent.requests.post", side_effect=[response, requests.Timeout("controlled local timeout")]):
            result = self.agent.ask("Count sales records.")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(result["trace"]), 1)
        self.assertIn("controlled local timeout", result["error"])
        events = [json.loads(line) for line in self.w.audit_path.read_text().splitlines()]
        self.assertTrue(any(e["kind"] == "agent_stopped" and e.get("status") == "incomplete" for e in events))

    def test_h_malformed_tool_call_is_reported_without_crashing(self):
        bad = {"role": "assistant", "content": "", "tool_calls": [17]}
        result, _ = self.ask("Inspect schemas.", [bad, final("Inspection completed.")])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["trace"][0]["result"]["status"], "error")

    def test_h_successful_delivery_recovery_is_not_rejected(self):
        actual_schema = self.agent.functions["inspect_schema"]
        self.agent.functions["inspect_schema"] = lambda **args: actual_schema(**args) if args else {"oversized": "X" * 46000}
        result, _ = self.ask("Inspect sales columns and row count.", [call("inspect_schema"), call("inspect_schema", table_id="sales"), final("Sales has 4 rows.")])
        self.assertEqual(result["status"], "answered")

    def test_h_legal_constant_calculation_does_not_require_dataset_sources(self):
        result, _ = self.ask("Calculate 2 + 2.", [call("query_sql", sql="SELECT 2+2 AS answer"), final("The result is 4.")])
        self.assertEqual(result["status"], "answered")

    def test_h_no_sql_instruction_is_enforced_for_non_inventory_requests(self):
        result, sent = self.ask("Inspect sales columns and row count. Do not run SQL.", [call("query_sql", sql="SELECT COUNT(*) FROM sales"), final("Unable to execute SQL under the requested restriction.")])
        self.assertNotIn("query_sql", [t["function"]["name"] for t in sent[0]["tools"]])
        self.assertEqual(result["trace"][0]["result"]["status"], "error")
        self.assertFalse(self.w.queries)

    def test_h_new_model_outputs_are_queried_in_a_later_turn(self):
        plan = {"bindings": {"s": "sales"}, "parameters": {}, "steps": [{"id": "total", "kind": "sql", "rule_ref": "Explicit test policy", "sql": "SELECT SUM(amount) AS total FROM {{s}}"}]}
        path = self.root / "plan.json"; path.write_text(json.dumps(plan)); plan_id = self.w.register_plan(path)
        batch = call("run_model", plan_id=plan_id)
        batch["tool_calls"] += call("query_sql", sql="SELECT total FROM model_baseline_total")["tool_calls"]
        result, _ = self.ask("Run the registered plan and report total amount.", [batch, call("query_sql", sql="SELECT total FROM model_baseline_total"), final("Total amount is 101.00.")])
        self.assertEqual(result["trace"][1]["result"]["status"], "error")
        self.assertIn("separate assistant turn", result["trace"][1]["result"]["error"])
        self.assertEqual(result["status"], "answered")

    def test_h_sql_error_recovery_uses_actual_results(self):
        result, _ = self.ask("Calculate total amount.", [call("query_sql", sql="SELECT SUM( FROM sales"), call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 101.00.")])
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["trace"][0]["result"]["status"], "error")

    def test_h_unknown_tool_error_cannot_be_called_complete(self):
        result, _ = self.ask("Calculate total amount.", [call("does_not_exist"), final("Completed successfully.")])
        self.assertEqual(result["status"], "incomplete")

    def test_h_oversized_tool_delivery_cannot_be_called_complete(self):
        self.agent.functions["inspect_schema"] = lambda: {"tables": [], "oversized": "X" * 46000}
        result, sent = self.ask("Inspect the schemas.", [call("inspect_schema"), final("Schema inspection completed successfully.")])
        self.assertFalse(json.loads(sent[-1]["messages"][-1]["content"])["ok"])
        self.assertEqual(result["status"], "incomplete")

    def test_h_followup_uses_prior_evidence_and_new_filter(self):
        first, _ = self.ask("What is total amount?", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 101.00.")])
        second, sent = self.ask("For that table, now include only June by posted date.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales WHERE posted < '2026-07-01'"), final("June total is 30.30.")])
        self.assertEqual(first["status"], "answered")
        self.assertEqual(second["status"], "answered")
        self.assertTrue(any(m.get("content") == "What is total amount?" for m in sent[0]["messages"]))

    def test_h_followup_can_cite_current_persisted_schema_evidence(self):
        first, _ = self.ask("Inspect sales schema.", [call("inspect_schema", table_id="sales"), final("Sales has 4 records.")])
        schema_id = first["trace"][0]["result"]["evidence_id"]
        result, _ = self.ask("Now calculate total amount for that table.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 101.00. Schema evidence: " + schema_id)])
        self.assertEqual(result["status"], "answered")

    def test_h_stale_schema_evidence_is_not_accepted_in_followup(self):
        first, _ = self.ask("Inspect sales schema.", [call("inspect_schema", table_id="sales"), final("Sales has 4 records.")])
        schema_id = first["trace"][0]["result"]["evidence_id"]
        frame = self.w.frames["sales"].copy(); frame["amount"] = frame["amount"] + Decimal(1)
        self.w.register(frame, "sales", {"kind": "fixture"})
        result, _ = self.ask("Calculate current total amount.", [call("query_sql", sql="SELECT SUM(amount) AS total FROM sales"), final("Total is 105.00. Schema evidence: " + schema_id)])
        self.assertEqual(result["status"], "incomplete")

    def test_i_unapproved_model_id_rejected(self):
        result, _ = self.ask("Run commission model.", [call("run_model", plan_id="invented"), final("The calculation is done.")])
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("user-registered", result["trace"][0]["result"]["error"])

    def test_i_inability_after_data_inspection_is_not_successful_completion(self):
        result, _ = self.ask("Calculate approved commissions after inspecting available data.", [call("query_sql", sql="SELECT COUNT(*) AS n FROM sales"), final("Sales has 4 records, but I cannot calculate approved commissions without compensation rules.")])
        self.assertIn(result["status"], {"incomplete", "blocked"})
        self.assertIn("compensation rules", result["answer"])

    def test_i_sql_safety_and_original_frames_preserved(self):
        original = self.w.frames["sales"].copy(deep=True)
        for sql in ["UPDATE sales SET amount=0", "SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM sales CROSS JOIN customers"]:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                self.w.query(sql)
        pd.testing.assert_frame_equal(original, self.w.frames["sales"])

    def test_i_missing_money_is_not_imputed(self):
        from local_analyst.finance import convert_currency, marginal_tiers
        for function, args in [(convert_currency, (100, None)), (marginal_tiers, (None, '.1'))]:
            with self.assertRaises(ValueError):
                function(*args)

    def test_i_numeric_grounding_evaluator_rejects_answer_only_success(self):
        fake = {"status": "answered", "answer": "107052 Evidence: fake", "trace": [{"tool": "query_sql", "result": {"query_id": "fake", "rows": [[999]]}}]}
        self.assertEqual(grade("app_count", fake, self.w)["status"], "FAIL")

    def test_i_evaluator_accepts_sentence_final_numeric_punctuation(self):
        from examples.reliability_audit import answer_numbers
        self.assertEqual(answer_numbers("Total is 230.00. Count is 107,052. Evidence: abcdef1234567890"), {Decimal("230.00"), Decimal(107052)})

    def test_i_evaluator_rejects_literal_constant_as_count_verification(self):
        self.w.catalog["sales"]["label"] = "9. App Export"
        q = self.w.query("SELECT 107052 AS n FROM sales LIMIT 1")
        fake = {"status": "answered", "answer": "107052 Evidence: " + q["query_id"], "trace": [{"tool": "query_sql", "result": q}]}
        self.assertEqual(grade("app_count", fake, self.w)["status"], "FAIL")

    def test_i_uncertainty_evaluator_allows_descriptive_inspection(self):
        query = self.w.query("SELECT id,posted FROM sales")
        result = {"status": "blocked", "answer": "Cannot calculate approved payroll commissions: compensation rules are missing.", "trace": [{"tool": "query_sql", "arguments": {"sql": "SELECT id,posted FROM sales"}, "result": query}]}
        self.assertEqual(grade("uncertainty", result, self.w)["status"], "PASS")

    def test_i_comparison_runner_uses_current_version_protocol(self):
        from examples import compare_models
        response = Mock(); response.json.return_value = {"models": []}
        with tempfile.TemporaryDirectory() as temp, patch.object(compare_models, "check_model", return_value={"available": True}), patch.object(compare_models.requests, "get", return_value=response), patch.object(compare_models, "Workspace", side_effect=RuntimeError("current protocol validated")):
            with self.assertRaisesRegex(RuntimeError, "current protocol validated"):
                compare_models.main(["--output", temp, "--baseline-model", "a:tag", "--test-model", "b:tag"])


class ReliabilityWorkbookTests(unittest.TestCase):
    def test_b_csv_decimal_lexemes_preserve_cents_and_large_values(self):
        with tempfile.TemporaryDirectory() as temp:
            w = Workspace(Path(temp) / "workspace", restore=False)
            self.addCleanup(w.db.close)
            small = Path(temp) / "small.csv"; small.write_text("code,amount\na,0.10\nb,0.20\n")
            table = w.load(small)["tables"][0]["table_id"]
            self.assertEqual(w.query('SELECT SUM(amount) FROM "' + table + '"')["rows"], [[Decimal("0.30")]])
            self.assertEqual(next(c["dtype"] for c in w.schema(table)["columns"] if c["name"] == "amount"), "decimal")
            large = Path(temp) / "large.csv"; large.write_text("code,amount\nx,10000000000000000.01\n")
            table = w.load(large)["tables"][0]["table_id"]
            self.assertEqual(w.query('SELECT SUM(amount) FROM "' + table + '"')["rows"], [[Decimal("10000000000000000.01")]])

    def test_a_clipboard_inventory_and_app_count_with_independent_fixture_oracle(self):
        with tempfile.TemporaryDirectory() as temp:
            w = Workspace(temp, restore=False)
            self.addCleanup(w.db.close)
            w.load(Path(__file__).parent / "data/Sales Compensation Analyst Case Study.xlsx", "reliability")
            expected = {"1. Sales Roster": 80, "2. HR Export": 1080, "3. Exchange Rates": 196,
                        "4. Ramp Terms": 17, "5. Compensation": 17,
                        "6. Accelerators / region 1": 2, "6. Accelerators / region 2": 2,
                        "7. Opportunity to ID Mapping": 761, "8. Opportunity Export": 815,
                        "9. App Export": 107052, "10. Billing Export": 1089, "11. Slack Scrape": 59}
            self.assertEqual({t["label"]: t["row_count"] for t in w.schema()["tables"]}, expected)
            with patch("local_analyst.agent.requests.post", side_effect=AssertionError("Metadata inventory must not invoke inference")) as post:
                result = AnalystAgent(w).ask("List all registered tables and their row counts. Do not run SQL.")
            post.assert_not_called()
            self.assertEqual(grade("inventory", result, w)["status"], "PASS")
            table = next(t for t, m in w.catalog.items() if m["label"] == "9. App Export")
            self.assertEqual(w.query('SELECT COUNT(*) FROM "' + table + '"')["rows"], [[107052]])

    def test_a_synthetic_xlsx_logical_regions_differ_from_sheet_count(self):
        with tempfile.TemporaryDirectory() as temp:
            from openpyxl import Workbook
            path = Path(temp) / "unfamiliar.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.append(["id", "amount"]); sheet.append(["a", 10]); sheet.append([None, None])
            sheet.append(["code", "rate"]); sheet.append(["USD", 1]); sheet.append(["EUR", 2])
            book.save(path)
            w = Workspace(Path(temp) / "workspace", restore=False)
            self.addCleanup(w.db.close)
            result = w.load(path)
            self.assertEqual([m["row_count"] for m in result["tables"]], [1, 2])
            self.assertEqual(len(result["tables"]), 2)

    def test_a_duplicate_csv_headers_require_explicit_renaming(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.csv"
            path.write_text("amount,amount\n1,2\n")
            w = Workspace(Path(temp) / "workspace", restore=False)
            self.addCleanup(w.db.close)
            with self.assertRaisesRegex(ValueError, "Duplicate column"):
                w.load(path)


if __name__ == "__main__":
    unittest.main()
