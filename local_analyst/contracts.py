"""Small explicit acceptance contracts, not arbitrary financial-rule interpretation.

Only source-bound single-table scalar sum/count contracts are verified here.
Ambiguous/unsupported finance remains BLOCKED. COUNT aliased as money is rejected.
"""
from decimal import Decimal as D
from pathlib import Path
import pandas as pd
import sqlglot
from sqlglot import exp


def _literal(node,parameters):
    if isinstance(node,(exp.Cast,exp.Paren)):return _literal(node.this,parameters)
    if isinstance(node,exp.Literal):return str(node.this)
    if isinstance(node,exp.Placeholder):return str(parameters.get(node.this))
    return None

def _where_matches(tree,c,parameters):
    where=tree.args.get('where')
    expected=[('eq',k,str(v)) for k,v in c.get('equals',{}).items()]
    if c.get('date_column'):expected += [('gte',c['date_column'],str(c['start'])),('lt',c['date_column'],str(c['end']))]
    if where is None:return not expected
    def clauses(node):
        if isinstance(node,exp.Paren):return clauses(node.this)
        if isinstance(node,exp.And):return clauses(node.left)+clauses(node.right)
        if type(node) not in (exp.EQ,exp.GTE,exp.LT) or not isinstance(node.left,exp.Column):raise ValueError('Unsupported filter form; verification blocked')
        value=_literal(node.right,parameters)
        if value is None:raise ValueError('Filter literal/parameter unverified')
        return [(node.key,node.left.name,value)]
    try:return sorted(clauses(where.this))==sorted(expected)
    except ValueError:return False

def _coefficient(node,c):
    if isinstance(node,(exp.Alias,exp.Cast,exp.Paren,exp.Round)):return _coefficient(node.this,c)
    if c['operation']=='sum' and isinstance(node,exp.Sum):
        value=node.this
        if isinstance(value,exp.Cast):value=value.this
        return D(1) if isinstance(value,exp.Column) and value.name==c['amount_column'] else None
    if c['operation']=='count' and isinstance(node,exp.Count):return D(1) if isinstance(node.this,exp.Star) else None
    if isinstance(node,(exp.Mul,exp.Div)):
        left=_coefficient(node.left,c);right=_literal(node.right,{})
        if left is not None and right is not None:return left*D(right) if isinstance(node,exp.Mul) else left/D(right)
        if isinstance(node,exp.Mul):
            right=_coefficient(node.right,c);left=_literal(node.left,{})
            if right is not None and left is not None:return D(left)*right
    return None

def contract_verifier(contracts):
    def check(w,answer,events):
        verdicts=[]
        for c in contracts:
            oid=c.get('objective_id','unknown')
            try:
                if c.get('kind')=='financial_rowset':
                    from .financial_eligibility import validate_financial_rows
                    ids=[e.get('result',{}).get('query_id') for e in events if e.get('tool')=='validate_financial_rows' and e.get('arguments',{}).get('contract_id')==c.get('id')]
                    results=[validate_financial_rows(w,q,c) for q in ids if q in w.queries]
                    selected=next((r for r in reversed(results) if r['status']=='PASS'),results[-1] if results else {'status':'BLOCKED','reason':'Call validate_financial_rows with the explicit contract ID and current query'})
                    verdicts.append({'objective_id':oid,'status':selected['status'],'reason':selected.get('reason','Explicit rowset/currency contract; formula numerical correctness not evaluated'),'scope':'eligibility_and_currency_only','details':selected})
                    continue
                if c.get('operation') not in {'sum','count'} or not c.get('unit'):raise ValueError('Explicit sum/count and unit required')
                if c['operation']=='count' and c['unit']!='rows':raise ValueError('COUNT cannot prove a monetary measure')
                sources=[t for t,m in w.catalog.items() if Path(m.get('source_path','')).name==c['source_name'] and m.get('kind')=='raw']
                if len(sources)!=1:raise ValueError('Source binding ambiguous or missing')
                table=sources[0];frame=w.frames[table].copy()
                for field,value in c.get('equals',{}).items():frame=frame[frame[field].eq(value)]
                if c.get('date_column'):
                    if not c.get('start') or not c.get('end'):raise ValueError('Explicit closed-open date range required')
                    dates=pd.to_datetime(frame[c['date_column']],errors='raise')
                    if dates.isna().any():raise ValueError('Missing dates require an explicit reviewed exclusion/hold')
                    frame=frame[dates.ge(c['start'])&dates.lt(c['end'])]
                if c['operation']=='count':expected=D(len(frame))
                else:
                    field=c['amount_column']
                    if frame[field].isna().any():raise ValueError('Missing numeric inputs cannot become zero')
                    expected=sum((D(str(x)) for x in frame[field]),D(0))*D(str(c.get('multiplier',1)))
                passed=False;observed_evidence=False
                for qid,evidence in w.queries.items():
                    if set(evidence.get('dependencies',{}))!={table}:continue
                    result=w.query_result_frame(qid)
                    if len(result)!=1 or c['output_column'] not in result:continue
                    tree=sqlglot.parse_one(evidence['sql'],read='duckdb')
                    selected=next((v for v in tree.expressions if v.alias_or_name==c['output_column']),None)
                    if selected is None:continue
                    if _coefficient(selected,c)!=D(str(c.get('multiplier',1))):continue
                    if not _where_matches(tree,c,evidence.get('parameters',{})):continue
                    observed_evidence=True
                    if abs(D(str(result.iloc[0][c['output_column']]))-expected)<=D(str(c.get('tolerance','.005'))):passed=True
                verdicts.append({'objective_id':oid,'status':'PASS' if passed else 'FAIL','expected':str(expected),'unit':c['unit'],
                    'reason':'Independent Decimal/filter/source/aggregate contract matched' if passed else 'No matching current source-bound SQL/operation/filter/value evidence',
                    'scope':'Explicit single-table scalar contract; arbitrary prose or unconfigured financial semantics not proven'})
            except Exception as e:verdicts.append({'objective_id':oid,'status':'BLOCKED','reason':str(e)})
        return verdicts
    return check
