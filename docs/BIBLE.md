# THE BIBLE — jev.best. Read this first.

Purpose: a fresh Claude (or human) should understand this entire project from THIS file alone, in
minutes, without spelunking. Every value below was read from the actual files, not remembered.
If any of it changes, update this file in the same commit (standing rule).

> TL;DR — A **React + Vite PWA** personal dossier, Russian-language, served at **jev.best**.
> It is the **fifth Docker stack** on the shared DigitalOcean droplet **64.225.108.200** (Frankfurt).
> Compose project `jevbest` at `/opt/jevbest`, one container `jev-web` running **Caddy on :8080**,
> reachable only through the estate's **shared edge Caddy on :443**. Deploy is `python jev.py deploy`
> — direct over SSH, **GitHub is not in the path**. **There are no secrets in this project.**


## 0. What this is, and what it is NOT

`jev.best` is a personal dossier / profile site for Evgeny (Jev) Vainsteins: a five-tab mobile-first
PWA with a compatibility-check widget. Static frontend, no backend, no database, no API, no auth.

**Do not confuse it with the neighbours.** It shares only the physical droplet with them:

| Project | Repo / folder | What it is | Deploy |
|---|---|---|---|
| **jev.best** | this folder | THIS project. Personal dossier PWA. | `python jev.py deploy` |
| JobHuntWOW | `jobhuntwow-app` | React+FastAPI job-application product | `python jhw.py deploy` |
| Colt / cybergod | `Linkedin Scraper` root (`electronic` repo) | cyber pre-sales bots | `python ship.py` |
| VideoDead | — | streaming; **owns the shared edge Caddy** | sibling |

Never import code, config or conventions across these. jev.best has no relationship to Tailor,
Shodan, the apply sandbox, Telegram bots or DO Inference. If you find yourself reaching for
`DO_INFERENCE_KEY` in this project, you are in the wrong folder.

**It previously lived on GitHub Pages** (`feranicus.github.io/feranicus/yantar.html`) and was moved
off deliberately. Pages is no longer in the picture; do not re-add a workflow that republishes it.


## 1. Placement on the droplet

- **Provider:** DigitalOcean · **Region:** FRA1 (Frankfurt) · **User:** `root` · **SSH:** port 22.
- **Public IP:** `64.225.108.200` (default in `jev.py`; override with env `DROPLET_HOST`).

Five unrelated stacks now live side by side. Nothing here may disturb the others:

| Stack | Compose project | Serves | Owner |
|---|---|---|---|
| **jevbest** | `-p jevbest` at `/opt/jevbest` | `jev.best` | **this project** |
| jobhuntwow | `-p jobhuntwow` at `/opt/jobhuntwow` | `jobhuntwow.com` | JobHuntWOW |
| colt-stack | `-p colt-stack` at `/opt/colt-stack` | `cybergod.ai`, assess bots | Colt |
| videodead | `videodead_*` | streaming; **owns edge Caddy (:443)** and net `videodead_appnet` | sibling |
| Amnezia VPN / Joplin | own containers | VPN (UDP) / notes | untouched |

jev.best couples to that environment through **exactly one thing**: the external Docker network
`videodead_appnet`, plus a marker-scoped block appended to the shared Caddyfile. It does **not** use
the `colt-stack_colt_events` log volume (no structured event stream — see §9).


## 2. Domain & DNS

| Domain | Points at | Serves | Registrar |
|---|---|---|---|
| `jev.best` | droplet `64.225.108.200` | this app, via edge Caddy → `jev-web:8080` | **Squarespace** |
| `www.jev.best` | droplet | 301 redirect to apex | Squarespace |

Required records:

| Type | Name | TTL | Value |
|---|---|---|---|
| A | @ | 1 hr | `64.225.108.200` |
| A | www | 1 hr | `64.225.108.200` |

