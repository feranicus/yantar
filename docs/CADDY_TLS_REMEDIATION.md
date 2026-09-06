# Caddy + Let's Encrypt TLS — Remediation Playbook

Reusable runbook for the class of TLS failures we hit shipping jev.best behind a shared edge Caddy.
Project-agnostic — the same three root causes recur on **any** Caddy + Let's Encrypt (ACME) setup,
especially when a domain is being migrated from a parking page / old host onto a new server.

> TL;DR: **99% of "Caddy won't get a cert" is one of three things** — (1) DNS isn't globally
> pointing at the server yet when the cert is requested, so the ACME challenge hits the wrong box and
> burns the Let's Encrypt failure limit; (2) `caddy reload` on an unchanged config is a no-op, so it
> never retries; (3) the `caddy:2-alpine` binary has a file capability that makes it crash under a
> hardened container, so it never comes up to answer the challenge. Fix DNS first, force issuance
> explicitly (admin `/load` + restart), and verify with an SNI-pinned handshake — not a browser.

---

## 1. Symptoms → what they actually mean

| What you see | Where | Meaning |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR`, `SSL_ERROR_NO_CYPHER_OVERLAP` | browser | Server answered :443 but has **no valid cert** for this hostname (challenge never succeeded). |
| `TLSV1_ALERT_INTERNAL_ERROR`, `sslv3 alert handshake failure` | `curl`/`openssl` | Same — TLS handshake aborts because Caddy has no cert to present for that SNI. |
| Site still shows the **old host / parking page** | browser | **DNS still resolves to the old server** (or a stale cache). Caddy on the new box is never even reached. |
| `HTTP 429`, `too many failed authorizations recently`, `rateLimited` | Caddy log | You've hit the LE **failed-validation limit** (5 per hostname per hour) from repeated bad attempts. |
| ACME log shows only *other* domains renewing, never yours | Caddy log | Caddy isn't (re)attempting your host — usually because the config didn't change (no-op reload) or it's in backoff. |
| `exec /usr/bin/caddy: operation not permitted` (crash-loop, Restarting 255) | container log | Not TLS — the caddy binary's **file capability** trips `no-new-privileges`/`cap_drop`. Container never serves, so it can't answer the challenge either. |

---

## 2. Root causes (the three that actually happen)

### A. Ordering: the cert was requested before DNS globally pointed at the server
Let's Encrypt validates by **connecting to your public hostname** over the internet:
- **TLS-ALPN-01** → connects to `:443`
- **HTTP-01** → connects to `:80`

If the domain's public A/AAAA record still resolves to the **old host** (registrar parking such as
Squarespace, a previous provider, Cloudflare in front, etc.) *anywhere in the world*, the challenge
lands on the wrong server and **fails**. Every failed attempt spends one token of the LE
failed-validation budget. Do this a handful of times and you're rate-limited, which *looks* like a
Caddy bug but is self-inflicted by wiring the vhost before DNS was ready.

### B. `caddy reload` on an unchanged config is a no-op → it never retries
Caddy only re-provisions certificates when the loaded config **changes**. After a failed issuance it
backs off and caches the failure. Re-running a deploy that reloads an **identical** Caddyfile does
**nothing** — so people "reload 10 times" and stay broken. You must actively force a fresh attempt.

### C. `caddy:2-alpine` binary has a file capability that crashes hardened containers
The official `caddy:2-alpine` ships `/usr/bin/caddy` with `security.capability` xattr
(`cap_net_bind_service=+ep`, so non-root can bind :80/:443). Under a hardened container —
`security_opt: no-new-privileges:true` **+** non-root `user:` **+** `cap_drop: [ALL]` — the kernel
refuses `execve` of a file that carries capabilities the process can't receive → **EPERM crash-loop**.
The server never starts, so it can't answer the ACME challenge. (Separate from TLS, same net effect.)

---

## 3. Architecture note (applies to both patterns)

- **Standalone Caddy**: Caddy terminates TLS on :443 and gets its own cert per site. Everything below
  applies directly to that Caddy.
- **Shared edge Caddy** (our estate: `videodead-caddy-1` terminates :443 for *all* sites; each app's
  in-container Caddy just serves static on :8080): **only the EDGE Caddy talks to Let's Encrypt.** The
  per-app Caddy needs no cert and no ACME. Do all TLS remediation on the **edge** Caddy and its
  Caddyfile (`/opt/videodead/Caddyfile`), never the app container. The app container's job is only to
  be reachable on the internal network so the edge can proxy to it.

---

## 4. Remediation — do these in order (do NOT skip step 1)

### Step 1 — Prove DNS is globally correct *first* (public resolver, not local cache)
```bash
# must return the SERVER IP from multiple public resolvers, everywhere:
dig +short example.com @1.1.1.1
dig +short example.com @8.8.8.8
dig +short www.example.com @1.1.1.1
# DoH check (bypasses any local resolver entirely):
curl -s 'https://dns.google/resolve?name=example.com&type=A' | grep -o '"data":"[^"]*"'
```
If these do **not** all show the server IP, **stop** — do not attempt/retry issuance. Fix DNS at the
registrar (add/point A `@` and `www` to the server IP; delete leftover parking A-records, `www` CNAME,
and any `HTTPS`/`SVCB` record the registrar auto-added — those silently override and send browsers to
the old host). Lower the TTL *before* a migration so this converges in minutes, not hours.

### Step 2 — Prove the server actually answers on :443 / :80 (container is up, not crash-looping)
```bash
docker ps --filter name=caddy            # is the edge Caddy Up (healthy), not Restarting?
docker logs --tail 50 <caddy-container>  # look for 'operation not permitted' (root cause C)
```
If you see `exec /usr/bin/caddy: operation not permitted`, apply the **capability fix** (section 6)
and rebuild before touching TLS.

### Step 3 — Force a fresh issuance (because reload is a no-op)
Pick the method that matches your setup:
```bash
# (a) Admin API load — POST the CURRENT config; provisioning re-runs even if identical:
docker exec <caddy> sh -c 'caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile' \
  && docker exec <caddy> sh -c 'curl -s -X POST http://localhost:2019/load \
       -H "Content-Type: text/caddyfile" --data-binary @/etc/caddy/Caddyfile'
