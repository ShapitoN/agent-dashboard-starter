"""Provider-reported token counts. Missing measurements stay unknown, never zero."""
import contextlib,contextvars,json
REPORTER=contextvars.ContextVar('agentdesk_reporter',default=None)
FIELDS=('input','output','cache_read','cache_write')

def usage(text):
 totals=dict.fromkeys(FIELDS,0);known=False;final=None;messages={};model=None;agents=[]
 for line in text.splitlines():
  try:e=json.loads(line)
  except (ValueError,TypeError):continue
  if e.get('type')=='system' and e.get('subtype')=='init':model=e.get('model')
  if e.get('type')=='assistant':
   m=e.get('message',{});u=m.get('usage')
   if u:messages[m.get('id',str(len(messages)))]=u
   for c in m.get('content',[]):
    if c.get('type')=='tool_use' and c.get('name') in ('Agent','Task'):
     name=(c.get('input') or {}).get('subagent_type')
     if name and name not in agents:agents.append(name)
  if e.get('type')=='result':
   if e.get('modelUsage'):
    final=dict.fromkeys(FIELDS,0)
    for v in e['modelUsage'].values():
     for key,source in [('input','inputTokens'),('output','outputTokens'),('cache_read','cacheReadInputTokens'),('cache_write','cacheCreationInputTokens')]:final[key]+=int(v.get(source,0))
   elif e.get('usage'):final=normalize(e['usage'])
  if e.get('type')=='turn.completed' and e.get('usage'):
   u=e['usage'];known=True
   # Codex input_tokens already includes cached_input_tokens; avoid counting it twice.
   cached=int(u.get('cached_input_tokens',0));totals['input']+=max(0,int(u.get('input_tokens',0))-cached);totals['cache_read']+=cached;totals['output']+=int(u.get('output_tokens',0))
 if final is not None:totals=final;known=True
 elif messages:
  for item in messages.values():
   for key,value in normalize(item).items():totals[key]+=value
  known=True
 return {'known':known,**totals,'total':sum(totals.values()) if known else None,'actual_model':model,'agents':agents}

def normalize(v):return {k:int(v.get(source,0)) for k,source in [('input','input_tokens'),('output','output_tokens'),('cache_read','cache_read_input_tokens'),('cache_write','cache_creation_input_tokens')]}

@contextlib.contextmanager
def reporting(callback):
 token=REPORTER.set(callback)
 try:yield
 finally:REPORTER.reset(token)

def report(text,run_dir,provider,status):
 data=usage(text);callback=REPORTER.get()
 if callback:
  try:callback({'run_id':str(run_dir),'provider':provider,'status':status,'usage':data,'actual_model':data.get('actual_model'),'agents':data['agents']})
  except Exception:
   import logging
   logging.getLogger('agentdesk').exception('Telemetry update failed; execution continues')
 return data

def summarize(ledger):
 known=[x['usage'] for x in ledger.values() if x.get('usage',{}).get('known')]
 values={key:sum(x.get(key,0) for x in known) for key in FIELDS}
 return dict(values,total=sum(values.values()) if known else None,known=bool(known),runs=len(ledger))
