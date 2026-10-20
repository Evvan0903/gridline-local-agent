"""Phase 3A read-only account/shift model, independent of the interactive agent.

All joins preserve source rows. Ambiguous source records stay unresolved.
Run with the project virtualenv to write CSVs and a reconciliation report.
"""

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from workbook_discovery import DEFAULT_WORKBOOK


BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "derived" / "phase3a"
REPORT_PATH = BASE_DIR / "reports" / "revenue_data_model.md"
DEFAULT_RULES = BASE_DIR / "Clipboard_Health_Case_Study_Rules.md"
SHEETS = {
    "mapping": "7. Opportunity to ID Mapping", "opportunities": "8. Opportunity Export",
    "shifts": "9. App Export", "billing": "10. Billing Export",
    "roster": "1. Sales Roster", "hr": "2. HR Export",
}
ACCOUNT_IDS = ["crm_account_id", "app_account_id", "billing_account_id"]
ATTRIBUTES = ["account_type", "segment", "charge_rate_per_shift", "pay_rate_per_shift", "net_terms"]


def _missing(value):
    return pd.isna(value) or (isinstance(value, str) and value == "")


def _valid_date(value):
    return isinstance(value, (date, datetime, pd.Timestamp, np.datetime64)) and pd.notna(value)


def _date_mask(values):
    # Series.map retains a datetime dtype for an empty Series in pandas 3.
    return pd.Series([_valid_date(value) for value in values], index=values.index, dtype=bool)


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _issue(issues, code, sheet, rows, entity, detail, severity="error"):
    issues.append({"code": code, "severity": severity, "source_sheet": sheet,
                   "source_rows": _json([int(row) for row in rows]),
                   "entity_id": str(entity), "detail": detail})


def read_sources(workbook_path=DEFAULT_WORKBOOK):
    """Read cached source values; add 1-based Excel row numbers (header is row 1)."""
    sources = {}
    with pd.ExcelFile(workbook_path) as workbook:
        for name, sheet in SHEETS.items():
            frame = pd.read_excel(workbook, sheet_name=sheet,
                                  keep_default_na=False, na_values=[""])
            frame["source_row"] = range(2, len(frame) + 2)
            sources[name] = frame
    return sources


def valid_mapping_rows(mapping, issues):
    """Exclude ALL rows involved in a duplicate or missing identity, never pick first."""
    missing = mapping[ACCOUNT_IDS].isna().any(axis=1) | mapping[ACCOUNT_IDS].eq("").any(axis=1)
    invalid = missing.copy()
    for column in ACCOUNT_IDS:
        duplicate = mapping[column].notna() & mapping[column].duplicated(keep=False)
        for value, group in mapping[duplicate].groupby(column, sort=False):
            _issue(issues, "duplicate_mapping", SHEETS["mapping"], group.source_row,
                   value, f"Repeated {column}; every affected mapping row is quarantined")
        invalid |= duplicate
    for _, row in mapping[missing].iterrows():
        _issue(issues, "missing_mapping_id", SHEETS["mapping"], [row.source_row],
               row.source_row, "A cross-system mapping ID is missing")
    return mapping[~invalid].copy(), mapping[invalid].copy()


def _constant(group, column, issues, code, sheet, entity):
    if group.empty:
        return pd.NA
    values = group[column]
    populated = values[~values.map(_missing)]
    if len(populated) == len(values) and populated.nunique() == 1:
        return populated.iloc[0]
    _issue(issues, code, sheet, group.source_row, entity,
           f"{column} is missing or conflicting; values={_json(values.astype(str).unique().tolist())}")
    return pd.NA


def _person_lookup(roster, hr, issues):
    people = {}
    for owner, group in roster[roster.crm_owner_id.notna()].groupby("crm_owner_id", sort=False):
        if (len(group) != 1 or _missing(group.iloc[0].hr_id)
                or roster.hr_id.eq(group.iloc[0].hr_id).sum() != 1):
            _issue(issues, "ambiguous_employee", SHEETS["roster"], group.source_row,
                   owner, "crm_owner_id must resolve to exactly one roster employee with hr_id")
            continue
        record = group.iloc[0]
        matches = hr[hr.hr_id.notna() & hr.hr_id.eq(record.hr_id)]
        if len(matches) != 1:
            _issue(issues, "unmatched_employee_hr", SHEETS["roster"], group.source_row,
                   owner, "Roster hr_id does not resolve to exactly one HR row")
            continue
        employee = matches.iloc[0]
        people[owner] = {"hr_id": record.hr_id, "name": record["name"],
                         "current_role": employee["Current Role"],
                         "roster_source_row": int(record.source_row),
                         "hr_source_row": int(employee.source_row)}
    return people


