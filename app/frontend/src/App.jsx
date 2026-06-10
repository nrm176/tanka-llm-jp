import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { marked } from 'marked'
import EvalMonitor from './EvalMonitor.jsx'
import TankaGallery from './TankaGallery.jsx'
import {
  cancelTask,
  checkHealth,
  clearFailures,
  createChatTask,
  createSession,
  createTankaTask,
  deleteSession,
  getActiveTask,
  getSession,
  listFailures,
  listModels,
  listSessions,
  normalizeMessage,
  splitHarmony,
  streamTask,
  switchModel,
} from './api.js'

marked.setOptions({ breaks: true, gfm: true })

const TANKA_PREFIX_RE = /^tanka\s*[:：]\s*/i
const LS_LAST_SESSION = 'tanka.lastSessionId'

// ───── 共通サブコンポーネント ─────

function ThinkingBlock({ text, thinkingActive, stayOpen }) {
  if (!text && !thinkingActive) return null
  return (
    <details className={'thinking' + (thinkingActive ? ' live' : '')} open={thinkingActive || stayOpen}>
      <summary>
        <span className="thinking-icon">💭</span>
        {thinkingActive ? '考えています' : '思考プロセスを見る'}
      </summary>
      <div className="thinking-body">{text || '...'}</div>
    </details>
  )
}

function MarkdownContent({ text }) {
  const html = useMemo(() => (text ? marked.parse(text) : ''), [text])
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />
}

function UserMessage({ content }) {
  return <div className="msg msg-user">{content}</div>
}

function AssistantMessage({ thinking, answer, streaming }) {
  return (
    <div className="msg msg-assistant">
      <ThinkingBlock text={thinking} thinkingActive={streaming && !answer} stayOpen={streaming} />
      {answer && <MarkdownContent text={answer} />}
      {streaming && answer && <span className="cursor" />}
    </div>
  )
}

function PhaseBlock({ phase, attempt, raw, complete, streaming }) {
  const { thinking, answer, markerSeen } = splitHarmony(raw)
  const headerLabel =
    phase === 'plan' ? '構想を立てる'
      : phase === 'compose' ? '作歌'
      : phase === 'self_critique' ? '自己点検'
      : phase === 'refine' ? `再詠 (試行 ${attempt})`
      : phase
  return (
    <details className={'phase' + (streaming && !complete ? ' active' : '')} open={!complete || streaming}>
      <summary>
        <span className="phase-label">{headerLabel}</span>
        {streaming && !complete && <span className="phase-spinner" />}
        {complete && <span className="phase-check">✓</span>}
      </summary>
      <div className="phase-body">
        {thinking && (
          <details
            className={'phase-thinking-block' + (streaming && !markerSeen ? ' live' : '')}
            open={streaming || !markerSeen}
          >
            <summary>
              <span className="thinking-icon">💭</span>
              {streaming && !markerSeen ? '考えています' : '思考プロセスを見る'}
            </summary>
            <pre>{thinking}</pre>
          </details>
        )}
        {markerSeen && (phase === 'plan'
          ? <pre className="phase-plan">{answer}</pre>
          : <pre className="phase-tanka">{answer}</pre>)}
      </div>
    </details>
  )
}

