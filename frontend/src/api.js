const API_URL = import.meta.env.VITE_API_URL;

// FastAPIのバリデーションエラー(422)はdetailが配列(オブジェクトの配列)になる。
// そのままErrorに渡すと"[object Object],..."という文字列になってしまうため、
// 読める形に変換する。detailが単純な文字列の場合はそのまま使う。
function formatErrorDetail(detail, status) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((e) => (e && typeof e === "object" ? e.msg || JSON.stringify(e) : String(e)))
      .join(" / ");
  }
  return `リクエストに失敗しました (${status})`;
}

async function request(path, options = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(formatErrorDetail(body.detail, res.status));
  }
  return res.json();
}

export const api = {
  getLoginUrl: () => request("/api/auth/login"),
  getMe: () => request("/api/auth/me"),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  getProfile: () => request("/api/profile"),
  saveProfile: (data) =>
    request("/api/profile", { method: "POST", body: JSON.stringify(data) }),
  getVideos: (pageToken) =>
    request(`/api/videos${pageToken ? `?page_token=${encodeURIComponent(pageToken)}` : ""}`),
  processVideo: (videoId) => request(`/api/videos/${videoId}/process`, { method: "POST" }),
  markComment: (commentId, body) =>
    request(`/api/comments/${commentId}/mark`, { method: "POST", body: JSON.stringify(body) }),
  deleteCommentOnYoutube: (commentId) =>
    request(`/api/comments/${commentId}/delete-youtube`, { method: "POST" }),
  deleteCommentsOnYoutubeBulk: (commentIds) =>
    request("/api/comments/delete-youtube-bulk", {
      method: "POST",
      body: JSON.stringify({ comment_ids: commentIds }),
    }),
  banAuthor: (commentId) => request(`/api/comments/${commentId}/ban`, { method: "POST" }),
  replyToComment: (commentId, text) =>
    request(`/api/comments/${commentId}/reply`, { method: "POST", body: JSON.stringify({ text }) }),
  rephrase: (text, commentId) =>
    request("/api/comments/rephrase", {
      method: "POST",
      body: JSON.stringify({ text, comment_id: commentId }),
    }),
  getSimilarityMarks: () => request("/api/similarity-marks"),
  resetSimilarityMarks: () => request("/api/similarity-marks", { method: "DELETE" }),
  deleteSimilarityMark: (markId) =>
    request(`/api/similarity-marks/${markId}`, { method: "DELETE" }),
  resetSimilarityMarkThreshold: (markId) =>
    request(`/api/similarity-marks/${markId}/reset-threshold`, { method: "POST" }),
  sendSupportMessage: (message, replyEmail, page) =>
    request("/api/support", {
      method: "POST",
      body: JSON.stringify({ message, reply_email: replyEmail, page }),
    }),
  restoreComment: (commentId, note = "") =>
    request(`/api/comments/${commentId}/restore`, { method: "POST", body: JSON.stringify({ note }) }),
};

// 一度YouTubeと接続した人は、次からツール説明(Diagnosis)を飛ばして動画一覧から始める。
// ログイン状態の確認(getMe)はRenderの起動待ちで数十秒かかることがあるので、
// 最初の画面ではブラウザに残した印だけで判断する。本当にログイン中かはHome側で確かめ、
// 切れていればそこで印を消して初期設定に戻す。
const CONNECTED_KEY = "regskipConnected";

export function setConnected(connected) {
  try {
    if (connected) localStorage.setItem(CONNECTED_KEY, "1");
    else localStorage.removeItem(CONNECTED_KEY);
  } catch {
    // ブラウザの保存領域が使えないときは、毎回ツール説明から始めるだけ
  }
}

export function wasConnected() {
  try {
    return localStorage.getItem(CONNECTED_KEY) === "1";
  } catch {
    return false;
  }
}