def build_account_master(sources, issues):
    """One account per verified mapping; apply plan 7.3/7.4/7.7 and Appendix A.8."""
    mapping, rejected = valid_mapping_rows(sources["mapping"], issues)
    opportunities, shifts = sources["opportunities"], sources["shifts"]
    people = _person_lookup(sources["roster"], sources["hr"], issues)
    duplicate_opportunities = (opportunities.opportunity_id.isna()
                               | opportunities.opportunity_id.eq("")
                               | opportunities.opportunity_id.duplicated(keep=False))
    for _, row in opportunities[duplicate_opportunities].iterrows():
        _issue(issues, "duplicate_or_missing_opportunity_id", SHEETS["opportunities"],
               [row.source_row], row.crm_account_id, f"opportunity_id={row.opportunity_id}")
    opp_groups = dict(tuple(opportunities.groupby("crm_account_id", sort=False)))
    shift_groups = dict(tuple(shifts.groupby("app_account_id", sort=False)))
    records = []
    for _, identity in mapping.iterrows():
        account = {column: identity[column] for column in ACCOUNT_IDS}
        account["mapping_source_row"] = int(identity.source_row)
        group = opp_groups.get(identity.crm_account_id, opportunities.iloc[:0])
        account["opportunity_source_rows"] = _json(group.source_row.tolist())
        account["opportunity_ids"] = _json(group.opportunity_id.tolist())
        account["opportunity_count"] = len(group)
        if group.empty:
            _issue(issues, "account_without_opportunities", SHEETS["mapping"],
                   [identity.source_row], identity.crm_account_id, "No opportunity rows exist")
        for column in ATTRIBUTES + ["crm_owner_id"]:
            account[column] = _constant(group, column, issues, "conflicting_account_attribute",
                                        SHEETS["opportunities"], identity.crm_account_id)
        won = group[group.opportunity_stage.eq("Closed Won")]
        account["closed_won_count"] = len(won)
        for column in ["earliest_closed_won_date", "earliest_opportunity_id",
                       "earliest_opportunity_source_row", "earliest_effective_start_date",
                       "closing_ae_crm_owner_id"]:
            account[column] = pd.NA
        if not group.empty and duplicate_opportunities.loc[group.index].any():
            account["earliest_opportunity_status"] = "ambiguous_opportunity_identity"
        elif won.empty:
            account["earliest_opportunity_status"] = "no_closed_won"
        elif not _date_mask(won.closed_won_date).all():
            account["earliest_opportunity_status"] = "invalid_or_missing_closed_won_date"
        else:
            earliest = won.closed_won_date.min()
            candidates = won[won.closed_won_date.eq(earliest)]
            account["earliest_closed_won_date"] = earliest
            if len(candidates) == 1:
                selected = candidates.iloc[0]
                account.update({"earliest_opportunity_status": "resolved",
                                "earliest_opportunity_id": selected.opportunity_id,
                                "earliest_opportunity_source_row": int(selected.source_row),
                                "earliest_effective_start_date": selected.effective_start_date,
                                "closing_ae_crm_owner_id": selected.closed_by_owner_id})
            else:
                account["earliest_opportunity_status"] = "tied_earliest_closed_won"
        if account["earliest_opportunity_status"] != "resolved":
            _issue(issues, account["earliest_opportunity_status"], SHEETS["opportunities"],
                   group.source_row, identity.crm_account_id,
                   "No opportunity/closer is selected; source candidates are retained")
        account["current_am_crm_owner_id"] = account.pop("crm_owner_id")
        for prefix in ("closing_ae", "current_am"):
            owner = account[f"{prefix}_crm_owner_id"]
            person = None if _missing(owner) else people.get(owner)
            unassigned = (prefix == "closing_ae" and _missing(owner)
                          and account["earliest_opportunity_status"] == "resolved")
            account[f"{prefix}_status"] = ("resolved" if person else
                                            "unassigned_by_plan" if unassigned else "unresolved")
            for field in ("hr_id", "name", "current_role", "roster_source_row", "hr_source_row"):
                account[f"{prefix}_{field}"] = person[field] if person else pd.NA
            if person is None:
                _issue(issues, "unassigned_closing_ae" if unassigned else "unresolved_" + prefix,
                       SHEETS["opportunities"], group.source_row, identity.crm_account_id,
                       f"Missing/ambiguous/unmatched owner ID: {owner}; no reassignment",
                       severity="warning" if unassigned else "error")
            elif ((prefix == "closing_ae" and person["current_role"] not in
                   {"SMB Account Executive", "ENT Account Executive"}) or
                  (prefix == "current_am" and person["current_role"] not in
                   {"SMB Account Manager", "ENT Account Manager"})):
                account[f"{prefix}_status"] = "role_mismatch"
                _issue(issues, "owner_role_mismatch", SHEETS["opportunities"], group.source_row,
                       identity.crm_account_id,
                       f"{prefix} ID resolves to HR role {person['current_role']}; source ID retained")
        app = shift_groups.get(identity.app_account_id, shifts.iloc[:0])
        account["source_shift_count"] = len(app)
        account["account_first_worked_shift_date"] = _constant(
            app, "account_first_worked_shift_date", issues, "conflicting_first_worked_date",
            SHEETS["shifts"], identity.app_account_id)
        account["first_worked_source_rows"] = _json(app.source_row.tolist())
        if app.empty:
            _issue(issues, "account_without_app_extract_rows", SHEETS["mapping"],
                   [identity.source_row], identity.crm_account_id,
                   "No supplied first-worked date available; no date inferred", severity="warning")
        # Plan 7.3: later expansions/renewals never restart go-live.
        account["go_live_date"] = (account["earliest_effective_start_date"]
                                   if _valid_date(account["earliest_effective_start_date"]) else pd.NaT)
        account["go_live_status"] = "resolved" if _valid_date(account["go_live_date"]) else "unresolved"
        if account["go_live_status"] != "resolved":
            _issue(issues, "unresolved_go_live", SHEETS["opportunities"], group.source_row,
                   identity.crm_account_id, "Earliest Closed Won effective_start_date is unresolved")
        records.append(account)
    columns = ACCOUNT_IDS + ATTRIBUTES + ["mapping_source_row", "opportunity_source_rows", "opportunity_ids",
                             "opportunity_count", "closed_won_count"]
    accounts = pd.DataFrame(records) if records else pd.DataFrame(columns=columns)
    return accounts, rejected


