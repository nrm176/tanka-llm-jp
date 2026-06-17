# 環境変数通過の詳細ガイド (repeated_word weight ablation)

## 問題背景

当プロジェクトは「weight環境変数でタスクごとに設定を切り替えて A/B 測定する」が原則。
ただし環境変数を docker-compose に「正確に通す」には見落としやすい落とし穴がある。

このドキュメントは `TANKA_W_REPEATED_WORD=12` を backend コンテナに確実に通す方法を解説。

---

## 前提: 現在のセットアップ

```bash
/Users/nrm176p/GitHub2/LLM/
├── docker-compose.yml      # ← backend サービス定義
├── app/
│   ├── backend/
│   │   ├── config.py       # (env 設定の参照はここで行わない)
│   │   ├── validator.py    # ← RULE_WEIGHTS で _env_int() を呼ぶ (ここが変数を読む)
│   │   ├── main.py         # FastAPI entry
│   │   └── ...
│   └── ...
└── start.sh                # docker-compose up の wrapper
```

### docker-compose.yml の backend サービス定義 (抜粋)

通常、こんな感じ:
```yaml
services:
  backend:
    build: ./app/backend
    ports:
      - "8001:8000"
    environment:
      # ← ここに「明示的に」env var を書いている場合と書いていない場合がある
    depends_on:
      - mongo
      - redis
```

現プロジェクトが `environment:` 節を持つか確認:

```bash
grep -A 5 "backend:" /Users/nrm176p/GitHub2/LLM/docker-compose.yml | head -20
```

---

## 環境変数通過の 3 つのメカニズム

### 方法 1: シェル環境変数で docker-compose を起動 ⭐ 推奨

```bash
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
```

**仕組み**:
- シェル (bash/zsh) が `TANKA_W_REPEATED_WORD=12` を環境変数として設定
- `docker compose` コマンドは親シェルの環境を **inherit** する
- Docker は `docker-compose.yml` を読む時に、内部で `${VAR_NAME}` 形式で親の env を参照可能

**条件**:
- `docker-compose.yml` が `environment:` 節で何らかの env 参照をしている
- **または** `environment:` 節がなく、docker-compose が親 env をデフォルト継承

**確認方法**:
```bash
TANKA_W_REPEATED_WORD=99 docker compose exec backend env | grep TANKA_W_REPEATED_WORD
# 出力: TANKA_W_REPEATED_WORD=99 なら成功
```

---

### 方法 2: docker-compose.yml に environment 節を追加 (要編集)

```yaml
services:
  backend:
    environment:
      TANKA_W_REPEATED_WORD: "${TANKA_W_REPEATED_WORD:-3}"  # デフォルト: 3
```

編集後、シェル変数経由で起動:
```bash
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
```

**仕組み**:
- `${VAR_NAME:-default}` = 親 env から VAR_NAME を読み、無ければ default を使う
- docker-compose が親シェルの環境を参照して YAML を **展開**

**メリット**:
- 一度編集すれば、以降は `TANKA_W_REPEATED_WORD=X` で簡単に切り替え可能
- 設定が yaml に明記されて追跡可能

**デメリット**:
- `docker-compose.yml` を編集する必要がある (リビジョン管理に乗る)
- 「テスト用の環境変数」が本番ファイルに混ざる可能性

**現状確認**:
```bash
grep -A 20 "backend:" /Users/nrm176p/GitHub2/LLM/docker-compose.yml | grep -i "environment"
```

無ければ、別の方法を探る。

---

### 方法 3: .env ファイルを docker-compose.yml と同じディレクトリに置く (限定的)

```bash
cd /Users/nrm176p/GitHub2/LLM
cat > .env << 'EOF'
TANKA_W_REPEATED_WORD=12
EOF
docker compose up -d backend
```

**仕組み**:
- docker-compose は起動時に `.env` ファイルを **自動で読み込む** (Docker Compose v1.28+)
- YAML の `${VAR}` 形式が `.env` を参照

**条件**:
- docker-compose.yml が `environment:` 節で `${TANKA_W_REPEATED_WORD:-3}` 等を参照している
- 現在のバージョン (Docker Compose v2) で `.env` は自動認識される

**メリット**:
- 複数の環境変数を `.env` でまとめて管理可能
- git 管理外に置けば (`.gitignore` に書けば) ファイル編集は不要

