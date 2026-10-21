#!/usr/bin/env python3
import fcntl,subprocess,time,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parent
state=Path.home()/'.local/share/agentdesk';state.mkdir(parents=True,exist_ok=True,mode=0o700)
with open(state/'launch.lock','w') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 def address():
  try:
   url=(state/'address').read_text().strip()
   if not url.startswith('http://127.0.0.1:'):return None
   with urllib.request.urlopen(url,timeout=1) as r:
    return url if b'<title>AgentDesk</title>' in r.read() else None
  except Exception:return None
 url=address()
 if not url:
  with open(state/'server.log','a') as log: subprocess.Popen(['/usr/bin/python3',str(root/'app.py'),'--no-window'],stdout=log,stderr=log,start_new_session=True)
  for _ in range(50):
   time.sleep(.1);url=address()
   if url:break
 if not url:raise SystemExit('AgentDesk: server did not start')
 subprocess.Popen(['google-chrome','--app='+url,'--class=AgentDesk','--window-size=1180,840'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
