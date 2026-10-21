#!/usr/bin/env python3
"""Single leased Kanban worker; Firestore is the durable queue and outbox."""
import argparse, copy, fcntl, hashlib, json, logging, os, signal, sys, threading, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from app import MODELS, choose_route, apply_manual
from routing import fingerprint
from runtime import execute,review,RuntimeFailure,STATE,CANCEL
from scheduling import POLL_SECONDS,RESULTS,workload,admit,machine_capacity
from telemetry import reporting,summarize
from auth_bridge import auth_request
from approvals import ApprovalDB,public_request,permission_question
from settings import BASE, DASHBOARD_URL as URL
LOG=logging.getLogger('agentdesk')

def now():return datetime.now(timezone.utc)
def task_url(ident):return URL+'/?task='+quote(ident,safe='')+'#kanban'

def chat_message(text,sender='agent'):
 return {'id':str(uuid.uuid4()),'from':sender,'text':str(text)[:4000],'at':now().isoformat()}

def answered(task):
 # Антон's message is the last one in the card chat: the agent's question has its answer.
 chat=task.get('chat') or []
 return bool(chat) and (chat[-1] or {}).get('from')=='anton'

def after_execution(route,result):
 if result['status']=='needs_human':return 'issue'
 # A manual choice is never escalated to another model behind Антон's back.
 if result['status']=='failed':return 'escalate' if route['provider']=='codex' and not route.get('manual') else 'issue'
 if result['status']=='reroute' and route.get('manual'):return 'issue'
 if result['status']=='reroute':return 'reroute'
 if route.get('claude_signal')=='C1':return 'test'
 if route['provider']=='codex' and (route['scores']['risk']==2 or route['scores']['unclear']==2):return 'test'
 # Automatic acceptance needs fresh, explicit verification evidence.
 return 'done' if result['verified'] and result['evidence'] else 'test'

def make_store():
 import firebase_admin
 from firebase_admin import credentials,firestore
 if not firebase_admin._apps:firebase_admin.initialize_app(credentials.Certificate(str(BASE/'config/serviceAccountKey.json')))
 return Store(firestore.client())

