"""Explicit, reproducible processing-error counterfactuals; no payroll assertions.

Isolated sensitivities are NOT additive. A separately identified ordered waterfall
reconciles their combined scenario and exposes interactions. Choices are named;
none changes governing treatment or silently substitutes an employee owner.
"""
import copy
from decimal import Decimal
import json
import pandas as pd
from . import benchmark as engine
from .revenue import build_account_master
from local_analyst.workspace import dump

ORDER=('join_multiplication','reversed_stage','denied_credit','leaver_recognition')
DEFINITIONS={
 'join_multiplication':'Naive direct account-opportunity join repeats every shift once per opportunity (minimum one for left join).',
 'reversed_stage':'Naively treat every populated closed_won_date as Closed Won, then rebuild account go-live and closing ownership.',
 'denied_credit':'Naively treat every denied billing ticket as approved; preserve billing month and existing-book eligibility.',
 'leaver_recognition':'Naively ignore employee End Date for scope reps; preserve start dates and full manager chain.'}


def variant(data, switches):
    d={**data,'sources':{k:v.copy(deep=True) for k,v in data['sources'].items()},
       'base':dict(data['base']),'people':data['people'].copy(deep=True)}
    if 'reversed_stage' in switches:
        opp=d['sources']['opportunities'];mask=opp.closed_won_date.notna()
        opp.loc[mask,'opportunity_stage']='Closed Won'
        d['base']['accounts']=build_account_master(d['sources'],[])[0]
    if 'join_multiplication' in switches:
        accounts=d['base']['accounts'];counts=accounts.set_index('app_account_id').opportunity_count.to_dict()
        shifts=data['base']['shifts'];repeats=shifts.app_account_id.map(counts).fillna(1).clip(lower=1).astype(int)
        d['base']['shifts']=shifts.loc[shifts.index.repeat(repeats)].reset_index(drop=True)
    if 'denied_credit' in switches:
        b=d['sources']['billing'];b.loc[b.ticket_status.eq('denied'),'ticket_status']='approved'
    if 'leaver_recognition' in switches:
        d['people']['End Date']=pd.NaT
    return d


def attribution(data, baseline, config):
    rows=[];details=[];waterfall=[];previous=baseline
    for index,topic in enumerate(ORDER):
        alt=engine.calculate(variant(data,[topic]),config)
        diff=engine.compare(baseline,alt);affected=diff.loc[diff.delta.ne(0)]
        net=alt['total_usd']-baseline['total_usd']
        gross=sum((abs(v) for v in affected.delta),Decimal(0))
        rows.append({'topic':topic,'counterfactual_definition':DEFINITIONS[topic],
            'net_incremental_payout_usd':net,'gross_rep_payout_change_usd':gross,
            'affected_reps':dump(affected.hr_id.tolist()),'affected_rep_count':len(affected),
            'impact_status':'quantified_conditional_counterfactual','payment_error_proven':False,
            'interactions':'Isolated differences are non-additive. See separately ordered waterfall.',
            'treatment':'Reject the named naive processing rule; retain original plan treatment.'})
        for record in affected.to_dict('records'):details.append({'topic':topic,**record})
        combined=engine.calculate(variant(data,ORDER[:index+1]),config)
        waterfall.append({'order':index+1,'topic':topic,'combined_total_usd':combined['total_usd'],
            'increment_from_previous_usd':combined['total_usd']-previous['total_usd'],
            'isolated_delta_usd':net,
            'interaction_adjustment_usd':combined['total_usd']-previous['total_usd']-net})
        previous=combined
    if sum((r['increment_from_previous_usd'] for r in waterfall),Decimal(0)) != previous['total_usd']-baseline['total_usd']:
        raise ValueError('Exception interaction waterfall does not reconcile')
    return pd.DataFrame(rows),pd.DataFrame(details),pd.DataFrame(waterfall)
