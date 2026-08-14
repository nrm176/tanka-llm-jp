// 表示用フォーマッタ。App.jsx / TankaGallery.jsx で共用する。

// 所要秒数 → 人間可読な日本語表記 (例: 45秒 / 3分12秒 / 1時間5分)。
// 数値でない・負・非有限は null を返す (呼び出し側は非表示にする)。
export function formatDuration(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null
  const t = Math.round(seconds)
  if (t < 60) return `${t}秒`
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const s = t % 60
  if (h > 0) return `${h}時間${m}分`
  return s > 0 ? `${m}分${s}秒` : `${m}分`
}
