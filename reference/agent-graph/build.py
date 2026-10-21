#!/usr/bin/env python3
"""
Карта агентов — сборщик графа.

Сам обходит диск и собирает, из чего состоит система: агентов (новое и старое
поколение), навыки, запланированные задачи Claude, скрипты, cron, хуки и
ключевые документы. Связи находит по упоминаниям в текстах файлов. Проблемы
(не запускается, дубль, заглушка, реестр устарел) вычисляет по фактам: что
стоит в установленном cron, когда последний раз писался лог и т.д.

Запуск:
    python3 /path/to/workspace/ClaudeAIAgent/agent-graph/build.py

Результат: agent-graph.html рядом (открывается в браузере без сервера)
и graph.json для отладки.
"""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path("/path/to/workspace")
CA = DATA / "ClaudeAIAgent"
HOME = Path.home()
NEW_AGENTS = DATA / ".claude/agents"
OLD_AGENTS = CA / ".claude/agents"
SKILLS = DATA / ".claude/skills"
SCHED_DIR = HOME / ".claude/scheduled-tasks"
JS = DATA / "job-search"
SCRIPTS = CA / "scripts"
LOGS = CA / "logs"
CTX = CA / "AIAgent/04 - Agent Context"
KIWI = CTX / "Kiwi"
REGISTRY = CTX / "System Agents.md"
SAD = CA / "CLAUDE.md"

NOW = datetime.now()
STALE_DAYS = 14

# ─── Ветки (направления от центра) ────────────────────────────────────────────
BRANCHES = {
    "job":   ("Поиск работы",        "hh, Telegram, почта, журнал, два региона"),
    "kiwi":  ("Уведомления",         "бот Киви: Telegram, хуки, сторож"),
    "solo":  ("Linux",               "Пингвин — система, driftwm, systemd"),
    "learn": ("Обучение",            "курсы SQL и английского"),
    "infra": ("Расписание и служебное", "cron, дашборд, карта агентов"),
    "la":    ("Example Workspace",          "продажи B24U: база, звонки, встречи, разборы, пульт"),
}

# ─── Старое поколение (ClaudeAIAgent/.claude/agents) ──────────────────────────
OLD = {
    # id: (русское имя, ветка, чем заменён в новой системе | None, роль)
    # Остальные агенты первого поколения выведены 02.10.2026 →
    # ClaudeAIAgent/_archive/agents-2026-10-02/
    "kiwi":        ("Киви",      "kiwi",  None, "агент-обёртка над ботом Telegram"),
    "penguin":     ("Пингвин",   "solo",  None, "Linux, driftwm, systemd"),
}
# Старые агенты, которые ещё живые и ничем не заменены (не «старое поколение»,
# а просто другая папка). Остальные старые с веткой job — вытеснены ягодами.
OLD_ALIVE = {"kiwi", "penguin"}
OLD_TOP = {"kiwi", "penguin"}

# Русские основы имён для поиска упоминаний (с заглавной, чтобы не ловить слова)
STEMS = {
    "barbaris": r"Барбарис", "chernika": r"Черник", "ezhevika": r"Ежевик",
    "kizil": r"Кизил", "malina": r"Малин[аеуыой]", "sliva": r"Слив[аеуыой]\b",
    "vishnya": r"Вишн[яиеюй]", "yabloko": r"Яблок[оауе]", "smorodina": r"Смородин",
    "citrus": r"Цитрус", "lemon": r"Лимон", "orange": r"Апельсин",
    "mandarin": r"Мандарин", "grapefruit": r"Грейпфрут", "kiwi": r"Киви",
    "mango": r"Манго", "lichi": r"Личи", "pear": r"Груш[аиеуй]",
    "penguin": r"Пингвин", "peach": r"Персик", "avocado": r"Авокадо",
    "fig": r"Инжир", "pomegranate": r"Гранат", "compote": r"Компот",
    "funduk": r"Фундук", "mindal": r"Миндал", "keshyu": r"Кешью", "fistashka": r"Фисташк",
    "pekan": r"Пекан", "arahis": r"Арахис", "kashtan": r"Каштан",
    "kedr": r"Кедр", "makadamiya": r"Макадами", "gretskiy": r"Грецк",
    "kokos": r"Кокос",
}
# Имена, общие для двух поколений: в новом контексте → новый агент
SHARED = {"yabloko": "apple", "vishnya": "cherry", "sliva": "plum", "kizil": "dogwood"}

