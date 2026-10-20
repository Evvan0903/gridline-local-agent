"""Inspectable JSON plans -> safe SQL / named deterministic financial operations."""

import copy
import hashlib
from datetime import date
from pathlib import Path
import re

import pandas as pd

from .finance import marginal_tiers, convert_currency, hierarchy_paths
from .workspace import dump, fingerprint, quote, parameter_names


def inspect_rules(path):
    path = Path(path).resolve()
    text = path.read_text()
    return {"path": str(path), "sha256": fingerprint(path), "text_preview": text[:18000],
            "warnings": ["Free-text rules are evidence, not executable code. Supply a reviewed JSON plan with rule references; unresolved interpretations must be listed as assumptions."]}


def validate_plan(plan, overrides=None):
    warnings, parameters = [], {}
    for name, definition in plan.get("parameters", {}).items():
        value = (overrides or {}).get(name, definition.get("value"))
        if value is None:
            warnings.append(f"Missing parameter: {name}")
            continue
        kind = definition.get("type")
        if kind == "number":
            from .finance import decimal
            decimal(value)
        elif kind == "date":
            date.fromisoformat(str(value))
        elif kind != "string":
            warnings.append(f"Unsupported parameter type for {name}: {kind}")
        parameters[name] = value
    if set(overrides or {}) - set(plan.get("parameters", {})):
        warnings.append("Scenario contains undeclared parameters")
    for assumption in plan.get("assumptions", []):
        if assumption.get("status") not in {"explicit_user_choice", "governing_rule"}:
            warnings.append("Unresolved assumption: " + assumption.get("description", "unspecified"))
    if not plan.get("steps"):
        warnings.append("No explicit calculation steps")
    seen = set()
    for step in plan.get("steps", []):
        if step.get("kind", "sql") not in {"sql", "tiers", "currency", "hierarchy"}:
            warnings.append("Unsupported rule operation: " + str(step.get("kind")))
        if not step.get("rule_ref"):
            warnings.append("Step has no rule evidence: " + step.get("id", "?"))
        if not re.fullmatch(r"[a-z][a-z0-9_]*", step.get("id", "")) or step["id"] in seen:
            warnings.append("Invalid/duplicate step identifier")
        seen.add(step.get("id"))
    return {"status": "blocked" if warnings else "validated", "warnings": warnings, "parameters": parameters}


