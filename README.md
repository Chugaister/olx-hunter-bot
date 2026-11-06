# OLX Hunter Bot

A personal Telegram bot that hunts for good second-hand deals on **olx.ua**, using the Claude Agent SDK.

- Tell the bot what you're looking for ("noise-cancelling headphones up to 4000 UAH"). It creates a **hunt**
  and checks OLX right away. The agent searches OLX, reads the promising listings, compares them with new
  prices found via web search and scores them. Listings scored 6/10 or higher are sent to you.
- **Automatic re-checks are off by default.** Turn them on per hunt ("keep watching this", or `/auto N on`)
  and the hunt is re-checked every 3 hours, except during quiet hours (01:00–07:00 Kyiv time). Checks that
  come due at night run at 07:00. Manual checks (`/run N`) work any time.
- You can have several hunts at once. Close one when you no longer need it.
- You can also just chat: "is 18k a good price for a MacBook Air M1?"

## Architecture

```
Telegram ──webhook──► FastAPI ──► aiogram handlers ──► Chat agent (ClaudeSDKClient, resumed per chat)
                         │                                 tools: olx MCP, WebSearch/WebFetch, hunt tools
                         └─ scheduler loop (60s tick) ──► HuntRunner ──► Hunt agent (structured JSON)
                                                              │             tools: olx MCP, WebSearch/WebFetch
                                                              └─ dedup (seen_listings) ──► Telegram
SQLite: chats, hunts, seen_listings, hunt_runs
```

| Path | What |
|---|---|
| `app.py` | FastAPI app, lifespan (DB, webhook, scheduler), webhook endpoint |
| `bot/` | Telegram handlers, message formatting, notifier |
| `agent/chat.py` | Conversational agent, one resumable Claude session per chat |
| `agent/hunter.py` | One-shot hunt agent, returns scored listings as JSON |
| `agent/hunt_tools.py` | In-process MCP tools for the chat agent: create/list/update/close/run hunts |
| `services/runner.py` | Runs a hunt: agent → dedup → persist → notify; concurrency limits |
| `services/scheduler.py` | Periodic lookups based on `next_run_at` |

The agents get only `WebSearch`/`WebFetch` plus the MCP tools: no shell or filesystem access, and no
`~/.claude` settings. They run in `data/agent-workdir`.

## Containers

```
Telegram ─HTTPS─► nginx (host, certbot) ─► 127.0.0.1:$BOT_PORT ─► bot ─► olx-mcp (compose network only)
```

- **bot** (`Dockerfile`): Python 3.14 + uv. The Agent SDK bundles Claude Code as a native binary,
  so no Node. Runs as uid 1000.
- **olx-mcp** (`docker/olx-mcp.Dockerfile`): the official Playwright image (Chromium included),
  serving MCP over Streamable HTTP. Its source is a **pinned commit** of the `deploy` branch of
  [Chugaister/olx-mcp](https://github.com/Chugaister/olx-mcp) (HTTP transport + scraper fixes,
  pending upstream). To update, bump the hash in `docker-compose.yml`.
- `./data` is mounted at `/data`: SQLite DB, the agent's working dir, and Claude Code's home
  (`data/home/.claude`, where chat sessions live). Everything stateful is in this one directory.
- Secrets come from `.env` at container start (`env_file`); `.dockerignore` keeps `.env` and
  `data/` out of the images.

## Deploy on a VPS (host nginx + certbot)

Requirements: Docker with the Compose plugin, nginx and certbot on the host, about 2 GB free RAM
(Chromium and Claude Code each get a 1 GB cap), and a subdomain with an **A record** pointing to
the VPS.

```bash
# 1. code
git clone git@github.com:<you>/olx-hunter-bot.git && cd olx-hunter-bot

# 2. config: fill in the required values; WEBHOOK_BASE_URL=https://<your subdomain>,
#    BOT_PORT = a port free on this host (check: ss -ltn | grep :8000)
cp .env.example .env && chmod 600 .env && nano .env

# 3. data dir owned by the container user (uid 1000)
mkdir -p data && sudo chown -R 1000:1000 data

# 4. TLS certificate first (the site config refers to it), then the nginx site
#    (the config is for olx-hunter.chugaister.net and BOT_PORT=8000; adjust if yours differ)
sudo certbot certonly --nginx -d olx-hunter.chugaister.net
sudo cp deploy/nginx/olx-hunter-bot.conf /etc/nginx/sites-available/olx-hunter-bot
sudo ln -s /etc/nginx/sites-available/olx-hunter-bot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 5. start (the first build takes a few minutes); the bot registers the webhook on startup
docker compose up -d --build
docker compose logs -f bot        # "webhook set to https://<subdomain>/tg/webhook", "scheduler started"
```

Check from outside: `curl -i https://<subdomain>/tg/webhook` returns `403` (GET is blocked by
nginx), and any other path returns `404`. Then send `/start` to the bot.

Containers restart automatically (`restart: unless-stopped`), including after a reboot, as long
as the Docker service is enabled (`sudo systemctl enable docker`). Logs are rotated (3 × 10 MB per
container).

**Update:**
```bash
git pull && docker compose up -d --build
```

**Backups:** `deploy/backup.sh` makes a consistent copy of the database while the bot runs,
into `data/backups/`, and deletes copies older than 14 days. Daily via cron:
```
30 4 * * * cd /path/to/olx-hunter-bot && deploy/backup.sh >> data/backups/backup.log 2>&1
```
For a full restore you also want `data/home` (chat sessions); copying the whole `data/`
directory while the bot is stopped covers everything.

## Local development

```bash
# in .env:
#   COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml   (publishes olx-mcp on 127.0.0.1:3000)
#   OLX_MCP_SOURCE=../olx-mcp                               (optional: build from your local checkout)
#   WEBHOOK_BASE_URL=<ngrok https URL>

ngrok http 8000

# either everything in containers ...
docker compose up -d --build

# ... or only olx-mcp in Docker and the bot on the host (OLX_MCP_URL=http://127.0.0.1:3000/mcp in .env)
docker compose up -d --build olx-mcp
uv sync && uv run olx-hunter-bot
```

Don't run the bot in both places at once: they'd share the port, the database and the
Telegram webhook. The free ngrok URL changes on every restart, so update `WEBHOOK_BASE_URL`
and restart the bot.

Without `OLX_MCP_URL`, the host-run bot falls back to spawning a local olx-mcp build
(`OLX_MCP_PATH`) over stdio for every agent session, which needs Node and Playwright's
Chromium on the host.

## Bot commands

| Command | |
|---|---|
| free text | talk to the agent (it creates, changes and closes hunts for you) |
| `/hunts` | active hunts |
| `/run N` | run hunt N now |
| `/auto N on\|off` | switch automatic re-checks for hunt N |
| `/close N` | close hunt N |
| `/new` | reset the conversation (hunts are kept) |

## Database

SQLite at `data/olx_hunter.db`, in WAL mode: `olx_hunter.db-wal` and `olx_hunter.db-shm` next to
it belong to the database. Copying only the `.db` file while the bot runs can miss recent writes;
back it up with:

```bash
sqlite3 data/olx_hunter.db ".backup data/backup-$(date +%F).db"
```

## Tests

```bash
uv run pytest
```
