"""Conservative completion checks, not a general natural-language proof system.

Coverage is explicit: metadata inventory, source-backed calculations, numeric support,
known evidence handles, requested quality/scenario/reconciliation work and full previews.
SQL/business meaning still needs an independent oracle or reviewed plan.
"""
from decimal import Decimal, InvalidOperation
import re
import sqlglot
from sqlglot import exp


def sql_prohibited(question):
    return bool(re.search(r"\b(?:do not|don't)\s+(?:run|execute|use)\s+sql\b|\bwithout\s+sql\b|(?:不要|禁止|不运行|不使用).{0,6}SQL", question, re.I))


def sql_requested(question):
    return not sql_prohibited(question) and bool(re.search(r"\b(?:execute|run)\s+(?:(?:read.only|grouped|a|one|the)\s+)*sql\b|执行\s*SQL|运行\s*SQL", question, re.I))


def rank_limit(question):
    match = re.search(r"\btop\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b|前\s*(\d+)", question, re.I)
    if not match:
        return None
    token = (match[1] or match[2]).lower()
    words = "one two three four five six seven eight nine ten".split()
    return int(token) if token.isdigit() else words.index(token)+1


def inventory_only(question):
    text = question.lower()
    inventory = bool(re.search(r"(?:registered|available|all)\s+tables|所有.{0,8}表|全部.{0,8}表", text))
    counts = bool(re.search(r"row\s*counts?|number of rows|行数", text))
    extra = bool(re.search(r"\b(?:sum|average|min|max|compare|largest|smallest|revenue|join|scenario|reconcile|export|save|top|monthly|columns?|schemas?|types?|samples?|fields?|missing|duplicates?|calculate total|compute total)\b|然后|并计算|导出|收入|情景|关联|对账|列名|字段|类型|示例|缺失|重复", text))
    if re.search(r"do not list|don't list|不要列出|别列出", text):
        return False
    return inventory and counts and not extra and not sql_requested(question)


