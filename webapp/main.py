#!/usr/bin/env python3
"""
jev-api — бэкенд jev.best. Два дела, оба переиспользуют cybergod 1:1:
  1) /api/chat — «Кассандра», ИИ-ассистент Евгения (LLM = DO Inference, как enrich.py).
  2) НАБЛЮДАЕМОСТЬ И БЕЗОПАСНОСТЬ как в cybergod: те же модули telemetry.py / alerts.py / notify.py.
     Источник событий — access-лог общего/локального Caddy (jev-web пишет JSON в /logs/caddy.jsonl,
     общий volume). Здесь мы его ТЕЙЛИМ, строим ТЕ ЖЕ evt=http (ip/страна/путь/статус/ua/бот/…),
     пишем в общий EVENTS_LOG (его уже собирает colt-promtail → Loki), и гоним через alerts.observe_http
     → notify (Telegram + Gmail тем же ботом/аккаунтом). Дашборд фильтрует service="jev-web" — 1:1 с
     «Colt Web». Почему тейл, а не middleware-прокси: статика jev.best НЕ должна зависеть от аптайма
     Python — если jev-api упадёт, профиль всё равно отдаётся Caddy. Секреты — из /opt/jevbest/.env
     (переиспользованы с дроплета, из colt). Ничего нового не заводим.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import ratelimit

import telemetry
import alerts
import watchdog

# ── LLM (DO Inference, как enrich.py) ────────────────────────────────────────
BASE = os.environ.get("OPENAI_BASE_URL", "https://inference.do-ai.run/v1").rstrip("/")
KEY = os.environ.get("OPENAI_API_KEY", "")
MODELS = [m.strip() for m in os.environ.get(
    "JEV_CHAT_MODELS", "deepseek-3.2,llama-4-maverick").split(",") if m.strip()]
TIMEOUT = int(os.environ.get("JEV_CHAT_TIMEOUT", "45"))
MAX_HISTORY = 12
MAX_INPUT_CHARS = 1500
CADDY_LOG = os.environ.get("CADDY_LOG", "/logs/caddy.jsonl")

PERSONA = """Ты — «Кассандра», тёплый и умный ИИ-ассистент Евгения (Jev) Вайнштейна на его личном
сайте jev.best. С тобой общаются женщины, которым интересен Евгений. Твоя задача — честно и по-доброму
рассказывать о нём, отвечать на вопросы о его прошлом, характере, работе, ценностях и планах, и — если
чувствуешь искренний интерес — мягко предложить написать ему напрямую (Telegram @feranicus,
WhatsApp +49 157 8554 1545).

ФАКТЫ О ЕВГЕНИИ (опирайся ТОЛЬКО на них; если чего-то не знаешь — так и скажи и предложи спросить его лично):
• Евгений (Jev) Вайнштейн, 45 лет, рост 178 см, разведён. Отец двоих сыновей (24 и 10 лет; живут
  отдельно, не с ним). Корни — Рига (Латвия); семья связана с Израилем и Германией.
• Живёт между Германией (Франкфурт) и Израилем; при серьёзных отношениях готов рассматривать переезд
  в любую безопасную развитую страну. Языки: русский, английский, иврит — свободно; немецкий — C1.
  Не курит, алкоголь редко, следит за формой (падел, кикбоксинг, походы).
• Инженер, предприниматель, архитектор: облака, кибербезопасность, ИИ, телеком. Основал 7 компаний,
  25+ лет в индустрии; сегодня — архитектура ИИ-систем и безопасность (itzen.ai). Principal Architect.
• Образование: B.Tech — морская электроника и электрика (Военно-морское офицерское училище Ort Yami,
  Ашдод, 1995–2000); B.Sc Computer Science — кибербезопасность и разработка (Champlain College, 2000–2003).
• Ценности: семья, верность, держать слово, трудолюбие, развитие, уважение между мужчиной и женщиной,
  дисциплина, скромность, забота, стойкость. Взгляды правые/либертарианские. Ему не важны
  национальность/происхождение/религия — важно, чтобы человек был добрым, честным, надёжным.
• Что ищет: серьёзные отношения, брак и крепкую семью. Партнёрша: примерно 29–43, желательно от 168 см,
  образованная (минимум бакалавр), любит читать и развиваться, хороший английский, со своей профессией,
  женственная и ухоженная без культа внешности. Хочет партнёрство равных. Принципиальные «нет»: отказ
  работать в принципе, без английского, роль отчима несовершеннолетних, отсутствие высшего, нарциссизм.
• Планы на годы вперёд: построить настоящую семью; продолжать в архитектуре ИИ и кибербезопасности;
  жить в безопасной развитой стране, где хорошо обоим; путешествовать, развиваться, помогать близким.