def _cents(value):
    if _missing(value) or isinstance(value, (bool, np.bool_)):
        return None
    try:
        number = Decimal(str(value)) * 100
        if not number.is_finite() or number != number.to_integral_value():
            return None
        return int(number)
    except (ValueError, InvalidOperation):
        return None


def build_shift_fact(sources, accounts, issues):
    """Preserve every original app row; enrich with a validated many-to-one join."""
    shifts = sources["shifts"].rename(columns={"source_row": "shift_source_row"}).copy()
    keep = ACCOUNT_IDS + ["mapping_source_row", "earliest_opportunity_id",
                          "earliest_opportunity_source_row", "earliest_effective_start_date",
                          "charge_rate_per_shift", "pay_rate_per_shift", "account_type", "segment",
                          "closing_ae_crm_owner_id", "current_am_crm_owner_id", "go_live_date",
                          "go_live_status", "opportunity_source_rows"]
    # Empty master still has the complete enrichment schema and can retain orphan shifts.
    dimension = accounts.reindex(columns=keep)
    fact = shifts.merge(dimension, on="app_account_id", how="left",
                        validate="many_to_one", indicator="account_join_status", sort=False)
    if len(fact) != len(shifts):
        raise AssertionError("Account enrichment changed shift row count")
    duplicate_shift = fact.shift_id.isna() | fact.shift_id.eq("") | fact.shift_id.duplicated(keep=False)
    net, margins, gsv, net_dates, statuses, gsv_statuses = [], [], [], [], [], []
    for index, row in fact.iterrows():
        charge, pay = _cents(row.charge_rate_per_shift), _cents(row.pay_rate_per_shift)
        margin = None if charge is None or pay is None else charge - pay
        margins.append(margin)
        worked = row.worked_shift
        valid_flag = isinstance(worked, (bool, np.bool_))
        has_date = _valid_date(row.shift_worked_date)
        if duplicate_shift.loc[index]:
            status = "duplicate_or_missing_shift_id"
        elif not valid_flag:
            status = "invalid_worked_flag"
        elif bool(worked) != has_date or (not bool(worked) and not _missing(row.shift_worked_date)):
            status = "worked_flag_date_conflict"
        elif not bool(worked):
            status = "not_worked"
        elif margin is None:
            status = "unresolved_rates"
        else:
            status = "recognized"
        amount = margin if status == "recognized" else 0 if status == "not_worked" else None
        net.append(amount)
        net_dates.append(row.shift_worked_date if status == "recognized" else pd.NaT)
        statuses.append(status)
        if duplicate_shift.loc[index]:
            gsv_status = "duplicate_or_missing_shift_id"
        elif not _valid_date(row.shift_posted_date):
            gsv_status = "invalid_or_missing_posted_date"
        elif charge is None:
            gsv_status = "unresolved_charge_rate"
        else:
            gsv_status = "recognized"
        gsv.append(charge if gsv_status == "recognized" else None)
        gsv_statuses.append(gsv_status)
        if gsv_status != "recognized" and gsv_status != status:
            _issue(issues, gsv_status, SHEETS["shifts"], [row.shift_source_row], row.shift_id,
                   "GSV remains null; all posted rows including unfilled shifts are retained")
        if status not in {"recognized", "not_worked"}:
            _issue(issues, status, SHEETS["shifts"], [row.shift_source_row], row.shift_id,
                   "Revenue remains null; the source row is retained")
        if row.account_join_status != "both":
            _issue(issues, "unmatched_shift_account", SHEETS["shifts"], [row.shift_source_row],
                   row.shift_id, f"No verified mapping/account for app_account_id={row.app_account_id}")
        if (has_date and pd.notna(row.account_first_worked_shift_date)
                and row.shift_worked_date < row.account_first_worked_shift_date):
            _issue(issues, "worked_before_supplied_first_worked", SHEETS["shifts"],
                   [row.shift_source_row], row.shift_id, "Source dates conflict; neither date is replaced")
        if (has_date and pd.notna(row.go_live_date) and row.shift_worked_date < row.go_live_date):
            _issue(issues, "worked_before_go_live", SHEETS["shifts"], [row.shift_source_row],
                   row.shift_id, "Worked date precedes go-live; raw base revenue retained for investigation")
        if margin is not None and margin < 0:
            _issue(issues, "negative_margin", SHEETS["shifts"], [row.shift_source_row], row.shift_id,
                   "Negative source margin retained; no floor invented", severity="warning")
    fact["per_shift_margin_cents"] = pd.array(margins, dtype="Int64")
    fact["base_net_revenue_cents"] = pd.array(net, dtype="Int64")
    fact["net_revenue_recognition_date"] = pd.to_datetime(net_dates)
    fact["net_revenue_status"] = statuses
    fact["gross_services_value_cents"] = pd.array(gsv, dtype="Int64")
    fact["gsv_recognition_date"] = fact.shift_posted_date.where(
        pd.Series(gsv_statuses, index=fact.index).eq("recognized"))
    fact["gsv_status"] = gsv_statuses
    fact["source_sheet"] = SHEETS["shifts"]
    return fact


