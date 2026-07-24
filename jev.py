#!/usr/bin/env python3
"""
jev.py — оркестратор jev.best. ОДНА КОМАНДА на операцию.

    python jev.py deploy     контекст → дроплет → build → up → Caddy → flush → проверить
    python jev.py status     реально ли jev.best отдаёт НАШЕ приложение (НЕ завися от DNS-кэша)
    python jev.py diagnose   кто владеет vhost'ом, что на appnet, что в общем Caddyfile
    python jev.py logs       хвост логов jev-web
    python jev.py dns        ГЛОБАЛЬНЫЙ DNS (публичный резолвер) + что кэширует дроплет
    python jev.py cert       форсировать выпуск TLS-серта на общем Caddy + показать ACME-лог
    python jev.py obs        наблюдаемость: логи jev-web → общий Loki, дашборд → общий Grafana
    python jev.py api        собрать/поднять jev-api (Кассандра, ИИ-чат; ключи из /opt/jevbest/.env)
    python jev.py flush      сбросить DNS-кэш резолвера ДРОПЛЕТА (чинит «status показывает старое»)
    python jev.py down       остановить только наш стек (соседей не трогает)

Почему проверка НЕ через getent и НЕ через браузер (урок ship.py — проверяй РЕАЛЬНОСТЬ,
а не «сайт ответил»):
  getent на дроплете и твой браузер держат DNS-КЭШ. Пока старый TTL не истёк, ОБА показывают
  старый Squarespace, хотя мир уже видит дроплет — и наивная проверка врёт. Поэтому verify()
  бьёт НАПРЯМУЮ в IP дроплета, пиня имя (SNI=jev.best): это доказывает, что общий Caddy отдаёт
  наш /__whoami под ВАЛИДНЫМ TLS-сертом, вообще не завися от кэша. А `dns` спрашивает ПУБЛИЧНЫЙ
  резолвер (dns.google/DoH), а не закэшированный резолвер дроплета.

Правила эстейта, зашитые сюда:
  * дроплет 64.225.108.200, проект compose `jevbest`, каталог /opt/jevbest;
  * сборка образа НА ДРОПЛЕТЕ (сюда едет только исходник);
  * никогда `--remove-orphans` — снесло бы colt-stack и videodead;
  * GitHub в пути деплоя не участвует.
"""
import json
import os
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

HOST = os.environ.get("DROPLET_HOST", "64.225.108.200")
USER = os.environ.get("DROPLET_USER", "root")
REMOTE = "/opt/jevbest"
PROJECT = "jevbest"
DOMAIN = os.environ.get("JEV_DOMAIN", "jev.best")
COMPOSE = f"docker compose -p {PROJECT} -f docker-compose.web.yml"
GIT_REMOTE = os.environ.get("JEV_GIT_REMOTE", "https://github.com/feranicus/yantar.git")

SHIP_FILES = [
    "package.json", "package-lock.json", "vite.config.js", "index.html",
    "srv.Caddyfile", "Dockerfile.web", "docker-compose.web.yml",
    "Dockerfile.api", "docker-compose.api.yml",
]
SHIP_DIRS = ["src", "public", "deploy", "webapp"]
SKIP = {"node_modules", "dist", "__pycache__", ".git", ".vite", ".DS_Store"}

ROOT = os.path.dirname(os.path.abspath(__file__))


# ── ssh ─────────────────────────────────────────────────────────────────────
def ssh_key():
    if os.environ.get("SSH_KEY"):
        return os.environ["SSH_KEY"]
    home = os.path.expanduser("~/.ssh")
    for name in ("id_ed25519", "id_rsa", "id_ecdsa"):
        p = os.path.join(home, name)
        if os.path.isfile(p):
            return p
    return None


def ssh_opts():
    # fail-fast набор как в ship.py: молчаливый висяк — это failure mode, за который уже заплачено.
    o = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=4",
    ]
    k = ssh_key()
    if k:
        o += ["-i", k]
    return o


def ssh(cmd, stdin_data=None, check=True, quiet=False):
    full = ["ssh", *ssh_opts(), f"{USER}@{HOST}", cmd]
    if not quiet:
        print(f"  ssh> {cmd if len(cmd) < 120 else cmd[:117] + '...'}")
    p = subprocess.run(
        full,
        input=stdin_data.encode() if stdin_data else None,
        capture_output=True,
    )
    out = p.stdout.decode(errors="replace")
    err = p.stderr.decode(errors="replace")
    if check and p.returncode != 0:
        print(out)
        print(err, file=sys.stderr)
        raise SystemExit(f"ssh упал (rc={p.returncode})")
    return out.strip(), err.strip(), p.returncode


def scp(local, remote):
    full = ["scp", *ssh_opts(), local, f"{USER}@{HOST}:{remote}"]
    p = subprocess.run(full, capture_output=True)
    if p.returncode != 0:
        print(p.stderr.decode(errors="replace"), file=sys.stderr)
        raise SystemExit("scp упал")


