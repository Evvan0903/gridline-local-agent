import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

import revenue_data_model as revenue


def fixture_sources():
    """Tiny independent, explicit expected-dollar example, not production data."""
    opportunity = {
        "crm_account_id": "c1", "crm_owner_id": "am1", "closed_by_owner_id": "ae1",
        "account_type": "GTM", "segment": "SMB", "charge_rate_per_shift": 10.25,
        "pay_rate_per_shift": 7.10, "net_terms": 30,
    }
    opportunities = pd.DataFrame([
        {**opportunity, "opportunity_id": "residue", "opportunity_stage": "Closed Lost",
         "closed_won_date": pd.Timestamp("2025-01-01"), "effective_start_date": pd.Timestamp("2025-02-01")},
        {**opportunity, "opportunity_id": "first", "opportunity_stage": "Closed Won",
         "closed_won_date": pd.Timestamp("2025-10-01"), "effective_start_date": pd.Timestamp("2025-11-01")},
        {**opportunity, "opportunity_id": "renewal", "opportunity_stage": "Closed Won",
         "closed_won_date": pd.Timestamp("2026-05-01"), "effective_start_date": pd.Timestamp("2026-05-05")},
    ])
    shifts = pd.DataFrame({
        "app_account_id": ["a1"] * 3, "shift_id": ["s1", "s2", "s3"],
        "shift_posted_date": pd.to_datetime(["2026-05-28", "2026-06-01", "2026-06-20"]),
        "worked_shift": [True, False, True],
        "shift_worked_date": pd.to_datetime(["2026-06-03", None, "2026-06-30"]),
        "account_first_worked_shift_date": pd.to_datetime(["2025-11-05"] * 3),
    })
    sources = {
        "mapping": pd.DataFrame({"crm_account_id": ["c1"], "app_account_id": ["a1"], "billing_account_id": ["b1"]}),
        "opportunities": opportunities, "shifts": shifts,
        "roster": pd.DataFrame({"crm_owner_id": ["ae1", "am1"], "hr_id": ["h1", "h2"], "name": ["AE", "AM"]}),
        "hr": pd.DataFrame({"hr_id": ["h1", "h2"], "Current Role": ["SMB Account Executive", "SMB Account Manager"]}),
        "billing": pd.DataFrame({"shift_id": ["s1"], "billing_account_id": ["b1"],
                                 "credit_amount": [1], "ticket_status": ["approved"],
                                 "ticket_type": ["promotion"], "billing_month": ["2026-06"]}),
    }
    for frame in sources.values():
        frame["source_row"] = range(2, len(frame) + 2)
    return sources


