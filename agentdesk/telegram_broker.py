#!/usr/bin/env python3
"""One Telegram getUpdates owner, approvals + durable relay to the Claude channel."""
import fcntl,hashlib,hmac,json,logging,os,re,sqlite3,threading,time,urllib.parse,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from approvals import ApprovalDB,public_request
STATE=Path.home()/'.local/share/agentdesk'
from settings import BASE, DASHBOARD_URL
CONFIG=BASE/'config/kiwi_config.json'
PORT=18780
LOG=logging.getLogger('telegram-broker')

class API:
 def __init__(self):
  config=json.loads(CONFIG.read_text());self.token=config['bot_token'];self.actor=str(config['chat_id'])
 def raw(self,method,body=None,content_type='application/json',query=''):
  url='https://api.telegram.org/bot'+self.token+'/'+method+('?' + query if query else '')
  req=urllib.request.Request(url,data=body,headers={'Content-Type':content_type})
  with urllib.request.urlopen(req,timeout=40) as r:return r.read()
 def call(self,method,params=None):
  result=json.loads(self.raw(method,json.dumps(params or {}).encode()))
  if not result.get('ok'):raise RuntimeError('Telegram API rejected '+method)
  return result['result']

class Broker:
 def __init__(self,api,approvals=None,path=None,store=None):
  self.api=api;self.approvals=approvals or ApprovalDB();self.path=Path(path or STATE/'telegram-inbox.sqlite3');self.store=store;self.condition=threading.Condition()
  with self.connect() as c:
   c.execute('CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY,payload TEXT)');c.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT)')
 def connect(self):
  from contextlib import contextmanager
  @contextmanager
  def db():
   c=sqlite3.connect(self.path,timeout=20)
   try:
    with c:yield c
   finally:c.close()
  return db()
 def cursor(self):
  with self.connect() as c:
   r=c.execute("SELECT value FROM meta WHERE key='offset'").fetchone();return int(r[0]) if r else 0
 def consume(self,update):
  callback=update.get('callback_query');handled=False
  if callback and str(callback.get('data','')).startswith('ad:'):
   handled=True;self.callback(callback)
  with self.connect() as c:
   if not handled:c.execute('INSERT OR IGNORE INTO updates VALUES(?,?)',(update['update_id'],json.dumps(update)))
   c.execute("INSERT OR REPLACE INTO meta VALUES('offset',?)",(str(update['update_id']+1),))
  with self.condition:self.condition.notify_all()
 def callback(self,query):
  match=re.fullmatch(r'ad:(yes|no):([a-f0-9]{24})',query.get('data',''))
  if not match:return
  message=query.get('message') or {};actor=(query.get('from') or {}).get('id');chat=(message.get('chat') or {}).get('id');mid=message.get('message_id')
  changed=self.approvals.decide(match[2],actor,chat,mid,self.api.actor,match[1]=='yes')
  label=('Можно — решение записано' if match[1]=='yes' else 'Нельзя — действие запрещено') if changed else 'Кнопка уже обработана, устарела или недоступна'
  try:self.api.call('answerCallbackQuery',{'callback_query_id':query['id'],'text':label})
  except Exception:LOG.warning('Could not acknowledge callback')
  if changed:
   try:
    if message.get('text'):self.api.call('editMessageText',{'chat_id':chat,'message_id':mid,'text':message['text']+'\n\n'+('✅ Можно — Антон разрешил' if match[1]=='yes' else '❌ Нельзя — Антон запретил'),'reply_markup':{'inline_keyboard':[]}})
    else:self.api.call('editMessageReplyMarkup',{'chat_id':chat,'message_id':mid,'reply_markup':{'inline_keyboard':[]}})
   except Exception:LOG.warning('Could not clear answered buttons')
 def relay(self,offset=0,timeout=0):
  deadline=time.monotonic()+min(25,max(0,timeout))
  while True:
   with self.connect() as c:
    if offset>0:c.execute('DELETE FROM updates WHERE id<?',(offset,))
    rows=c.execute('SELECT payload FROM updates WHERE id>=? ORDER BY id LIMIT 100',(offset,)).fetchall()
   if rows or time.monotonic()>=deadline:return [json.loads(x[0]) for x in rows]
   with self.condition:self.condition.wait(min(1,deadline-time.monotonic()))
 def send_requests(self):
  for row in self.approvals.pending_send():
   with self.approvals.connect() as c:
    changed=c.execute("UPDATE approvals SET status='sending' WHERE id=? AND status='pending' AND message_id IS NULL",(row['id'],)).rowcount
   if not changed:continue
   action=json.loads(row['action']);link=DASHBOARD_URL+'/?task='+urllib.parse.quote(row['task_id'])+'#kanban'
   text='AgentDesk: можно?\n'+action['description']+'\nКуда: '+action['target']+'\nТекст:\n'+action['text']+'\n\nРазрешение на одно действие.\n'+link
   keyboard={'inline_keyboard':[[{'text':'✅ Можно','callback_data':'ad:yes:'+row['id']},{'text':'❌ Нельзя','callback_data':'ad:no:'+row['id']}]]}
   try:
    message=self.api.call('sendMessage',{'chat_id':self.api.actor,'text':text,'reply_markup':keyboard})
    self.approvals.bind_message(row['id'],message['message_id']);self.approvals.mark(row['id'],'pending')
    LOG.info('Approval sent id=%s message_id=%s',row['id'],message['message_id'])
   except Exception:
    # Delivery could have happened; never blindly duplicate a permission message.
    self.approvals.mark(row['id'],'delivery_uncertain');LOG.warning('Approval delivery uncertain id=%s',row['id'])
 def sync_decisions(self):
  if self.store is None:return
  from routing import fingerprint
  from datetime import datetime,timezone
  import uuid
  for row in self.approvals.decisions():
   ref=self.store.db.collection('tasks').document(row['task_id'])
   def tx(t):
    task=ref.get(transaction=t).to_dict() or {}
    if fingerprint(task)!=row['revision'] or (task.get('agentDesk') or {}).get('approvalRequest',{}).get('id')!=row['id']:return False
    if task.get('status') not in ('issue','todo'):return False
    patch={'agentDesk.approvalRequest':public_request(row),'updatedAt':datetime.now(timezone.utc)}
    if row['status']=='authorized':
     if json.loads(row['action'])['kind']=='test_note':patch.update({'status':'done','agentDesk.phase':'done','agentNotes':'Техническая проверка кнопки Можно завершена. Ничего клиентам не отправлялось.'})
     else:patch.update({'status':'todo','routeNonce':str(uuid.uuid4()),'agentDesk.phase':'queued','agentNotes':'Антон разрешил конкретное действие в Telegram. Дополнительных сообщений не отправлять.'})
    else:patch.update({'status':'issue','agentDesk.phase':'issue','agentNotes':'Антон нажал «Нельзя»: действие запрещено.'})
    t.update(ref,patch);return True
   updated=self.store.transaction(tx)
   self.approvals.mark(row['id'],synced=True)
   LOG.info('Decision id=%s status=%s applied=%s',row['id'],row['status'],updated)
 def poll(self):
  while True:
   try:
    for update in self.api.call('getUpdates',{'offset':self.cursor(),'timeout':25,'allowed_updates':['message','callback_query','edited_message','my_chat_member']}):self.consume(update)
   except Exception as e:LOG.warning('Telegram poll retry (%s)',type(e).__name__);time.sleep(5)
 def maintain(self):
  while True:
   try:self.send_requests();self.sync_decisions()
   except Exception as e:LOG.warning('Approval maintenance retry (%s)',type(e).__name__)
   time.sleep(2)

