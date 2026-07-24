#!/usr/bin/env python3
"""
daily_report.py — ежедневный отчёт по jev.best → email (ALERT_EMAIL) + короткая сводка в Telegram.
Структура 1:1 как cybergod (webapp/backend/app/daily_report.py), адаптировано под jev:
  * нет jobs.sqlite (у jev нет ассесментов) — вместо этого чат/аптайм-разделы;
  * читаем ОБЩИЙ events.log, но берём ТОЛЬКО service="jev-web" (в файле лежат и события colt);
  * источники — те же evt=http/security_alert/chat/error/health, что рисует дашборд.

Работает фоновой задачей внутри jev-api (никакого cron/systemd, что дрейфует мимо репо). Считает
следующие 07:00 UTC на каждом витке, поэтому рестарт не задваивает и не роняет отправку.
Вручную:
    docker exec jev-api python3 daily_report.py          # печать + отправка
    docker exec jev-api python3 daily_report.py --print  # только печать
"""
import asyncio
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

try:
    from . import notify
except ImportError:
    import notify

EVENTS_LOG  = os.environ.get("EVENTS_LOG", "/coltevents/events.log")
SERVICE     = os.environ.get("SERVICE", "jev-web")
REPORT_HOUR = int(os.environ.get("DAILY_REPORT_HOUR", "7"))      # UTC
ENABLED     = os.environ.get("DAILY_REPORT", "1") != "0"


def _read_events(since):
    """Только НАШИ (service=jev-web) события за окно — общий лог делим с colt по полю service."""
    out = []
    try:
        with open(EVENTS_LOG, "r", errors="replace") as fh:
            for line in fh:
                i = line.find("{")
                if i < 0:
                    continue
                try:
                    e = json.loads(line[i:])
                except Exception:
                    continue
                if e.get("service") != SERVICE:
                    continue
                if float(e.get("ts") or 0) >= since:
                    out.append(e)
    except FileNotFoundError:
        pass
    return out


