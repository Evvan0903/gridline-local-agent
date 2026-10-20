import argparse
import json

from .agent import AnalystAgent
from .workspace import Workspace, dump
from .modeling import run_plan
from .config import resolve_model


def main():
    parser = argparse.ArgumentParser(description="Fully local Excel/CSV analyst using DuckDB and Ollama")
    parser.add_argument("--workspace", default=".analyst")
    parser.add_argument("--load", action="append", default=[], metavar="CSV_OR_XLSX")
    parser.add_argument("--namespace")
    parser.add_argument("--ask")
    parser.add_argument("--export-format", action="append", choices=["csv", "excel", "pdf"],
                        help="Explicit artifact permission for --ask; repeat for multiple formats. Default: direct answer")
    parser.add_argument("--sql")
    parser.add_argument("--plan")
    parser.add_argument("--tables", action="store_true")
    parser.add_argument("--model", help="Ollama tag; overrides OLLAMA_MODEL and ollama_config.json")
    args = parser.parse_args()
    if args.export_format and not args.ask:
        parser.error("--export-format requires --ask")
    args.model = resolve_model(args.model)
    workspace = Workspace(args.workspace)
    for path in args.load:
        print(dump(workspace.load(path, args.namespace)))
    if args.tables:
        print(dump(workspace.schema()))
    if args.sql:
        print(dump(workspace.query(args.sql)))
    if args.plan:
        plan_id = workspace.register_plan(args.plan)
        print(dump({"plan_id":plan_id,"run":run_plan(workspace, workspace.reviewed_plan(plan_id))}))
    if args.ask:
        print(dump(AnalystAgent(workspace, args.model).ask(args.ask, export_formats=args.export_format)))
    elif not (args.tables or args.sql or args.plan or args.load):
        agent = AnalystAgent(workspace, args.model)
        print("Local Excel/CSV Data Analyst. /load PATH, /tables, exit")
        while True:
            try:
                prompt = input("You: ")
            except EOFError:
                break
            if prompt.lower() in {"exit", "quit"}:
                break
            if prompt.startswith("/load "):
                print(dump(workspace.load(prompt[6:].strip())))
            elif prompt == "/tables":
                print(dump(workspace.schema()))
            else:
                try:
                    print(agent.ask(prompt)["answer"])
                except Exception as error:
                    print("Error:", error)


if __name__ == "__main__":
    main()