# ─── Скрипты: какие и в какую ветку ───────────────────────────────────────────
CA_SCRIPTS = {
    # Скрипты бывшей работы (созвоны, GitLab, раннер, дайджесты) выведены
    # 02.10.2026 → ClaudeAIAgent/_archive/scripts-2026-10-02/
    "kiwi_flush.py": "kiwi", "kiwi_queue.py": "kiwi", "send_telegram.py": "kiwi",
    "check_links.py": "infra",
}
KIWI_SCRIPTS = ["kiwi.py", "kiwi_listener.py", "kiwi_watchdog.py",
                "kiwi_task_done.py", "kiwi_hook.py"]

LA = DATA / "light-audit"
DOCS = [
    # (путь, ветка, подпись)
    (LA / "core/rules.md", "la", "Правила Example Workspace"),
    (LA / "STATE.md", "la", "STATE Example Workspace"),
    (DATA / "dev/la_pult/README.md", "la", "Пульт продавца"),
    (SAD, "center", "Сад — CLAUDE.md"),
    (CTX / "Agent Base Instructions.md", "infra", "Базовые правила агентов"),
    (CTX / "Kiwi Prompt.md", "kiwi", "Промпт Киви"),
    (JS / "README.md", "job", "README поиска работы"),
    (JS / "STATE.md", "job", "STATE — состояние цикла"),
    (JS / "core/candidate.md", "job", "Профиль кандидата"),
    (JS / "core/sending.md", "job", "Правила отправки"),
    (JS / "core/ownership.md", "job", "Кто за что отвечает"),
    (JS / "core/reliability.md", "job", "Надёжность прогона"),
    (JS / "core/token-rules.md", "job", "Экономия токенов"),
    (JS / "regions/kz.md", "job", "Регион: Казахстан"),
    (JS / "regions/ru.md", "job", "Регион: Россия"),
]


def mtime(p):
    try:
        return datetime.fromtimestamp(Path(p).stat().st_mtime)
    except OSError:
        return None


def kb(p):
    try:
        return round(Path(p).stat().st_size / 1024, 1)
    except OSError:
        return None


def iso(d):
    return d.strftime("%Y-%m-%d %H:%M") if d else None


def ru_date(d):
    return d.strftime("%d.%m.%Y") if d else "—"


def read(p):
    try:
        return Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
    return fm


