"""V2 candidate adapter. Reuses the existing case engine; never approves payroll.

Excel compilation is generic. This adapter supplies an explicitly conditional
Python input schedule and full source lineage. No reference answer is a model input.
"""
import json
from pathlib import Path
import shutil
from decimal import Decimal
import pandas as pd
from . import benchmark as legacy
from local_analyst.financial_model import build_financial_model, evaluate, number
from local_analyst.exporting import export_pdf
from local_analyst.workspace import dump, fingerprint


def F(name):return {'field':name}
def P(name):return {'param':name}
def op(name,*args):return {name:list(args)}


def case_spec():
    # Rates/thresholds and eligibility are source-derived schedule inputs. Parameters
    # change only SMB accelerator pricing; revenue, ramp and hierarchy refresh in Python.
    def commission(m1,m2):
        return op('mul',F('pay_eligible'),op('add',
            op('mul',op('min',F('credited_revenue'),F('threshold1')),F('rate')),
            op('mul',op('max',op('sub',op('min',F('credited_revenue'),F('threshold2')),F('threshold1')),0),F('rate'),m1),
            op('mul',op('max',op('sub',F('credited_revenue'),F('threshold2')),0),F('rate'),m2),
            op('mul',F('secondary_credit'),F('rate2')),F('spiff')))
    m1=op('add',F('multiplier1'),op('mul',F('scenario_eligible'),op('sub',P('smb_band1'),F('multiplier1'))))
    m2=op('add',F('multiplier2'),op('mul',F('scenario_eligible'),op('sub',P('smb_band2'),F('multiplier2'))))
    return {'version':1,'rule_ref':'Original rules sections 7.8-7.13; conditional source schedule',
      'source_key':'hr_id','status':'CONDITIONAL; held/disputed allocation unresolved; actual paid unavailable',
      'parameters':{'smb_band1':{'value':'1.3','description':'Scenario SMB AE band I multiplier'},
                    'smb_band2':{'value':'1.5','description':'Scenario SMB AE band II multiplier'}},
      'calculations':[
       {'id':'baseline_unrounded','expression':commission(F('multiplier1'),F('multiplier2'))},
       {'id':'baseline_usd','expression':op('round',F('baseline_unrounded'),2)},
       {'id':'baseline_local','expression':op('round',op('mul',F('baseline_unrounded'),F('fx_units_per_usd')),2)},
       {'id':'scenario_unrounded','expression':commission(m1,m2)},
       {'id':'scenario_usd','expression':op('round',F('scenario_unrounded'),2)},
       {'id':'scenario_local','expression':op('round',op('mul',F('scenario_unrounded'),F('fx_units_per_usd')),2)},
       {'id':'delta_usd','expression':op('sub',F('scenario_usd'),F('baseline_usd'))}],
      'totals':['baseline_usd','scenario_usd','delta_usd']}


def coverage(data, month):
    start=pd.Timestamp(month).replace(day=1);end=start+pd.offsets.MonthEnd(0)
    shifts=data['base']['shifts'];billing=data['sources']['billing']
    worked=int(shifts.shift_worked_date.between(start,end).sum())
    posted=int(shifts.shift_posted_date.between(start,end).sum())
    tickets=int(billing.billing_month.eq(start.strftime('%Y-%m')).sum())
    # Presence is a necessary check, not proof the extract is complete.
    return {'month':start.strftime('%Y-%m'),'worked_rows':worked,'posted_rows':posted,'billing_rows':tickets,
            'present':worked>0 and posted>0 and tickets>0,'complete':False,
            'reason':'Source coverage attestation and full historical GTM posting coverage unavailable'}


