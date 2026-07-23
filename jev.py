#!/usr/bin/env python3
"""
jev.py — оркестратор jev.best. ОДНА КОМАНДА на операцию.

    python jev.py deploy     контекст → дроплет → build → up → Caddy → flush → проверить
    python jev.py status     реально ли jev.best отдаёт НАШЕ приложение (НЕ завися от DNS-кэша)
    python jev.py diagnose   кто владеет vhost'ом, что на appnet, что в общем Caddyfile
    python jev.py logs       хвост логов jev-web
    python jev.py dns        ГЛОБАЛЬНЫЙ DNS (публичный резолвер) + что кэширует дроплет
    python jev.py cert       форсировать выпуск TLS-серта на общем Caddy + показать ACME-лог
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
]
SHIP_DIRS = ["src", "public", "deploy"]
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
    # ВАЖНО: без --remove-orphans. Соседние стеки живут в этом же демоне.
    ssh(f"cd {REMOTE} && {COMPOSE} up -d --force-recreate")
    ssh(
        f"docker network inspect videodead_appnet "
        f"--format '{{{{range .Containers}}}}{{{{.Name}}}} {{{{end}}}}'"
    )

    print("→ 5/5 vhost + перезагрузка Caddy + сброс DNS-кэша дроплета")
    out, err, rc = ssh(f"cd {REMOTE} && python3 deploy/fix_caddy.py", check=False)
    print("   " + (out or err).replace("\n", "\n   "))
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
    scp(os.path.join(ROOT, "deploy", "fix_caddy.py"), f"{REMOTE}/deploy/fix_caddy.py")
    scp(os.path.join(ROOT, "deploy", "caddy", "jev.best.caddy"), f"{REMOTE}/deploy/caddy/jev.best.caddy")
    print("→ форсирую выпуск методом cybergod (admin /load, при неудаче — рестарт caddy)…")
    out, err, rc = ssh(f"cd {REMOTE} && python3 deploy/fix_caddy.py", check=False)
    print("   " + ((out or "") + (("\n" + err) if err.strip() else "")).replace("\n", "\n   "))
    if rc != 0:
        print("   [!] fix_caddy вернул ошибку — смотри выше.")

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
