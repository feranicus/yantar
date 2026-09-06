"""The public chat endpoint spends money. Prove the budget holds, and that a shopper does not.

Context (2026-09-06): the sibling project jobhuntwow exposed an unauthenticated POST /api/chat that
forwarded a CALLER-CHOSEN model to DigitalOcean on our key; one address made 1,538 calls in eight
days. jev.best's chat is deliberately public and must stay so, and it already avoids two of the three
mistakes -- the model comes from settings.CHAT_MODELS, and chat.respond() coerces every incoming
role to user/assistant so no caller can inject a system prompt. This file pins the third: a budget.

Stdlib only, no pytest, no httpx: it must run on the operator's machine with no setup.
"""
import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("JEV_TELEMETRY", "0")
sys.path.insert(0, os.path.join(ROOT, "webapp"))

FAILS = []


def ok_(cond, label):
    print(("  ok    " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def call(method, path, body=None, ip="203.0.113.9"):
    from main import app
    hdrs = [(b"host", b"jev.best"), (b"content-type", b"application/json"),
            (b"x-forwarded-for", ip.encode())]
    raw = json.dumps(body).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "https", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": hdrs, "client": (ip, 1234), "server": ("jev.best", 443)}
    out = {"status": None, "body": b""}
    sent = [False]

    async def receive():
        if sent[0]:
            await asyncio.sleep(3600)
        sent[0] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(app(scope, receive, send))
    return out


def main():
    import ratelimit, main as app_mod
    print("[1] the API schema is not published")
    for p in ("/openapi.json", "/docs", "/redoc"):
        r = call("GET", p)
        # The SPA fallback answers unknown GETs, so "not the schema" is the property, not a 404.
        ok_(r["status"] == 404 or b"openapi" not in r["body"][:400].lower(),
            "%s does not serve the API schema (status %s)" % (p, r["status"]))

    print("\n[2] the per-address budget refuses a script, with the right status")
    app_mod._llm = lambda messages, model, timeout: "ok"
    app_mod.KEY = "test-key"
    ratelimit._HITS.clear()
    body = {"messages": [{"role": "user", "content": "hello"}]}
    codes = [call("POST", "/api/chat", body, ip="198.51.100.7")["status"] for _ in range(20)]
    allowed = codes.count(200)
    ok_(allowed == ratelimit.PER_IP_PER_MIN,
        "exactly the minute budget got through (%d of 20, budget %d)" % (allowed, ratelimit.PER_IP_PER_MIN))
    ok_(429 in codes, "the rest were refused with 429, not 200")
    last = call("POST", "/api/chat", body, ip="198.51.100.7")
    ok_(last["status"] == 429, "still refused")
    ok_(b"reply" in last["body"],
        "the refusal keeps the widget's contract, so the chat degrades instead of breaking")

    print("\n[3] a REAL shopper is never caught by it")
    ratelimit._HITS.clear()
    conv = [call("POST", "/api/chat", body, ip="203.0.113.44")["status"] for _ in range(6)]
    ok_(all(c == 200 for c in conv), "a six-turn conversation is untouched (%s)" % conv)

    print("\n[4] one address cannot exhaust another, and a /24 is one actor")
    ratelimit._HITS.clear()
    for _ in range(ratelimit.PER_IP_PER_MIN):
        call("POST", "/api/chat", body, ip="198.51.100.7")
    other = call("POST", "/api/chat", body, ip="192.0.2.55")["status"]
    ok_(other == 200, "an unrelated address is unaffected")
    ratelimit._HITS.clear()
    net = [call("POST", "/api/chat", body, ip="198.51.100.%d" % (10 + i))["status"] for i in range(25)]
    ok_(429 in net, "a /24 sweeping its own addresses still hits the network budget")

    print("\n[5] it fails OPEN: a bug here must never take the shop down")
    ratelimit._HITS.clear()
    boom = ratelimit._net
    try:
        ratelimit._net = lambda ip: 1 / 0
        ok_(ratelimit.check("203.0.113.1")[0] is True, "an exception inside the limiter ALLOWS")
    finally:
        ratelimit._net = boom

    print("\n[6] the model is chosen by the SERVER, never by the caller")
    src = open(os.path.join(ROOT, "webapp", "main.py"), encoding="utf-8").read()
    cls = src[src.index("class ChatIn"):src.index("def _llm")]
    ok_("model" not in cls, "ChatIn has no `model` field -- the jobhuntwow defect cannot occur here")
    csrc = src
    ok_('role = "assistant" if m.get("role") == "assistant" else "user"' in csrc,
        "incoming roles are coerced, so no caller can inject a system prompt")

    print("\n%d checks failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
