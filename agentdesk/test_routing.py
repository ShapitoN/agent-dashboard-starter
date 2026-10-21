import copy, json, tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path
from unittest.mock import patch,Mock
import app,routing,board_worker as worker
from runtime import RuntimeFailure

def assessment(n=1,r=0,o=1,signal=None,**kw):
 return dict(provider='claude' if signal else 'codex',claude_signal=signal,unclear=n,risk=r,size=o,needs_human=False,question=None,confidence='high',reason='Тест',**kw)
def decision(a,task=None):return routing.decide(task or {'title':'Тестовая задача'},a,app.MODELS)

class PolicyTests(unittest.TestCase):
 def test_all_documented_examples(self):
  rows=[('Поправить отступ в виджете батареи',0,1,0,None,'codex','low'),('Переписать firebase_sync.py на новую схему, ТЗ есть',0,1,2,None,'codex','medium'),('Почему wifi отваливается после сна — найти и починить',2,1,1,None,'codex','high'),('Сводка по 5 конкурентам B24U из интернета',1,0,1,None,'codex','medium'),('Подготовить звонок с клиникой (Миндаль)',0,0,1,None,'codex','low'),('Смета и КП для нового клиента по записи созвона',1,2,2,'C1','opus','high'),('Ответ Илье по GD, тон аккуратный',1,2,0,'C1','opus','medium'),('Как устроить свою подработку, варианты',2,1,1,'C2','opus','high')]
  for title,n,r,o,c,key,effort in rows:
   with self.subTest(title=title):
    route=decision(assessment(n,r,o,c),{'title':title});self.assertEqual((route['model_key'],route['effort']),(key,effort))
 def test_default_is_codex(self):self.assertEqual(decision(assessment())['model_id'],'gpt-6.1-sol')
 def test_provider_without_signal_is_ignored(self):
  d=assessment();d['provider']='claude';self.assertEqual(decision(d)['provider'],'codex')
 def test_max_only_joint_risk_unclear(self):
  for p,key in [(None,'xhigh'),('C2','max')]:self.assertEqual(decision(assessment(2,2,0,p))['effort'],key)
 def test_large_clear_cap(self):self.assertEqual(decision(assessment(0,1,2))['effort'],'medium')
 def test_manual_prefix(self):
  for label,key in [('клод','opus'),('codex','codex'),('opus','opus'),('sonnet','sonnet')]:
   with self.subTest(label=label):self.assertEqual(decision(assessment(1,2,0),{'title':label+': задача'})['model_key'],key)
 def test_embedded_label_not_manual(self):self.assertEqual(decision(assessment(),{'title':'Передай в opus: задачу'})['model_key'],'codex')
 def test_manual_does_not_bypass_stop(self):
  d=assessment();d.update(needs_human=True,question='Нужен доступ');self.assertTrue(decision(d,{'title':'codex: задача'})['needs_human'])
 def test_escalate_second_return(self):self.assertEqual(decision(assessment(),{'title':'Задача','codexFailures':2})['model_key'],'opus')
 def test_unable_escalation(self):self.assertEqual(decision(assessment(),{'title':'Задача','codexUnable':True})['model_key'],'opus')
 def test_false_c3_ignored(self):self.assertEqual(decision(assessment(signal='C3'))['model_key'],'codex')
 def test_low_confidence_stop(self):
  d=assessment();d['confidence']='low';self.assertTrue(decision(d)['needs_human'])
 def test_fallback_default_medium(self):self.assertEqual(app.route('Сделай сводку по конкурентам')['effort'],'medium')
 def test_fallback_c1(self):self.assertEqual(app.route('Напиши письмо клиенту о сроках')['model_key'],'opus')
 def test_no_astra(self):self.assertNotIn('astra',json.dumps(app.MODELS).lower())
 def test_scores_validated(self):
  d=assessment();d['risk']=True
  with self.assertRaises(ValueError):decision(d)
 def test_no_auth_fallback(self):
  with patch('runtime.assess',side_effect=RuntimeFailure('Not logged in','auth')):self.assertEqual(app.choose_route({'title':'Сводка по рынку'})['method'],'Без Sonnet')
 def test_routing_failure_not_silently_ignored(self):
  with patch('runtime.assess',side_effect=RuntimeFailure('Bad JSON','format')):
   with self.assertRaises(ValueError):app.choose_route({'title':'Сводка по рынку'})
 def test_manual_still_assessed(self):
  with patch('runtime.assess',return_value=(assessment(2,2,1),{})) as assess:
   route=app.choose_route({'title':'codex: сложная задача'});self.assertEqual(route['effort'],'xhigh');assess.assert_called_once()

