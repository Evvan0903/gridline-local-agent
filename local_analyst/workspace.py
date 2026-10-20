"""Local input registry and an isolated, read-only DuckDB query surface."""

from datetime import date, datetime
from decimal import Decimal
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid

import duckdb
import numpy as np
import pandas as pd
import sqlglot
from sqlglot import exp

from .quality import profile, test_relationship, key_counts, relationship_counts, exception_rows, atom


def json_value(value):
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if value is pd.NA or value is pd.NaT:
        return None
    raise TypeError(type(value).__name__)


def dump(value):
    return json.dumps(value, default=json_value, ensure_ascii=False, allow_nan=False)


def quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def parameter_names(sql):
    """Read real bound placeholders, ignoring dollars inside strings/comments."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    return {node.name for node in tree.find_all(exp.Placeholder) if node.this is not None}


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frame_fingerprint(frame):
    """Detect changes to registered in-memory values as well as source files."""
    schema = dump([(str(c), str(t)) for c, t in zip(frame.columns, frame.dtypes)])
    values = pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    return hashlib.sha256(schema.encode() + values).hexdigest()


class ResultDigest:
    """Bounded-memory multiset checksum; includes duplicates, not incidental row order."""
    def __init__(self, columns):
        self.columns, self.count, self.total = list(columns), 0, 0

    def add(self, row):
        self.total = (self.total + int(hashlib.sha256(dump(list(row)).encode()).hexdigest(), 16)) % (1 << 256)
        self.count += 1

    def value(self):
        return {"row_count": self.count, "sha256": hashlib.sha256(
            dump([self.columns, self.count, str(self.total)]).encode()).hexdigest(),
            "algorithm": "sha256-row-multiset-sum-v1"}


def _header(row):
    populated = [v for v in row if pd.notna(v)]
    return len(populated) >= 2 and all(isinstance(v, str) for v in populated) and len(set(populated)) == len(populated)


def sheet_regions(raw):
    """Conservative generic header/blank-separated region detection; notes are retained as metadata."""
    regions, excluded, cursor = [], [], 0
    while cursor < len(raw):
        row = raw.iloc[cursor]
        if not _header(row) and not (len(raw.columns) == 1 and cursor == 0):
            if row.notna().any():
                excluded.append(cursor + 1)
            cursor += 1
            continue
        start = cursor
        width = int(row.last_valid_index()) + 1
        headers = row.iloc[:width].tolist()
        if any(pd.isna(h) for h in headers):
            raise ValueError(f"Header row {start + 1} has gaps; provide explicit region configuration")
        if any(str(h).startswith("__") for h in headers):
            raise ValueError("Input column names beginning __ are reserved; no source column is overwritten")
        cursor += 1
        while cursor < len(raw) and raw.iloc[cursor, :width].notna().any():
            cursor += 1
        frame = raw.iloc[start + 1:cursor, :width].copy()
        # A note paragraph can follow data without a blank separator: do not infer its meaning.
        frame.columns = [str(h) for h in headers]
        frame["__source_row"] = range(start + 2, cursor + 1)
        regions.append((frame.reset_index(drop=True).infer_objects().convert_dtypes(), start + 1))
    return regions, excluded


SAFE_FUNCTIONS = set("SUM COUNT AVG MIN MAX ROUND ABS COALESCE NULLIF CAST TRY_CAST DATE_DIFF DATEDIFF DATE_TRUNC TIMESTAMP_TRUNC STRFTIME STRPTIME TRY_STRPTIME EXTRACT YEAR MONTH DAY DATE_ADD DATE_SUB LOWER UPPER TRIM LENGTH CONCAT SUBSTRING REPLACE ROW_NUMBER RANK DENSE_RANK LAG LEAD FIRST_VALUE LAST_VALUE BOOL_AND BOOL_OR STDDEV STDDEV_SAMP VARIANCE VAR_SAMP MEDIAN QUANTILE_CONT PERCENTILE_CONT GREATEST LEAST IF CASE IFNULL ISNULL POWER SQRT FLOOR CEIL CEILING MOD SIGN LOG LN EXP ARRAY_AGG STRING_AGG LIST SUM_IF COUNT_IF COUNTIF FILTER ANY_VALUE DATE TO_DATE EPOCH MAKE_DATE LAST_DAY".split())
SAFE_FUNCTIONS.update({"AND", "OR", "NOT"})
SAFE_FUNCTIONS.update({"TIME_TO_STR", "STR_TO_TIME"})  # sqlglot names for DuckDB STRFTIME/STRPTIME


class Workspace:
    def __init__(self, root=".analyst", restore=True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.frames, self.catalog, self.queries = {}, {}, {}
        self.on_record = None
        self.db = duckdb.connect(config={"enable_external_access": "false", "autoload_known_extensions": "false",
                                        "autoinstall_known_extensions": "false", "threads": "2", "memory_limit": "1GB"})
        self.audit_path = self.root / "audit.jsonl"
        self.inputs_path = self.root / "inputs.json"
        self.plans_path = self.root / "reviewed_plans.json"
        self.plans = json.loads(self.plans_path.read_text()) if self.plans_path.exists() else {}
        self.inputs = []
        if restore and self.inputs_path.exists():
            for item in json.loads(self.inputs_path.read_text()):
                self.load(item["path"], item["namespace"], item.get("regions"), persist=False)

    def record(self, kind, **evidence):
        from .providers import redact
        evidence = redact(evidence)
        event = {"event_id": hashlib.sha256(dump([kind, evidence, time.time_ns()]).encode()).hexdigest()[:16],
                 "kind": kind, "time_utc": datetime.now().astimezone().isoformat(), **evidence}
        with self.audit_path.open("a") as stream:
            stream.write(dump(event) + "\n")
        if self.on_record:
            self.on_record(event)
        return event["event_id"]

    def register(self, frame, table_id, metadata):
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table_id):
            raise ValueError("Table identifier must use letters, digits and underscores")
        if len(set(frame.columns)) != len(frame.columns):
            raise ValueError("Duplicate column names require explicit renaming")
        metadata = copy.deepcopy(metadata)
        content_hash = frame_fingerprint(frame)
        # Raw versions survive restore when both bytes and parsing are unchanged.
        # Every derived replacement gets a fresh generation, even with identical values.
        generation = None if metadata.get("kind") == "raw" else uuid.uuid4().hex
        version = "v_" + hashlib.sha256(dump([table_id, metadata, content_hash, generation]).encode()).hexdigest()
        self.frames[table_id] = frame.copy(deep=True)
        self.catalog[table_id] = {**metadata, "table_id": table_id, "dataset_id": table_id,
                                  "version_id": version, "content_sha256": content_hash, "row_count": len(frame)}
        self.db.register(table_id, self.frames[table_id])
        self.record("dataset_version", dataset=self.catalog[table_id])

    def dependencies(self, tables):
        """Validate and snapshot the complete parent-version closure."""
        captured, visiting = {}, set()

        def visit(table, expected=None):
            meta = self.catalog.get(table)
            if meta is None or (expected is not None and meta["version_id"] != expected):
                raise ValueError(f"Stale data dependency {table}; reload/recalculate dependencies and run a new query")
            if table in visiting:
                raise ValueError("Cyclic dataset lineage; fix the model and run a new query")
            if table in captured:
                return
            if frame_fingerprint(self.frames[table]) != meta["content_sha256"]:
                raise ValueError(f"Stale in-memory data {table}; register the updated dataset and run a new query")
            if meta.get("kind") == "raw":
                path = Path(meta["source_path"])
                if not path.exists() or fingerprint(path) != meta["source_sha256"]:
                    raise ValueError(f"Stale source {table}; reload the input, recalculate dependencies and run a new query")
            visiting.add(table)
            for parent, version in meta.get("parents", {}).items():
                visit(parent, version)
            visiting.remove(table)
            captured[table] = copy.deepcopy(meta)

        for table in tables:
            visit(table)
        return captured

    def query_evidence(self, query_id):
        """Only current, versioned handles can be reused. Legacy evidence fails closed."""
        if query_id not in self.queries and self.audit_path.exists():
            for line in self.audit_path.read_text().splitlines():
                event = json.loads(line)
                if event.get("event_id") == query_id and event.get("kind") == "query":
                    self.queries[query_id] = event
                    break
        if query_id not in self.queries:
            raise ValueError("Unknown query_id. Use an actual ID returned by query_sql; run a new query")
        evidence = self.queries[query_id]
        if "dependencies" not in evidence or "result_digest" not in evidence:
            raise ValueError("Stale unversioned query evidence; run a new query")
        current = self.dependencies(evidence["dependencies"])
        for table, old in evidence["dependencies"].items():
            if table not in current or old["version_id"] != current[table]["version_id"]:
                raise ValueError(f"Stale query {query_id}: dataset {table} changed; run a new query")
        return {"query_id": query_id, "status": "validated_current", **copy.deepcopy(evidence)}

    def query_result_frame(self, query_id):
        """Reexecute only validated versions, checking the COMPLETE result, never the preview."""
        evidence = self.query_evidence(query_id)
        frame = self.query_frame(evidence["sql"], evidence["parameters"])
        digest = ResultDigest(frame.columns)
        for row in frame.itertuples(index=False, name=None):
            digest.add(row)
        self.query_evidence(query_id)
        if digest.value() != evidence["result_digest"]:
            raise ValueError("Stale or non-reproducible query result; run a new query before export")
        return frame

    def schema_evidence(self, evidence_id):
        """Reuse persisted schema evidence only while every captured version is current."""
        if self.audit_path.exists():
            for line in self.audit_path.read_text().splitlines():
                event = json.loads(line)
                if event.get("event_id") != evidence_id:
                    continue
                if event.get("kind") == "schema_profile":
                    result = event["result"]
                    expected = {result["table_id"]: result["version_id"]}
                elif event.get("kind") == "schema_inventory":
                    result = {"tables": event["tables"], "table_count": event["table_count"]}
                    expected = {t["table_id"]: t["version_id"] for t in event["tables"]}
                    if set(expected) != set(self.catalog):
                        raise ValueError("Stale schema inventory; inspect the current registry again")
                else:
                    break
                current = self.dependencies(expected)
                if any(current[t]["version_id"] != version for t, version in expected.items()):
                    raise ValueError("Stale schema evidence; inspect the current schema again")
                return {"evidence_id": evidence_id, "status": "validated_current", "result": copy.deepcopy(result)}
        raise ValueError("Unknown schema evidence_id; use an actual inspect_schema result")

    def load(self, path, namespace=None, regions=None, persist=True):
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() not in {".csv", ".xlsx"}:
            raise ValueError("Only local CSV and XLSX files are supported")
        namespace = namespace or path.stem
        if any(i["namespace"] == namespace and i["path"] != str(path) for i in self.inputs):
            raise ValueError("Namespace already belongs to another file; specify a different namespace")
        digest = fingerprint(path)
        tables = []
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                headers = next(csv.reader(stream), [])
            if len(set(headers)) != len(headers):
                raise ValueError("Duplicate column names require explicit renaming; CSV headers were not silently changed")
            frame = pd.read_csv(path, keep_default_na=False, na_values=[""])
            # Re-read fractional columns as lexical decimals before float inference can
            # round away cents. This is representation preservation, not a money rule.
            fractional = [c for c in frame if pd.api.types.is_float_dtype(frame[c])]
            if fractional:
                lexical = pd.read_csv(path, usecols=fractional, dtype="string", keep_default_na=False, na_values=[""])
                for column in fractional:
                    values = [None if pd.isna(v) else Decimal(str(v)) for v in lexical[column]]
                    finite = [v for v in values if v is not None]
                    if finite and all(v.is_finite() for v in finite):
                        scale = max(max(-v.as_tuple().exponent, 0) for v in finite)
                        integer_digits = max(max(v.adjusted()+1, 0) if v else 0 for v in finite)
                        if scale <= 38 and integer_digits + scale <= 38:
                            frame[column] = pd.Series(values, dtype=object)
            frame = frame.convert_dtypes()
            if any(str(c).startswith("__") for c in frame):
                raise ValueError("Input column names beginning __ are reserved for lineage")
            frame["__source_row"] = range(2, len(frame) + 2)
            candidates = [("csv", frame, 1, [])]
        else:
            candidates = []
            with pd.ExcelFile(path) as book:
                for sheet in book.sheet_names:
                    raw = pd.read_excel(book, sheet_name=sheet, header=None, keep_default_na=False, na_values=[""])
                    if regions and sheet in regions:
                        parsed = []
                        for region in regions[sheet]:
                            header = region["header_row"] - 1
                            end = region["last_row"]
                            width = region.get("columns", int(raw.iloc[header].last_valid_index()) + 1)
                            frame = raw.iloc[header + 1:end, :width].copy()
                            frame.columns = [str(c) for c in raw.iloc[header, :width]]
                            frame["__source_row"] = range(header + 2, end + 1)
                            parsed.append((frame.reset_index(drop=True).infer_objects().convert_dtypes(), header + 1))
                        notes = []
                    else:
                        parsed, notes = sheet_regions(raw)
                    for index, (frame, header) in enumerate(parsed):
                        if any(c.startswith("__") and c != "__source_row" for c in frame):
                            raise ValueError("Input column names beginning __ are reserved")
                        label = sheet if len(parsed) == 1 else f"{sheet} / region {index + 1}"
                        candidates.append((label, frame, header, notes))
        if fingerprint(path) != digest:
            raise ValueError("Source changed while loading; retry against a stable file")
        stale = [key for key,meta in self.catalog.items() if meta.get("source_path") == str(path)
                 or meta.get("kind") == "derived"]
        for key in stale:
            self.db.unregister(key)
            self.frames.pop(key)
            self.catalog.pop(key)
        for label, frame, header, notes in candidates:
            logical = namespace + ":" + label
            slug = re.sub(r"[^a-z0-9]+", "_", logical.lower()).strip("_")[:45]
            table_id = "t_" + slug + "_" + hashlib.sha256(logical.encode()).hexdigest()[:8]
            if table_id in self.catalog and self.catalog[table_id].get("source_path") != str(path):
                raise ValueError("Table identifier collision; choose an explicit namespace")
            self.register(frame, table_id, {"source_path": str(path), "source_sha256": digest,
                          "namespace": namespace, "label": label, "header_row": header,
                          "unclassified_source_rows": notes, "kind": "raw"})
            tables.append(self.catalog[table_id])
        self.inputs = [i for i in self.inputs if i["path"] != str(path)]
        self.inputs.append({"path": str(path), "namespace": namespace, "regions": regions})
        if persist:
            self.inputs_path.write_text(dump(self.inputs))
        self.record("load", path=str(path), sha256=digest, tables=tables)
        return {"tables": copy.deepcopy(tables), "warning": "Header detection is structural; inspect unclassified rows. No business meanings or relationships are assumed."}

    def register_plan(self, path):
        """Explicit user/CLI action. No agent tool can write to this registry."""
        path = Path(path).resolve()
        json.loads(path.read_text())
        plan_id = "plan_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
        self.plans[plan_id] = {"path": str(path), "sha256": fingerprint(path)}
        self.plans_path.write_text(dump(self.plans))
        self.record("user_registered_plan", plan_id=plan_id, **self.plans[plan_id])
        return plan_id

    def reviewed_plan(self, plan_id):
        if plan_id not in self.plans:
            raise ValueError("No user-registered plan with that ID. Register an explicit JSON plan via CLI --plan first; the LLM cannot approve invented rules.")
        evidence = self.plans[plan_id]
        if fingerprint(evidence["path"]) != evidence["sha256"]:
            raise ValueError("Plan changed since user registration; register the updated file explicitly")
        return json.loads(Path(evidence["path"]).read_text())

    def schema(self, table_id=None):
        if table_id is None:
            self.dependencies(self.catalog)
            result = {"tables": copy.deepcopy(list(self.catalog.values())), "table_count": len(self.catalog)}
            result["evidence_id"] = self.record("schema_inventory", **result)
            return result
        if table_id not in self.catalog:
            raise ValueError("Unknown table identifier. Call inspect_schema with {} (no table_id) to list the exact registered identifiers.")
        self.dependencies([table_id])
        frame = self.frames[table_id]
        sample = [{str(k): (None if pd.isna(v) else v[:300] if isinstance(v, str) else v)
                   for k, v in row.items()} for row in frame.head(3).to_dict("records")]
        result = {**copy.deepcopy(self.catalog[table_id]), **profile(frame), "sample": sample}
        result["evidence_id"] = self.record("schema_profile", table_id=table_id, result=result)
        return result

    def resolve(self, name):
        if name in self.frames:
            return name
        matched = [key for key, meta in self.catalog.items() if f"{meta.get('namespace')}:{meta.get('label')}" == name]
        if len(matched) != 1:
            raise ValueError(f"Table binding is missing or ambiguous: {name}")
        return matched[0]

    def relationships(self):
        result = []
        # Header evidence only. Deliberately do not equate differently named UUID namespaces.
        ids = list(self.frames)
        for i, left in enumerate(ids):
            for right in ids[i + 1:]:
                for col in sorted(set(self.frames[left]) & set(self.frames[right])):
                    if col.startswith("__"):
                        continue
                    a, b = self.frames[left][col], self.frames[right][col]
                    if not (pd.api.types.is_string_dtype(a) or "id" in col.lower() or a.is_unique or b.is_unique):
                        continue
                    audit = test_relationship(self.frames[left], self.frames[right], col, col)
                    if audit["matched_distinct_keys"]:
                        result.append({"left_table": left, "right_table": right, **audit,
                                       "semantic_status": "candidate_only_requires_user_interpretation"})
        return result

    def join_check(self, left_table, right_table, left_on, right_on, persist=True):
        dependencies = self.dependencies([left_table, right_table])
        result = test_relationship(self.frames[left_table], self.frames[right_table], left_on, right_on)
        result.update({"left_table": left_table, "right_table": right_table,
                       "dataset_versions": {t: m["version_id"] for t, m in dependencies.items()},
                       "semantic_status": "explicit keys tested, business meaning not inferred"})
        result["evidence_id"] = self.record("join_check", result=result)
        if not persist:
            return result
        directory = self.root / "exceptions"
        directory.mkdir(exist_ok=True)
        for side, other, keys, other_keys in [("left", right_table, left_on, right_on), ("right", left_table, right_on, left_on)]:
            table = left_table if side == "left" else right_table
            frame = self.frames[table]
            other_counts, _ = key_counts(self.frames[other], other_keys)
            keys = [keys] if isinstance(keys, str) else keys
            bad = []
            for position, row in enumerate(frame[keys].itertuples(index=False, name=None)):
                missing = any(pd.isna(v) or (isinstance(v,str) and v == "") for v in row)
                if missing or tuple(atom(v) for v in row) not in other_counts:
                    bad.append(position)
            if bad:
                path = directory / f"{result['evidence_id']}_{side}_unmatched.csv"
                frame.iloc[bad].to_csv(path, index=False)
                result[f"{side}_exception_ledger"] = str(path)
        return result

    def validate_sql(self, sql):
        parsed = sqlglot.parse(sql, read="duckdb")
        if len(parsed) != 1 or not isinstance(parsed[0], exp.Query):
            raise ValueError("Exactly one SELECT/WITH query is permitted. For independent calculations, send separate query_sql calls, each containing one SELECT, or one combined aggregation. No statements were executed")
        tree = parsed[0]
        forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command, exp.Into)
        if any(isinstance(node, forbidden) for node in tree.walk()):
            raise ValueError("Only read-only relational queries are permitted")
        if tree.find(exp.With) and tree.find(exp.With).args.get("recursive"):
            raise ValueError("Recursive CTEs are not supported in agent SQL")
        ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
        sources = []
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier) or table.db or table.catalog:
                raise ValueError("Table functions, files and external catalogs are forbidden")
            if table.name not in self.frames and table.name not in ctes:
                raise ValueError(f"Unregistered table: {table.name}")
            if table.name in self.frames:
                sources.append(table.name)
        for func in tree.find_all(exp.Func):
            name = func.name.upper() if isinstance(func, exp.Anonymous) else func.sql_name().upper()
            if name not in SAFE_FUNCTIONS:
                raise ValueError(f"Unsupported SQL function: {name}")
        warnings = []
        for select in tree.find_all(exp.Select):
            aliases = {t.alias_or_name: t.name for t in select.find_all(exp.Table)
                       if t.name in self.frames}
            for join in select.args.get("joins", []):
                if join.args.get("method") or str(join.args.get("kind", "")).upper() == "CROSS":
                    raise ValueError("Implicit/NATURAL/CROSS joins are not permitted")
                on = join.args.get("on")
                if on is None and not join.args.get("using"):
                    raise ValueError("Every join must specify explicit ON or USING keys")
                pairs = []
                if on is not None:
                    for equality in on.find_all(exp.EQ):
                        a, b = equality.left, equality.right
                        if isinstance(a, exp.Column) and isinstance(b, exp.Column) and a.table != b.table and a.table in aliases and b.table in aliases:
                            pairs.append((aliases[a.table], a.name, aliases[b.table], b.name))
                if len(pairs) == 1:
                    l, lc, r, rc = pairs[0]
                    audit = self.join_check(l, r, lc, rc, persist=False)
                    warnings.append({"join": audit, "warning": "many-to-many row multiplication" if audit["cardinality"] == "many_to_many" else "snapshot join audit; filters may change grain"})
                else:
                    warnings.append({"warning": "Composite, USING, range or derived-table join: automatic cardinality not established; run explicit join_check for intended keys"})
        if not sources:
            warnings.append({"warning": "This query reads no registered datasets; it is not evidence of any source table's contents or row count"})
        self.dependencies(sources)
        return sorted(set(sources)), warnings

    def validate_decimal_calculation(self, sql):
        """For explicitly exact-decimal requests, reject raw FLOAT arithmetic.

        Inspect direct registered operands in each SELECT scope. This is not a
        universal type/lineage proof for arbitrary derived SQL expressions.
        """
        tree = sqlglot.parse_one(sql, read="duckdb")
        bad = set()
        def scope_of(node):
            node = node.parent
            while node is not None and not isinstance(node, exp.Select):
                node = node.parent
            return node
        for column in tree.find_all(exp.Column):
            scope = scope_of(column)
            if scope is None:
                continue
            aliases = {t.alias_or_name: t.name for t in scope.find_all(exp.Table)
                       if scope_of(t) is scope and t.name in self.frames}
            candidates = [aliases[column.table]] if column.table in aliases else ([] if column.table else list(aliases.values()))
            floating = [t for t in candidates if column.name in self.frames[t] and pd.api.types.is_float_dtype(self.frames[t][column.name])]
            if not floating:
                continue
            node = column.parent
            while node is not None and node is not scope:
                if isinstance(node, exp.Count):
                    break
                if type(node).__name__ in {"Cast", "TryCast"} and re.search(r"\bDECIMAL\b|\bNUMERIC\b", str(node.args.get("to")), re.I):
                    break
                if isinstance(node, exp.AggFunc) or type(node).__name__ in {"Add", "Sub", "Mul", "Div", "Mod", "Pow"}:
                    bad.update(t + "." + column.name for t in floating)
                    break
                node = node.parent
        if bad:
            raise ValueError("Exact decimal calculation requested: cast FLOAT operands to DECIMAL with appropriate precision/scale BEFORE arithmetic or aggregation. Uncast operands: " + ", ".join(sorted(bad)))

    def _execute(self, sql, parameters, full=False, limit=30, timeout=15):
        timer = threading.Timer(timeout, self.db.interrupt)
        timer.daemon = True
        timer.start()
        try:
            cursor = self.db.execute(sql, parameters or {})
            names = [col[0] for col in cursor.description]
            if full:
                # Keep Decimal/NULL/date values; fetchdf can coerce Decimal to float.
                return pd.DataFrame(cursor.fetchall(), columns=names, dtype=object)
            digest, preview = ResultDigest(names), []
            while rows := cursor.fetchmany(4096):
                for row in rows:
                    digest.add(row)
                    if len(preview) < limit:
                        preview.append(row)
            return names, preview, digest.count > limit, digest.value()
        finally:
            timer.cancel()

    def query(self, sql, parameters=None, max_rows=30):
        try:
            if type(max_rows) is not int or not 1 <= max_rows <= 100:
                raise ValueError("max_rows must be an integer from 1 to 100; omitted defaults to 30")
            limit = max_rows
            tables, warnings = self.validate_sql(sql)
            dependencies = self.dependencies(tables)
            names, rows, truncated, checksum = self._execute(sql, parameters, limit=limit)
            if dependencies != self.dependencies(tables):
                raise ValueError("Stale dependencies changed during execution; run a new query")
            result = {"columns": names, "rows": [[(str(v)[:300] if isinstance(v, str) else v) for v in row] for row in rows],
                      "truncated": truncated, "preview_limit": limit, "warnings": warnings}
            tree = sqlglot.parse_one(sql, read="duckdb")
            result["calculation_expressions"] = [item.sql(dialect="duckdb") for select in tree.find_all(exp.Select)
                                                   for item in select.expressions][:40]
            result["interpretation_note"] = "Column aliases are user/model supplied, not verified business definitions. COUNT(*) counts all qualifying rows; COUNT(column) counts non-null values."
            evidence = {"sql": sql, "parameters": copy.deepcopy(parameters or {}),
                        "sources": {t: copy.deepcopy(self.catalog[t]) for t in tables},
                        "dependencies": dependencies, "result_digest": checksum, "result": result}
            query_id = self.record("query", **evidence)
            self.queries[query_id] = evidence
            return {"query_id": query_id, "status": "validated_current",
                    "dataset_versions": {t: m["version_id"] for t, m in dependencies.items()},
                    "result_digest": checksum, **result}
        except Exception as error:
            self.record("query_error", sql=sql, parameters=parameters or {}, error=str(error))
            raise

    def query_frame(self, sql, parameters=None):
        self.validate_sql(sql)
        return self._execute(sql, parameters, full=True)

    def quality_check(self, table_id, kind, columns, amount_column=None, attributes=None, persist=True):
        self.dependencies([table_id])
        frame = self.frames[table_id]
        bad = exception_rows(frame, columns, kind, attributes)
        impact = None
        if amount_column:
            values = pd.to_numeric(bad[amount_column], errors="raise")
            impact = str(sum(Decimal(str(v)) for v in values.dropna()))
        event = self.record("quality_check", table=table_id, check=kind, columns=columns,
                            affected_rows=len(bad), amount_column=amount_column, exposed_amount=impact,
                            classification="confirmed_structural_observation_not_necessarily_business_error")
        result = {"evidence_id": event, "affected_rows": len(bad), "exposed_amount": impact,
                  "classification": "confirmed_structural_observation",
                  "dataset_versions": {table_id: self.catalog[table_id]["version_id"]}}
        if persist:
            path = self.root / "exceptions"
            path.mkdir(exist_ok=True)
            bad.to_csv(path / f"{event}.csv", index=False)
            result["ledger_csv"] = str(path / f"{event}.csv")
        return result

    def reconcile(self, left_sql, right_sql, parameters=None, tolerance="0"):
        from .finance import decimal
        tolerance = decimal(tolerance)
        if tolerance < 0:
            raise ValueError("tolerance cannot be negative")
        parameters = parameters or {}
        left_names, right_names = parameter_names(left_sql), parameter_names(right_sql)
        unused = set(parameters) - left_names - right_names
        if unused:
            raise ValueError(f"Unused reconciliation parameters: {sorted(unused)}")
        left = self.query(left_sql, {k: v for k, v in parameters.items() if k in left_names}, 2)
        right = self.query(right_sql, {k: v for k, v in parameters.items() if k in right_names}, 2)
        if any(len(q["rows"]) != 1 or len(q["columns"]) != 1 or q["rows"][0][0] is None for q in (left, right)):
            raise ValueError("Reconciliation requires two non-null scalar queries")
        delta = decimal(left["rows"][0][0]) - decimal(right["rows"][0][0])
        return {"left_query_id": left["query_id"], "right_query_id": right["query_id"],
                "difference": str(delta), "passed": abs(delta) <= tolerance}