def _audit_unmatched(sources, accounts, issues):
    for name, column in [("opportunities", "crm_account_id"), ("billing", "billing_account_id")]:
        frame = sources[name]
        unmatched = frame[column].isna() | ~frame[column].isin(accounts[column])
        for _, row in frame[unmatched].iterrows():
            _issue(issues, f"unmatched_{name}_account", SHEETS[name], [row.source_row],
                   row[column], "No verified canonical mapping for this source account")
    app = sources["shifts"]
    billing = sources["billing"]
    app_unique = app[app.shift_id.notna() & ~app.shift_id.duplicated(keep=False)]
    links = billing.merge(app_unique[["shift_id", "app_account_id", "source_row"]],
                          on="shift_id", how="left", validate="many_to_one",
                          suffixes=("_billing", "_app"))
    links = links.merge(accounts[["app_account_id", "billing_account_id"]],
                        on="app_account_id", how="left", validate="many_to_one",
                        suffixes=("_source", "_mapped"))
    for _, row in links.iterrows():
        if _missing(row.app_account_id):
            code = "unmatched_or_ambiguous_billing_shift"
        elif _missing(row.billing_account_id_mapped) or row.billing_account_id_source != row.billing_account_id_mapped:
            code = "billing_shift_account_mismatch"
        else:
            continue
        _issue(issues, code, SHEETS["billing"], [row.source_row_billing], row.shift_id,
               "Billing and app identity paths do not resolve to the same mapped account")
    return links


