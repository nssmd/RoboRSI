"""Original-LIBERO compound declarations and independent simulator gates."""
import re,yaml

def validate(q):
    if q.get('kind')!='compound_policy' or q.get('task')!='libero_pick_place':raise ValueError('Wrong compound kind/atomic scope')
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}',str(q.get('compound_name',''))):raise ValueError('Invalid compound name')
    task=q.get('task_key')
    if not isinstance(task,str) or not re.fullmatch(r'libero_(spatial|object|goal)/[0-9]',task):raise ValueError('Missing exact source simulation task')
    parts=str(q.get('skill_md','')).split('---',2)
    if len(parts)!=3:raise ValueError('Missing frontmatter')
    fm=yaml.safe_load(parts[1]);h=(fm.get('metadata') or {}).get('harness') or {}
    if fm.get('name')!=q['compound_name']:raise ValueError('Name mismatch')
    if h.get('skip_harness') or h.get('setup') or h.get('sim_task')!=task:raise ValueError('Full source task required; no setup or skipped gate')
    if h.get('seeds')!=[21,22]:raise ValueError('Declared development seeds must be21/22')
    if h.get('pass_criteria')!={'kind':'simulator_task_success','min_seeds_passing':2}:raise ValueError('Both independent simulator successes required')
    args=h.get('args');declared=fm.get('args')
    if not isinstance(args,list) or len(args)!=1 or not isinstance(args[0],dict):raise ValueError('One fixed argument row for both seeds required')
    if not isinstance(declared,dict) or not declared or any(not isinstance(v,dict) or not isinstance(v.get('type'),str) for v in declared.values()):raise ValueError('Typed argument mapping required')
    if not set(args[0]).issubset(declared):raise ValueError('Undeclared argument')
    return fm,h

def passed(q,report):
    _,h=validate(q);rows=report.get('results',[])
    return (report.get('sim_task')==h['sim_task'] and report.get('verdict')=='PASS'
            and len(rows)==2 and {r.get('seed') for r in rows}=={21,22}
            and all(r.get('harness_simulator_success') is True and r.get('crashed') is False for r in rows))
