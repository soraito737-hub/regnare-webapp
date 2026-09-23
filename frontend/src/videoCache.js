// タブ遷移をまたいで動画ごとの判定結果を覚えておくためのブラウザタブ内キャッシュ。
// Streamlit版の st.session_state.rd_video_comments / rd_dismissed_authors に相当する。
const processed = new Map(); // video_id -> { tabs, flagged_authors }
export const dismissedAuthors = new Set();

export function getProcessed(videoId) {
  return processed.get(videoId);
}

export function setProcessed(videoId, data) {
  processed.set(videoId, data);
}

// 「どこまで見たか」をブラウザに保存しておくための永続キャッシュ(localStorage)。
// YouTube側のコメント総数(comment_count)との差分だけで「新着あり」を判定するので、
// AI判定(Gemini)を呼ばずにホーム画面で新着バッジを出せる。
const SEEN_COUNTS_KEY = "regskip_seen_comment_counts";

function loadSeenCounts() {
  try {
    return JSON.parse(localStorage.getItem(SEEN_COUNTS_KEY)) || {};
  } catch {
    return {};
  }
}

export function getSeenCount(videoId) {
  return loadSeenCounts()[videoId] ?? 0;
}

// まだ一度も記録がない(=このアプリで一度も開いていない)動画かどうか。
// これがtrueの動画は、今ある全コメントを「新着」として数えず、
// 現在の件数を初期値として記録するだけにする(既存コメントを新着扱いしないため)。
export function hasSeenRecord(videoId) {
  return Object.prototype.hasOwnProperty.call(loadSeenCounts(), videoId);
}

export function markSeen(videoId, commentCount) {
  try {
    const counts = loadSeenCounts();
    counts[videoId] = commentCount;
    localStorage.setItem(SEEN_COUNTS_KEY, JSON.stringify(counts));
  } catch {
    // localStorageが使えない(プライベートモード等)場合は無視。新着バッジが消えないだけ。
  }
}
