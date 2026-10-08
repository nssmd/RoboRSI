"""Attach operator-owned episode identity to skill proposals, never guessed context."""
import json
from pathlib import Path
from roborsi.embodied.paths import home

def attach_origin(fields, source_workdir):
    result={k:v for k,v in fields.items() if k not in {'task','task_key','source_task','source_workspace','source_run_id','source_trace_dir'}}
    if source_workdir is None:
        return result
    root=(home()/'workspaces').resolve()
    candidate=Path(source_workdir).resolve()
    if not candidate.is_relative_to(root):
        return result
    for workspace in [candidate,*candidate.parents]:
        if workspace==root:break
        identity=workspace/'episode_identity.json'
        if not identity.is_file():continue
        record=json.loads(identity.read_text())
        task=record.get('task_key')
        if not isinstance(task,str) or not task:break
        result.update(task=task,source_task=task,source_workspace=str(workspace),source_trace_dir=str(candidate))
        break
    return result
