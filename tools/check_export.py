"""Checks the shareable source tree for accidental local data and credential markers."""
from pathlib import Path
import re, subprocess
root=Path(__file__).resolve().parents[1]
paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,text=True).splitlines()
patterns=[r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'AIza[0-9A-Za-z_-]{30,}',r'gh[pousr]_[A-Za-z0-9]{20,}',r'\b\d{8,}:[A-Za-z0-9_-]{30,}',r'/home/antonk',r'/media/antonk',r'agent-dashboard-antonk',r'"private_key"\s*:']
errors=[]
for name in paths:
 p=root/name
 if p.suffix in {'.png','.jpg','.sqlite3','.jsonl','.log'}:errors.append(name+': private artifact type')
 if p.name in {'serviceAccountKey.json','kiwi_config.json','firebase-config.js'}:errors.append(name+': local config')
 if name=='tools/check_export.py':continue
 for pattern in patterns:
  if re.search(pattern,p.read_text(errors='replace')):errors.append(name+': credential or original-machine marker')
if errors:raise SystemExit('\n'.join(errors))
print(f'PASS: {len(paths)} source files; no forbidden artifacts or credential/original-machine markers')
