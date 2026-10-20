"""Independent reliability evaluation. New evidence directories only; no model downloads.

python -B -m examples.reliability_audit --output NEW_DIR --unit
python -B -m examples.reliability_audit --output NEW_DIR --fixtures SHARED_DIR --live
Oracles use explicit fixture values, not the Agent's answer or SQL as ground truth.
"""
import argparse
from collections import Counter
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import pandas as pd
import requests
import sqlglot
from sqlglot import exp

from local_analyst import Workspace
from local_analyst.agent import AnalystAgent
from local_analyst.workspace import dump, fingerprint

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("qwen3.8:27b", "qwen3:8b")
QUESTIONS = {
    "inventory": "List all registered tables and their row counts. Do not run SQL.",
    "app_count": "How many App Export records are registered, including unfilled shifts? Inspect the schema and execute SQL to verify the exact count. Cite the query evidence ID.",
    "grouped": "For the collections invoices, use due as the date basis. For paid=true and due on or before 2026-06-30, report summed gross by rep and the overall total. Execute read-only SQL and cite evidence.",
    "scenario_export": "Use the user-registered collections plan. Run the baseline and a scenario changing only rate to 0.2; report baseline total local_fee, scenario total and delta. Query the baseline converted invoice rows and export those rows to CSV, Excel and PDF. Cite actual evidence IDs.",
    "multi_step": "Inspect the transactions schema. Define net as gross minus refund using exact decimal amounts. Calculate monthly net revenue by posted date, find the top five named customers by total net revenue, check missing customer values, and summarize all results with evidence. Exclude missing customers only from the customer ranking.",
    "join": "Inspect transactions and customers. Check the proposed join transactions.customer = customers.customer. Report duplicate-key and null-key counts, inner and left join row counts and multiplicity risks. These are proposed keys, not an approved business relationship. Do not assume a foreign key.",
    "recovery": "For collections invoices, execute SQL to return total gross for paid=true. If a tool fails, correct or retry it within the existing limits. Cite the query ID.",
    "uncertainty": "Calculate approved payroll commissions for July 2027 from these transactions. No compensation rules, employee mapping, currency policy, or July 2027 coverage declaration have been supplied. Do not invent rules or values.",
    "followup": "Now change only the paid filter to false and give total gross for that same table and date basis. Execute SQL and cite evidence.",
}


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def fixtures(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    content = (
        "order_id,customer,posted,gross,refund\n"
        "O1,A,2026-06-01,100.10,0\nO2,B,2026-06-02,50.20,5.20\n"
        "O3,A,2026-06-03,20.30,0\nO4,C,2026-06-04,40.40,0\n"
        "O5,B,2026-07-01,30.50,0\nO6,D,2026-07-02,60.60,10.60\n"
        "O7,,2026-06-10,10.70,0\nO7,A,2026-06-11,5.80,0\n"
    )
    for name, text in {"transactions.csv": content,
                       "customers.csv": "customer,region\nA,West\nB,East\nC,West\nD,North\nD,South\n"}.items():
        path = directory / name
        if path.exists() and path.read_text() != text:
            raise ValueError("Evaluation fixture already exists with different bytes")
        if not path.exists():
            path.write_text(text)
    return directory


def numeric(value):
    try:
        result = Decimal(str(value).replace(",", ""))
        return result if result.is_finite() else None
    except Exception:
        return None


def answer_numbers(answer):
    clean = re.sub(r"\b[a-zA-Z_][\w]*\b", " ", answer)
    return {numeric(m) for m in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?!\w)", clean)}


def history_trace(history):
    """Preserve actual calls on an HTTP failure, before ask() has returned its trace."""
    start = max((i for i, m in enumerate(history) if m.get("role") == "user"), default=-1)
    pending, result = [], []
    for message in history[start+1:]:
        if message.get("role") == "assistant":
            pending.extend(message.get("tool_calls") or [])
        elif message.get("role") == "tool" and pending:
            call = pending.pop(0).get("function", {})
            args = call.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            content = json.loads(message["content"])
            result.append({"tool": call.get("name"), "arguments": args,
                           "result": content.get("result", {"status": "error", **content})})
    return result


def grade(case, result, workspace, injected=False):
    """Check executed rows/dependencies/artifacts, then independently check final prose."""
    trace = result.get("trace", [])
    queries = []
    for item in trace:
        qid = item.get("result", {}).get("query_id")
        if item.get("tool") == "query_sql" and qid:
            try:
                evidence = workspace.query_evidence(qid)
                frame = workspace.query_result_frame(qid)
                queries.append((qid, evidence, frame))
            except ValueError:
                continue
    source_queries = [(qid, e, f) for qid, e, f in queries if e["dependencies"]]
    answer = result.get("answer", "")
    numbers = answer_numbers(answer)
    checks = {"controller_answered": result.get("status") == "answered"}
    if case == "inventory":
        expected = workspace.catalog
        checks.update(no_sql=not any(t["tool"] == "query_sql" for t in trace),
                      every_table_id=all(t in answer for t in expected),
                      all_counts=all(any(t in line and Decimal(m["row_count"]) in answer_numbers(line) for line in answer.splitlines()) for t, m in expected.items()))
    elif case == "uncertainty":
        checks = {"no_success_claim": result.get("status") in {"blocked", "incomplete"} or
                  bool(re.search(r"cannot|unable|not possible|missing|not provided|unsupported|cannot verify", answer, re.I)),
                  "no_artifacts": not (workspace.root / "outputs").exists(),
                  "no_fabricated_payroll": not re.search(r"(?:commissions?|payroll)\s*(?:is|=|:)\s*\$?\d", answer, re.I)}
    elif case == "scenario_export":
        scenario = [t["result"] for t in trace if t["tool"] == "run_scenario" and "delta" in t["result"]]
        checks["scenario_values"] = any([numeric(t[k]) for k in ("baseline", "scenario", "delta")] == [35, 70, 35] for t in scenario)
        exports = [t["result"] for t in trace if t["tool"] == "export_result" and t["result"].get("status") == "exported"]
        checks["all_formats_and_values"] = False
        for output in exports:
            if not {"csv", "excel", "pdf"}.issubset(output):
                continue
            try:
                csv = pd.read_csv(output["csv"], dtype=str)
                import openpyxl
                book = openpyxl.load_workbook(output["excel"]["path"], data_only=True)
                excel = list(book["Result"].values)
                idx = list(excel[0]).index("local_fee")
                values = [numeric(row[idx]) for row in excel[1:]]
                provenance = list(book["Provenance"].values)
                book.close()
                from pypdf import PdfReader
                pdf_text = "\n".join(p.extract_text() for p in PdfReader(output["pdf"]["path"]).pages)
                lineage = json.loads(Path(output["lineage"]).read_text())
                qid = output["query_id"]
                checks["all_formats_and_values"] = sorted(numeric(x) for x in csv.local_fee) == [10, 25] and sorted(values) == [10, 25] and qid in str(provenance) and qid in pdf_text and all(fingerprint(Path(output["lineage"]).parent / name) == digest for name, digest in lineage["export"]["files_sha256"].items())
            except Exception as error:
                checks["artifact_error"] = str(error)
        checks["numbers_in_answer"] = {Decimal(35), Decimal(70)} <= numbers
    elif case == "join":
        joins = [t["result"] for t in trace if t["tool"] == "check_join" and "inner_join_rows" in t["result"]]
        checks["join_oracle"] = any(t["inner_join_rows"] == 8 and t["left_join_rows"] == 9 and t["left"]["null_rows"] == 1 and t["right"]["duplicate_excess_rows"] == 1 and t["cardinality"] == "many_to_many" for t in joins)
        checks["multiplicity_disclosed"] = bool(re.search(r"many.to.many|multipli|duplicat", answer, re.I))
    else:
        checks["source_backed_query"] = bool(source_queries)
        checks["actual_query_cited"] = any(q in answer for q, _, _ in source_queries)
        values = {numeric(v) for _, _, frame in source_queries for row in frame.itertuples(index=False, name=None) for v in row}
        if case == "app_count":
            checks["actual_count"] = Decimal(107052) in values
            checks["correct_source"] = any(any(workspace.catalog[t].get("label") == "9. App Export" for t in e["dependencies"]) and sqlglot.parse_one(e["sql"], read="duckdb").find(exp.Count) is not None and sqlglot.parse_one(e["sql"], read="duckdb").find(exp.Where) is None and Decimal(107052) in {numeric(v) for row in f.itertuples(index=False, name=None) for v in row} for _, e, f in source_queries)
            checks["numbers_in_answer"] = Decimal(107052) in numbers
        elif case in {"recovery", "followup"}:
            expected = Decimal(230 if case == "recovery" else 999)
            checks["actual_total"] = expected in values
            checks["numbers_in_answer"] = expected in numbers
            if case == "recovery":
                checks["fault_injected_and_recovered"] = injected and any(t["result"].get("status") == "error" for t in trace)
        elif case == "grouped":
            found = {}
            for _, _, frame in source_queries:
                for row in frame.itertuples(index=False, name=None):
                    for rep, expected in (("P", Decimal(150)), ("Q", Decimal(80))):
                        if rep in row and expected in {numeric(v) for v in row}:
                            found[rep] = expected
            checks["grouped_rows"] = len(found) == 2
            checks["actual_total"] = Decimal(230) in values
            checks["numbers_in_answer"] = {Decimal(150), Decimal(80), Decimal(230)} <= numbers
        elif case == "multi_step":
            # Independent fixture oracle: June 222.30; July 80.50; A 126.20, B 75.50, D 50, C 40.40.
            required = {Decimal(x) for x in ("222.30", "80.50", "126.20", "75.50", "50", "40.40")}
            pairs = {"2026-06": Decimal("222.30"), "2026-07": Decimal("80.50"), "A": Decimal("126.20"), "B": Decimal("75.50"), "D": Decimal("50"), "C": Decimal("40.40")}
            checks["actual_monthly_and_customer_values"] = all(any(any((key in str(v) if key.startswith("2026-") else key == v) for v in row) and expected in {numeric(v) for v in row} for _, _, f in source_queries for row in f.itertuples(index=False, name=None)) for key, expected in pairs.items())
            checks["missing_customer_checked"] = any(t["tool"] == "check_quality" and t["arguments"].get("kind") == "missing" and t["result"].get("affected_rows") == 1 for t in trace) or any("IS NULL" in e["sql"].upper() and Decimal(1) in {numeric(v) for row in f.itertuples(index=False, name=None) for v in row} for _, e, f in source_queries)
            checks["numbers_in_answer"] = required <= numbers
    return {"status": "PASS" if all(value is True for value in checks.values()) else "FAIL", "checks": checks}


def run_live(output, shared, cases, repeat):
    availability = requests.get("http://localhost:11434/api/tags", timeout=15).json()
    installed = {m["name"] for m in availability["models"]}
    save(output / "installed.json", availability)
    results = []
    for model in MODELS:
        if model not in installed:
            results.extend({"model": model, "case": case, "status": "BLOCKED", "reason": "Exact tag absent"} for case in cases for _ in range(repeat))
            continue
        show = requests.post("http://localhost:11434/api/show", json={"model": model}, timeout=30).json()
        save(output / (model.replace(":", "_") + "_show.json"), show)
        if "tools" not in show.get("capabilities", []):
            raise ValueError("Model does not advertise tool support")
        for iteration in range(repeat):
            for case in cases:
                name = model.replace(":", "_").replace(".", "_") + "_" + case + "_" + str(iteration + 1)
                directory = output / name
                directory.mkdir()
                print("START", name, flush=True)
                with tempfile.TemporaryDirectory(prefix="analyst-live-") as temp:
                    w = Workspace(Path(temp) / "workspace", restore=False)
                    try:
                        if case in {"inventory", "app_count"}:
                            w.load(ROOT / "data/Sales Compensation Analyst Case Study.xlsx", "clipboard")
                        elif case in {"grouped", "scenario_export", "recovery", "followup"}:
                            w.load(ROOT / "examples/collections/invoices.csv", "collections")
                            w.register_plan(ROOT / "examples/collections/plan.json")
                        else:
                            w.load(shared / "transactions.csv", "transactions")
                            w.load(shared / "customers.csv", "customers")
                        agent = AnalystAgent(w, model=model, max_steps=8)
                        real_post = requests.post
                        requests_seen = []
                        def observed(url, **kwargs):
                            started = time.monotonic()
                            payload = copy.deepcopy(kwargs.get("json"))
                            requests_seen.append(payload)
                            with (directory / "http.jsonl").open("a") as stream:
                                stream.write(dump({"kind": "request", "payload": payload}) + "\n")
                            try:
                                response = real_post(url, **kwargs)
                                body = response.json()
                            except Exception as error:
                                with (directory / "http.jsonl").open("a") as stream:
                                    stream.write(dump({"kind": "exception", "error": str(error), "seconds": time.monotonic()-started}) + "\n")
                                raise
                            with (directory / "http.jsonl").open("a") as stream:
                                stream.write(dump({"kind": "response", "body": body, "seconds": time.monotonic()-started}) + "\n")
                            return response
                        injected = []
                        if case == "recovery":
                            real_query = agent.functions["query_sql"]
                            def fail_once(**kwargs):
                                if not injected:
                                    injected.append(True)
                                    raise ValueError("Evaluation injected transient query failure. Retry with valid read-only SQL.")
                                return real_query(**kwargs)
                            agent.functions["query_sql"] = fail_once
                        started = time.monotonic()
                        exception = None
                        result = {}
                        prior_evaluation = None
                        try:
                            with patch("local_analyst.agent.requests.post", side_effect=observed):
                                if case == "followup":
                                    prior = agent.ask(QUESTIONS["grouped"])
                                    save(directory / "prior_turn.json", prior)
                                    prior_evaluation = grade("grouped", prior, w)
                                result = agent.ask(QUESTIONS[case])
                                exception = result.get("error")
                        except Exception as error:
                            exception = repr(error)
                            result = {"status": "incomplete", "answer": "", "trace": history_trace(agent.history)}
                        evaluation = grade(case, result, w, bool(injected))
                        if prior_evaluation:
                            evaluation["checks"]["prior_turn_correct"] = prior_evaluation["status"] == "PASS"
                            if prior_evaluation["status"] != "PASS":
                                evaluation["status"] = "FAIL"
                        record = {"model": model, "case": case, "iteration": iteration+1, "question": QUESTIONS[case],
                                  "seconds": round(time.monotonic()-started, 3), "http_calls": len(requests_seen),
                                  "tool_calls": len(result.get("trace", [])), "exception": exception,
                                  "inference_executed": bool(requests_seen), **evaluation, "result": result}
                        if prior_evaluation:
                            record["prior_evaluation"] = prior_evaluation
                        if exception:
                            record["status"] = "FAIL"
                        save(directory / "result.json", record)
                        save(directory / "history.json", agent.history)
                        save(directory / "catalog.json", w.catalog)
                        try:
                            memory = requests.get("http://localhost:11434/api/ps", timeout=15).json()
                            save(directory / "ollama_ps.json", memory)
                            rss = subprocess.run(["ps", "-axo", "pid=,rss=,command="], capture_output=True, text=True, timeout=5)
                            (directory / "ollama_rss.txt").write_text("\n".join(line for line in rss.stdout.splitlines() if re.search(r"ollama|mlx.*server", line, re.I)))
                        except Exception as error:
                            save(directory / "memory_error.json", {"error": str(error)})
                        shutil.copyfile(w.audit_path, directory / "audit.jsonl")
                        # Keep small explicit export examples for independent final inspection.
                        if (w.root / "outputs").exists():
                            shutil.copytree(w.root / "outputs", directory / "artifacts")
                        if requests_seen:
                            first = {k: v for k, v in requests_seen[0].items() if k != "model"}
                            record["initial_payload_without_model_sha256"] = hashlib.sha256(dump(first).encode()).hexdigest()
                        results.append(record)
                        save(output / "summary.json", {"kind": "live_ollama", "results": results, "counts": dict(Counter(r["status"] for r in results))})
                        print("END", name, record["status"], record["seconds"], "seconds", flush=True)
                    finally:
                        w.db.close()
    save(output / "summary.json", {"kind": "live_ollama", "results": results, "counts": dict(Counter(r["status"] for r in results))})


def run_units(output, pattern="test*.py"):
    class Recorded(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.records = []
        def addSuccess(self, test):
            super().addSuccess(test); self.records.append({"test": test.id(), "status": "PASS"})
        def addFailure(self, test, error):
            super().addFailure(test, error); self.records.append({"test": test.id(), "status": "FAIL", "detail": self._exc_info_to_string(error, test)})
        def addError(self, test, error):
            super().addError(test, error); self.records.append({"test": test.id(), "status": "FAIL", "detail": self._exc_info_to_string(error, test)})
        def addSkip(self, test, reason):
            super().addSkip(test, reason); self.records.append({"test": test.id(), "status": "SKIPPED", "reason": reason})
        def addSubTest(self, test, subtest, error):
            super().addSubTest(test, subtest, error)
            if error:
                self.records.append({"test": test.id(), "status": "FAIL", "detail": str(subtest) + "\n" + self._exc_info_to_string(error, test)})
    with (output / "tests.log").open("w") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Recorded).run(unittest.defaultTestLoader.discover(str(ROOT), pattern=pattern))
    records = {}
    for item in result.records:
        if item["test"] not in records:
            records[item["test"]] = item
        elif item["status"] == "FAIL":
            records[item["test"]]["status"] = "FAIL"
            records[item["test"]]["detail"] = records[item["test"]].get("detail", "") + "\n" + item.get("detail", "")
    summary = {"kind": "deterministic", "run": result.testsRun, "counts": dict(Counter(r["status"] for r in records.values())), "records": list(records.values())}
    save(output / "summary.json", summary)
    print(dump({k: v for k, v in summary.items() if k != "records"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--unit", action="store_true")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--cases", nargs="+", choices=QUESTIONS, default=list(QUESTIONS))
    parser.add_argument("--repeat", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / "engine_manifest.json", {str(p.relative_to(ROOT)): fingerprint(p) for p in sorted((ROOT / "local_analyst").rglob("*.py"))})
    if args.unit:
        run_units(args.output, args.pattern)
    if args.live:
        if not args.fixtures:
            parser.error("--live requires shared --fixtures for identical source IDs and versions")
        run_live(args.output, fixtures(args.fixtures), args.cases, args.repeat)


if __name__ == "__main__":
    main()
