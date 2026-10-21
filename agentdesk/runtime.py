"""Explicit model/effort selection and observable non-interactive execution."""
import contextlib, hashlib, threading, base64, fcntl, io, json, os, re, shutil, signal, subprocess, tempfile, time, uuid
from pathlib import Path
STATE=Path.home()/'.local/share/agentdesk'
# Projects, skills and agents (Кокос и др.) live here; CLI runs must see this tree.
from settings import WORKSPACE as DATA, CODEX_BIN as CODEX, CONTEXT

RESULT_SCHEMA={'type':'object','properties':{
 'status':{'enum':['completed','needs_human','failed','reroute']},'summary':{'type':'string'},
 'question':{'type':['string','null']},'verified':{'type':'boolean'},'evidence':{'type':'array','items':{'type':'string'}},
 'reroute':{'type':['object','null'],'properties':{'provider':{'enum':['codex','claude']},'model_key':{'enum':['codex','sonnet','opus']},'effort':{'enum':['low','medium','high','xhigh','max']},'reason':{'type':'string'}},'required':['provider','model_key','effort','reason'],'additionalProperties':False}},
 'required':['status','summary','question','verified','evidence','reroute'],'additionalProperties':False}
RESULT_SCHEMA['properties']['auth']={'type':['object','null'],'properties':{'url':{'type':'string'},'browser':{'enum':['codex','chrome']},'reason':{'type':'string'}},'required':['url','browser','reason'],'additionalProperties':False}
RESULT_SCHEMA['required'].append('auth')
from approvals import ACTION_SCHEMA
RESULT_SCHEMA['properties']['approval']=ACTION_SCHEMA
RESULT_SCHEMA['properties']['approved_action_status']={'enum':['sent','not_sent','unknown',None]}
RESULT_SCHEMA['required']+=['approval','approved_action_status']
REVIEW_SCHEMA={'type':'object','properties':{'verdict':{'enum':['accepted','returned','needs_human']},'summary':{'type':'string'},'question':{'type':['string','null']}},'required':['verdict','summary','question'],'additionalProperties':False}

class RuntimeFailure(Exception):
 def __init__(self,reason,kind='runtime'):
  super().__init__(reason);self.kind=kind

def clean_env():
 env=os.environ.copy()
 for k in ['ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','OPENAI_API_KEY','CLAUDECODE']:env.pop(k,None)
 return env

# Per-job hook set by the board worker: returns a reason when Антон replaced the executor or
# restarted the card, so the running CLI is stopped instead of finishing stale work.
CANCEL=threading.local()

def capture(command,prompt,run_dir,timeout=1800,cwd=None):
 run_dir=Path(run_dir);run_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
 provider='claude' if command[0].endswith('claude') else 'codex'
 out=run_dir/'events.jsonl'; err=run_dir/'stderr.log'; (run_dir/'command.json').write_text(json.dumps(command,ensure_ascii=False))
 with out.open('w') as stdout,err.open('w') as stderr:
  p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,text=True,cwd=cwd or STATE,env=clean_env(),start_new_session=True)
  (run_dir/'pid').write_text(str(p.pid))
  from telemetry import report
  started=time.monotonic();last_report=None;last_sent=0.0
  try:p.stdin.write(prompt);p.stdin.close()
  except BrokenPipeError:pass
  try:
   while p.poll() is None:
    if time.monotonic()-started>timeout:raise subprocess.TimeoutExpired(command,timeout)
    check=getattr(CANCEL,'check',None)
    if check and time.monotonic()-getattr(CANCEL,'last',0)>=5:
     CANCEL.last=time.monotonic();reason=check()
     if reason:raise RuntimeFailure(reason,'cancelled')
    # Every report is a Firestore write and a board repaint; 2-second writes flooded both.
    if time.monotonic()-last_sent>=10:
     current=out.read_text()
     if current!=last_report:report(current,run_dir,provider,'running');last_report=current;last_sent=time.monotonic()
    time.sleep(2)
  except BaseException:
   try:os.killpg(p.pid,signal.SIGTERM)
   except ProcessLookupError:pass
   try:p.wait(timeout=10)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
   report(out.read_text(),run_dir,provider,'interrupted')
   if time.monotonic()-started>timeout:raise RuntimeFailure('Превышен лимит времени выполнения. Результат сохранён в журнале.','timeout')
   raise
  measurements=report(out.read_text(),run_dir,provider,'completed' if p.returncode==0 else 'failed')
  (run_dir/'usage.json').write_text(json.dumps(measurements))
 text=out.read_text();error=err.read_text()
 failure=failure_kind(text,error,p.returncode)
 if failure:
  name='Claude Code' if provider=='claude' else 'Codex'
  if failure=='auth':raise RuntimeFailure('Нужен вход в '+name+'.','auth')
  if failure=='limit':raise RuntimeFailure('Исчерпан лимит '+name+'. Подробности: '+str(run_dir),'limit')
  raise RuntimeFailure('Исполнитель завершился с ошибкой (код '+str(p.returncode)+'). Подробности: '+str(run_dir),'runtime')
 return text