**デメリット**:
- `.env` の存在を知らないと、変数が「どこから来たのか」が謎になる
- docker-compose.yml が `environment:` 節を持たないと効かない

**推奨**: 現在のセットアップを確認してから判断。

---

## 現プロジェクトでの推奨手順

### Step 0: セットアップ確認

```bash
cd /Users/nrm176p/GitHub2/LLM
docker-compose --version  # v2.x.x であることを確認

# docker-compose.yml の backend 環境設定を確認
grep -A 30 "services:" docker-compose.yml | grep -A 15 "backend:"
```

### Step 1: 最も確実な方法 (環境変数継承)

**既存の docker-compose.yml を編集せずに**:

```bash
cd /Users/nrm176p/GitHub2/LLM

# 現在のコンテナを停止
docker compose down

# 環境変数を設定した状態で backend を起動
export TANKA_W_REPEATED_WORD=12
docker compose up -d backend

# 確認
sleep 5
curl http://localhost:8001/api/health
```

**コンテナ内で変数を確認**:
```bash
docker compose exec backend env | grep TANKA
# TANKA_W_REPEATED_WORD=12 が見えるか確認
```

これで validator.py の `_env_int()` が正しく `12` を読む。

### Step 2: 確実性を上げるなら — .env ファイルを作成

編集が嫌な場合:

```bash
cd /Users/nrm176p/GitHub2/LLM

# .env を作成 (git 管理外)
cat > .env << 'EOF'
TANKA_W_REPEATED_WORD=12
EOF

# (既に YAML で ${TANKA_W_REPEATED_WORD} が参照されていると仮定)
docker compose up -d backend

# 確認
docker compose exec backend python3 -c "import os; print(os.environ.get('TANKA_W_REPEATED_WORD'))"
```

eval 完了後、.env を削除か編集:
```bash
echo "TANKA_W_REPEATED_WORD=3" > .env
docker compose restart backend
```

---

## トラブルシューティング: 変数が通らない場合

### 症状 1: コンテナの env に TANKA_W_REPEATED_WORD が無い

```bash
docker compose exec backend env | grep TANKA_W_REPEATED_WORD
# (出力なし)
```

**原因**: 
- シェル環境変数が継承されていない (docker-compose.yml が参照していない)
- または docker-compose が古いバージョン

**対策**:

a) docker-compose.yml に環境設定が無いか確認:
```bash
grep -B 2 -A 10 "^  backend:" docker-compose.yml
```

`environment:` セクションが無ければ、追加:
```yaml
services:
  backend:
    build: ./app/backend
    ports:
      - "8001:8000"
    environment:                           # ← 追加
      TANKA_W_REPEATED_WORD: "${TANKA_W_REPEATED_WORD:-3}"
    depends_on:
      - mongo
      - redis
```

b) または .env で指定:
```bash
echo "TANKA_W_REPEATED_WORD=12" > /Users/nrm176p/GitHub2/LLM/.env
docker compose restart backend
```

### 症状 2: コンテナには env があるが、validator.py が読んでない

```bash
docker compose exec backend python3 -c "
import sys, os
sys.path.insert(0, '/app')
os.environ['TANKA_W_REPEATED_WORD'] = '12'  # 手動で設定
import validator
print('repeated_word weight:', validator.RULE_WEIGHTS.get('repeated_word'))
"
# 出力: 12 (正常) or 3 (読まれていない)
```

**原因**:
- validator.py がモジュール読み込み時に一度だけ `_env_int()` を実行
- その後の env 変更は反映されない (モジュールレベルの変数だから)

**確認**:
```bash
docker compose exec backend python3 << 'PY'
import os
os.environ['TANKA_W_REPEATED_WORD'] = '12'
import validator
print(validator.RULE_WEIGHTS['repeated_word'])
PY
# 出力: 3 (env 設定後の import は反映されないため)
```

**解決**:
- コンテナ起動前に env を設定して、python が validator を読むときに環境に既にあるようにする
- docker-compose restart backend

### 症状 3: eval.sh の validator ルール違反頻度が「repeated_word」で変わらない

eval.sh は HTTP 経由で backend と通信するため、backend のプロセス内で env を読む。

```bash
# eval.sh 実行中、別ターミナルで backend ログを確認
docker compose logs -f backend | grep -i "repeated_word" | head -10
```

ログに「weight: 12」などの記録があるか確認。

