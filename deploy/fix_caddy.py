#!/usr/bin/env python3
"""
Запускается НА ДРОПЛЕТЕ. Вписывает vhost jev.best в ОБЩИЙ Caddyfile — 1:1 по МЕТОДУ cybergod.ai
(см. deploy_web_direct.py в проекте colt):

  1) вписать committed-сниппет (deploy/caddy/jev.best.caddy) между маркерами, НА МЕСТЕ (open 'w',
     чтобы не рвать inode bind-mount'а — sed -i менял бы inode и контейнер читал бы старый файл);
  2) validate;
  3) ФОРСИРОВАННАЯ полная загрузка через admin API (`caddy adapt | POST /load`) — обычный
     `caddy reload` при неизменённом конфиге = no-op и может держать старый конфиг;
  4) проверить, что jev.best реально отдаёт наш контейнер (curl --resolve на 127.0.0.1);
  5) ЕСЛИ не отдаёт (серт ещё не выпущен) — РЕСТАРТ контейнера caddy. Это перепровижинит ВСЕ сайты
     и запускает свежую попытку выпуска серта для jev.best. Ровно так делает cybergod's
     deploy_web_direct.py как надёжный fallback; соседи (cybergod/jobhuntwow/videodead) вернутся за
     пару секунд со СВОИМИ, уже выпущенными сертами.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

# Маркеры ДОЛЖНЫ совпадать с тем, что уже лежит в Caddyfile на дроплете, иначе старый блок не
# снимется и получится дубликат сайта. Оставляем прежние.
BEGIN = "# >>> jev.best (managed by jev.py deploy) >>>"
END = "# <<< jev.best <<<"
BLOCK_SRC = "/opt/jevbest/deploy/caddy/jev.best.caddy"
DOMAIN = "jev.best"


def sh(cmd, check=True):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"FAIL: {cmd}\n{p.stderr.strip()}")
    return p.stdout.strip()


def dexec(container, inner):
    """docker exec <container> sh -c '<inner>' — inner передаём через json.dumps, чтобы кавычки выжили."""
    return subprocess.run(
        f"docker exec {container} sh -c {json.dumps(inner)}",
        shell=True, capture_output=True, text=True,
    )


def find_caddy_container():
    names = sh("docker ps --format '{{.Names}}'").splitlines()
    for n in names:
        if n.strip() == "videodead-caddy":
            return n.strip()
    guess = [n for n in names if "caddy" in n.lower()]
    if not guess:
        raise SystemExit("НЕ НАЙДЕН контейнер caddy. docker ps пуст или Caddy не запущен.")
    return guess[0]


def find_caddyfile(container):
    info = json.loads(sh(f"docker inspect {container}"))[0]
    for m in info.get("Mounts", []):
        dst = m.get("Destination", "")
        if dst.endswith("/Caddyfile") or dst == "/etc/caddy/Caddyfile":
            src = m.get("Source")
            if src and os.path.isfile(src):
                return src
    raise SystemExit(f"У {container} не найден bind-mount Caddyfile. Проверь: docker inspect {container}")


def strip_block(text):
    return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)


def is_live(container):
    """jev.best реально отдаёт наш /__whoami через общий Caddy? (-k: серт может быть ещё staging/новый)"""
    r = dexec(container,
              f"curl -sk -m 8 --resolve {DOMAIN}:443:127.0.0.1 https://{DOMAIN}/__whoami 2>/dev/null || true")
    return "jev-best" in (r.stdout or "")


def main():
    if not os.path.isfile(BLOCK_SRC):
        raise SystemExit(f"Нет файла блока: {BLOCK_SRC}")

    c = find_caddy_container()
    cf = find_caddyfile(c)
    print(f"caddy container : {c}")
    print(f"caddyfile       : {cf}")

    block = open(BLOCK_SRC, encoding="utf-8").read().strip()
    original = open(cf, encoding="utf-8").read()
    backup = f"{cf}.bak.{int(time.time())}"
    shutil.copy2(cf, backup)
    print(f"backup          : {backup}")

    body = strip_block(original).rstrip()
    new = f"{body}\n\n{BEGIN}\n{block}\n{END}\n"
    if new != original:
        with open(cf, "w", encoding="utf-8") as f:
            f.write(new)
        print("Caddyfile обновлён (in-place).")
    else:
        print("Caddyfile уже актуален — правок нет.")

    v = dexec(c, "caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile")
    if v.returncode != 0:
        shutil.copy2(backup, cf)
        raise SystemExit("Caddyfile НЕ валиден — откатил на прежнюю версию.\n" + v.stderr.strip())
    print("validate        : ok")

    # ФОРС полной загрузки через admin API (как cybergod: обычный reload может держать старый конфиг).
    load = dexec(c, "caddy adapt --config /etc/caddy/Caddyfile > /tmp/cfg.json && "
                    "curl -sS -X POST -H 'Content-Type: application/json' "
                    "--data @/tmp/cfg.json http://localhost:2019/load && echo ADMIN_LOAD_OK")
    print("admin /load     : " + ("ok" if "ADMIN_LOAD_OK" in (load.stdout or "")
                                   else "не прошёл (" + ((load.stderr or load.stdout or "")[:100]) + ")"))
    time.sleep(3)

    if is_live(c):
        print("live            : jev.best уже отдаёт наш контейнер ✓")
        return

    # Не живой -> серта для jev.best нет. РЕСТАРТ caddy = перепровижен всех сайтов + свежая попытка
    # выпуска. Это метод cybergod (deploy_web_direct.py). Соседи вернутся со своими сертами за секунды.
    print("live            : ещё нет -> РЕСТАРТ caddy (метод cybergod: перепровижен + свежий выпуск серта)")
    sh(f"docker restart {c} >/dev/null", check=False)
    time.sleep(10)
    for _ in range(5):
        if is_live(c):
            print("live            : jev.best поднялся ✓")
            return
        time.sleep(8)
    print("live            : серт ещё выпускается (или лимит Let's Encrypt). jev.py проверит дальше;")
    print("                  если это лимит после ранних неудач — подожди ~1 час и снова python jev.py cert.")


if __name__ == "__main__":
    sys.exit(main())
