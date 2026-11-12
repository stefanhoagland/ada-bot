# Ada

Ada is a personal AI assistant you chat with on Telegram. She runs as a Docker container on an Unraid server and uses Claude as her brain.

She can:
- **Check and manage the homelab**: containers, disks, logs, system health.
- **Write and edit code** in her workspace folder.
- **Answer questions** and help think things through.

**She asks before changing anything.** Read-only commands (like `docker ps` or `df -h`) run right away. Anything else, such as restarting a container or writing a file, shows up in Telegram with ✅ Approve and ❌ Deny buttons and waits for you. If you don't answer within 10 minutes, the action is skipped.

## What you need

1. **A Claude API key.** Create one at [console.anthropic.com](https://console.anthropic.com) under API Keys, and add a little credit under Billing.
2. **A Telegram bot token.** In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts (name it Ada). It gives you a token like `123456:ABC...`.
3. **Your Telegram user ID.** Message **@userinfobot** and it replies with your numeric ID. Ada only answers this ID, so nobody else can control your server.

## Install on Unraid

The Docker image is built automatically by GitHub Actions and published to `ghcr.io/stefanhoagland/ada-bot:latest` whenever `main` changes.

1. The first time, make the image pullable: on GitHub go to your profile → **Packages** → **ada-bot** → **Package settings** and set visibility to **Public**. (Or keep it private and log Unraid in to ghcr.io.)
2. On the Unraid server (Terminal button in the top bar), save the template:
   ```bash
   mkdir -p /boot/config/plugins/dockerMan/templates-user
   curl -o /boot/config/plugins/dockerMan/templates-user/my-ada.xml \
     https://raw.githubusercontent.com/stefanhoagland/ada-bot/main/unraid/ada.xml
   ```
   (If the repo is private, copy `unraid/ada.xml` there by hand instead.)
3. In the Unraid web UI go to **Docker** → **Add Container**, pick **ada** from the Template list, fill in the three required fields, and click **Apply**.
4. Open Telegram, find your bot, and send `/start`.

### What the folders do

| In the container | On Unraid | Why |
|---|---|---|
| `/var/run/docker.sock` | `/var/run/docker.sock` | Lets Ada list, inspect, and (with approval) restart your containers |
| `/workspace` | `/mnt/user/appdata/ada/workspace` | Ada's own folder for code projects |
| `/mnt/user/appdata` | `/mnt/user/appdata` | Lets Ada read your apps' config files and edit them with approval |

Ada can only see the folders you map. Remove a mapping to take that access away.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Claude API key |
| `TELEGRAM_BOT_TOKEN` | (required) | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | (required) | Comma-separated Telegram user IDs allowed to use Ada |
| `TZ` | `America/New_York` | Time zone |
| `ADA_MODEL` | `claude-opus-5-5` | Claude model |
| `ADA_EFFORT` | `medium` | `low`, `medium`, `high`: how hard Ada thinks |
| `ADA_SAFE_COMMANDS` | built-in list | Comma-separated command prefixes that run without approval |
| `ADA_APPROVAL_TIMEOUT` | `600` | Seconds to wait for Approve/Deny |
| `ADA_COMMAND_TIMEOUT` | `300` | Seconds before a command is stopped |

## Telegram commands

- `/start` or `/help`: say hello
- `/new`: start a fresh conversation

## Safety notes

- Access to the Docker socket is powerful: anything that can use it can control every container on the server. That's why every change needs your tap, and why Ada only answers your user ID.
- Commands that chain or redirect (`;`, `&&`, `|`, `>`, `$( )`) always need approval, even if they start with a safe command.
- Conversations are kept in memory and reset when the container restarts.

## Run locally (for development)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
cp .env.example .env   # fill it in
set -a; . ./.env; set +a
python -m ada
```

## Roadmap

1. ✅ Core: Claude, shell and file tools, approval gate, Telegram, Docker
2. Homelab skills: health checks, Unraid array status, update checks
3. Reminders from your Outlook calendar
