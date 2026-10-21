"""Durable, action-scoped human approvals. SQLite is authoritative, not card text."""
import contextlib,hashlib,json,secrets,sqlite3,time
from pathlib import Path
from urllib.parse import urlsplit
from routing import fingerprint
STATE=Path.home()/'.local/share/agentdesk'
ACTION_SCHEMA={'type':['object','null'],'properties':{'kind':{'enum':['send_message']},'description':{'type':'string'},'target':{'type':'string'},'text':{'type':'string'}},'required':['kind','description','target','text'],'additionalProperties':False}

def validate_action(action):
 if not isinstance(action,dict) or action.get('kind') not in ('send_message','test_note'):raise ValueError('Неподдерживаемое действие')
 for k in ('target','text','description'):
  if not isinstance(action.get(k),str) or not action[k].strip():raise ValueError('Не указан '+k)
 if len(action['text'])>2400 or len(action['target'])>400 or len(action['description'])>500:raise ValueError('Описание действия слишком длинное для однозначного согласования')
 if action['kind']=='send_message':
  u=urlsplit(action['target'])
  if u.scheme not in ('https','mailto','telegram') or u.username or u.password:raise ValueError('Некорректный адрес получателя')
 return {k:action[k] for k in ('kind','description','target','text')}

class ApprovalDB:
 def __init__(self,path=None):
  self.path=Path(path or STATE/'approvals.sqlite3');self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  with self.connect() as c:
   c.execute('CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY,task_id TEXT,revision TEXT,action TEXT,status TEXT,message_id INTEGER,created REAL,expires REAL,decision_actor TEXT,synced INTEGER DEFAULT 0)')
   c.execute('CREATE UNIQUE INDEX IF NOT EXISTS approval_scope ON approvals(task_id,revision,action)')
 @contextlib.contextmanager
 def connect(self):
  c=sqlite3.connect(self.path,timeout=20);c.row_factory=sqlite3.Row
  try:
   with c:yield c
  finally:c.close()
 def get(self,ident):
  with self.connect() as c:
   r=c.execute('SELECT * FROM approvals WHERE id=?',(ident,)).fetchone();return dict(r) if r else None
 def request(self,task_id,task,action,direct_actor=None):
  action=validate_action(action);revision=fingerprint(task);serialized=json.dumps(action,ensure_ascii=False,sort_keys=True)
  with self.connect() as c:
   c.execute('INSERT OR IGNORE INTO approvals(id,task_id,revision,action,status,created,expires,decision_actor) VALUES(?,?,?,?,?,?,?,?)',(secrets.token_hex(12),task_id,revision,serialized,'authorized' if direct_actor else 'pending',time.time(),time.time()+7*86400,str(direct_actor) if direct_actor else None))
   row=dict(c.execute('SELECT * FROM approvals WHERE task_id=? AND revision=? AND action=?',(task_id,revision,serialized)).fetchone())
  return row
 def bind_message(self,ident,mid):
  with self.connect() as c:c.execute('UPDATE approvals SET message_id=? WHERE id=?',(mid,ident))
 def decide(self,ident,actor,chat_id,mid,allowed_actor,allow):
  if str(actor)!=str(allowed_actor) or str(chat_id)!=str(allowed_actor):return False
  with self.connect() as c:
   changed=c.execute("UPDATE approvals SET status=?,decision_actor=?,synced=0 WHERE id=? AND status='pending' AND message_id=? AND expires>?",('authorized' if allow else 'denied',str(actor),ident,mid,time.time())).rowcount
   return bool(changed)
 def pending_send(self):
  with self.connect() as c:return [dict(x) for x in c.execute("SELECT * FROM approvals WHERE status='pending' AND message_id IS NULL AND expires>?",(time.time(),))]
 def decisions(self):
  with self.connect() as c:return [dict(x) for x in c.execute("SELECT * FROM approvals WHERE status IN ('authorized','denied') AND synced=0")]
 def mark(self,ident,status=None,synced=None):
  with self.connect() as c:
   if status is not None:c.execute('UPDATE approvals SET status=? WHERE id=?',(status,ident))
   if synced is not None:c.execute('UPDATE approvals SET synced=? WHERE id=?',(int(synced),ident))
 def grant(self,task_id,task):
  with self.connect() as c:
   row=c.execute("SELECT * FROM approvals WHERE task_id=? AND revision=? AND status='authorized' AND expires>? ORDER BY created DESC LIMIT 1",(task_id,fingerprint(task),time.time())).fetchone()
   if not row:return None
   r=dict(row);return {'id':r['id'],'action':json.loads(r['action']),'source':'Антон: прямое разрешение' if r['message_id'] is None else 'Антон: кнопка Можно в Telegram'}
 def reserve(self,ident):
  with self.connect() as c:return bool(c.execute("UPDATE approvals SET status='reserved' WHERE id=? AND status='authorized'",(ident,)).rowcount)
 def finish(self,ident,action_status):
  self.mark(ident,'authorized' if action_status=='not_sent' else 'used' if action_status=='sent' else 'uncertain')

def public_request(row):return {'id':row['id'],'status':row['status'],'action':json.loads(row['action']),'messageId':row['message_id']}

def permission_question(question):
 import re
 q=(question or '').lower()
 return bool(re.search(r'подтверд|разреш|соглас',q) and re.search(r'отправ|сообщен',q) and not re.search(r'авториз|вход|браузер|парол|токен|доступ',q))