def run_plan(workspace, plan, overrides=None, run_name="baseline"):
    validation = validate_plan(plan, overrides)
    if validation["status"] == "blocked":
        return {"validation": validation, "outputs": {}}
    parameters = validation["parameters"]
    bindings = {key: workspace.resolve(value) for key, value in plan.get("bindings", {}).items()}
    outputs, evidence = {}, []
    for step in plan["steps"]:
        kind = step.get("kind", "sql")
        if kind == "sql":
            sql = step["sql"]
            for key, value in bindings.items():
                sql = sql.replace("{{" + key + "}}", quote(value))
            if "{{" in sql:
                raise ValueError("Unresolved table/step binding")
            names = parameter_names(sql)
            if not names.issubset(parameters):
                raise ValueError(f"Missing SQL parameters: {names - parameters.keys()}")
            used = {k: parameters[k] for k in names}
            query = workspace.query(sql, used)
            frame = workspace.query_result_frame(query["query_id"])
            parents = {t: m["version_id"] for t, m in workspace.query_evidence(query["query_id"])["sources"].items()}
            evidence.append({"step": step, "query_id": query["query_id"], "sql": sql, "parameters": used})
        else:
            source = bindings.get(step["source"], step["source"])
            workspace.dependencies([source])
            parents = {source: workspace.catalog[source]["version_id"]}
            frame = workspace.frames[source].copy()
            if kind == "tiers":
                thresholds = [parameters[v] if isinstance(v, str) and v in parameters else v for v in step["thresholds"]]
                multipliers = [parameters[v] if isinstance(v, str) and v in parameters else v for v in step["multipliers"]]
                rate = parameters[step["rate_parameter"]]
                frame[step["output_column"]] = frame[step["amount_column"]].map(
                    lambda v: marginal_tiers(v, rate, thresholds, multipliers))
            elif kind == "currency":
                frame[step["output_column"]] = [convert_currency(a, b) for a, b in
                    zip(frame[step["amount_column"]], frame[step["rate_column"]])]
            else:
                paths, warnings = hierarchy_paths(frame, step["employee_column"], step["manager_column"], step.get("external_roots", []))
                if warnings:
                    return {"validation": {"status": "blocked", "warnings": warnings}, "outputs": outputs}
                frame = pd.DataFrame([(k, parent, depth + 1) for k, chain in paths.items() for depth, parent in enumerate(chain)],
                                     columns=["employee", "ancestor", "depth"])
            evidence.append({"step": step, "parameters": parameters})
        table = "model_" + re.sub(r"[^a-z0-9_]", "_", run_name.lower()) + "_" + step["id"]
        checks = []
        for check in step.get("checks", []):
            kind = check["kind"]
            if kind == "row_count":
                observed = len(frame)
                passed = observed == check["equals"]
            elif kind == "not_null":
                observed = int(frame[check["columns"]].isna().any(axis=1).sum())
                passed = observed == 0
            elif kind == "unique":
                observed = int(frame.duplicated(check["columns"], keep=False).sum())
                passed = observed == 0
            elif kind == "sum":
                from .finance import decimal
                values = frame[check["column"]]
                if values.isna().any():
                    observed, passed = "unresolved nulls", False
                else:
                    observed = sum((decimal(v) for v in values), decimal(0))
                    passed = abs(observed - decimal(check["equals"])) <= decimal(check.get("tolerance",0))
            else:
                raise ValueError(f"Unsupported validation check: {kind}")
            checks.append({"check": check, "observed": observed, "passed": passed})
        evidence[-1]["checks"] = checks
        if any(not item["passed"] for item in checks):
            event = workspace.record("model_validation_failed", step=step, checks=checks, parameters=parameters)
            return {"validation": {"status":"blocked","warnings":["Output validation failed"], "checks":checks},
                    "outputs":outputs, "evidence_id":event}
        workspace.register(frame, table, {"kind": "derived", "rule_ref": step["rule_ref"], "run": run_name,
                                         "parents": parents, "provenance": evidence[-1],
                                         "plan_sha256": hashlib.sha256(dump(plan).encode()).hexdigest(),
                                         "model_engine_sha256": fingerprint(__file__),
                                         "financial_engine_sha256": fingerprint(Path(__file__).with_name("finance.py")),
                                         "parameters": parameters, "assumptions": plan.get("assumptions", []),
                                         "validation": "validated"})
        evidence[-1]["output_version"] = workspace.catalog[table]["version_id"]
        bindings[step["id"]] = table
        outputs[step["id"]] = table
    event = workspace.record("model_run", plan=plan, parameters=parameters, run=run_name, evidence=evidence,
                             inputs=workspace.inputs, validation=validation)
    return {"validation": validation, "outputs": outputs, "evidence_id": event, "parameters": parameters,
            "output_schemas": {step: {"columns": list(workspace.frames[t].columns),
                                      "row_count": workspace.catalog[t]["row_count"]} for step, t in outputs.items()},
            "dataset_versions": {t: workspace.catalog[t]["version_id"] for t in outputs.values()}}


def scenario(workspace, plan, overrides, metric_step, metric_column):
    baseline = run_plan(workspace, plan, run_name="baseline")
    if baseline["validation"]["status"] != "validated":
        return {"baseline": baseline, "status": "blocked"}
    schemas = baseline["output_schemas"]
    if metric_step not in schemas or metric_column not in schemas[metric_step]["columns"]:
        raise ValueError(f"Unknown scenario metric {metric_step}.{metric_column}. Available output step columns: {dump(schemas)}. Select the step containing the requested metric before retrying")
    alternative = run_plan(workspace, plan, overrides, run_name="scenario")
    if baseline["validation"]["status"] != "validated" or alternative["validation"]["status"] != "validated":
        return {"baseline": baseline, "scenario": alternative, "status": "blocked"}
    from .finance import decimal
    totals = []
    for run in (baseline, alternative):
        values = workspace.frames[run["outputs"][metric_step]][metric_column]
        if values.isna().any():
            raise ValueError("Scenario metric contains unresolved amounts")
        totals.append(sum((decimal(v) for v in values), decimal(0)))
    return {"baseline": str(totals[0]), "scenario": str(totals[1]), "delta": str(totals[1] - totals[0]),
            "metric": {"step": metric_step, "column": metric_column,
                       "baseline_table": baseline["outputs"][metric_step], "scenario_table": alternative["outputs"][metric_step]},
            "dataset_versions": {**baseline["dataset_versions"], **alternative["dataset_versions"]},
            "baseline_evidence": baseline["evidence_id"], "scenario_evidence": alternative["evidence_id"]}
