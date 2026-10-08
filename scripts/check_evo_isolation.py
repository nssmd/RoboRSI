from pathlib import Path
import os,json,tempfile
from types import SimpleNamespace
from unittest.mock import patch
from roborsi.runtime_mode import use_run_mode,evolution_enabled
from roborsi.embodied.paths import home
from roborsi.agents import task_wiki,workspace,reviewer
from roborsi.agents import gt_firewall
R=Path(__file__).resolve().parents[1]
assert str(home()).endswith('isolation-check')
assert task_wiki.WIKI_REVIEW_ROOT.parent==home()
assert workspace._ROOT.parent==home()
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp)/'wiki.md';p.write_text('# Task-local observations\n')
 shared=home()/'cross_task_approved.json';shared.parent.mkdir(parents=True,exist_ok=True)
 shared.write_text(json.dumps([{'status':'approved','source_task':'task_a','lesson':'Refresh the camera after a base or arm motion before reusing image coordinates.'},{'status':'pending','source_task':'task_b','lesson':'UNAPPROVED_MARKER'}]))
 with patch.object(task_wiki,'wiki_path',return_value=p),patch.object(gt_firewall,'predicate_source',side_effect=AssertionError('Must not inspect task source')):
  text=task_wiki.read_wiki('different_task')
  assert 'Refresh the camera' in text and 'UNAPPROVED_MARKER' not in text
 with use_run_mode('eval'):
  assert not evolution_enabled()
  try:task_wiki.resolve_wiki_hypothesis(Path(tmp)/'none',approve=True)
  except Exception as e:assert type(e).__name__=='EvolutionDisabledError'
  else:raise AssertionError('eval mutation allowed')
 with use_run_mode('evolve'):assert evolution_enabled()
s=(R/'roborsi/channels/core/agent.py').read_text();a=s.index('            episode_history.append(');b=s.index('            print(f"[3role] 🔁',a);assert 'sim_predicate' not in s[a:b];assert "eng_result.get('outcome')" not in s[a:b]
print('PASS isolated stores, cross-task approved-only read, no predicate-source access, eval guard and replan feedback')

from roborsi.channels.core.agent import _enqueue_proposal
with use_run_mode('evolve'):
    queued=json.loads(_enqueue_proposal(kind='new',name='isolation_probe',category='base/robotwin',new_code=''))
assert Path(queued['queue']).parent==home()/'skill_review'
print('PASS actual Engineer proposal queue isolation')