**THE LANDMINE — the HTTPS/SVCB record.** Squarespace's default DNS preset includes an `HTTPS` (type
65 / SVCB) record carrying `ipv4hint="198.185.159.x"`. Chrome honours that hint **in preference to
the A record**. While it exists, the domain keeps resolving to Squarespace no matter how many times
you fix the A records, and the symptom looks exactly like DNS propagation lag. Delete it, along with
the four default A records (`198.49.23.144/145`, `198.185.159.144/145`) and the
`www → ext-sq.squarespace.com` CNAME.

TLS is issued by the edge Caddy automatically over **TLS-ALPN-01 on the already-open :443**. No new
firewall ports, no cert files to manage, no certbot. Usually under a minute after the first request.

Check reality, don't assume: `python jev.py dns` prints what the droplet actually resolves.


## 3. The two Caddys — read this before touching anything

This is the single most confusable thing in the project. There are **two** Caddy instances and they
do different jobs.

```
internet :443
     │
     ▼
┌─────────────────────────┐   videodead_appnet    ┌──────────────────────┐
│  EDGE Caddy             │  ───────────────────► │  jev-web (container) │
│  container videodead-   │   jev-web:8080        │  IN-CONTAINER Caddy  │
│  caddy, owned by the    │                       │  serves /srv (static)│
│  videodead stack.       │                       │  admin/auto_https OFF│
│  Terminates TLS for     │                       └──────────────────────┘
│  EVERY site on the box. │
└─────────────────────────┘
   config: SHARED Caddyfile, bind-mounted.        config: srv.Caddyfile, baked into image.
   We append a marker-scoped block to it.         Ours alone. Change freely.
```

| | Edge Caddy | In-container Caddy |
|---|---|---|
| Container | `videodead-caddy` | `jev-web` |
| Owned by | videodead stack — **shared, be careful** | this project — yours |
| Config file | shared Caddyfile, discovered via `docker inspect` | `/etc/caddy/Caddyfile` from `srv.Caddyfile` |
| Our source | `deploy/caddy/jev.best.caddy` | `srv.Caddyfile` |
| Does TLS | **yes**, for the whole box | no (`auto_https off`) |
| Listens | `:80`, `:443` public | `:8080`, internal only |

Editing the shared Caddyfile by hand is forbidden. `deploy/fix_caddy.py` does it, and only inside
its markers:

```
# >>> jev.best (managed by jev.py deploy) >>>
   ... our vhost ...
# <<< jev.best <<<
```

**Why it never uses `sed -i`:** the shared Caddyfile is bind-mounted into the edge container.
`sed -i` writes a new file and swaps the inode — the container keeps reading the old one and your
change silently does nothing. `fix_caddy.py` always rewrites **in place** (`open(path, 'w')`),
after taking a `Caddyfile.bak.<timestamp>` next to it. It then runs `caddy validate` and, on
failure, restores the original before exiting non-zero.


## 4. Directory map — this PC

```
jev-best/
├── jev.py                     # THE orchestrator. One verb per operation. Start here.
├── package.json               # react 18.3 · vite 5.4 · @vitejs/plugin-react 4.3 · vite-plugin-pwa 0.21
├── vite.config.js             # build + PWA manifest + Workbox runtime caching for Google Fonts
├── index.html                 # Vite entry; holds apple-mobile-web-app-* meta and font <link>s
├── srv.Caddyfile              # config for the IN-CONTAINER Caddy (see §3)
├── Dockerfile.web             # multi-stage: node:22-alpine build → caddy:2-alpine serve
├── docker-compose.web.yml     # PROD: project `jevbest`, joins videodead_appnet
├── .dockerignore · .gitignore
├── src/
│   ├── main.jsx               # React root (StrictMode)
│   ├── App.jsx                # tab shell + the 5 screens
│   ├── data.jsx               # ALL content + the match engine (`evaluate`)
│   ├── styles.css             # every style; CSS custom properties at :root
│   └── components/
│       ├── ui.jsx             # Spec, Cards, Pull, Flags, Timeline, Stats, Links, Press…
│       ├── TabBar.jsx         # bottom tab bar + inline SVG icons
│       ├── Boot.jsx           # terminal-style boot overlay
│       ├── AmberField.jsx     # canvas: particles suspended in resin
│       └── Match.jsx          # compatibility widget UI
├── public/icons/              # icon-192, icon-512, maskable-512, apple-touch-icon (generated PNGs)
├── deploy/
│   ├── caddy/jev.best.caddy   # the EDGE vhost block (marker-scoped; deploy re-appends it)
│   └── fix_caddy.py           # runs ON the droplet: discovers, patches, validates, reloads
└── docs/
    ├── BIBLE.md               # ← this file
    └── DEPLOY.md              # deploy + DNS walkthrough, in Russian
```