class Store:
 def __init__(self,db):
  from firebase_admin import firestore
  self.db=db;self.fs=firestore;self.owner=str(uuid.uuid4());self.lock=db.collection('agentdesk_runtime').document('worker');self.owned=False
 def transaction(self,fn):return self.fs.transactional(fn)(self.db.transaction())
 def acquire(self):
  def tx(t):
   s=self.lock.get(transaction=t);d=s.to_dict() or {}
   if d.get('expiresAt',now()-timedelta(seconds=1))>now() and d.get('owner')!=self.owner:return False
   t.set(self.lock,{'owner':self.owner,'expiresAt':now()+timedelta(seconds=90),'updatedAt':now()});return True
  self.owned=self.transaction(tx);return self.owned
 def heartbeat(self):
  def tx(t):
   d=self.lock.get(transaction=t).to_dict() or {}
   if d.get('owner')!=self.owner:return False
   t.update(self.lock,{'expiresAt':now()+timedelta(seconds=90),'updatedAt':now()});return True
  self.owned=self.transaction(tx);return self.owned
 def release(self):
  def tx(t):
   d=self.lock.get(transaction=t).to_dict() or {}
   if d.get('owner')==self.owner:t.update(self.lock,{'expiresAt':now(),'updatedAt':now()})
  self.transaction(tx);self.owned=False
 def candidates(self,status):
  from google.cloud.firestore_v1.base_query import FieldFilter
  return [(d.id,d.to_dict()) for d in self.db.collection('tasks').where(filter=FieldFilter('status','==',status)).stream()]
 def claim(self,ident,expected):
  ref=self.db.collection('tasks').document(ident);token=str(uuid.uuid4())
  def tx(t):
   snap=ref.get(transaction=t);d=snap.to_dict()
   if not d or d.get('status')!=expected.get('status') or fingerprint(d)!=fingerprint(expected) or d.get('routeNonce')!=expected.get('routeNonce'):return None
   # A previous dead worker cannot have its task silently executed twice.
   agent=d.get('agentDesk') or {}
   agent.update(runToken=token,owner=self.owner,phase='reviewing' if d['status']=='test' else 'routing',startedAt=now(),inputHash=fingerprint(d),inputNonce=d.get('routeNonce'))
   t.update(ref,{'agentDesk':agent,'status':'test' if d['status']=='test' else 'in-progress','updatedAt':now()})
   return dict(d,agentDesk=agent)
  d=self.transaction(tx)
  return (token,d) if d else (None,None)
 def save(self,ident,token,task,patch,agent):
  ref=self.db.collection('tasks').document(ident)
  def tx(t):
   d=ref.get(transaction=t).to_dict()
   if not d or (d.get('agentDesk') or {}).get('runToken')!=token:return False
   if fingerprint(d)!=fingerprint(task) or d.get('routeNonce')!=task.get('routeNonce'):
    if d.get('status')=='in-progress':
     changed=d.get('agentDesk') or {};changed['phase']='superseded'
     t.update(ref,{'status':'todo','agentDesk':changed,'updatedAt':now()})
    return False
   if d.get('status') not in ('in-progress','test'):return False
   current=d.get('agentDesk') or {};current.update(agent)
   t.update(ref,dict(patch,agentDesk=current,updatedAt=now()));return True
  if not self.owned:return False
  return self.transaction(tx)
 def record_telemetry(self,ident,task,phase,measurement):
  ref=self.db.collection('tasks').document(ident)
  key=hashlib.sha256(measurement['run_id'].encode()).hexdigest()[:24]
  def tx(t):
   d=ref.get(transaction=t).to_dict() or {}
   if fingerprint(d)!=fingerprint(task) or d.get('routeNonce')!=task.get('routeNonce'):return
   a=d.get('agentDesk') or {};ledger=a.get('usageLedger') or {};entry=dict(measurement,phase=phase)
   if ledger.get(key)==entry:return
   ledger[key]=entry;a.update(usageLedger=ledger,usage=summarize(ledger),currentExecutor={'provider':measurement['provider'],'model':measurement.get('actual_model'),'phase':phase,'status':measurement['status'],'agents':measurement.get('agents',[])})
   t.update(ref,{'agentDesk':a})
  self.transaction(tx)
 def plan(self,ident,task,route):
  ref=self.db.collection('tasks').document(ident)
  def tx(t):
   d=ref.get(transaction=t).to_dict() or {}
   if d.get('status')!='todo' or fingerprint(d)!=fingerprint(task) or d.get('routeNonce')!=task.get('routeNonce'):return False
   a=d.get('agentDesk') or {};a.update(route=route,routedHash=fingerprint(d),routedNonce=d.get('routeNonce'),phase='queued')
   t.update(ref,{'agentDesk':a});return True
  return self.transaction(tx)
 def scheduler_status(self,data):
  self.lock.set(data,merge=True)
 def event(self,ident,kind,data):
  self.db.collection('agentdesk_events').add(dict(data,taskId=ident,kind=kind,createdAt=now()))
 def notify(self,ident,reason,title,text,review_event=False):
  # The same card can stop again for a different reason after Антон returns it to To do;
  # only an identical message is a duplicate.
  key=hashlib.sha256((ident+'|'+reason+'|'+text).encode()).hexdigest();ref=self.db.collection('agentdesk_notifications').document(key)
  def reserve(t):
   if ref.get(transaction=t).exists:return False
   t.set(ref,{'taskId':ident,'reason':reason,'status':'sending','createdAt':now()});return True
  if not self.transaction(reserve):return
  prefix='AgentDesk: приёмка — '+text if review_event else 'AgentDesk: нужен ты'
  message=(prefix+'\n'+title+'\n'+('' if review_event else 'Почему: ')+('' if review_event else text)+'\n'+task_url(ident))
  try:
   sys.path.insert(0,str(BASE/'AIAgent/04 - Agent Context/Kiwi'))
   from kiwi import notify
   mid=notify(message)
   ref.update({'status':'sent' if mid else 'queued','messageId':mid,'updatedAt':now()})
  except Exception:
   # Unknown delivery state: no blind retry that could duplicate a notification.
   ref.update({'status':'uncertain','updatedAt':now()});LOG.exception('Notification needs inspection: %s',key)
 def chat_append(self,message):return self.fs.ArrayUnion([message])
 def cancel_check(self,ident,task):
  ref=self.db.collection('tasks').document(ident);started=(task.get('routeNonce'),task.get('manualExecutor'))
  def check():
   d=ref.get().to_dict()
   if not d:return 'Карточка удалена.'
   if (d.get('routeNonce'),d.get('manualExecutor'))!=started:return 'Антон сменил исполнителя или перезапустил карточку.'
   if d.get('status') not in ('in-progress','test'):return 'Карточку убрали из работы.'
  return check
 def watch(self,wake):
  # Firestore pushes changes of Issue/To do cards; a chat reply wakes the worker in seconds
  # instead of the 10-minute poll.
  from google.cloud.firestore_v1.base_query import FieldFilter
  query=self.db.collection('tasks').where(filter=FieldFilter('status','in',['issue','todo']))
  return query.on_snapshot(lambda *args:wake.set())
 def resume_answered(self):
  # Issue → To do once Антон answered in the card chat; the planned route is kept, a router
  # question counts as answered.
  moved=[]
  for ident,d in self.candidates('issue'):
   a=d.get('agentDesk') or {}
   if not answered(d) or a.get('phase')=='awaiting_approval':continue
   if 'only with human' in [str(x).lower() for x in d.get('tags') or []]:continue
   ref=self.db.collection('tasks').document(ident);last=(d.get('chat') or [])[-1].get('id')
   def tx(t):
    fresh=ref.get(transaction=t).to_dict() or {}
    if fresh.get('status')!='issue' or not answered(fresh) or fresh['chat'][-1].get('id')!=last:return False
    agent=fresh.get('agentDesk') or {};route=agent.get('nextRoute') or agent.get('route')
    if route and route.get('needs_human'):route=dict(route,needs_human=False,question=None)
    agent.update(phase='queued',nextRoute=route,executor=None)
    t.update(ref,{'status':'todo','agentDesk':agent,'updatedAt':now()});return True
   if self.transaction(tx):self.event(ident,'chat-reply',{'messageId':last});moved.append(ident)
  return moved
 def recover(self):
  # On restart a killed task requires inspection, never automatic replay of side effects.
  for ident,d in self.candidates('in-progress'):
   a=d.get('agentDesk') or {}
   if a.get('owner') and a.get('owner')!=self.owner:
    ref=self.db.collection('tasks').document(ident)
    def tx(t):
     fresh=ref.get(transaction=t).to_dict() or {}
     if fresh.get('status')!='in-progress' or (fresh.get('agentDesk') or {}).get('runToken')!=a.get('runToken'):return False
     old=fresh.get('agentDesk') or {};old.update(phase='interrupted')
     t.update(ref,{'status':'issue','agentDesk':old,'agentNotes':'Исполнение прервано. Проверь результат перед возвратом в To do.','chat':self.chat_append(chat_message('Исполнение прервано перезапуском службы. Проверь результат и напиши здесь «продолжай» — заберу задачу снова.')),'updatedAt':now()});return True
    if self.transaction(tx):self.notify(ident,'interrupted',d.get('title','Задача'),'Исполнение прервано. Проверь результат перед возвратом в To do.')