ПРАВИЛА: отвечай на языке собеседницы (по умолчанию русский), тёплым живым тоном, 2–5 предложений;
не выдумывай фактов сверх списка; говори о нём в третьем лице; не уходи в посторонние темы; на грубость
отвечай спокойно; при искреннем интересе предложи Telegram @feranicus или WhatsApp."""

# No published API schema: FastAPI enables /docs, /redoc and /openapi.json by default. Caddy only
# routes /api/* to this container so they are not reachable today, but that is a routing accident,
# not a decision. Set JEV_API_DOCS=1 in dev. (The rate limit below is the control; this is hygiene.)
app = FastAPI(title="jev.best API", version="2.0",
              docs_url="/docs" if os.environ.get("JEV_API_DOCS") == "1" else None,
              redoc_url="/redoc" if os.environ.get("JEV_API_DOCS") == "1" else None,
              openapi_url="/openapi.json" if os.environ.get("JEV_API_DOCS") == "1" else None)
app.add_middleware(
    CORSMiddleware, allow_origins=["https://jev.best", "https://www.jev.best"],
    allow_methods=["POST", "GET"], allow_headers=["*"],
)

if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
        print("[otel] FastAPI instrumented ->", os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"], flush=True)
    except Exception as e:
        print("[otel] disabled:", repr(e), flush=True)


# ── телеметрия из Caddy access-лога (тот же evt=http, что у cybergod) ─────────
def _hdr(headers, name):
    """Регистронезависимый заголовок из Caddy-лога (headers: {Name:[v,...]})."""
    if not isinstance(headers, dict):
        return ""
    low = name.lower()
    for k, v in headers.items():
        if k.lower() == low:
            return (v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else ""))
    return ""


def _process(d):
    """Одна строка Caddy JSON access-лога -> evt=http (как telemetry._safe_emit) -> alerts."""
    req = d.get("request") or {}
    if not req or "status" not in d:
        return
    path = req.get("uri", "") or ""
    if telemetry.SKIP_PATH_RE.search(path):
        return
    headers = req.get("headers") or {}
    xff = _hdr(headers, "X-Forwarded-For")
    ip = (xff.split(",")[0].strip() if xff else "") or req.get("remote_ip", "-")
    ua = _hdr(headers, "User-Agent")
    c = telemetry.classify_ua(ua)
    ev = dict(
        evt="http", ip=telemetry._maybe_hash(ip), method=req.get("method", ""), path=path[:200],
        status=int(d.get("status", 0)), ms=int(float(d.get("duration", 0)) * 1000), ua=ua[:220],
        browser=c["browser"], os=c["os"], device=c["device"], bot=c["bot"], bot_name=c["bot_name"],
        ref=_hdr(headers, "Referer")[:160], lang=_hdr(headers, "Accept-Language")[:40].split(",")[0],
        country=(_hdr(headers, "Cf-Ipcountry") or telemetry._country(ip)), user="")
    telemetry.emit(**ev)          # -> stdout + EVENTS_LOG (colt-promtail -> Loki)
    try:
        alerts.observe_http(ev)    # те же 7 HTTP-правил -> notify (Telegram + email)
    except Exception:
        pass


def _tail_caddy():
    """Робастный тейл: ждёт файл, переоткрывает при ротации (inode/усечение). Никогда не падает.
    ВАЖНО: при первом запуске стартуем с КОНЦА файла (как `tail -f`), НЕ переигрывая историю. Иначе
    рестарт jev-api за секунду перечитывал бы тысячи старых строк → всплеск = ложный алерт ddos/burst
    и дубли событий в Loki. Пропускаем лог только за время простоя рестарта — это осознанный размен."""
    print("[telemetry] tailing", CADDY_LOG, flush=True)
    pos, ino, started = 0, None, False
    while True:
        try:
            st = os.stat(CADDY_LOG)
            if not started:                            # первый успешный stat: не переигрываем историю
                pos, ino, started = st.st_size, st.st_ino, True
            elif ino != st.st_ino or st.st_size < pos:  # новый файл / ротация / усечение
                ino, pos = st.st_ino, 0
            if st.st_size > pos:
                with open(CADDY_LOG, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            _process(json.loads(line))
                        except Exception:
                            pass
                    pos = fh.tell()
        except FileNotFoundError:
            pass
        except Exception as e:
            print("[telemetry] tail error:", repr(e)[:160], flush=True)
        time.sleep(1.0)


@app.on_event("startup")
def _start_background():
    if os.environ.get("JEV_TELEMETRY", "1") != "0":
        threading.Thread(target=_tail_caddy, name="caddy-tail", daemon=True).start()
    watchdog.start()          # активный аптайм-мониторинг: сайт лёг / серт истекает -> алерт


@app.on_event("startup")
async def _start_daily_report():
    """Ежедневный отчёт на email (+короткая сводка в Telegram) в 07:00 UTC — 1:1 как cybergod."""
    if os.environ.get("DAILY_REPORT", "1") == "0":
        return
    try:
        import asyncio
        import daily_report
        asyncio.create_task(daily_report.scheduler())
        print("[daily_report] scheduled at %s:00 UTC" %
              os.environ.get("DAILY_REPORT_HOUR", "7"), flush=True)
    except Exception as e:
        print("[daily_report] disabled:", repr(e), flush=True)


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    """Наш мини-Sentry: любое необработанное исключение -> evt=error -> alerts.observe_error."""
    try:
        ev = dict(evt="error", path=str(request.url.path)[:200], method=request.method,
                  exc=type(exc).__name__, msg=str(exc)[:200])
        telemetry.emit(**ev)
        alerts.observe_error(ev)
    except Exception:
        pass
    return JSONResponse({"error": "internal"}, status_code=500)


# ── чат ──────────────────────────────────────────────────────────────────────
class ChatIn(BaseModel):
    messages: list


def _llm(messages, model, timeout):
    payload = {"model": model, "messages": messages, "temperature": 0.6, "max_tokens": 600}
    req = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return d["choices"][0]["message"]["content"].strip()


@app.get("/api/health")
def health():
    return {"ok": True, "llm_configured": bool(KEY), "models": MODELS,
            "telemetry": os.environ.get("JEV_TELEMETRY", "1") != "0",
            "alerts": os.environ.get("ALERTS_ENABLED", "1") != "0",
            "watchdog": os.environ.get("WATCHDOG_ENABLED", "1") != "0",
            "caddy_log": CADDY_LOG}


def _chat_metric(ok, ms=0, model="", reason="", turns=0):
    """evt=chat: одна запись на диалоговый ход. Латентность LLM, какая модель ответила, успех/провал.
    Содержимое сообщений НЕ логируем (приватность) — только метрики. Тот же конвейер -> Loki."""
    try:
        ev = dict(evt="chat", ok=bool(ok), ms=int(ms), model=model or "-",
                  reason=reason or ("ok" if ok else "fail"), turns=int(turns))
        telemetry.emit(**ev)
        alerts.observe_chat(ev)
    except Exception:
        pass


@app.post("/api/chat")
def chat(inp: ChatIn, request: Request):
    # A BUDGET, NOT A LOGIN. This endpoint is public by design and spends model tokens on our
    # DigitalOcean key on every call. The sibling project jobhuntwow had the same shape with no
    # cap and took 1,538 unmetered calls from one address in eight days (2026-09-06). The model
    # here is server-chosen (MODELS) and roles are coerced below, so the only missing control was
    # a limit on how often one address may spend.
    _ip = ratelimit.client_ip(request)
    _ok, _retry, _why = ratelimit.check(_ip)
    if not _ok:
        _chat_metric(False, reason="rate_limited")
        return JSONResponse({"reply": "Слишком много запросов подряд. Попробуйте через минуту."},
                            status_code=429, headers={"Retry-After": str(_retry)})
    if not KEY:
        _chat_metric(False, reason="no_key")
        return JSONResponse({"reply": "ИИ-ассистент сейчас недоступен. Напишите Евгению напрямую: "
                                      "Telegram @feranicus или WhatsApp +49 157 8554 1545."}, status_code=200)
    hist = []
    for m in (inp.messages or [])[-MAX_HISTORY:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        text = str(m.get("content", ""))[:MAX_INPUT_CHARS].strip()
        if text:
            hist.append({"role": role, "content": text})
    if not hist:
        return {"reply": "Спросите меня о Евгении — о его работе, ценностях, семье или планах."}
    convo = [{"role": "system", "content": PERSONA}] + hist
    last_err = None
    for model in MODELS:
        t0 = time.time()
        try:
            reply = _llm(convo, model, TIMEOUT)
            if reply:
                _chat_metric(True, ms=(time.time() - t0) * 1000, model=model, turns=len(hist))
                return {"reply": reply, "model": model}
        except Exception as e:
            last_err = e
            continue
    print("[chat] all models failed:", repr(last_err), flush=True)
    _chat_metric(False, reason="all_models_failed", turns=len(hist))
    return JSONResponse({"reply": "Кассандра сейчас думает медленно 🙈 Напишите Евгению напрямую: "
                                  "Telegram @feranicus или WhatsApp +49 157 8554 1545."}, status_code=200)
