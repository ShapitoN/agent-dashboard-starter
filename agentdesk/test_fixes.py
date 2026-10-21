"""Регрессии, найденные 20.10.2026: ложный «лимит», блокировка очереди, маршрут B24U."""
import json,unittest
from pathlib import Path
from unittest.mock import patch
from runtime import failure_kind,extra_dirs,DATA
from routing import b24u_task
from test_routing import assessment,decision,MemoryStore
import board_worker

RUN='/nonexistent/example-events.jsonl'

class FailureTests(unittest.TestCase):
 def test_allowed_rate_event_and_failed_tool_are_not_limit(self):
  text='\n'.join(json.dumps(x) for x in [
   {'type':'user','message':{'content':[{'type':'tool_result','is_error':True,'content':'permission denied'}]}},
   {'type':'rate_limit_event','rate_limit_info':{'status':'allowed'}},
   {'type':'result','is_error':False,'result':'ok'}])
  self.assertIsNone(failure_kind(text,'',0))
 def test_real_run_that_was_reported_as_limit(self):
  if not Path(RUN).exists():self.skipTest('Private historical log excluded from export')
  self.assertIsNone(failure_kind(Path(RUN).read_text(),'',0))
 def test_rejected_limit(self):
  text='{"type":"rate_limit_event","rate_limit_info":{"status":"rejected"}}\n{"type":"result","is_error":true,"result":"x"}'
  self.assertEqual(failure_kind(text,'',1),'limit')
 def test_auth(self):self.assertEqual(failure_kind('{"type":"result","is_error":true,"result":"Not logged in · Please run /login"}','',1),'auth')
 def test_words_in_tool_output_ignored_on_failure(self):
  text='{"type":"user","message":{"content":"README: rate limit, authentication"}}\n{"type":"result","is_error":true,"result":"boom"}'
  self.assertEqual(failure_kind(text,'',1),'runtime')
 def test_data_dir_added(self):self.assertEqual(extra_dirs(Path.home(),[DATA]),[str(DATA)] if DATA.is_dir() else [])

class B24UTests(unittest.TestCase):
 def test_profile_goes_to_opus(self):
  route=decision(assessment(0,0,1),{'title':'Создание профиля Example Client','tags':['ExampleWorkspace'],'description':'https://example.com/'})
  self.assertEqual((route['provider'],route['model_key'],route['effort']),('claude','opus','high'))
 def test_research_stays_codex(self):self.assertFalse(b24u_task({'title':'Сводка по 5 конкурентам B24U из интернета'}))

class QueueTests(unittest.TestCase):
 def test_manual_in_progress_card_does_not_block(self):
  route=decision(assessment(0,0,1))
  todo=dict(title='Напиши внутреннюю заметку',description='Критерий: три пункта',workspace='/tmp/a',status='todo')
  todo['agentDesk']={'route':route,'routedHash':board_worker.fingerprint(todo),'routedNonce':None}
  mine=dict(title='Личное дело',status='in-progress')
  finished=dict(title='Старый запуск',status='in-progress',agentDesk={'owner':'gone','phase':'issue'})
  s=MemoryStore(todo);s.owner='me'
  s.candidates=lambda status:{'todo':[('a',todo)],'in-progress':[('m',mine),('f',finished)]}.get(status,[])
  scheduler=board_worker.Scheduler(s)
  with patch.object(scheduler,'launch') as launch,patch('board_worker.machine_capacity',return_value=2):scheduler.tick()
  self.assertEqual(launch.call_count,1)
 def test_foreign_running_card_blocks(self):
  todo=dict(title='x',status='todo')
  s=MemoryStore(todo);s.owner='me'
  s.candidates=lambda status:{'todo':[('a',todo)],'in-progress':[('f',dict(status='in-progress',agentDesk={'owner':'other','phase':'executing'}))]}.get(status,[])
  scheduler=board_worker.Scheduler(s)
  with patch.object(scheduler,'launch') as launch:scheduler.tick()
  self.assertEqual(launch.call_count,0)

class ResultFolderTests(unittest.TestCase):
 def test_visible_named_folder(self):
  from scheduling import workload
  w=workload({'title':'Подготовить подсказку: Дискавери','description':'Критерий: три пункта'},decision(assessment(0,0,1)),'abc',Path('/tmp/state'),Path('/tmp/results'))
  self.assertTrue(w['isolated']);self.assertTrue(w['workspace'].startswith('/tmp/results/'));self.assertIn('Подготовить подсказку Дискавери',w['workspace'])

if __name__=='__main__':unittest.main()