def issue(store,ident,token,task,message,reason='human',agent=None):
 details=dict(agent or {},phase='issue');result=details.get('result') or {}
 request=auth_request(dict(task,agentNotes=message),result)
 if request:details['authRequest']=request
 prior_grant=ApprovalDB().grant(ident,task)
 if prior_grant and request:
  message='Нужен браузер, доступный исполнителю и авторизованный в CRM. Разрешение Антона на одну указанную отправку уже есть; повторно спрашивать его не нужно.'
 proposal=result.get('approval')
 if prior_grant and proposal and proposal.get('target')==prior_grant['action']['target'] and proposal.get('text')==prior_grant['action']['text']:proposal=None
 approval=None
 if proposal:
  try:approval=ApprovalDB().request(ident,task,proposal)
  except ValueError:message+=' Уточни получателя и точный текст для согласования.'
 if approval:
  details['approvalRequest']=public_request(approval)
  if approval['status']=='pending':
   details['phase']='awaiting_approval';message='Жду решения Антона в Telegram: Можно / Нельзя.'
  elif approval['status']=='denied':message='Антон уже запретил это действие. Повторно разрешение не запрашивается.'
  elif approval['status'] in ('authorized','reserved','used','uncertain'):message='Разрешение уже учтено. '+message
 if store.save(ident,token,task,{'status':'issue','agentNotes':message,'chat':store.chat_append(chat_message(message))},details):
  store.event(ident,'issue',{'reason':reason,'message':message})
  if not approval:store.notify(ident,reason,task.get('title','Задача'),message)