def reconcile_model(sources, accounts, fact, issues, rejected, billing_links):
    """Independent source-account aggregation checks all recognized base amounts."""
    expected, expected_gsv = 0, 0
    for _, account in accounts.iterrows():
        charge, pay = _cents(account.charge_rate_per_shift), _cents(account.pay_rate_per_shift)
        source = sources["shifts"]
        valid_rows = source[source.app_account_id.eq(account.app_account_id)
                            & source.shift_id.notna() & source.shift_id.ne("")
                            & ~source.shift_id.duplicated(keep=False)]
        if charge is not None:
            expected_gsv += int(_date_mask(valid_rows.shift_posted_date).sum()) * charge
        if charge is None or pay is None:
            continue
        eligible = valid_rows[valid_rows.worked_shift.map(lambda v: isinstance(v, (bool, np.bool_)) and bool(v))
                              & _date_mask(valid_rows.shift_worked_date)]
        expected += len(eligible) * (charge - pay)
    actual = int(fact.base_net_revenue_cents.sum())
    lineage_ok = sorted(fact.shift_source_row.tolist()) == sorted(sources["shifts"].source_row.tolist())
    checks = {
        "mapping_source_rows": len(sources["mapping"]), "account_master_rows": len(accounts),
        "quarantined_mapping_rows": len(rejected), "source_shift_rows": len(sources["shifts"]),
        "shift_fact_rows": len(fact), "shift_row_count_preserved": len(fact) == len(sources["shifts"]),
        "shift_source_rows_preserved_once": lineage_ok,
        "account_id_columns_unique": all(accounts[c].notna().all() and accounts[c].is_unique for c in ACCOUNT_IDS),
        "recognized_worked_rows": int(fact.net_revenue_status.eq("recognized").sum()),
        "unworked_rows": int(fact.net_revenue_status.eq("not_worked").sum()),
        "unresolved_net_revenue_rows": int(fact.base_net_revenue_cents.isna().sum()),
        "base_net_revenue_cents": actual, "source_aggregation_net_revenue_cents": expected,
        "net_revenue_reconciliation_difference_cents": actual - expected,
        "gross_services_value_cents": int(fact.gross_services_value_cents.sum()),
        "source_aggregation_gsv_cents": expected_gsv,
        "gsv_reconciliation_difference_cents": int(fact.gross_services_value_cents.sum()) - expected_gsv,
        "unresolved_gsv_rows": int(fact.gross_services_value_cents.isna().sum()),
        "gsv_resolved_rows": int(fact.gross_services_value_cents.notna().sum()),
        "billing_source_rows": len(sources["billing"]), "billing_audit_rows": len(billing_links),
        "issue_counts": dict(Counter(issue["code"] for issue in issues)),
    }
    original_first = sources["shifts"].set_index("source_row").account_first_worked_shift_date
    copied_first = fact.set_index("shift_source_row").account_first_worked_shift_date
    checks["provided_first_worked_dates_preserved"] = original_first.sort_index().equals(copied_first.sort_index())
    original = sources["shifts"].set_index("source_row").sort_index()
    retained = fact.set_index("shift_source_row").sort_index()
    checks["all_original_shift_columns_preserved"] = all(
        original[column].equals(retained[column]) for column in original.columns)
    checks["mapping_row_count_reconciles"] = len(accounts) + len(rejected) == len(sources["mapping"])
    checks["go_live_resolved_accounts"] = int(accounts.get("go_live_status", pd.Series(dtype=str)).eq("resolved").sum())
    checks["closing_ae_unassigned_accounts"] = int(accounts.get("closing_ae_status", pd.Series(dtype=str)).eq("unassigned_by_plan").sum())
    if not all([checks["shift_row_count_preserved"], lineage_ok,
                checks["account_id_columns_unique"], checks["provided_first_worked_dates_preserved"],
                checks["all_original_shift_columns_preserved"], checks["mapping_row_count_reconciles"],
                actual == expected, checks["gsv_reconciliation_difference_cents"] == 0]):
        raise AssertionError(f"Revenue model reconciliation failed: {checks}")
    return checks


def summarize_monthly_revenue(fact):
    """Separate recognition calendars; no eligibility/window/commission calculation."""
    summaries = []
    for status, date_column, amount in [
        ("net_revenue_status", "net_revenue_recognition_date", "base_net_revenue_cents"),
        ("gsv_status", "gsv_recognition_date", "gross_services_value_cents"),
    ]:
        rows = fact[fact[status].eq("recognized")].copy()
        rows["month"] = pd.to_datetime(rows[date_column]).dt.strftime("%Y-%m")
        summaries.append(rows.groupby("month")[amount].sum().rename(amount))
    result = pd.concat(summaries, axis=1).fillna(0).astype("int64").reset_index()
    return result.sort_values("month").reset_index(drop=True)


