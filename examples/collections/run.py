"""A different schema and explicitly supplied financial policy using ONLY generic core."""
import json
from pathlib import Path
import pandas as pd

from local_analyst import Workspace
from local_analyst.modeling import run_plan, scenario
from local_analyst.workspace import dump
from local_analyst.exporting import export_query, export_excel


def main():
    here=Path(__file__).parent
    root=here.parents[1]/'output'/'collections'
    workspace=Workspace(root/'workspace',restore=False)
    workspace.load(here/'invoices.csv','collections')
    plan=json.loads((here/'plan.json').read_text())
    result=scenario(workspace,plan,{'rate':'0.2'},'converted','local_fee')
    evidence=workspace.query('SELECT * FROM model_baseline_converted')
    exported=export_query(workspace,evidence['query_id'],'baseline',pdf=True)
    export_excel({'Baseline':workspace.frames['model_baseline_converted'],
                  'Scenario':workspace.frames['model_scenario_converted'],
                  'Parameters':pd.DataFrame([{'plan':dump(plan),'comparison':dump(result)}])},root/'model.xlsx')
    (root/'scenario.json').write_text(dump(result))
    print(dump({'result':result,'outputs':exported}))


if __name__=='__main__':main()
