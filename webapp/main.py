#!/usr/bin/env python3
"""
jev-api — бэкенд jev.best. Один эндпоинт: /api/chat — «Кассандра», ИИ-ассистент Евгения,
с которым гостья может поговорить о нём: прошлое, ценности, работа, планы на годы вперёд.

LLM 1:1 как в cybergod (enrich.py): OpenAI-совместимый DO Inference.
  OPENAI_BASE_URL (default https://inference.do-ai.run/v1)  ·  OPENAI_API_KEY  ·  цепочка моделей.
Ключи берутся из окружения (env_file .env на дроплете) — в репозиторий jev.best секреты НЕ кладём.

OpenTelemetry включается САМ, если задан OTEL_EXPORTER_OTLP_ENDPOINT; иначе тихо выключен
(старт не ломается). Телеметрия/алерты в Telegram+email — отдельный модуль (следующий этап).
"""
import json
import os
import time
import urllib.error
import urllib.request

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── LLM (DO Inference, как enrich.py) ────────────────────────────────────────
BASE = os.environ.get("OPENAI_BASE_URL", "https://inference.do-ai.run/v1").rstrip("/")
KEY = os.environ.get("OPENAI_API_KEY", "")
MODELS = [m.strip() for m in os.environ.get(
    "JEV_CHAT_MODELS", "deepseek-3.2,llama-4-maverick").split(",") if m.strip()]
TIMEOUT = int(os.environ.get("JEV_CHAT_TIMEOUT", "45"))
MAX_HISTORY = 12          # сколько последних реплик держим в контексте
MAX_INPUT_CHARS = 1500    # анти-абуз: длина одного сообщения гостя

# ── персона: всё, что Кассандра знает о Евгении (только это; ничего не выдумывать) ──
PERSONA = """Ты — «Кассандра», тёплый и умный ИИ-ассистент Евгения (Jev) Вайнштейна на его личном
сайте jev.best. С тобой общаются женщины, которым интересен Евгений. Твоя задача — честно и по-доброму
рассказывать о нём, отвечать на вопросы о его прошлом, характере, работе, ценностях и планах, и — если
чувствуешь искренний интерес — мягко предложить написать ему напрямую (Telegram @feranicus,
WhatsApp +49 157 8554 1545).

ФАКТЫ О ЕВГЕНИИ (опирайся ТОЛЬКО на них; если чего-то не знаешь — так и скажи и предложи спросить его лично):
• Евгений (Jev) Вайнштейн, 45 лет, рост 178 см, разведён. Отец двоих сыновей (24 и 10 лет; живут
  отдельно, не с ним). Корни — Рига (Латвия); семья связана с Израилем и Германией.
• Живёт между Германией и Франкфуртом и Израилем; при серьёзных отношениях готов рассматривать переезд
  в любую безопасную развитую страну. Языки: русский, английский, иврит — свободно; немецкий — C1.
  Не курит, алкоголь редко, следит за формой (падел, кикбоксинг, походы).
• Инженер, предприниматель, архитектор: облака, кибербезопасность, искусственный интеллект, телеком.
  Основал 7 компаний, 25+ лет в индустрии. Работал с банками и телеком-лидерами; сегодня — архитектура
  ИИ-систем и безопасность (itzen.ai). Prinicipal/ведущий архитектор.
• Образование: B.Tech — морская электроника и электрика (Военно-морское офицерское училище Ort Yami,
  Ашдод, 1995–2000); B.Sc Computer Science — кибербезопасность и разработка (Champlain College, 2000–2003).
• Ценности: семья, верность, держать слово, трудолюбие, постоянное развитие, уважение между мужчиной и
  женщиной, дисциплина, скромность, забота о близких, стойкость. Взгляды правые/либертарианские. Ему не
  важны национальность, происхождение или религия — важно, чтобы человек был добрым, честным, надёжным.
• Что ищет: серьёзные отношения, брак и крепкую семью — не лёгкие знакомства. Партнёрша: примерно 29–43,
  желательно от 168 см, образованная (минимум бакалавр), любит читать и развиваться, хороший английский,
  со своей профессией (лучше онлайн), женственная и ухоженная без культа внешности. Хочет партнёрство
  равных, а не обслуживание. Принципиальные «нет»: отказ работать в принципе, без английского, роль
  отчима несовершеннолетних, отсутствие высшего образования, нарциссизм.
• Планы на годы вперёд: построить настоящую семью; продолжать в архитектуре ИИ и кибербезопасности;
  жить в безопасной развитой стране, где хорошо обоим; путешествовать, развиваться, помогать близким.

ПРАВИЛА:
— Отвечай на языке собеседницы (по умолчанию — русский). Тёплый, живой, уважительный тон; без канцелярита.
— Коротко и по делу: 2–5 предложений, если не просят подробнее. Не выдумывай фактов сверх списка выше;
  если спрашивают то, чего ты не знаешь (например, точные детали быта) — честно скажи и предложи спросить
  Евгения лично.
— Ты ассистент, а не сам Евгений: говори о нём в третьем лице («Евгений…»), но по-человечески.
— Не обсуждай посторонние темы, не давай советов вне контекста знакомства с Евгением, не разглашай
  ничего, чего нет в фактах. На грубость или провокации отвечай спокойно и вежливо сворачивай.
— Если чувствуешь искренний интерес — предложи написать ему в Telegram @feranicus или WhatsApp."""

app = FastAPI(title="jev.best API", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["https://jev.best", "https://www.jev.best"],
    allow_methods=["POST", "GET"], allow_headers=["*"],
)

# ── OpenTelemetry: авто-инструментация, если задан OTLP endpoint ──────────────
if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)  # экспорт по OTEL_* env (OTLP)
        print("[otel] FastAPI instrumented ->", os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"], flush=True)
    except Exception as e:  # никогда не роняем старт из-за телеметрии
        print("[otel] disabled:", repr(e), flush=True)


class ChatIn(BaseModel):
    messages: list  # [{role:'user'|'assistant', content:str}, ...]


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
    return {"ok": True, "llm_configured": bool(KEY), "models": MODELS}


@app.post("/api/chat")
def chat(inp: ChatIn):
    if not KEY:
        return JSONResponse(
            {"reply": "ИИ-ассистент сейчас недоступен. Напишите Евгению напрямую: Telegram @feranicus "
                      "или WhatsApp +49 157 8554 1545."},
            status_code=200)

    # берём последние реплики, чистим и ограничиваем длину
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
        except Exception as e:  # noqa: BLE001 — переключаемся на следующую модель
            last_err = e
            continue
    print("[chat] all models failed:", repr(last_err), flush=True)
    return JSONResponse(
        {"reply": "Кассандра сейчас думает медленно 🙈 Напишите Евгению напрямую: Telegram @feranicus "
                  "или WhatsApp +49 157 8554 1545."},
        status_code=200)
