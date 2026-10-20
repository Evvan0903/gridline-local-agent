"""Read-only schema and exact-key relationship discovery; no agent or business logic.

Run `.venv/bin/python workbook_discovery.py` to regenerate the Markdown report.
The public discovery functions return JSON-serializable dictionaries for future tools.
"""

import argparse
from collections import Counter
from datetime import date, datetime
import hashlib
from itertools import combinations
from numbers import Number
from pathlib import Path
import re

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_WORKBOOK = BASE_DIR / "data" / "Sales Compensation Analyst Case Study.xlsx"
DEFAULT_REPORT = BASE_DIR / "reports" / "workbook_relationships.md"


def _label(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def _is_id(column):
    return "id" in _label(column).split()


def _key_kind(column):
    label = _label(column)
    if _is_id(column):
        return "identifier"
    if label in {"role", "current role", "previous role"}:
        return "role"
    if label in {"country code", "country", "code"}:
        return label
    return None


from local_analyst.quality import profile_key, key_counts as _key_counts


def profile_table(frame):
    """Profile full data, not a sample. PKs are snapshot candidates, not constraints."""
    columns = []
    for column in frame.columns:
        stats = profile_key(frame, column)
        columns.append({
            "name": str(column),
            "dtype": str(frame[column].dtype),
            "value_types": sorted({type(v).__name__ for v in frame[column].dropna()}),
            "candidate_id": _is_id(column),
            "key_kind": _key_kind(column),
            **{key: value for key, value in stats.items() if key != "columns"},
        })
    primary_keys = [[c["name"]] for c in columns
                    if c["key_kind"] and c["is_primary_key_candidate"]]
    # Only search small, minimally unique combinations when no single candidate exists.
    composites = []
    if not primary_keys:
        pool = [c for c in frame.columns if _is_id(c) or _label(c) in
                {"timestamp", "channel", "billing month", "ticket type"}]
        for size in (2, 3):
            for candidate in combinations(pool, size):
                if any(set(key).issubset(candidate) for key in composites):
                    continue
                if profile_key(frame, candidate)["is_primary_key_candidate"]:
                    composites.append(list(candidate))
    return {"row_count": len(frame), "columns": columns,
            "candidate_id_columns": [c["name"] for c in columns if c["candidate_id"]],
            "potential_primary_keys": primary_keys,
            "potential_composite_keys": composites}


def _frame_from_rows(raw, header_index, end, width):
    headers = raw.iloc[header_index, :width].tolist()
    if any(pd.isna(h) for h in headers) or len(set(headers)) != len(headers):
        raise ValueError(f"Missing or duplicate column headers at row {header_index + 1}")
    frame = raw.iloc[header_index + 1:end, :width].copy()
    frame.columns = headers
    return frame.reset_index(drop=True).infer_objects().convert_dtypes()


def load_workbook_tables(workbook_path=DEFAULT_WORKBOOK):
    """Read all sheets. The mixed Accelerators layout is split at its actual headers.

    Blank cells are null; literal NA/na/N/A strings are preserved. No trimming,
    case conversion, identifier casting, formula evaluation, or source writes occur.
    Unknown multi-region layouts require review rather than invented headers.
    """
    tables, sheets = {}, []
    with pd.ExcelFile(workbook_path) as workbook:
        for sheet in workbook.sheet_names:
            raw = pd.read_excel(workbook, sheet_name=sheet, header=None,
                                keep_default_na=False, na_values=[""])
            regions = []
            if sheet == "6. Accelerators":
                # Workbook-specific layout evidence, not a compensation rule.
                headers = [i for i in range(len(raw))
                           if raw.iat[i, 0] == "Role"]
                if not headers:
                    raise ValueError("Accelerators: no expected Role table headers found")
                for start in headers:
                    width = int(raw.iloc[start].notna().sum())
                    end = start + 1
                    while (end < len(raw) and raw.iloc[end, :width].notna().all()
                           and end not in headers):
                        end += 1
                    regions.append((start, end, width))
            elif not raw.empty:
                regions = [(0, len(raw), len(raw.columns))]
            sheet_tables, classified_rows = [], set()
            for start, end, width in regions:
                table_id = sheet if len(regions) == 1 else f"{sheet} [header row {start + 1}]"
                frame = _frame_from_rows(raw, start, end, width)
                tables[table_id] = frame
                sheet_tables.append({"table_id": table_id, "header_row": start + 1,
                                     "first_data_row": start + 2, "last_data_row": end,
                                     "row_count": len(frame)})
                classified_rows.update(range(start, end))
            unclassified = [i + 1 for i in range(len(raw))
                            if i not in classified_rows and raw.iloc[i].notna().any()]
            sheets.append({"sheet_name": sheet, "physical_rows": len(raw),
                           "blank_rows": int(raw.isna().all(axis=1).sum()),
                           "data_rows": sum(t["row_count"] for t in sheet_tables),
                           "tables": sheet_tables, "note_or_unclassified_rows": unclassified})
    return tables, sheets


from local_analyst.quality import relationship_counts as _relationship, test_relationship


def discover_relationships(tables):
    """Propose keys from headers or exact ID overlap, then audit every proposal.

    Differently named IDs need nonzero overlap, except explicit manager/closer
    aliases which are tested even with zero matches. Role aliases are hypotheses.
    Date coincidences, amounts, names and free text never create relationships.
    """
    fields = [(table, column, _key_kind(column))
              for table, frame in tables.items() for column in frame.columns
              if _key_kind(column)]
    cache = {(table, column): _key_counts(tables[table], column)
             for table, column, _ in fields}
    results = []
    for (lt, lc, lk), (rt, rc, rk) in combinations(fields, 2):
        same_table = lt == rt
        aliases = {_label(lc), _label(rc)}
        explicit_id_alias = aliases in ({"manager id", "hr id"},
                                        {"closed by owner id", "crm owner id"})
        left_counts, left_nulls = cache[(lt, lc)]
        right_counts, right_nulls = cache[(rt, rc)]
        if same_table and not explicit_id_alias:
            continue
        if _label(lc) == _label(rc):
            reason = "same normalized header (hypothesis, then exact values tested)"
        elif explicit_id_alias:
            reason = "manager/owner header alias hypothesis, then exact values tested"
        elif lk == rk == "role" and "role" in aliases:
            reason = "Role versus Current/Previous Role header hypothesis"
        elif lk == rk == "identifier" and left_counts.keys() & right_counts.keys():
            reason = "different ID headers with observed exact value overlap; semantics need review"
        else:
            continue
        results.append({"left_table": lt, "right_table": rt,
                        "left_on": [lc], "right_on": [rc], "candidate_reason": reason,
                        **_relationship(left_counts, left_nulls, right_counts, right_nulls)})
    return results


def _workbook_path_checks(tables):
    """Workbook-specific cross-checks using only existing columns and tested joins."""
    required = {"1. Sales Roster", "2. HR Export", "5. Compensation", "8. Opportunity Export",
                "7. Opportunity to ID Mapping", "9. App Export", "10. Billing Export"}
    if not required.issubset(tables):
        return {}
    roster, hr = tables["1. Sales Roster"], tables["2. HR Export"]
    scoped_hr = hr[hr["hr_id"].isin(roster["hr_id"].dropna())]
    role_coverage = test_relationship(scoped_hr, tables["5. Compensation"],
                                      "Current Role", "Role")
    # Check account identity along Billing -> shift -> app account -> account map.
    billing = tables["10. Billing Export"]
    app = tables["9. App Export"]
    mapping = tables["7. Opportunity to ID Mapping"]
    shift_link = test_relationship(billing, app, "shift_id", "shift_id")
    map_link = test_relationship(app, mapping, "app_account_id", "app_account_id")
    if not shift_link["left_fk_to_right"] or not map_link["left_fk_to_right"]:
        account_check = {"status": "not_tested", "reason": "Join path is not fully verified"}
    else:
        joined = billing[["shift_id", "billing_account_id"]].merge(
            app[["shift_id", "app_account_id"]], on="shift_id", how="left",
            validate="many_to_one").merge(
                mapping[["app_account_id", "billing_account_id"]], on="app_account_id",
                how="left", validate="many_to_one", suffixes=("_billing", "_mapping"))
        mismatch = int(joined["billing_account_id_billing"].ne(
            joined["billing_account_id_mapping"]).sum())
        account_check = {"status": "verified" if mismatch == 0 else "needs_investigation",
                         "tested_rows": len(joined), "mismatched_account_rows": mismatch}
    opportunity = tables["8. Opportunity Export"]
    opportunity_link = test_relationship(opportunity, mapping, "crm_account_id", "crm_account_id")
    if opportunity_link["left_fk_to_right"]:
        opportunity_accounts = opportunity[["crm_account_id"]].merge(
            mapping[["crm_account_id", "app_account_id"]], on="crm_account_id",
            how="left", validate="many_to_one")
        account_join = test_relationship(opportunity_accounts, app,
                                         "app_account_id", "app_account_id")
    else:
        account_join = {"status": "not_tested", "reason": "Account mapping is not verified"}
    return {"sales_roster_current_role_to_compensation": role_coverage,
            "billing_shift_account_consistency": account_check,
            "opportunity_to_shift_account_join": account_join}


def discover_workbook(workbook_path=DEFAULT_WORKBOOK):
    """Full read-only audit, suitable for a future no-argument agent-tool wrapper."""
    path = Path(workbook_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    frames, sheets = load_workbook_tables(path)
    result = {"workbook": path.name, "sha256": digest, "sheets": sheets,
              "tables": {name: profile_table(frame) for name, frame in frames.items()},
              "relationships": discover_relationships(frames),
              "path_checks": _workbook_path_checks(frames)}
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Workbook changed during discovery; rerun against a stable file")
    return result


def test_workbook_relationship(left_table, right_table, left_on, right_on,
                               workbook_path=DEFAULT_WORKBOOK):
    """Tool-friendly audit of explicitly selected real table/column names."""
    frames, _ = load_workbook_tables(workbook_path)
    if left_table not in frames or right_table not in frames:
        raise ValueError("Unknown table; use discover_workbook to inspect available names")
    return test_relationship(frames[left_table], frames[right_table], left_on, right_on)


def _md(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(audit):
    """Render the audit as Markdown; all quantitative claims come from the audit."""
    lines = ["# Workbook Schema & Relationship Discovery", "",
             f"Source: `{audit['workbook']}`. SHA-256: `{audit['sha256']}`.", "",
             "## Method and limits", "",
             "Read-only full-row profiling of all worksheets. No commissions or business rules "
             "are calculated. The existing Agent Loop and tools are unchanged.", "",
             "Blank cells are null. Literal strings such as `NA`, `na`, and `N/A` remain text "
             "(notably Namibia's `NA` country code). Values are not trimmed, case-folded or "
             "cast to strings for matching. Dtypes are pandas-inferred types, not Excel constraints. "
             "Formula cells use the reader's cached values; formulas are not recalculated.", "",
             "Candidate IDs contain a separate `id` header token. Role/country/code columns "
             "are also tested as dimension keys. Single-column null-free unique candidates and "
             "minimal 2–3-column combinations (ID/time/channel/month/type) are reported. "
             "Incidental uniqueness in names, messages, dates or amounts does not establish an ID. "
             "This is a bounded search, not exhaustive key discovery.", "",
             "Candidate joins come from identical headers, explicit manager/closer aliases, "
             "Role aliases, or overlapping exact IDs. Every proposed pair is tested. "
             "No-overlap aliases are retained as rejected candidates. Differently named ID pairs "
             "without overlap are not proposed. Composite relationships can be tested explicitly "
             "with `test_workbook_relationship`; they are not guessed automatically.", "",
             "**Verified data relationship** means all populated foreign-key values exist in "
             "a unique, null-free target key in this file. It does not prove business meaning, "
             "historical validity, or future uniqueness. Null foreign keys remain missing and "
             "never match each other. Partial matches and many-to-many joins need investigation. "
             "Unused parent rows are not orphan children.", "",
             "Duplicate excess counts repeated rows after the first occurrence; duplicate rows "
             "counts every row in a repeated-key group. Cardinality uses all non-null keys on "
             "each side, including unmatched groups. Join row counts are exact products of key "
             "frequencies, without materializing a potentially large join.", "",
             "## Worksheet inventory", "",
             "| Worksheet | Physical rows incl. headers/notes | Data rows | Tables | Note/unclassified Excel rows |",
             "| --- | ---: | ---: | ---: | --- |"]
    for sheet in audit["sheets"]:
        lines.append(f"| {_md(sheet['sheet_name'])} | {sheet['physical_rows']} | "
                     f"{sheet['data_rows']} | {len(sheet['tables'])} | "
                     f"{', '.join(map(str, sheet['note_or_unclassified_rows'])) or 'none'} |")
    lines += ["", "Accelerators is a mixed-layout worksheet: row 1 is a note, rows 2 and 8 "
              "are separate headers, rows 3–4 and 9–10 are data, row 5 is blank, and rows 6–7 "
              "are notes/labels. These two tables are profiled separately; note text is not data.", "",
              "## Table schemas and potential keys", ""]
    for name, table in audit["tables"].items():
        keys = table["potential_primary_keys"] + table["potential_composite_keys"]
        key_text = "; ".join(" + ".join(f"`{c}`" for c in key) for key in keys) or "none identified"
        lines += [f"### {name}", "", f"Rows: **{table['row_count']}**. "
                  f"Potential keys (observed uniqueness only): {key_text}.", "",
                  "| Column | Inferred dtype / value types | Null rows | Distinct non-null | Duplicate excess | Candidate ID |",
                  "| --- | --- | ---: | ---: | ---: | --- |"]
        for col in table["columns"]:
            lines.append(f"| {_md(col['name'])} | {_md(col['dtype'])} / "
                         f"{', '.join(col['value_types'])} | {col['null_rows']} | "
                         f"{col['distinct_non_null_keys']} | {col['duplicate_excess_rows']} | "
                         f"{'yes' if col['candidate_id'] else 'no'} |")
        lines.append("")
    lines += ["## Tested relationships", "",
              "L/R refer to the columns in the first two cells. Unmatched counts exclude "
              "nulls and count source rows, not distinct keys. FK direction identifies which "
              "side can safely look up a unique target. A left lookup should use pandas "
              "`validate='many_to_one'` (or `one_to_one` when both keys are unique), with "
              "explicit null and unmatched checks.", ""]
    for status, title in [("verified_data_relationship", "Verified in this snapshot"),
                          ("needs_investigation", "Partial or ambiguous candidates"),
                          ("rejected_no_overlap", "Rejected candidates (no exact overlap)")]:
        lines += [f"### {title}", "",
                  "| Left table.column | Right table.column | Cardinality L:R | Matched keys | Unmatched rows L/R | Null rows L/R | Duplicate excess L/R | Inner / left join rows | Verified FK direction |",
                  "| --- | --- | --- | ---: | --- | --- | --- | --- | --- |"]
        for rel in audit["relationships"]:
            if rel["status"] != status:
                continue
            direction = ", ".join(label for flag, label in
                                  [(rel["left_fk_to_right"], "L→R"),
                                   (rel["right_fk_to_left"], "R→L")] if flag) or "none"
            lines.append(f"| {_md(rel['left_table'])}.`{_md(rel['left_on'][0])}` | "
                         f"{_md(rel['right_table'])}.`{_md(rel['right_on'][0])}` | "
                         f"{rel['cardinality']} | {rel['matched_distinct_keys']} | "
                         f"{rel['left_unmatched_rows']} / {rel['right_unmatched_rows']} | "
                         f"{rel['left']['null_rows']} / {rel['right']['null_rows']} | "
                         f"{rel['left']['duplicate_excess_rows']} / {rel['right']['duplicate_excess_rows']} | "
                         f"{rel['inner_join_rows']} / {rel['left_join_rows']} | {direction} |")
        lines.append("")
    lines += _interpretation(audit)
    return "\n".join(lines) + "\n"


def _interpretation(audit):
    """Interpret the supplied workbook layout without inventing eligibility/timing rules."""
    lines = ["## Data grain and recommended join paths", "",
             "Data grain is what one row represents. The following interpretations use sheet "
             "labels plus the measured keys; business owners must confirm their meaning.", "",
             "- Sales Roster and HR Export: one row per `hr_id` in this snapshot. The roster "
             "is a subset of HR, not the entire employee population. Join on `hr_id`; retain "
             "the roster as the left side when investigating sales employees.",
             "- Exchange Rates: one row per `Country Code` (also unique by Country). Currency "
             "`Code` repeats across countries, so it is not a unique lookup key. No rate-effective "
             "date column exists; the correct historical conversion date needs confirmation.",
             "- Ramp Terms and Compensation: one row per `Role`. Each Accelerators subtable "
             "also has one row per Role within that region. These are different tables and must "
             "not be joined as if every role exists in both. Matching role labels does not "
             "establish eligibility, timing or payout rules.",
             "- Opportunity to ID Mapping: one row per cross-system account mapping; all three "
             "account ID columns are independently unique. Use the actual mapping, never assume "
             "CRM/app/billing UUIDs have equal values.",
             "- Opportunity Export: one row per `opportunity_id`, not per `crm_account_id`. "
             "Multiple opportunities can belong to an account. Link to the mapping on "
             "`crm_account_id`; link owner and closer separately to roster `crm_owner_id`. "
             "Those two fields are different roles, not interchangeable business rules.",
             "- App Export: one row per `shift_id`, with many shifts per `app_account_id`. "
             "Link to the mapping using `app_account_id`.",
             "- Billing Export: one row per `shift_id` in this file. Do not assume all future "
             "tickets will be unique by shift. `billing_account_id` or account plus month is "
             "not an established row identifier. Link to app on `shift_id` for row-level "
             "reconciliation, and to the mapping on `billing_account_id` for account identity.",
             "- Slack Scrape: multiple messages per `slack_user_id`. Candidate composite keys "
             "are shown above, but no explicit message ID is supplied. Timestamp uniqueness "
             "in this file does not guarantee a durable message key. Join to roster using "
             "`slack_user_id`; message text is context, not an executable business rule.", "",
             "### Join multiplication risk", "",
             "Do not directly join opportunity rows to shift rows by account and treat the "
             "result as one opportunity per shift. The account mapping verifies identity, "
             "not opportunity attribution. Repeated account keys on both sides can multiply "
             "rows. Any opportunity-to-shift date/ownership allocation needs separately "
             "validated business rules. Joining Billing to App by account similarly loses "
             "shift grain; use the tested `shift_id` path instead.", "",
             "## Additional path checks", ""]
    checks = audit["path_checks"]
    role = checks.get("sales_roster_current_role_to_compensation")
    if role:
        lines += [f"- Restricting HR to roster `hr_id` values: {role['left']['non_null_rows']} "
                  f"populated current-role rows, {role['left_unmatched_rows']} unmatched "
                  f"against Compensation.Role. Status: {role['status']}. Full HR includes "
                  "non-sales roles; those unmatched roles must not be treated as missing "
                  "sales plans without this scope check."]
    account = checks.get("billing_shift_account_consistency")
    if account:
        if account["status"] == "not_tested":
            lines.append(f"- Billing account path not tested: {account['reason']}.")
        else:
            lines.append(f"- Billing → App by shift_id → Mapping by app_account_id: "
                         f"{account['tested_rows']} rows tested, "
                         f"{account['mismatched_account_rows']} account mismatches; "
                         f"status: {account['status']}. This checks account identity, "
                         "not whether credits are payable.")
    account_join = checks.get("opportunity_to_shift_account_join")
    if account_join and account_join["status"] != "not_tested":
        lines.append(f"- Opportunity → Mapping → App on account ID: observed "
                     f"{account_join['cardinality']}; an inner join would produce "
                     f"{account_join['inner_join_rows']} rows from "
                     f"{account_join['left']['non_null_rows']} opportunity rows and "
                     f"{account_join['right']['non_null_rows']} shift rows. "
                     f"{account_join['left_unmatched_rows']} opportunity rows have no "
                     "app-account match. Account equality does not identify which "
                     "opportunity owns each shift.")
    lines += ["", "## Still needs investigation", "",
              "- HR manager references with no matching hr_id must be reviewed; missing "
              "manager values are reported separately and are not silently filled.",
              "- Role joins against all HR are broader than the sales roster. Previous roles "
              "and current roles need date-aware interpretation; no effective-date rules "
              "are inferred here.",
              "- Nullable closer IDs and unused mapped accounts may be legitimate, but their "
              "business causes are unverified. A missing app account does not itself prove "
              "a missing shift, and unused parent rows are not broken foreign keys.",
              "- Table 6 layout extraction is specific to the observed Role headers. New note "
              "rows, missing cells or additional regions need review; note/unclassified "
              "Excel row numbers are retained in the inventory.",
              "- Snapshot uniqueness does not replace source-system constraints. No invented "
              "join keys, calculated commissions, eligibility rules, currency timing rules "
              "or opportunity allocation rules have been added.", "",
              "## Reuse and verification", "",
              "`discover_workbook(path)` returns the complete JSON-serializable audit; "
              "`profile_table(frame)` and `profile_key(frame, columns)` inspect in-memory "
              "data; `test_relationship(left, right, left_on, right_on)` audits explicit "
              "single/composite keys; `test_workbook_relationship(...)` accepts real table "
              "names and column names and can later be exposed to Qwen3. No tools are "
              "automatically registered and importing the module does not write files.", "",
              "Regenerate: `.venv/bin/python workbook_discovery.py`. Run tests: "
              "`.venv/bin/python -m unittest -v test_workbook_discovery test_main`. "
              "Only the Markdown report is written. The workbook SHA-256 is checked before "
              "and after discovery and in the workbook integration test."]
    return lines


def write_report(audit, report_path=DEFAULT_REPORT):
    """Only write Markdown, never an Excel file."""
    path = Path(report_path)
    if path.suffix.lower() != ".md":
        raise ValueError("Report output must be a .md file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(audit), encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    audit = discover_workbook(args.workbook)
    print(write_report(audit, args.report))
    print(f"Inspected {len(audit['sheets'])} worksheets and {len(audit['tables'])} tables; "
          f"tested {len(audit['relationships'])} candidate relationships.")