# ── ПРАВДА DNS/HTTP, не завися от кэша ───────────────────────────────────────
def doh(name, rtype):
    """Спросить ГЛОБАЛЬНЫЙ DNS через DoH (dns.google). Это правда мира, а не кэш дроплета."""
    url = "https://dns.google/resolve?name=%s&type=%s" % (name, rtype)
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def public_a(name):
    """([ip, ...], err) — A-записи имени по публичному резолверу."""
    try:
        j = doh(name, "A")
        return sorted({a["data"] for a in j.get("Answer", []) if a.get("type") == 1}), None
    except Exception as e:  # noqa: BLE001
        return [], str(e)


def public_has_https_rr(name):
    """(bool|None, err) — есть ли HTTPS/SVCB-запись (лэндмайн Squarespace) в ГЛОБАЛЬНОМ DNS."""
    try:
        j = doh(name, "HTTPS")
        return any(a.get("type") == 65 for a in j.get("Answer", [])), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def pinned_whoami(host, ip, path="/__whoami", timeout=15):
    """GET https://{host}{path}, но TCP идёт ПРЯМО на {ip}; SNI и проверка серта — по {host}.
    Возвращает (ok, detail). НЕ зависит ни от какого DNS-кэша и заодно валидирует TLS-серт:
    если серт ещё не выпущен — честно скажет об этом, а не покажет чужую парковку."""
    ctx = ssl.create_default_context()
    try:
        raw = socket.create_connection((ip, 443), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return False, "нет TCP до %s:443 (%s)" % (ip, e)
    try:
        s = ctx.wrap_socket(raw, server_hostname=host)
    except ssl.SSLCertVerificationError as e:
        raw.close()
        return False, "TLS-серт для %s ещё не выпущен/невалиден (%s)" % (
            host, getattr(e, "verify_message", str(e)))
    except Exception as e:  # noqa: BLE001
        raw.close()
        return False, "TLS-ошибка: %s" % e
    try:
        req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: jev-verify\r\n"
               "Accept-Encoding: identity\r\nConnection: close\r\n\r\n" % (path, host))
        s.sendall(req.encode())
        buf = b""
        while len(buf) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    except Exception as e:  # noqa: BLE001
        return False, "ошибка чтения: %s" % e
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
    text = buf.decode("utf-8", "replace")
    head, _, body = text.partition("\r\n\r\n")
    status = head.split("\r\n", 1)[0] if head else "(нет ответа)"
    return ("jev-best" in body), "%s | body=%r" % (status, body.strip()[:40])


def flush_droplet_dns():
    """Сбросить кэш резолвера НА ДРОПЛЕТЕ, чтобы getent-проверки читали свежий DNS."""
    ssh("resolvectl flush-caches 2>/dev/null || systemd-resolve --flush-caches 2>/dev/null "
        "|| systemctl restart systemd-resolved 2>/dev/null || true", check=False, quiet=True)


