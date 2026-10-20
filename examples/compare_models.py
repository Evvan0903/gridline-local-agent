"""Fixed-question local Ollama comparison; instrumentation lives outside the engine.

Run: python -m examples.compare_models --output output/model_comparison/NEW_RUN
Both exact tags must be installed. No downloads, prompt repairs, or engine edits.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from unittest.mock import patch

import requests
import sqlglot
from sqlglot import exp

from local_analyst import Workspace
from local_analyst.agent import AnalystAgent
from local_analyst.config import resolve_model, settings
from local_analyst.workspace import dump
from .demo_qwen import ROOT, CONFIG_PATH, build_cases, check_model, evaluate_result


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(dump(value), encoding="utf-8")
    temporary.replace(path)


def append(path, value):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, default=str, ensure_ascii=False) + "\n")


def system_value(*command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else "unavailable: " + result.stderr.strip()


class MemorySampler:
    """Sample server/worker RSS and Ollama allocation metadata; never add them together."""
    def __init__(self, base, model, path):
        self.base, self.model, self.path = base, model, path
        self.stop = threading.Event()
        self.samples = []

    def sample(self):
        item = {"time_ns": time.time_ns()}
        try:
            response = requests.get(self.base + "/api/ps", timeout=2)
            response.raise_for_status()
            item["models"] = response.json().get("models", [])
        except Exception as error:
            item["api_ps_error"] = str(error)
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss=,comm="], text=True, timeout=3)
            rows = []
            for line in output.splitlines():
                pieces = line.strip().split(None, 3)
                if len(pieces) == 4:
                    rows.append({"pid": int(pieces[0]), "ppid": int(pieces[1]),
                                 "rss_kib": int(pieces[2]), "comm": pieces[3]})
            server = {row["pid"] for row in rows if "ollama" in row["comm"].lower()}
            while True:
                expanded = server | {row["pid"] for row in rows if row["ppid"] in server}
                if expanded == server:
                    break
                server = expanded
            item["ollama_processes"] = [row for row in rows if row["pid"] in server]
            item["ollama_rss_kib"] = sum(row["rss_kib"] for row in item["ollama_processes"])
            item["client_rss_kib"] = next((r["rss_kib"] for r in rows if r["pid"] == os.getpid()), None)
        except Exception as error:
            item["rss_error"] = str(error)
        self.samples.append(item)
        append(self.path, item)

    def __enter__(self):
        def loop():
            while not self.stop.is_set():
                self.sample()
                self.stop.wait(0.5)
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=8)

    def summary(self):
        matches = [m for item in self.samples for m in item.get("models", [])
                   if self.model in {m.get("name"), m.get("model")}]
        return {"samples": len(self.samples), "interval_target_seconds": 0.5,
                "peak_ollama_tree_rss_kib": max((s.get("ollama_rss_kib", 0) for s in self.samples), default=0),
                "peak_client_rss_kib": max((s.get("client_rss_kib") or 0 for s in self.samples), default=0),
                "peak_model_size_bytes": max((m.get("size", 0) for m in matches), default=0),
                "peak_model_size_vram_bytes": max((m.get("size_vram", 0) for m in matches), default=0),
                "context_lengths": sorted({m["context_length"] for m in matches if "context_length" in m}),
                "sampling_errors": sum("api_ps_error" in s or "rss_error" in s for s in self.samples)}


def diagnostics(result, expected_rows, catalog):
    trace = result.get("trace", [])
    queries = [t for t in trace if t["tool"] == "query_sql"]
    successful = [t for t in queries if "query_id" in t["result"]]
    canonical = lambda rows: Counter(tuple(sorted(map(str, row))) for row in rows)
    matching = [t for t in successful if canonical(t["result"]["rows"]) == canonical(expected_rows)
                and not t["result"].get("truncated")]
    identifiers = []
    parse_errors = []
    for query in queries:
        try:
            tree = sqlglot.parse_one(query["arguments"]["sql"], read="duckdb")
            ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
            identifiers.extend(t.name for t in tree.find_all(exp.Table) if t.name not in ctes)
        except Exception as error:
            parse_errors.append(str(error))
    errors = [{"tool": t["tool"], "arguments": t["arguments"], "error": t["result"]["error"]}
              for t in trace if "error" in t["result"]]
    return {"tool_calls": len(trace), "sql_attempts": len(queries), "successful_sql": len(successful),
            "source_result_match": bool(matching), "expected_rows": expected_rows,
            "sql_table_ids": sorted(set(identifiers)),
            "invalid_sql_table_ids": sorted(set(identifiers) - set(catalog)),
            "sql_parse_errors": parse_errors, "tool_errors": errors,
            "evidence_id_cited": any(t["result"]["query_id"] in result.get("answer", "") for t in successful)}


def run_question(workspace, model, base, question, expected, expected_rows, directory, inject=False):
    """Save HTTP before/after each request, even if the Agent raises or the test fails."""
    directory.mkdir(parents=True, exist_ok=True)
    agent = AnalystAgent(workspace, model=model, url=base + "/api/chat")
    injections = []
    if inject:
        original_query = agent.functions["query_sql"]
        def flaky_query(*args, **kwargs):
            if not injections:
                injections.append({"arguments": kwargs, "executed": False})
                raise ValueError("Temporary test failure: SQL not executed. Retry the same query.")
            return original_query(*args, **kwargs)
        agent.functions["query_sql"] = flaky_query
    real_post = requests.post
    metrics = []
    def observed_post(url, **kwargs):
        request_id = len(metrics) + 1
        append(directory / "http.jsonl", {"kind": "request", "id": request_id, "url": url,
                                        "payload": kwargs.get("json"), "timeout": kwargs.get("timeout")})
        start = time.perf_counter()
        try:
            response = real_post(url, **kwargs)
            try:
                body = response.json()
            except ValueError:
                body = {"raw_text": response.text}
            metric = {"id": request_id, "seconds": time.perf_counter() - start, "status": response.status_code,
                      **{k: body[k] for k in ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
                                              "eval_count", "eval_duration", "done_reason") if k in body}}
            metrics.append(metric)
            append(directory / "http.jsonl", {"kind": "response", **metric, "body": body})
            return response
        except Exception as error:
            metric = {"id": request_id, "seconds": time.perf_counter() - start, "exception": repr(error)}
            metrics.append(metric)
            append(directory / "http.jsonl", {"kind": "exception", **metric})
            raise
    start = time.perf_counter()
    exception = None
    try:
        with patch("local_analyst.agent.requests.post", side_effect=observed_post):
            result = agent.ask(question)
        exception = result.get("error")
    except Exception as error:
        exception = repr(error)
        # Tool messages and completed SQL evidence survive a transport failure.
        result = {"answer": "", "trace": [], "steps": None}
    outcome = {"model": model, "question": question, "expected_values": expected,
               "seconds": time.perf_counter() - start, "exception": exception, **result,
               "evaluation": evaluate_result(result, expected),
               "diagnostics": diagnostics(result, expected_rows, workspace.catalog),
               "http_metrics": metrics, "injected_errors": injections}
    if inject:
        outcome["recovered"] = bool(injections) and outcome["evaluation"]["passed"] and outcome["diagnostics"]["source_result_match"]
    save(directory / "history.json", agent.history)
    save(directory / "result.json", outcome)
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-model")
    parser.add_argument("--test-model")
    args = parser.parse_args(argv)
    directory = args.output.resolve()
    if (directory / "run_manifest.json").exists():
        raise ValueError("Choose a fresh output directory; previous comparison logs cannot be overwritten")
    directory.mkdir(parents=True, exist_ok=True)
    base = settings()["ollama_url"].rstrip("/")
    models = [resolve_model(args.baseline_model, "comparison_model"), resolve_model(args.test_model, "test_model")]
    if models[0] == models[1]:
        raise ValueError("Comparison needs two distinct exact tags. Set --baseline-model and --test-model, or unset OLLAMA_MODEL.")
    availability = [check_model(model, base) for model in models]
    save(directory / "availability.json", availability)
    if not all(item["available"] for item in availability):
        print(dump(availability), flush=True)
        return 2
    protocol = json.loads((ROOT / json.loads(CONFIG_PATH.read_text())["baseline_manifest"]).read_text())
    for name, digest in {**protocol["engine_sha256"], protocol["source_workbook"]: protocol["source_sha256"]}.items():
        if fingerprint(ROOT / name) != digest:
            raise ValueError("Comparison version mismatch: " + name)
    workbook = ROOT / protocol["source_workbook"]
    manifest = {"started_utc": datetime.now(timezone.utc).isoformat(), "models": models,
                "protocol": protocol, "availability": availability,
                "harness_sha256": fingerprint(__file__), "host_memory_bytes": system_value("sysctl", "-n", "hw.memsize"),
                "swap_before": system_value("sysctl", "vm.swapusage"), "unload_events": [],
                "generation_options": {"temperature": 0, "num_ctx": 16384, "think": False, "timeout_seconds": 180, "max_steps": 8}}
    save(directory / "run_manifest.json", manifest)
    # Only unload the two explicitly selected benchmark models, never unrelated models.
    def unload(model):
        response = requests.post(base + "/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
        event = {"model": model, "status": response.status_code, "body": response.json()}
        manifest["unload_events"].append(event)
        save(directory / "run_manifest.json", manifest)
        response.raise_for_status()
    loaded = requests.get(base + "/api/ps", timeout=10).json().get("models", [])
    save(directory / "loaded_before.json", loaded)
    for model in models:
        if any(model in {m.get("name"), m.get("model")} for m in loaded):
            unload(model)
    summaries = []
    for index, model in enumerate(models):
        model_dir = directory / ("baseline" if index == 0 else "candidate")
        workspace = Workspace(model_dir / "workspace", restore=False)
        start = time.perf_counter()
        loaded = workspace.load(workbook, "benchmark")
        loading_seconds = time.perf_counter() - start
        by_label = {item["label"]: workspace.frames[item["table_id"]] for item in loaded["tables"]}
        cases = build_cases(by_label)
        questions = [q for q, _ in cases]
        if questions != protocol["questions"]:
            raise ValueError("Questions changed; no inference started")
        expected_rows = [
            [[len(by_label["9. App Export"])]],
            [[str(k), int(v)] for k, v in by_label["8. Opportunity Export"].opportunity_stage.value_counts().items()],
            [[str(k), int(len(g)), int(g.credit_amount.sum())] for k, g in by_label["10. Billing Export"].groupby("ticket_status")],
        ]
        summary = {"model": model, "loading_seconds": loading_seconds, "table_ids": list(workspace.catalog), "questions": []}
        print(f"START {model}; {len(workspace.catalog)} tables; workbook load {loading_seconds:.2f}s", flush=True)
        with MemorySampler(base, model, model_dir / "memory.jsonl") as sampler:
            for number, ((question, expected), rows) in enumerate(zip(cases, expected_rows), 1):
                print(f"START {model} Q{number}", flush=True)
                outcome = run_question(workspace, model, base, question, expected, rows, model_dir / f"q{number}")
                summary["questions"].append(outcome)
                save(model_dir / "summary.json", summary)
                print(f"DONE {model} Q{number} passed={outcome['evaluation']['passed']} seconds={outcome['seconds']:.2f} exception={outcome['exception']}", flush=True)
            print(f"START {model} separate controlled-recovery probe", flush=True)
            summary["recovery_probe"] = run_question(workspace, model, base, *cases[0], expected_rows[0], model_dir / "recovery", inject=True)
        summary["memory"] = sampler.summary()
        summary["passed"] = sum(q["evaluation"]["passed"] for q in summary["questions"])
        save(model_dir / "summary.json", summary)
        summaries.append(summary)
        save(directory / "summary.json", summaries)
        print(f"DONE {model}: {summary['passed']}/3; recovery={summary['recovery_probe']['recovered']}; memory={dump(summary['memory'])}", flush=True)
        unload(model)
        workspace.db.close()
        del workspace
    manifest["swap_after"] = system_value("sysctl", "vm.swapusage")
    manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    save(directory / "run_manifest.json", manifest)
    return 0 if all(s["passed"] == 3 for s in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
