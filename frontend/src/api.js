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
  banAuthor: (commentId) => request(`/api/comments/${commentId}/ban`, { method: "POST" }),
  replyToComment: (commentId, text) =>
    request(`/api/comments/${commentId}/reply`, { method: "POST", body: JSON.stringify({ text }) }),
  rephrase: (text) =>
    request("/api/comments/rephrase", { method: "POST", body: JSON.stringify({ text }) }),
  getSimilarityMarks: () => request("/api/similarity-marks"),
  resetSimilarityMarks: () => request("/api/similarity-marks", { method: "DELETE" }),
  refineSimilarityMark: (index, exceptionNote) =>
    request(`/api/similarity-marks/${index}/refine`, {
      method: "POST",
      body: JSON.stringify({ exception_note: exceptionNote }),
    }),
};