Not in git, not shipped: `node_modules/`, `dist/`, `dev-dist/`, `__pycache__/`.

Local dev: `npm install` then `npm run dev` → `http://localhost:5173`.


## 5. Directory map — the droplet

```
/opt/jevbest/                 # THIS app's build context + runtime (project `jevbest`)
├── docker-compose.web.yml    # scp'd from PC each deploy
├── Dockerfile.web · srv.Caddyfile · package.json · package-lock.json
├── vite.config.js · index.html
├── src/ · public/ · deploy/
└── (no .env — this project has no secrets)
```

Shared Docker objects jevbest attaches to (never redefines):
- network `videodead_appnet` (external) — how the edge Caddy reaches `jev-web:8080`

Neighbouring dirs that are **not ours**: `/opt/jobhuntwow`, `/opt/colt-stack`, `/opt/videodead`.


## 6. Deploy — exactly how it works

**You never SSH by hand for changes.** Every droplet change is a committed artifact applied by ONE
command. SSH is for read-only diagnostics only.

### Connection (from `jev.py`)
- Target `root@64.225.108.200`. Override via `DROPLET_HOST`, `DROPLET_USER`, `SSH_KEY`.
- Key auto-detected from `~/.ssh/{id_ed25519,id_rsa,id_ecdsa}`.
- SSH opts always carry `ConnectTimeout=10`, `ServerAliveInterval=15`,
  `StrictHostKeyChecking=accept-new`.

### `python jev.py deploy` does, in order:
1. tar the build context — `SHIP_FILES` (`package.json`, `package-lock.json`, `vite.config.js`,
   `index.html`, `srv.Caddyfile`, `Dockerfile.web`, `docker-compose.web.yml`) plus `SHIP_DIRS`
   (`src`, `public`, `deploy`); skipping `node_modules, dist, __pycache__, .git, .vite` and
   anything matching `*.env`.
2. scp to `/opt/jevbest`, untar, delete the archive.
3. **the droplet builds the image itself**: `docker compose -p jevbest -f docker-compose.web.yml
   build`. `dist/` is never shipped — what the server built is what the server runs.
4. `up -d --force-recreate`. **Never `--remove-orphans`** — the string appears in this repo only as
   warning comments (`jev.py` ×3, `docker-compose.web.yml` ×1), never as an actual flag. Adding it
   would delete colt-promtail and the bots.
5. `deploy/fix_caddy.py` on the droplet: find the edge container and its Caddyfile via
   `docker inspect`, back up, rewrite in place, `caddy validate`, `caddy reload`.
6. **verify in three places** — inside the container, across `videodead_appnet`, and from the public
   internet over HTTPS. All three must return `jev-best` from `/__whoami`. "The site answers" is not
   accepted as proof of routing.

### The verbs

| command | does |
|---|---|
| `python jev.py deploy` | ship source → droplet builds → up → wire edge Caddy → verify |
| `python jev.py status` | is `jev.best` actually serving OUR container (probe-tagged) |
| `python jev.py diagnose` | who is on appnet, what's in the shared Caddyfile, what resolves |
| `python jev.py logs` | tail `jev-web` (JSON access logs from the in-container Caddy) |
| `python jev.py dns` | GLOBAL DNS via a public resolver (dns.google) + what the droplet's resolver caches |
| `python jev.py cert` | force the edge Caddy to re-issue jev.best's TLS cert (remove+readd vhost); prints the real ACME reason if it still fails (§11.10) |
| `python jev.py flush` | flush the droplet resolver's DNS cache (fixes "`status`/`dns` shows the old IP") |
| `python jev.py down` | stop only `jevbest`; neighbours untouched |

