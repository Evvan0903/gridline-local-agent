import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import main


def assistant_call(name, arguments, call_id=None):
    call = {"function": {"name": name, "arguments": arguments}}
    if call_id:
        call["id"] = call_id
    return {"role": "assistant", "content": "", "tool_calls": [call]}


class AgentLoopTests(unittest.TestCase):
    def run_scripted(self, replies):
        history = copy.deepcopy(main.messages)
        requests_sent = []

        def respond(*args, **kwargs):
            requests_sent.append(copy.deepcopy(kwargs["json"]))
            response = Mock()
            response.json.return_value = {"message": replies[len(requests_sent) - 1]}
            return response

        with patch.object(main.requests, "post", side_effect=respond):
            with redirect_stdout(io.StringIO()):
                main.run_agent(history)
        return history, requests_sent

    def test_both_tools_execute_and_results_return_to_model(self):
        for arguments in ({"sheet_name": "1. Sales Roster"},
                          json.dumps({"sheet_name": "1. Sales Roster"})):
            with self.subTest(arguments=arguments):
                final = {"role": "assistant", "content": "80 employees"}
                history, sent = self.run_scripted([
                    assistant_call("list_excel_sheets", {}),
                    assistant_call("inspect_excel_sheet", arguments, "inspect-1"),
                    final,
                ])
                sheets = sent[1]["messages"][-1]
                self.assertEqual(sheets["role"], "tool")
                self.assertEqual(sheets["tool_name"], "list_excel_sheets")
                self.assertEqual(json.loads(sheets["content"]), main.list_excel_sheets())
                inspection = sent[2]["messages"][-1]
                self.assertEqual(inspection["tool_name"], "inspect_excel_sheet")
                self.assertEqual(inspection["tool_call_id"], "inspect-1")
                result = json.loads(inspection["content"])
                self.assertEqual(result["row_count"], 80)
                self.assertEqual(result["columns"],
                                 ["name", "hr_id", "crm_owner_id", "slack_user_id"])
                self.assertEqual(len(result["sample"]), 3)
                self.assertEqual(history[-1], final)

    def test_analyze_employee_roster_registered_and_dispatched(self):
        definitions = [tool["function"] for tool in main.tools
                       if tool["function"]["name"] == "analyze_employee_roster"]
        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0]["parameters"],
                         {"type": "object", "properties": {}, "required": []})
        final = {"role": "assistant", "content": "Roster analysis complete"}
        history, sent = self.run_scripted([
            assistant_call("analyze_employee_roster", {}), final,
        ])
        result_message = sent[1]["messages"][-1]
        self.assertEqual(result_message["tool_name"], "analyze_employee_roster")
        result = json.loads(result_message["content"])
        self.assertEqual(result, main.analyze_employee_roster())
        self.assertEqual(result["total_employees"], 80)
        self.assertEqual(history[-1], final)

    def test_tool_errors_return_to_model(self):
        for name, arguments in [
            ("inspect_excel_sheet", {}),
            ("inspect_excel_sheet", {"sheet_name": "missing worksheet"}),
            ("inspect_excel_sheet", "invalid JSON"),
            ("inspect_excel_sheet", []),
            ("unknown_tool", {}),
        ]:
            with self.subTest(name=name, arguments=arguments):
                _, sent = self.run_scripted([
                    assistant_call(name, arguments),
                    {"role": "assistant", "content": "Tool failed"},
                ])
                result = sent[1]["messages"][-1]
                self.assertEqual(result["role"], "tool")
                self.assertTrue(result["content"].startswith("Error:"))

    @unittest.skipUnless(os.environ.get("LIVE_OLLAMA") == "1", "Set LIVE_OLLAMA=1")
    def test_live_qwen_calls_both_tools(self):
        history = copy.deepcopy(main.messages)
        history[-1]["content"] = (
            "First list all worksheet names using list_excel_sheets. "
            "Then inspect '1. Sales Roster' using inspect_excel_sheet. "
            "Report its employee count and column names."
        )
        main.run_agent(history)
        results = {m["tool_name"]: json.loads(m["content"])
                   for m in history if m["role"] == "tool"}
        self.assertEqual(results["list_excel_sheets"], main.list_excel_sheets())
        self.assertEqual(results["inspect_excel_sheet"],
                         main.inspect_excel_sheet("1. Sales Roster"))
        self.assertEqual(history[-1]["role"], "assistant")
        self.assertFalse(history[-1].get("tool_calls"))
        self.assertIn("80", history[-1]["content"])
        for column in results["inspect_excel_sheet"]["columns"]:
            self.assertIn(column, history[-1]["content"])


if __name__ == "__main__":
    unittest.main()
