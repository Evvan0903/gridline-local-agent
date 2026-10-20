"""Explicit financial primitives. Inputs carry meaning; no dataset-specific rules."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd


def decimal(value):
    if value is None or value is pd.NA or pd.isna(value):
        raise ValueError("Missing financial input; no default or imputation is allowed")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite financial input")
    return result


def money(value):
    return decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def marginal_tiers(amount, rate, thresholds=(), multipliers=(1,)):
    amount, rate = decimal(amount), decimal(rate)
    thresholds = [decimal(v) for v in thresholds]
    multipliers = [decimal(v) for v in multipliers]
    if len(multipliers) != len(thresholds) + 1 or thresholds != sorted(set(thresholds)) or any(v < 0 for v in thresholds):
        raise ValueError("Tier thresholds must increase and have exactly one multiplier per band")
    # Negative amounts are not silently floored; apply base rate unless an explicit policy says otherwise.
    if amount < 0:
        return amount * rate
    total, lower = Decimal(0), Decimal(0)
    for upper, multiplier in zip(thresholds + [amount], multipliers):
        band = max(Decimal(0), min(amount, upper) - lower)
        total += band * rate * multiplier
        lower = upper
    return total


def window_index(event_date, anchor, width_days, pre_anchor_in_first=False):
    if width_days <= 0 or pd.isna(event_date) or pd.isna(anchor):
        raise ValueError("Window needs valid dates and a positive width")
    day = (pd.Timestamp(event_date).normalize() - pd.Timestamp(anchor).normalize()).days
    return 0 if pre_anchor_in_first and day < 0 else day // width_days


def window_bounds(anchor, index, width_days):
    start = pd.Timestamp(anchor).normalize() + timedelta(days=index * width_days)
    return start, start + timedelta(days=width_days - 1)


def employed_on(day, start, end=None):
    if pd.isna(start):
        raise ValueError("Missing employment start")
    day = pd.Timestamp(day).normalize()
    return pd.Timestamp(start).normalize() <= day and (pd.isna(end) or end is None or day <= pd.Timestamp(end).normalize())


def ramp_credit(month, quota, anchor, employed_start, employed_end, percentages, window_days=30):
    """Calendar-day floor with explicit start, length and percentages; no role assumptions."""
    month = pd.Timestamp(month).normalize().replace(day=1)
    end = month + pd.offsets.MonthEnd(0)
    if pd.isna(anchor):
        raise ValueError("Ramp start is missing")
    quota = decimal(quota)
    total, eligible_days, active_days = Decimal(0), 0, 0
    for day in pd.date_range(month, end):
        if not employed_on(day, employed_start, employed_end):
            continue
        eligible_days += 1
        index = window_index(day, anchor, window_days)
        if 0 <= index < len(percentages):
            active_days += 1
            total += quota / Decimal(end.day) * decimal(percentages[index])
    return {"floor": total, "ramp_days": active_days, "employed_days": eligible_days,
            "prorated_quota": quota * Decimal(eligible_days) / Decimal(end.day)}


def hierarchy_paths(frame, employee_column, manager_column, external_roots=()):
    if frame[employee_column].isna().any() or frame[employee_column].duplicated().any():
        raise ValueError("Hierarchy employee keys must be complete and unique")
    parents = frame.set_index(employee_column)[manager_column].to_dict()
    external_roots = set(external_roots)
    paths, warnings = {}, []
    for employee in parents:
        seen, path, cursor = {employee}, [], employee
        while pd.notna(parents.get(cursor)):
            manager = parents[cursor]
            if manager in seen:
                raise ValueError(f"Hierarchy cycle involving {employee}")
            if manager not in parents:
                if manager not in external_roots:
                    warnings.append({"employee": employee, "missing_manager": manager, "classification": "unresolved_assumption"})
                break
            path.append(manager)
            seen.add(manager)
            cursor = manager
        paths[employee] = path
    return paths, warnings


def convert_currency(amount, units_per_base):
    rate = decimal(units_per_base)
    if rate <= 0:
        raise ValueError("Currency rate must be positive and explicitly supplied")
    return decimal(amount) * rate