`status`/`dns`/`verify` do NOT trust the droplet's `getent` or a browser — both cache DNS. `status`
proves reality by connecting straight to the droplet IP with the hostname pinned (SNI=jev.best),
which validates the real cert independent of any cache; `dns` asks a public resolver. (Lesson from
`ship.py`: prove reality from the right vantage point, don't accept "the site answered".)

**No GitHub, no GHCR, no CI.** Nothing about this project depends on a git remote.


## 7. The container

Built by `Dockerfile.web`, two stages:

1. `node:22-alpine` — `npm ci` (falls back to `npm install`), `npm run build` → `/app/dist`.
2. `caddy:2-alpine` — `srv.Caddyfile` → `/etc/caddy/Caddyfile`, `dist` → `/srv`.

Runtime hardening (all in `docker-compose.web.yml`, all load-bearing):

| setting | why |
|---|---|
| `networks: [appnet]` — **exactly one** | a container on two networks makes Docker DNS hand the edge Caddy an unreachable IP → intermittent 502. This was a real outage on the neighbouring stack; it is a hard rule. |
| no `ports:` | the only public surface on the box is the edge Caddy's `:443` |
| `user: "1000:1000"`, `:8080` | non-root, high port ⇒ no `CAP_NET_BIND_SERVICE` needed |
| `cap_drop: [ALL]`, `no-new-privileges` | nothing to drop privileges *to* |
| `read_only: true` + `tmpfs /tmp` (32m) | `admin off` + `auto_https off` + `persist_config off` in the Caddyfile means there is nothing to write; `XDG_DATA_HOME`/`XDG_CONFIG_HOME` point at `/tmp` for anything Caddy still wants |
| `mem_limit 128m`, `cpus 0.5` | static files; it does not need more |
| healthcheck greps for `jev-best` | a 200 is not proof; the body is |
| json-file logs, `10m × 3` | bounded, same as every other stack |


## 8. Ports & network exposure

| Port | Host | Exposure | What |
|---|---|---|---|
| 443 | droplet | **public** | edge Caddy → `jev-web:8080` (the only public surface) |
| 80 | droplet | public | edge Caddy, HTTP→HTTPS redirect |
| 22 | droplet | public | SSH (deploy + diagnostics) |
| 8080 | container | **internal only** | in-container Caddy, reachable only on `videodead_appnet` |
| 5173 | local | `127.0.0.1` | `npm run dev` (Vite) |

`jev-web` publishes nothing. If you ever see `ports:` appear in `docker-compose.web.yml`, that is a
regression — remove it.


## 9. Secrets and data — there are none

State this plainly so nobody goes looking:

- **No `.env`.** Not on the PC, not at `/opt/jevbest`. Nothing to protect.
- No API keys, no tokens, no database, no user accounts, no forms, no analytics, no trackers.
- The compatibility widget computes **entirely in the browser**. Nothing is transmitted or stored;
  there is no server-side code to receive it.
- Only external requests: Google Fonts (cached by the service worker after first load).
- `.gitignore` still blocks `.env`/`*.env` and the tar filter drops them, as a backstop — if a
  secret ever becomes necessary, the guardrails are already there.

**Observability (added rev 46) — reuses the SHARED stack, never a second one.** `python jev.py obs`
ships `deploy/obs/` and starts **one** container, `jev-promtail` (project `jevbest`, on
`videodead_appnet`), which uses `docker_sd` to tail **only** `jev-web`'s Caddy JSON access logs and
pushes them to the **existing** `videodead-loki-1` (`LOKI_URL=http://videodead-loki-1:3100/...`),
labelled `job=jevbest` / `container=jev-web`. The dashboard `deploy/obs/grafana/jevbest.json`
(uid `jevbest-web`: request rate, 2xx/3xx/4xx/5xx, p50/p95 latency, top paths, methods, bytes,
unique IPs, live stream) is imported into the **existing** Grafana via its HTTP API — set
`GRAFANA_URL` (e.g. `https://godeyes.ai/observe`) + `GRAFANA_TOKEN` (Editor/Admin service-account
token) and re-run `jev.py obs`. Still **no second Loki/Grafana** and no `.env`/secrets in the repo.
`python jev.py logs` still tails `jev-web` directly. Security-by-design needs nothing added — the
hardening in §7 (read_only, cap_drop ALL, no-new-privileges, single network, non-root, limits) IS the
security-by-design stack, identical in intent to cybergod's `colt-web`.


## 10. The app itself

Five tabs, mounted conditionally (only the active screen is in the DOM); scroll position is
remembered per tab. Navigation: tap the bottom bar, swipe horizontally on touch, arrow keys on
desktop. `?tab=N` deep-links a tab (used by the PWA manifest shortcuts).

| # | Tab | Content |
|---|---|---|
| 1 | Профиль | hero, base spec table, character cards |
| 2 | Работа | animated stats, companies, timeline, The Bell press block, links |
| 3 | Ценности | 10 value cards, worldview, sport/travel/coaching, family block |
| 4 | Запрос | what he's looking for, appearance stance, **the three hard stops**, green/red flags |
| 5 | Матч | the compatibility widget, geography, contacts |

**Content lives in `src/data.jsx`, not in components.** To change wording, edit that file. Some
entries contain JSX (that is why the file is `.jsx`, not `.js` — esbuild only parses JSX in `.jsx`).

### The match engine (`evaluate()` in `src/data.jsx`)

Eleven weighted criteria, **max 146 points** (rev 46 added `education` 16 + `reading` 8):

| criterion | weight | hard stop when |
|---|---|---|
| goal (what she's looking for) | 22 | `casual` |
| english | 20 | `none` |
| kids | 18 | `minor` |
| education | 16 | `none` (no higher ed; **min bachelor** — `college`/incomplete is a warn, not a stop) |
| work | 16 | `none` (principled refusal; a pause for parental leave / study / relocation is fine) |
| age | 14 | — |
| social media | 10 | — |
| mobility | 10 | — |
| reading / self-development | 8 | — |
| height | 8 | — |
| smoking | 4 | — |

There are now **four** hard stops (goal, english, kids, education) — the UI text on Screen 4/5 says
"четыре пункта", keep them in sync if the set changes. Any hard stop caps the score at **28 %** and
swaps in a specific, honest verdict. Without a hard stop the score is the plain weighted percentage.
Reference results after rev 46 (regression check): ideal (`master`+`lots`) `100`, office+good-English
`~97`, basic English `~88`, any hard stop `28`. `MATCH_INIT` defaults `edu:'master'`, `read:'lots'`.

### PWA

`vite-plugin-pwa` in `generateSW` mode, `registerType: 'autoUpdate'`. Precaches the whole bundle
(14 entries, ~375 KiB); Google Fonts get StaleWhileRevalidate (CSS) and CacheFirst (font files).
iOS installability comes from the `apple-mobile-web-app-*` meta tags in `index.html`, Android from
the generated manifest. Safe-area insets, 44 px+ tap targets and `prefers-reduced-motion` are all
handled in `styles.css`.

Build output for reference: `~172 kB` JS (`~59 kB` gzip), `~13 kB` CSS.


## 11. Landmines — every one of these has already bitten

1. **`sed -i` on the shared Caddyfile.** Breaks the bind-mount inode; the edge container keeps
   serving the old config. Always rewrite in place.
2. **`--remove-orphans`.** Would delete the neighbours' containers. Never.
3. **A second network on `jev-web`.** Docker DNS then hands Caddy an unreachable IP → intermittent
   502s that look random.
4. **`respond` without `handle` in `srv.Caddyfile`.** In Caddy's directive order `try_files` runs
   **before** `respond`, so `/__whoami` was being rewritten to `/index.html` and the deploy probe
   returned HTML instead of the marker. Both are wrapped in mutually-exclusive `handle` blocks now.
5. **`*` in the middle of a `path` matcher.** Caddy supports the wildcard only at the start or end.
   `/workbox-*.js` silently matched nothing; it is `/workbox-*` now.
6. **SPA fallback over `/assets/*`.** A missing hashed chunk returning `index.html` gives the browser
   HTML with MIME `text/html` where JS was expected, and an unreadable parse error. `/assets/*` has
   its own `handle` with no fallback, so it 404s properly.
7. **Caching `sw.js`.** An installed PWA then never learns about updates. `sw.js`, `registerSW.js`,
   the manifest and `index.html` are all `no-cache`; only content-hashed files are `immutable`.
8. **The Squarespace HTTPS/SVCB record.** See §2. It outranks your A record in Chrome.
9. **The `caddy:2-alpine` binary carries a file capability.** The stock image runs
   `setcap cap_net_bind_service=+ep /usr/bin/caddy` so Caddy can bind :80/:443 as non-root. We run the
   container with `no-new-privileges: true` **and** as a non-root user — and the kernel refuses to
   `execve()` a file that carries capabilities under `no_new_privs`, failing with EPERM. The symptom is
   `jev-web` crash-looping (`Restarting (255)`) with `exec /usr/bin/caddy: operation not permitted` in
   the logs, while `/__whoami` returns empty both inside the container and across `appnet`. We bind
   :8080 (a high port), so the capability is dead weight. `Dockerfile.web` strips it by copying the
   binary — `RUN cp /usr/bin/caddy /usr/local/bin/caddy` — because a plain `cp` (no `-a` /
   `--preserve=xattr`) does not copy the `security.capability` xattr, so the copy execs cleanly; `CMD`
   runs `/usr/local/bin/caddy`. Do **not** "simplify" this away and do **not** drop `no-new-privileges`
   to work around it — the copy keeps every bit of the hardening in §7.
10. **ORDER OF GO-LIVE: DNS first, THEN wire the edge Caddy — or you burn the Let's Encrypt limit.**
   If the `jev.best` vhost is added to the edge Caddy while DNS still points at Squarespace, Let's
   Encrypt's TLS-ALPN challenge reaches Squarespace, not the droplet, and **fails**. Let's Encrypt
   allows only **5 failed authorizations per hostname per account per hour** (refills 1 every 12 min);
   the early failures exhaust it and Caddy then backs off (per its docs, exponential back-off up to
   **1 day** between attempts). The public symptom is `ERR_SSL_PROTOCOL_ERROR` on mobile /
   `TLSV1_ALERT_INTERNAL_ERROR` from a pinned probe = "Caddy is configured for jev.best but has no
   cert". The edge Caddy logs to a **file, not `docker logs`**, and its cert store is
   `/data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/<domain>/` — a missing `jev.best/`
   folder there means issuance never succeeded. Two truths that make this sticky: **(a) `caddy reload`
   with an UNCHANGED config is a no-op and does NOT restart issuance** (you'll see `"config is
   unchanged"` in the log); **(b)** the container correctly named `jev-web`/project `jevbest` is fine —
   the cert lives on the shared `videodead-caddy-1` edge, which is not ours to rename or restart.
   FIX = `python jev.py cert`: it ships `deploy/fix_caddy.py --reissue`, which **removes the vhost →
   reload → re-adds it → reload**, recreating the cert-management job so Caddy makes an **immediate
   fresh attempt** — zero-downtime for the neighbours (never `docker restart` the shared edge; that
   blips TLS for cybergod/jobhuntwow/videodead). If the fresh attempt still fails with
   `rateLimited` / `too many failed authorizations`, the limit is still cooling down: **wait ~1 hour,
   then `python jev.py cert` again** (with correct DNS the challenge then succeeds). PREVENTION: do the
   Squarespace A-record cutover (§2) and confirm it globally (`python jev.py dns` shows `[OK]`)
   **before** the first `python jev.py deploy`, so the very first challenge lands on the droplet.

## 12. Failure modes

| symptom | look here |
|---|---|
| domain still shows Squarespace | leftover HTTPS/SVCB record, or DNS cache — `python jev.py dns` |
| `jev-web` Restarting (255), `/__whoami` empty inside + on appnet | `exec ... operation not permitted` — the caddy file-capability vs `no-new-privileges` (§11.9); `Dockerfile.web` strips it |
| `ERR_SSL_PROTOCOL_ERROR` / `TLSV1_ALERT_INTERNAL_ERROR`, no cert for jev.best | edge Caddy never issued — challenge ran before DNS cutover, LE back-off (§11.10). Fix: `python jev.py cert` (forces re-provision); if `rateLimited`, wait ~1h and re-run |
| 502 from the edge | `python jev.py diagnose` — is `jev-web` listed on `videodead_appnet`? |
| deploy probe returns HTML, not `jev-best` | `handle` block in `srv.Caddyfile` got flattened (§11.4) |
| installed PWA stuck on an old version | `Cache-Control` on `/sw.js` — must be `no-cache` |
| edge Caddy didn't reload | `fix_caddy.py` rolls back on invalid config; read step 5/5 of the deploy output |
| need to undo a Caddyfile edit | `Caddyfile.bak.<timestamp>` sits next to the original on the droplet |
| build fails on the droplet | it builds there, not here — read the tail printed by step 3/5 |


## 13. First-five-minutes checklist for a new session

1. Read this file. There is no `CLAUDE.md` for this project; the hard rules are §11.
2. Working dir is `jev-best/`. Orchestrator is `python jev.py <verb>`.
3. To change the site: edit `src/data.jsx` (content) or `src/styles.css` (looks), then
   `python jev.py deploy`. Never hand-SSH a fix.
4. To change routing/caching: `srv.Caddyfile` (in-container) or `deploy/caddy/jev.best.caddy` (edge).
   Validate locally with `caddy validate --config srv.Caddyfile --adapter caddyfile` before deploying.
5. There are **no secrets** here. If a task seems to need one, you are in the wrong project (§0).
6. Droplet is `root@64.225.108.200`, app at `/opt/jevbest`, container `jev-web`, project `jevbest`.
7. Verify before claiming: `python jev.py status` must print `jev-best`, not just a 200.


## 14. Standing rules

- **ONE orchestrator.** The operator ever runs only `python jev.py <verb>`. Every other piece of
  orchestration Python is **folded into jev.py** (e.g. the old `deploy/fix_caddy.py` is now the
  embedded `FIXCADDY_PY`, run on the droplet via `ssh python3 -`). Never add a standalone
  orchestration script — add a verb. The **only** other `.py` in the repo is `webapp/main.py`, which
  is the **product backend app** (`jev-api` / the «Кассандра» AI chat), not an orchestrator.
- **Reuse the key already on the droplet — no new key anywhere.** `python jev.py api` pulls
  `OPENAI_API_KEY`/`OPENAI_BASE_URL` from a running **colt** container's env ON THE DROPLET and writes
  `/opt/jevbest/.env` (umask 077); the value never leaves the droplet, never prints, never enters the
  repo (`.gitignore` + tar drop `*.env`). Same source for the bot token/alert routing later.
- **Architecture is now full-stack** (rev 46+): static React (`jev-web`) **plus** a FastAPI backend
  (`jev-api`, DO Inference LLM, same client/keys as cybergod `enrich.py`). §0/§9's "static, no backend,
  no secrets" describes the ORIGINAL site; the backend + reused-key are the deliberate exception above.
- If a value in this file changes, update this file **in the same commit**.
- One command per operation. Never hand a human a list of shell lines to paste.
- Verify with the machine, not with confidence. A response body, not a status code.
- Do not cross the streams: code/conventions don't move between repos. The one sanctioned reuse is the
  droplet's existing LLM key (read at runtime, never copied into the repo). The droplet is shared.
