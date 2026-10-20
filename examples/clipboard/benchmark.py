"""Section 07 executable benchmark. Dataset semantics are deliberately isolated here.

All IDs, amounts, dates, owners, rates and thresholds come from inputs/configuration.
No employee/account selections or expected totals are embedded in this workflow.
"""

import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from local_analyst.finance import (decimal as D, money, marginal_tiers, window_bounds,
                                   window_index, employed_on, ramp_credit, hierarchy_paths, convert_currency)
from local_analyst.communications import link_messages, draft_response
from local_analyst.workspace import dump, fingerprint
from local_analyst.exporting import export_excel, export_pdf
from .revenue import build_revenue_model, DEFAULT_WORKBOOK, DEFAULT_RULES, read_sources
from .discovery import load_workbook_tables


ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).with_name("plan.json")
ZERO = Decimal(0)


def prepare(workbook=DEFAULT_WORKBOOK, rules=DEFAULT_RULES):
    model = build_revenue_model(workbook, rules)
    sources = read_sources(workbook)
    tables, _ = load_workbook_tables(workbook)
    roster, hr = sources["roster"], sources["hr"]
    people = roster.merge(hr, on="hr_id", how="left", validate="one_to_one", suffixes=("_roster", "_hr"))
    comp = tables["5. Compensation"].set_index("Role")
    bands = tables["6. Accelerators [header row 2]"].set_index("Role")
    spiffs = tables["6. Accelerators [header row 8]"].set_index("Role")
    ramp = tables["4. Ramp Terms"].set_index("Role")
    ramp_percentages = {}
    for role in list(bands.index) + list(spiffs.index):
        text = str(ramp.loc[role, "Start Date Ramp Terms"])
        matched = re.search(r"(\d+)% quota credit in days 1-30 and (\d+)% in days 31-60", text)
        if not matched:
            raise ValueError(f"Unsupported ramp wording for {role}; no interpretation fabricated")
        ramp_percentages[role] = [D(matched[1])/100, D(matched[2])/100]
    fx = tables["3. Exchange Rates"].set_index("Country Code")
    return {"base": model, "sources": sources, "tables": tables, "people": people,
            "comp": comp, "bands": bands, "spiffs": spiffs, "ramp_percentages": ramp_percentages, "fx": fx,
            "workbook": str(Path(workbook).resolve()), "rules": str(Path(rules).resolve())}


