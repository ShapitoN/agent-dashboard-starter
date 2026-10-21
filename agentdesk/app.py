#!/usr/bin/env python3
"""AgentDesk: local desktop dispatcher. Kanban execution with explicit models."""
import argparse, json, os, re, secrets, sqlite3, subprocess, threading, uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
STATE = Path.home()/'.local/share/agentdesk'
STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(STATE, 0o700)
TOKEN = secrets.token_urlsafe(32)
MODELS = {'codex': {'id':'gpt-6.1-sol','label':'GPT-6.1 Sol'}, 'sonnet': {'id':'sonnet','label':'Sonnet'}, 'opus': {'id':'opus','label':'Opus'}}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def db():
    c = sqlite3.connect(STATE/'history.sqlite3',factory=ClosingConnection)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, created TEXT, prompt TEXT, route TEXT, status TEXT)')
    if 'result' not in [r[1] for r in c.execute('PRAGMA table_info(requests)')]: c.execute('ALTER TABLE requests ADD COLUMN result TEXT')
    c.commit()
    return c

# Efforts each executor actually accepts in the board's manual picker.
MANUAL_EFFORTS = {'sonnet':['low','medium','high','max'], 'opus':['low','medium','high','xhigh','max'], 'codex':['low','medium']}

def apply_manual(route, task):
    """Антон's executor choice on the card wins over the router, escalations and reroutes."""
    m=task.get('manualExecutor') or {}
    key=m.get('model_key')
    if not route or key not in MANUAL_EFFORTS: return route
    effort=m.get('effort') if m.get('effort') in MANUAL_EFFORTS[key] else (route.get('effort') if route.get('effort') in MANUAL_EFFORTS[key] else MANUAL_EFFORTS[key][-1])
    reason=route.get('reason','')
    if not reason.startswith('Исполнителя выбрал Антон'): reason='Исполнителя выбрал Антон. '+reason
    return dict(route,provider='codex' if key=='codex' else 'claude',model_key=key,model=MODELS[key]['label'],model_id=MODELS[key]['id'],effort=effort,complexity=effort,method='Антон вручную',manual=True,reason=reason[:240])

def choose_route(task, provider='auto', model='auto', semantic=True):
    from routing import fallback,decide,manual_label
    from runtime import assess,RuntimeFailure
    manual = None
    if provider not in ('auto','codex','claude'): raise ValueError('Неизвестный исполнитель')
    if provider!='auto': manual='codex' if provider=='codex' else 'claude'
    if model!='auto':
        mapping={'Sonnet':'sonnet','Opus':'opus','GPT-6.1 Sol':'codex'}
        if model not in mapping: raise ValueError('Неизвестная модель')
        manual=mapping[model]
        if provider=='codex' and manual!='codex' or provider=='claude' and manual=='codex': raise ValueError('Модель не соответствует исполнителю')
    method='Без Sonnet'; assessment=fallback(task); meta=None
    if semantic:
        try:
            router_task={k:task.get(k) for k in ['title','description','tags','codexFailures','codexUnable','approved_action','chat']}
            router_task['attachments_count']=len(task.get('images') or [])
            assessment,meta=assess(router_task); method='Sonnet'
        except RuntimeFailure as e:
            if e.kind not in ('auth','limit'): raise ValueError(str(e))
    result=decide(task,assessment,MODELS,method,manual)
    from approvals import permission_question
    permission_only=permission_question(result['question']) or ((result['question'] or '').startswith('Подтверди внешнее действие') and 'отправ' in (task.get('title','')+' '+task.get('description','')).lower())
    if result['needs_human'] and result['confidence']=='high' and permission_only:
        result.update(needs_human=False,question=None,approval_preparation=not bool(task.get('approved_action')))
    if meta:result['router_runtime']=meta
    return apply_manual(result,task)

def route(prompt,provider='auto',model='auto'):
    return choose_route({'title':prompt,'description':''},provider,model,False)

def semantic_route(prompt):
    return choose_route({'title':prompt,'description':''})