class RevenueFunctionTests(unittest.TestCase):
    def setUp(self):
        self.sources, self.issues = fixture_sources(), []

    def build(self):
        accounts, rejected = revenue.build_account_master(self.sources, self.issues)
        fact = revenue.build_shift_fact(self.sources, accounts, self.issues)
        return accounts, fact, rejected

    def test_stage_filter_earliest_go_live_and_owners(self):
        accounts, fact, _ = self.build()
        account = accounts.iloc[0]
        self.assertEqual(account.earliest_opportunity_id, "first")
        self.assertEqual(account.go_live_date, pd.Timestamp("2025-11-01"))
        self.assertEqual(account.closing_ae_crm_owner_id, "ae1")
        self.assertEqual(account.current_am_crm_owner_id, "am1")
        self.assertEqual(account.closing_ae_hr_id, "h1")
        self.assertEqual(account.current_am_hr_id, "h2")
        self.assertEqual(account.earliest_opportunity_source_row, 3)
        self.assertEqual(len(fact), 3)  # three opportunities never become nine shifts

    def test_money_cross_month_and_unfilled_gsv(self):
        _, fact, _ = self.build()
        self.assertEqual(fact.base_net_revenue_cents.tolist(), [315, 0, 315])
        self.assertEqual(fact.gross_services_value_cents.tolist(), [1025, 1025, 1025])
        self.assertEqual(fact.iloc[0].net_revenue_recognition_date, pd.Timestamp("2026-06-03"))
        self.assertEqual(fact.iloc[0].gsv_recognition_date, pd.Timestamp("2026-05-28"))
        summary = revenue.summarize_monthly_revenue(fact).set_index("month")
        self.assertEqual(summary.loc["2026-05", "base_net_revenue_cents"], 0)
        self.assertEqual(summary.loc["2026-06", "base_net_revenue_cents"], 630)
        self.assertEqual(summary.loc["2026-05", "gross_services_value_cents"], 1025)
        # Billing credit, net terms and GTM classification do not change base margins.
        self.assertEqual(int(fact.base_net_revenue_cents.sum()), 630)

    def test_first_worked_is_not_extract_minimum(self):
        accounts, fact, _ = self.build()
        self.assertEqual(accounts.iloc[0].account_first_worked_shift_date, pd.Timestamp("2025-11-05"))
        self.assertTrue(fact.account_first_worked_shift_date.eq(pd.Timestamp("2025-11-05")).all())
        self.assertNotEqual(fact.shift_worked_date.min(), accounts.iloc[0].account_first_worked_shift_date)

    def test_posted_before_first_worked_still_generates_gsv(self):
        self.sources["shifts"]["account_first_worked_shift_date"] = pd.Timestamp("2026-06-03")
        _, fact, _ = self.build()
        self.assertEqual(fact.iloc[0].gross_services_value_cents, 1025)

    def test_tied_wins_never_use_row_order_tiebreak(self):
        self.sources["opportunities"].loc[2, "closed_won_date"] = pd.Timestamp("2025-10-01")
        accounts, fact, _ = self.build()
        self.assertTrue(pd.isna(accounts.iloc[0].go_live_date))
        self.assertTrue(pd.isna(accounts.iloc[0].earliest_opportunity_id))
        self.assertEqual(accounts.iloc[0].earliest_opportunity_status, "tied_earliest_closed_won")
        # Independently verified account rates can still support raw revenue.
        self.assertEqual(int(fact.base_net_revenue_cents.sum()), 630)

    def test_blank_closer_never_falls_back_to_am(self):
        self.sources["opportunities"].loc[1, "closed_by_owner_id"] = None
        accounts, _, _ = self.build()
        self.assertTrue(pd.isna(accounts.iloc[0].closing_ae_crm_owner_id))
        self.assertEqual(accounts.iloc[0].closing_ae_status, "unassigned_by_plan")
        self.assertEqual(accounts.iloc[0].current_am_crm_owner_id, "am1")

    def test_missing_win_date_or_effective_date_stays_unresolved(self):
        for column in ("closed_won_date", "effective_start_date"):
            with self.subTest(column=column):
                self.sources = fixture_sources()
                self.sources["opportunities"].loc[1, column] = pd.NaT
                accounts, _, _ = self.build()
                self.assertTrue(pd.isna(accounts.iloc[0].go_live_date))
                self.assertEqual(accounts.iloc[0].go_live_status, "unresolved")

    def test_duplicate_opportunity_id_never_selects_acquisition(self):
        self.sources["opportunities"].loc[2, "opportunity_id"] = "first"
        accounts, _, _ = self.build()
        self.assertTrue(pd.isna(accounts.iloc[0].earliest_opportunity_id))
        self.assertEqual(accounts.iloc[0].earliest_opportunity_status, "ambiguous_opportunity_identity")
        self.assertEqual(accounts.iloc[0].closing_ae_status, "unresolved")

    def test_conflicting_first_worked_dates_preserve_every_source_value(self):
        self.sources["shifts"].loc[0, "account_first_worked_shift_date"] = pd.Timestamp("2025-10-01")
        accounts, fact, _ = self.build()
        self.assertTrue(pd.isna(accounts.iloc[0].account_first_worked_shift_date))
        pd.testing.assert_series_equal(fact.account_first_worked_shift_date,
                                       self.sources["shifts"].account_first_worked_shift_date)
        self.assertIn("conflicting_first_worked_date", [issue["code"] for issue in self.issues])

    def test_conflicting_account_rates_and_am_are_unresolved(self):
        self.sources["opportunities"].loc[2, "charge_rate_per_shift"] = 15
        self.sources["opportunities"].loc[2, "crm_owner_id"] = "am2"
        accounts, fact, _ = self.build()
        self.assertTrue(pd.isna(accounts.iloc[0].charge_rate_per_shift))
        self.assertTrue(pd.isna(accounts.iloc[0].current_am_crm_owner_id))
        self.assertTrue(fact.loc[fact.worked_shift, "base_net_revenue_cents"].isna().all())
        self.assertTrue(fact.gross_services_value_cents.isna().all())

    def test_duplicate_mapping_all_rows_quarantined(self):
        self.sources["mapping"] = pd.concat([self.sources["mapping"], self.sources["mapping"]], ignore_index=True)
        self.sources["mapping"]["source_row"] = [2, 3]
        accounts, fact, rejected = self.build()
        self.assertEqual(len(accounts), 0)
        self.assertEqual(len(rejected), 2)
        self.assertEqual(len(fact), 3)
        self.assertTrue(fact.gross_services_value_cents.isna().all())
        self.assertIn("duplicate_mapping", [i["code"] for i in self.issues])

    def test_orphan_shift_and_duplicate_shift_never_disappear(self):
        self.sources["shifts"].loc[0, "app_account_id"] = "unknown"
        self.sources["shifts"].loc[2, "shift_id"] = "s2"
        _, fact, _ = self.build()
        self.assertEqual(len(fact), 3)
        self.assertTrue(fact.base_net_revenue_cents.isna().all())
        self.assertTrue(fact.gross_services_value_cents.isna().all())
        self.assertEqual(fact.shift_source_row.tolist(), [2, 3, 4])

    def test_worked_date_flag_conflict_does_not_corrupt_posted_gsv(self):
        self.sources["shifts"].loc[0, "shift_worked_date"] = pd.NaT
        _, fact, _ = self.build()
        self.assertTrue(pd.isna(fact.iloc[0].base_net_revenue_cents))
        self.assertEqual(fact.iloc[0].gross_services_value_cents, 1025)

    def test_dates_and_negative_margins_are_flagged_without_invented_floor(self):
        self.sources["opportunities"]["pay_rate_per_shift"] = 12.25
        self.sources["shifts"].loc[0, "shift_worked_date"] = pd.Timestamp("2025-01-01")
        _, fact, _ = self.build()
        self.assertEqual(fact.iloc[0].base_net_revenue_cents, -200)
        codes = [i["code"] for i in self.issues]
        self.assertIn("worked_before_supplied_first_worked", codes)
        self.assertIn("worked_before_go_live", codes)
        self.assertIn("negative_margin", codes)

    def test_exact_cents_and_invalid_precision(self):
        self.assertEqual(revenue._cents(Decimal("10.25")), 1025)
        self.assertEqual(revenue._cents(0), 0)
        for value in (10.251, "not a rate", float("inf"), None, True):
            self.assertIsNone(revenue._cents(value))

    def test_account_without_app_rows_reconciles_without_fabricating_history(self):
        mapping = self.sources["mapping"].iloc[0].copy()
        mapping.update({"crm_account_id": "c2", "app_account_id": "a2",
                        "billing_account_id": "b2", "source_row": 3})
        self.sources["mapping"] = pd.concat([self.sources["mapping"], mapping.to_frame().T], ignore_index=True)
        opportunities = self.sources["opportunities"].copy()
        opportunities["crm_account_id"] = "c2"
        opportunities["opportunity_id"] += "-second"
        opportunities["source_row"] += 3
        self.sources["opportunities"] = pd.concat([self.sources["opportunities"], opportunities], ignore_index=True)
        accounts, fact, rejected = self.build()
        self.assertEqual(len(accounts), 2)
        self.assertTrue(pd.isna(accounts.iloc[1].account_first_worked_shift_date))
        billing_links = revenue._audit_unmatched(self.sources, accounts, self.issues)
        result = revenue.reconcile_model(self.sources, accounts, fact, self.issues, rejected, billing_links)
        self.assertEqual(result["base_net_revenue_cents"], 630)
        self.assertEqual(result["gross_services_value_cents"], 3075)

    def test_functions_do_not_mutate_input_frames(self):
        before = copy.deepcopy(self.sources)
        self.build()
        for name in self.sources:
            pd.testing.assert_frame_equal(self.sources[name], before[name])

    def test_unmatched_billing_and_identity_conflict_are_ledgered(self):
        accounts, _, _ = self.build()
        self.sources["billing"].loc[0, "billing_account_id"] = "wrong"
        links = revenue._audit_unmatched(self.sources, accounts, self.issues)
        self.assertEqual(len(links), 1)
        codes = [i["code"] for i in self.issues]
        self.assertIn("unmatched_billing_account", codes)
        self.assertIn("billing_shift_account_mismatch", codes)


class WorkbookRevenueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(revenue.DEFAULT_WORKBOOK.read_bytes()).hexdigest()
        cls.model = revenue.build_revenue_model()

    def test_local_counts_and_source_lineage(self):
        model = self.model
        self.assertEqual(len(model["accounts"]), 761)
        self.assertEqual(len(model["shifts"]), 107052)
        self.assertEqual(len(model["billing_lineage"]), 1089)
        self.assertEqual(model["shifts"].shift_source_row.tolist(), list(range(2, 107054)))
        self.assertEqual(model["reconciliation"]["recognized_worked_rows"], 79048)
        self.assertEqual(model["reconciliation"]["unworked_rows"], 28004)
        self.assertEqual(model["reconciliation"]["unresolved_net_revenue_rows"], 0)
        self.assertEqual(model["reconciliation"]["unresolved_gsv_rows"], 0)
        self.assertTrue(model["reconciliation"]["all_original_shift_columns_preserved"])

    def test_totals_against_independent_raw_source_join(self):
        source = revenue.read_sources()
        attributes = source["opportunities"].groupby("crm_account_id")
        self.assertTrue(attributes.charge_rate_per_shift.nunique().eq(1).all())
        self.assertTrue(attributes.pay_rate_per_shift.nunique().eq(1).all())
        rates = attributes[["charge_rate_per_shift", "pay_rate_per_shift"]].first().reset_index()
        reference = source["shifts"].merge(source["mapping"], on="app_account_id", validate="many_to_one")
        reference = reference.merge(rates, on="crm_account_id", validate="many_to_one")
        worked = reference[reference.worked_shift]
        expected_net = int(((worked.charge_rate_per_shift - worked.pay_rate_per_shift) * 100).sum())
        expected_gsv = int((reference.charge_rate_per_shift * 100).sum())
        self.assertEqual(self.model["reconciliation"]["base_net_revenue_cents"], expected_net)
        self.assertEqual(self.model["reconciliation"]["gross_services_value_cents"], expected_gsv)
        self.assertEqual(self.model["reconciliation"]["net_revenue_reconciliation_difference_cents"], 0)
        self.assertEqual(self.model["reconciliation"]["gsv_reconciliation_difference_cents"], 0)

    def test_saved_outputs_and_hashes_and_raw_write_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "derived"
            report = Path(temp) / "revenue.md"
            revenue.write_outputs(self.model, output, report)
            manifest = json.loads((output / "manifest.json").read_text())
            for name, digest in manifest["output_sha256"].items():
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            shifts = pd.read_csv(output / "shift_fact.csv")
            self.assertEqual(len(shifts), 107052)
            self.assertEqual(int(shifts.base_net_revenue_cents.sum()), self.model["reconciliation"]["base_net_revenue_cents"])
            self.assertIn("7.3", report.read_text())
        with self.assertRaises(ValueError):
            revenue.write_outputs(self.model, revenue.DEFAULT_WORKBOOK.parent, revenue.REPORT_PATH)
        self.assertEqual(hashlib.sha256(revenue.DEFAULT_WORKBOOK.read_bytes()).hexdigest(), self.original_hash)


if __name__ == "__main__":
    unittest.main()
