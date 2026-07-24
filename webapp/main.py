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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import telemetry
import alerts

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

app = FastAPI(title="jev.best API", version="2.0")
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
    """Робастный тейл: ждёт файл, переоткрывает при ротации (inode/усечение). Никогда не падает."""
    print("[telemetry] tailing", CADDY_LOG, flush=True)
    pos, ino = 0, None
    while True:
        try:
            st = os.stat(CADDY_LOG)
            if ino != st.st_ino or st.st_size < pos:   # новый файл / ротация / усечение
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
def _start_tailer():
    if os.environ.get("JEV_TELEMETRY", "1") != "0":
        threading.Thread(target=_tail_caddy, name="caddy-tail", daemon=True).start()


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
            "caddy_log": CADDY_LOG}


@app.post("/api/chat")
def chat(inp: ChatIn):
    if not KEY:
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
        try:
            reply = _llm(convo, model, TIMEOUT)
            if reply:
                return {"reply": reply, "model": model}
        except Exception as e:
            last_err = e
            continue
    print("[chat] all models failed:", repr(last_err), flush=True)
    return JSONResponse({"reply": "Кассандра сейчас думает медленно 🙈 Напишите Евгению напрямую: "
                                  "Telegram @feranicus или WhatsApp +49 157 8554 1545."}, status_code=200)
