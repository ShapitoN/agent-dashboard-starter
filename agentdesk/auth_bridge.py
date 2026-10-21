#!/usr/bin/env python3
"""User-initiated browser handoff. Never logs in or resumes a task automatically."""
import json,subprocess,sys
from urllib.parse import urlsplit,parse_qs,urlencode

def validate_url(url):
 u=urlsplit(url)
 if u.scheme!='https' or not u.hostname or u.username or u.password:raise ValueError('Разрешены только HTTPS-страницы входа без учётных данных')
 if any(k.lower() in ('token','access_token','password','secret','authorization','code') for k in parse_qs(u.query)):raise ValueError('Ссылка не должна содержать секреты')
 return url

def command(url,browser):
 validate_url(url)
 if browser=='codex':return ['chatgpt','codex://browser?'+urlencode({'url':url})]
 if browser=='chrome':return ['google-chrome','--profile-directory=Default',url]
 raise ValueError('Неизвестный браузер')

def auth_request(task,result=None):
 data=(result or {}).get('auth') or {}
 if data:
  try:command(data['url'],data['browser']);return {k:str(data.get(k,'')) for k in ('url','browser','reason')}
  except (ValueError,KeyError,TypeError):return None
 notes=((task.get('agentNotes') or '')+' '+(result or {}).get('question','') if (result or {}).get('question') else (task.get('agentNotes') or '')).lower()
 if 'b24u' in notes and any(w in notes for w in ('авториз','вход','браузер','доступ')):
  return {'url':'https://crm.b24u.ru/auth/','browser':'codex','reason':'Войти в B24U CRM через кабинет партнёра'}
 return None

def main():
 uri=urlsplit(sys.argv[1])
 if uri.scheme!='agentdesk' or uri.netloc!='authorize':raise SystemExit('Неверная ссылка AgentDesk')
 query=parse_qs(uri.query)
 subprocess.Popen(command(query.get('url',[''])[0],query.get('browser',[''])[0]),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
if __name__=='__main__':main()