def build(hours=24):
    since = time.time() - hours * 3600
    ev = _read_events(since)
    http   = [e for e in ev if e.get("evt") == "http"]
    alerts = [e for e in ev if e.get("evt") == "security_alert"]
    chat   = [e for e in ev if e.get("evt") == "chat"]
    errs   = [e for e in ev if e.get("evt") == "error"]
    health = [e for e in ev if e.get("evt") == "health"]

    humans = [e for e in http if not e.get("bot")]
    bots   = [e for e in http if e.get("bot")]
    uniq_h = len({e.get("ip") for e in humans})
    err_http = len([e for e in http if int(e.get("status", 0)) >= 400])

    chat_ok   = [e for e in chat if e.get("ok")]
    chat_fail = [e for e in chat if not e.get("ok")]

    up = [e for e in health if e.get("up") == 1 or e.get("ok") is True]
    up_pct = (100.0 * len(up) / len(health)) if health else 0.0
    cert_days = next((e.get("cert_days") for e in reversed(health)
                      if isinstance(e.get("cert_days"), int) and e.get("cert_days") >= 0), "?")

    L = []
    now = datetime.now(timezone.utc)
    L.append("jev.best — ежедневный отчёт")
    L.append("Окно: последние %dч  ·  сформирован %s UTC" % (hours, now.strftime("%Y-%m-%d %H:%M")))
    L.append("=" * 64)
    L.append("")
    L.append("ТРАФИК")
    L.append("  Посетители (уник. IP, люди) : %d" % uniq_h)
    L.append("  Запросы (люди / боты)       : %d / %d" % (len(humans), len(bots)))
    L.append("  Ответы 4xx/5xx              : %d" % err_http)
    L.append("")
    L.append("ИИ-ЧАТ «Кассандра»")
    L.append("  Ответов / неудач            : %d / %d" % (len(chat_ok), len(chat_fail)))
    if chat_ok:
        lat = sorted(int(e.get("ms", 0)) for e in chat_ok)
        p95 = lat[max(0, int(len(lat) * 0.95) - 1)]
        models = Counter(e.get("model") for e in chat_ok)
        L.append("  Латентность LLM p95         : %d ms" % p95)
        L.append("  Модели                      : %s"
                 % (", ".join("%s=%d" % (m, n) for m, n in models.most_common()) or "-"))
    L.append("")
    L.append("НАДЁЖНОСТЬ И БЕЗОПАСНОСТЬ")
    L.append("  Аптайм (по пробам watchdog) : %.1f%%  (%d проб)" % (up_pct, len(health)))
    L.append("  TLS-сертификат              : %s дней до истечения" % cert_days)
    L.append("  Ошибки jev-api (evt=error)  : %d" % len(errs))
    L.append("  Алерты безопасности         : %d" % len(alerts))
    L.append("")

    top_c = Counter(e.get("country") for e in humans if e.get("country") not in (None, "-"))
    if top_c:
        L.append("СТРАНЫ (люди): " + ", ".join("%s=%d" % (c, n) for c, n in top_c.most_common(10)))
        L.append("")
    dev = Counter(e.get("device") for e in humans if e.get("device") not in (None, "-", ""))
    if dev:
        L.append("УСТРОЙСТВА: " + ", ".join("%s=%d" % (d, n) for d, n in dev.most_common()))
        L.append("")
    top_ip = Counter(e.get("ip") for e in humans if e.get("ip") not in (None, "-"))
    if top_ip:
        L.append("ТОП IP (люди):")
        for ip, n in top_ip.most_common(10):
            L.append("  %-20s %d" % (ip, n))
        L.append("")
    if alerts:
        L.append("АЛЕРТЫ БЕЗОПАСНОСТИ (%d)" % len(alerts))
        L.append("-" * 64)
        for a in alerts[:20]:
            L.append("  [%s] %s — %s" % (a.get("severity"), a.get("rule"), a.get("subject")))
        L.append("")
    bot_names = Counter(e.get("bot_name") for e in bots if e.get("bot_name") not in (None, "-"))
    if bot_names:
        L.append("БОТЫ/СКАНЕРЫ: " + ", ".join("%s=%d" % (b, n) for b, n in bot_names.most_common(12)))
        L.append("")

    L.append("-" * 64)
    L.append("Личные данные (IP, страна) обрабатываются для мониторинга безопасности — GDPR Art.6(1)(f).")
    L.append("Полные графики: godeyes.ai/observe → «jev.best — Web (visitors + security)».")
    day = now.strftime("%Y-%m-%d")
    return day, "\n".join(L)


def send(hours=24):
    """Полный отчёт — на email; короткая шапка — в Telegram (чтобы отчёт был и там)."""
    day, body = build(hours)
    ok_e = notify.email("jev.best — ежедневный отчёт %s" % day, body)
    try:
        head = "\n".join(body.split("\n")[:14])
        notify.telegram("📊 *jev.best — отчёт за сутки*\n\n" + head + "\n\n(полный отчёт — на email)")
    except Exception:
        pass
    notify._log(evt="daily_report", result="sent" if ok_e else "error", window_h=hours)
    return ok_e, body


async def scheduler():
    """Срабатывает в REPORT_HOUR UTC ежедневно. Пересчитывает задержку каждый виток — рестарт
    не задваивает и не пропускает."""
    if not ENABLED:
        return
    while True:
        now = datetime.now(timezone.utc)
        nxt = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep(max(60, (nxt - now).total_seconds()))
        try:
            send(24)
        except Exception as e:
            notify._log(evt="daily_report", result="error", err=repr(e)[:160])


if __name__ == "__main__":
    day, body = build(24)
    print(body)
    if "--print" not in sys.argv:
        ok, _ = send(24)
        print("\n[email/telegram] sent:", ok)