def summarize_account_activity(fact):
    """Posted-cohort diagnostics; fill ratio is descriptive, not a payout rule."""
    rows = fact.copy()
    rows["posted_month"] = pd.to_datetime(rows.shift_posted_date.where(
        _date_mask(rows.shift_posted_date)), errors="coerce").dt.strftime("%Y-%m")
    rows["worked_record"] = rows.worked_shift.map(lambda v: isinstance(v, (bool, np.bool_)) and bool(v))
    rows["unfilled_record"] = rows.worked_shift.map(lambda v: isinstance(v, (bool, np.bool_)) and not bool(v))
    rows["gsv_missing"] = rows.gross_services_value_cents.isna()
    result = rows.groupby(["app_account_id", "account_type", "posted_month"], dropna=False).agg(
        posted_shift_records=("shift_source_row", "size"),
        worked_from_posted_cohort=("worked_record", "sum"),
        unfilled_from_posted_cohort=("unfilled_record", "sum"),
        gsv_cents=("gross_services_value_cents", lambda s: s.sum(min_count=1)),
        unresolved_gsv_rows=("gsv_missing", "sum"),
    ).reset_index()
    result["cohort_fill_ratio"] = result.worked_from_posted_cohort / result.posted_shift_records
    return result


def build_revenue_model(workbook_path=DEFAULT_WORKBOOK, rules_path=DEFAULT_RULES):
    """Build in memory; never write or register an Agent tool on import/build."""
    workbook_path = Path(workbook_path)
    rules_path = Path(rules_path)
    rules_text = rules_path.read_text(encoding="utf-8")
    rules_digest = hashlib.sha256(rules_text.encode("utf-8")).hexdigest()
    digest = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    sources = read_sources(workbook_path)
    issues = []
    accounts, rejected = build_account_master(sources, issues)
    fact = build_shift_fact(sources, accounts, issues)
    billing_links = _audit_unmatched(sources, accounts, issues)
    checks = reconcile_model(sources, accounts, fact, issues, rejected, billing_links)
    monthly = summarize_monthly_revenue(fact)
    if (int(monthly.base_net_revenue_cents.sum()) != checks["base_net_revenue_cents"]
            or int(monthly.gross_services_value_cents.sum()) != checks["gross_services_value_cents"]):
        raise AssertionError("Monthly revenue summaries do not reconcile")
    if hashlib.sha256(workbook_path.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Source workbook changed while building the model")
    # Full fingerprints and Excel row numbers provide lineage without copying raw inputs.
    for frame in (accounts, fact):
        frame["source_workbook_sha256"] = digest
    return {"accounts": accounts, "shifts": fact, "rejected_mappings": rejected,
            "issues": pd.DataFrame(issues, columns=["code", "severity", "source_sheet", "source_rows", "entity_id", "detail"]),
            "billing_lineage": billing_links, "monthly_summary": monthly,
            "account_activity": summarize_account_activity(fact),
            "reconciliation": checks, "source_sha256": digest,
            "source_workbook": str(workbook_path.resolve()),
            "rules_path": str(rules_path.resolve()), "rules_sha256": rules_digest}


def write_outputs(model, output_dir=OUTPUT_DIR, report_path=REPORT_PATH):
    """Write derived CSV/JSON/Markdown only, outside the raw-data directory."""
    output_dir, report_path = Path(output_dir).resolve(), Path(report_path).resolve()
    raw_dir = Path(model["source_workbook"]).parent.resolve()
    if output_dir.is_relative_to(raw_dir) or report_path.is_relative_to(raw_dir):
        raise ValueError("Derived output must be outside the source-data directory")
    if report_path.suffix.lower() != ".md":
        raise ValueError("Report must be Markdown")
    if hashlib.sha256(Path(model["source_workbook"]).read_bytes()).hexdigest() != model["source_sha256"]:
        raise RuntimeError("Source changed since build; rebuild before export")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_hashes = {}
    for key, filename in [("accounts", "account_master.csv"), ("shifts", "shift_fact.csv"),
                          ("issues", "data_quality_issues.csv"), ("rejected_mappings", "quarantined_mappings.csv"),
                          ("billing_lineage", "billing_shift_lineage.csv"),
                          ("monthly_summary", "monthly_revenue_summary.csv"),
                          ("account_activity", "account_activity_summary.csv")]:
        model[key].to_csv(output_dir / filename, index=False)
        output_hashes[filename] = hashlib.sha256((output_dir / filename).read_bytes()).hexdigest()
    manifest = {"source_workbook": model["source_workbook"], "source_sha256": model["source_sha256"],
                "rules_path": model["rules_path"], "rules_sha256": model["rules_sha256"],
                "status": "phase3a_base_model", "money_storage": "integer USD cents; no FX",
                "output_sha256": output_hashes,
                "rules": {"base_net_revenue": "User request: (charge - pay) for worked shifts, by worked date",
                          "go_live": "Plan 7.3: effective_start_date of earliest Closed Won",
                          "gsv": "Plan 7.1: every posted shift charge rate, by posted date",
                          "ownership": "Plan 7.4/7.7 and Appendix A.8: closed_by_owner_id / crm_owner_id"},
                "reconciliation": model["reconciliation"]}
    (output_dir / "manifest.json").write_text(_json(manifest) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(model), encoding="utf-8")
    if hashlib.sha256(Path(model["source_workbook"]).read_bytes()).hexdigest() != model["source_sha256"]:
        raise RuntimeError("Source changed during export")


def render_report(model):
    checks = model["reconciliation"]
    usd = lambda cents: f"${Decimal(int(cents)) / 100:,.2f}"
    accounts, fact = model["accounts"], model["shifts"]
    lines = ["# Phase 3A: Account & Shift Revenue Data Model", "",
             "Implemented from the original Case Study, especially Sections 06, 7.1–7.7, "
             "7.13 and Appendix A, plus `reports/workbook_relationships.md`. "
             "No commissions, GTM credit conversion, billing adjustments, ramps or FX are calculated.", "",
             f"Rules: `{Path(model['rules_path']).name}`; SHA-256 `{model['rules_sha256']}`.",
             f"Workbook SHA-256: `{model['source_sha256']}`; original unchanged.", "",
             "## Models and governing rules", "",
             "- **Account Master**: one row per verified CRM/App/Billing mapping. Every mapping "
             "ID must be populated and unique. All duplicate/incomplete mappings are quarantined, "
             "including identical duplicate rows. No first-row deduplication is used.",
             "- **Acquisition and go-live (7.3)**: filter strictly to `Closed Won`, then select "
             "the earliest `closed_won_date`. A populated win date on any other stage is ignored. "
             "Go-live is that opportunity's `effective_start_date`; a later renewal never resets it. "
             "An earliest-date tie, duplicate opportunity ID or missing win date leaves the "
             "selection unresolved. The plan gives no tie-breaker.",
             "- **Owners (7.4, 7.7, Appendix A.8)**: closing AE is the selected win's "
             "`closed_by_owner_id`; a blank remains unassigned. Current AM is the agreed "
             "`crm_owner_id` across source opportunities; no last-row/current-owner timestamp "
             "is invented. Both IDs retain roster/HR lineage. Identity and role exceptions are "
             "logged. This phase does not calculate employment-date credit eligibility.",
             "- **Account attributes (7.3)**: rates, terms, segment and account type must agree "
             "across all opportunities. Missing/conflicting attributes remain blank. Rates are "
             "account properties, so no opportunity-to-shift allocation is invented.",
             "- **Shift Fact**: one retained row per original App source row, enriched from Account "
             "Master with a validated many-to-one join. Original source IDs and first-worked date "
             "are retained, with Excel source row numbers, mapping row, all opportunity evidence "
             "rows, selected acquisition opportunity ID/row, owner IDs, and source workbook hash.",
             "- **Base Net Revenue (7.1)**: `(charge_rate_per_shift - pay_rate_per_shift)` "
             "for worked shifts, by worked date; unworked shifts contribute zero. Invalid shift "
             "IDs, flags/dates or unresolved rates remain null, not silently zeroed. Negative "
             "margins are flagged and retained without inventing a floor.",
             "- **GSV (7.1)**: `charge_rate_per_shift` for every posted shift, including unfilled "
             "shifts, by posted date. Stored for all accounts; later GTM logic can filter the "
             "original account_type. Posting before first worked is valid under 7.6 and is "
             "not filtered out. No 3% conversion, window credit or quota retirement is applied.",
             "- **First worked (Appendix A.9)**: source `account_first_worked_shift_date` is copied "
             "unchanged. Account-level consolidation requires identical supplied dates. Accounts "
             "without App rows have no date fabricated. Extract minima never replace source dates.", "",
             "All money is stored as integer USD cents (06 and 7.13); unsupported fractional-cent "
             "rates remain unresolved rather than being rounded. Base figures are before billing "
             "credits and net-terms adjustments. Null amounts are separately counted; check those "
             "counts before treating a total as complete.", "", "## Reconciliation", "",
             f"Base Net Revenue: **{usd(checks['base_net_revenue_cents'])}**; "
             f"GSV: **{usd(checks['gross_services_value_cents'])}** over the full extract, "
             "not just June. Independent source-account aggregation agrees exactly.", "",
             "| Check | Result |", "| --- | --- |"]
    for name, value in checks.items():
        if name != "issue_counts":
            lines.append(f"| {name} | {value} |")
    lines += ["", "### Monthly reconciliation", "",
              "Net Revenue follows worked month; GSV follows posted month. February GSV can "
              "therefore exist although the worked-shift extract starts in March.", "",
              "| Month | Base Net Revenue (USD) | GSV (USD) |", "| --- | ---: | ---: |"]
    for row in model["monthly_summary"].itertuples(index=False):
        lines.append(f"| {row.month} | {usd(row.base_net_revenue_cents)} | {usd(row.gross_services_value_cents)} |")
    lines += ["", "## Data quality and unresolved issues", "", "| Issue | Count |", "| --- | ---: |"]
    for code, count in checks["issue_counts"].items():
        lines.append(f"| {code} | {count} |")
    for code in ["duplicate_mapping", "conflicting_account_attribute", "tied_earliest_closed_won",
                 "unmatched_shift_account", "unmatched_opportunities_account", "unmatched_billing_account",
                 "unmatched_or_ambiguous_billing_shift", "billing_shift_account_mismatch"]:
        if code not in checks["issue_counts"]:
            lines.append(f"| {code} | 0 |")
    lines += ["", f"{int(accounts.closed_won_count.gt(1).sum())} accounts have multiple Closed Won "
              "rows; the earliest acquisition record is used as required, and other source rows "
              "remain traceable. Different renewal start dates are not automatically an account "
              "attribute conflict. Blank closing owners are deliberately not reassigned.", "",
              "Four kinds of exceptions must be distinguished: source identity/attribute errors "
              "(unresolved fields); missing owner attribution (no invented owner); dates outside "
              "account chronology (source base figures retained and flagged); and incomplete "
              "history (first-worked date preserved, no fabricated historical shifts). "
              "Zero issue counts are not proof of complete history or commission eligibility.", "",
              "GTM posting/fill behavior is summarized separately to support review before later "
              "commission calculations. High posted GSV with low fill must be investigated; no "
              "payout hold threshold or adjustment is invented here. The full AE windows and "
              "existing-book classification remain for later phases. Manager-chain validation "
              "is deferred; Appendix A explicitly allows the Head's manager to be outside HR.", "",
              "## Outputs and tests", "",
              "Derived files in `derived/phase3a/`: `account_master.csv`, `shift_fact.csv`, "
              "`billing_shift_lineage.csv`, `quarantined_mappings.csv`, `data_quality_issues.csv`, "
              "`monthly_revenue_summary.csv`, `account_activity_summary.csv`, and `manifest.json`. "
              "Excel source rows are 1-based (header row 1). CSV dates are ISO text, blank fields "
              "are unresolved/null, and JSON arrays link all source evidence. The manifest contains "
              "rule/source hashes, output hashes, units and reconciliation results.", "",
              "Run: `.venv/bin/python revenue_data_model.py`. Tests: "
              "`.venv/bin/python -m unittest -v test_revenue_data_model test_workbook_discovery test_main`. "
              "Tests cover non-won date residues, earliest-won/go-live selection, ties, blank "
              "closers, ownership/rate conflicts, duplicate mappings/shifts, exact cents, "
              "cross-month recognition, unfilled GSV, missing history, unmatched records, "
              "full-source lineage, independent totals and the existing Agent tools. "
              "Live Ollama is optional; this phase adds no Agent features."]
    gtm = model["account_activity"]
    gtm = gtm[gtm.account_type.eq("GTM") & gtm.posted_month.eq("2026-06")]
    if not gtm.empty:
        top = gtm.sort_values("gsv_cents", ascending=False).iloc[0]
        lines += ["", "### GTM review example (ranked by June posted GSV)", "",
                  f"App account `{top.app_account_id}` has {int(top.posted_shift_records)} "
                  f"June-posted records, {int(top.worked_from_posted_cohort)} worked records "
                  f"in that cohort ({top.cohort_fill_ratio:.1%} fill), and {usd(top.gsv_cents)} "
                  "posted GSV. This deserves operational review before commission calculations. "
                  "The model retains plan-defined GSV without inventing a payout threshold. "
                  "Posting-cohort fill is not the same as shifts worked during June."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    args = parser.parse_args()
    model = build_revenue_model(args.workbook, args.rules)
    write_outputs(model, args.output_dir, args.report)
    print(_json(model["reconciliation"]))
    print("Phase 3A outputs saved; no commissions calculated.")