# (b) If admin API is off or (a) didn't trigger a new cert — restart the process:
docker restart <caddy>
```
`docker restart` is the reliable "make Caddy re-evaluate everything now" hammer. On a shared edge it's
acceptable — neighbours come back in seconds with their **existing** cached certs; only the missing one
is (re)issued.

### Step 4 — Watch issuance succeed (or read the exact error)
```bash
docker logs -f <caddy> 2>&1 | grep -iE 'acme|certificate|challenge|rateLimited|429|obtain'
```
Success looks like `certificate obtained successfully` / `... for [example.com]`. A `429` /
`rateLimited` / `too many failed authorizations` means you're in the penalty box → section 5.

### Step 5 — Verify with an SNI-pinned handshake (NOT a browser — caches lie)
```bash
# Pin the hostname to the server IP so DNS/browser cache is irrelevant:
echo | openssl s_client -connect <SERVER_IP>:443 -servername example.com 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates
# Expect: issuer = Let's Encrypt, subject/SAN = example.com, notAfter ~90 days out.
curl -sv --resolve example.com:443:<SERVER_IP> https://example.com/ -o /dev/null 2>&1 | grep -i 'issuer\|subject\|HTTP/'
```
Only call it fixed when the pinned handshake returns *your* cert. The browser may still show the old
host for a while due to local DNS cache — `ipconfig /flushdns` (Windows) / `sudo dscacheutil
-flushcache` (macOS) + hard reload to confirm client-side.

---

## 5. If you're rate-limited (the self-inflicted trap)

**Let's Encrypt production limits that bite here (verified 2026):**
- **Failed validations: 5 per hostname, per account, per hour.** Refills **1 every 12 minutes** (so a
  full stop clears in ~60 min). This is the one you hit by retrying against bad DNS.
- New certs per registered domain: **50 / 7 days.** Duplicate cert (same exact hostnames): **5 / 7 days**
  (refill 1 / 34 h).
- Sustained failures pause issuance for the identifier; it recovers slowly (≈1/day) until a success.

**Recovery:**
1. **Stop retrying.** Every retry against a still-broken setup deepens the hole.
2. Fix the *real* cause (DNS in step 1, reachability/cap in step 2) **before** the next attempt.
3. Wait for the window to refill (~12 min per token, ~1 h for a full reset).
4. **Test against LE staging** while you iterate — staging has vastly higher limits and won't burn prod:
   - Caddy global option: `acme_ca https://acme-staging-v02.api.letsencrypt.org/directory`
   - Staging certs are **untrusted** (browser warning) — that's expected; it only proves the challenge
     path works. Remove the staging line and reload to get the real cert once green.