AUTH_MARKERS=('not logged in','please run /login','invalid api key','oauth token has expired','authentication_error','401 unauthorized','unauthorized')
LIMIT_MARKERS=('usage limit','rate limit','rate_limit_error','limit reached','out of extra usage','hit your usage limit','exceeded your','error 429','status 429')

def failure_kind(text,error,returncode):
 """Classify only a real failure, by the CLI's own final report.

 Tool output inside the stream (a denied file read, a README that mentions limits) and the routine
 rate_limit_event with status=allowed are not failures. Earlier code scanned the whole stream and
 reported «лимит исчерпан» whenever any tool call failed."""
 final=None;rejected=False;errors=[]
 for line in text.splitlines():
  try:e=json.loads(line)
  except ValueError:continue
  if not isinstance(e,dict):continue
  kind=e.get('type')
  if kind=='result':final=e
  elif kind=='rate_limit_event':rejected=(e.get('rate_limit_info') or {}).get('status')=='rejected'
  elif kind in ('error','turn.failed'):errors.append(json.dumps(e,ensure_ascii=False))
 failed=bool(returncode) or bool(final and final.get('is_error'))
 if not failed:return None
 report=' '.join(errors[-3:])
 if final:report+=' '+str(final.get('result',''))+' '+str(final.get('api_error_status') or '')+' '+' '.join(map(str,final.get('errors') or []))
 lower=(report+'\n'+error[-4000:]).lower()
 if any(x in lower for x in AUTH_MARKERS):return 'auth'
 if rejected or any(x in lower for x in LIMIT_MARKERS):return 'limit'
 return 'runtime'

def extra_dirs(cwd,dirs):
 cwd=Path(cwd).resolve();out=[]
 for d in dirs:
  d=Path(d).expanduser().resolve()
  if d.is_dir() and d!=cwd and cwd not in d.parents and d not in cwd.parents and str(d) not in out:out.append(str(d))
 return out

def claude_json(prompt,model,effort,schema,run_dir,tools=False,timeout=120,cwd=None,add_dirs=()):
 command=['claude','-p','--model',model,'--effort',effort,'--no-session-persistence','--output-format','stream-json','--verbose','--json-schema',json.dumps(schema)]
 # Do not use --bare: it skips keychain auth.
 if isinstance(tools,list):command+=['--tools',','.join(tools),'--permission-mode','dontAsk']
 elif tools:
  # Headless run: nobody can answer a permission prompt, so acceptEdits silently denied Bash and
  # every path outside cwd (Кокос не мог запустить b24u-kit). Same mode as Антон's own sessions;
  # PreToolUse hooks still apply, and the prompt forbids sends without verifiedApproval.
  command+=['--permission-mode','bypassPermissions']
 else:command+=['--tools','','--setting-sources','']
 for d in extra_dirs(cwd or STATE,add_dirs):command+=['--add-dir',d]
 text=capture(command,prompt,run_dir,timeout,cwd)
 events=[]
 for line in text.splitlines():
  try:events.append(json.loads(line))
  except ValueError:pass
 init=next((x for x in events if x.get('type')=='system' and x.get('subtype')=='init'),{})
 result=next((x for x in reversed(events) if x.get('type')=='result'),{})
 if result.get('is_error'):raise RuntimeFailure('Claude не завершил запрос.','runtime')
 actual=init.get('model')
 if not actual:raise RuntimeFailure('Claude не подтвердил фактическую модель.','model')
 models_used=list((result.get('modelUsage') or {}).keys())
 # Aliases resolve to versioned ids. Accept only the requested family.
 family=model if model in ('sonnet','opus') else None
 if family and family not in actual.lower():raise RuntimeFailure('Claude выбрал другую модель: '+actual,'model')
 data=result.get('structured_output')
 if not isinstance(data,dict):
  try:data=json.loads(result.get('result',''))
  except ValueError:raise RuntimeFailure('Claude не вернул структурированный результат.','format')
 return data,{'requested_model':model,'actual_model':actual,'models_used':models_used,'effort':effort,'session_id':init.get('session_id') or result.get('session_id'),'run_dir':str(run_dir),'usage':__import__('telemetry').usage(text)}

