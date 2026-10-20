"""Opt-in reviewed row-set/currency contracts. No inferred business eligibility."""
from decimal import Decimal as D
from pathlib import Path
import hashlib,json
import pandas as pd
from .workspace import dump,fingerprint
CATEGORIES={'payable','held','disputed','unknown'}

def validate_financial_rows(w,query_id,contract=None,*,result_frame=None,totals=None,rowset_only=False):
    def blocked(reason):return {'status':'BLOCKED','error_code':'ELIGIBILITY_RULE_UNRESOLVED','json_path':'$.financial_contract','reason':reason,'numerical_correctness':'NOT_EVALUATED'}
    if not contract:return blocked('Explicit reviewed source/status/currency contract required; model assumptions cannot authorize eligibility')
    try:
        if contract.get('kind')!='financial_rowset' or not contract.get('rule_ref'):return blocked('Explicit financial_rowset kind and governing rule_ref required')
        candidates=[t for t,m in w.catalog.items() if Path(m.get('source_path','')).name==contract.get('source_name') and m.get('kind')=='raw']
        if len(candidates)!=1:return blocked('Unique current raw source binding required')
        table=candidates[0];source=w.frames[table].copy();key=contract['source_key'];status_col=contract['status_column'];mapping=contract['status_map']
        if not isinstance(mapping,dict) or not set(mapping.values())<=CATEGORIES or 'payable' not in mapping.values():return blocked('Explicit status map to payable/held/disputed/unknown required')
        if contract.get('require_payable_only') is not True:return blocked('This restricted validator requires explicit payable-only scope')
        for c,value in contract.get('equals',{}).items():source=source[source[c].eq(value)]
        if contract.get('date_column'):
            if not contract.get('start') or not contract.get('end'):return blocked('Closed-open eligibility date scope required')
            dates=pd.to_datetime(source[contract['date_column']],errors='raise')
            if dates.isna().any():return blocked('Unresolved source dates')
            source=source[dates.ge(contract['start'])&dates.lt(contract['end'])]
        if source[key].isna().any() or source[key].duplicated().any():return {'status':'FAIL','error_code':'SOURCE_GRAIN_DUPLICATE','json_path':'$.source_key','reason':'Reviewed source grain is not unique; deduplication authority is required','numerical_correctness':'NOT_EVALUATED'}
        classes=source[status_col].map(mapping).fillna('unknown')
        evidence=w.query_evidence(query_id)
        if table not in evidence.get('dependencies',{}):return blocked('Result query lacks the contracted source dependency')
        frame=w.query_result_frame(query_id) if result_frame is None else result_frame
        result_key=contract.get('result_key',key)
        if result_key not in frame:return blocked('Result lacks source-row key; aggregated-only eligibility cannot be proved')
        partitions={c:source[classes.eq(c)].to_dict('records') for c in sorted(CATEGORIES)}
        versions={t:m['version_id'] for t,m in w.catalog.items()}
        payload={'contract':contract,'query_id':query_id,'source_versions':versions,'partitions':partitions}
        audit=w.root/'validations'/('eligibility_'+hashlib.sha256(dump(payload).encode()).hexdigest()[:16]+'.json');audit.parent.mkdir(exist_ok=True,parents=True);audit.write_text(dump(payload))
        common={'source_table':table,'query_id':query_id,'dataset_versions':versions,'audit':{'path':str(audit),'sha256':fingerprint(audit)},'partition_counts':{k:len(v) for k,v in partitions.items()},'numerical_correctness':'NOT_EVALUATED','limitations':['Row-set eligibility and currency aggregation only; mathematical/business formula correctness requires its own acceptance contract']}
        if (classes=='unknown').any():return {**blocked('Unknown source classifications require review; no guessed exclusions'),**common}
        expected=set(source.loc[classes.eq('payable'),key]);observed=frame[result_key]
        issues=[]
        if observed.isna().any() or observed.duplicated().any():issues.append({'error_code':'DUPLICATE_RESULT_GRAIN','json_path':'$.result_rows','reason':'Unexpected duplicate/null source keys; possible join multiplication'})
        bad=set(observed)-expected;missing=expected-set(observed)
        if bad or missing:issues.append({'error_code':'INELIGIBLE_PAYABLE_ROWS','json_path':'$.result_rows','unexpected_keys':sorted(map(str,bad)),'missing_payable_keys':sorted(map(str,missing)),'reason':'Payable output must exactly cover eligible source keys; held/disputed records remain in the audit'})
        if rowset_only:return {**common,'status':'FAIL' if issues else 'PASS','error_code':issues[0]['error_code'] if issues else None,'json_path':issues[0]['json_path'] if issues else None,'issues':issues,'scope':'rowset_only'}
        monetary=contract.get('monetary_columns')
        if not isinstance(monetary,dict) or not monetary:return {**blocked('Explicit monetary-column currency bindings required'),**common}
        total_ids=set(totals if totals is not None else contract.get('totals',[]));groups={}
        for amount,rule in monetary.items():
            if amount not in frame:
                return {**blocked('Missing contracted amount column: '+amount),**common}
            path='$.monetary_columns.'+amount
            if frame[amount].isna().any():issues.append({'error_code':'MISSING_MONEY','json_path':path,'reason':'Missing money cannot become zero'});continue
            values=[D(str(v)) for v in frame[amount]]
            if not all(v.is_finite() for v in values):return {**blocked('Non-finite monetary input'),**common}
            if rule.get('currency_column'):
                currencies=frame[rule['currency_column']]
                if currencies.isna().any() or any(not str(v).strip() for v in currencies):return {**blocked('Unresolved result currencies'),**common}
            elif isinstance(rule.get('fixed_currency'),str) and rule['fixed_currency'].strip():currencies=pd.Series([rule['fixed_currency']]*len(frame),index=frame.index)
            else:return {**blocked('No currency binding for '+amount),**common}
            unique=set(map(str,currencies));conversion=rule.get('conversion')
            if rule.get('currency_column') and not conversion:
                source_currency=rule.get('source_currency_column',rule['currency_column'])
                if source_currency not in source:return {**blocked('Source currency evidence missing for '+amount),**common}
                raw=source.set_index(key)
                if any(row[result_key] in raw.index and str(row[rule['currency_column']])!=str(raw.loc[row[result_key],source_currency]) for _,row in frame.iterrows()):issues.append({'error_code':'CURRENCY_LINEAGE_MISMATCH','json_path':path,'reason':'Result currency labels disagree with source evidence'})
            if conversion:
                required={'rule_ref','reporting_currency','source_amount_column','source_currency_column','rate_column','operation'}
                if not required<=set(conversion) or not conversion.get('rule_ref') or not isinstance(conversion.get('reporting_currency'),str) or not conversion['reporting_currency'].strip() or conversion['operation'] not in {'multiply','divide'}:return {**blocked('Explicit source-bound conversion and reporting-currency rule required'),**common}
                raw=source.set_index(key)
                for (_,row),observed_amount in zip(frame.iterrows(),values):
                    if row[result_key] not in raw.index:continue
                    src=raw.loc[row[result_key]];rate=D(str(src[conversion['rate_column']]));base=D(str(src[conversion['source_amount_column']]))
                    if not rate.is_finite() or rate<=0:return {**blocked('Unresolved source conversion rate'),**common}
                    if pd.isna(src[conversion['source_currency_column']]):return {**blocked('Unknown source conversion currency'),**common}
                    expected_amount=base*rate if conversion['operation']=='multiply' else base/rate
                    if abs(expected_amount-observed_amount)>D(str(rule.get('tolerance','.005'))):issues.append({'error_code':'CONVERSION_MISMATCH','json_path':path,'reason':'Output disagrees with explicit source amount/rate conversion'})
                if unique!={str(conversion['reporting_currency'])}:issues.append({'error_code':'REPORTING_CURRENCY','json_path':path,'reason':'Converted output must carry the declared reporting currency'})
            if len(unique)>1 and amount in total_ids and (totals is not None or rule.get('aggregation')!='per_currency'):issues.append({'error_code':'MIXED_CURRENCY_TOTAL','json_path':path,'reason':'Mixed currencies require separate per-currency subtotals or verified conversion, never an unqualified sum'})
            groups[amount]={c:str(sum((value for currency,value in zip(currencies,values) if str(currency)==c),D(0))) for c in sorted(unique)}
        # Every requested monetary total needs an explicit unit contract.
        unbound=total_ids-set(monetary)
        if unbound:return {**blocked('Total currency bindings missing: '+', '.join(sorted(unbound))),**common}
        return {**common,'status':'FAIL' if issues else 'PASS','error_code':issues[0]['error_code'] if issues else None,'json_path':issues[0]['json_path'] if issues else None,'issues':issues,'per_currency_subtotals':groups}
    except (KeyError,TypeError,ValueError,ArithmeticError) as error:return blocked('Unsupported/incomplete contract or source evidence: '+str(error))
