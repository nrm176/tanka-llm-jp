// SSE クライアント + REST API ラッパー。
// バックエンドは `data: <json>\n\n` 形式のストリームを返す。EventSource は POST 不可なので
// fetch + ReadableStream で手動パースする。

const HARMONY_FINAL_MARKER = '<|channel|>final<|message|>'
const SPECIAL_TOKEN_RE = /<\|[^|]+\|>/g

export function splitHarmony(raw) {
  const idx = raw.indexOf(HARMONY_FINAL_MARKER)
  if (idx === -1) {
    return {
      thinking: raw.replace(SPECIAL_TOKEN_RE, '').trim(),
      answer: '',
      markerSeen: false,
    }
  }
  return {
    thinking: raw.slice(0, idx).replace(SPECIAL_TOKEN_RE, '').trim(),
    answer: raw.slice(idx + HARMONY_FINAL_MARKER.length).replace(SPECIAL_TOKEN_RE, '').trim(),
    markerSeen: true,
  }
}

async function* readSSE(response) {
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`HTTP ${response.status}: ${text || response.statusText}`)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n')
    buffer = events.pop() ?? ''
    for (const evt of events) {
      for (const line of evt.split('\n')) {
        if (!line.startsWith('data:')) continue
        const data = line.slice(5).trim()
        if (!data) continue
        try {
          yield JSON.parse(data)
        } catch (e) {
          console.warn('failed to parse SSE data:', data, e)
        }
      }
    }
  }
}

// ─── Task creation (returns {task_id, kind, session_id}) ───

export async function createChatTask({ sessionId, userMessage, mode }) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, user_message: userMessage, mode }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`createChatTask HTTP ${res.status}: ${text}`)
  }
  return res.json()
}

export async function createTankaTask({ sessionId, theme, maxRefines = 3, manualPlan = null }) {
  const body = { session_id: sessionId, theme, max_refines: maxRefines }
  if (manualPlan) body.manual_plan = manualPlan  // 手動構想モード: LLM Plan をスキップ
  const res = await fetch('/api/tanka', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`createTankaTask HTTP ${res.status}: ${text}`)
  }
  return res.json()
}

// 構想の下書き (#63): お題 (+季語 / 季節) から LLM に情景候補・心情・背景を出させるタスクを起動。
// 季語があれば季節は backend が辞書から確定するので送らない (不整合 422 を避ける)。
export async function createPlanDraft({ sessionId, theme, kigo = null, season = null, nCandidates = 3 }) {
  const body = { session_id: sessionId, theme, n_candidates: nCandidates }
  if (kigo) body.kigo = kigo
  else if (season) body.season = season
  const res = await fetch('/api/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`createPlanDraft HTTP ${res.status}: ${text}`)
  }
  return res.json()
}

// タスク文書 (#61): status と、終了していれば result (SSE を購読しないポーリング経路)
export async function getTask(taskId) {
  const res = await fetch(`/api/tasks/${taskId}`)
  if (!res.ok) throw new Error(`getTask HTTP ${res.status}`)
  return res.json()
}

// 手動構想モードの季語ドロップダウン用: 季節ラベル → 季語リスト
export async function getKigo() {
  const res = await fetch('/api/kigo')
  if (!res.ok) throw new Error(`getKigo HTTP ${res.status}`)
  return res.json()
}

// 手動構想モードのプリフィル用 (#43): 自由文から季語辞書スキャンで季節・季語を抽出 (LLM 不使用)
export async function extractPlan(text) {
  const res = await fetch('/api/plan/extract', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok) throw new Error(`extractPlan HTTP ${res.status}`)
  return res.json()
}

// ─── Task event stream (replay + live) ───

export async function* streamTask(taskId, signal) {
  const res = await fetch(`/api/tasks/${taskId}/stream`, { signal })
  yield* readSSE(res)
}

export async function cancelTask(taskId) {
  const res = await fetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' })
  if (!res.ok) throw new Error(`cancelTask HTTP ${res.status}`)
  return res.json()
}

export async function getActiveTask(sessionId) {
  const res = await fetch(`/api/sessions/${sessionId}/active-task`)
  if (!res.ok) return { task_id: null }
  return res.json()
}

// ─── REST: sessions ───

export async function listSessions() {
  const res = await fetch('/api/sessions')
  if (!res.ok) throw new Error(`listSessions HTTP ${res.status}`)
  return res.json()
}

