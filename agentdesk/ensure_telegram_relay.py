#!/usr/bin/env python3
"""Narrow, repeatable compatibility patch for the official Telegram channel.

The bot token stays in the existing isolated directory. Only this channel's
getUpdates transport goes through the shared local receiver.
"""
from pathlib import Path
ROOT=Path.home()/'.claude/plugins/cache/claude-plugins-official/telegram'
OLD='const bot = new Bot(TOKEN)'
NEW="const bot = new Bot(TOKEN, STATE_DIR.endsWith('telegram-kiwi') ? { client: { apiRoot: 'http://127.0.0.1:18780' } } : undefined) // AgentDesk shared Telegram receiver"

def apply():
 paths=sorted(ROOT.glob('*/server.ts'))
 if not paths:raise SystemExit('Telegram plugin source not found; channel not started to avoid bot polling conflict')
 for path in paths:
  source=path.read_text()
  if NEW in source:continue
  if source.count(OLD)!=1:raise SystemExit('Telegram plugin changed: relay adaptation needs review')
  backup=path.with_suffix('.ts.pre-agentdesk')
  if not backup.exists():backup.write_text(source)
  path.write_text(source.replace(OLD,NEW))
if __name__=='__main__':apply()