def process(store,ident,task,admission=None):
 phase='reviewing' if task.get('status')=='test' else 'executing'
 callback=lambda m:store.record_telemetry(ident,task,phase,m)
 CANCEL.check=store.cancel_check(ident,task);CANCEL.last=0
 try:
  with reporting(callback):return _process(store,ident,task,admission)
 finally:CANCEL.check=None

def _process(store,ident,task,admission=None):
 token,task=store.claim(ident,task)
 if not task:return
 a=copy.deepcopy(task.get('agentDesk') or {});route=a.get('route');cycle=str(uuid.uuid4());executor=None
 try:
  if task['status']=='test':
   if not route:return
   result=a.get('result') or {}
   verdict,meta=review(task,result,route)
   if verdict.get('verdict') not in ('accepted','returned','needs_human'):raise RuntimeFailure('Некорректный вердикт приёмки.','format')
   if verdict['verdict']=='needs_human':issue(store,ident,token,task,verdict.get('question') or verdict['summary'],'review-human');return
   accepted=verdict['verdict']=='accepted';fails=int(a.get('codexFailures',0))+(0 if accepted else 1)
   patch={'status':'done' if accepted else 'todo','agentNotes':None if accepted else verdict['summary']}
   update={'phase':'done' if accepted else 'queued','review':verdict,'reviewRuntime':meta,'codexFailures':fails,'route':route}
   if not accepted:
    retry=dict(route)
    if fails>=2:
     retry.update(provider='claude',model_key='opus',model=MODELS['opus']['label'],model_id=MODELS['opus']['id'],claude_signal='C3')
     if retry['effort']=='xhigh':retry['effort']='max'
    update['nextRoute']=retry
   if store.save(ident,token,task,patch,update):
    store.event(ident,'review',{'verdict':verdict,'route':route,'runtime':meta})
    store.notify(ident,'review:'+str(a.get('executionId',''))+':'+verdict['verdict'],task.get('title','Задача'),('принято' if accepted else 'возвращено')+'\n'+verdict['summary'],True)
   return
  grant=ApprovalDB().grant(ident,task)
  enriched=dict(task,codexFailures=a.get('codexFailures',0),codexUnable=a.get('codexUnable',False),approved_action=grant['action'] if grant else None)
  fresh_input=fingerprint(task)
  if a.get('routedHash')==fresh_input and a.get('routedNonce')==task.get('routeNonce'):
   route=a.get('nextRoute') or a.get('route')
  else:route=None
  if not route:route=choose_route(enriched)
  route=apply_manual(route,task)
  if not store.save(ident,token,task,{}, {'route':route,'nextRoute':None,'phase':'routing','routedHash':fresh_input,'routedNonce':task.get('routeNonce')}):return
  store.event(ident,'route',{'route':route,'inputHash':fresh_input,'executionId':cycle})
  if route['needs_human']:issue(store,ident,token,task,route['question'],'router:'+route['question']);return
  # Admission is held for this job; runtime locks enforce file/resource conflicts.
  max_reroutes=3
  for reroutes in range(max_reroutes+1):
   # One authoritative «who works on it» field for the board; route alone is only the plan.
   executor={'provider':route['provider'],'model':route.get('model'),'model_id':route.get('model_id'),'effort':route.get('effort'),'state':'running','startedAt':now(),'actual_model':None}
   if not store.save(ident,token,task,{}, {'phase':'executing','route':route,'executionId':cycle,'executor':executor}):return
   prompt_task={k:task.get(k) for k in ['title','description','tags','images','workspace','chat']}
   prompt_task['resources']=task.get('resources') or []
   if admission:
    prompt_task['workspace']=admission['workspace']
    if admission.get('isolated'):
     Path(admission['workspace']).mkdir(parents=True,exist_ok=True)
     prompt_task['isolation']='Создавай и меняй файлы только в этой рабочей папке, итоговые файлы клади прямо в неё (без вложенных служебных папок) и укажи полный путь к ним в summary. Общие файлы, браузер и настройки не изменять.'
   if task.get('agentNotes'):prompt_task['feedback']=task['agentNotes']
   grant=ApprovalDB().grant(ident,task)
   reserved=False
   if grant:
    reserved=ApprovalDB().reserve(grant['id'])
    if not reserved:raise RuntimeFailure('Разрешённое действие уже выполняется или использовано. Повторная отправка запрещена.','human')
    prompt_task['verifiedApproval']=grant
   try:
    result,meta=execute(prompt_task,route,STATE/'runs'/(cycle+'-'+str(reroutes)))
   except RuntimeFailure as error:
    if reserved:ApprovalDB().finish(grant['id'],'not_sent' if error.kind in ('auth','access','busy') else 'unknown')
    raise
   except Exception:
    if reserved:ApprovalDB().finish(grant['id'],'unknown')
    raise
   executor=dict(executor,state='finished',finishedAt=now(),actual_model=meta.get('actual_model'))
   store.save(ident,token,task,{}, {'executor':executor})
   if reserved:
    action_status=result.get('approved_action_status')
    if action_status=='sent' and not result.get('evidence'):action_status='unknown'
    # Handing the task to another executor or asking Антон is not a send attempt; null used to
    # burn the one-time approval as «uncertain».
    if action_status is None and result['status'] in ('reroute','needs_human','failed'):action_status='not_sent'
    ApprovalDB().finish(grant['id'],action_status)
    store.event(ident,'approved-action',{'approvalId':grant['id'],'status':action_status or 'unknown'})
   state=after_execution(route,result)
   store.event(ident,'execution',{'route':route,'result':result,'runtime':meta,'executionId':cycle})
   if state!='reroute':break
   requested=result.get('reroute') or {};key=requested.get('model_key');provider=requested.get('provider');effort=requested.get('effort')
   if key not in MODELS or provider!=('codex' if key=='codex' else 'claude') or effort not in ('low','medium','high','xhigh','max'):raise RuntimeFailure('Исполнитель вернул некорректное переназначение.','format')
   if reroutes==max_reroutes:raise RuntimeFailure('Превышен предел переназначений исполнителей.','reroute')
   if key=='codex' and effort=='max':effort='xhigh'
   if key!='codex' and effort=='xhigh':effort='high'
   previous=route;route=dict(route,provider=provider,model_key=key,model_id=MODELS[key]['id'],model=MODELS[key]['label'],effort=effort,reason=requested.get('reason',''),method=previous['provider']+'-reroute')
   store.event(ident,'reroute',{'from':previous,'route':route})
  if state=='issue':issue(store,ident,token,task,result.get('question') or result['summary'],'executor-human',{'result':result,'runtime':meta});return
  if state=='escalate':
   retry=dict(route,provider='claude',model_key='opus',model_id=MODELS['opus']['id'],model=MODELS['opus']['label'],claude_signal='C3',effort='max' if route['effort']=='xhigh' else route['effort'])
   store.save(ident,token,task,{'status':'todo','agentNotes':result['summary']},{'phase':'queued','codexUnable':True,'nextRoute':retry,'result':result,'runtime':meta})
   store.event(ident,'escalation',{'route':retry,'reason':result['summary']});return
  store.save(ident,token,task,{'status':state,'agentNotes':None},{'phase':state,'route':route,'result':result,'runtime':meta,'executionId':cycle})
 except RuntimeFailure as e:
  # Only rate limits permit fallback, and only for Codex risk<=1. Never mask auth/model errors.
  if e.kind=='cancelled':
   # Антон already moved the card; the stale run leaves no result and no notification.
   store.event(ident,'cancelled',{'reason':str(e),'route':route})
  elif e.kind=='busy':
   store.save(ident,token,task,{'status':'test' if task['status']=='test' else 'todo','agentNotes':'Ожидает освобождения общих ресурсов.'},{'phase':'queued','nextRoute':route,'executor':None})
   store.event(ident,'deferred',{'reason':'Общие ресурсы заняты'})
  elif e.kind=='limit' and route and route['provider']=='codex' and not route.get('manual') and route['scores']['risk']<=1:
   retry=dict(route,provider='claude',model_key='sonnet',model_id=MODELS['sonnet']['id'],model=MODELS['sonnet']['label'],method='Лимит Codex → Sonnet')
   if retry['effort']=='xhigh':retry['effort']='high'
   store.save(ident,token,task,{'status':'todo','agentNotes':str(e)},{'phase':'queued','nextRoute':retry,'executor':dict(executor,state='failed',error='limit') if executor else None})
   store.event(ident,'limit-fallback',{'route':retry})
  else:issue(store,ident,token,task,str(e),e.kind,{'executor':dict(executor,state='failed',finishedAt=now(),error=e.kind)} if executor else None)
 except Exception as e:
  LOG.exception('Task failed: %s',ident);issue(store,ident,token,task,'Ошибка диспетчера. Подробности в журнале AgentDesk.','dispatcher',{'executor':dict(executor,state='failed',error='dispatcher')} if executor else None)