def candidate(workbook, rules, destination, month='2026-06-01'):
    rules=Path(rules);workbook=Path(workbook);destination=Path(destination)
    if not rules.is_file():raise ValueError('ORIGINAL_RULES_MISSING')
    if destination.exists():raise ValueError('Preserve existing candidate destination')
    original={str(p):fingerprint(p) for p in (workbook,rules)}
    data=legacy.prepare(workbook,rules)
    config=json.loads(legacy.CONFIG.read_text());config['review_month']=month
    cov=coverage(data,month)
    if not cov['present']:raise ValueError('PERIOD_COVERAGE_REQUIRED:'+dump(cov))
    base=legacy.calculate(data,config)
    alternative=legacy.calculate(data,config,config['smb_scenario_multipliers'])
    ledger=legacy.exception_ledger(data,base,config)
    from .v2_exceptions import attribution
    impacts,impact_details,waterfall=attribution(data,base,config)
    for r in impacts.itertuples():
        ledger.loc[ledger.topic.eq(r.topic),'commission_impact_usd']=r.net_incremental_payout_usd
        ledger.loc[ledger.topic.eq(r.topic),'affected_employees']=r.affected_reps
        ledger.loc[ledger.topic.eq(r.topic),'treatment']=r.counterfactual_definition+' '+r.treatment
    # Missing closer has no counterfactual assignee. Its economic opportunity cost is unknown.
    ledger.loc[ledger.topic.eq('missing_closer'),'commission_impact_usd']=None
    # Unknown commission impact remains explicitly unresolved; never substitute zero.
    ledger['impact_status']=ledger.commission_impact_usd.map(lambda v:'UNRESOLVED' if pd.isna(v) else 'conditional_counterfactual')
    ledger['interaction']='Counterfactuals are non-additive; shared employees/accounts need joint attribution before payroll approval'
    ledger['payout_control']='Review required; no automatic exclusion or approval'
    linked,_,_=legacy.communications(data,base,ledger)
    # Select actual actionable questions, rather than high-impact topic keywords in routine updates.
    actionable=linked[linked.message.str.contains(r'\?|Ops flagged|Followed up',regex=True) & linked.linked_evidence_ids.ne('')].copy()
    actionable['decision_priority']=actionable.message.str.contains(r'same facility|still being credited|commission still process|Negotiation|denied',case=False,regex=True).astype(int)
    actionable=actionable.sort_values(['decision_priority','financial_exposure'],ascending=False,kind='stable')
    picks=[];topics=set()
    for idx,row in actionable.iterrows():
        if row.topics in topics:continue
        picks.append(idx);topics.add(row.topics)
        if len(picks)==3:break
    escalations=actionable.loc[picks].copy()
    escalations['payout_concern']=escalations.topics
    escalations['why_escalate']='Actual source question concerns June attribution or eligibility; reconcile statement before changing payment'
    escalations['support_and_limits']='Linked counterfactual is aggregate entity/topic support, not a proven transaction or actual-paid discrepancy'
    escalations['required_decision']='Confirm account/statement identifiers, original rule treatment and any approved exception with Finance'
    join_impact=impacts.loc[impacts.topic.eq('join_multiplication'),'net_incremental_payout_usd'].iloc[0]
    gtm_impact=ledger.loc[ledger.topic.eq('posting_fill_anomaly'),'commission_impact_usd'].iloc[0]
    response=(f'Sales leadership: Please review the rural GTM posting concern before authorizing June adjustments. '
      f'Excluding one investigated GTM account would reduce conditional commission by USD {gtm_impact:,.2f}, including management effects. '
      'The Slack message does not identify an account, so this sensitivity is not yet a transaction match or evidence of an incorrect payment. '
      'GTM posted-volume credit applies to AEs. AM retention uses the existing-book revenue rules, so the two concerns must be reconciled separately. '
      'Please have Sales Operations confirm who authorized the shift templates and identify the facility. Finance should match its source records to the actual statement. '
      'Low fill alone does not authorize withholding commission. For the leaver question, check recognition dates against employment dates; managers still carry the revenue. '
      'Denied billing tickets must not reduce the AM number. Any exception to the written plan needs written approval. We have not changed a payment.')
    frame=base['people'].copy()
    frame['pay_eligible']=frame.employed_days.gt(0).astype(int)
    frame['scenario_eligible']=(frame.role.eq('SMB Account Executive') & frame.multiplier1.map(lambda x:x!=1)).astype(int)
    frame['conditionally_payable_usd']='UNRESOLVED_PENDING_APPROVAL'
    frame['held_or_disputed_usd']='UNRESOLVED_PENDING_ATTRIBUTION'
    frame['actual_paid_usd']='UNAVAILABLE_NO_PAYMENT_RECORDS'
    frame=frame.drop(columns=['usd_unrounded','local_unrounded','usd','local'])
    # Native numeric fields remain numeric; optional text/list columns are traceability only.
    frame=frame.fillna('UNAVAILABLE')
    spec=case_spec()
    provenance={'sources_sha256':original,'period':month,'coverage':cov,'conditional_assumptions':config['assumptions']}
    result=build_financial_model(spec,frame,destination,provenance,{
        'Exceptions':ledger,'Exception impacts':impacts,'Impact waterfall':waterfall,'Escalations':escalations,'Assumptions':pd.DataFrame(config['assumptions'])})
    evaluated=evaluate(spec,frame)
    actual=sum(evaluated.baseline_usd,Decimal(0));scenario=sum(evaluated.scenario_usd,Decimal(0))
    if actual!=base['total_usd'] or scenario!=alternative['total_usd']:
        raise ValueError('Formula/Python case mismatch')
    # Detailed large schedules stay as companion CSVs with executable source code.
    for name,table in [('credit_events',base['events']),('existing_book',base['books']),
        ('shift_fact',data['base']['shifts']),('account_master',data['base']['accounts']),
        ('exceptions',ledger),('exception_rep_impacts',impact_details),('exception_waterfall',waterfall),('escalations',escalations),('rep_outputs',evaluated)]:
        table.to_csv(destination/(name+'.csv'),index=False)
    code=destination/'code';code.mkdir(exist_ok=True)
    for package in ('local_analyst','examples/clipboard'):
        target=code/package;target.mkdir(parents=True,exist_ok=True)
        for source in (Path(__file__).resolve().parents[2]/package).glob('*.py'):shutil.copy2(source,target/source.name)
    shutil.copy2(legacy.CONFIG,code/'examples/clipboard/plan.json')
    shutil.copy2(Path(__file__).resolve().parents[2]/'workbook_discovery.py',code/'workbook_discovery.py')
    (code/'examples/__init__.py').write_text('')
    (destination/'REFRESH.md').write_text('Run from the project root: `.venv/bin/python -m examples.clipboard.v2 --workbook PATH --rules PATH --output NEW_DIRECTORY --month YYYY-MM-01`. Code is included under code/. Install project requirements. Python refreshes revenue, ownership, ramp and hierarchy input schedules. Excel Parameters update tier pricing only. Missing-period presence rejects refresh. Approved payroll also requires complete source coverage, approved interpretations and resolved exceptions. None is inferred from source presence.\n')
    rows=[['Finding','USD impact','Treatment']]+[[r.topic,str(r.commission_impact_usd) if pd.notna(r.commission_impact_usd) else 'UNRESOLVED','Review; impacts are not additive'] for r in ledger.assign(_impact=ledger.commission_impact_usd.map(lambda v:abs(v) if pd.notna(v) else Decimal(-1))).sort_values('_impact',ascending=False).itertuples()]
    sections=[{'title':'June conditional commission model','paragraphs':[
      f'80 roster reps. Plan-calculated USD {actual:,.2f}; repriced USD {scenario:,.2f}; delta USD {scenario-actual:,.2f}.',
      'Actual paid, conditionally payable and held allocations are unavailable or unresolved. Source coverage is not attested complete. This model is not approved payroll.',
      'Only eligible SMB AE tier pricing changes. Manager carried revenue is unchanged by accelerator repricing. Editable multipliers update detail, local currency, scenario total and delta. Revenue/ramp/hierarchy require the documented Python refresh.']},
      {'page_break':True,'title':'Exceptions and assumptions','rows':rows,'paragraphs':[
      'The posting/fill account sensitivity is quantified. A largest-of-all finding cannot be independently certified while other commission impacts remain unresolved.',
      'Partial-month ramp and rounding interpretations require approval. Unknown impacts have not been treated as zero. Counterfactual impacts overlap and must not be summed.']},
      {'page_break':True,'title':'Three Slack escalations','paragraphs':[
        f'Source row {r.message_source_row}: {r.message[:240]} Support: {r.linked_evidence_ids}. Entity/topic linkage requires transaction review; no Slack promise overrides the plan.' for r in escalations.itertuples()]+['Draft response (not sent): '+response]}]
    export_pdf('Clipboard Health - conditional case review',sections,destination/'summary.pdf')
    from pypdf import PdfReader
    pdf=PdfReader(destination/'summary.pdf');text=' '.join(p.extract_text() for p in pdf.pages)
    if len(pdf.pages)>3 or len(escalations)!=3 or len(response.split())>250 or any(f'{n:,.2f}' not in text for n in (actual,scenario,scenario-actual)):
        raise ValueError('PDF or Slack structural validation failed')
    if any(fingerprint(Path(p))!=sha for p,sha in original.items()):raise ValueError('SOURCE_CHANGED')
    validation={'Q1':{'status':'BLOCKED','modeled_reps':len(frame),'conditional_total_usd':str(actual),
          'reason':'Approvals, complete coverage, payable/held allocation and payment records unavailable'},
       'Q2':{'status':'FAIL','unresolved_topics':ledger.loc[ledger.impact_status.eq('UNRESOLVED'),'topic'].tolist(),
          'reason':'Four named naive-processing sensitivities and ordered interaction waterfall quantified; missing-owner opportunity cost unresolved; no actual-paid comparison. Largest finding requires full independent impact review.'},
       'Q3':{'status':'PASS','scope':'conditional plan scenario only; actual-paid comparison BLOCKED','scenario_usd':str(scenario),'delta_usd':str(scenario-actual)},
       'Q4':{'status':'PASS','selected_messages':len(escalations),'response_words':len(response.split()),
          'scope':'Documentary escalation selection and draft; does not prove a transaction-level payment discrepancy',
          'source_rows':escalations.message_source_row.tolist(),
          'reason':'Three actual actionable source questions; linked financial evidence and limitations; leadership response under 250 words; independent source/text check required in acceptance report'},
       'overall':'FAIL','pdf_pages':len(pdf.pages),'pdf_excel_consistent':True,'coverage':cov}
    (destination/'case_validation.json').write_text(dump(validation))
    return {**result,'files_sha256':{p.name:fingerprint(p) for p in destination.iterdir() if p.is_file()},'pdf':{'path':str((destination/'summary.pdf').resolve())},'validation':validation,'source_sha256':original}


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--workbook',required=True);p.add_argument('--rules',required=True)
    p.add_argument('--output',required=True);p.add_argument('--month',default='2026-06-01');a=p.parse_args()
    print(dump(candidate(a.workbook,a.rules,a.output,a.month)))
if __name__=='__main__':main()