def make_link(provider,prompt):
    if provider=='codex': return 'codex://threads/new?'+urlencode({'prompt':prompt})
    # Installed Claude desktop exposes new-chat route; prompt/model parameters are unverified.
    return 'claude://claude.ai/new?surface=chat&source=desktop_action'

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,value,code=200):
        data=json.dumps(value,ensure_ascii=False).encode(); self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}': return self.reply({'error':'Invalid host'},403)
        if self.path=='/':
            data=(ROOT/'index.html').read_text().replace('__TOKEN__',TOKEN).encode()
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('X-Frame-Options','DENY'); self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'unsafe-inline'; script-src 'nonce-"+TOKEN+"'; img-src 'self' data:; frame-ancestors 'none'"); self.end_headers(); self.wfile.write(data)
        elif self.path=='/api/history' and self.headers.get('X-AgentDesk-Token')==TOKEN:
            with db() as c: rows=c.execute('SELECT * FROM requests ORDER BY created DESC LIMIT 80').fetchall()
            self.reply([dict(row,route=json.loads(row['route'])) for row in rows])
        elif self.path.startswith('/api/result/') and self.headers.get('X-AgentDesk-Token')==TOKEN:
            ident=self.path.rsplit('/',1)[-1]
            with db() as c: row=c.execute('SELECT * FROM requests WHERE id=?',(ident,)).fetchone()
            self.reply(dict(row) if row else {'error':'Not found'},200 if row else 404)
        else: self.reply({'error':'Not found'},404)
    def do_POST(self):
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}' or self.headers.get('X-AgentDesk-Token')!=TOKEN: return self.reply({'error':'Forbidden'},403)
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<150000: raise ValueError('Запрос слишком большой')
            body=json.loads(self.rfile.read(size))
            if self.path=='/api/route':
                prompt=body.get('prompt','').strip()
                if not prompt or len(prompt)>24000: raise ValueError('Введи запрос до 24 000 символов')
                provider=body.get('provider','auto'); model=body.get('model','auto')
                r=choose_route({'title':prompt,'description':''},provider,model,bool(body.get('semantic')))
                ident=str(uuid.uuid4())
                with db() as c: c.execute('INSERT INTO requests (id,created,prompt,route,status) VALUES (?,?,?,?,?)',(ident,datetime.now().astimezone().isoformat(),prompt,json.dumps(r,ensure_ascii=False),'prepared'))
                return self.reply({'id':ident,'prompt':prompt,'route':r,'status':'prepared'})
            if self.path=='/api/open':
                with db() as c:
                    row=c.execute('SELECT * FROM requests WHERE id=?',(body.get('id'),)).fetchone()
                    if row is None: raise ValueError('Запрос не найден')
                    r=json.loads(row['route'])
                    if not r.get('model_id'):raise ValueError('Переназначь запрос по новым правилам ROUTING.md')
                    if r['needs_human']:raise ValueError(r['question'])
                    changed=c.execute("UPDATE requests SET status='running' WHERE id=? AND status='prepared'",(row['id'],)).rowcount
                    if not changed:raise ValueError('Запрос уже запускался. Для нового запуска создай новый запрос.')
                threading.Thread(target=run_local,args=(dict(row),),daemon=True).start()
                return self.reply({'status':'running','message':f"Запущен {r['model']} · {r['effort']}. Модель передана исполнителю явно."})
            self.reply({'error':'Not found'},404)
        except subprocess.TimeoutExpired: self.reply({'error':'Превышено время ожидания. Запрос сохранён в поле ввода.'},504)
        except (ValueError,TypeError,OSError,json.JSONDecodeError) as e: self.reply({'error':str(e)},400)
        except Exception: self.reply({'error':'Внутренняя ошибка. Запрос остаётся в поле ввода.'},500)

def run_local(row):
    from runtime import execute,RuntimeFailure
    try:
        result,meta=execute({'title':row['prompt'],'description':''},json.loads(row['route']))
        status='completed' if result['status']=='completed' else 'issue'
        output={'result':result,'runtime':meta}
    except Exception as e:status='issue';output={'error':str(e)}
    with db() as c:c.execute('UPDATE requests SET status=?,result=? WHERE id=?',(status,json.dumps(output,ensure_ascii=False),row['id']))


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--no-window',action='store_true'); parser.add_argument('--port',type=int,default=0); args=parser.parse_args()
    db().close()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    url=f'http://127.0.0.1:{server.server_port}'
    (STATE/'address').write_text(url)
    (STATE/'server.pid').write_text(str(os.getpid()))
    print(url,flush=True)
    if not args.no_window:
        subprocess.Popen(['google-chrome','--app='+url,'--class=AgentDesk','--window-size=1180,840'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    server.serve_forever()
if __name__=='__main__': main()
