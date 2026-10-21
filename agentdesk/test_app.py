import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
import app

class ApiTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.old=app.STATE;app.STATE=Path(cls.temp.name);app.db().close()
  cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=cls.server.serve_forever,daemon=True).start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();app.STATE=cls.old;cls.temp.cleanup()
 def request(self,path,data=None,token=True):
  req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers={'X-AgentDesk-Token':app.TOKEN if token else ''})
  with urllib.request.urlopen(req) as r:return json.load(r)
 def test_auth(self):
  with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/route',{'prompt':'Тест'},False)
  self.assertEqual(e.exception.code,403);e.exception.close()
 def test_empty(self):
  with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/route',{'prompt':' '})
  self.assertEqual(e.exception.code,400);e.exception.close()
 def test_history(self):
  item=self.request('/api/route',{'prompt':'Сделай сводку по конкурентам'})
  self.assertIn(item['id'],[x['id'] for x in self.request('/api/history')]);self.assertEqual(item['route']['model_id'],'gpt-6.1-sol')
 def test_launch_not_clipboard(self):
  item=self.request('/api/route',{'prompt':'Сделай сводку по конкурентам'})
  with patch('app.run_local') as runner:
   answer=self.request('/api/open',{'id':item['id']});self.assertEqual(answer['status'],'running')
  row=self.request('/api/result/'+item['id']);self.assertEqual(row['status'],'running')
  with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/open',{'id':item['id']})
  self.assertEqual(e.exception.code,400);e.exception.close()
 def test_human_gate(self):
  item=self.request('/api/route',{'prompt':'Опубликуй пост в канале'})
  with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/open',{'id':item['id']})
  self.assertEqual(e.exception.code,400);e.exception.close()
 def test_wrong_model(self):
  with self.assertRaises(ValueError):app.route('Задача','codex','Opus')
if __name__=='__main__':unittest.main()