class MemoryStore:
 def __init__(self,task):self.task=copy.deepcopy(task);self.events=[];self.messages=[];self.owned=True
 def claim(self,ident,expected):
  self.task['agentDesk']={**self.task.get('agentDesk',{}),'runToken':'token'};return 'token',copy.deepcopy(self.task)
 def save(self,ident,token,task,patch,agent):
  if self.task.get('routeNonce')!=task.get('routeNonce'):return False
  self.task.update(patch);self.task.setdefault('agentDesk',{}).update(agent);return True
 def record_telemetry(self,*args):pass
 def scheduler_status(self,*args):pass
 def plan(self,ident,task,route):return True
 def event(self,*args):self.events.append(args)
 def notify(self,*args):self.messages.append(args)
 def candidates(self,status):return [('test',self.task)] if self.task.get('status')==status else []
 def chat_append(self,message):return (self.task.get('chat') or [])+[message]
 def cancel_check(self,ident,task):return lambda:None

class LifecycleTests(unittest.TestCase):
 def setUp(self):
  self.task={'title':'Проверочная задача','description':'Критерий готовности описан','status':'todo','routeNonce':'one'}
  self.route=decision(assessment(0,0,0))
  self.result={'status':'completed','summary':'Проверено','question':None,'verified':True,'evidence':['test: passed'],'reroute':None}
 def run_task(self,store,result=None,route=None):
  with patch('board_worker.choose_route',return_value=route or self.route),patch('board_worker.execute',return_value=(result or self.result,{'actual_model':'gpt-6.1-sol'})):
   worker.process(store,'test',copy.deepcopy(store.task))
 def test_verified_to_done(self):
  s=MemoryStore(self.task);self.run_task(s);self.assertEqual(s.task['status'],'done')
 def test_unverified_to_test(self):
  s=MemoryStore(self.task);r=dict(self.result,verified=False);self.run_task(s,r);self.assertEqual(s.task['status'],'test')
 def test_risky_to_review(self):
  s=MemoryStore(self.task);self.run_task(s,route=decision(assessment(2,1,1)));self.assertEqual(s.task['status'],'test')
 def test_human_issue_notifies(self):
  s=MemoryStore(self.task);r=dict(self.route,needs_human=True,question='Нужен вход');self.run_task(s,route=r);self.assertEqual(s.task['status'],'issue');self.assertEqual(len(s.messages),1)
 def test_failed_codex_escalates_opus(self):
  s=MemoryStore(self.task);self.run_task(s,dict(self.result,status='failed'));self.assertEqual(s.task['status'],'todo');self.assertEqual(s.task['agentDesk']['nextRoute']['model_key'],'opus')
 def test_codex_reroute_actually_executes_opus(self):
  s=MemoryStore(self.task)
  reroute=dict(self.result,status='reroute',verified=False,reroute={'provider':'claude','model_key':'opus','effort':'high','reason':'Кабинеты только Кокос'})
  with patch('board_worker.choose_route',return_value=self.route),patch('board_worker.execute',side_effect=[(reroute,{}),(self.result,{})]) as execute:
   worker.process(s,'test',copy.deepcopy(s.task))
  self.assertEqual(execute.call_count,2)
  self.assertEqual(execute.call_args_list[1].args[1]['model_key'],'opus')
  self.assertEqual(s.task['status'],'done')
  self.assertEqual(s.messages,[])
 def test_limit_safe_fallback(self):
  s=MemoryStore(self.task)
  with patch('board_worker.choose_route',return_value=self.route),patch('board_worker.execute',side_effect=RuntimeFailure('limit','limit')):worker.process(s,'test',s.task.copy())
  self.assertEqual(s.task['agentDesk']['nextRoute']['model_key'],'sonnet')
 def test_limit_risk_two_blocks(self):
  s=MemoryStore(self.task)
  with patch('board_worker.choose_route',return_value=decision(assessment(1,2,1))),patch('board_worker.execute',side_effect=RuntimeFailure('limit','limit')):worker.process(s,'test',s.task.copy())
  self.assertEqual(s.task['status'],'issue')
 def test_review_second_rejection_escalates(self):
  self.task.update(status='test',agentDesk={'route':self.route,'result':self.result,'codexFailures':1})
  s=MemoryStore(self.task)
  with patch('board_worker.review',return_value=({'verdict':'returned','summary':'Исправь тест','question':None},{})):worker.process(s,'test',s.task.copy())
  self.assertEqual(s.task['agentDesk']['nextRoute']['model_key'],'opus')
 def test_review_accept(self):
  self.task.update(status='test',agentDesk={'route':self.route,'result':self.result})
  s=MemoryStore(self.task)
  with patch('board_worker.review',return_value=({'verdict':'accepted','summary':'Принято','question':None},{})):worker.process(s,'test',s.task.copy())
  self.assertEqual(s.task['status'],'done');self.assertEqual(len(s.messages),1)
 def test_backlog_not_read(self):
  s=MemoryStore(dict(self.task,status='backlog'))
  with patch.object(s,'candidates',wraps=s.candidates) as c:worker.tick(s);self.assertNotIn('backlog',[x.args[0] for x in c.call_args_list])
 def test_cached_route_not_classified_again(self):
  self.task['agentDesk']={'route':self.route,'routedHash':routing.fingerprint(self.task),'routedNonce':'one'};s=MemoryStore(self.task)
  with patch('board_worker.choose_route') as choose,patch('board_worker.execute',return_value=(self.result,{})):worker.process(s,'test',s.task.copy());choose.assert_not_called()
 def test_auth_error_blocks(self):
  s=MemoryStore(self.task)
  with patch('board_worker.choose_route',return_value=self.route),patch('board_worker.execute',side_effect=RuntimeFailure('Войди','auth')):worker.process(s,'test',s.task.copy())
  self.assertEqual(s.task['status'],'issue')