---

## 6. Related Caddy landmine — the `caddy:2-alpine` capability crash

If the container crash-loops with `exec /usr/bin/caddy: operation not permitted`:
```dockerfile
FROM caddy:2-alpine
# The distro binary carries a file capability (cap_net_bind_service=+ep). Under
# no-new-privileges + non-root + cap_drop ALL, execve of a capability-bearing file returns EPERM.
# A plain `cp` produces a copy WITHOUT the security.capability xattr → executes fine.
RUN cp /usr/bin/caddy /usr/local/bin/caddy
# ... build/stage your site ...
# Make sure CMD runs the COPY, and that the copy is early in PATH:
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
```
Keep the hardening (`read_only`, `cap_drop: [ALL]`, `no-new-privileges:true`, non-root `user:`) — the
`cp` is all that's needed. (Alternative: don't drop the cap / run as root — but the `cp` is cleaner.)

---

## 7. Prevention checklist (bake into every deploy)

- [ ] **DNS-first gate:** never wire the vhost or request a cert until `dig @1.1.1.1` / `@8.8.8.8` /
      DoH all return the server IP. Lower TTL before migrating a live domain.
- [ ] **Delete registrar leftovers:** old parking A-records, `www` CNAME, and any auto-added
      `HTTPS`/`SVCB` record — they override and send clients to the old host.
- [ ] **Idempotent force-cert path:** your deploy tool must have an explicit "re-provision now" action
      (admin `/load` + `docker restart`), because a plain reload of unchanged config fixes nothing.
- [ ] **Health-gate the deploy:** don't report success until an **SNI-pinned** handshake returns your
      cert — never trust a browser during migration.
- [ ] **Use LE staging while iterating**, switch to prod only when the challenge path is green.
- [ ] **Capability fix in the Dockerfile** if you run `caddy:2-alpine` hardened.
- [ ] Edge-vs-app: on a shared edge Caddy, remediate **only** the edge; app containers need no cert.

---

## 8. Copy-paste triage (fill in `HOST`, `IP`, `CADDY`)

```bash
HOST=example.com; IP=203.0.113.10; CADDY=edge-caddy

echo "== DNS (must == $IP) =="
for r in 1.1.1.1 8.8.8.8; do echo -n "$r: "; dig +short $HOST @$r; done

echo "== container up? (not Restarting / EPERM) =="
docker ps --filter name=$CADDY --format '{{.Names}} {{.Status}}'
docker logs --tail 20 $CADDY 2>&1 | grep -iE 'operation not permitted|acme|rateLimited|429|obtain' || true

echo "== force re-provision =="
docker exec $CADDY caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && docker restart $CADDY

echo "== verify (SNI-pinned, cache-proof) =="
sleep 8
echo | openssl s_client -connect $IP:443 -servername $HOST 2>/dev/null | openssl x509 -noout -issuer -dates
```

---

## 9. References
- Let's Encrypt rate limits — https://letsencrypt.org/docs/rate-limits/
- Let's Encrypt staging environment — https://letsencrypt.org/docs/staging-environment/
- Caddy automatic HTTPS — https://caddyserver.com/docs/automatic-https
- Caddy ACME challenges (TLS-ALPN-01 / HTTP-01) — https://caddyserver.com/docs/automatic-https#acme-challenges
- Caddy admin API (`/load`) — https://caddyserver.com/docs/api

*Cert store on disk (Caddy default):
`/data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/<host>/<host>.crt|.key`.*
