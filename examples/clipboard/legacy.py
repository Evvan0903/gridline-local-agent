
import json
from pathlib import Path

import pandas as pd
import requests
from local_analyst.config import resolve_model


# ==================================
# 1. Configuration
# ==================================

MODEL = resolve_model()
OLLAMA_URL = "http://localhost:11434/api/chat"

BASE_DIR = Path(__file__).resolve().parents[2]
EXCEL_PATH = BASE_DIR / "data" / "Sales Compensation Analyst Case Study.xlsx"

MAX_STEPS = 5


# ==================================
# 2. Python Tool
# ==================================

def list_excel_sheets():
    """Return the names of worksheets in the workbook."""
    with pd.ExcelFile(EXCEL_PATH) as excel:
        return excel.sheet_names


def inspect_excel_sheet(sheet_name):
    """Inspect the structure of one worksheet."""

    df = pd.read_excel(EXCEL_PATH, sheet_name=sheet_name)

    return {
        "sheet_name": sheet_name,
        "row_count": len(df),
        "columns": df.columns.tolist(),
        "sample": df.head(3).fillna("").astype(str).to_dict(
            orient="records"
        )
    }

def inspect_workbook():
    """Inspect the structure of every worksheet."""

    workbook = pd.ExcelFile(EXCEL_PATH)

    result = {}

    for sheet_name in workbook.sheet_names:
        df = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
            nrows=0
        )

        result[sheet_name] = {
            "columns": df.columns.tolist()
        }

    workbook.close()

    return result

def analyze_employee_roster():
    roster = pd.read_excel(EXCEL_PATH, sheet_name="1. Sales Roster")
    hr = pd.read_excel(EXCEL_PATH, sheet_name="2. HR Export")

    merged = pd.merge(
        roster,
        hr,
        on="hr_id",
        how="left",
        validate="one_to_one",
        indicator=True
    )

    return {
        "total_employees": len(merged),
        "matched": int((merged["_merge"] == "both").sum()),
        "role_distribution": merged["Current Role"].value_counts().to_dict()
    }
# ==================================
# 3. Tool Definition for Qwen3
# ==================================


tools = [
    {
        "type": "function",
        "function": {
            "name": "list_excel_sheets",
            "description": "List all worksheet names in the Excel workbook.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_excel_sheet",
            "description": (
                "Inspect a specific Excel worksheet. "
                "Return its row count, column names, "
                "and the first three rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet_name": {
                        "type": "string",
                        "description": (
                            "Exact worksheet name, "
                            "for example: 1. Sales Roster"
                        )
                    }
                },
                "required": ["sheet_name"]
            }
        }
    },
    
{
    "type": "function",
    "function": {
        "name": "inspect_workbook",
        "description": (
            "Inspect all worksheets in the Excel workbook "
            "and return their names and column names. "
            "Use this to discover the workbook structure."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
    },   

    {
        "type": "function",
        "function": {
            "name": "analyze_employee_roster",
            "description": "Analyze employee roles and HR matching in the Sales Roster.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
}
]


# ==================================
# 4. Conversation History
# ==================================

messages = [
    {
        "role": "system",
        "content": (
            "You are a local Excel analysis assistant. "
            "Use the available tools to inspect workbook data. "
            "Do not invent spreadsheet contents. "
            "If a tool fails, explain the error."
        )
    },

]


# ==================================
# 5. Agent Loop
# ==================================

def run_agent(messages):
    for step in range(MAX_STEPS):

        print(f"\nAgent Step {step + 1}")

        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": False
            },
            timeout=120
        )

        response.raise_for_status()

        message = response.json()["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls", [])

        # Model has finished its answer
        if not tool_calls:
            print("\nQwen3 Final Answer:")
            print(message.get("content", ""))
            break

        # Execute requested tools
        for tool_call in tool_calls:

            function_name = tool_call["function"]["name"]

            try:
                # Ollama supplies an object; also accept JSON-encoded arguments.
                arguments = tool_call["function"].get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")

                if function_name == "list_excel_sheets":
                    result = list_excel_sheets(**arguments)
                elif function_name == "inspect_excel_sheet":
                    result = inspect_excel_sheet(**arguments)
                elif function_name == "analyze_employee_roster":
                    result = analyze_employee_roster(**arguments)
                elif function_name == "inspect_workbook":
                    result = inspect_workbook(**arguments)
                else:
                    raise ValueError(f"Unknown tool {function_name}")

                tool_result = json.dumps(result, ensure_ascii=False)
                print("Tool executed:", function_name)
                print("Tool result:", result)

            except Exception as error:
                tool_result = f"Error: {error}"
                print(tool_result)

            # Return tool output to the model
            tool_message = {
                "role": "tool",
                "tool_name": function_name,
                "content": tool_result
            }

            # Associate response with its call when an ID is provided
            if tool_call.get("id"):
                tool_message["tool_call_id"] = tool_call["id"]

            messages.append(tool_message)

    else:
        print("\nAgent stopped: maximum steps reached.")



if __name__ == "__main__":
    print("Local Excel AI Agent")
    print("Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ")

        if user_input.lower() == "exit":
            print("Goodbye!")
            break

        messages.append({
            "role": "user",
            "content": user_input
        })

        run_agent(messages)

