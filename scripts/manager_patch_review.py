"""Exact Manager-authored patches; unchanged review and simulator publication gates."""
import ast,hashlib,json

def patch_base(request):
 code=request.get('new_code');name=request.get('name');parent=request.get('parent_proposal')
 if not isinstance(code,str) or not code or not name or not parent:return None
 return {'base_proposal_id':(str(parent)[:-5] if str(parent).endswith('.json') else str(parent)),'base_sha256':hashlib.sha256(code.encode()).hexdigest(),'name':name,'target_path':f'roborsi/embodied/skills/base/{name}/libero/policy.py'}

def patch_prompt(request):
 base=patch_base(request)
 if base is None:return ''
 return '\nEXACT PATCH AUTHORING OPTION: Prefer a focused patch to rewriting the whole current candidate. You may return code_proposal with kind="patch", category="base/libero", the exact name/base_proposal_id/base_sha256/target_path below, patches=[{"old_string":"exact unique existing text","new_string":"your changed text"},...], and rationale. skill_md may be omitted to preserve the exact current harness, or supplied in full when documentation needs correction. The executor only materializes your literal edits against the named unpromoted candidate, then queues the FULL result for a separate actual Manager review and the unchanged simulator gate. No patch is self-approved or published by materialization. Do not change declared test arguments or thresholds. Base: '+json.dumps(base)+'\n'

def materialize(candidate,request):
 if candidate.get('kind')!='patch':return candidate
 base=patch_base(request)
 if base is None:raise ValueError('No unambiguous candidate base for patch')
 for key,value in base.items():
  if candidate.get(key)!=value:raise ValueError('Patch base mismatch: '+key)
 if candidate.get('category')!='base/libero':raise ValueError('Patch category mismatch')
 patches=candidate.get('patches')
 if not isinstance(patches,list) or not patches:raise ValueError('Patch needs literal hunks')
 code=request['new_code']
 for patch in patches:
  old=patch.get('old_string');new=patch.get('new_string')
  if not isinstance(old,str) or not old or not isinstance(new,str) or code.count(old)!=1:raise ValueError('Patch hunk missing or ambiguous')
  code=code.replace(old,new,1)
 if code==request['new_code']:raise ValueError('Unchanged candidate is not a repair')
 ast.parse(code)
 result={k:v for k,v in candidate.items() if k not in ['patches','target_path','base_proposal_id','base_sha256']}
 result.update(kind='update',new_code=code,skill_md=candidate.get('skill_md') or request.get('skill_md',''),original_manager_patch=candidate)
 return result

def compact_history(lines):
 compact=[]
 for line in lines:
  try:r=json.loads(line)
  except ValueError:
   compact.append(json.dumps({'unparsed_historical_record_sha256':hashlib.sha256(line.encode()).hexdigest()}));continue
  d=r.get('decision',{});decision=dict(d) if isinstance(d,dict) else {'decision':d}
  code=decision.pop('code_proposal',None)
  if isinstance(code,dict):decision['prior_candidate']={'name':code.get('name'),'kind':code.get('kind'),'code_sha256':hashlib.sha256(str(code.get('new_code','')).encode()).hexdigest(),'full_source_retained_in_raw_history':True}
  compact.append(json.dumps({**{k:r.get(k) for k in ['utc','proposal','source_task','model']},'decision':decision}))
 return compact
