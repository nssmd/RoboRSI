"""Separate task-specific memories from Manager-approved transferable skills."""
import os,re
from roborsi.embodied.paths import home

def key(task, explicit=None):
    if task != "libero_pick_place":return task
    value=explicit or os.environ.get("ROBORSI_CURRENT_SIM_TASK")
    if not value or not re.fullmatch(r"libero_(spatial|object|goal)(?:_(lan|object|swap|task))?/[0-9]+",value):
        return None
    return value

def directory(task, explicit=None):
    identity=key(task,explicit)
    if task != "libero_pick_place" or identity is None:return None
    p=home()/"task_memories"/identity
    p.mkdir(parents=True,exist_ok=True)
    return p