if __name__=='__main__':unittest.main()

class AttachmentTests(unittest.TestCase):
 def test_png_attachment(self):
  import base64,io
  from PIL import Image
  from runtime import prepare_images
  data=io.BytesIO();Image.new('RGB',(2,2),'red').save(data,format='PNG')
  with tempfile.TemporaryDirectory() as directory:
   paths=prepare_images({'images':[{'url':'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()}]},directory)
   self.assertEqual(len(paths),1)
   with Image.open(paths[0]) as image:self.assertEqual(image.size,(2,2))
 def test_external_image_is_not_fetched(self):
  from runtime import prepare_images
  with tempfile.TemporaryDirectory() as directory:
   with self.assertRaises(RuntimeFailure):prepare_images({'images':[{'url':'http://127.0.0.1/private'}]},directory)
 def test_router_does_not_receive_image_data(self):
  with patch('runtime.assess',return_value=(assessment(),{})) as assess:
   app.choose_route({'title':'Проверить макет','images':[{'url':'private-base64'}]})
   data=assess.call_args.args[0];self.assertNotIn('images',data);self.assertEqual(data['attachments_count'],1)


class DraftAcceptanceTests(unittest.TestCase):
 def test_external_draft_never_auto_done(self):
  route=decision(assessment(1,2,1,'C1'))
  result={'status':'completed','summary':'Черновик','verified':True,'evidence':['Проверено'],'question':None}
  self.assertEqual(worker.after_execution(route,result),'test')