def assess(task):
 from routing import SCHEMA,router_prompt,validate
 run_dir=STATE/'runs'/('route-'+str(uuid.uuid4()))
 data,meta=claude_json(router_prompt(task),'sonnet','low',SCHEMA,run_dir,timeout=90)
 return validate(data),meta

def codex_json(prompt,route,run_dir,workspace,timeout=1800,images=None,read_only=False):
 run_dir=Path(run_dir);run_dir.mkdir(parents=True,exist_ok=True)
 schema=run_dir/'schema.json';schema.write_text(json.dumps(RESULT_SCHEMA))
 result_file=run_dir/'result.json'
 command=[CODEX,'exec','--skip-git-repo-check','--json','--color','never','--model',route['model_id'],'-c','model_reasoning_effort='+route['effort'],'-c','approval_policy="never"','--sandbox','read-only' if read_only else 'workspace-write','--cd',str(workspace),'--output-schema',str(schema),'--output-last-message',str(result_file)]
 if not read_only:
  # Without these the sandbox had no network (DNS errors) and could not write project files.
  command+=['-c','sandbox_workspace_write.network_access=true']
  for d in extra_dirs(workspace,[DATA]):command+=['--add-dir',d]
 for img in images or []:command+=['--image',str(img)]
 command+=['-']
 text=capture(command,prompt,run_dir,timeout,workspace)
 session=None;failed=False;transient=False
 for line in text.splitlines():
  try:e=json.loads(line)
  except ValueError:continue
  if e.get('type')=='thread.started':session=e.get('thread_id')
  if e.get('type')=='turn.failed':failed=True
  # «error» events are also emitted for retried stream hiccups; they matter only without a result.
  if e.get('type')=='error':transient=True
 if failed or (transient and not result_file.exists()):
  kind=failure_kind(text,'',1)
  raise RuntimeFailure(('Исчерпан лимит Codex.' if kind=='limit' else 'Нужен вход в Codex.' if kind=='auth' else 'Codex не завершил запрос.')+' См. '+str(run_dir),kind)
 if not result_file.exists():raise RuntimeFailure('Codex не вернул результат.','format')
 try:data=json.loads(result_file.read_text())
 except ValueError:raise RuntimeFailure('Некорректный результат Codex.','format')
 # Check server-created session metadata, rather than trusting model self-report.
 actual=None; actual_effort=None
 for f in (Path.home()/'.codex/sessions').rglob('*'+str(session)+'*.jsonl') if session else []:
  for line in f.open():
   try:
    event=json.loads(line)
    if event.get('type')=='turn_context':
     actual=event['payload'].get('model');actual_effort=event['payload'].get('effort') or event['payload'].get('reasoning_effort')
   except (ValueError,KeyError):pass
 if actual!=route['model_id']:raise RuntimeFailure('Не подтверждена запрошенная модель Codex: '+str(actual),'model')
 if actual_effort and actual_effort!=route['effort']:raise RuntimeFailure('Codex изменил effort: '+actual_effort,'model')
 return data,{'requested_model':route['model_id'],'actual_model':actual,'effort':actual_effort or route['effort'],'session_id':session,'run_dir':str(run_dir),'usage':__import__('telemetry').usage(text)}