# ── Caddy-фикс, встроенный в оркестратор (ОДИН python — jev.py; отдельных скриптов нет) ──
# Раньше это был deploy/fix_caddy.py. По стандарту эстейта оркестратор ОДИН, поэтому логику
# держим здесь и гоним на дроплет как `ssh python3 -` (stdin=код). Метод cybergod:
# вписать committed-сниппет → validate → admin /load → если не живой, РЕСТАРТ общего Caddy.
FIXCADDY_PY = r'''
import json, os, re, shutil, subprocess, sys, time
BEGIN = "# >>> jev.best (managed by jev.py deploy) >>>"
END = "# <<< jev.best <<<"
BLOCK_SRC = "/opt/jevbest/deploy/caddy/jev.best.caddy"
DOMAIN = "jev.best"
def sh(cmd, check=True):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit("FAIL: " + cmd + "\n" + p.stderr.strip())
    return p.stdout.strip()
def dexec(c, inner):
    return subprocess.run("docker exec " + c + " sh -c " + json.dumps(inner),
                          shell=True, capture_output=True, text=True)
def find_caddy():
    names = sh("docker ps --format '{{.Names}}'").splitlines()
    for n in names:
        if n.strip() == "videodead-caddy":
            return n.strip()
    g = [n for n in names if "caddy" in n.lower()]
    if not g:
        raise SystemExit("no caddy container")
    return g[0]
def find_cf(c):
    info = json.loads(sh("docker inspect " + c))[0]
    for m in info.get("Mounts", []):
        d = m.get("Destination", "")
        if d.endswith("/Caddyfile") or d == "/etc/caddy/Caddyfile":
            s = m.get("Source")
            if s and os.path.isfile(s):
                return s
    raise SystemExit("no Caddyfile bind-mount on " + c)
def strip_block(t):
    return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", t, flags=re.S)
def live(c):
    r = dexec(c, "curl -sk -m 8 --resolve " + DOMAIN + ":443:127.0.0.1 https://" + DOMAIN + "/__whoami 2>/dev/null || true")
    return "jev-best" in (r.stdout or "")
c = find_caddy(); cf = find_cf(c)
print("caddy container : " + c); print("caddyfile       : " + cf)
block = open(BLOCK_SRC, encoding="utf-8").read().strip()
orig = open(cf, encoding="utf-8").read()
bak = cf + ".bak." + str(int(time.time())); shutil.copy2(cf, bak); print("backup          : " + bak)
new = strip_block(orig).rstrip() + "\n\n" + BEGIN + "\n" + block + "\n" + END + "\n"
if new != orig:
    open(cf, "w", encoding="utf-8").write(new); print("Caddyfile обновлён (in-place).")
else:
    print("Caddyfile уже актуален.")
v = dexec(c, "caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile")
if v.returncode != 0:
    shutil.copy2(bak, cf); raise SystemExit("НЕ валиден — откатил.\n" + v.stderr.strip())
print("validate        : ok")
load = dexec(c, "caddy adapt --config /etc/caddy/Caddyfile > /tmp/cfg.json && "
                "curl -sS -X POST -H 'Content-Type: application/json' --data @/tmp/cfg.json "
                "http://localhost:2019/load && echo ADMIN_LOAD_OK")
print("admin /load     : " + ("ok" if "ADMIN_LOAD_OK" in (load.stdout or "") else "FAILED " + ((load.stderr or load.stdout or "")[:100])))
time.sleep(3)
if live(c):
    print("live            : jev.best уже отдаёт наш контейнер"); sys.exit(0)
print("live            : ещё нет -> РЕСТАРТ caddy (метод cybergod)")
sh("docker restart " + c + " >/dev/null", check=False); time.sleep(10)
for _ in range(5):
    if live(c):
        print("live            : jev.best поднялся"); sys.exit(0)
    time.sleep(8)
print("live            : серт ещё выпускается (или лимит Let's Encrypt). jev.py проверит дальше.")
'''


def run_fix_caddy():
    """Прогнать встроенный Caddy-фикс НА ДРОПЛЕТЕ: ssh python3 - (код идёт в stdin)."""
    out, err, rc = ssh("python3 -", stdin_data=FIXCADDY_PY, check=False)
    body = (out or "") + (("\n" + err) if err.strip() else "")
    print("   " + body.replace("\n", "\n   "))
    return rc


# ── deploy ──────────────────────────────────────────────────────────────────
def build_tar():
    fd, path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)

    def flt(ti):
        parts = ti.name.split("/")
        if any(p in SKIP for p in parts):
            return None
        if ti.name.endswith(".env") or ti.name.endswith(".pyc"):
            return None
        return ti

    with tarfile.open(path, "w:gz") as tar:
        for f in SHIP_FILES:
            p = os.path.join(ROOT, f)
            if os.path.isfile(p):
                tar.add(p, arcname=f, filter=flt)
        for d in SHIP_DIRS:
            p = os.path.join(ROOT, d)
            if os.path.isdir(p):
                tar.add(p, arcname=d, filter=flt)
    return path