def event_credits(data, config, excluded_accounts=()):
    month = pd.Timestamp(config["review_month"]).replace(day=1)
    end = month + pd.offsets.MonthEnd(0)
    accounts, shifts = data["base"]["accounts"], data["base"]["shifts"]
    people = data["people"].set_index("crm_owner_id")
    groups = dict(tuple(shifts.groupby("app_account_id", sort=False)))
    events, warnings = [], []
    width = int(config["window_days"])
    for _, account in accounts.iterrows():
        if account.crm_account_id in excluded_accounts:
            continue
        rows = groups.get(account.app_account_id)
        if rows is None or pd.isna(account.account_first_worked_shift_date):
            continue
        anchor = pd.Timestamp(account.account_first_worked_shift_date)
        gtm = account.account_type == "GTM"
        amount_col = "gross_services_value_cents" if gtm else "base_net_revenue_cents"
        date_col = "shift_posted_date" if gtm else "shift_worked_date"
        relevant = rows[rows[date_col].notna()].copy()
        if relevant[amount_col].isna().any():
            warnings.append({"account": account.crm_account_id, "reason": "Unresolved source amounts; account credit withheld"})
            continue
        relevant["window"] = ((pd.to_datetime(relevant[date_col]).dt.normalize() - anchor.normalize()).dt.days // width)
        if gtm:
            relevant["window"] = relevant.window.clip(lower=0)
        owner = account.closing_ae_crm_owner_id
        person = people.loc[owner] if pd.notna(owner) and owner in people.index else None
        rate = D(data["comp"].loc[person["Current Role"], "Standard Payout"]) if person is not None else None
        if gtm:
            multiplier = D(config["gtm_rate"]) / rate if rate else None
        else:
            multiplier = D(config["net_terms_factors"].get(str(int(account.net_terms)))) if pd.notna(account.net_terms) else None
        totals = [sum((D(v)/100 for v in relevant.loc[relevant.window.eq(i), amount_col]), ZERO) for i in range(3)]
        for index in range(3):
            start, close = window_bounds(anchor, index, width)
            current = relevant[relevant.window.eq(index)]
            if index == 0:
                recognized = current[(current[date_col] >= month) & (current[date_col] <= end)]
                pieces = [(pd.Timestamp(day), group, sum((D(v)/100 for v in group[amount_col]), ZERO))
                          for day, group in recognized.groupby(date_col)]
            elif month <= close <= end:
                improvement = max(ZERO, totals[index] - max(totals[:index]))
                pieces = [(close, current, improvement * D(config["later_window_factor"]))]
            else:
                pieces = []
            for recognized_date, evidence_rows, basis in pieces:
                if basis == 0:
                    continue
                credit = basis * multiplier if multiplier is not None else None
                employed = "unassigned"
                if person is not None:
                    employed = "active" if employed_on(recognized_date, person["Start Date"], person["End Date"]) else (
                        "pre_start" if recognized_date < person["Start Date"] else "after_end")
                events.append({"account": account.crm_account_id, "app_account_id": account.app_account_id,
                               "owner_crm_id": owner, "employee_hr_id": person.hr_id if person is not None else None,
                               "recognized_date": recognized_date, "window": index + 1,
                               "window_close": close, "basis": "GSV" if gtm else "Net Revenue",
                               "basis_credit_usd": basis, "whole_window_usd": totals[index],
                               "prior_best_window_usd": max(totals[:index]) if index else ZERO,
                               "credited_revenue": credit, "employment_status": employed,
                               "shift_source_rows": dump(evidence_rows.shift_source_row.tolist()),
                               "all_window_source_rows": dump(relevant[relevant.window.le(index)].shift_source_row.tolist()),
                               "opportunity_source_row": account.earliest_opportunity_source_row,
                               "rule_ref": "7.4-7.6, 7.11"})
    return pd.DataFrame(events, columns=["account", "app_account_id", "owner_crm_id", "employee_hr_id", "recognized_date", "window", "window_close", "basis", "basis_credit_usd", "whole_window_usd", "prior_best_window_usd", "credited_revenue", "employment_status", "shift_source_rows", "all_window_source_rows", "opportunity_source_row", "rule_ref"]), warnings


def existing_books(data, month, excluded_accounts=()):
    month = pd.Timestamp(month).replace(day=1)
    end = month + pd.offsets.MonthEnd(0)
    accounts, shifts = data["base"]["accounts"], data["base"]["shifts"]
    eligible = accounts[(pd.to_datetime(accounts.go_live_date) < month) & ~accounts.crm_account_id.isin(excluded_accounts)]
    joined = shifts.merge(eligible[["app_account_id", "current_am_crm_owner_id"]], on="app_account_id", how="inner", validate="many_to_one", suffixes=("_fact", ""))
    rows = joined[joined.shift_worked_date.between(month, end) & joined.worked_shift]
    result = defaultdict(lambda: ZERO)
    evidence = []
    if rows.current_am_crm_owner_id.isna().any() or rows.base_net_revenue_cents.isna().any():
        raise ValueError("Existing book has unresolved ownership or revenue")
    for owner, value in rows.groupby("current_am_crm_owner_id").base_net_revenue_cents.sum().items():
        result[owner] += D(value)/100
    billing = data["sources"]["billing"]
    approved = billing[billing.ticket_status.eq("approved") & billing.billing_month.eq(month.strftime("%Y-%m"))]
    credits = approved.merge(eligible[["billing_account_id", "current_am_crm_owner_id"]], on="billing_account_id", how="inner", validate="many_to_one")
    for _, row in credits.iterrows():
        if pd.isna(row.current_am_crm_owner_id):
            raise ValueError("Credit has no unambiguous AM")
        result[row.current_am_crm_owner_id] -= D(row.credit_amount)
    for owner in set(result):
        source_rows = rows[rows.current_am_crm_owner_id.eq(owner)].shift_source_row.tolist()
        billing_rows = credits[credits.current_am_crm_owner_id.eq(owner)].source_row.tolist()
        evidence.append({"owner_crm_id": owner, "month": month.strftime("%Y-%m"), "net_credit": result[owner],
                         "shift_source_rows": dump(source_rows), "billing_source_rows": dump(billing_rows), "rule_ref": "7.2,7.7"})
    return result, pd.DataFrame(evidence)


def calculate(data, config, scenario_multipliers=None, excluded_accounts=()):
    month = pd.Timestamp(config["review_month"]).replace(day=1)
    end, days = month + pd.offsets.MonthEnd(0), (month + pd.offsets.MonthEnd(0)).day
    events, warnings = event_credits(data, config, excluded_accounts)
    existing, book_evidence = existing_books(data, month, excluded_accounts)
    prior_month = month - pd.offsets.MonthBegin(1)
    # Same June book, live before May: aggregate both months using the same account restriction.
    account = data["base"]["accounts"]
    same_book_ids = account.loc[pd.to_datetime(account.go_live_date) < prior_month, "crm_account_id"]
    excluded_retention = set(account.crm_account_id) - set(same_book_ids) | set(excluded_accounts)
    retention_previous, _ = existing_books(data, prior_month, excluded_retention)
    retention_current, _ = existing_books(data, month, excluded_retention)
    people, comp = data["people"], data["comp"]
    ae_roles, am_roles = set(data["bands"].index), set(data["spiffs"].index)
    external = people.loc[people["Current Role"].eq("Head of Sales & AM"), "Manager - ID"].dropna().tolist()
    paths, hierarchy_warnings = hierarchy_paths(people, "hr_id", "Manager - ID", external)
    warnings.extend(hierarchy_warnings)
    records, carry = {}, {}
    for _, person in people.iterrows():
        role, employee, owner = person["Current Role"], person.hr_id, person.crm_owner_id
        if role not in comp.index:
            raise ValueError(f"No role plan: {role}")
        plan = comp.loc[role]
        rate = D(plan["Standard Payout"])
        employed_days = sum(employed_on(day, person["Start Date"], person["End Date"]) for day in pd.date_range(month, end))
        is_ic = role in ae_roles | am_roles
        floor, ramp_days = ZERO, 0
        if role in data["ramp_percentages"]:
            ramp = ramp_credit(month, plan["Monthly Quota Target"], person["Current Role Start Date"],
                               person["Start Date"], person["End Date"], data["ramp_percentages"][role])
            floor, ramp_days = ramp["floor"], ramp["ramp_days"]
        if role in ae_roles:
            own = events[events.owner_crm_id.eq(owner)]
            active = sum((D(v) for v in own.loc[own.employment_status.eq("active"), "credited_revenue"] if pd.notna(v)), ZERO)
            left = sum((D(v) for v in own.loc[own.employment_status.eq("after_end"), "credited_revenue"] if pd.notna(v)), ZERO)
            if own.employment_status.eq("pre_start").any():
                warnings.append({"employee": employee, "reason": "Pre-employment credit has no explicit manager treatment; withheld from payable and rollup"})
            metric = "new"
        elif role in am_roles:
            own_evidence = data["base"]["shifts"]
            # AM individual employment timing uses worked date and billing-month recognition.
            # A mid-month AM employment change would need credit dates for monthly tickets.
            if employed_days not in (0, days):
                warnings.append({"employee": employee, "reason": "AM partial employment: monthly billing-credit recognition date unspecified; conditional monthly treatment requires approval"})
            active = existing[owner] if employed_days else ZERO
            left = existing[owner] if not employed_days and pd.notna(person["End Date"]) and person["End Date"] < month else ZERO
            metric = "existing"
        else:
            active, left, metric = ZERO, ZERO, "manager"
        paid_on = (max(active, floor) if ramp_days else active) if is_ic and employed_days else ZERO
        carry[employee] = {"new": paid_on + left if metric == "new" else ZERO,
                           "existing": paid_on + left if metric == "existing" else ZERO}
        policy = config["ramp_month_policy"]
        if policy not in {"any_ramp_day_disables", "month_end_status"}:
            raise ValueError("Ambiguous ramp policy: supply an explicit supported choice")
        end_index = window_index(end, person["Current Role Start Date"], config["window_days"]) if role in data["ramp_percentages"] else -1
        ramp_ineligible = ramp_days > 0 if policy == "any_ramp_day_disables" else 0 <= end_index < 2
        if 0 < ramp_days < employed_days:
            warnings.append({"employee": employee, "reason": "Partial-month ramp eligibility is a conditional interpretation; alternative quantified"})
        thresholds, multipliers = [], [D(1)]
        if role in ae_roles and not ramp_ineligible:
            band = data["bands"].loc[role]
            thresholds = [D(band["Accelerator $ Range I (Low)"]), D(band["Accelerator $ Range II (Low)"])]
            multipliers = [D(1), D(band["Accelerator Multiplier I"]), D(band["Accelerator Multiplier II"])]
            if role == "SMB Account Executive" and scenario_multipliers:
                multipliers = [D(1)] + [D(v) for v in scenario_multipliers]
        spiff = ZERO
        if role in am_roles and not ramp_ineligible and employed_on(end, person["Start Date"], person["End Date"]):
            if retention_previous[owner] > 0 and retention_current[owner] >= retention_previous[owner]:
                spiff = D(data["spiffs"].loc[role, "SPIFF Amount"])
        records[employee] = {"hr_id": employee, "name": person["name"], "role": role,
                             "source_roster_row": person.source_row_roster, "source_hr_row": person.source_row_hr,
                             "own_production": active, "floor": floor, "credited_revenue": paid_on,
                             "secondary_credit": ZERO, "rate": rate, "rate2": ZERO,
                             "quota": D(plan["Monthly Quota Target"]) * D(employed_days) / D(days),
                             "ramp_days": ramp_days, "employed_days": employed_days,
                             "threshold1": thresholds[0] if thresholds else paid_on,
                             "threshold2": thresholds[1] if thresholds else paid_on,
                             "multiplier1": multipliers[1] if thresholds else D(1),
                             "multiplier2": multipliers[2] if thresholds else D(1),
                             "spiff": spiff, "retention_previous": retention_previous[owner],
                             "retention_current": retention_current[owner],
                             "usd_unrounded": marginal_tiers(paid_on, rate, thresholds, multipliers) + spiff,
                             "metric": metric, "country": person["Country Code"]}
    # Only IC leaves enter rollups; summing intermediate managers would double count.
    for employee, record in records.items():
        if record["metric"] != "manager":
            continue
        role = record["role"]
        descendants = [key for key in carry if employee in paths[key] and records[key]["metric"] != "manager"]
        new = sum((carry[key]["new"] for key in descendants), ZERO)
        old = sum((carry[key]["existing"] for key in descendants), ZERO)
        plan = comp.loc[role]
        new_metric = plan["Metric I"] == "New Customer Net Revenue"
        record["credited_revenue"] = new if new_metric else old
        if str(plan["Metric II"]) == "Existing Customer Net Revenue":
            record["secondary_credit"] = old
            record["rate2"] = D(plan["Standard Payout 2"])
        record["usd_unrounded"] = ((record["credited_revenue"] * record["rate"]
                                      + record["secondary_credit"] * record["rate2"]) if record["employed_days"] else ZERO)
        record["threshold1"] = record["credited_revenue"]
        record["threshold2"] = record["credited_revenue"]
        record["descendant_hr_ids"] = dump(descendants)
        # Derive manager quota from current leaf role quotas, and expose disagreement with tab 5.
        relevant = [key for key in descendants if records[key]["metric"] == ("new" if new_metric else "existing")]
        hierarchy_quota = sum((D(comp.loc[records[key]["role"], "Monthly Quota Target"]) for key in relevant), ZERO)
        record["tab5_full_quota"] = D(plan["Monthly Quota Target"])
        record["hierarchy_full_quota"] = hierarchy_quota
        record["quota"] = hierarchy_quota * D(record["employed_days"]) / D(days)
        if hierarchy_quota != record["tab5_full_quota"]:
            warnings.append({"employee": employee, "reason": "Tab5 quota differs from current HR descendant quotas; calculated hierarchy quota is shown alongside tab value"})
    for employee, record in records.items():
        if record["country"] not in data["fx"].index:
            raise ValueError("Missing exchange rate; no currency invented")
        fx = data["fx"].loc[record["country"]]
        record["fx_units_per_usd"] = D(fx["1 USD ="])
        record["currency"] = fx["Code"]
        record["usd"] = money(record["usd_unrounded"])
        record["local_unrounded"] = convert_currency(record["usd_unrounded"], record["fx_units_per_usd"])
        record["local"] = money(record["local_unrounded"])
        record["actual_paid_usd"] = None
        record["status"] = "conditional_plan_calculation_not_actual_paid"
    return {"people": pd.DataFrame(records.values()), "events": events, "books": book_evidence,
            "warnings": warnings, "config": config, "total_usd": sum((r["usd"] for r in records.values()), ZERO),
            "hierarchy_paths": paths}


def compare(baseline, alternative):
    merged = baseline["people"][["hr_id", "name", "usd"]].merge(
        alternative["people"][["hr_id", "usd"]], on="hr_id", validate="one_to_one", suffixes=("_baseline", "_scenario"))
    merged["delta"] = merged.usd_scenario - merged.usd_baseline
    return merged


def exception_ledger(data, baseline, config):
    ledger = []
    def add(code, classification, records, people, revenue, commission, evidence, recommendation):
        ledger.append({"evidence_id": "exception_" + str(len(ledger) + 1), "topic": code,
                       "classification": classification, "affected_records": int(records),
                       "affected_employees": dump(sorted(str(v) for v in people)),
                       "revenue_exposure_usd": revenue, "commission_impact_usd": commission,
                       "source_evidence": evidence, "recommendation": recommendation})
    source = data["sources"]
    counts = source["opportunities"].groupby("crm_account_id").size()
    fact = data["base"]["shifts"]
    account = data["base"]["accounts"]
    duplicate_factors = account.set_index("app_account_id").crm_account_id.map(counts).to_dict()
    joined = fact.app_account_id.map(duplicate_factors).fillna(0)
    over = sum((D(n-1) * D(value)/100 for n, value in zip(joined, fact.base_net_revenue_cents) if n > 1 and pd.notna(value)), ZERO)
    add("join_multiplication", "confirmed_naive_join_error_not_actual_payroll_error", int(joined.gt(1).sum()), [], over, None,
        "Opportunity account counts + Shift Fact source rows; 7.3", "Use one Account Master row, never direct opportunity-to-shift many-to-many join")
    residue = source["opportunities"][source["opportunities"].closed_won_date.notna() & ~source["opportunities"].opportunity_stage.eq("Closed Won")]
    add("reversed_stage", "confirmed_source_residue", len(residue), [], None, None,
        dump(residue.source_row.tolist()), "Ignore win-date residue on non-Closed-Won rows (7.3); no paid-statement error claimed")
    unowned = baseline["events"][baseline["events"].owner_crm_id.isna()]
    add("missing_closer", "confirmed_unassigned_by_plan", len(unowned), [], sum(unowned.basis_credit_usd, ZERO), ZERO,
        dump(unowned.account.unique().tolist()), "No reassignment under 7.4; economic basis is not a payable commission estimate")
    denied = source["billing"][source["billing"].ticket_status.eq("denied") & source["billing"].billing_month.eq(pd.Timestamp(config["review_month"]).strftime("%Y-%m"))]
    add("denied_credit", "potential_naive_error", len(denied), [], sum((D(v) for v in denied.credit_amount), ZERO), None,
        dump(denied.source_row.tolist()), "Denied credits must not reduce AM credit; actual impact requires repricing affected employees")
    # Full-chain leave-one-account-out sensitivity, not an automatic payroll hold or invented fill threshold.
    activity = data["base"]["account_activity"]
    activity = activity[activity.account_type.eq("GTM") & activity.posted_month.eq(pd.Timestamp(config["review_month"]).strftime("%Y-%m"))]
    if len(activity):
        candidate = activity.sort_values("gsv_cents", ascending=False).iloc[0]
        target = account[account.app_account_id.eq(candidate.app_account_id)].iloc[0]
        held = calculate(data, config, excluded_accounts=[target.crm_account_id])
        differences = compare(baseline, held)
        affected = differences[differences.delta.ne(0)]
        impact = baseline["total_usd"] - held["total_usd"]
        add("posting_fill_anomaly", "suspected_anomaly_requires_approval", candidate.posted_shift_records,
            affected.hr_id.tolist(), D(candidate.gsv_cents)/100, impact,
            dump({"account": target.crm_account_id, "app_account": target.app_account_id,
                  "worked": int(candidate.worked_from_posted_cohort), "posted": int(candidate.posted_shift_records),
                  "fill": float(candidate.cohort_fill_ratio), "difference_rows": affected.astype(str).to_dict("records")}),
            "Investigate templates/authorization. Illustrative exclusion changes the FULL dependency chain; do not automatically withhold or infer fraud")
    alternate_config = dict(config, ramp_month_policy="month_end_status")
    alternate = calculate(data, alternate_config)
    difference = compare(baseline, alternate)
    affected = difference[difference.delta.ne(0)]
    add("partial_ramp_policy", "decision_requires_approval", len(affected), affected.hr_id.tolist(), None,
        alternate["total_usd"] - baseline["total_usd"], dump(affected.astype(str).to_dict("records")),
        "Resolve monthly versus month-end ramp eligibility; neither conditional result is labelled approved payroll")
    leave = baseline["events"][baseline["events"].employment_status.eq("after_end")]
    add("leaver_recognition", "confirmed_plan_timing", len(leave), leave.employee_hr_id.dropna().unique(),
        sum((D(v) for v in leave.credited_revenue.dropna()), ZERO), None,
        dump(leave.account.unique().tolist()), "Rep receives no post-employment credit, manager retains revenue; actual paid statement unavailable")
    return pd.DataFrame(ledger)


def communications(data, baseline, ledger):
    messages = data["tables"]["11. Slack Scrape"].copy()
    messages["__source_row"] = range(2, len(messages) + 2)
    people = data["people"].set_index("hr_id")
    evidence = []
    topic_map = {"posting_fill_anomaly": "gtm", "reversed_stage": "stage", "partial_ramp_policy": "ramp",
                 "leaver_recognition": "employment", "denied_credit": "billing", "join_multiplication": "renewal"}
    for _, item in ledger.iterrows():
        employees = json.loads(item.affected_employees)
        if item.topic == "posting_fill_anomaly":
            info = json.loads(item.source_evidence)
            account = data["base"]["accounts"]
            owner = account.loc[account.crm_account_id.eq(info["account"]), "current_am_hr_id"].iloc[0]
            if pd.notna(owner):
                employees.append(owner)
        if not employees:
            # Structural findings support only messages whose topic matches; not an exact complaint/account claim.
            employees = people.index.tolist()
        for employee in employees:
            if employee not in people.index:
                continue
            evidence.append({"slack_user_id": people.loc[employee, "slack_user_id"],
                             "topic": topic_map.get(item.topic, item.topic), "evidence_id": item.evidence_id,
                             "impact": item.commission_impact_usd if pd.notna(item.commission_impact_usd) else ZERO})
    topics = {"gtm": ["GTM", "fills", "posting far more", "template"], "stage": ["Negotiation"],
              "ramp": ["ramp", "floor", "accelerator"], "employment": ["Left the team", "left"],
              "billing": ["denied", "credit", "dispute"], "renewal": ["second opportunity", "same facility", "renewal"]}
    linked = link_messages(messages, pd.DataFrame(evidence), "slack_user_id", "slack_user_id", "message", topics)
    # Require three different supported issue topics, not three paraphrases of one issue.
    picks, seen = [], set()
    for index, row in linked.iterrows():
        if row.linked_evidence_ids and row.topics not in seen:
            picks.append(index)
            seen.add(row.topics)
        if len(picks) == 3:
            break
    escalations = linked.loc[picks].copy()
    if len(escalations):
        selected = escalations.iloc[0]
        response = draft_response(selected,
            "The export shows posted-volume GSV and worked-margin revenue separately. GTM gross credit applies to AEs, not AM retention. "
            "The linked ledger sizes a conditional account-exclusion sensitivity and retains the source rows. "
            "A low fill ratio supports an operational review, but does not by itself prove an error or authorize a different payout.", "Section 7.1, 7.6 and 7.7")
    else:
        response = "No sufficiently linked message was found; no factual response is invented."
    return linked, escalations, response


def execute(workbook=DEFAULT_WORKBOOK, output=None, config=None, export=True):
    config = dict(config or json.loads(CONFIG.read_text()))
    data = prepare(workbook)
    baseline = calculate(data, config)
    repriced = calculate(data, config, config["smb_scenario_multipliers"])
    differences = compare(baseline, repriced)
    ledger = exception_ledger(data, baseline, config)
    linked, escalations, response = communications(data, baseline, ledger)
    checks = {"roster_rows": len(data["people"]), "payout_rows": len(baseline["people"]),
              "unique_employees": baseline["people"].hr_id.is_unique,
              "scenario_delta_matches_rows": differences.delta.sum() == repriced["total_usd"] - baseline["total_usd"],
              "source_unchanged": fingerprint(workbook) == data["base"]["source_sha256"],
              "base_revenue_reconciliation": data["base"]["reconciliation"],
              "all_statuses_conditional": baseline["people"].status.eq("conditional_plan_calculation_not_actual_paid").all(),
              "actual_paid_available": False, "response_words": len(response.split())}
    assert checks["roster_rows"] == checks["payout_rows"] and checks["unique_employees"]
    assert checks["scenario_delta_matches_rows"] and checks["source_unchanged"] and checks["response_words"] <= 250
    result = {"data": data, "baseline": baseline, "scenario": repriced, "differences": differences,
              "ledger": ledger, "messages": linked, "escalations": escalations, "response": response, "checks": checks}
    if export:
        save(result, output or ROOT / "output" / "clipboard_benchmark")
    return result


def save(result, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline, alternate = result["baseline"], result["scenario"]
    summary = baseline["people"][["name", "role", "usd", "currency", "local", "quota", "credited_revenue", "spiff", "status"]].copy()
    tables = {"Summary": summary, "Plan baseline": baseline["people"], "Repriced scenario": alternate["people"],
              "Scenario delta": result["differences"], "Exception ledger": result["ledger"],
              "Credit events": baseline["events"], "Existing book evidence": baseline["books"],
              "Escalations": result["escalations"], "Assumptions": pd.DataFrame(baseline["config"]["assumptions"]),
              "Validation": pd.DataFrame([{"check": k, "result": dump(v)} for k,v in result["checks"].items()]),
              "Warnings": pd.DataFrame(baseline["warnings"]),
              "Rule steps": pd.DataFrame([
                  {"ref": "7.3", "formula": "stage='Closed Won'; earliest closed_won_date; selected effective_start_date", "implementation": "revenue.py:build_account_master"},
                  {"ref": "7.1", "formula": "worked margin by worked date; every posted charge as GSV by posted date", "implementation": "revenue.py:build_shift_fact"},
                  {"ref": "7.4-7.6", "formula": "F1 recognition by date; F2/F3 at window close: max(window-best_previous,0)*0.5; terms OR 0.03/role_rate", "implementation": "benchmark.py:event_credits + local_analyst.finance"},
                  {"ref": "7.7", "formula": "go_live < month_start; worked NR - approved billing_month credits; current AM", "implementation": "benchmark.py:existing_books"},
                  {"ref": "7.8-7.13", "formula": "inclusive employment; daily ramp floor; marginal tiers; leaf-only descendant rollups; same-book retention; USD times FX", "implementation": "benchmark.py:calculate + local_analyst.finance"},
              ])}
    for name, frame in tables.items():
        frame.to_csv(output / (name.lower().replace(" ", "_") + ".csv"), index=False)
    result["data"]["base"]["shifts"].to_csv(output / "shift_fact_lineage.csv", index=False)
    result["data"]["base"]["accounts"].to_csv(output / "account_master_lineage.csv", index=False)
    tables["Response draft"] = pd.DataFrame([{"draft": result["response"]}])
    evidence = {"source_workbook": result["data"]["workbook"], "source_sha256": fingerprint(result["data"]["workbook"]),
                "rules": result["data"]["rules"], "rules_sha256": fingerprint(result["data"]["rules"]),
                "parameters": baseline["config"], "validation": result["checks"],
                "source_code_sha256": {str(path.relative_to(ROOT)): fingerprint(path) for path in
                                        [Path(__file__), Path(__file__).with_name("revenue.py"), ROOT / "local_analyst" / "finance.py"]}}
    (output / "run_manifest.json").write_text(dump(evidence))
    (output / "parameters.json").write_text(dump(baseline["config"]))
    formulas = {}
    for sheet in ("Plan baseline", "Repriced scenario"):
        frame = tables[sheet].copy()
        from openpyxl.utils import get_column_letter
        def cell(col, row):
            return f"{get_column_letter(frame.columns.get_loc(col)+1)}{row}"
        formula = {}
        for index in range(len(frame)):
            row = index + 2
            c = lambda col: cell(col, row)
            # Native formulas recompute visible tier pricing, secondary metric, rounding and FX.
            formula[c("usd_unrounded")] = (f'=IF({c("employed_days")}>0,MIN({c("credited_revenue")},{c("threshold1")})*{c("rate")}+'
                f'MAX(MIN({c("credited_revenue")},{c("threshold2")})-{c("threshold1")},0)*{c("rate")}*{c("multiplier1")}+'
                f'MAX({c("credited_revenue")}-{c("threshold2")},0)*{c("rate")}*{c("multiplier2")}+'
                f'{c("secondary_credit")}*{c("rate2")}+{c("spiff")},0)')
            formula[c("usd")] = f'=ROUND({c("usd_unrounded")},2)'
            formula[c("local_unrounded")] = f'={c("usd_unrounded")}*{c("fx_units_per_usd")}'
            formula[c("local")] = f'=ROUND({c("local_unrounded")},2)'
        formulas[sheet] = formula
    export_excel(tables, output / "auditable_model.xlsx", formulas)
    total, changed = baseline["total_usd"], alternate["total_usd"]
    ranked = result["ledger"].sort_values("commission_impact_usd", ascending=False, na_position="last")
    report = ["# Clipboard benchmark: four tasks", "",
              "Status: conditional plan calculations, not approved payroll and not actual paid statements. "
              "No actual-paid file exists. See assumptions and full source lineage in the model.", "",
              f"Calculated baseline USD {total}; SMB repricing USD {changed}; delta USD {changed-total}.", "",
              "## Ranked exceptions", "", ranked.astype(str).to_markdown(index=False), "",
              "## Assumptions and limitations", "", dump(baseline["config"]["assumptions"]), "",
              "The model does not prove postings were authorized or that the limited extract contains complete historical GSV. "
              "Zero-fill suspicions are not automatically treated as fraud. Actual paid vs recomputed differences cannot be calculated without statements.", "",
              "## Three escalations", "", result["escalations"].astype(str).to_markdown(index=False), "",
              "## Draft (not sent)", "", result["response"], "", "## Validation", "", dump(result["checks"])]
    (output / "report.md").write_text("\n".join(report))
    sections = [
        {"title": "1. Conditional baseline and repricing", "paragraphs": [
            f"Plan-calculated baseline: USD {total:,.2f}. SMB AE 1.3x/1.5x scenario: USD {changed:,.2f}. Delta: USD {changed-total:,.2f}.",
            "Actual paid statements were not supplied. These are reproducible conditional plan calculations, not a claim about June actually paid. The unchanged quotas and carried credit mean an accelerator-only repricing affects eligible SMB AEs, not manager credited revenue.",
            "Inputs and source hashes are in run_manifest.json. All employee results, local currency, source rows, formulas and conditional interpretations are in auditable_model.xlsx and companion CSVs."],
         "rows": [["Validation", "Result"], ["Roster / modeled reps", f"{result['checks']['roster_rows']} / {result['checks']['payout_rows']}"],
                  ["Base revenue / GSV reconciliation", "Zero differences"], ["Unresolved source issues", "See exception and warnings sheets"]]},
        {"page_break": True, "title": "2. Exceptions and decisions", "paragraphs": [
            "Financial impacts below are labelled counterfactual sensitivities or exposures. They do not prove a wrong historical payment. Proposed exclusions require leadership approval.",
            "Partial-month ramp eligibility and payroll rounding order are not fully specified. Both monthly and month-end ramp treatments are calculated. The full extract is used without fabricating first-worked dates or historical transactions."],
         "rows": [["Issue", "Class", "USD commission sensitivity"]] + [[r.topic, r.classification.replace('_',' '), str(r.commission_impact_usd) if pd.notna(r.commission_impact_usd) else "not established"] for r in ranked.itertuples()]},
        {"page_break": True, "title": "3. Escalations and evidence-grounded response", "paragraphs": [
            "Messages are linked by employee and configured topic, not presumed to identify an exact unnamed account. The CSV ledger retains message source rows and linked calculation evidence."] +
            [f"Row {r.message_source_row}: {r.message[:240]} Evidence: {r.linked_evidence_ids}; exposure {r.financial_exposure}." for r in result["escalations"].itertuples()] +
            ["Draft (not sent): " + result["response"]]},
    ]
    export_pdf("Local Analyst - Clipboard Health benchmark", sections, output / "summary.pdf")
    evidence["output_sha256"] = {p.name: fingerprint(p) for p in output.iterdir() if p.is_file() and p.name != "run_manifest.json"}
    (output / "run_manifest.json").write_text(dump(evidence))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "clipboard_benchmark")
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    result = execute(args.workbook, args.output, json.loads(args.config.read_text()))
    print(dump({"baseline": result["baseline"]["total_usd"], "scenario": result["scenario"]["total_usd"],
                "delta": result["scenario"]["total_usd"]-result["baseline"]["total_usd"], "checks": result["checks"]}))


if __name__ == "__main__":
    main()
