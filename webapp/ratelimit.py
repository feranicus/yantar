"""A per-address budget for the ONE endpoint on this site that spends money.

Ported verbatim from the POLARA shop (2026-09-06). These are separate deployables with
no shared package, so the module is duplicated rather than imported; it is stateless,
self-contained and stdlib-only, and a shared library would be a bigger change than the
problem. If a third project needs it, publish it properly instead of copying again.

WHY (2026-09-06). The sibling project jobhuntwow exposed an unauthenticated `POST /api/chat` that
forwarded a CALLER-CHOSEN model to DigitalOcean on our key. One address made 1,538 calls in eight
days before anyone noticed, and the bill was the first signal.

jev.best's chat is a deliberately PUBLIC site assistant and must stay public: a visitor cannot be
asked to register before asking a question about the site owner. So authentication is the wrong
control here. What is missing is a BUDGET. Two of the three things that made jobhuntwow expensive
are already absent by design in this file's neighbours:

  * the model is chosen by the SERVER (settings.CHAT_MODELS), never by the request, so nobody can
    ask for a premium model we never configured;
  * `chat.respond()` coerces every incoming role to user/assistant, so a caller cannot inject a
    system prompt and turn the shop assistant into a general-purpose chatbot.

The third -- a cap on how often one address may spend -- is this file.

DESIGN NOTES, each one a decision rather than a default:
  * IN-MEMORY, no Redis. This is a single container; a dependency would be a bigger change than
    the problem. State is lost on restart, which is acceptable: the window is minutes.
  * PER /24 as well as per address. A rented droplet range is one actor with many addresses, which
    is exactly the shape the sibling incident had.
  * FAILS OPEN. A bug here must never take the shop's chat down; a refusal is cheaper than an
    outage, but an exception is neither.
  * 429 with Retry-After, the correct status, so a legitimate client can back off politely.
  * The refusal is LOGGED as an event, so a burst is visible in Grafana instead of being silently
    absorbed. A control nobody can see is a control nobody trusts.
"""
import ipaddress
import os
import threading
import time

# Generous for a human shopper (a conversation is a handful of turns), tight against a script.
PER_IP_PER_MIN = int(os.environ.get("JEV_RATE_IP_MIN", "8"))
PER_IP_PER_DAY = int(os.environ.get("JEV_RATE_IP_DAY", "120"))
PER_NET_PER_MIN = int(os.environ.get("JEV_RATE_NET_MIN", "20"))
ENABLED = os.environ.get("JEV_RATE_LIMIT", "1") != "0"

_LOCK = threading.Lock()
_HITS = {}          # key -> [timestamps]


def client_ip(request):
    """ONE proxy (caddy) sits in front, so the first X-Forwarded-For entry is the client.
    Attacker-controlled text: used for rate limiting and logging, never for authorisation."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _net(ip):
    """The /24 (or /48 for IPv6): one rented range is one actor, whatever the last octet says."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if a.version == 4:
        return str(ipaddress.ip_network(ip + "/24", strict=False))
    return str(ipaddress.ip_network(ip + "/48", strict=False))


def _count(key, window, now):
    hits = _HITS.get(key) or []
    hits = [t for t in hits if now - t < window]
    _HITS[key] = hits
    return len(hits)


def check(ip):
    """(allowed, retry_after_seconds, reason). Never raises."""
    if not ENABLED or not ip:
        return True, 0, ""
    try:
        now = time.time()
        net = _net(ip)
        with _LOCK:
            if _count("m:" + ip, 60, now) >= PER_IP_PER_MIN:
                return False, 60, "per-address minute budget (%d)" % PER_IP_PER_MIN
            if _count("d:" + ip, 86400, now) >= PER_IP_PER_DAY:
                return False, 3600, "per-address daily budget (%d)" % PER_IP_PER_DAY
            if _count("n:" + net, 60, now) >= PER_NET_PER_MIN:
                return False, 60, "per-network minute budget (%d, %s)" % (PER_NET_PER_MIN, net)
            # Only a call we are going to ALLOW is recorded; a refusal costs nothing and must not
            # extend its own penalty, or one blocked script would lock a whole /24 out for a day.
            for k, w in (("m:" + ip, 60), ("d:" + ip, 86400), ("n:" + net, 60)):
                _HITS.setdefault(k, []).append(now)
            if len(_HITS) > 20000:              # unbounded growth is its own denial of service
                cutoff = now - 86400
                for k in [k for k, v in _HITS.items() if not v or v[-1] < cutoff]:
                    _HITS.pop(k, None)
        return True, 0, ""
    except Exception:
        return True, 0, ""                      # FAILS OPEN: never take the shop down
