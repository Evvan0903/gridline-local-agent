"""Deterministic, restricted expression compiler for auditable financial workbooks.

No free-form Excel formulas, Python, or LLM rule approval. Python input schedules
are snapshots with explicit refresh boundaries. Parameters drive live Excel builds.
"""
from __future__ import annotations
import copy
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import pandas as pd
from .exporting import export_excel, excel_value
from .workspace import dump, fingerprint

IDENT = re.compile(r'^[a-z][a-z0-9_]{0,59}$')


def col(i):
    result=''
    while i:
        i,r=divmod(i-1,26);result=chr(65+r)+result
    return result


def number(v):
    if isinstance(v,bool) or v is None:
        raise ValueError('Missing/invalid financial number')
    n=Decimal(str(v))
    if not n.is_finite():raise ValueError('Non-finite financial number')
    return n


def expression(node, fields, params, mode='excel', depth=0):
    if depth>25:raise ValueError('Expression too deep')
    if not isinstance(node,dict):
        n=number(node);return str(n) if mode=='excel' else n
    if len(node)!=1:raise ValueError('Expression must have exactly one operation')
    kind,value=next(iter(node.items()))
    if kind in ('field','param'):
        source=fields if kind=='field' else params
        if value not in source:raise ValueError('Unknown or forward reference: '+str(value))
        return source[value] if mode=="excel" else number(source[value])
    if kind not in ('add','sub','mul','div','min','max','round') or not isinstance(value,list):
        raise ValueError('Unsupported formula operation: '+kind)
    args=[expression(v,fields,params,mode,depth+1) for v in value]
    if (kind in ('sub','div','round') and len(args)!=2) or (kind in ('add','mul','min','max') and len(args)<2):
        raise ValueError('Invalid operation arity')
    if kind=='round':
        digits=value[1]
        if type(digits) is not int or not 0<=digits<=8:raise ValueError('Rounding digits must be 0..8')
    if mode=='excel':
        if kind in ('min','max','round'):return kind.upper()+'('+','.join(args)+')'
        return '('+{'add':'+','sub':'-','mul':'*','div':'/'}[kind].join(args)+')'
    if kind=='add':return sum(args,Decimal(0))
    if kind=='sub':return args[0]-args[1]
    if kind=='mul':
        out=Decimal(1)
        for a in args:out*=a
        return out
    if kind=='div':return args[0]/args[1]
    if kind=='min':return min(args)
    if kind=='max':return max(args)
    return args[0].quantize(Decimal(1).scaleb(-int(args[1])),rounding=ROUND_HALF_UP)


def validate_spec(spec, frame):
    from .financial_validation import validate_financial_spec
    validate_financial_spec(spec,frame)
    if spec.get('version')!=1 or not spec.get('rule_ref'):raise ValueError('Model version and governing rule reference required')
    if any(a.get('status') not in ('governing_rule','explicit_user_choice') for a in spec.get('assumptions',[])):
        raise ValueError('Unresolved assumptions block this reviewed model')
    if spec.get('approved_result') and not spec.get('coverage_complete'):
        raise ValueError('PERIOD_COVERAGE_REQUIRED')
    if not spec.get('source_key') or spec['source_key'] not in frame:
        raise ValueError('Source-row key required')
    keys=frame[spec['source_key']]
    if keys.isna().any() or keys.duplicated().any():raise ValueError('Source-row key must be non-null and unique')
    if len(frame)==0 or len(frame)>10000:raise ValueError('Model needs 1..10000 input schedule rows')
    names=set(frame.columns)
    if any(not IDENT.fullmatch(str(n)) for n in names):raise ValueError('Input field names must be stable identifiers')
    params=spec.get('parameters',{})
    for name,p in params.items():
        if not IDENT.fullmatch(name):raise ValueError('Invalid parameter name')
        number(p['value'])
    for calc in spec.get('calculations',[]):
        name=calc.get('id','')
        if not IDENT.fullmatch(name) or name in names:raise ValueError('Invalid/duplicate calculation')
        expression(calc['expression'],{f:'1' for f in names},{p:'1' for p in params})
        names.add(name)
    if not spec.get('calculations') or any(s not in names for s in spec.get('totals',[])):
        raise ValueError('Explicit calculations and valid totals required')