def do_git(message="jev.best: build + ship"):
    """Коммит + пуш в GitHub (источник правды, как ship.py). Репо: feranicus/yantar.
    Инициализирует репо и remote при первом запуске. Никогда не force-пушим."""
    import subprocess as _sp
    print("→ 0/5 git: commit + push (GitHub = источник правды)")
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        _sp.run(["git", "init", "-q"], cwd=ROOT)
        _sp.run(["git", "branch", "-M", "main"], cwd=ROOT)
        print("   git init (ветка main)")
    gi = os.path.join(ROOT, ".gitignore")
    if not os.path.isfile(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("node_modules/\ndist/\ndev-dist/\n__pycache__/\n.vite/\n_to_delete/\n*.env\n")
    r = _sp.run(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True, capture_output=True)
    if r.returncode != 0:
        _sp.run(["git", "remote", "add", "origin", GIT_REMOTE], cwd=ROOT)
        print(f"   remote origin -> {GIT_REMOTE}")
    _sp.run(["git", "add", "-A"], cwd=ROOT)
    changed = _sp.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
    if changed:
        _sp.run(["git", "commit", "-q", "-m", message], cwd=ROOT)
        print(f"   commit: {message}")
    else:
        print("   (нечего коммитить)")
    p = _sp.run(["git", "push", "-u", "origin", "main"], cwd=ROOT, text=True, capture_output=True)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    print("   " + (out.replace("\n", "\n   ")[:600] if out else "push ok"))
    if p.returncode != 0:
        print("   [!] push НЕ прошёл (remote ушёл вперёд / нет доступа). Деплой продолжаю.")
        print("       Разрули: git pull --rebase origin main, затем снова python jev.py deploy.")


def cmd_deploy():
    do_git()
    print("→ 1/5 собираю контекст")
    tar = build_tar()
    size = os.path.getsize(tar) / 1024
    print(f"   контекст: {size:.0f} КБ")

    print("→ 2/5 отправляю на дроплет")
    ssh(f"mkdir -p {REMOTE}", quiet=True)
    scp(tar, f"{REMOTE}/ctx.tar.gz")
    os.unlink(tar)
    ssh(f"cd {REMOTE} && tar xzf ctx.tar.gz && rm -f ctx.tar.gz")

    print("→ 3/5 дроплет собирает образ")
    out, err, _ = ssh(f"cd {REMOTE} && {COMPOSE} build 2>&1 | tail -25")
    print("   " + out.replace("\n", "\n   "))

    print("→ 4/5 поднимаю контейнер")
    # Каталог access-лога Caddy создаём и отдаём uid 1000 ДО старта. Иначе Docker создаст
    # /opt/jevbest/logs как root:root, а read_only-контейнер jev-web (uid 1000, cap_drop ALL)
    # не сможет открыть /logs/caddy.jsonl — Caddy упадёт на старте и статика ляжет.
    # Статик-деплой обязан быть самодостаточным и НЕ зависеть от `python jev.py api`.
    ssh(f"mkdir -p {REMOTE}/logs && chown -R 1000:1000 {REMOTE}/logs", quiet=True)
    # ВАЖНО: без --remove-orphans. Соседние стеки живут в этом же демоне.
    ssh(f"cd {REMOTE} && {COMPOSE} up -d --force-recreate")
    ssh(
        f"docker network inspect videodead_appnet "
        f"--format '{{{{range .Containers}}}}{{{{.Name}}}} {{{{end}}}}'"
    )

    print("→ 5/5 vhost + перезагрузка Caddy + сброс DNS-кэша дроплета")
    rc = run_fix_caddy()
    if rc != 0:
        raise SystemExit("Caddy не настроен — деплой не завершён.")
    flush_droplet_dns()

    print()
    verify()


def verify():
    """Проверка РЕАЛЬНОСТИ, не завися от DNS-кэша (ни дроплета, ни браузера)."""
    print("→ проверка")

    out, _, _ = ssh(
        f"docker exec jev-web wget -qO- http://127.0.0.1:8080/__whoami", check=False
    )
    print(f"   контейнер изнутри : {out or '(пусто)'}")
    if not (out or "").strip():
        # Пусто изнутри = Caddy НЕ слушает :8080 (упал на старте), контейнер сам жив.
        # Почти всегда — не открылся /logs/caddy.jsonl (права на /opt/jevbest/logs).
        st, _, _ = ssh("docker inspect -f '{{.State.Status}} restarts={{.RestartCount}}' jev-web 2>/dev/null || echo none", check=False)
        print(f"   [!] jev-web не отдаёт :8080. state: {st or '?'}")
        cl, _, _ = ssh("docker logs --tail 6 jev-web 2>&1 | tail -6", check=False)
        if cl.strip():
            print("       последние строки лога jev-web:")
            print("       " + cl.replace("\n", "\n       "))

    out, _, _ = ssh(
        f"docker run --rm --network videodead_appnet curlimages/curl:latest "
        f"-s http://jev-web:8080/__whoami", check=False
    )
    print(f"   по сети appnet    : {out or '(пусто)'}")

    # ГЛАВНОЕ: публично, но пиним имя на IP дроплета — кэш вообще ни при чём.
    ok, detail = pinned_whoami(DOMAIN, HOST)
    print(f"   PIN {DOMAIN}→{HOST}: {'jev-best ✓' if ok else 'НЕ наш ✗'}")
    print(f"                       {detail}")

    a, err = public_a(DOMAIN)
    if err:
        pub = f"DoH недоступен ({err})"
    elif HOST in a:
        pub = f"→ {HOST}  [обычные посетители едут К НАМ]"
    elif a:
        pub = f"→ {', '.join(a)}  [ещё указывает на старый хост]"
    else:
        pub = "нет A-записи"
    print(f"   глобальный DNS    : {pub}")

    hasrr, _ = public_has_https_rr(DOMAIN)
    if hasrr:
        print(f"   HTTPS/SVCB        : ЕСТЬ — удали запись в Squarespace (лэндмайн, Chrome идёт по ней)")

    print()
    if ok:
        print("✓ ГОТОВО. Общий Caddy отдаёт НАШЕ приложение под валидным TLS-сертом.")
        if HOST not in (a or []):
            print("  Глобальный DNS ещё не везде указывает на нас: посетители на старом кэше")
            print("  увидят старый хост, пока не истечёт TTL. Сам сайт уже РАБОЧИЙ.")
            print("  На своей машине увидишь сразу:  ipconfig /flushdns  → Ctrl+Shift+R в браузере.")
    else:
        print(f"✗ Наше приложение по {DOMAIN} пока НЕ подтверждено — смотри строку PIN выше.")
        print("  Если TLS-серт «ещё не выпущен» — подожди ~минуту (Caddy выпускает его сам, как")
        print("  только jev.best резолвится в дроплет глобально) и повтори: python jev.py status")
    return ok


def cmd_status():
    verify()


def cmd_diagnose():
    print("— контейнеры на appnet —")
    out, _, _ = ssh(
        "docker network inspect videodead_appnet "
        "--format '{{range .Containers}}{{.Name}}{{\"\\n\"}}{{end}}'"
    )
    print(out)

    print("— наш контейнер —")
    out, _, _ = ssh(
        "docker ps --filter name=jev-web "
        "--format '{{.Names}}\t{{.Status}}\t{{.Image}}'", check=False
    )
    print(out or "jev-web не запущен")

    print("— блок в общем Caddyfile —")
    out, _, _ = ssh(
        "f=$(docker inspect videodead-caddy "
        "--format '{{range .Mounts}}{{if eq .Destination \"/etc/caddy/Caddyfile\"}}"
        "{{.Source}}{{end}}{{end}}'); "
        "echo \"файл: $f\"; grep -n -A2 'jev.best' \"$f\" | head -30", check=False
    )
    print(out)

    print("— глобальный DNS (публичный резолвер) —")
    a, err = public_a(DOMAIN)
    print(f"  {DOMAIN}: {', '.join(a) or '—'}  {'[OK]' if HOST in a else ('[DoH?]' if err else '[не наш]')}")


def cmd_logs():
    out, _, _ = ssh(f"cd {REMOTE} && {COMPOSE} logs --tail 80 jev-web", check=False)
    print(out)


def cmd_down():
    # без --remove-orphans и только наш проект
    ssh(f"cd {REMOTE} && {COMPOSE} down")
    print("jev-web остановлен. Соседние стеки не тронуты.")


def _edge_caddy():
    """Имя контейнера общего edge-Caddy (videodead-caddy-1). Ищем, не предполагаем."""
    out, _, _ = ssh("docker ps --format '{{.Names}}' | grep -i caddy | head -1",
                    check=False, quiet=True)
    return (out.strip().splitlines()[0] if out.strip() else "videodead-caddy-1")


def cmd_cert():
    """Форсировать выпуск TLS-серта для jev.best МЕТОДОМ cybergod (deploy_web_direct.py).

    Серт не выпускался, потому что первую попытку Caddy сделал, пока jev.best ещё резолвился в
    Squarespace — challenge провалился, Caddy ушёл в backoff, а `caddy reload` при неизменённом
    конфиге — no-op и выпуск НЕ перезапускает. fix_caddy теперь делает то же, что рабочий
    cybergod: admin `/load`, а если сайт всё ещё не живой — РЕСТАРТ контейнера caddy (перепровижен
    всех сайтов + свежая попытка выпуска). Соседи вернутся со своими сертами за секунды."""
    c = _edge_caddy()
    print(f"общий Caddy: {c}")
    # Доставляем committed-сниппет + fix_caddy на дроплет и запускаем метод cybergod.
    ssh(f"mkdir -p {REMOTE}/deploy/caddy", quiet=True)
    scp(os.path.join(ROOT, "deploy", "caddy", "jev.best.caddy"), f"{REMOTE}/deploy/caddy/jev.best.caddy")
    print("→ форсирую выпуск методом cybergod (admin /load, при неудаче — рестарт caddy)…")
    rc = run_fix_caddy()
    if rc != 0:
        print("   [!] Caddy-фикс вернул ошибку — смотри выше.")

    print("→ жду выпуск Let's Encrypt (TLS-ALPN) и проверяю напрямую по IP…")
    time.sleep(8)
    for attempt in range(7):
        ok, detail = pinned_whoami(DOMAIN, HOST)
        print(f"   PIN {DOMAIN}→{HOST}: {'jev-best ✓' if ok else 'ещё нет'}  ({detail})")
        if ok:
            print("\n✓ Серт выпущен — jev.best живой под валидным TLS.")
            print("  На своей машине:  ipconfig /flushdns  → Ctrl+Shift+R.")
            return
        if attempt < 6:
            time.sleep(12)
    print("\n✗ Серт всё ещё не выпущен. Достаю НАСТОЯЩУЮ причину из общего Caddy…\n")
    _caddy_acme_report(c)
    print("\n  Читай блок 'ACME' выше:")
    print("   • 'too many failed authorizations' / 'rateLimited' / 'urn:ietf:params:acme:error:rateLimited'")
    print("     → лимит Let's Encrypt (5 неудачных проверок/час на домен) после ранних попыток,")
    print("       пока DNS смотрел на Squarespace. Ждём ~1 час — потом: python jev.py cert.")
    print("   • 'timeout'/'connection refused'/'Timeout during connect' на TLS-ALPN challenge")
    print("     → challenge не доходит до эджа. Пришли мне строки — разберём.")
    print("   • пусто и здесь → пришли вывод, добавлю другой источник лога.")


def _caddy_acme_report(c):
    """Вытащить настоящую ACME-причину: сырой stdout, файловые логи и cert-store общего Caddy.
    (Эдж пишет логи в файл, поэтому `docker logs | grep` был пуст — читаем всё, что есть.)"""
    print("   -- ACME: сырой docker-лог (последние строки) --")
    raw, _, _ = ssh(f"docker logs --since 15m {c} 2>&1 | tail -40", check=False, quiet=True)
    print("   " + (raw or "(docker logs пуст — Caddy пишет в файл)").replace("\n", "\n   "))

    print("\n   -- ACME: файловые логи Caddy по jev.best --")
    filelog, _, _ = ssh(
        f"docker exec {c} sh -c 'grep -rihEl \"acme|jev.best\" /var/log/caddy /data 2>/dev/null "
        f"| head -6 | while read f; do echo \"== $f ==\"; "
        f"grep -iE \"jev.best|acme|authz|challenge|rate|error|obtain|order\" \"$f\" 2>/dev/null "
        f"| tail -15; done' 2>/dev/null", check=False, quiet=True)
    print("   " + (filelog or "(файловых ACME-логов не нашёл)").replace("\n", "\n   "))

    print("\n   -- cert-store: есть ли серт jev.best на диске эджа --")
    store, _, _ = ssh(
        f"docker exec {c} sh -c 'find /data -type d -name \"jev.best\" 2>/dev/null; "
        f"ls -la /data/caddy/certificates/*/jev.best/ 2>/dev/null' 2>/dev/null",
        check=False, quiet=True)
    print("   " + (store or "(серта jev.best в /data нет — выпуск НЕ прошёл)").replace("\n", "\n   "))


def _grafana_import(dash_path):
    """Импорт дашборда в СУЩЕСТВУЮЩУЮ Grafana по HTTP API (как import_dashboard.py у colt):
    найти uid Loki-датасорса, переприцелить рефы, POST /api/dashboards/db (idempotent)."""
    import urllib.error
    url = os.environ.get("GRAFANA_URL", "").rstrip("/")
    tok = os.environ.get("GRAFANA_TOKEN", "")
    if not (url and tok):
        print("   [i] Дашборд НЕ импортирован — задай доступ к Grafana и повтори `python jev.py obs`:")
        print("       PowerShell:  $env:GRAFANA_URL='https://godeyes.ai/observe'; $env:GRAFANA_TOKEN='glsa_…'")
        print("       (токен: Grafana → Administration → Service accounts → Add token, роль Editor/Admin)")
        return False
    H = {"Content-Type": "application/json", "Accept": "application/json",
         "Authorization": "Bearer " + tok}

    def api(method, u, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(u, data=data, method=method, headers=H)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw[:1] in "{[" else raw)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    uid = os.environ.get("GRAFANA_LOKI_UID", "")
    if not uid:
        st, ds = api("GET", url + "/api/datasources")
        if st == 200 and isinstance(ds, list):
            loki = [d for d in ds if d.get("type") == "loki"]
            for d in loki:
                if d.get("uid") == "loki" or d.get("name", "").lower() == "loki":
                    uid = d["uid"]; break
            if not uid and loki:
                uid = loki[0]["uid"]
    uid = uid or "loki"
    print(f"   Loki datasource uid: {uid}")

    dash = json.load(open(dash_path, encoding="utf-8"))

    def retarget(o):
        if isinstance(o, dict):
            if o.get("type") == "loki" and "uid" in o:
                o["uid"] = uid
            for v in o.values():
                retarget(v)
        elif isinstance(o, list):
            for v in o:
                retarget(v)

    retarget(dash)
    dash["id"] = None
    st, res = api("POST", url + "/api/dashboards/db",
                  {"dashboard": dash, "overwrite": True, "message": "jev.py obs"})
    if st == 200 and isinstance(res, dict) and res.get("status") == "success":
        print(f"   ✓ дашборд импортирован: {url}{res.get('url', '')}")
        return True
    print(f"   [!] импорт не прошёл ({st}): {str(res)[:220]}")
    return False


def cmd_obs():
    """Дашборд jev.best — СТРУКТУРА КАК В CYBERGOD «Colt Web» (Visitors + Security), по тем же
    событиям evt=http / security_alert, но service=jev-web. События генерирует jev-api (см. `api`):
    он тейлит access-лог jev-web, строит evt=http через ТЕ ЖЕ telemetry/alerts/notify, что и colt-web,
    и пишет их в ОБЩИЙ colt events.log → colt-promtail → общий Loki. Здесь только ПРОВИЖЕНИМ дашборд
    в каталог, который читает videodead-grafana (токен НЕ нужен). Сначала `python jev.py api`."""
    print("→ дашборд jev.best (как «Colt Web», service=jev-web) — провижен в общий Grafana, без токена")
    dash_dir = "/opt/videodead/observability/grafana/dashboards"
    ssh(f"mkdir -p {dash_dir}", quiet=True)
    scp(os.path.join(ROOT, "deploy", "obs", "grafana", "jevbest.json"), f"{dash_dir}/jevbest.json")
    print(f"   дашборд провижен : {dash_dir}/jevbest.json (Grafana подхватит за ~10-30с)")

    # опционально — если заданы GRAFANA_URL+GRAFANA_TOKEN, ещё и импортируем по API
    if os.environ.get("GRAFANA_URL") and os.environ.get("GRAFANA_TOKEN"):
        print("→ (доп.) импорт по API — заданы GRAFANA_URL+GRAFANA_TOKEN")
        _grafana_import(os.path.join(ROOT, "deploy", "obs", "grafana", "jevbest.json"))

    # проверим, что события jev-web уже долетают в Loki (jev-api должен быть запущен и был трафик)
    out, _, _ = ssh(
        "docker run --rm --network videodead_appnet curlimages/curl:latest -s "
        "'http://videodead-loki-1:3100/loki/api/v1/query?query="
        "count_over_time({container=~%22.*assess-bot.*%22}%20|%20json%20|%20service=%22jev-web%22%20[15m])' "
        "2>/dev/null || true", check=False, quiet=True)
    seen = '"result":[{' in (out or "")
    print("   события service=jev-web в Loki: " + ("ЕСТЬ ✓" if seen else "пока нет (запусти `python jev.py api`, дай трафик)"))
    print("\n  Grafana → Dashboards → «jev.best — Web (visitors + security)».")
    print("  Explore (Loki):  {container=~\".*assess-bot.*\"} | json | service=\"jev-web\" | evt=\"http\"")


def cmd_api():
    """Собрать и поднять jev-api (Кассандра, ИИ-чат) на дроплете. Ключи — из /opt/jevbest/.env
    (те же, что в cybergod: OPENAI_API_KEY, OPENAI_BASE_URL — DO Inference). Секреты НЕ в репозитории.
    Маршрут /api/* включается ребилдом jev-web (srv.Caddyfile) — сначала `deploy`, затем `api`."""
    print("→ jev-api: переиспользую ВСЕ секреты, что УЖЕ на дроплете (из colt) — новых не завожу")
    # ВСЁ тянем из живого окружения colt-контейнера ПРЯМО НА ДРОПЛЕТЕ: LLM-ключ + бот + email (Gmail).
    # Значения НИКОГДА не покидают дроплет и не печатаются (показываем только ИМЕНА, что нашли).
    # Плюс создаём общий каталог логов (jev-web пишет туда caddy.jsonl) и находим colt-events volume.
    reuse = (
        "set -e; mkdir -p /opt/jevbest/logs; chown -R 1000:1000 /opt/jevbest/logs 2>/dev/null || true; "
        "SRC=''; for c in colt-web colt-assessbot colt-cassandra; do "
        "  if docker inspect \"$c\" >/dev/null 2>&1; then SRC=\"$c\"; break; fi; done; "
        "if [ -z \"$SRC\" ]; then echo NO_COLT; exit 0; fi; "
        "umask 077; : > /opt/jevbest/.env.tmp; "
        "for V in OPENAI_API_KEY OPENAI_BASE_URL BOT_TOKEN ALERT_TG_CHAT ALERT_EMAIL GMAIL_SENDER GMAIL_SA_B64; do "
        "  VAL=$(docker exec \"$SRC\" printenv \"$V\" 2>/dev/null || true); "
        "  [ -n \"$VAL\" ] && printf '%s=%s\\n' \"$V\" \"$VAL\" >> /opt/jevbest/.env.tmp; done; "
        "grep -q OPENAI_BASE_URL /opt/jevbest/.env.tmp || echo 'OPENAI_BASE_URL=https://inference.do-ai.run/v1' >> /opt/jevbest/.env.tmp; "
        "echo 'JEV_CHAT_MODELS=deepseek-3.2,llama-4-maverick' >> /opt/jevbest/.env.tmp; "
        "VOL=$(docker volume ls --format '{{.Name}}' | grep -i 'colt.*event' | head -1); "
        "[ -n \"$VOL\" ] && echo \"COLT_EVENTS_VOLUME=$VOL\" >> /opt/jevbest/.env.tmp; "
        "mv /opt/jevbest/.env.tmp /opt/jevbest/.env; "
        "echo \"REUSED from $SRC:\"; sed 's/=.*/=<set>/' /opt/jevbest/.env"
    )
    out, _, _ = ssh(reuse, check=False, quiet=True)
    print("   " + (out or "(нет ответа)").replace("\n", "\n   "))
    if "OPENAI_API_KEY=<set>" not in out:
        print("   [!] LLM-ключ не найден в colt — чат ответит фолбэком. Проверь, что colt-web запущен.")
    if "ALERT_TG_CHAT=<set>" not in out:
        print("   [i] ALERT_TG_CHAT не задан в colt — Telegram-алерты некуда слать. Добавь свой chat_id в")
        print("       /opt/jevbest/.env (ALERT_TG_CHAT=<число>), напиши боту, чтобы узнать id. email — по ALERT_EMAIL.")
    print("→ дроплет собирает jev-api")
    out, err, _ = ssh(f"cd {REMOTE} && docker compose -p {PROJECT} -f docker-compose.api.yml build 2>&1 | tail -15")
    print("   " + (out or err).replace("\n", "\n   "))
    print("→ поднимаю jev-api (без --remove-orphans)")
    ssh(f"cd {REMOTE} && docker compose -p {PROJECT} -f docker-compose.api.yml up -d")
    time.sleep(6)
    out, _, _ = ssh("docker exec jev-api python3 -c "
                    "\"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=5).read().decode())\" "
                    "2>/dev/null || echo '(jev-api не отвечает)'", check=False)
    print("   health          : " + (out or "(нет ответа)"))
    out, _, _ = ssh("docker exec jev-web wget -qO- http://127.0.0.1:8080/api/health 2>/dev/null || echo '(маршрут /api не готов — нужен deploy jev-web)'", check=False)
    print("   через jev-web    : " + (out or "(нет ответа)"))
    print("\n  Чат: открой jev.best → кнопка «Спросить ИИ». Если health показал \"llm_configured\": false —")
    print("  добавь ключ в /opt/jevbest/.env и повтори: python jev.py api")


def cmd_flush():
    print("Сбрасываю DNS-кэш резолвера НА ДРОПЛЕТЕ…")
    flush_droplet_dns()
    print("готово — теперь getent-проверки на дроплете читают свежий DNS.\n")
    cmd_dns()


def cmd_dns():
    print(f"Нужные записи для {DOMAIN} (регистратор Squarespace):\n")
    print(f"  A     @      {HOST}     TTL 1 hr")
    print(f"  A     www    {HOST}     TTL 1 hr\n")
    print("Удалить (если ещё остались): дефолтные A 198.49.23.x / 198.185.159.x,")
    print("CNAME www → ext-sq.squarespace.com и HTTPS/SVCB-запись Squarespace.\n")

    print("— ГЛОБАЛЬНЫЙ DNS (публичный резолвер — правда мира) —")
    for name in (DOMAIN, f"www.{DOMAIN}"):
        a, err = public_a(name)
        if err:
            print(f"  {name:<16} DoH недоступен ({err})")
        else:
            mark = "OK" if HOST in a else ("нет A" if not a else "не наш")
            print(f"  {name:<16} {', '.join(a) or '—':<34} [{mark}]")
    hasrr, err = public_has_https_rr(DOMAIN)
    if hasrr is None:
        print(f"  {'HTTPS/SVCB':<16} DoH недоступен ({err})")
    else:
        print(f"  {'HTTPS/SVCB':<16} {'ЕСТЬ — удали (лэндмайн)' if hasrr else 'нет — ок':<34} "
              f"[{'плохо' if hasrr else 'OK'}]")

    print("\n— как это видит РЕЗОЛВЕР ДРОПЛЕТА (может быть ЗАКЭШИРОВАНО — не показатель) —")
    for name in (DOMAIN, f"www.{DOMAIN}"):
        out, _, _ = ssh(f"getent ahostsv4 {name} | awk '{{print $1}}' | sort -u", check=False, quiet=True)
        got = out.replace("\n", ", ") if out else "нет ответа"
        mark = "OK" if HOST in out else "кэш/старое"
        print(f"  {name:<16} {got:<34} [{mark}]")
    print("\n  Глобальный [OK], а дроплет показывает старое → это просто кэш. Сбрось: python jev.py flush")


CMDS = {
    "deploy": cmd_deploy,
    "status": cmd_status,
    "diagnose": cmd_diagnose,
    "logs": cmd_logs,
    "down": cmd_down,
    "dns": cmd_dns,
    "cert": cmd_cert,
    "obs": cmd_obs,
    "api": cmd_api,
    "flush": cmd_flush,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        return 1
    print(f"jev.best → {USER}@{HOST}  проект {PROJECT}\n")
    CMDS[sys.argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
