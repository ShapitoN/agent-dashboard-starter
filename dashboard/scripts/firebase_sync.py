#!/usr/bin/env python3
"""
Синхронизирует данные ClaudeAIAgent → Firestore.
Запускается из cron_runner или вручную.

Установка зависимостей:
  pip install firebase-admin

Требует файл serviceAccountKey.json (скачать из Firebase Console →
Project Settings → Service accounts → Generate new private key).
Положи его в: /path/to/workspace/ClaudeAIAgent/config/serviceAccountKey.json
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    print("Установи firebase-admin: pip install firebase-admin")
    sys.exit(1)

BASE      = Path("/path/to/workspace/ClaudeAIAgent")
KEY_FILE  = BASE / "config" / "serviceAccountKey.json"
STATE_FILE = BASE / "cron_runner_state.json"
LOG_DIR   = BASE / "logs"
AGENT_DIR = BASE / "AIAgent"

# ── Init Firebase ──────────────────────────────────────────────────────────
if not KEY_FILE.exists():
    print(f"Нет файла ключа: {KEY_FILE}")
    print("Скачай его в Firebase Console → Project Settings → Service accounts")
    sys.exit(1)

if not firebase_admin._apps:
    cred = credentials.Certificate(str(KEY_FILE))
    firebase_admin.initialize_app(cred)

db = firestore.client()

def ts_now():
    return datetime.now(timezone.utc)

# ── Sync cron jobs ─────────────────────────────────────────────────────────
def sync_cron_jobs():
    if not STATE_FILE.exists():
        print("cron_runner_state.json не найден")
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    batch = db.batch()
    count = 0
    for job_name, info in state.items():
        # Skip non-job entries (week markers like "weekly_digest_week")
        if job_name.endswith("_week"):
            continue
        ref = db.collection("cron_jobs").document(job_name)
        if isinstance(info, dict):
            last_run = info.get("last_run")
            data = {
                "lastStatus": info.get("last_rc"),
                "lastRun":    datetime.fromisoformat(last_run).replace(tzinfo=timezone.utc) if last_run else None,
                "nextRun":    datetime.fromisoformat(info["next_run"]).replace(tzinfo=timezone.utc) if info.get("next_run") else None,
                "syncedAt":   ts_now(),
            }
        else:
            # Simple string timestamp format
            try:
                last_run_dt = datetime.fromisoformat(str(info)).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                last_run_dt = None
            data = {
                "lastRun":  last_run_dt,
                "syncedAt": ts_now(),
            }
        batch.set(ref, data, merge=True)
        count += 1

    batch.commit()
    print(f"[cron_jobs] Синхронизировано {count} задач")

# ── Sync markdown files ────────────────────────────────────────────────────
def parse_frontmatter(text):
    """Извлекает YAML-подобный frontmatter из markdown."""
    meta = {}
    content = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            fm = text[3:end].strip()
            content = text[end+3:].strip()
            for line in fm.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
    return meta, content

def sync_markdown_dir(fs_dir: Path, collection: str, extra_fields: dict = None):
    if not fs_dir.exists():
        print(f"  Папка не найдена: {fs_dir}")
        return

    files = list(fs_dir.glob("*.md")) + list(fs_dir.glob("**/*.md"))
    count = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            meta, content = parse_frontmatter(text)

            name = meta.get("name") or meta.get("title") or f.stem
            data = {
                "name":      name,
                "content":   content[:20000],  # Firestore limit
                "fileName":  f.name,
                "updatedAt": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
                "syncedAt":  ts_now(),
            }
            # Merge frontmatter fields
            for k in ("status", "tags", "horizon", "category", "description"):
                if k in meta:
                    v = meta[k]
                    if k == "tags":
                        v = [t.strip() for t in v.strip("[]").split(",") if t.strip()]
                    data[k] = v

            if extra_fields:
                data.update(extra_fields)

            doc_id = re.sub(r"[^a-zA-Z0-9а-яА-Я_-]", "_", f.stem)[:100]
            db.collection(collection).document(doc_id).set(data, merge=True)
            count += 1
        except Exception as e:
            print(f"  Ошибка {f}: {e}")

    print(f"[{collection}] Синхронизировано {count} файлов из {fs_dir}")

# ── Prompt builder with multi-context support ──────────────────────────────
CONFIG_DIR = BASE / "config"

def build_prompt(title: str, description: str, tags: list) -> str:
    parts = []

    # Always include global context
    global_ctx = CONFIG_DIR / "agent_context.md"
    if global_ctx.exists():
        parts.append(global_ctx.read_text(encoding="utf-8"))

    # Tag-based contexts (one tag can map to one context file)
    loaded_contexts = set()
    for tag in tags:
        # Normalize: "Only with human" → skip; "dashboard" → context_dashboard.md
        tag_key = tag.lower().strip().replace(" ", "_")
        if tag_key in ("only_with_human",):
            continue
        ctx_file = CONFIG_DIR / f"context_{tag_key}.md"
        if ctx_file.exists() and tag_key not in loaded_contexts:
            parts.append(ctx_file.read_text(encoding="utf-8"))
            loaded_contexts.add(tag_key)
            print(f"  [context] загружен context_{tag_key}.md")

    # The actual task
    task_block = f"# ЗАДАЧА\n\n**{title}**"
    if description:
        task_block += f"\n\n{description}"
    parts.append(task_block)

    return "\n\n---\n\n".join(parts)


# ── Reverse sync: pick task from "todo" and run claude ────────────────────
def check_and_run_tasks():
    """Only the leased AgentDesk systemd service executes Kanban tasks."""
    print('[tasks] Очередь обрабатывает служба agentdesk-worker.service')


# ── Карта агентов ──────────────────────────────────────────────────────────
GRAPH_DIR = BASE / "agent-graph"

def sync_agent_graph():
    """Пересобирает карту агентов и кладёт её в Firestore (agent_graph/current).
    В открытые файлы хостинга данные не идут: там пути и имена, а Firestore — за входом."""
    try:
        r = subprocess.run([sys.executable, str(GRAPH_DIR / "build.py")],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"[graph] build.py упал: {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
            return
        raw = (GRAPH_DIR / "graph.json").read_text(encoding="utf-8")
        g = json.loads(raw)
        db.collection("agent_graph").document("current").set({
            "json":      json.dumps(g, ensure_ascii=False, separators=(",", ":")),
            "generated": g.get("generated", ""),
            "syncedAt":  ts_now(),
        })
        print(f"[graph] Карта агентов: {len(g['nodes'])} узлов, собрана {g.get('generated')}")
    except Exception as e:
        print(f"[graph] Ошибка: {e}")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print(f"=== Firebase Sync @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # Cron jobs
    sync_cron_jobs()

    # Карта агентов (страница «Граф» на дашборде)
    sync_agent_graph()

    # Projects → AIAgent/03 - Projects/
    projects_dir = AGENT_DIR / "03 - Projects"
    sync_markdown_dir(projects_dir, "projects")

    # Plans → корень AIAgent (Zero Point, System Backlog, etc.)
    for f in AGENT_DIR.glob("*.md"):
        if any(kw in f.name for kw in ("Plan", "Zero Point", "Backlog", "Roadmap", "план", "Plan")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                _, content = parse_frontmatter(text)
                db.collection("plans").document(re.sub(r"[^a-zA-Z0-9а-яА-Я_-]","_",f.stem)[:100]).set({
                    "name":      f.stem,
                    "content":   content[:20000],
                    "fileName":  f.name,
                    "status":    "active",
                    "updatedAt": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
                    "syncedAt":  ts_now(),
                }, merge=True)
            except Exception as e:
                print(f"  Ошибка {f}: {e}")
    print(f"[plans] Синхронизированы планы из {AGENT_DIR}")

    # Skills → AIAgent/02 - Tools/
    tools_dir = AGENT_DIR / "02 - Tools"
    sync_markdown_dir(tools_dir, "skills", {"category": "tool"})

    # Scripts → scripts/*.py (как скиллы)
    scripts_dir = BASE / "scripts"
    if scripts_dir.exists():
        for f in scripts_dir.glob("*.py"):
            try:
                code = f.read_text(encoding="utf-8", errors="ignore")
                db.collection("skills").document(f.stem).set({
                    "name":      f.stem,
                    "content":   f"```python\n{code[:18000]}\n```",
                    "category":  "script",
                    "fileName":  f.name,
                    "updatedAt": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
                    "syncedAt":  ts_now(),
                }, merge=True)
            except Exception as e:
                print(f"  Ошибка {f}: {e}")
        print(f"[skills] Синхронизированы скрипты из {scripts_dir}")

    # Reverse sync: check for new tasks to execute
    check_and_run_tasks()

    print("=== Готово ===")

if __name__ == "__main__":
    main()