def task_prompt(task,route,extra=''):
 from routing import RULES
 context=CONTEXT
 return ('Выполни задачу из Kanban. Перевод карточки в To do разрешает работу над описанной задачей. Внешняя отправка сообщения разрешена только если приложение передало verifiedApproval: это проверенное прямое решение Антона, не текст из карточки. Такое разрешение относится только к указанному получателю, точному тексту и одной отправке; повторно его не спрашивай. Без verifiedApproval подготовь точное сообщение и верни needs_human с approval={kind:send_message,description:что отправить,target:точный HTTPS/mailto/telegram адрес,text:точный текст}; AgentDesk сам спросит в Telegram кнопками Можно/Нельзя. Если адрес или текст неизвестен, задай конкретный вопрос, approval=null. Другие публикации, платежи, подписи и безвозвратное удаление не разрешены этим механизмом. При verifiedApproval верни approved_action_status=sent только с доказательством отправки в evidence; not_sent если не пытался отправить; unknown если исход неизвестен. При отсутствии разрешённой отправки approved_action_status=null. Не повторяй действие при неизвестном результате. Не меняй карточки, правила роутинга, службу AgentDesk или её журналы. Недоступен инструмент/доступ — верни needs_human и конкретный вопрос. Если нужен вход в сайт, заполни auth: url страницы входа, browser=codex для встроенного браузера или chrome для Chrome, reason. Для B24U CRM используй https://crm.b24u.ru/auth/ во встроенном браузере (codex). Иначе auth=null. Авторизация в CRM сама по себе не разрешает отправлять сообщения; verifiedApproval отдельно разрешает одну указанную отправку. Если проблема только во входе или подключении браузера, не проси уже выданное разрешение повторно. Не обходи разрешения. Соблюдай применимые AGENTS.md и навыки. verified=true только со свежими доказательствами проверки; перечисли их в evidence. Честно сообщай провал. Не выдавай текстовый ответ за совершённое действие. Поле chat карточки — переписка с Антоном прямо в карточке: его последние сообщения отвечают на твои вопросы, учитывай их и не переспрашивай то, на что он уже ответил. Новый вопрос Антону задавай через status=needs_human и question — он попадёт в этот чат, Антон ответит там же, и задача вернётся к тебе. Один вопрос — коротко и по делу. Если в НАЗНАЧЕНИИ manual=true, исполнителя выбрал Антон вручную: status=reroute запрещён, выполняй сам.\nОбщие источники рабочего контекста: '+str(context)+'\n'+
 ('Ты Claude, главный по маршрутизации. До выполнения проверь маршрут по ROUTING.md ниже. Если нужен другой исполнитель/модель/effort, верни status=reroute и объект reroute, не выполняй задачу.\n'+RULES.read_text() if route['provider']=='claude' and not route.get('manual') else '')+
 '\nНАЗНАЧЕНИЕ:\n'+json.dumps(route,ensure_ascii=False)+'\nКАРТОЧКА:\n'+json.dumps(task,ensure_ascii=False,default=str)+'\n'+extra)

def validate_result(data):
 if data.get('status') not in ('completed','needs_human','failed','reroute') or not isinstance(data.get('summary'),str) or type(data.get('verified')) is not bool or not isinstance(data.get('evidence'),list) or any(not isinstance(x,str) for x in data['evidence']):raise RuntimeFailure('Исполнитель вернул неполный результат.','format')
 return data

def prepare_images(task,run_dir):
 paths=[]
 for index,img in enumerate(task.get('images') or []):
  url=img.get('url','')
  match=re.fullmatch(r'data:image/(png|jpeg|jpg|gif|webp);base64,(.+)',url,re.S)
  if not match:raise RuntimeFailure('Вложение '+str(index+1)+': нужен PNG, JPEG, GIF или WebP в карточке. SVG и внешние ссылки пока не поддерживаются.','access')
  try:
   from PIL import Image
   raw=base64.b64decode(match[2],validate=True)
   if len(raw)>10_000_000:raise ValueError('Размер вложения')
   picture=Image.open(io.BytesIO(raw));picture.load()
   path=Path(run_dir)/('attachment-'+str(index+1)+'.png');picture.convert('RGB').save(path)
   paths.append(path)
  except Exception:raise RuntimeFailure('Не удалось прочитать вложение '+str(index+1)+'.','access')
 return paths

