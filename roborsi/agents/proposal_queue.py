"""Python3.10-safe proposal intake; never imports channel/provider or applies code."""
import json,re,time,uuid
from roborsi.embodied.paths import home
from roborsi.runtime_mode import require_evolution

def enqueue(kind, namespace='robotwin', **fields):
    require_evolution('queueing skill proposal')
    name=str(fields.get('name',''))
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,63}',name):
        return json.dumps({'ok':False,'reason':'name must be lower_snake_case'})
    if kind not in ('new','update') or namespace not in ('robotwin','libero'):
        return json.dumps({'ok':False,'reason':'unsupported proposal kind or namespace'})
    pid=f'{time.time_ns()}-{kind}-{name}-{uuid.uuid4().hex[:6]}'
    category=fields.get('category') or f'base/{namespace}'
    # Tool schemas use a descriptive category; publish layout is fixed by backend.
    if '/' not in category:category=f'base/{namespace}'
    if category!=f'base/{namespace}':
        return json.dumps({'ok':False,'reason':'proposal must target the current public base namespace'})
    record={**fields,'id':pid,'kind':kind,'name':name,'category':category,'status':'pending',
            'submitted_at':time.time(),'submitted_by':'Engineer',
            'new_code':fields.get('new_code') or fields.get('code','')}
    root=home()/'skill_review';root.mkdir(parents=True,exist_ok=True)
    p=root/(pid+'.json')
    with p.open('x') as f:json.dump(record,f,indent=2)
    return json.dumps({'proposal_id':pid,'queue':str(p),'note':'Queued for independent Manager review and simulator validation; not applied.'})
