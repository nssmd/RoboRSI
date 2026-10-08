"""Verified, task-scoped evolving episode evidence for policy consolidation."""
from pathlib import Path
import json,os,re,sqlite3

def records(task_key, state_home=None, campaigns=None):
    if not isinstance(task_key,str) or not re.fullmatch(r'libero_(spatial|object|goal)/[0-9]+',task_key):return []
    if state_home is None:
        from roborsi.embodied.paths import home
        state_home=home()
    state_home=Path(state_home)
    campaigns=Path(campaigns or os.environ.get('ROBORSI_COMPOUND_SOURCE_CAMPAIGNS',str(state_home.parent/'campaigns')))
    db=state_home/'trace.db'
    if not db.exists() or not campaigns.exists():return []
    with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=10) as conn:
        evidence={}
        for r in conn.execute("SELECT id,status,episode_summary_json FROM runs WHERE task='libero_pick_place' AND run_mode='evolve'"):
            try:summary=json.loads(r[2] or '{}')
            except (ValueError,TypeError):continue
            if isinstance(summary,dict):evidence[r[0]]=(r[1],summary)
    by_seed={}
    for path in campaigns.glob('*/episodes.jsonl'):
        if (path.parent/'adjudication_exclusion.json').exists():continue
        for line in path.read_text().splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if row.get('task_key')!=task_key or row.get('sim_task')!=task_key or row.get('run_mode')!='evolve':continue
            if row.get('status')!='terminal' or row.get('verdict') not in ['success','failure']:continue
            if type(row.get('seed')) is not int:continue
            run_id=row.get('run_id');workspace=Path(row.get('workspace') or '/nonexistent').resolve()
            if run_id not in evidence or not workspace.is_relative_to((state_home/'workspaces').resolve()):continue
            identity=workspace/'episode_identity.json'
            if not identity.exists():continue
            ident=json.loads(identity.read_text())
            if ident.get('task_key')!=task_key or ident.get('seed')!=row.get('seed'):continue
            status,summary=evidence[run_id]
            item={'run_id':run_id,'task_key':task_key,'seed':row['seed'],'workspace':str(workspace),
                  'verdict':row['verdict'],'verified_success':row['verdict']=='success' and status=='success' and summary.get('predicate_check') is True}
            by_seed.setdefault(row['seed'],[]).append(item)
    # Duplicate identities are never extra successes and require separate audit.
    return [items[0] for _,items in sorted(by_seed.items()) if len(items)==1]

def stable(task_key, **kwargs):
    rows=records(task_key,**kwargs);successes=sum(r['verified_success'] for r in rows)
    return successes>=3 and bool(rows) and successes/len(rows)>=.5