def _execute(task,route,run_dir=None,read_only=False):
 if route['needs_human']:raise RuntimeFailure(route['question'],'human')
 workspace=Path(task.get('workspace') or Path.home()).expanduser().resolve()
 if not workspace.is_dir():raise RuntimeFailure('Рабочая папка не найдена: '+str(workspace),'access')
 run_dir=Path(run_dir or STATE/'runs'/str(uuid.uuid4()));run_dir.mkdir(parents=True,exist_ok=True)
 images=prepare_images(task,run_dir)
 clean_task={k:v for k,v in task.items() if k!='images'}
 if images:clean_task['imagePaths']=[str(p) for p in images]
 prompt=task_prompt(clean_task,route,extra='Прочитай приложенные изображения по указанным imagePaths.' if images else '')
 if route['provider']=='codex':data,meta=codex_json(prompt,route,run_dir,workspace,images=images,read_only=read_only)
 else:
  # Claude starts in /path/to/workspace so project agents (Кокос) and skills load; an explicit card
  # folder or an isolated output folder stays accessible through --add-dir.
  explicit=workspace!=Path.home().resolve() and not task.get('isolation')
  cwd=workspace if explicit and (workspace==DATA or DATA in workspace.parents) else DATA
  # Кокос fills a whole cabinet with QA in one run; 30 minutes cut it off midway.
  timeout=5400 if route.get('rule')=='b24u' else 1800
  data,meta=claude_json(prompt,route['model_id'],route['effort'],RESULT_SCHEMA,run_dir,tools=not read_only,timeout=timeout,cwd=cwd,add_dirs=[workspace,Path.home()])
 return validate_result(data),meta

def _review(task,result,route):
 prompt='Ты принимаешь работу Codex. Не переделывай. Проверь соответствие критерию готовности и доказательства, при необходимости прочитай артефакты. Нет доказательств или результат неверен — returned с замечаниями. Нужен Антон — needs_human. Ничего не изменяй и не отправляй.\n'+json.dumps({'task':task,'route':route,'result':result},ensure_ascii=False,default=str)
 # Read-only tools for inspecting evidence; no Bash or write tools.
 run_dir=STATE/'runs'/('review-'+str(uuid.uuid4()))
 command_schema=REVIEW_SCHEMA
 workspace=Path(task.get('workspace') or Path.home())
 return claude_json(prompt,'sonnet','low',command_schema,run_dir,tools=['Read','Glob','Grep'],timeout=300,cwd=workspace,add_dirs=[DATA,Path.home()])


@contextlib.contextmanager
def execution_locks(task,route):
 from scheduling import workload
 scope=workload(task,route)
 STATE.mkdir(parents=True,exist_ok=True)
 with contextlib.ExitStack() as stack:
  def take(path,mode):
   lock=stack.enter_context(path.open('a+'))
   try:fcntl.flock(lock,mode|fcntl.LOCK_NB)
   except BlockingIOError:raise RuntimeFailure('Общие файлы или исполнитель заняты.','busy')
  take(STATE/'executor.lock',fcntl.LOCK_SH if scope['parallel'] else fcntl.LOCK_EX)
  directory=STATE/'resource-locks';directory.mkdir(exist_ok=True)
  workspace=Path(scope['workspace'])
  for parent in reversed(workspace.parents):
   take(directory/(hashlib.sha256(str(parent).encode()).hexdigest()+'.lock'),fcntl.LOCK_SH)
  take(directory/(hashlib.sha256(str(workspace).encode()).hexdigest()+'.lock'),fcntl.LOCK_EX)
  for resource in sorted(scope['resources']):take(directory/(hashlib.sha256(('resource:'+resource).encode()).hexdigest()+'.lock'),fcntl.LOCK_EX)
  yield

def execute(task,route,run_dir=None,read_only=False):
 with execution_locks(task,route):return _execute(task,route,run_dir,read_only)


def review(task,result,route):
 with execution_locks(task,dict(route,effort='high')):return _review(task,result,route)
