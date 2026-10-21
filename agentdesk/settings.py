"""Local configuration; never put credentials in the repository."""
import os,shutil
from pathlib import Path
WORKSPACE=Path(os.environ.get('AGENTDESK_WORKSPACE',str(Path.home()/'agent-workspace'))).expanduser()
BASE=Path(os.environ.get('AGENTDESK_BASE',str(WORKSPACE/'ClaudeAIAgent'))).expanduser()
DASHBOARD_URL=os.environ.get('AGENTDESK_DASHBOARD_URL','https://YOUR_FIREBASE_PROJECT.web.app')
CODEX_BIN=os.environ.get('AGENTDESK_CODEX_BIN',shutil.which('codex') or 'codex')
CONTEXT=Path(os.environ.get('AGENTDESK_CONTEXT',str(WORKSPACE/'CONTEXT.md'))).expanduser()
RESULTS=Path(os.environ.get('AGENTDESK_RESULTS',str(WORKSPACE/'AgentDesk-results'))).expanduser()
