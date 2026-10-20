"""Structural diagnostics for the existing DSL. Never rewrite a proposal."""
from decimal import Decimal,InvalidOperation
import re
IDENT=re.compile(r'^[a-z][a-z0-9_]{0,59}$')
OPS={'add','sub','mul','div','min','max','round'}
REFERENCE_FORMS=[{'field':'input_or_prior_calculation'},{'param':'declared_parameter'}]
class FinancialPlanError(ValueError):
    def __init__(self,errors):
        self.errors=errors
        super().__init__('; '.join(f"{e['json_path']}: {e['error_code']} {e.get('message','')}" for e in errors[:4]))
    def response(self):
        blocked=any(e.get('semantic_validation',{}).get('status')=='BLOCKED' for e in self.errors)
        return {'status':'BLOCKED' if blocked else 'error','error_category':'FINANCIAL_PLAN_VALIDATION','error':str(self),'errors':self.errors,
                'error_code':self.errors[0]['error_code'],'json_path':self.errors[0]['json_path'],
                'formula_rewritten':False}

def validate_financial_spec(spec,frame):
    errors=[]
    def error(code,path,message,expected,received,**extra):
        if len(errors)<30:errors.append({'error_code':code,'json_path':path,'message':message,'expected_type':expected,'received_type':type(received).__name__,**extra})
    if not isinstance(spec,dict):raise FinancialPlanError([{'error_code':'OBJECT_REQUIRED','json_path':'$','message':'Financial spec must be an object','expected_type':'object','received_type':type(spec).__name__}])
    for key in ('version','rule_ref','source_key','parameters','calculations','totals'):
        if key not in spec:error('REQUIRED_FIELD',f'$.{key}','Required financial specification field','present',None)
    if errors:raise FinancialPlanError(errors)
    if type(spec['version']) is not int or spec['version']!=1:error('VERSION','$.version','Only DSL version 1 is supported','integer 1',spec['version'])
    if not isinstance(spec['rule_ref'],str) or not spec['rule_ref'].strip():error('RULE_REFERENCE','$.rule_ref','Reference supplied governing text; do not invent rules','nonempty string',spec['rule_ref'])
    if not isinstance(spec['source_key'],str) or spec['source_key'] not in list(frame.columns):error('SOURCE_KEY','$.source_key','Use an actual input column','input field',spec['source_key'])
    params=spec['parameters'];calcs=spec['calculations'];totals=spec['totals']
    if not isinstance(params,dict) or len(params)>32:error('PARAMETERS','$.parameters','At most 32 declared numeric parameters','object',params);params={}
    if not isinstance(calcs,list) or not 1<=len(calcs)<=50:error('CALCULATIONS','$.calculations','Supply 1..50 ordered calculations','array',calcs);calcs=[]
    if not isinstance(totals,list) or not totals:error('TOTALS','$.totals','Supply explicit total identifiers','nonempty array',totals);totals=[]
    def numeric(value,path,allow_reference=True):
        try:
            if isinstance(value,bool) or value is None:raise ValueError()
            n=Decimal(str(value))
            if not n.is_finite():raise ValueError()
        except (InvalidOperation,ValueError,TypeError):
            if isinstance(value,str) and allow_reference:
                form={'param':value} if value in params else {'field':value}
                error('BARE_REFERENCE',path,'Non-numeric strings are not references. Use explicit field/param objects.','finite numeric literal or one-key expression',value,allowed_reference_forms=REFERENCE_FORMS,corrected_syntax_example=form)
            else:error('NUMERIC_LITERAL',path,'Finite numeric constant required','finite number',value)
    for name,p in params.items():
        path=f'$.parameters.{name}'
        if not IDENT.fullmatch(str(name)):error('IDENTIFIER',path,'Stable lowercase identifier required','identifier',name)
        if not isinstance(p,dict) or 'value' not in p:error('PARAMETER_VALUE',path+'.value','Numeric parameter value required','number',p)
        else:numeric(p['value'],path+'.value',allow_reference=False)
    names={str(c):'input_field' for c in frame.columns};future={c.get('id') for c in calcs if isinstance(c,dict) and isinstance(c.get('id'),str)}
    def expr(node,path,depth=0):
        if depth>25:error('EXPRESSION_DEPTH',path,'Expression exceeds depth 25','bounded expression',node);return
        if not isinstance(node,dict):numeric(node,path);return
        if len(node)!=1:error('EXPRESSION_SHAPE',path,'Exactly one operator/reference per object','one-key object',node);return
        op,value=next(iter(node.items()))
        if op in {'field','param'}:
            source=names if op=='field' else params
            if not isinstance(value,str) or value not in source:
                code='FORWARD_REFERENCE' if op=='field' and isinstance(value,str) and value in future else 'UNKNOWN_REFERENCE'
                error(code,path+'.'+op,'Fields must be input/prior calculations; parameters must be declared','existing reference',value,namespace='input_or_prior_calculation' if op=='field' else 'parameter',allowed_reference_forms=REFERENCE_FORMS)
            elif op=='field' and value in frame.columns:
                try:
                    if any(isinstance(v,bool) or v is None or not Decimal(str(v)).is_finite() for v in frame[value]):raise ValueError()
                except (InvalidOperation,ValueError,TypeError):error('NON_NUMERIC_SOURCE_FIELD',path+'.field','Numeric DSL input is missing or non-numeric; investigate source/eligibility evidence, never coerce to zero','finite numeric source column',value,conflicting_identifier=value,namespace='input_field')
            return
        if op not in OPS:error('UNSUPPORTED_FORMULA',path,'Free-form formulas and unknown operators are unsupported','restricted DSL operator',op,allowed_operators=sorted(OPS));return
        if not isinstance(value,list) or (op in {'sub','div','round'} and len(value)!=2) or (op in {'add','mul','min','max'} and len(value)<2):error('OPERATOR_ARITY',path+'.'+op,'Wrong operand count','two operands' if op in {'sub','div','round'} else 'at least two operands',value);return
        for i,v in enumerate(value):expr(v,f'{path}.{op}[{i}]',depth+1)
        if op=='round' and (type(value[1]) is not int or not 0<=value[1]<=8):error('ROUND_DIGITS',path+'.round[1]','Rounding digits must be integer 0..8','integer 0..8',value[1])
    for i,c in enumerate(calcs):
        path=f'$.calculations[{i}]'
        if not isinstance(c,dict):error('CALCULATION_SHAPE',path,'id and expression required','object',c);continue
        name=c.get('id')
        if not isinstance(name,str) or not IDENT.fullmatch(name):error('IDENTIFIER',path+'.id','Stable lowercase identifier required','identifier',name)
        elif name in names:
            base='calc_'+name[:40];example=base;suffix=2
            while example in names:
                example=base+'_'+str(suffix);suffix+=1
            error('DUPLICATE_CALCULATION_ID',path+'.id','Calculation IDs must differ from every input field and prior calculation','unique calculation identifier',name,conflicting_identifier=name,namespace=names[name],uniqueness_rule='unique across input fields and ordered calculation IDs',corrected_syntax_example={'id':example,'expression':c.get('expression')})
        if 'expression' not in c:error('REQUIRED_FIELD',path+'.expression','Calculation expression required','expression',None)
        else:expr(c['expression'],path+'.expression')
        if isinstance(name,str):names.setdefault(name,'prior_calculation')
    for i,t in enumerate(totals):
        if not isinstance(t,str) or t not in names:error('UNKNOWN_TOTAL',f'$.totals[{i}]','Total must reference an existing numeric field/calculation','existing identifier',t)
    errors.sort(key=lambda e:e['error_code']=='NON_NUMERIC_SOURCE_FIELD')
    if errors:raise FinancialPlanError(errors)
    return {'status':'validated','scope':'DSL syntax, namespaces and dependency order only; not business approval'}
