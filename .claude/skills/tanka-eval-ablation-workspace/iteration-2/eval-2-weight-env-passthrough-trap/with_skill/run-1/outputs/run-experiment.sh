#!/bin/bash
# 実験スクリプト: repeated_word 重み A/B (3 vs 12)
# 使い方: bash run-experiment.sh
# または: bash run-experiment.sh | tee experiment.log

set -e

PROJECT_DIR="/Users/nrm176p/GitHub2/LLM"
BACKEND_DIR="$PROJECT_DIR/app/backend"
EVAL_DIR="$BACKEND_DIR/eval"
APP_DIR="$PROJECT_DIR/app"

HEALTH_URL="http://localhost:8001/api/health"
FAILURES_URL="http://localhost:8001/api/failures"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $@"
}

check_health() {
    log "ヘルスチェック..."
    result=$(curl -s "$HEALTH_URL")
    status=$(echo "$result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', 'unknown'))" 2>/dev/null || echo "unknown")
    lm_ok=$(echo "$result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('lm_studio_ok', False))" 2>/dev/null || echo "false")

    if [ "$status" != "ok" ]; then
        log "エラー: backend が応答していません (status=$status)"
        exit 1
    fi
    if [ "$lm_ok" != "True" ]; then
        log "警告: LM Studio が接続していません (lm_studio_ok=$lm_ok)"
        log "LM Studio を起動してください: lms server start"
        exit 1
    fi
    log "✓ ヘルスチェック OK"
}

reset_failures() {
    log "DB の失敗記憶をリセット..."
    curl -s -X DELETE "$FAILURES_URL" | jq .
    sleep 2
    count=$(curl -s "$FAILURES_URL" | python3 -c "import sys, json; print(json.load(sys.stdin).get('count', -1))" 2>/dev/null || echo -1)
    if [ "$count" = "0" ]; then
        log "✓ failures リセット完了 (count=0)"
    else
        log "⚠ 警告: failures がクリアされていない (count=$count)"
    fi
}

setup_env() {
    local weight=$1
    local variant=$2

    log "環境変数をセット: TANKA_W_REPEATED_WORD=$weight"
    cd "$APP_DIR"

    if [ "$weight" = "3" ]; then
        docker compose up -d backend
    else
        TANKA_W_REPEATED_WORD=$weight docker compose up -d backend
    fi

    sleep 5
    log "確認: コンテナ内の環境変数"
    actual=$(docker compose exec backend printenv | grep TANKA_W_REPEATED_WORD | cut -d= -f2)
    if [ "$actual" = "$weight" ]; then
        log "✓ 環境変数が正しくセットされている: $actual"
    else
        log "エラー: 環境変数が異なります (期待: $weight, 実際: $actual)"
        exit 1
    fi
}

run_eval() {
    local variant=$1
    log "eval を実行: $variant"

    cd "$EVAL_DIR"
    timeout 3600 ./eval.sh "$variant" eval_themes.json || {
        code=$?
        if [ $code -eq 124 ]; then
            log "警告: eval がタイムアウト (1 時間) しました。バックグラウンドで継続中の可能性があります。"
            log "進捗確認: docker compose logs -f backend"
            log "または: ls -lrt results/ | tail -1"
        else
            log "エラー: eval が失敗しました (終了コード $code)"
            exit 1
        fi
    }
}

compare_results() {
    log "結果を比較..."
    cd "$EVAL_DIR"

    # 最新のファイルを取得
    baseline_file=$(ls -t results/w-repeated-3*.json 2>/dev/null | head -1)
    treatment_file=$(ls -t results/w-repeated-12*.json 2>/dev/null | head -1)

    if [ -z "$baseline_file" ] || [ -z "$treatment_file" ]; then
        log "エラー: 結果ファイルが見つかりません"
        log "baseline: $baseline_file"
        log "treatment: $treatment_file"
        exit 1
    fi

    log "baseline ファイル: $baseline_file"
    log "treatment ファイル: $treatment_file"

    ./compare.sh "$(basename "$baseline_file" .json)" "$(basename "$treatment_file" .json)"
}

main() {
    log "=================================================================================="
    log "実験開始: repeated_word ルール重み A/B (3 vs 12)"
    log "=================================================================================="

    # Step 0: 前提チェック
    check_health

    log ""
    log "【注意】"
    log "1. LM Studio が 20 分以上アイドルでない場合は、以下を実行してください:"
    log "   lms stop && lms server start"
    log "2. eval はバックグラウンドで 25～60 分かかります。"
    log "   進捗は docker compose logs -f backend で監視してください。"
    log ""
    read -p "準備OK? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "キャンセルしました"
        exit 1
    fi

    # Baseline: w=3
    log ""
    log "=================================================================================="
    log "Baseline 実行: TANKA_W_REPEATED_WORD=3"
    log "=================================================================================="
    setup_env 3 w-repeated-3
    reset_failures
    run_eval w-repeated-3

    # Treatment: w=12
    log ""
    log "=================================================================================="
    log "Treatment 実行: TANKA_W_REPEATED_WORD=12"
    log "=================================================================================="
    setup_env 12 w-repeated-12
    reset_failures
    run_eval w-repeated-12

    # 比較
    log ""
    log "=================================================================================="
    log "結果比較"
    log "=================================================================================="
    compare_results

    log ""
    log "=================================================================================="
    log "実験完了"
    log "=================================================================================="
    log "次のステップ:"
    log "1. 結果を確認: cd $EVAL_DIR && ./compare.sh w-repeated-3 w-repeated-12"
    log "2. 記録を追加: app/backend/eval/FINDINGS.md に結果を追記"
    log "3. PR を作成 (採用する場合)"
}

main