function ValidationBlock({ validation }) {
  const { attempt, errors = [], warnings = [], violations = [], resolved, score } = validation
  // 構造化された violations があれば優先表示。なければ errors/warnings からフォールバック。
  const showStructured = violations.length > 0
  return (
    <div className={'validation ' + (resolved ? 'ok' : 'fail')}>
      <div className="validation-header">
        <strong>検証 (試行 {attempt})</strong>
        <span className="validation-status">
          {typeof score === 'number' && (
            <span className="validation-score">{score}/100</span>
          )}
          {resolved ? '✓ 合格' : '✗ 違反あり'}
        </span>
      </div>
      {showStructured ? (
        <ul className="validation-list">
          {violations.map((v, i) => (
            <li key={i} className={`violation violation-${v.severity}`}>
              <span className="violation-weight">-{v.weight}</span>
              <span className="violation-rule">[{v.rule}]</span>
              <span className="violation-msg">{v.message}</span>
            </li>
          ))}
        </ul>
      ) : (
        <>
          {errors.length > 0 && (
            <ul className="validation-list">
              {errors.map((e, i) => <li key={i} className="validation-error">{e}</li>)}
            </ul>
          )}
          {warnings.length > 0 && (
            <ul className="validation-list">
              {warnings.map((w, i) => <li key={i} className="validation-warning">⚠ {w}</li>)}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

// バックエンドの validator.PASS_THRESHOLD (env TANKA_PASS_THRESHOLD, 既定 80) に対応。
// これ未満のスコアは「合格に達していない暫定案」を意味する。
const PASS_THRESHOLD = 80

function TankaCompleteBlock({ tanka, moras, plan, kigo, season, image, emotion, score }) {
  if (!tanka) return null
  const lines = String(tanka).split('\n')
  const unmet = typeof score === 'number' && score < PASS_THRESHOLD
  return (
    <div className={'tanka-complete' + (unmet ? ' unmet' : '')}>
      {unmet && (
        <div
          className="tanka-unmet-badge"
          title={`合格は ${PASS_THRESHOLD} 点以上です。これは規定を満たさない暫定案 (best-of-N の最高得点案) として表示しています。`}
        >
          ⚠ 規定未達（{score}/100・合格 {PASS_THRESHOLD}）
        </div>
      )}
      <div className="tanka-final">
        {lines.map((line, i) => <div key={i}>{line || ' '}</div>)}
      </div>
      <div className="tanka-meta">
        {Array.isArray(moras) && moras.length > 0 && (
          <span className="tanka-meta-item">拍数: {moras.join(' - ')}</span>
        )}
        {kigo && <span className="tanka-meta-item">季語: <b>{kigo}</b></span>}
        {season && <span className="tanka-meta-item">季節: {season}</span>}
        {typeof score === 'number' && (
          <span className={'tanka-meta-item tanka-score' + (score >= PASS_THRESHOLD ? ' pass' : ' fail')}>
            評点: {score}/100
          </span>
        )}
      </div>
      {(image || emotion) && (
        <details className="tanka-meta-detail">
          <summary>情景・心情</summary>
          {image && <div><b>情景:</b> {image}</div>}
          {emotion && <div><b>心情:</b> {emotion}</div>}
        </details>
      )}
      {plan && (
        <details className="tanka-plan">
          <summary>構想を見る</summary>
          <pre>{plan}</pre>
        </details>
      )}
    </div>
  )
}

function TankaMessage({ msg }) {
  return (
    <div className="msg msg-tanka">
      <div className="tanka-header">短歌生成: お題「{msg.theme}」</div>
      {msg.ragExamples && msg.ragExamples.length > 0 && (
        <details className="rag-block">
          <summary>📜 参考にした古典の名歌 ({msg.ragExamples.length})</summary>
          <div className="rag-list">
            {msg.ragExamples.map((p, i) => (
              <div key={i} className="rag-item">
                <div className="rag-poem">{String(p.text || '').split('\n').join(' / ')}</div>
                <div className="rag-attr">— {p.author}（{p.source}） 季語: {(p.kigo || []).join('・') || '—'}</div>
              </div>
            ))}
          </div>
        </details>
      )}
      {msg.phases?.map((p, i) => (
        <PhaseBlock
          key={`${p.phase}-${p.attempt ?? 0}-${i}`}
          phase={p.phase}
          attempt={p.attempt}
          raw={p.raw}
          complete={p.complete}
          streaming={msg.streaming && i === msg.phases.length - 1 && !p.complete}
        />
      ))}
      {msg.validations?.map((v, i) => (
        <ValidationBlock key={i} validation={v} />
      ))}
      {msg.plateauReached && (
        <div className="warn-banner">
          ⚠ これ以上 score が改善しないため打ち切りました
          {typeof msg.bestScore === 'number' && ` (最高 ${msg.bestScore}/100)`}。
          全 attempt 中で最も高得点だった案を最終結果として採用しています。
        </div>
      )}
      {msg.maxRefinesReached && !msg.plateauReached && (
        <div className="warn-banner">
          ⚠ 安全上限に達したため打ち切りました
          {typeof msg.bestScore === 'number' && ` (最高 ${msg.bestScore}/100)`}。
        </div>
      )}
      {msg.llmError && (
        <div className="warn-banner">
          ⚠ 生成中に LLM エラーが発生しました（コンテキスト超過の可能性）。
          {msg.llmErrorRecovered
            ? ' それまでで最も良かった案を最終結果として採用しています。'
            : ' 有効な短歌を生成できませんでした。'}
        </div>
      )}
      {msg.complete && (
        <TankaCompleteBlock
          tanka={msg.complete.tanka}
          moras={msg.complete.moras}
          plan={msg.complete.plan}
          kigo={msg.complete.kigo}
          season={msg.complete.season}
          image={msg.complete.image}
          emotion={msg.complete.emotion}
          score={msg.complete.score}
        />
      )}
      {msg.error && <div className="error-banner">エラー: {msg.error}</div>}
    </div>
  )
}

// ───── 失敗履歴パネル (Phase 6: 長期記憶) ─────

function FailuresPanel({ onClose }) {
  const [data, setData] = useState({ count: 0, items: [] })
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const d = await listFailures(100)
      setData(d)
    } catch (e) {
      console.warn('listFailures failed:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const handleClear = async () => {
    if (!confirm('長期失敗記憶をすべてクリアしますか？')) return
    await clearFailures()
    await reload()
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal failures-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>長期失敗記憶 ({data.count} 件)</h3>
          <div className="modal-actions">
            <button onClick={reload} disabled={loading}>更新</button>
            <button onClick={handleClear} className="danger" disabled={loading || data.count === 0}>
              すべてクリア
            </button>
            <button onClick={onClose}>閉じる</button>
          </div>
        </div>
        <div className="modal-body">
          {loading && <div className="loading">読み込み中…</div>}
          {!loading && data.items.length === 0 && (
            <div className="empty">失敗例の記録はまだありません。</div>
          )}
          {!loading && data.items.map((f) => (
            <div key={f.id} className="failure-item">
              <div className="failure-head">
                <span className="failure-theme">お題: 「{f.theme}」</span>
                <span className="failure-score">score: {f.score}</span>
                <span className="failure-ts">{new Date(f.ts).toLocaleString('ja-JP')}</span>
              </div>
              {f.parsed && (
                <div className="failure-parsed">
                  季語: <b>{f.parsed.kigo || '?'}</b> / 季: {f.parsed.season || '?'}
                </div>
              )}
              <ul className="failure-violations">
                {(f.violations || []).map((v, i) => (
                  <li key={i} className={`violation violation-${v.severity}`}>
                    <span className="violation-weight">-{v.weight}</span>
                    <span className="violation-rule">[{v.rule}]</span>
                    <span className="violation-msg">{v.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ───── モデル切替 (#15/#20) ─────
// 自己完結コンポーネント (AppInner の hook 宣言順に影響を与えない — §フック TDZ 罠の回避)。
// #20 以降これは「既定モデル」: 新規セッションの既定 + モデル未固定セッションのフォールバック。
// 切替は新規タスクから有効。実行中タスクは開始時のモデルで走り切る (backend 側で保証)。

function ModelSelector({ onSwitched }) {
  const [models, setModels] = useState(null)   // {current, default, available[]} | null
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const refresh = useCallback(async () => {
    try {
      setModels(await listModels())
      setErr(null)
    } catch (e) {
      setErr(e.message)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const onChange = async (e) => {
    const model = e.target.value
    if (!models || model === models.current) return
    setBusy(true)
    try {
      await switchModel(model)
      await refresh()
      onSwitched?.()  // ヘッダの「<model> 接続中」表示を追従させる
    } catch (e2) {
      setErr(e2.message)
    } finally {
      setBusy(false)
    }
  }

  if (err) {
    return (
      <div className="model-selector">
        <div className="model-selector-error" title={err}>モデル取得失敗</div>
        <button className="model-selector-retry" onClick={refresh}>再試行</button>
      </div>
    )
  }
  if (!models) return null

  return (
    <div className="model-selector">
      <label className="model-selector-label" htmlFor="model-select">既定モデル</label>
      <select
        id="model-select"
        value={models.current}
        onChange={onChange}
        disabled={busy}
        title="生成に使うモデル。切替は次の生成から有効"
      >
        {/* current が available に無いケース (LM Studio 側で消えた等) も表示は維持する */}
        {!models.available.includes(models.current) && (
          <option value={models.current}>{models.current} (未検出)</option>
        )}
        {models.available.map((m) => (
          <option key={m} value={m}>{m}{m === models.default ? ' (既定)' : ''}</option>
        ))}
      </select>
      <div className="model-selector-hint">
        新規セッションの既定。モデル固定セッションには影響しません。
        未ロードのモデルは初回生成時にロードが走り遅くなることがあります
      </div>
    </div>
  )
}

// ───── 新規セッション作成 (モデル選択付き #20) ─────
// クリックでインラインのモデル選択を開き、選んで作成する。
// 「既定」はモデル未固定 (グローバル現在値追従) のセッションを作る。

function NewSessionControl({ onCreate }) {
  const [open, setOpen] = useState(false)
  const [models, setModels] = useState(null)
  const [choice, setChoice] = useState('')   // '' = 既定 (未固定)
  const [busy, setBusy] = useState(false)

  const toggle = async () => {
    if (open) { setOpen(false); return }
    setOpen(true)
    if (!models) {
      try { setModels(await listModels()) } catch { setModels({ current: '', available: [] }) }
    }
  }

  const create = async () => {
    setBusy(true)
    try {
      await onCreate(choice || null)
      setOpen(false)
      setChoice('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="new-session-control">
      <button className="new-session" onClick={toggle} title="新規セッション">
        + 新しい会話
      </button>
      {open && (
        <div className="new-session-panel">
          <label className="model-selector-label" htmlFor="new-session-model">モデル</label>
          <select
            id="new-session-model"
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            disabled={busy || !models}
          >
            <option value="">
              {models ? `既定 (現在: ${models.current})` : '読込中…'}
            </option>
            {(models?.available || []).map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <div className="model-selector-hint">
            モデルを選ぶとこのセッションに固定されます。既定は全体設定に追従します
          </div>
          <div className="new-session-actions">
            <button className="new-session-create" onClick={create} disabled={busy}>作成</button>
            <button className="new-session-cancel" onClick={() => setOpen(false)} disabled={busy}>キャンセル</button>
          </div>
        </div>
      )}
    </div>
  )
}

// ───── Sidebar ─────

function Sidebar({ sessions, activeId, onSelect, onCreate, onDelete, onModelSwitched }) {
  return (
    <aside className="sidebar">
      <NewSessionControl onCreate={onCreate} />
      <div className="session-list">
        {sessions.length === 0 && (
          <div className="session-empty">セッションなし</div>
        )}
        {sessions.map((s) => {
          const running = !!s.active_task
          return (
            <div
              key={s.id}
              className={'session-item' + (s.id === activeId ? ' active' : '')}
              onClick={() => onSelect(s.id)}
              title={running ? '生成中' : ''}
            >
              {running && <span className="session-spinner" />}
              <div className="session-title">{s.title || '無題'}</div>
              {s.model && (
                <span className="session-model" title={`モデル固定: ${s.model}`}>
                  {String(s.model).split('/').pop()}
                </span>
              )}
              <button
                className="session-delete"
                title="削除"
                onClick={(e) => { e.stopPropagation(); onDelete(s.id) }}
              >×</button>
            </div>
          )
        })}
      </div>
      <ModelSelector onSwitched={onModelSwitched} />
    </aside>
  )
}

// ───── ErrorBoundary ─────
// 描画ツリーで例外が起きても真っ白にならないようにする最後の砦。

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }
  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info)
  }
  handleReset = () => this.setState({ hasError: false, error: null })
  handleReload = () => window.location.reload()
  render() {
    if (this.state.hasError) {
      const msg = String(this.state.error?.message || this.state.error || 'Unknown error')
      return (
        <div className="error-fallback">
          <h2>表示中にエラーが発生しました</h2>
          <pre>{msg}</pre>
          <div className="error-actions">
            <button onClick={this.handleReset}>続行</button>
            <button onClick={this.handleReload}>再読み込み</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

// ───── セッション ID チップ (LLM に伝えやすくするためのコピー可能 ID 表示) ─────

function SessionIdChip({ id }) {
  const [copied, setCopied] = useState(false)
  if (!id) return null
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(id)
    } catch {
      // clipboard 不可環境 (非 https 等) でも選択できるよう prompt フォールバック
      window.prompt('セッション ID (コピーしてください):', id)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }
  return (
    <button
      className="sid-chip"
      onClick={copy}
      title={`セッション ID: ${id}\nクリックで全体をコピー`}
    >
      <span className="sid-label">ID</span>
      <span className="sid-value">{id.slice(0, 8)}…</span>
      <span className="sid-copy">{copied ? '✓ コピー' : '📋'}</span>
    </button>
  )
}

// ───── メインアプリ ─────

function AppInner() {
  const [sessions, setSessions] = useState([])
  const [activeId, setActiveId] = useState(() => localStorage.getItem(LS_LAST_SESSION))
  const [messages, setMessages] = useState([])
  const [streaming, setStreaming] = useState(null)
  const [input, setInput] = useState('')
  const [tankaMode, setTankaMode] = useState(false)
  const [health, setHealth] = useState({ status: 'checking' })
  const [error, setError] = useState(null)
  const [showFailures, setShowFailures] = useState(false)
  const [view, setView] = useState('chat') // 'chat' | 'gallery' | 'eval'

  const abortRef = useRef(null)
  const scrollRef = useRef(null)
  const textareaRef = useRef(null)
  // 短歌一覧からのジャンプ先 ({sessionId, index} | null)。セッション読込後の
  // オートスクロールを「最下部」ではなく該当メッセージへ向ける 1 回限りの指示。
  const pendingScrollRef = useRef(null)

  // クロージャ越しに「最新の」activeId / streaming を読むための ref
  // ストリーム中にユーザーがセッション切替したことを検知して、
  // 古いストリームの mutation を捨てるのに使う。
  const activeIdRef = useRef(activeId)
  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  // 初期化を Strict Mode の二重実行から守るためのガード
  const initOnceRef = useRef(false)

  // ── ヘルス ──
  // モデル切替 (#15) 後にヘッダ表示を追従させるため、再取得を callback 化して
  // ModelSelector へ渡す (マウント時 1 回だけだと切替が反映されない)
  const refreshHealth = useCallback(() => {
    checkHealth().then(setHealth)
  }, [])
  useEffect(() => {
    refreshHealth()
  }, [refreshHealth])

  const refreshSessions = useCallback(async () => {
    try {
      const list = await listSessions()
      setSessions(list)
      return list
    } catch (e) {
      // 取得失敗時は既存の sessions をそのまま残す（誤って [] にしない）
      console.warn('listSessions failed:', e)
      setError(e.message)
      return null
    }
  }, [])

  // 配列の最後のメッセージだけ書き換えるユーティリティ。ストリーム中の updates で使う。
  const updateLastMessage = useCallback((updater) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev
      const last = prev[prev.length - 1]
      return [...prev.slice(0, -1), updater(last)]
    })
  }, [])

  // ── タスクストリーム購読 (chat / tanka 共通) ──
  // taskId と placeholder kind ('chat' | 'tanka') を受け取り、SSE を購読してメッセージを更新する。
  // セッション切替時は SSE のみ abort、バックエンドのタスクは生き続ける (再接続で続きが見える)。
  const consumeTaskStream = useCallback(async (taskId, kind, sessionAtStart) => {
    const sameSession = () => activeIdRef.current === sessionAtStart

    abortRef.current = new AbortController()
    setStreaming(kind)
    let raw = ''
    let cancelledByUser = false

    try {
      for await (const event of streamTask(taskId, abortRef.current.signal)) {
        if (!sameSession()) break

        if (event.type === 'task_meta') {
          continue
        }

        if (kind === 'chat') {
          if (event.type === 'chunk') {
            raw += event.text || ''
            const { thinking, answer } = splitHarmony(raw)
            updateLastMessage((m) => ({ ...m, raw, thinking, answer }))
          } else if (event.type === 'complete') {
            updateLastMessage((m) => ({
              ...m,
              thinking: event.thinking ?? splitHarmony(raw).thinking,
              answer: event.answer ?? splitHarmony(raw).answer,
            }))
          } else if (event.type === 'cancelled') {
            cancelledByUser = true
          } else if (event.type === 'error') {
            updateLastMessage((m) => ({
              ...m,
              answer: (m.answer || '') + ` …(エラー: ${event.message})`,
            }))
          }
        } else if (kind === 'tanka') {
          if (event.type === 'rag') {
            updateLastMessage((m) => ({ ...m, ragExamples: event.examples }))
          } else if (event.type === 'phase_start') {
            updateLastMessage((m) => ({
              ...m,
              phases: [...(m.phases || []), { phase: event.phase, attempt: event.attempt, raw: '', complete: false }],
            }))
          } else if (event.type === 'chunk') {
            updateLastMessage((m) => {
              const phases = [...(m.phases || [])]
              if (phases.length > 0) {
                const last = phases[phases.length - 1]
                phases[phases.length - 1] = { ...last, raw: (last.raw || '') + (event.text || '') }
              }
              return { ...m, phases }
            })
          } else if (event.type === 'phase_end') {
            updateLastMessage((m) => {
              const phases = [...(m.phases || [])]
              if (phases.length > 0) {
                phases[phases.length - 1] = { ...phases[phases.length - 1], complete: true, finalText: event.text }
              }
              return { ...m, phases }
            })
          } else if (event.type === 'validation') {
            updateLastMessage((m) => ({ ...m, validations: [...(m.validations || []), event] }))
          } else if (event.type === 'max_refines_reached') {
            updateLastMessage((m) => ({ ...m, maxRefinesReached: true, bestScore: event.best_score }))
          } else if (event.type === 'plateau_reached') {
            updateLastMessage((m) => ({ ...m, plateauReached: true, bestScore: event.best_score, scoreHistory: event.history }))
          } else if (event.type === 'llm_error') {
            updateLastMessage((m) => ({ ...m, llmError: event.message, llmErrorRecovered: event.recovered }))
          } else if (event.type === 'complete') {
            updateLastMessage((m) => ({
              ...m,
              complete: event.tanka
                ? {
                    tanka: event.tanka,
                    plan: event.plan,
                    moras: event.moras || [],
                    kigo: event.kigo,
                    season: event.season,
                    image: event.image,
                    emotion: event.emotion,
                    score: event.score,
                  }
                : null,
            }))
            if (sameSession()) setTankaMode(true)
          } else if (event.type === 'cancelled') {
            cancelledByUser = true
          } else if (event.type === 'error') {
            updateLastMessage((m) => ({ ...m, error: event.message }))
          }
        }

        if (event.type === 'done') break
      }
    } catch (e) {
      if (e.name !== 'AbortError' && sameSession()) {
        updateLastMessage((m) => ({ ...m, error: e.message || String(e) }))
      }
    } finally {
      if (sameSession()) {
        updateLastMessage((m) => ({ ...m, streaming: false, cancelled: cancelledByUser || undefined }))
      }
      setStreaming(null)
      abortRef.current = null
      refreshSessions()
    }
  }, [updateLastMessage, refreshSessions])

  const loadSession = useCallback(async (id) => {
    // SSE のみ切断 (バックエンドのタスクは生かしておく)。abort は新しい AbortController に流すだけ。
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setStreaming(null)

    if (!id) {
      setMessages([])
      setActiveId(null)
      activeIdRef.current = null
      setTankaMode(false)
      localStorage.removeItem(LS_LAST_SESSION)
      return
    }
    try {
      const sess = await getSession(id)
      if (!sess) {
        localStorage.removeItem(LS_LAST_SESSION)
        setActiveId(null)
        activeIdRef.current = null
        setMessages([])
        return
      }
      setActiveId(id)
      activeIdRef.current = id
      localStorage.setItem(LS_LAST_SESSION, id)
      const baseMessages = (sess.messages || []).map(normalizeMessage)
      setMessages(baseMessages)
      const last = (sess.messages || []).slice().reverse().find((m) => m.kind === 'assistant' || m.kind === 'tanka')
      setTankaMode(last?.kind === 'tanka')

      // アクティブなタスクがあれば再接続して live updates を表示
      const active = await getActiveTask(id)
      if (active.task_id) {
        // タスクの kind に応じた placeholder を末尾に挿入してから stream へ
        if (active.kind === 'tanka') {
          setMessages((prev) => [
            ...prev,
            {
              kind: 'tanka',
              theme: '...',
              phases: [],
              validations: [],
              complete: null,
              maxRefinesReached: false,
              streaming: true,
              error: null,
            },
          ])
        } else {
          setMessages((prev) => [
            ...prev,
            { kind: 'assistant', raw: '', thinking: null, answer: '', streaming: true },
          ])
        }
        // バックグラウンドで購読 (await しない: loadSession はすぐ返したい)
        consumeTaskStream(active.task_id, active.kind, id)
      }
    } catch (e) {
      console.warn('loadSession failed:', e)
      setError(e.message)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [consumeTaskStream])

  // 起動時の初期化を 1 回だけ実行
  useEffect(() => {
    if (initOnceRef.current) return
    initOnceRef.current = true
    ;(async () => {
      const list = await refreshSessions()
      if (list === null) return  // 取得失敗時はそのまま
      const stored = localStorage.getItem(LS_LAST_SESSION)
      if (stored && list.some((s) => s.id === stored)) {
        await loadSession(stored)
      } else if (list.length > 0) {
        await loadSession(list[0].id)
      } else {
        try {
          const created = await createSession()
          await refreshSessions()
          await loadSession(created.id)
        } catch (e) {
          setError(e.message)
        }
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── textarea & 自動スクロール ──
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 240) + 'px'
  }, [input])
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    // 短歌一覧からのジャンプ: 全メッセージ種が main 直下の .msg なので、
    // messages 配列の添字と ':scope > .msg' の位置が 1:1 対応する
    const pending = pendingScrollRef.current
    if (pending && pending.sessionId === activeIdRef.current) {
      pendingScrollRef.current = null
      const node = el.querySelectorAll(':scope > .msg')[pending.index]
      if (node) {
        node.scrollIntoView({ block: 'start' })
        node.classList.add('msg-jump-flash')
        setTimeout(() => node.classList.remove('msg-jump-flash'), 2000)
        return
      }
      // 見つからなければ従来どおり最下部へフォールバック
    }
    el.scrollTop = el.scrollHeight
  }, [messages])

  // 他セッションで task が走っている間だけサイドバーを軽く poll してインジケータを最新化
  const anyRunning = sessions.some((s) => s.active_task)
  useEffect(() => {
    if (!anyRunning) return
    const t = setInterval(() => { refreshSessions() }, 3000)
    return () => clearInterval(t)
  }, [anyRunning, refreshSessions])

  // ── 短歌パイプライン (タスク作成 → ストリーム購読) ──
  const runTanka = useCallback(async (theme) => {
    if (!activeId) return
    const sessionAtStart = activeId
    setMessages((prev) => [
      ...prev,
      { kind: 'user', content: `tanka:${theme}` },
      {
        kind: 'tanka',
        theme,
        phases: [],
        validations: [],
        complete: null,
        maxRefinesReached: false,
        streaming: true,
        error: null,
      },
    ])
    setError(null)
    try {
      const { task_id } = await createTankaTask({ sessionId: sessionAtStart, theme })
      await consumeTaskStream(task_id, 'tanka', sessionAtStart)
    } catch (e) {
      console.warn('runTanka failed:', e)
      setError(e.message)
      updateLastMessage((m) => ({ ...m, streaming: false, error: e.message }))
    }
  }, [activeId, consumeTaskStream, updateLastMessage])

  // ── 通常チャット ──
  const runChat = useCallback(async (text) => {
    if (!activeId) return
    const sessionAtStart = activeId
    setMessages((prev) => [
      ...prev,
      { kind: 'user', content: text },
      { kind: 'assistant', raw: '', thinking: null, answer: '', streaming: true },
    ])
    setError(null)
    try {
      const { task_id } = await createChatTask({
        sessionId: sessionAtStart,
        userMessage: text,
        mode: tankaMode ? 'tanka' : 'normal',
      })
      await consumeTaskStream(task_id, 'chat', sessionAtStart)
    } catch (e) {
      console.warn('runChat failed:', e)
      setError(e.message)
      updateLastMessage((m) => ({ ...m, streaming: false, answer: (m.answer || '') + ` …(エラー: ${e.message})` }))
    }
  }, [activeId, tankaMode, consumeTaskStream, updateLastMessage])

  // ── 送信 ──
  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming || !activeId) return
    setInput('')

    if (text.toLowerCase() === '/end-tanka' || text.toLowerCase() === '/normal') {
      setTankaMode(false)
      setMessages((prev) => [...prev, { kind: 'system', content: '通常モードに戻りました。' }])
      return
    }

    const tankaMatch = text.match(TANKA_PREFIX_RE)
    if (tankaMatch) {
      const theme = text.slice(tankaMatch[0].length).trim()
      if (!theme) { setError('お題が空です。例: tanka:夏の夕暮れ'); return }
      await runTanka(theme)
    } else {
      await runChat(text)
    }
  }, [input, streaming, activeId, runTanka, runChat])

  // 停止ボタン: バックエンドのタスクをキャンセル (SSE は cancelled イベントを受けて自然終了する)。
  // セッション切替で SSE を切るのとは違って、タスク本体に止まってもらう。
  const stop = useCallback(async () => {
    if (!activeId) return
    try {
      const active = await getActiveTask(activeId)
      if (active.task_id) {
        await cancelTask(active.task_id)
      }
    } catch (e) {
      console.warn('stop failed:', e)
    }
  }, [activeId])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      send()
    }
  }

  // ── セッション操作 (ストリーム中の切替も自由に許可。SSE 切断のみ、タスクは生存) ──
  const handleNewSession = useCallback(async (model = null) => {
    try {
      const created = await createSession(null, model)
      const list = await refreshSessions()
      await loadSession(created.id)
      if (!list) await refreshSessions()
    } catch (e) {
      setError(e.message)
    }
  }, [refreshSessions, loadSession])

  // 短歌一覧のカードから元の会話セッションの該当メッセージへジャンプする。
  // sessionId も持つのは、読込完了前に別セッションへ切り替えた場合に
  // 古い index で誤スクロールしないための保険 (effect 側で照合)。
  const openSessionFromGallery = useCallback(async (id, messageIndex) => {
    pendingScrollRef.current = Number.isInteger(messageIndex)
      ? { sessionId: id, index: messageIndex }
      : null
    setView('chat')
    await loadSession(id)
  }, [loadSession])

  const handleDeleteSession = useCallback(async (id) => {
    if (!confirm('このセッションを削除しますか？（実行中のタスクもキャンセルされます）')) return
    try {
      await deleteSession(id)
      const list = await refreshSessions()
      if (id === activeId) {
        if (list && list.length > 0) {
          await loadSession(list[0].id)
        } else {
          await loadSession(null)
          const created = await createSession()
          await refreshSessions()
          await loadSession(created.id)
        }
      }
    } catch (e) {
      setError(e.message)
    }
  }, [activeId, refreshSessions, loadSession])

  // ── status ヘッダー ──
  const statusOk = health.status === 'ok'
  const statusDegraded = health.status === 'degraded'
  const statusClass = statusOk ? 'ok' : (statusDegraded ? 'warn' : (health.status === 'checking' ? '' : 'err'))
  // アクティブセッションの実効モデル (#20): session.model 固定 > グローバル現在値
  const activeSession = sessions.find((s) => s.id === activeId)
  const effectiveModel = activeSession?.model || health.configured_model
  const statusText = health.status === 'checking'
    ? 'バックエンド確認中…'
    : statusOk
      ? `${effectiveModel} 接続中${activeSession?.model ? ' (セッション固定)' : ''}`
      : statusDegraded
        ? `部分的に接続: LM=${health.lm_studio_ok ? 'OK' : 'NG'} / Mongo=${health.mongo_ok ? 'OK' : 'NG'}`
        : `バックエンド未接続 (${health.error ?? 'unknown'})`

  return (
    <div className="app">
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        onSelect={loadSession}
        onCreate={handleNewSession}
        onDelete={handleDeleteSession}
        onModelSwitched={refreshHealth}
      />

      <div className="main">
        <header>
          <div className="title">llm-jp Tanka Chat</div>
          <div className="view-toggle">
            <button
              className={'view-tab' + (view === 'chat' ? ' active' : '')}
              onClick={() => setView('chat')}
            >チャット</button>
            <button
              className={'view-tab' + (view === 'gallery' ? ' active' : '')}
              onClick={() => setView('gallery')}
            >短歌一覧</button>
            <button
              className={'view-tab' + (view === 'eval' ? ' active' : '')}
              onClick={() => setView('eval')}
            >評価モニタ</button>
          </div>
          <div className="header-right">
            {view === 'chat' && <SessionIdChip id={activeId} />}
            <button
              className="header-link"
              onClick={() => setShowFailures(true)}
              title="長期失敗記憶を表示"
            >
              失敗履歴
            </button>
            <div className={`status ${statusClass}`}>
              <span className="dot" />
              {statusText}
            </div>
          </div>
        </header>

        {showFailures && <FailuresPanel onClose={() => setShowFailures(false)} />}

        {view === 'eval' && <EvalMonitor />}

        {view === 'gallery' && <TankaGallery onOpenSession={openSessionFromGallery} />}

        {view === 'chat' && (
        <>
        <main ref={scrollRef}>
          {messages.length === 0 && (
            <div className="empty">
              <p>メッセージを入力してください。</p>
              <p className="hint">短歌は <code>tanka:&lt;お題&gt;</code> で開始。</p>
              <p className="hint">例: <code>tanka:夏の夕暮れ</code></p>
            </div>
          )}

          {messages.map((m, i) => {
            if (m.kind === 'user') return <UserMessage key={i} content={m.content} />
            if (m.kind === 'assistant')
              return <AssistantMessage key={i} thinking={m.thinking} answer={m.answer} streaming={m.streaming} />
            if (m.kind === 'tanka') return <TankaMessage key={i} msg={m} />
            if (m.kind === 'system') return <div key={i} className="msg msg-system">{m.content}</div>
            return null
          })}

          {error && <div className="error-banner">{error}</div>}
        </main>

        <footer>
          {tankaMode && <div className="mode-pill">短歌モード（フォローアップ可・<code>/end-tanka</code> で解除）</div>}
          <div className="input-row">
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={tankaMode ? '短歌について質問…' : 'メッセージを入力…  (短歌は tanka:<お題>)'}
              disabled={!!streaming || !activeId}
            />
            {streaming ? (
              <button className="send-btn stop-btn" onClick={stop} title="中断">
                <svg viewBox="0 0 16 16" fill="currentColor"><rect x="4" y="4" width="8" height="8" rx="1"/></svg>
                <span>中断</span>
              </button>
            ) : (
              <button
                className="send-btn"
                onClick={send}
                disabled={!input.trim() || !activeId}
                title="送信 (Cmd/Ctrl + Enter)"
              >
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="8" y1="13" x2="8" y2="3" />
                  <polyline points="3,8 8,3 13,8" />
                </svg>
                <span>送信</span>
              </button>
            )}
          </div>
          <div className="footer-hint">
            送信ボタンをクリック、または <kbd>⌘ / Ctrl</kbd> + <kbd>Enter</kbd> で送信。Enter は改行。
          </div>
        </footer>
        </>
        )}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppInner />
    </ErrorBoundary>
  )
}