validator.py にデバッグ出力を追加:
```python
# validator.py 冒頭に
import sys
print(f"DEBUG: RULE_WEIGHTS['repeated_word'] = {RULE_WEIGHTS['repeated_word']}", file=sys.stderr)
```

その後、コンテナ再起動:
```bash
docker compose restart backend
docker compose logs backend | head -20
```

---

## 環境変数指定のチートシート

### 最小限の手順

```bash
cd /Users/nrm176p/GitHub2/LLM

# Baseline (デフォルト値 3)
docker compose down
docker compose up -d backend

# Variant (値 12)
docker compose down
TANKA_W_REPEATED_WORD=12 docker compose up -d backend

# 復帰
docker compose down
docker compose up -d backend
```

### eval 実行一体化スクリプト

以下を `repeated-word-ab-test.sh` として作成:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /Users/nrm176p/GitHub2/LLM

echo "=== Baseline (w=3) ==="
docker compose down
docker compose up -d backend
sleep 5
cd app/backend/eval
./eval.sh baseline-w3

echo ""
echo "=== Variant (w=12) ==="
cd /Users/nrm176p/GitHub2/LLM
docker compose down
TANKA_W_REPEATED_WORD=12 docker compose up -d backend
sleep 5
cd app/backend/eval
./eval.sh variant-w12

echo ""
echo "=== Comparison ==="
./compare.sh baseline-w3 variant-w12

echo ""
echo "=== Restore default ==="
cd /Users/nrm176p/GitHub2/LLM
docker compose down
docker compose up -d backend
sleep 5
echo "Done."
```

実行:
```bash
chmod +x repeated-word-ab-test.sh
./repeated-word-ab-test.sh
```

---

## 参考: 関連する環境変数一覧

validator.py では以下の env vars が読まれている:

```python
RULE_WEIGHTS: dict[str, int] = {
    "mora_count":            _env_int("TANKA_W_MORA_COUNT", 10),
    "kigo_present":          _env_int("TANKA_W_KIGO_PRESENT", 25),
    "season_matches_plan":   _env_int("TANKA_W_SEASON_MATCHES_PLAN", 30),
    "kigo_unique":           _env_int("TANKA_W_KIGO_UNIQUE", 15),
    "season_consistent":     _env_int("TANKA_W_SEASON_CONSISTENT", 20),
    "kigo_matches_plan":     _env_int("TANKA_W_KIGO_MATCHES_PLAN", 20),
    "no_other_kigo_cross":   _env_int("TANKA_W_NO_OTHER_KIGO_CROSS", 30),
    "no_other_kigo_same":    _env_int("TANKA_W_NO_OTHER_KIGO_SAME", 25),
    "kigo_in_dictionary":    _env_int("TANKA_W_KIGO_IN_DICTIONARY", 5),
    "repeated_word":         _env_int("TANKA_W_REPEATED_WORD", 3),  # ← 今回変更対象
    "mora_count_disputed":   _env_int("TANKA_W_MORA_DISPUTED", 3),
    "mora_count_off_by_one": _env_int("TANKA_W_MORA_OFF_BY_ONE", 3),
    "kireji_absent":         _env_int("TANKA_W_KIREJI_ABSENT", 3),
    "theme_time_mismatch":   _env_int("TANKA_W_THEME_TIME_MISMATCH", 25),
    "theme_time_uncovered":  _env_int("TANKA_W_THEME_TIME_UNCOVERED", 5),
    "theme_motif_uncovered": _env_int("TANKA_W_THEME_MOTIF_UNCOVERED", 5),
}

PASS_THRESHOLD = _env_int("TANKA_PASS_THRESHOLD", 80)
```

eval 時に複数の重みを同時に変更したい場合:
```bash
TANKA_W_REPEATED_WORD=12 TANKA_W_KIGO_UNIQUE=20 docker compose up -d backend
```

---

## まとめ

| 方法 | 手順 | 確実性 | ファイル編集 |
|---|---|---|---|
| シェル env 継承 | `TANKA_W_REPEATED_WORD=12 docker compose up -d` | ⭐⭐⭐ | 不要 |
| .env ファイル | `.env` に記述 → `docker compose up` | ⭐⭐ | .env のみ |
| YAML 編集 | `docker-compose.yml` に `environment:` 追加 | ⭐⭐⭐ | docker-compose.yml |

**推奨**: シェル env 継承。確実で、ファイル編集不要。