def evaluate(spec, frame, overrides=None):
    validate_spec(spec,frame)
    if set(overrides or {})-set(spec.get('parameters',{})):raise ValueError('Undeclared parameter override')
    params={k:number((overrides or {}).get(k,p['value'])) for k,p in spec['parameters'].items()}
    records=[]
    for record in frame.to_dict('records'):
        values={k:(number(v) if not isinstance(v,str) else v) for k,v in record.items() if v is not None}
        for i,calc in enumerate(spec['calculations']):
            try:values[calc['id']]=expression(calc['expression'],values,params,'decimal')
            except (ArithmeticError,ValueError,TypeError) as error:
                from .financial_validation import FinancialPlanError
                raise FinancialPlanError([{'error_code':'EXPRESSION_EVALUATION','json_path':f'$.calculations[{i}].expression','message':'Calculation cannot evaluate: inspect numeric source inputs, references and zero denominators. Formula was not rewritten.','expected_type':'defined finite numeric result','received_type':type(error).__name__}]) from error
        records.append(values)
    return pd.DataFrame(records)


def build_financial_model(spec, frame, destination, provenance, supporting=None):
    """Compile a reviewed structured model; preserve destination and supplied sources."""
    validate_spec(spec,frame)
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Model destination already exists')
    evaluated=evaluate(spec,frame)
    params=list(spec['parameters']); inputs=list(frame.columns)
    calculations=[c['id'] for c in spec['calculations']]
    allcols=inputs+calculations
    formulas={'Build':{},'Summary':{},'Reconciliation':{}}
    rows=frame.copy()
    for c in calculations:rows[c]=None
    paramrefs={p:f"'Parameters'!$B${i+2}" for i,p in enumerate(params)}
    dependencies={}
    for r in range(2,len(frame)+2):
        refs={f:f"'Inputs'!{col(i+1)}{r}" for i,f in enumerate(inputs)}
        for i,f in enumerate(inputs):formulas['Build'][f'{col(i+1)}{r}']='='+refs[f]
        for c in spec['calculations']:
            target=f'{col(allcols.index(c["id"])+1)}{r}'
            formula='='+expression(c['expression'],refs,paramrefs)
            formulas['Build'][target]=formula
            dependencies['Build!'+target]=formula
            refs[c['id']]=target
    totals=spec.get('totals',calculations[-1:])
    summary=pd.DataFrame([{'metric':t,'amount':None,'status':spec.get('status','reviewed model; payment evidence separate')} for t in totals])
    checks=pd.DataFrame([{'metric':t,'detail_less_summary':None} for t in totals])
    for i,t in enumerate(totals,2):
        c=col(allcols.index(t)+1)
        formulas['Summary'][f'B{i}']=f"=SUM('Build'!{c}2:{c}{len(frame)+1})"
        formulas['Reconciliation'][f'B{i}']=f"=SUM('Build'!{c}2:{c}{len(frame)+1})-'Summary'!B{i}"
    tables={'Summary':summary,'Parameters':pd.DataFrame([{'parameter':p,'value':float(number(spec['parameters'][p]['value'])),
               'meaning':spec['parameters'][p].get('description','')} for p in params],columns=['parameter','value','meaning']),
            'Inputs':frame,'Build':rows,'Reconciliation':checks,
            'Model Guide':pd.DataFrame({'topic':['Status','Live boundary','Refresh','Rounding','Source'],
              'description':[spec.get('status','reviewed calculation; payment evidence separate'),
               'Editable Parameters update Build and Summary. Inputs and supporting schedules are Python snapshots.',
               'Reload complete period source coverage; rerun reviewed local calculation; create a NEW model. Existing workbooks do not auto-refresh source files.',
               'ROUND_HALF_UP; row rounding must be explicit in the reviewed expressions.','Source evidence and hashes: lineage.json. Input rows retain stable source keys; large supporting schedules accompany the workbook as CSV.']})}
    tables.update(supporting or {})
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.model-',dir=destination.parent))
    try:
        specpath=temp/'model_spec.json';specpath.write_text(dump(spec))
        frame.to_json(temp/'inputs.json',orient='records',default_handler=str)
        (temp/'lineage.json').write_text(dump({'provenance':provenance,'dependencies':dependencies,
                'spec_sha256':fingerprint(specpath),'refresh_boundary':'Python input snapshots; live parameter formulas'}))
        code=temp/'code'/'local_analyst';code.mkdir(parents=True)
        for source in Path(__file__).parent.glob('*.py'):shutil.copy2(source,code/source.name)
        requirements=next((p/'requirements.txt' for p in Path(__file__).resolve().parents if (p/'requirements.txt').is_file()),None)
        if requirements:shutil.copy2(requirements,temp/'requirements.txt')
        assets=code/'assets';assets.mkdir()
        for source in (Path(__file__).parent/'assets').glob('*.mjs'):shutil.copy2(source,assets/source.name)
        runtime=Path(__file__).parent/'assets'/'node_modules'
        if runtime.exists():(assets/'node_modules').symlink_to(runtime.resolve(),target_is_directory=True)
        (temp/'refresh_model.py').write_text("from pathlib import Path\nimport json,sys\nimport pandas as pd\nsys.path.insert(0,str(Path(__file__).parent/'code'))\nfrom local_analyst.financial_model import build_financial_model\nr=Path(__file__).parent\ns=json.loads((r/'model_spec.json').read_text())\nf=pd.DataFrame(json.loads((r/'inputs.json').read_text()))\nbuild_financial_model(s,f,sys.argv[1],{'refresh':'input snapshot; replace inputs only after source coverage review'})\n")
        (temp/'expected.json').write_text(dump({'totals':{t:str(sum((number(v) for v in evaluated[t]),Decimal(0))) for t in totals}}))
        export_excel(tables,temp/'model.xlsx',formulas,financial=True)
        # Independently reopen the emitted file and compare saved numeric caches to Decimal oracle.
        from openpyxl import load_workbook
        reopened=load_workbook(temp/'model.xlsx',data_only=True)
        for i,t in enumerate(totals,2):
            expected=sum((number(v) for v in evaluated[t]),Decimal(0))
            observed=reopened['Summary'][f'B{i}'].value
            if observed is None or abs(number(observed)-expected)>Decimal('0.005'):
                raise ValueError('Reopened workbook does not reconcile: '+t)
        errors=[]
        for sheet in reopened:
            for row in sheet:
                errors.extend(f'{sheet.title}!{c.coordinate}:{c.value}' for c in row if c.data_type=='e')
        if errors:raise ValueError('Formula errors: '+str(errors[:5]))
        (temp/'validation.json').write_text(dump({'status':'PASS','engine':'Artifact Tool + independent Decimal + reopened XLSX cache',
            'totals':{t:str(sum((number(v) for v in evaluated[t]),Decimal(0))) for t in totals},'formula_errors':errors}))
        temp.rename(destination)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return {'status':'exported','kind':'dynamic_financial_model','path':str(destination/'model.xlsx'),
            'sha256':fingerprint(destination/'model.xlsx'),'validation':json.loads((destination/'validation.json').read_text())}


def export_workspace_model(workspace, plan_id, query_id, name='financial_model'):
    if not re.fullmatch('[a-zA-Z0-9_-]+',name):raise ValueError('Invalid model output name')
    plan=workspace.reviewed_plan(plan_id)
    spec=plan.get('financial_model')
    if not spec:raise ValueError('Reviewed plan has no financial_model specification')
    evidence=workspace.query_evidence(query_id)
    if not evidence.get('dependencies'):raise ValueError('Financial inputs need source dependencies')
    frame=workspace.query_result_frame(query_id)
    result=build_financial_model(spec,frame,workspace.root/'outputs'/name,{'query_id':query_id,'evidence':evidence,'plan_id':plan_id})
    workspace.query_evidence(query_id)
    workspace.record('financial_model_export',query_id=query_id,result=result)
    return {**result,'query_id':query_id,'excel':{'path':result['path']}}
