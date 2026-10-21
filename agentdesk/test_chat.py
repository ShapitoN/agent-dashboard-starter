import copy,unittest
from unittest.mock import patch
import board_worker as worker
from app import apply_manual
from runtime import RuntimeFailure
from test_routing import MemoryStore,decision,assessment

class ChatAndManualTests(unittest.TestCase):
 def setUp(self):
  self.task={'title':'Проверочная задача','description':'Критерий готовности описан','status':'todo','routeNonce':'one'}
  self.done={'status':'completed','summary':'Проверено','question':None,'verified':True,'evidence':['ok'],'reroute':None}
 def run_task(self,store,result,route):
  seen={}
  def fake(task,r,run_dir):seen.update(task=task,route=r);return result,{'actual_model':r['model_id']}
  with patch('board_worker.choose_route',return_value=route),patch('board_worker.execute',side_effect=fake):worker.process(store,'test',copy.deepcopy(store.task))
  return seen
 def test_manual_overrides_router_and_effort(self):
  r=apply_manual(decision(assessment(2,2,2)),{'manualExecutor':{'model_key':'codex','effort':'high'}})
  self.assertEqual((r['provider'],r['model_key'],r['effort'],r['method'],r['manual']),('codex','codex','medium','Антон вручную',True))
  r=apply_manual(decision(assessment(0,0,0)),{'manualExecutor':{'model_key':'opus','effort':'xhigh'}})
  self.assertEqual((r['provider'],r['model_key'],r['effort']),('claude','opus','xhigh'))
  self.assertEqual(apply_manual({'effort':'low'},{}),{'effort':'low'})
 def test_manual_wins_over_planned_escalation(self):
  t=dict(self.task,manualExecutor={'model_key':'sonnet','effort':'high'})
  s=MemoryStore(t);seen=self.run_task(s,self.done,decision(assessment(2,2,2)))
  self.assertEqual(seen['route']['model_key'],'sonnet');self.assertEqual(s.task['status'],'done')
 def test_manual_codex_failure_is_issue_not_opus(self):
  t=dict(self.task,manualExecutor={'model_key':'codex','effort':'low'})
  s=MemoryStore(t);self.run_task(s,dict(self.done,status='failed',summary='Не смог'),decision(assessment(0,0,0)))
  self.assertEqual(s.task['status'],'issue');self.assertIsNone(s.task['agentDesk'].get('nextRoute'))
 def test_question_goes_to_chat_and_chat_reaches_executor(self):
  s=MemoryStore(self.task);self.run_task(s,dict(self.done,status='needs_human',question='Какой город?'),decision(assessment(0,0,0)))
  self.assertEqual(s.task['status'],'issue');self.assertEqual(s.task['chat'][-1]['from'],'agent');self.assertEqual(s.task['chat'][-1]['text'],'Какой город?')
  self.assertFalse(worker.answered(s.task))
  s.task['chat'].append(worker.chat_message('Алматы','anton'));self.assertTrue(worker.answered(s.task))
  s.task['status']='todo';seen=self.run_task(s,self.done,decision(assessment(0,0,0)))
  self.assertEqual(seen['task']['chat'][-1]['text'],'Алматы');self.assertEqual(s.task['status'],'done')
 def test_cancel_leaves_no_result(self):
  s=MemoryStore(self.task)
  with patch('board_worker.choose_route',return_value=decision(assessment(0,0,0))),patch('board_worker.execute',side_effect=RuntimeFailure('Антон сменил исполнителя','cancelled')):worker.process(s,'test',copy.deepcopy(s.task))
  self.assertNotEqual(s.task['status'],'issue');self.assertEqual(s.messages,[]);self.assertEqual(s.events[-1][1],'cancelled')
 def test_manual_reroute_request_is_refused(self):
  t=dict(self.task,manualExecutor={'model_key':'opus','effort':'high'})
  s=MemoryStore(t);self.run_task(s,dict(self.done,status='reroute',reroute={'provider':'codex','model_key':'codex','effort':'low','reason':'x'}),decision(assessment(0,0,0)))
  self.assertEqual(s.task['status'],'issue')
if __name__=='__main__':unittest.main()
