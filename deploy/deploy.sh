#!/usr/bin/env bash
# Deploy GitHub `main` to the VPS. Run from your laptop, inside the repo:
#
#   deploy/deploy.sh                  # checks, tests, deploy
#   SKIP_TESTS=1 deploy/deploy.sh     # e.g. a docs-only change
#
# Settings (environment variables):
#   DEPLOY_HOST  ssh host or alias from ~/.ssh/config     (default: vps)
#   DEPLOY_DIR   repo path on the VPS                     (default: /opt/olx-hunter-bot)
#
# The VPS must already be set up once by hand (clone, .env, data/ ownership, nginx);
# see README "Deploy on a VPS". This script never touches .env, data/ or nginx, and
# only touches containers and images of the `olx-hunter` compose project.
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-vps}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/olx-hunter-bot}"

cd "$(dirname "$0")/.."
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. local checks: deploy exactly what is on GitHub main -------------------------
step "Checking the local repo"
branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "$branch" == main ]] || die "on branch '$branch'; switch to main"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] \
    || die "uncommitted changes; commit and push them first"
git fetch --quiet origin main
local_sha="$(git rev-parse main)"
remote_sha="$(git rev-parse origin/main)"
[[ "$local_sha" == "$remote_sha" ]] \
    || die "local main ($(git rev-parse --short main)) differs from origin/main ($(git rev-parse --short origin/main)); push or pull first"
echo "deploying $(git log -1 --format='%h %s' main)"

# --- 2. tests ------------------------------------------------------------------------
if [[ "${SKIP_TESTS:-}" == 1 ]]; then
    step "Skipping tests (SKIP_TESTS=1)"
else
    step "Running tests"
    uv run pytest -q
fi

# --- 3. on the VPS -------------------------------------------------------------------
step "Deploying to $DEPLOY_HOST:$DEPLOY_DIR"
ssh "$DEPLOY_HOST" bash -s -- "$DEPLOY_DIR" "$local_sha" <<'REMOTE'
set -euo pipefail
dir="$1"; expected="$2"
step() { printf '\n\033[1m==> [vps] %s\033[0m\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

cd "$dir"
[[ -f .env ]] || die "no .env in $dir; do the one-time setup first (README)"

step "Pulling"
git pull --ff-only --quiet origin main
[[ "$(git rev-parse HEAD)" == "$expected" ]] \
    || die "VPS is at $(git rev-parse --short HEAD), expected ${expected:0:7}"

step "Building and starting (only changed containers are recreated)"
docker compose up -d --build

step "Waiting for the bot to become healthy"
for _ in $(seq 1 45); do
    status="$(docker inspect -f '{{.State.Health.Status}}' olx-hunter-bot 2>/dev/null || echo missing)"
    [[ "$status" == healthy ]] && break
    sleep 2
done
if [[ "$status" != healthy ]]; then
    docker compose ps
    docker compose logs --tail 50 bot
    die "bot is '$status' after 90s"
fi
docker compose ps
docker compose logs --tail 15 bot

step "Removing this project's old images"
docker image prune -f --filter label=com.chugaister.project=olx-hunter
REMOTE

step "Done: $(git log -1 --format='%h %s' main) is live"