def first_sentence(s, limit=160):
    s = re.sub(r"\s+", " ", s or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    s = m.group(1) if m else s
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


nodes, edges, issues = {}, {}, []


def add_node(nid, **kw):
    kw.setdefault("issues", [])
    kw["id"] = nid
    nodes[nid] = kw
    return kw


def add_edge(a, b, kind="link", w=1):
    if a == b or a not in nodes or b not in nodes:
        return
    key = (a, b) if a < b else (b, a)
    e = edges.get(key)
    if e:
        e["w"] += w
        if kind != "belongs":
            e["kind"] = kind if e["kind"] == "belongs" else e["kind"]
    else:
        edges[key] = {"source": a, "target": b, "kind": kind, "w": w}


def issue(nid, level, text):
    """level: red — не работает; amber — разъехалось/дубль; grey — уборка."""
    nodes[nid]["issues"].append({"level": level, "text": text})


# ─── Центр и ветки ────────────────────────────────────────────────────────────
add_node("center", label="Сад", sub="главный ассистент, принимает задачи Антона",
         kind="center", branch="center", status="active", path=str(SAD),
         desc="Точка входа. В сессии из ClaudeAIAgent маршрутизирует по CLAUDE.md; "
              "в сессии из /path/to/workspace видит только агентов поиска работы и навыки.")
for b, (label, sub) in BRANCHES.items():
    add_node("b:" + b, label=label, sub=sub, kind="branch", branch=b, status="active")
    add_edge("center", "b:" + b, "trunk")

# ─── Агенты: новое поколение ──────────────────────────────────────────────────
texts = {}  # nid → (текст, контекст 'new'|'old')
for p in sorted(NEW_AGENTS.glob("*.md")) + sorted((NEW_AGENTS / "_archive").glob("*.md")):
    t = read(p)
    fm = frontmatter(t)
    nid = fm.get("name", p.stem)
    desc = fm.get("description", "")
    label = desc.split("—")[0].strip() if "—" in desc[:30] else nid
    sub = first_sentence(desc.split("—", 1)[1] if "—" in desc[:30] else desc, 90)
    archived = "_archive" in p.parts
    abr = fm.get("branch", "job")
    add_node(nid, label=label, sub=sub, kind="agent", branch=abr,
             status="archived" if archived else "active", gen="new",
             path=str(p), desc=desc, model=fm.get("model", ""),
             effort=fm.get("effort", ""), size=kb(p), mtime=iso(mtime(p)))
    add_edge(nid, "b:" + abr, "belongs")
    texts[nid] = (t, "new")
    if archived:
        issue(nid, "grey", "Лежит в _archive — не используется. Можно удалить.")

# ─── Агенты: старое поколение ─────────────────────────────────────────────────
sad_text = read(SAD)
for p in sorted(OLD_AGENTS.glob("*.md")):
    nid = p.stem
    t = read(p)
    fm = frontmatter(t)
    desc = fm.get("description", "")
    ru, br, repl, role = OLD.get(nid, (nid, "solo", None, ""))
    gid = "old:" + nid
    stub = desc.startswith("УСТАРЕЛ")
    if stub:
        status = "stub"
    elif nid in OLD_ALIVE:
        status = "active"
    else:
        status = "legacy"
    add_node(gid, label=ru, sub=role or first_sentence(desc, 90), kind="agent",
             branch=br, status=status, gen="old", path=str(p), desc=desc,
             model=fm.get("model", ""), effort=fm.get("effort", ""), size=kb(p),
             mtime=iso(mtime(p)))
    add_edge(gid, "b:" + br, "belongs")
    texts[gid] = (t, "old")
    if stub:
        issue(gid, "grey", "Заглушка «УСТАРЕЛ» — ничего не делает. Файл можно удалить.")
    elif status == "legacy":
        routed = re.search(r"\*\*" + re.escape(nid) + r"\*\*|\(\.claude/agents/" + re.escape(nid) + r"\.md\)", sad_text)
        if repl:
            rname = "навык job-search" if repl.startswith("skill:") else repl
            txt = f"Старое поколение, заменён новым агентом ({rname})."
            if routed:
                txt += " Но Сад в ClaudeAIAgent/CLAUDE.md до сих пор отправляет задачи сюда — в сессии из ClaudeAIAgent «Ищи работу» уйдёт в старую систему."
                issue(gid, "amber", txt)
            else:
                issue(gid, "grey", txt + " Можно убрать в архив.")
        else:
            issue(gid, "amber", f"Старое поколение, и в новой системе замены нет: «{role}» сейчас никто не делает. Решить — вернуть в новую систему или закрыть направление.")
    if nid in OLD_TOP:
        issue(gid, "grey", "Виден только в сессии, открытой в ClaudeAIAgent. Из /path/to/workspace, где идёт поиск работы, его не вызвать.")

# ─── Навыки и запланированные задачи ─────────────────────────────────────────
for p in sorted(SKILLS.glob("*/SKILL.md")):
    t = read(p)
    fm = frontmatter(t)
    nid = "skill:" + fm.get("name", p.parent.name)
    br = fm.get("branch") or ("job" if "job" in nid else "learn")
    label = {"skill:job-search": "Компот", "skill:courses": "Курсы",
             "skill:light-audit": "Щелкунчик", "skill:razbor-sozvona": "Разбор созвона"}.get(nid, nid)
    sub = {"skill:job-search": "оркестратор поиска работы (навык)",
           "skill:courses": "SQL и английский, урок в день",
           "skill:light-audit": "оркестратор Example Workspace (навык)",
           "skill:razbor-sozvona": "нестандартные проекты: КП и смета"}.get(nid, "")
    add_node(nid, label=label, sub=sub, kind="skill", branch=br, status="active",
             path=str(p), desc=fm.get("description", ""), size=kb(p), mtime=iso(mtime(p)))
    add_edge(nid, "b:" + br, "belongs")
    texts[nid] = (t, "new")
    # правила каналов, вынесенные из навыка: читаются по необходимости
    for r in sorted((p.parent / "references").glob("*.md")):
        rid = "d:" + r.name
        add_node(rid, label="Правила: " + r.stem, sub="references/" + r.name, kind="doc",
                 branch=br, status="active", path=str(r), size=kb(r), mtime=iso(mtime(r)))
        add_edge(rid, "b:" + br, "belongs")
        add_edge(nid, rid, "link", 2)
        texts[rid] = (read(r), "new")

sched_file = next(iter((HOME / ".config/Claude/claude-code-sessions").glob("*/*/scheduled-tasks.json")), None)
sched = {}
if sched_file:
    try:
        sched = {x["id"]: x for x in json.loads(read(sched_file)).get("scheduledTasks", [])}
    except (ValueError, KeyError):
        pass
for p in sorted(SCHED_DIR.glob("*/SKILL.md")):
    t = read(p)
    fm = frontmatter(t)
    tid = p.parent.name
    nid = "task:" + tid
    cfg = sched.get(tid, {})
    enabled = cfg.get("enabled", True)
    when = cfg.get("cronExpression") or ("разово" if cfg.get("fireAt") else "")
    last = cfg.get("lastRunAt")
    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone().replace(tzinfo=None) if last else None
    tbr = "job" if "job" in tid or "telegram" in tid else "infra"
    add_node(nid, label=tid, sub=first_sentence(fm.get("description", ""), 90),
             kind="task", branch=tbr, status="active" if enabled else "off",
             path=str(p), desc=fm.get("description", ""),
             schedule=("Claude, " + when) if when else "Claude", last_run=iso(last_dt))
    add_edge(nid, "b:" + tbr, "belongs")
    texts[nid] = (t, "new")
    if enabled and when and not last:
        issue(nid, "amber", "Задача ни разу не запускалась. Первый прогон встанет на запросах разрешений — "
                            "нажать «Run now» в Claude Desktop и подтвердить их один раз.")
    if not enabled:
        issue(nid, "grey", f"Задача выключена (последний запуск {ru_date(last_dt)}). Если больше не нужна — удалить, чтобы не путала.")

# ─── Планировщики ─────────────────────────────────────────────────────────────
live_cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
doc_cron = read(CA / "crontab_ubuntu.txt")
runner_src = read(SCRIPTS / "cron_runner.py")
runner_jobs = {}
for m in re.finditer(r'^\s*\{"name":\s*"([^"]+)",\s*"script":\s*"([^"]+)"', runner_src, re.M):
    runner_jobs[m.group(2)] = m.group(1)


def cron_lines(src):
    out = {}
    for line in src.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" in line.split()[0]:
            continue
        parts = line.split()
        expr = " ".join(parts[:5])
        for m in re.finditer(r"([\w\-]+\.(?:py|sh))", line):
            out[m.group(1)] = expr
    return out


live = cron_lines(live_cron)
documented = cron_lines(doc_cron)
runner_live = "cron_runner.py" in live

add_node("cron", label="cron", sub="системное расписание Linux", kind="sched",
         branch="infra", status="active",
         desc="Установленный crontab пользователя: " + ", ".join(sorted(live)) if live else "")
add_edge("cron", "b:infra", "belongs")
add_node("hooks", label="Хуки Claude Code", sub="UserPromptSubmit / Stop → Киви",
         kind="sched", branch="kiwi", status="active",
         path=str(HOME / ".claude/settings.json"),
         desc="Каждая задача дольше 2 минут шлёт отчёт в Telegram через kiwi_task_done.py.")
add_edge("hooks", "b:kiwi", "belongs")

# ─── Скрипты ──────────────────────────────────────────────────────────────────


def log_mtime(stem, job_name=None):
    cands = [LOGS / f"{stem}.log", LOGS / f"{stem}_cron.log"]
    if job_name:
        cands.append(LOGS / f"{job_name}.log")
    ds = [d for d in (mtime(c) for c in cands) if d]
    return max(ds) if ds else None


def add_script(p, br, label=None):
    name = p.name
    nid = "s:" + name
    job = runner_jobs.get(name)
    last = log_mtime(p.stem, job)
    schedule, status = None, "active"
    if name in live:
        schedule = "cron " + live[name]
    elif job:
        schedule = "через cron_runner (" + job + ")"
    elif name in documented:
        schedule = "cron " + documented[name] + " (по документам)"
    exists = p.exists()
    add_node(nid, label=label or name, sub="", kind="script", branch=br,
             status="active" if exists else "missing", path=str(p),
             schedule=schedule, last_run=iso(last), mtime=iso(mtime(p)))
    add_edge(nid, "b:" + br, "belongs")
    if exists and name.endswith(".py"):
        texts[nid] = (read(p), "new" if JS in p.parents else "old")
    if name in live:
        add_edge("cron", nid, "runs")
    elif job:
        add_edge("s:cron_runner.py", nid, "runs")
    # Проблемы расписания
    planned = name in documented or job
    running = name in live or (job and runner_live)
    if planned and not running:
        where = "crontab_ubuntu.txt" if name in documented else "cron_runner.py"
        issue(nid, "red", f"По плану ({where}) должен запускаться сам, но в установленном cron его нет. "
                          f"Последний след в логах: {ru_date(last)}.")
        nodes[nid]["status"] = "dead"
    elif running and last and (NOW - last).days > STALE_DAYS:
        issue(nid, "red", f"Стоит в расписании, но лог не обновлялся с {ru_date(last)}.")
        nodes[nid]["status"] = "dead"
    return nid


# cron_runner первым, чтобы к нему цеплялись его задачи
if (SCRIPTS / "cron_runner.py").exists():
    add_script(SCRIPTS / "cron_runner.py", "infra")
for name, br in CA_SCRIPTS.items():
    if name != "cron_runner.py":
        add_script(SCRIPTS / name, br)
add_script(CA / "dashboard/scripts/firebase_sync.py", "infra")
for name in KIWI_SCRIPTS:
    add_script(KIWI / name, "kiwi")
add_edge("hooks", "s:kiwi_task_done.py", "runs")
for sp in (HOME / ".claude/settings.json", DATA / ".claude/settings.local.json"):
    try:
        hk = json.loads(read(sp)).get("hooks", {})
    except ValueError:
        continue
    for ev, groups in hk.items():
        for g in groups:
            for h in g.get("hooks", []):
                m = re.search(r'(/[^"\']+\.py)', h.get("command", ""))
                if not m:
                    continue
                hp = Path(m.group(1))
                if "s:" + hp.name not in nodes:
                    add_script(hp, "infra")
                    nodes["s:" + hp.name]["sub"] = f"хук {ev}" + (f" ({g['matcher']})" if g.get("matcher") else "")
                add_edge("hooks", "s:" + hp.name, "runs")

def proc_running(script):
    """Запущен ли python-процесс с этим скриптом (по argv, а не по подстроке командной строки)."""
    for c in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            argv = c.read_bytes().split(b"\0")
        except OSError:
            continue
        if argv and b"python" in argv[0] and any(a.decode(errors="replace").endswith(script) for a in argv[1:]):
            return True
    return False


# Демоны Киви: файл есть, а процесс?
tg_channel = HOME / ".local/bin/claude-telegram.sh"
for d in ("kiwi_listener.py", "kiwi_watchdog.py"):
    n = nodes.get("s:" + d)
    if n and not proc_running(d):
        n["status"] = "idle"
        txt = "Демон не запущен."
        if d == "kiwi_listener.py":
            txt += " Без него не работают кнопки «да/нет» и ответы из Telegram, которые обещает агент Киви."
            if tg_channel.exists():
                txt += " Запускать нельзя: бота слушает канал «Claude из Telegram», второй слушатель даст конфликт 409."
        else:
            txt += " Сторож зависших сессий (heartbeat) не работает."
        issue("s:" + d, "grey", txt + " Убрать в архив или вернуть в строй.")

# Вход из Telegram (плагин Claude Code Channels)
if tg_channel.exists():
    add_node("tg-channel", label="Claude из Telegram", sub="пишешь боту Киви — сообщение приходит в сессию Claude",
             kind="sched", branch="kiwi", status="active", path=str(tg_channel),
             desc="Ярлык «Claude — Telegram» открывает сессию в /path/to/workspace с каналом "
                  "plugin:telegram. Запросы разрешений приходят в Telegram.")
    add_edge("tg-channel", "b:kiwi", "belongs")
    add_edge("tg-channel", "center", "link", 3)
for p in sorted((JS / "bin").glob("*.py")):
    add_script(p, "job")
for p in sorted((JS / "bin/browser").glob("*.js")):
    add_script(p, "job")

# Мёртвый раннер тянет за собой все свои задачи — сводим в одну красную проблему
if not runner_live and runner_jobs:
    nodes["s:cron_runner.py"]["issues"] = [{"level": "red", "text":
        "Раннер плановых задач не стоит в установленном cron — вместе с ним не запускаются: "
        + ", ".join(sorted(runner_jobs.values())) + ". Последний запуск: "
        + ru_date(log_mtime("cron_runner")) + "."}]
    for s in runner_jobs:
        n = nodes.get("s:" + s)
        if n:
            n["issues"] = [{"level": "red", "text": "Запускается только через cron_runner, а он сам не стоит в cron. "
                                                     "Последний запуск: " + ru_date(log_mtime(Path(s).stem, runner_jobs[s])) + "."}]
            n["status"] = "dead"

# ─── Документы ────────────────────────────────────────────────────────────────
for p, br, label in DOCS:
    if not p.exists():
        continue
    if br == "center":
        texts["center"] = (read(p), "old")
        continue
    nid = "d:" + p.name if br != "la" else "d:la:" + p.name
    add_node(nid, label=label, sub=p.name, kind="doc", branch=br, status="active",
             path=str(p), size=kb(p), mtime=iso(mtime(p)))
    add_edge(nid, "b:" + br, "belongs")
    texts[nid] = (read(p), "new" if JS in p.parents else "old")

# ─── Связи по упоминаниям ─────────────────────────────────────────────────────
agent_ids_new = {n for n, v in nodes.items() if v["kind"] == "agent" and v.get("gen") == "new"}


def resolve(stem_id, ctx):
    """id из STEMS → id узла с учётом поколения."""
    if stem_id in SHARED:
        return stem_id if ctx == "new" else "old:" + SHARED[stem_id]
    if stem_id == "compote":
        return "skill:job-search" if ctx == "new" else "old:compote"
    if stem_id in agent_ids_new:
        return stem_id
    return "old:" + stem_id


script_names = {v["path"].split("/")[-1]: n for n, v in nodes.items() if v["kind"] == "script"}
def doc_key(v):
    # документы Example Workspace узнаём по пути: у них общие с поиском работы имена (STATE.md, README.md, rules.md)
    path = v["path"]
    if "/light-audit/" in path:
        return "light-audit/" + path.split("/light-audit/", 1)[1]
    if "/la_pult/" in path:
        return "la_pult/" + path.split("/la_pult/", 1)[1]
    return path.split("/")[-1]


doc_names = {doc_key(v): n for n, v in nodes.items() if v["kind"] == "doc"}
la_keys = [k for k in doc_names if "/" in k]
latin = {}
for n, v in nodes.items():
    if v["kind"] == "agent":
        latin[n.split(":")[-1]] = n
latin_re = re.compile(r"(?<![\w\-/])(" + "|".join(sorted(map(re.escape, latin), key=len, reverse=True)) + r")(?:\.md)?(?![\w\-])")
missing_refs = {}

for src, (t, ctx) in texts.items():
    body = t
    counts = {}
    for sid, pat in STEMS.items():
        c = len(re.findall(pat, body))
        if c:
            tgt = resolve(sid, ctx)
            counts[tgt] = counts.get(tgt, 0) + c
    # латинские id только в коде/ссылках: subagent_type, agents/x.md, `x`
    for m in latin_re.finditer(body):
        lid = m.group(1)
        # в новом контексте «yabloko»/«sliva» и т.п. — новые; «apple» и т.п. — старые
        tgt = latin[lid]
        counts[tgt] = counts.get(tgt, 0) + 1
    for name, tgt in script_names.items():
        c = body.count(name)
        if c:
            counts[tgt] = counts.get(tgt, 0) + c
    if src.startswith("s:") and "from kiwi import" in body:
        counts["s:kiwi.py"] = counts.get("s:kiwi.py", 0) + 1
    for name, tgt in doc_names.items():
        c = body.count(name)
        if "/" not in name:
            c -= sum(body.count(k) for k in la_keys if k.endswith("/" + name) or k.endswith(name))
        if c:
            counts[tgt] = counts.get(tgt, 0) + c
    if "job-search" in body and src.startswith("task:"):
        counts["skill:job-search"] = counts.get("skill:job-search", 0) + 3
    for tgt, c in counts.items():
        add_edge(src, tgt, "link", c)
    # ссылки на скрипты, которых нет нигде
    if nodes[src]["kind"] in ("agent", "skill", "task"):
        for m in set(re.findall(r"\b([a-z][a-z0-9_]+\.py)\b", body)):
            if m in script_names or m in ("file.py", "script.py", "example.py"):
                continue
            hits = any(next(base.glob(f"**/{m}"), None) for base in (CA, JS, DATA / "learning", DATA / ".claude", DATA / "dev/calls", DATA / "dev/la_pult"))
            if not hits:
                missing_refs.setdefault(src, set()).add(m)

for src, names in missing_refs.items():
    if nodes[src]["status"] in ("legacy", "stub", "archived"):
        continue
    issue(src, "amber", "Ссылается на скрипты, которых нет на диске: " + ", ".join(sorted(names)) + ".")

# ─── Реестр и маршрутизация Сада ──────────────────────────────────────────────
reg = read(REGISTRY)
absent = [v["label"] for n, v in nodes.items()
          if v["kind"] == "agent" and v.get("gen") == "new" and v["status"] == "active"
          and v["label"] not in reg and n not in reg]
if absent and "d:System Agents.md" in nodes and "выведен" not in reg:
    issue("d:System Agents.md", "amber",
          f"Реестр устарел (последняя правка {ru_date(mtime(REGISTRY))}): нет новых агентов поиска работы — "
          + ", ".join(absent) + ". Зато описаны старые Яблоко, Персик, Авокадо, Инжир как действующие.")
if "compote.md" in sad_text and "job-search" not in sad_text:
    issue("center", "amber",
          "CLAUDE.md Сада маршрутизирует поиск работы в старых агентов (apple, compote, cherry, plum, dogwood). "
          "Новая система (навык job-search и ягоды) там не упомянута.")
if not runner_live and runner_jobs:
    issue("b:calls", "red", "Ветка без автозапуска: fetch_telemost.py и cron_runner.py не стоят в cron — "
                            "созвоны сами не подтягиваются, еженедельный дайджест не собирается.")
    issue("b:infra", "red", "Утренняя сводка, проверка здоровья, дедлайны, синк GitLab, линтер — "
                            "не запускаются с " + ru_date(log_mtime("cron_runner")) + ".")

# ─── Проверки инструкций ──────────────────────────────────────────────────────
# 1. Упоминания выведенных агентов в живых инструкциях
GONE = {"Цитрус": r"Цитрус", "Лимон": r"Лимон", "Апельсин": r"Апельсин", "Мандарин": r"Мандарин",
        "Грейпфрут": r"Грейпфрут", "Манго": r"Манго", "Личи": r"Личи", "Груша": r"Груш[аиеуй]\b",
        "Персик": r"Персик", "Авокадо": r"Авокадо", "Инжир": r"Инжир", "Гранат": r"Гранат"}
GONE = {k: v for k, v in GONE.items() if not (OLD_AGENTS / "x").parent.joinpath(
    {"Цитрус": "citrus", "Лимон": "lemon", "Апельсин": "orange", "Мандарин": "mandarin",
     "Грейпфрут": "grapefruit", "Манго": "mango", "Личи": "lichi", "Груша": "pear", "Персик": "peach",
     "Авокадо": "avocado", "Инжир": "fig", "Гранат": "pomegranate"}[k] + ".md").exists()}
for src, (t, ctx) in texts.items():
    if src == "center" or nodes[src]["kind"] == "script":
        continue
    live_lines = "\n".join(l for l in t.splitlines()
                           if not re.search(r"стар|выведен|архив|[Лл]егаси|v1|потерял", l))
    found = [k for k, pat in GONE.items() if re.search(pat, live_lines)]
    if found:
        issue(src, "amber" if len(found) >= 3 else "grey",
              "Инструкция ссылается на агентов, которых больше нет: " + ", ".join(found) + ".")

# 2. Агент Киви обещает то, что не работает
kiwi_txt = texts.get("old:kiwi", ("", ""))[0]
if kiwi_txt and nodes.get("s:kiwi_listener.py", {}).get("status") == "idle" and "ask_approve" in kiwi_txt:
    issue("old:kiwi", "amber", "Описывает запросы подтверждений (ask_approve, ask_choice), листенер и heartbeat — "
                               "ничего из этого не запущено. Рабочая часть Киви сейчас одна: notify().")

# 3. Стартовый контекст прогона поиска работы
state = JS / "STATE.md"
if "d:STATE.md" in nodes and (kb(state) or 0) > 25:
    secs = re.split(r"(?m)^## ", read(state))[1:]
    big = sorted(((len(x.encode()) / 1024, x.splitlines()[0]) for x in secs), reverse=True)
    pools = [x for x in big if x[1].startswith("Пул на следующий прогон")]
    start = sum(kb(f) or 0 for f in (state, JS / "README.md", JS / "core/token-rules.md",
                                     SKILLS / "job-search/SKILL.md"))
    txt = (f"{kb(state):.0f} КБ. Компот читает файл целиком в начале каждого прогона, а его же правило — "
           f"«контекст 15 КБ, не больше». Самый большой раздел: «{big[0][1][:40]}» — {big[0][0]:.0f} КБ.")
    if len(pools) > 1:
        txt += f" Разделов «Пул на следующий прогон» накопилось {len(pools)} — нужен только последний."
    txt += f" Весь старт прогона (навык + README + STATE + правила токенов) — {start:.0f} КБ."
    issue("d:STATE.md", "amber", txt)

# 4. Модель и глубина рассуждений у агентов
try:
    sess_effort = json.loads(read(HOME / ".claude/settings.json")).get("effortLevel", "")
except ValueError:
    sess_effort = ""
no_effort = [v["label"] for v in nodes.values()
             if v["kind"] == "agent" and v.get("gen") == "new" and v["status"] == "active" and not v.get("effort")]
no_model = [v["label"] for v in nodes.values()
            if v["kind"] == "agent" and v.get("gen") == "new" and v["status"] == "active" and not v.get("model")]
if no_effort:
    txt = "Не задан effort (глубина рассуждений): " + ", ".join(no_effort) + "."
    if sess_effort:
        txt += f" Тогда берётся общий из настроек — сейчас «{sess_effort}», для механических циклов это дорого."
    if no_model:
        txt += " Не задана и модель: " + ", ".join(no_model) + "."
    issue("skill:job-search", "amber", txt)

# 5. Инструкции выведенной системы в 04 - Agent Context
stale_ctx = []
for f in sorted(CTX.glob("*.md")):
    t = read(f)
    if sum(1 for pat in GONE.values() if re.search(pat, t)) >= 3:
        stale_ctx.append(f.name)
if stale_ctx:
    issue("b:infra", "grey", "В «04 - Agent Context» лежат инструкции выведенной системы (Цитрус, Манго, Яблоко-v1): "
          + ", ".join(stale_ctx) + ". Агент, который их прочтёт, получит устаревшую картину. Перенести в архив.")

# Скрипты, на которые никто не ссылается и которых нет в расписании
for n, v in nodes.items():
    if v["kind"] != "script" or v.get("schedule") or v["issues"]:
        continue
    linked = [e for e in edges.values() if n in (e["source"], e["target"]) and e["kind"] != "belongs"]
    if not linked:
        last = v.get("last_run")
        issue(n, "grey", "Ни один агент, навык или расписание на него не ссылается"
              + (f", последний запуск {last[:10]}" if last else "") + ". Проверить, нужен ли.")

# ─── Итог ────────────────────────────────────────────────────────────────────
for n in nodes.values():
    for i in n["issues"]:
        issues.append({"node": n["id"], **i})

deg = {n: 0 for n in nodes}
for e in edges.values():
    deg[e["source"]] += 1
    deg[e["target"]] += 1
for n, v in nodes.items():
    v["deg"] = deg[n]

graph = {
    "generated": NOW.strftime("%d.%m.%Y %H:%M"),
    "branches": {k: {"label": v[0], "sub": v[1]} for k, v in BRANCHES.items()},
    "nodes": list(nodes.values()),
    "links": list(edges.values()),
}
(HERE / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
tpl = (read(HERE / "template.html")
       .replace("/*GRAPH_CSS*/", read(HERE / "graph.css"))
       .replace("/*GRAPH_JS*/", read(HERE / "graph.js")))
out = tpl.replace("/*GRAPH_DATA*/null", json.dumps(graph, ensure_ascii=False).replace("</", "<\\/"))
# artifact.html — без обёртки, для публикации; agent-graph.html — для открытия локально
(HERE / "artifact.html").write_text(out, encoding="utf-8")
(HERE / "agent-graph.html").write_text(
    '<!doctype html>\n<html lang="ru"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n'
    + out + "\n</body></html>\n", encoding="utf-8")

DASH = CA / "dashboard/public"
if DASH.is_dir():
    for name in ("graph.js", "graph.css"):
        src = read(HERE / name)
        dst = DASH / ("agent-" + name)
        if read(dst) != src:
            dst.write_text(src, encoding="utf-8")

lv = {"red": 0, "amber": 0, "grey": 0}
for i in issues:
    lv[i["level"]] += 1
print(f"узлов {len(nodes)}, связей {len(edges)}, проблем: "
      f"красных {lv['red']}, жёлтых {lv['amber']}, уборка {lv['grey']}")
print("→", HERE / "agent-graph.html")
