"""Normalize an equivalent named-argument YAML syntax; do not repair semantics."""
import yaml


def normalize_arg_mapping(markdown):
    if not isinstance(markdown,str):raise ValueError('SKILL.md must be text')
    parts=markdown.split('---',2)
    if len(parts)!=3 or parts[0].strip():raise ValueError('SKILL.md frontmatter required')
    front=yaml.safe_load(parts[1])
    if not isinstance(front,dict):raise ValueError('SKILL.md frontmatter must be a mapping')
    args=front.get('args')
    if isinstance(args,dict):return markdown,False
    if not isinstance(args,list) or not args:raise ValueError('Missing typed SKILL.md args mapping')
    normalized={}
    for entry in args:
        if not isinstance(entry,dict):raise ValueError('Argument entries must be mappings')
        name=entry.get('name');kind=entry.get('type')
        if not isinstance(name,str) or not name.strip() or name!=name.strip():raise ValueError('Argument needs an unambiguous name')
        if name in normalized:raise ValueError('Duplicate argument name')
        if not isinstance(kind,str) or not kind.strip():raise ValueError('Argument needs a declared type')
        normalized[name]={k:v for k,v in entry.items() if k!='name'}
    front['args']=normalized
    return '---\n'+yaml.safe_dump(front,sort_keys=False)+'---'+parts[2],True
