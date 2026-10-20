"""Public conditional-model proposal contract; no case-specific formulas/answers."""
FINANCIAL_SPEC={
    'type':'object','additionalProperties':False,
    'required':['version','rule_ref','source_key','parameters','calculations','totals'],
    'properties':{
        'version':{'type':'integer','enum':[1]},
        'rule_ref':{'type':'string','minLength':1,'description':'Reference the supplied governing text. This is a model proposal, not approval.'},
        'source_key':{'type':'string','minLength':1,'description':'Unique non-null column from the actual input query.'},
        'parameters':{'type':'object','maxProperties':32,'additionalProperties':{'type':'object','required':['value'],
            'properties':{'value':{'type':['string','number']},'description':{'type':'string'}}}},
        'calculations':{'type':'array','minItems':1,'maxItems':50,'items':{'type':'object','required':['id','expression'],
            'properties':{'id':{'type':'string'},'expression':{'type':['object','number','string'],
                'description':'Calculation IDs must not collide with input fields or earlier calculations. Bare strings are numeric literals only, never references. Correct example: {"mul":[{"field":"net_amount"},{"param":"rate"}]}. Use {"id":"calculated_amount","expression":{"field":"net_amount"}} rather than id=net_amount. One-key expression: {field:column}, {param:parameter}, {add:[a,b]}, {sub:[a,b]}, {mul:[a,b]}, {div:[a,b]}, {min:[a,b]}, {max:[a,b]}, {round:[value,2]}; numeric constants allowed. Only prior calculation IDs may be referenced.'}}}},
        'totals':{'type':'array','minItems':1,'maxItems':50,'items':{'type':'string'}},
        'assumptions':{'type':'array','items':{'type':'object','required':['description','status'],
            'properties':{'description':{'type':'string'},'status':{'type':'string','enum':['governing_rule','explicit_user_choice','unresolved']}}}},
    }
}

def conditional_spec(spec):
    if not isinstance(spec,dict):raise ValueError('financial_spec must be an object')
    missing=[k for k in FINANCIAL_SPEC['required'] if k not in spec]
    if missing:raise ValueError('Missing required financial_spec fields: '+', '.join(missing))
    if type(spec['version']) is not int or spec['version']!=1:raise ValueError('financial_spec.version must be integer 1')
    if spec.get('approved_result') or spec.get('coverage_complete'):raise ValueError('A model cannot approve business rules, coverage or payroll')
    if len(spec.get('calculations',[]))>50 or len(spec.get('parameters',{}))>32:raise ValueError('Financial specification size limit')
    return {**spec,'status':'MODEL_PROPOSED_CONDITIONAL_NOT_APPROVED'}
