import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from approvals import ApprovalDB,permission_question
from telegram_broker import Broker

ACTION={'kind':'send_message','description':'Отправить тест','target':'https://example.com/chat','text':'Проверка'}
TASK={'title':'Проверка','description':'Точный критерий','status':'issue'}
class ApprovalTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.db=ApprovalDB(Path(self.tmp.name)/'approvals.db')
 def tearDown(self):self.tmp.cleanup()
 def request(self):return self.db.request('card',TASK,ACTION)
 def test_dedup_same_action(self):self.assertEqual(self.request()['id'],self.request()['id'])
 def test_exact_text_scope(self):
  one=self.request();two=self.db.request('card',TASK,dict(ACTION,text='Другой текст'));self.assertNotEqual(one['id'],two['id'])
 def test_changed_card_requires_new_consent(self):
  row=self.request();self.db.bind_message(row['id'],10);self.assertTrue(self.db.decide(row['id'],7,7,10,7,True));self.assertIsNone(self.db.grant('card',dict(TASK,description='Другая задача')))
 def test_only_anton_may_allow(self):
  row=self.request();self.db.bind_message(row['id'],10);self.assertFalse(self.db.decide(row['id'],8,7,10,7,True));self.assertIsNone(self.db.grant('card',TASK))
 def test_forwarded_or_wrong_message_cannot_allow(self):
  row=self.request();self.db.bind_message(row['id'],10)
  for actor,chat,mid in [(7,8,10),(7,7,11)]:self.assertFalse(self.db.decide(row['id'],actor,chat,mid,7,True))
 def test_approve_once(self):
  row=self.request();self.db.bind_message(row['id'],10);self.assertTrue(self.db.decide(row['id'],7,7,10,7,True));self.assertFalse(self.db.decide(row['id'],7,7,10,7,True));self.assertIsNotNone(self.db.grant('card',TASK))
 def test_deny_never_grants(self):
  row=self.request();self.db.bind_message(row['id'],10);self.db.decide(row['id'],7,7,10,7,False);self.assertIsNone(self.db.grant('card',TASK));self.assertEqual(self.request()['status'],'denied')
 def test_expired_never_grants(self):
  row=self.request();self.db.bind_message(row['id'],10)
  with self.db.connect() as c:c.execute('UPDATE approvals SET expires=0')
  self.assertFalse(self.db.decide(row['id'],7,7,10,7,True))
 def test_reserve_only_once_and_unknown_never_repeats(self):
  row=self.db.request('card',TASK,ACTION,direct_actor=7);self.assertTrue(self.db.reserve(row['id']));self.assertFalse(self.db.reserve(row['id']));self.db.finish(row['id'],'unknown');self.assertIsNone(self.db.grant('card',TASK))
 def test_not_sent_keeps_prior_permission(self):
  row=self.db.request('card',TASK,ACTION,direct_actor=7);self.db.reserve(row['id']);self.db.finish(row['id'],'not_sent');self.assertIsNotNone(self.db.grant('card',TASK))
 def test_sending_disabled_for_incomplete_proposal(self):
  with self.assertRaises(ValueError):self.db.request('card',TASK,dict(ACTION,text=''))
 def test_browser_question_is_not_consent_question(self):self.assertFalse(permission_question('Подключи браузер и разреши отправку сообщения'))

class BrokerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.db=ApprovalDB(root/'approval.db');self.api=Mock(actor='7');self.api.call.return_value={'message_id':10};self.broker=Broker(self.api,self.db,root/'inbox.db')
 def tearDown(self):self.tmp.cleanup()
 def test_send_buttons_once(self):
  row=self.db.request('card',TASK,ACTION);self.broker.send_requests();self.broker.send_requests();calls=[c for c in self.api.call.call_args_list if c.args[0]=='sendMessage'];self.assertEqual(len(calls),1);keyboard=calls[0].args[1]['reply_markup'];self.assertEqual([b['text'] for b in keyboard['inline_keyboard'][0]],['✅ Можно','❌ Нельзя']);self.assertEqual(self.db.get(row['id'])['message_id'],10)
 def test_ordinary_messages_relay_to_claude(self):
  self.broker.consume({'update_id':3,'message':{'text':'Привет'}});self.assertEqual(len(self.broker.relay()),1);self.assertEqual(self.broker.cursor(),4);self.assertEqual(self.broker.relay(offset=4),[])
 def test_native_permission_callbacks_relay(self):
  self.broker.consume({'update_id':4,'callback_query':{'data':'perm:allow:abcde'}});self.assertEqual(len(self.broker.relay()),1)
 def test_approval_callback_not_sent_to_llm(self):
  row=self.db.request('card',TASK,ACTION);self.broker.send_requests()
  self.broker.consume({'update_id':4,'callback_query':{'id':'q','data':'ad:yes:'+row['id'],'from':{'id':7},'message':{'message_id':10,'chat':{'id':7}}}})
  self.assertEqual(self.db.get(row['id'])['status'],'authorized');self.assertEqual(self.broker.relay(),[])
 def test_network_uncertainty_does_not_duplicate(self):
  row=self.db.request('card',TASK,ACTION);self.api.call.side_effect=TimeoutError();self.broker.send_requests();self.broker.send_requests();self.assertEqual(self.api.call.call_count,1);self.assertEqual(self.db.get(row['id'])['status'],'delivery_uncertain')

class RoutingApprovalTests(unittest.TestCase):
 def test_scoped_permission_allows_preparing_telegram_request(self):
  import app
  route=app.route('Отправь сообщение клиенту с подтверждением встречи')
  self.assertFalse(route['needs_human']);self.assertTrue(route['approval_preparation'])
 def test_browser_problem_still_blocks(self):
  import app
  route=app.route('Нужен доступ к браузеру для проверки сайта')
  self.assertTrue(route['needs_human'])
if __name__=='__main__':unittest.main()

class DecisionSyncTests(unittest.TestCase):
 def setUp(self):
  import copy
  self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);self.db=ApprovalDB(root/'a.db');self.task=copy.deepcopy(TASK)
  row=self.db.request('card',self.task,ACTION);self.db.bind_message(row['id'],10);self.ident=row['id'];self.task['agentDesk']={'approvalRequest':{'id':row['id']}}
  owner=self
  class Ref:
   def get(self,transaction=None):return self
   def to_dict(self):return owner.task.copy()
  class Tx:
   def update(self,ref,patch):owner.task.update(patch)
  ref=Ref();store=Mock();store.db.collection.return_value.document.return_value=ref;store.transaction=lambda f:f(Tx())
  self.broker=Broker(Mock(actor='7'),self.db,root/'in.db',store)
 def tearDown(self):self.temp.cleanup()
 def test_allow_automatically_requeues_card(self):
  self.db.decide(self.ident,7,7,10,7,True);self.broker.sync_decisions();self.assertEqual(self.task['status'],'todo');self.assertIn('routeNonce',self.task);self.assertTrue(self.db.get(self.ident)['synced'])
 def test_deny_keeps_card_blocked(self):
  self.db.decide(self.ident,7,7,10,7,False);self.broker.sync_decisions();self.assertEqual(self.task['status'],'issue');self.assertIn('Нельзя',self.task['agentNotes'])
 def test_changed_card_not_resumed(self):
  self.task['description']='Изменённое задание';self.db.decide(self.ident,7,7,10,7,True);self.broker.sync_decisions();self.assertEqual(self.task['status'],'issue');self.assertNotIn('routeNonce',self.task)
 def test_already_done_not_reopened(self):
  self.task['status']='done';self.db.decide(self.ident,7,7,10,7,True);self.broker.sync_decisions();self.assertEqual(self.task['status'],'done')
