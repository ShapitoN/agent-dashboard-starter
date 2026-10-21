import copy,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from scheduling import *
from runtime import execution_locks,RuntimeFailure
from telemetry import usage,summarize
from auth_bridge import command,auth_request
from test_routing import assessment,decision,MemoryStore
import board_worker

class AdmissionTests(unittest.TestCase):
 def setUp(self):self.route=decision(assessment(0,0,1))
 def task(self,path=None,**kw):return dict(title='Напиши внутреннюю заметку',description='Критерий: три пункта',workspace=path,**kw)
 def test_poll_is_ten_minutes(self):self.assertEqual(POLL_SECONDS,600)
 def test_two_simple_independent(self):
  a=workload(self.task('/tmp/a'),self.route);b=workload(self.task('/tmp/b'),self.route)
  self.assertTrue(admit(b,[a])[0]);self.assertFalse(admit(b,[a,a])[0])
 def test_same_workspace_blocked(self):
  a=workload(self.task('/tmp/a'),self.route);self.assertFalse(admit(a,[a])[0])
 def test_nested_workspace_blocked(self):
  a=workload(self.task('/tmp/a'),self.route);b=workload(self.task('/tmp/a/child'),self.route);self.assertFalse(admit(a,[b])[0])
 def test_shared_resource_blocked(self):
  a=workload(self.task('/tmp/a',resources=['crm']),self.route);b=workload(self.task('/tmp/b',resources=['crm']),self.route);self.assertFalse(admit(a,[b])[0])
 def test_heavy_exclusive(self):
  a=workload(self.task('/tmp/a'),self.route);b=workload(self.task('/tmp/b'),decision(assessment(2,1,1)));self.assertFalse(admit(a,[b])[0]);self.assertFalse(admit(b,[a])[0])
 def test_missing_scores_exclusive(self):self.assertFalse(workload(self.task('/tmp/a'),{})['parallel'])
 def test_isolated_output_created_per_task(self):
  a=workload(self.task(),self.route,'one',Path('/tmp/state'));b=workload(self.task(),self.route,'two',Path('/tmp/state'));self.assertNotEqual(a['workspace'],b['workspace']);self.assertTrue(a['isolated'])
 def test_shared_browser_exclusive(self):self.assertFalse(workload({'title':'Открой браузер','workspace':'/tmp/a'},self.route)['parallel'])
 def test_scheduler_admits_two(self):
  a=self.task('/tmp/a');b=self.task('/tmp/b')
  for x in (a,b):x.update(status='todo',agentDesk={'route':self.route,'routedHash':board_worker.fingerprint(x),'routedNonce':None})
  s=MemoryStore(a)
  s.candidates=lambda status:[('a',a),('b',b)] if status=='todo' else []
  scheduler=board_worker.Scheduler(s)
  with patch.object(scheduler,'launch') as launch,patch('board_worker.machine_capacity',return_value=2):scheduler.tick();self.assertEqual(launch.call_count,2)
 def test_runtime_locks_really_allow_independent_jobs(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.task(str(Path(d)/'a'));b=self.task(str(Path(d)/'b'))
   with execution_locks(a,self.route):
    with execution_locks(b,self.route):pass
    with self.assertRaises(RuntimeFailure):
     with execution_locks(a,self.route):pass
    with self.assertRaises(RuntimeFailure):
     with execution_locks(b,dict(self.route,effort='high')):pass

class UsageTests(unittest.TestCase):
 def test_missing_is_unknown(self):self.assertIsNone(usage('')['total'])
 def test_codex_cache_not_double_counted(self):
  u=usage(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':10}}));self.assertEqual((u['input'],u['cache_read'],u['total']),(20,80,110))
 def test_claude_cumulative_result(self):
  u=usage(json.dumps({'type':'result','modelUsage':{'sonnet':{'inputTokens':10,'outputTokens':20,'cacheReadInputTokens':30,'cacheCreationInputTokens':40}}}));self.assertEqual(u['total'],100)
 def test_stream_and_result_not_counted_twice(self):
  u=usage('\n'.join(json.dumps(x) for x in [{'type':'assistant','message':{'id':'m','usage':{'input_tokens':10,'output_tokens':2}}},{'type':'result','usage':{'input_tokens':10,'output_tokens':20}}]));self.assertEqual(u['total'],30)
 def test_aggregate_multiple_runs(self):
  ledger={str(i):{'usage':usage(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}))} for i in range(3)};self.assertEqual(summarize(ledger)['total'],45)

class AuthTests(unittest.TestCase):
 def test_b24u_opens_correct_browser(self):
  c=command('https://crm.b24u.ru/auth/','codex');self.assertEqual(c[0],'chatgpt');self.assertTrue(c[1].startswith('codex://browser?url='))
 def test_chrome_profile(self):self.assertIn('--profile-directory=Default',command('https://example.com/login','chrome'))
 def test_no_script_or_password_link(self):
  for url in ['javascript:alert(1)','file:///etc/passwd','https://name:password@example.com/','https://example.com/?token=secret']:
   with self.assertRaises(ValueError):command(url,'chrome')
 def test_existing_b24u_issue(self):self.assertEqual(auth_request({'agentNotes':'Подключить браузер с авторизованным кабинетом B24U'})['browser'],'codex')
if __name__=='__main__':unittest.main()

class CaptureTests(unittest.TestCase):
 def test_real_process_reports_usage(self):
  import sys
  from runtime import capture
  from telemetry import reporting
  events=[]
  output={'type':'turn.completed','usage':{'input_tokens':25,'cached_input_tokens':5,'output_tokens':3}}
  with tempfile.TemporaryDirectory() as d:
   with reporting(events.append):capture([sys.executable,'-c','import sys,json;sys.stdin.read();print('+repr(json.dumps(output))+')'],'тест',d,timeout=10)
   self.assertTrue(events);self.assertEqual(events[-1]['usage']['total'],28);self.assertTrue((Path(d)/'usage.json').is_file())
