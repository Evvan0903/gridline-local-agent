"""Three real natural-language questions through local Qwen3 and generic SQL tools."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import requests

from local_analyst import Workspace
from local_analyst.agent import AnalystAgent
from local_analyst.workspace import dump
from local_analyst.config import resolve_model


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).with_name("qwen_demo_config.json")


def check_model(model, ollama_url):
    """Metadata-only preflight: never calls chat/generate or pulls a model."""
    if urlparse(ollama_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Ollama endpoint must be local")
    base = ollama_url.rstrip("/")
    response = requests.get(base + "/api/tags", timeout=15)
    response.raise_for_status()
    installed = response.json().get("models", [])
    exact = next((item for item in installed if model in {item.get("name"), item.get("model")}), None)
    result = {"model": model, "ollama_url": base, "installed": exact is not None,
              "available": False, "installed_tags": sorted(item.get("name", item.get("model", "")) for item in installed)}
    if exact is None:
        result["reason"] = "Exact model tag is not installed; no demonstration was started"
        return result
    response = requests.post(base + "/api/show", json={"model": model}, timeout=30)
    response.raise_for_status()
    info = response.json()
    result.update({"digest": exact.get("digest"), "size_bytes": exact.get("size"),
                   "capabilities": info.get("capabilities", exact.get("capabilities", [])),
                   "details": info.get("details", exact.get("details", {}))})
    result["available"] = "tools" in result["capabilities"]
    result["reason"] = ("Exact tag installed and metadata readable; tool capability confirmed. No inference run."
                        if result["available"] else "Tool capability not confirmed; demonstration blocked")
    return result


def build_cases(by_label):
    """Original questions and source-derived acceptance values, shared by both runners."""
    shifts, opportunities = by_label["9. App Export"], by_label["8. Opportunity Export"]
    billing = by_label["10. Billing Export"]
    cases = [
        ("How many posted shift records are in the App Export table, including unfilled shifts? Inspect schemas and execute SQL to verify the count. Include the query evidence ID.", [len(shifts)]),
        ("Which opportunity stages appear in Opportunity Export, and how many records are in each stage? Use grouped SQL counts and cite query evidence.", opportunities.opportunity_stage.value_counts().tolist()),
        ("In Billing Export, show the number of tickets and summed credit_amount for each ticket_status. These are raw ticket amounts, not commission adjustments. Use grouped SQL and cite query evidence.",
         billing.groupby('ticket_status').size().tolist() + billing.groupby('ticket_status').credit_amount.sum().astype(int).tolist()),
    ]
    return cases


def evaluate_result(result, expected):
    """Keep the archived SQL-execution + answer-number criteria unchanged."""
    queries = [item for item in result.get("trace", [])
               if item["tool"] == "query_sql" and "query_id" in item["result"]]
    answer_digits = result.get("answer", "").replace(",", "")
    return {"sql_executed": bool(queries),
            "source_values_in_answer": all(str(value) in answer_digits for value in expected),
            "passed": bool(queries) and all(str(value) in answer_digits for value in expected)}


def main(argv=None):
    config = json.loads(CONFIG_PATH.read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=config.get("model") or resolve_model(purpose="test_model"))
    parser.add_argument("--ollama-url", default=config["ollama_url"])
    parser.add_argument("--check-only", action="store_true", help="Verify local tag and tools without loading data or running inference")
    args = parser.parse_args(argv)
    availability = check_model(args.model, args.ollama_url)
    readiness = ROOT / "output" / "qwen_demos" / "readiness.json"
    readiness.parent.mkdir(parents=True, exist_ok=True)
    readiness.write_text(dump({"checked_at_utc": datetime.now(timezone.utc).isoformat(), **availability}))
    print(dump(availability), flush=True)
    if args.check_only or not availability["available"]:
        return 0 if availability["available"] else 2
    baseline = json.loads((ROOT / config["baseline_manifest"]).read_text())
    workbook = ROOT / baseline["source_workbook"]
    fingerprint = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    if fingerprint(workbook) != baseline["source_sha256"]:
        raise ValueError("Dataset changed since the archived baseline; comparison is blocked")
    for path, digest in baseline["engine_sha256"].items():
        if fingerprint(ROOT / path) != digest:
            raise ValueError(f"Engine/tool implementation changed since baseline: {path}")
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", args.model)
    directory = ROOT / "output" / "qwen_demos" / slug / str(time.time_ns())
    workspace = Workspace(directory / "workspace", restore=False)
    loaded = workspace.load(ROOT / "data" / "Sales Compensation Analyst Case Study.xlsx", "benchmark")
    by_label = {item["label"]: workspace.frames[item["table_id"]] for item in loaded["tables"]}
    cases = build_cases(by_label)
    question_hash = hashlib.sha256(json.dumps([q for q, _ in cases], ensure_ascii=False).encode()).hexdigest()
    if question_hash != baseline["questions_sha256"]:
        raise ValueError("Demo questions changed since baseline; comparison is blocked")
    transcripts = []
    for question, expected in cases:
        result = AnalystAgent(workspace, model=args.model, url=args.ollama_url.rstrip("/") + "/api/chat").ask(question)
        evaluation = evaluate_result(result, expected)
        if not evaluation["sql_executed"]:
            raise AssertionError("Natural-language demo did not actually execute a successful SQL query")
        if not evaluation["source_values_in_answer"]:
            raise AssertionError(f"Qwen answer omitted/misstated known SQL counts: {result['answer']}")
        transcripts.append({"question": question, "validation": "SQL executed and source counts present in answer", **result})
        print(result["answer"], flush=True)
    path = directory / "transcript.json"
    path.write_text(dump(transcripts))
    (directory / "manifest.json").write_text(dump({"model": args.model, "availability": availability,
        "questions_sha256": question_hash, "source_sha256": baseline["source_sha256"],
        "engine_sha256": baseline["engine_sha256"], "baseline_manifest": config["baseline_manifest"],
        "evaluation": baseline["evaluation"], "generation_options": {"temperature": 0, "num_ctx": 16384},
        "max_steps": 8, "transcript_sha256": fingerprint(path)}))
    print("Saved real tool traces:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
