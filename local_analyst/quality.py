"""Schema-neutral quality checks; numerical evidence is not business meaning."""

from collections import Counter
from datetime import date, datetime
from numbers import Number
from decimal import Decimal

import numpy as np
import pandas as pd


def atom(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, str):
        return ("text", value)
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return ("date", pd.Timestamp(value).isoformat())
    if isinstance(value, Number):
        return ("number", value)
    return (type(value).__name__, str(value))


def columns(value):
    result = [value] if isinstance(value, str) else list(value)
    if not result or len(set(result)) != len(result):
        raise ValueError("Supply distinct existing column names")
    return result


def key_counts(frame, keys):
    keys = columns(keys)
    if not set(keys).issubset(frame.columns):
        raise ValueError(f"Unknown columns: {set(keys) - set(frame.columns)}")
    counts, nulls = Counter(), 0
    for row in frame[keys].itertuples(index=False, name=None):
        if any(pd.isna(v) or (isinstance(v, str) and v == "") for v in row):
            nulls += 1
        else:
            counts[tuple(atom(v) for v in row)] += 1
    return counts, nulls


def key_summary(counts, nulls):
    size = sum(counts.values())
    return {"null_rows": nulls, "non_null_rows": size, "distinct_non_null_keys": len(counts),
            "duplicate_key_count": sum(n > 1 for n in counts.values()),
            "duplicate_rows": sum(n for n in counts.values() if n > 1),
            "duplicate_excess_rows": size - len(counts),
            "is_primary_key_candidate": bool(counts) and nulls == 0 and size == len(counts)}


def profile_key(frame, keys):
    return {"columns": columns(keys), **key_summary(*key_counts(frame, keys))}


def relationship_counts(lc, ln, rc, rn):
    left, right = key_summary(lc, ln), key_summary(rc, rn)
    common = lc.keys() & rc.keys()
    lo, ro = lc.keys() - rc.keys(), rc.keys() - lc.keys()
    lu, ru = sum(lc[k] for k in lo), sum(rc[k] for k in ro)
    inner = sum(lc[k] * rc[k] for k in common)
    lf = bool(common) and right["is_primary_key_candidate"] and lu == 0
    rf = bool(common) and left["is_primary_key_candidate"] and ru == 0
    cardinality = ("many" if left["duplicate_excess_rows"] else "one") + "_to_" + (
        "many" if right["duplicate_excess_rows"] else "one")
    return {"left": left, "right": right, "matched_distinct_keys": len(common),
            "left_matched_rows": sum(lc[k] for k in common), "right_matched_rows": sum(rc[k] for k in common),
            "left_unmatched_rows": lu, "right_unmatched_rows": ru,
            "left_unmatched_distinct_keys": len(lo), "right_unmatched_distinct_keys": len(ro),
            "cardinality": cardinality if common else "no_matching_keys",
            "inner_join_rows": inner, "left_join_rows": inner + lu + ln,
            "right_join_rows": inner + ru + rn, "left_fk_to_right": lf, "right_fk_to_left": rf,
            "status": "verified_data_relationship" if lf or rf else
            "needs_investigation" if common else "rejected_no_overlap"}


def test_relationship(left, right, left_on, right_on):
    left_on, right_on = columns(left_on), columns(right_on)
    if len(left_on) != len(right_on):
        raise ValueError("Key lengths differ")
    return {"left_on": left_on, "right_on": right_on,
            **relationship_counts(*key_counts(left, left_on), *key_counts(right, right_on))}


def profile(frame):
    result = []
    for col in frame:
        if col.startswith("__"):
            continue
        stats = profile_key(frame, col)
        values = frame[col].dropna()
        dtype = "decimal" if len(values) and all(isinstance(v, Decimal) for v in values) else str(frame[col].dtype)
        result.append({"name": col, "dtype": dtype, **stats})
    return {"rows": len(frame), "columns": result,
            "unique_key_candidates": [c["name"] for c in result if c["is_primary_key_candidate"]],
            "interpretation": "Snapshot uniqueness only; no semantic primary key is declared"}


def exception_rows(frame, keys, kind, attributes=None):
    keys = columns(keys)
    if kind == "duplicates":
        return frame[frame.duplicated(keys, keep=False)].copy()
    if kind == "missing":
        return frame[frame[keys].isna().any(axis=1) | frame[keys].eq("").any(axis=1)].copy()
    if kind == "conflicts":
        attributes = columns(attributes or [])
        counts = frame.groupby(keys, dropna=False)[attributes].transform(lambda s: s.nunique(dropna=False))
        return frame[counts.gt(1).any(axis=1)].copy()
    raise ValueError("Check kind must be duplicates, missing or conflicts")
