#!/usr/bin/env bash
# Docker compose で 4 サービス (mongo + redis + backend + frontend) を一括起動。
# Ctrl-C 1 回でグレースフルシャットダウン (バックエンドは最大 30 秒、in-flight LLM タスクの
# partial save が走り終わるのを待つ)。2 回目で強制停止。

cd "$(dirname "$0")"

YELLOW=$'\033[33m'
RED=$'\033[31m'
GREEN=$'\033[32m'
RESET=$'\033[0m'

if ! command -v docker >/dev/null 2>&1; then
  echo "${RED}Error: docker not found. Install Docker Desktop or equivalent.${RESET}" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "${RED}Error: 'docker compose' subcommand not available.${RESET}" >&2
  exit 1
fi

# LM Studio がホスト側で動いているかの軽い注意喚起
if ! curl -sS -o /dev/null -m 1 http://localhost:1234/v1/models 2>/dev/null; then
  echo ""
  echo "${YELLOW}⚠  LM Studio が localhost:1234 で応答していません。${RESET}"
  echo "${YELLOW}   別ターミナルで 'lms server start' を実行してから続けることを推奨します。${RESET}"
  echo ""
fi

# ── Graceful shutdown ハンドラ ──
# Ctrl-C 1 回目: 'docker compose up' に SIGINT が同時に渡るのでコンテナへ SIGTERM を送り、
#                stop_grace_period (backend=30s) の範囲で各サービスが終了するのを待つ。
# Ctrl-C 2 回目: trap が再度走らないよう exit する。docker compose は force-stop を始める。
shutdown_count=0
graceful_shutdown() {
  shutdown_count=$((shutdown_count + 1))
  if [ "$shutdown_count" -eq 1 ]; then
    echo ""
    echo "${YELLOW}Stopping containers gracefully…${RESET}"
    echo "${YELLOW}  - backend は実行中の LLM タスクの partial save に最大 25 秒かかる場合があります${RESET}"
    echo "${YELLOW}  - もう一度 Ctrl-C で強制停止 (in-flight 状態は次回起動時に 'failed' 扱い)${RESET}"
    # docker compose up は SIGINT を自分でも受け取り containers を stop し始めるので、
    # こちらは通知だけして、docker compose 終了を待つ。
  else
    echo ""
    echo "${RED}Force stop. Containers will be killed.${RESET}"
    # 2 回目の Ctrl-C: docker compose に強制終了を依頼
    docker compose kill 2>/dev/null || true
    exit 130
  fi
}
trap graceful_shutdown INT TERM

echo "Starting tanka-chat stack..."
echo "  Frontend: http://localhost:5178"
echo "  Backend:  http://localhost:8001  (health: /api/health)"
echo "  MongoDB:  localhost:27017"
echo "  Redis:    localhost:6379"
echo ""
echo "${GREEN}Press Ctrl-C once to stop gracefully (up to ~30s). Twice to force stop.${RESET}"
echo ""

# exec せずに前景で走らせる → trap が効く。
docker compose up --build "$@"
exit_code=$?

if [ "$shutdown_count" -gt 0 ]; then
  echo ""
  echo "${GREEN}Stopped cleanly.${RESET}"
fi

exit "$exit_code"