class Scheduler:
 def __init__(self,store):
  self.store=store;self.running={};self.guard=threading.Lock();self.wake=threading.Event()
 def launch(self,ident,task,admission):
  def job():
   try:process(self.store,ident,task,admission)
   except Exception:LOG.exception('Job failed: %s',ident)
   finally:
    with self.guard:self.running.pop(ident,None)
    self.wake.set()  # a freed slot or a card restarted by Антон starts without the 10-minute wait
  with self.guard:self.running[ident]=admission
  thread=threading.Thread(target=job,name='kanban-'+ident,daemon=True);thread.start()
 def tick(self):
  store=self.store
  scan_started=now()
  store.scheduler_status({'lastPollAt':scan_started,'nextPollAt':scan_started+timedelta(seconds=POLL_SECONDS),'pollSeconds':POLL_SECONDS,'maxParallel':2})
  with self.guard:active=dict(self.running)
  # Only a card another AgentDesk worker is actually running blocks the queue. Cards Антон moved to
  # In progress himself, and leftovers of a finished run, used to stop every task on the board.
  running=('routing','executing','reviewing')
  if any(i not in active and (t.get('agentDesk') or {}).get('phase') in running and (t.get('agentDesk') or {}).get('owner') not in (None,store.owner) for i,t in store.candidates('in-progress')):
   store.scheduler_status({'blockedReason':'Другой экземпляр AgentDesk выполняет задачу'});return
  tests=[(i,t) for i,t in store.candidates('test') if (t.get('agentDesk') or {}).get('route',{}).get('provider')=='codex' and (t.get('agentDesk') or {}).get('route',{}).get('claude_signal')!='C1' and any((t.get('agentDesk') or {}).get('route',{}).get('scores',{}).get(k)==2 for k in ('risk','unclear')) and (t.get('agentDesk') or {}).get('result')]
  todos=sorted([(i,t) for i,t in store.candidates('todo') if 'only with human' not in [x.lower() for x in t.get('tags') or []]],key=lambda item:(item[1].get('order',0),item[0]))
  capacity=machine_capacity()
  store.scheduler_status({'blockedReason':None if capacity else 'Мало свободной памяти: задачи ждут'})
  if not capacity and (tests or todos):LOG.warning('No capacity: low memory, %d tasks wait',len(tests)+len(todos))
  for ident,task in tests+todos:
   if ident in active:continue
   if len(active)>=capacity:break
   if any(not x['parallel'] for x in active.values()):break
   a=task.get('agentDesk') or {};route=a.get('route')
   if task['status']=='todo':
    if a.get('routedHash')==fingerprint(task) and a.get('routedNonce')==task.get('routeNonce'):route=apply_manual(a.get('nextRoute') or route,task)
    else:route=None
    if not route:
     try:
      with reporting(lambda m:store.record_telemetry(ident,task,'routing',m)):
       grant=ApprovalDB().grant(ident,task)
       route=choose_route(dict(task,codexFailures=a.get('codexFailures',0),codexUnable=a.get('codexUnable',False),approved_action=grant['action'] if grant else None))
     except Exception as e:
      token,claimed=store.claim(ident,task)
      if claimed:issue(store,ident,token,claimed,'Не удалось определить маршрут: '+str(e),'routing')
      continue
     route=apply_manual(route,task)
     if not store.plan(ident,task,route):continue
     task=copy.deepcopy(task);task.setdefault('agentDesk',{}).update(route=route,routedHash=fingerprint(task),routedNonce=task.get('routeNonce'))
   admission=workload(task,route,ident,STATE,RESULTS)
   # A card that only needs Антон's answer does no work; it must not hold the queue for 10 minutes.
   if route.get('needs_human'):admission=dict(admission,parallel=True,resources=[],workspace=str(STATE/'questions'/ident))
   allowed,reason=admit(admission,list(active.values()),capacity)
   store.event(ident,'admission',{'accepted':allowed,'reason':reason,'parallel':admission['parallel']})
   if not allowed:
    if not admission['parallel']:break  # no starvation of an earlier heavy job
    continue
   self.launch(ident,task,admission);active[ident]=admission
   if not admission['parallel']:break