def numbers(text):
    """Extract prose numbers without mistaking identifiers, dates or list ordinals for claims."""
    text = re.sub(r"```.*?```", " ", str(text), flags=re.S)
    text = re.sub(r"\b\d{4}-\d{2}(?:-\d{2}(?:T[\d:.-]+)?)?\b", " ", text)
    text = re.sub(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b", " ", text, flags=re.I)
    # Display ordinals in an explicitly labelled Rank table are formatting, not amounts.
    lines, ordinal_table, ordinal = text.splitlines(), False, 1
    for index, line in enumerate(lines):
        if re.match(r"\s*\|\s*(?:Rank|#|No\.?|Index|序号|排名)\s*\|", line, re.I):
            ordinal_table, ordinal = True, 1
        elif ordinal_table and re.match(r"\s*\|\s*\d+\s*\|", line):
            match = re.match(r"(\s*\|\s*)(\d+)(\s*\|.*)", line)
            if int(match[2]) == ordinal:
                lines[index] = match[1] + " " + match[3]
                ordinal += 1
        elif ordinal_table and line.strip() and not line.lstrip().startswith("|"):
            ordinal_table = False
    text = "\n".join(lines)
    text = re.sub(r"(?m)^\s*\d+[.)]\s+", " ", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s*\d+[.)]\s+", " ", text)
    text = re.sub(r"\b[\w]*[a-zA-Z_][\w]*\b", " ", text)
    found = set()
    for value in re.findall(r"(?<![\w.])-?(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?!\w)", text):
        try:
            number = Decimal(value.replace(",", ""))
            if number.is_finite():
                found.add(number)
        except InvalidOperation:
            continue
    return found


def numeric_values(value):
    if isinstance(value, dict):
        return set().union(*(numeric_values(v) for k, v in value.items() if k not in {
            "sql", "query_id", "evidence_id", "sha256", "result_digest", "dataset_versions",
            "source_sha256", "version_id", "content_sha256", "interpretation_note", "text_preview"}), set())
    if isinstance(value, (list, tuple)):
        return set().union(*(numeric_values(v) for v in value), set())
    if value is None or isinstance(value, bool):
        return set()
    try:
        number = Decimal(str(value))
        return {number} if number.is_finite() else set()
    except InvalidOperation:
        return set()


def check_completion(question, answer, trace, workspace, known_ids=()):
    reasons, supported, schema_supported, source_queries = [], set(), set(), []
    known_sql_expressions = set()
    pure_math = bool(re.fullmatch(r"\s*(?:(?:calculate|compute|what is|计算)\s*)?[-\d+*/().%\s]+[?。]?\s*", question, re.I))
    claim_text = answer
    for metadata in workspace.catalog.values():
        if metadata.get("label"):
            claim_text = claim_text.replace(metadata["label"], " ")
    claim_text = re.sub(r"`[^`]*(?:SELECT\b|COUNT\s*\(|SUM\s*\(|CAST\s*\()[^`]*`", " ", claim_text, flags=re.I)
    successful = [t for t in trace if str(t["result"].get("status", "")).lower() not in {"error", "fail", "failed", "blocked", "require_review"} and not t.get("delivery_error")]
    latest_deliveries = {t["tool"]: t.get("delivery_error") for t in trace}
    if any(latest_deliveries.values()):
        reasons.append("A tool result could not be delivered within the model context limit")
    latest = {t["tool"]: t["result"] for t in trace}
    blocked = any(str(r.get("status", "")).lower() in {"blocked", "require_review"} or str(r.get("validation", {}).get("status", "")).lower() in {"blocked", "require_review"} for r in latest.values())
    if blocked:
        reasons.append("A requested tool calculation is blocked by missing inputs or unapproved rules")
    if any(str(r.get("status", "")).lower() in {"error", "fail", "failed"} for name, r in latest.items() if name != "export_result"):
        reasons.append("A tool error has not been resolved by a successful retry")
    ids = set(known_ids)
    for item in successful:
        result = item["result"]
        for key in ("evidence_id", "baseline_evidence", "scenario_evidence"):
            if result.get(key):
                ids.add(result[key])
        if item["tool"] in {"query_sql", "inspect_query_evidence"} and result.get("query_id"):
            try:
                evidence = workspace.query_evidence(result["query_id"])
                ids.add(result["query_id"])
                ids.add(evidence["result_digest"]["sha256"][:16])
                syntax = sqlglot.parse_one(evidence["sql"], read="duckdb")
                known_sql_expressions.update(n.sql(dialect="duckdb") for n in syntax.walk() if isinstance(n, (exp.Func, exp.Binary)))
                if evidence["dependencies"] or pure_math:
                    source_queries.append({"query_id": result["query_id"], "current_turn": True, "sql": evidence["sql"], "result_digest": evidence["result_digest"], **evidence["result"]})
                    supported |= numeric_values(evidence["result"]["rows"])
                    supported |= {Decimal(m["row_count"]) for m in evidence["dependencies"].values()}
                    # A query's result-row count is support only for its explicitly labelled
                    # technical footer, not for claims about the underlying source table.
                    pattern = r"(" + re.escape(result["query_id"]) + r"[^\n]{0,80}?[—=:]\s*)(\d+)(\s+rows?)"
                    claim_text = re.sub(pattern, lambda m: m[1] + "returned rows" if int(m[2]) == evidence["result_digest"]["row_count"] else m[0], claim_text)
            except ValueError:
                if result["query_id"] in answer:
                    reasons.append("Query evidence is stale")
        elif item["tool"] == "inspect_schema":
            schema_supported |= numeric_values(result)
        elif item["tool"] != "export_result":
            supported |= numeric_values(result)
        supported |= numeric_values(item["arguments"].get("parameters", {}))
        supported |= numeric_values(item["arguments"].get("overrides", {}))
    for handle in re.findall(r"\b[0-9a-f]{16}\b", answer):
        if handle not in ids:
            try:
                workspace.query_evidence(handle)
                ids.add(handle)
            except ValueError:
                try:
                    schema = workspace.schema_evidence(handle)
                    ids.add(handle)
                    supported |= numeric_values(schema["result"])
                except ValueError:
                    reasons.append("Unknown or stale evidence citation: " + handle)
    for handle in re.findall(r"\bquery[_\s-]?id[`\"']?\s*(?:[:=]|\bis\b)\s*[`\"']?([a-zA-Z0-9_-]+)", answer, re.I):
        try:
            workspace.query_evidence(handle)
        except ValueError:
            reasons.append("Unknown or stale query_id citation: " + handle)
    # Explicit reuse of a current prior query is permitted, but not guessed handles.
    for handle in known_ids:
        if handle in answer and not any(q.get("query_id") == handle for q in source_queries):
            try:
                old = workspace.query_evidence(handle)
                ids.add(old["result_digest"]["sha256"][:16])
                syntax = sqlglot.parse_one(old["sql"], read="duckdb")
                known_sql_expressions.update(n.sql(dialect="duckdb") for n in syntax.walk() if isinstance(n, (exp.Func, exp.Binary)))
                if old["dependencies"]:
                    source_queries.append({"query_id": handle, "current_turn": False, "sql": old["sql"], "result_digest": old["result_digest"], **old["result"]})
                    supported |= numeric_values(old["result"]["rows"])
            except ValueError:
                reasons.append("Cited prior evidence is stale")
    supported |= schema_supported
    text = question.lower()
    calculation = bool(re.search(r"\b(?:calculate|compute|count|sum|average|total|how many|revenue|commission|payroll)\b|计算|总额|收入|多少|佣金", text))
    metadata = any(t["tool"] == "inspect_schema" for t in successful)
    limitation = bool(re.search(r"cannot|unable|unsupported|not provided|missing.{0,30}(?:rule|input|data|coverage)|无法|不支持|缺少.{0,15}(?:规则|数据|输入)", answer, re.I))
    sql_missing = sql_requested(question) and not any(t["tool"] == "query_sql" and (t["result"].get("dataset_versions") or pure_math) for t in successful)
    if sql_missing:
        reasons.append("Explicit SQL execution was requested but no successful source query was performed")
    if calculation and not source_queries and not metadata and not any(t["tool"] in {"run_model", "run_scenario", "reconcile_queries"} for t in successful) and not limitation:
        reasons.append("No source-backed calculation or metadata evidence was obtained")
    if not successful and not source_queries and not limitation:
        reasons.append("No requested tool work was completed")
    if re.search(r"(?:check|find|identify|report).{0,65}missing|检查.{0,20}缺失", text):
        missing = any(t["tool"] == "check_quality" and t["arguments"].get("kind") == "missing" for t in successful)
        missing |= any(t["tool"] == "query_sql" and re.search(r"\bIS\s+NULL\b|\bCOUNT\s*\([^*]", t["arguments"].get("sql", ""), re.I) for t in successful)
        if not missing:
            reasons.append("Requested missing-value check was not performed")
    if re.search(r"\bmonthly\b|\bby month\b|按月|月度", text):
        period = any(re.search(r"\bGROUP\s+BY\b", q.get("sql", ""), re.I) and
                     (re.search(r"month|strftime|date_trunc|substr", q["sql"], re.I) or
                      any(re.match(r"\d{4}-\d{2}", str(v)) for row in q["rows"] for v in row)) for q in source_queries)
        if not period:
            reasons.append("Requested period aggregation was not performed")
    requested_rank = rank_limit(question)
    if requested_rank is not None:
        supported.add(Decimal(requested_rank))
        ranked = any(re.search(r"\bORDER\s+BY\b.*\bDESC\b|\b(?:DENSE_)?RANK\s*\(", q.get("sql", ""), re.I | re.S) and
                     not q.get("truncated") and q.get("result_digest", {}).get("row_count", 0) <= requested_rank for q in source_queries)
        if not ranked:
            reasons.append("Requested bounded ranking was not performed")
    for pattern, name in [(r"\bscenario\b|情景|情境", "run_scenario"), (r"\breconcil\w*\b|对账", "reconcile_queries")]:
        if re.search(pattern, text) and not any(t["tool"] == name for t in successful) and not limitation:
            reasons.append("Requested operation was not performed: " + name)
    if re.search(r"\b(?:show|list|return|report)\s+all\b|显示所有|列出全部", text):
        if source_queries and all(q.get("truncated") for q in source_queries):
            reasons.append("Only a truncated preview was obtained for a request for all rows")
    if re.search(r"\b(?:i will|we will)\s+(?:run|retry|calculate|export|check)|would you like me to|shall i proceed|将会重试|是否继续", answer, re.I):
        reasons.append("Final text promises or asks to perform unfinished work")
    supported |= numbers(question)
    def known_expression(match):
        try:
            expression = sqlglot.parse_one(match[1], read="duckdb")
            if expression is not None and expression.sql(dialect="duckdb") in known_sql_expressions:
                return " "
        except (sqlglot.errors.ParseError, ValueError, TypeError):
            pass
        return match[0]
    claim_text = re.sub(r"`([^`]+)`", known_expression, claim_text)
    claim_text = re.sub(r"\b[0-9a-f]{16}\b", " ", claim_text)
    claims = numbers(claim_text)
    # Require the requested scalar count itself, not an unrelated null/sample/result count.
    if re.search(r"\bcounts?\b|\bhow many\b|row counts?|行数|记录数", text) and not re.search(r"missing|null|distinct|unique|duplicat|group|\bper\b|\bby\b|缺失|重复|唯一|各", text):
        expected_counts = set()
        for query in source_queries:
            tree = sqlglot.parse_one(query["sql"], read="duckdb")
            if isinstance(tree, exp.Select) and not tree.args.get("group") and len(query["rows"]) == 1:
                for index, expression in enumerate(tree.expressions):
                    expression = expression.this if isinstance(expression, exp.Alias) else expression
                    if isinstance(expression, exp.Count) and index < len(query["rows"][0]):
                        expected_counts |= numeric_values(query["rows"][0][index])
        if not source_queries:
            expected_counts |= {Decimal(t["result"]["row_count"]) for t in successful if t["tool"] == "inspect_schema" and "row_count" in t["result"]}
        if expected_counts and not expected_counts.intersection(claims):
            reasons.append("The requested scalar row count is absent or replaced by a different statistic")
    # Free-text numeric extraction is advisory: identifiers, format names and
    # legitimate derived summaries are not reliably classifiable as metric claims.
    # A directly requested, single scalar result has a deterministic hard check.
    scalar_check = "not_applicable"
    current_queries = [q for q in source_queries if q.get("current_turn")]
    if calculation and len(current_queries) == 1 and len(current_queries[0]["rows"]) == 1 and len(current_queries[0]["columns"]) == 1 and not re.search(r"\b(?:is|does|whether)\b.{0,70}\b(?:positive|negative|greater|less|equal|exceed)|是否", text):
        value = current_queries[0]["rows"][0][0]
        expected = numeric_values(value)
        if value is None:
            reasons.append("Requested scalar result is NULL; no verified numerical result is available")
            scalar_check = "missing_result"
        elif expected:
            if not any(abs(v-s) <= Decimal("0.005") for v in claims for s in expected):
                reasons.append("Requested scalar result was not reported accurately")
                scalar_check = "failed"
            else:
                scalar_check = "passed"
    unsupported = [v for v in claims if not any(abs(v-s) <= Decimal("0.005") for s in supported)]
    commentary = {"status": "advisory_not_semantic_proof", "unmatched_numeric_tokens": list(map(str, sorted(unsupported)))}
    unmet_action = bool(re.search(r"cannot\s+(?:be fulfilled|calculate|compute|provide|complete|export|verify)|unable\s+to|no.{0,45}(?:payroll|commissions?).{0,50}can be computed|无法(?:计算|提供|完成|导出|验证)", answer, re.I))
    if unmet_action:
        reasons.append("The requested calculation or action remains unsupported or unavailable")
    if reasons:
        return {"status": "blocked" if blocked else "incomplete", "reasons": list(dict.fromkeys(reasons)),
                "repairable": ["execute_requested_sql"] if sql_missing and len(reasons) == 1 else [],
                "primary_scalar_check": scalar_check, "commentary_check": commentary,
                "scope": "limitation_report" if unmet_action and len(reasons) == 1 else "bounded_structural_and_primary_results"}
    if limitation and not source_queries and not any(t["tool"] in {"run_model", "run_scenario", "reconcile_queries", "check_quality", "check_join"} for t in successful):
        return {"status": "blocked", "reasons": ["Missing or unsupported inputs; no calculation claimed"], "scope": "limitation_report"}
    return {"status": "answered", "reasons": [], "primary_scalar_check": scalar_check,
            "commentary_check": commentary, "scope": "bounded_structural_and_primary_results"}
