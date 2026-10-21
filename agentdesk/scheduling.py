"""Admission policy: ten-minute scans, one heavy job or two independent light jobs.

This module changes workload admission only. ROUTING.md remains the model policy.
"""
import hashlib, os, re
from pathlib import Path
POLL_SECONDS=600
MAX_PARALLEL=2

# Visible home for results of tasks without a card folder. They used to land in the hidden
# ~/.local/share/agentdesk/workspaces/<hash>, so Антон could not find what an agent made.
from settings import RESULTS

def result_folder(root,task,ident):
 from datetime import date
 name=re.sub(r'[^\w\- ]+','',task.get('title') or 'задача').strip()[:50].strip() or 'задача'
 return str(Path(root)/(date.today().isoformat()+' '+name+' '+hashlib.sha256(ident.encode()).hexdigest()[:6]))

def workload(task,route,ident='local',state=None,results=None):
 scores=route.get('scores') or {}
 simple=(route.get('effort') in ('low','medium') and scores.get('unclear',2)<=1 and scores.get('risk',2)==0 and scores.get('size',2)<=1 and route.get('claude_signal')!='C3')
 if task.get('status')=='test':simple=False
 text=task.get('title','')+'\n'+task.get('description','')
 # Unspecified access to paths or shared desktops/services cannot be isolated automatically.
 shared=bool(re.search(r'(?i)(?:/(?:home|media|etc|usr|var|tmp)/|\b(?:driftwm|wayland|systemd|firebase|gmail|browser|chrome)\b|браузер|почт|календар|общ.{0,10}(?:файл|папк|таблиц)|сервер)',text))
 resources=[str(x) for x in (task.get('resources') or [])]
 resources.extend(t.split(':',1)[1].strip() for t in task.get('tags') or [] if isinstance(t,str) and t.startswith('resource:'))
 workspace=task.get('workspace')
 isolated=False
 if workspace:workspace=str(Path(workspace).expanduser().resolve())
 elif simple and not shared and not resources and state is not None:
  workspace=result_folder(results,task,ident) if results else str(Path(state)/'workspaces'/hashlib.sha256(ident.encode()).hexdigest()[:24]);isolated=True
 else:workspace=str(Path.home())
 if shared or workspace==str(Path.home()):simple=False
 return {'parallel':simple,'workspace':workspace,'resources':sorted(set(resources)),'isolated':isolated}

def overlap(a,b):
 if set(a['resources']) & set(b['resources']):return True
 p,q=Path(a['workspace']),Path(b['workspace'])
 return p==q or p in q.parents or q in p.parents

def admit(candidate,active,capacity=MAX_PARALLEL):
 if len(active)>=capacity:return False,'Достигнут предел параллельных задач'
 if active and (not candidate['parallel'] or any(not x['parallel'] for x in active)):return False,'Сложная задача выполняется отдельно'
 if any(overlap(candidate,x) for x in active):return False,'Общие файлы или ресурсы заняты'
 return True,'Независимая простая задача' if active else 'Свободный исполнитель'

def machine_capacity():
 """Memory and CPU may reduce admission; they never stop an already running job."""
 try:
  fields={line.split(':',1)[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
  # One CLI run needs ~0.5 GiB. The old 2 GiB floor stopped the queue on a busy 16 GiB laptop.
  if fields.get('MemAvailable',0)<1*1024**3:return 0
  if fields.get('MemAvailable',0)<3*1024**3:return 1
 except (OSError,ValueError):return 1
 try:
  if os.getloadavg()[0]>max(1,os.cpu_count() or 1)*.85:return 1
 except OSError:return 1
 return MAX_PARALLEL