def tick(store):
 # Compatibility for one-shot callers; the service retains its Scheduler instance.
 scheduler=Scheduler(store);scheduler.tick();return scheduler


def main():
 parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');parser.add_argument('--inspect',action='store_true');parser.add_argument('--stats',action='store_true');args=parser.parse_args()
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
 STATE.mkdir(parents=True,exist_ok=True)
 with (STATE/'board-worker.lock').open('w') as lock:
  if not (args.inspect or args.stats):
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:LOG.info('Worker already running');return
  store=make_store()
  if args.stats:
   from google.cloud.firestore_v1.base_query import FieldFilter
   events=[d.to_dict() for d in store.db.collection('agentdesk_events').where(filter=FieldFilter('createdAt','>=',now()-timedelta(days=7))).stream()]
   routes=[e for e in events if e.get('kind')=='route'];codex=sum(e.get('route',{}).get('provider')=='codex' for e in routes)
   report={'period_days':7,'routes':len(routes),'codex_share':codex/len(routes) if routes else None,'returns':sum(e.get('kind')=='review' and e.get('verdict',{}).get('verdict')=='returned' for e in events),'escalations':sum(e.get('kind')=='escalation' for e in events),'manual_routes':sum(e.get('route',{}).get('method')=='Антон вручную' for e in routes)}
   print(json.dumps(report,ensure_ascii=False));return
  if args.inspect:
   print(json.dumps({'todo':len(store.candidates('todo')),'in-progress':len(store.candidates('in-progress')),'test':len(store.candidates('test'))}));return
  while not store.acquire():
   if args.once:LOG.info('Another worker holds lease');return
   LOG.info('Waiting for previous lease to expire');time.sleep(10)
  stopped=threading.Event()
  def beat():
   while not stopped.wait(20):
    try:
     if not store.heartbeat():LOG.error('Lease lost');os.kill(os.getpid(),signal.SIGTERM);return
    except Exception:LOG.exception('Heartbeat failed');os.kill(os.getpid(),signal.SIGTERM);return
  thread=threading.Thread(target=beat,daemon=True);thread.start()
  def terminate(signum,frame):raise SystemExit(0)
  signal.signal(signal.SIGTERM,terminate)
  signal.signal(signal.SIGINT,terminate)
  try:
   store.recover()
   scheduler=Scheduler(store);watch=None
   if not args.once:
    try:watch=store.watch(scheduler.wake)
    except Exception:LOG.exception('Live updates unavailable; 10-minute poll only')
   next_tick=0
   while True:
    urgent=False
    if scheduler.wake.is_set():
     scheduler.wake.clear()
     try:
      moved=store.resume_answered()
      with scheduler.guard:active=set(scheduler.running)
      def waiting(i,t):
       a=t.get('agentDesk') or {}
       if i in active or 'only with human' in [str(x).lower() for x in t.get('tags') or []]:return False
       # New or restarted card, or a queued one that only waited for a free slot.
       return a.get('routedNonce')!=t.get('routeNonce') or (a.get('phase')=='queued' and not active)
      urgent=bool(moved) or any(waiting(i,t) for i,t in store.candidates('todo'))
     except Exception:LOG.exception('Live check failed')
    if urgent or time.monotonic()>=next_tick:
     next_tick=time.monotonic()+POLL_SECONDS
     try:scheduler.tick()
     except Exception:LOG.exception('Poll failed')
    if args.once:
     while scheduler.running:time.sleep(.1)
     break
    # Short pause coalesces a burst of snapshot events into one check.
    scheduler.wake.wait(timeout=max(1,next_tick-time.monotonic()));time.sleep(1)
  finally:
   stopped.set();thread.join(timeout=1)
   try:watch and watch.unsubscribe()
   except Exception:pass
   store.release()
if __name__=='__main__':main()
