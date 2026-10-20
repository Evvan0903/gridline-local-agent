"""Mutation benchmark: same 11-sheet structure, new identities/dates/amounts."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

import pandas as pd

from local_analyst.workspace import dump
from .revenue import DEFAULT_WORKBOOK


def create_synthetic(source, output, factor=1.1, day_shift=365):
    mapping = {}
    def identity(value):
        if value not in mapping:
            mapping[value] = str(uuid.uuid5(uuid.NAMESPACE_URL, "local-analyst-synthetic:" + value))
        return mapping[value]
    money_headers = {"charge_rate_per_shift", "pay_rate_per_shift", "Base Comp", "Monthly Quota Target",
                     "Implied Monthly Commissions", "Implied Annual Commissions", "Monthly Target",
                     "Implied Monthly Commissions 2", "Implied Annual Commissions 2", "credit_amount"}
    sheets = []
    with pd.ExcelFile(source) as book:
        for name in book.sheet_names:
            raw = pd.read_excel(book, sheet_name=name, header=None, keep_default_na=False, na_values=[""])
            monetary = [i for i, value in enumerate(raw.iloc[0]) if value in money_headers]
            dates, rows = set(), []
            for row_index, row in enumerate(raw.itertuples(index=False, name=None)):
                converted = []
                for column, value in enumerate(row):
                    if pd.isna(value):
                        value = None
                    elif isinstance(value, (pd.Timestamp, datetime)):
                        value = {"__date": (pd.Timestamp(value) + timedelta(days=day_shift)).isoformat()}
                        dates.add(column)
                    elif isinstance(value, str):
                        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
                            value = identity(value)
                        elif re.fullmatch(r"(?:SH|OPP)-\d+", value):
                            value = "SYN-" + value
                        elif name == "6. Accelerators" and value.startswith("$"):
                            value = re.sub(r"\d+(?:\.\d+)?", lambda m: f"{float(m[0])*factor:g}", value)
                        elif name == "10. Billing Export" and column == 1 and re.fullmatch(r"\d{4}-\d\d", value):
                            value = (pd.Timestamp(value + "-01") + timedelta(days=day_shift)).strftime("%Y-%m")
                    elif row_index and column in monetary:
                        value = round(float(value) * factor, 2)
                    if name == "6. Accelerators" and isinstance(value, (int, float)):
                        if row_index in (2, 3) and column in (1, 3, 4, 7) or row_index in (8, 9) and column == 3:
                            value = round(value * factor, 2)
                    converted.append(value)
                rows.append(converted)
            sheets.append({"name": name, "rows": rows, "date_columns": sorted(dates)})
    assets = Path(__file__).resolve().parents[2] / "local_analyst" / "assets"
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        payload = Path(temporary) / "synthetic.json"
        payload.write_text(dump({"sheets": sheets}))
        process = subprocess.run(["node", str(assets / "export.mjs"), str(payload), str(output)],
                                 capture_output=True, text=True, timeout=300)
        if process.returncode:
            raise RuntimeError(process.stderr[-3000:])
    return {"path": str(output), "factor": factor, "day_shift": day_shift,
            "changed_uuid_count": len(mapping), "sheets": len(sheets)}


if __name__ == "__main__":
    print(create_synthetic(DEFAULT_WORKBOOK, Path("output/synthetic/input.xlsx")))