export async function createSession(title = null, model = null) {
  // model を渡すとセッションにモデルを固定する (#20)。null なら既定 (グローバル現在値追従)
  const res = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, model }),
  })
  if (!res.ok) throw new Error(`createSession HTTP ${res.status}`)
  return res.json()
}

export async function getSession(id) {
  const res = await fetch(`/api/sessions/${id}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`getSession HTTP ${res.status}`)
  return res.json()
}

export async function renameSession(id, title) {
  const res = await fetch(`/api/sessions/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!res.ok) throw new Error(`renameSession HTTP ${res.status}`)
  return res.json()
}

export async function deleteSession(id) {
  const res = await fetch(`/api/sessions/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`deleteSession HTTP ${res.status}`)
  return res.json()
}

// ─── Eval Monitor ───

export async function listEvalRuns() {
  const res = await fetch('/api/eval/runs')
  if (!res.ok) throw new Error(`listEvalRuns HTTP ${res.status}`)
  return res.json()
}

// ─── Tanka Gallery (全セッション横断の短歌一覧) ───

export async function listTankaRecords(limit = 500) {
  const res = await fetch(`/api/tanka/records?limit=${limit}`)
  if (!res.ok) throw new Error(`listTankaRecords HTTP ${res.status}`)
  return res.json()
}

// ─── Failures (長期失敗記憶) ───

export async function listFailures(limit = 50) {
  const res = await fetch(`/api/failures?limit=${limit}`)
  if (!res.ok) throw new Error(`listFailures HTTP ${res.status}`)
  return res.json()
}

export async function clearFailures() {
  const res = await fetch('/api/failures', { method: 'DELETE' })
  if (!res.ok) throw new Error(`clearFailures HTTP ${res.status}`)
  return res.json()
}

// ─── Model selection (#15) ───

export async function listModels() {
  const res = await fetch('/api/models')
  if (!res.ok) throw new Error(`listModels HTTP ${res.status}`)
  return res.json()
}

export async function switchModel(model) {
  const res = await fetch('/api/model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `switchModel HTTP ${res.status}`)
  }
  return res.json()
}

// ─── Health ───

export async function checkHealth() {
  try {
    const res = await fetch('/api/health')
    if (!res.ok) return { status: 'error', error: `HTTP ${res.status}` }
    return await res.json()
  } catch (e) {
    return { status: 'error', error: e.message }
  }
}

// ─── DB → UI 用メッセージ正規化 ───

// 永続化されたメッセージを React コンポーネントが期待する rich shape へ変換。
export function normalizeMessage(msg) {
  if (msg.kind === 'tanka') {
    return {
      kind: 'tanka',
      theme: msg.theme,
      // 永続化された生成過程 (phase 毎の thinking 込み raw) を復元。
      // raw を PhaseBlock が splitHarmony してライブ時と同じ表示になる。
      // 古いメッセージ (phases 未保存) は [] のまま。
      phases: (msg.phases || []).map((p) => ({
        phase: p.phase,
        attempt: p.attempt,
        raw: p.raw || '',
        complete: true,
      })),
      validations: msg.validations || [],
      maxRefinesReached: msg.max_refines_reached || false,
      plateauReached: msg.plateau_reached || false,
      bestScore: typeof msg.best_score === 'number' ? msg.best_score : undefined,
      ragExamples: msg.rag_examples || [],
      // compose に注入された長期失敗記憶の教訓。古いメッセージは未保存 → []
      lessons: msg.lessons || [],
      complete: msg.tanka
        ? {
            tanka: msg.tanka,
            plan: msg.plan,
            moras: msg.moras || [],
            kigo: msg.kigo,
            season: msg.season,
            image: msg.image,
            emotion: msg.emotion,
            score: typeof msg.final_score === 'number' ? msg.final_score : undefined,
            // 生成所要秒 (#27)。旧データは未保存 → undefined (非表示)
            durationSeconds: typeof msg.duration_seconds === 'number' ? msg.duration_seconds : undefined,
          }
        : null,
      streaming: false,
    }
  }
  if (msg.kind === 'assistant') {
    return {
      kind: 'assistant',
      thinking: msg.thinking || null,
      answer: msg.content || '',
      streaming: false,
    }
  }
  return { kind: 'user', content: msg.content || '' }
}
