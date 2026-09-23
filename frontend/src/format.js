// 数値・日付の表示フォーマットを共通化するユーティリティ。
export function formatCount(n) {
  return (n ?? 0).toLocaleString("ja-JP");
}

export function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}.${m}.${day}`;
}
