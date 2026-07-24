#!/usr/bin/env python3
"""
watchdog.py — активный аптайм-мониторинг jev.best. Единственное, чего НЕ видит пассивный
конвейер (Caddy-лог → evt=http): если сайт ЛЁГ, трафика нет, дашборд показывает «No data»,
и никакой алерт не срабатывает. Этот воркер закрывает именно эту слепую зону.

Каждые WATCHDOG_INTERVAL секунд из контейнера jev-api:
  1) internal  — GET http://jev-web:8080/__whoami  (жив ли НАШ статик-контейнер);
  2) edge+TLS  — TLS-хендшейк к общему эджу videodead-caddy-1:443 c SNI=jev.best, затем
     HTTP/1.1 GET /__whoami с Host: jev.best. Проверяет разом: эдж поднят, сертификат валиден
     и НЕ протух, и маршрут jev.best → jev-web цел. Ходим к эджу по имени контейнера в общей
     сети appnet, а НЕ на публичный IP — так проверка не зависит от hairpin-NAT дроплета.
  3) cert_days — сколько дней до истечения сертификата (из того же хендшейка).

Пишет evt=health в общий EVENTS_LOG (тот же конвейер, что evt=http → Loki → Grafana). Это ещё и
пульс: если jev-api умрёт, evt=health перестанут идти (в дашборде виден «последний пульс»).
Алерты (сайт лёг / сайт поднялся / сертификат истекает) — через alerts.observe_health → notify
(тот же Telegram-бот + Gmail). Никаких новых бэкендов и ключей.
"""
import calendar
import os
import socket
import ssl
import threading
import time
import urllib.request

import telemetry
try:
    from . import alerts
except ImportError:
    import alerts

ENABLED       = os.environ.get("WATCHDOG_ENABLED", "1") != "0"
INTERVAL      = int(os.environ.get("WATCHDOG_INTERVAL", "60"))
START_DELAY   = int(os.environ.get("WATCHDOG_START_DELAY", "20"))
TIMEOUT       = int(os.environ.get("WATCHDOG_TIMEOUT", "8"))
INTERNAL_URL  = os.environ.get("WATCHDOG_INTERNAL_URL", "http://jev-web:8080/__whoami")
EDGE_HOST     = os.environ.get("WATCHDOG_EDGE_HOST", "videodead-caddy-1")
EDGE_PORT     = int(os.environ.get("WATCHDOG_EDGE_PORT", "443"))
SNI           = os.environ.get("WATCHDOG_SNI", "jev.best")
PATH          = os.environ.get("WATCHDOG_PATH", "/__whoami")
MARKER        = os.environ.get("WATCHDOG_MARKER", "jev-best")


def _days_from_cert(cert):
    """Дней до истечения из peer-сертификата (notAfter: 'Jun  1 12:00:00 2027 GMT'). -1 если нет."""
    try:
        na = (cert or {}).get("notAfter")
        if not na:
            return -1
        exp = calendar.timegm(time.strptime(na, "%b %d %H:%M:%S %Y %Z"))
        return int((exp - time.time()) // 86400)
    except Exception:
        return -1


def _probe_internal():
    """Наш статик-контейнер отдаёт метку на :8080?"""
    t0 = time.time()
    try:
        req = urllib.request.Request(INTERNAL_URL, headers={"User-Agent": "jev-watchdog/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(64).decode("utf-8", "replace")
            return {"ok": MARKER in body, "status": getattr(r, "status", 0),
                    "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "status": 0, "ms": int((time.time() - t0) * 1000), "err": repr(e)[:120]}


def _probe_edge():
    """TLS к эджу с SNI=jev.best + GET /__whoami. Валидирует эдж, серт и сквозной маршрут."""
    t0 = time.time()
    try:
        ctx = ssl.create_default_context()          # проверяет цепочку, имя и срок сертификата
        with socket.create_connection((EDGE_HOST, EDGE_PORT), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=SNI) as ss:
                cert = ss.getpeercert()
                request = (
                    "GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: jev-watchdog/1.0\r\n"
                    "Accept: */*\r\nConnection: close\r\n\r\n" % (PATH, SNI)
                ).encode()
                ss.sendall(request)
                buf = b""
                while len(buf) < 8192:
                    chunk = ss.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
        cert_days = _days_from_cert(cert)
        text = buf.decode("latin1", "replace")
        first = text.split("\r\n", 1)[0].split()
        status = int(first[1]) if len(first) >= 2 and first[1].isdigit() else 0
        ok = (status == 200 and MARKER in text)
        return {"ok": ok, "status": status, "cert_days": cert_days,
                "ms": int((time.time() - t0) * 1000)}
    except ssl.SSLError as e:
        return {"ok": False, "status": 0, "cert_days": -1,
                "ms": int((time.time() - t0) * 1000), "err": "tls:" + repr(e)[:110]}
    except Exception as e:
        return {"ok": False, "status": 0, "cert_days": -1,
                "ms": int((time.time() - t0) * 1000), "err": repr(e)[:120]}


def _cycle():
    internal = _probe_internal()
    edge = _probe_edge()
    ok = bool(internal["ok"] and edge["ok"])
    ev = dict(
        evt="health", ok=ok, internal_ok=internal["ok"], edge_ok=edge["ok"],
        # числовые дубли для графиков Loki (unwrap хочет число, а не true/false):
        up=1 if ok else 0, internal_up=1 if internal["ok"] else 0, edge_up=1 if edge["ok"] else 0,
        status=edge.get("status", 0), cert_days=edge.get("cert_days", -1),
        ms_internal=internal.get("ms", 0), ms_edge=edge.get("ms", 0),
        err=(internal.get("err") or edge.get("err") or ""),
    )
    telemetry.emit(**ev)                 # пульс + история в Loki
    try:
        alerts.observe_health(ev)        # сайт лёг / поднялся / серт истекает -> notify
    except Exception:
        pass


def _loop():
    print("[watchdog] active uptime monitor: internal=%s edge=%s(SNI %s) every %ds"
          % (INTERNAL_URL, EDGE_HOST, SNI, INTERVAL), flush=True)
    time.sleep(START_DELAY)              # дать jev-web и эджу подняться после деплоя
    while True:
        try:
            _cycle()
        except Exception as e:
            print("[watchdog] cycle error:", repr(e)[:160], flush=True)
        time.sleep(INTERVAL)


def start():
    if not ENABLED:
        print("[watchdog] disabled (WATCHDOG_ENABLED=0)", flush=True)
        return
    threading.Thread(target=_loop, name="watchdog", daemon=True).start()