class Proxy(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):self.handle_api()
 def do_POST(self):self.handle_api()
 def handle_api(self):
  api=self.server.broker.api
  if self.path=='/health':return self.respond(b'{"ok":true}',200)
  parsed=urllib.parse.urlsplit(self.path);parts=parsed.path.strip('/').split('/')
  if len(parts)!=2 or not hmac.compare_digest(parts[0],'bot'+api.token):return self.respond(b'{"ok":false}',403)
  method=parts[1]
  if not re.fullmatch(r'[A-Za-z]+',method):return self.respond(b'{"ok":false}',400)
  try:
   size=int(self.headers.get('Content-Length',0))
   if size>50_000_000:raise ValueError()
   body=self.rfile.read(size) if size else None;content=self.headers.get('Content-Type','application/json')
   if method.lower()=='getupdates':
    params=json.loads(body) if body and 'json' in content else {k:v[0] for k,v in urllib.parse.parse_qs(body.decode() if body else parsed.query).items()}
    data=self.server.broker.relay(int(params.get('offset',0)),int(params.get('timeout',0)))
    return self.respond(json.dumps({'ok':True,'result':data}).encode(),200)
   self.respond(api.raw(method,body,content,parsed.query),200)
  except Exception:self.respond(b'{"ok":false,"description":"Local Telegram relay error"}',502)
 def respond(self,body,status):
  self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

def main():
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s');STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
 with (STATE/'telegram-broker.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  from board_worker import make_store
  broker=Broker(API(),store=make_store())
  server=ThreadingHTTPServer(('127.0.0.1',PORT),Proxy);server.broker=broker
  threading.Thread(target=broker.poll,daemon=True).start();threading.Thread(target=broker.maintain,daemon=True).start()
  LOG.info('Single Telegram receiver ready; local relay port=%s',PORT);server.serve_forever()
if __name__=='__main__':main()
