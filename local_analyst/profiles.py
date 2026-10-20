"""Explicit legacy/reference boundary. Never imported by a generic model tool."""
from pathlib import Path
from .workspace import fingerprint

def clipboard_profile(profile,task,sources,output,models,rule_text,limits):
    root=Path(__file__).resolve().parents[1]
    original=root/'data/Sales Compensation Analyst Case Study.xlsx';rules=root/'Clipboard_Health_Case_Study_Rules.md'
    if len(sources)!=1 or fingerprint(sources[0])!=fingerprint(original) or rule_text!=rules.read_text():
        return {'status':'BLOCKED','reason':'Specialized Clipboard profile requires exact original source hash and complete governing rule text; no substitution for an unfamiliar task','api_requests':0,'api_usd':'0'}
    if profile=='assisted':
        if set(models)!={'analyst'} or limits!={'requests':8,'tools':24}:
            return {'status':'BLOCKED','reason':'Legacy assisted case runner supports only one model and historical 8/24 limits; no implicit budget changes','api_requests':0,'api_usd':'0'}
        from .providers import CLOUD_MODELS
        if models['analyst'] in CLOUD_MODELS:return {'status':'NOT RUN','reason':'V3 cloud disabled','api_requests':0,'api_usd':'0'}
        from .discovery import local_models
        if models['analyst'] not in local_models():return {'status':'BLOCKED','reason':'Selected local model not installed','api_requests':0,'api_usd':'0'}
        from .benchmarking import run_case
        result=run_case(models['analyst'],sources[0],rules,Path(output)/'legacy_assisted')
        result['assistance']='PREBUILT CLIPBOARD RULES/SLACK/EXCEL/PDF. Separately scored; not autonomous general reasoning.'
        result['task_scope']='Fixed original Clipboard full-case assignment; specialized operational profile, not arbitrary task execution'
        return result
    from examples.clipboard.v2 import candidate
    result=candidate(sources[0],rules,Path(output)/'reference_artifacts')
    # Independent existing source-version-specific Decimal oracle; no model sees it.
    import pandas as pd
    from decimal import Decimal as D
    oracle=pd.read_csv(root/'reports/validation/gridline_v2/independent_oracle/independent_payouts_and_lineage.csv',dtype=str).set_index('hr_id')
    observed=pd.read_csv(Path(output)/'reference_artifacts/rep_outputs.csv',dtype=str).set_index('hr_id')
    valid=set(oracle.index)==set(observed.index) and all(D(observed.loc[k,c])==D(oracle.loc[k,o]) for k in oracle.index for c,o in [('baseline_usd','usd'),('baseline_local','local'),('scenario_usd','scenario_usd')])
    return {'status':'REFERENCE_VERIFIED' if valid else 'FAILED','profile':'reference','model_requests':0,'api_requests':0,'api_usd':'0',
        'reference':result,'conditional_numeric_oracle':'PASS' if valid else 'FAIL','full_case':result['validation']['overall'],
        'limitations':['Reviewed deterministic reference, no model capability claim. Original-source conditional oracle; no payment approval inferred.']}
