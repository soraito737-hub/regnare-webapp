import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "../components/Header.jsx";
import { api } from "../api.js";
import { formatCount, formatDate } from "../format.js";
import { getSeenCount, hasSeenRecord, markSeen } from "../videoCache.js";

// このアプリで一度も開いていない動画は、今ある既存コメントを丸ごと「新着」扱いしないよう、
// 現在の件数をそのまま基準値として記録するだけにする。
function initSeenBaselines(list) {
  for (const v of list) {
    if (!hasSeenRecord(v.video_id)) {
      markSeen(v.video_id, v.comment_count ?? 0);
    }
  }
}

export default function Home() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [errorMsg, setErrorMsg] = useState("");
  const [videos, setVideos] = useState([]);
  const [nextPageToken, setNextPageToken] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const me = await api.getMe();
        if (!me.logged_in) {
          navigate("/settings");
          return;
        }

        const pending = localStorage.getItem("pendingProfile");
        if (pending) {
          await api.saveProfile(JSON.parse(pending));
          localStorage.removeItem("pendingProfile");
        }

        const { videos: list, next_page_token } = await api.getVideos();
        initSeenBaselines(list);
        setVideos(list);
        setNextPageToken(next_page_token);
        setStatus("ready");
      } catch (err) {
        setErrorMsg(err.message);
        setStatus("error");
      }
    })();
  }, [navigate]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const { videos: more, next_page_token } = await api.getVideos(nextPageToken);
      initSeenBaselines(more);
      setVideos((v) => [...v, ...more]);
      setNextPageToken(next_page_token);
    } catch (err) {
      alert(err.message);
    } finally {
      setLoadingMore(false);
    }
  };

  if (status === "loading") {
    return (
      <div className="page">
        <Header showMenu onMenuSettings={() => navigate("/settings")} />
        <p className="page-lead">あなたのチャンネルの動画一覧です。動画を選ぶとコメントを確認・整理できます。</p>
        <div className="centered-state">読み込み中…</div>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="page">
        <Header showMenu onMenuSettings={() => navigate("/settings")} />
        <p className="page-lead">あなたのチャンネルの動画一覧です。動画を選ぶとコメントを確認・整理できます。</p>
        <div className="centered-state error-text">{errorMsg}</div>
      </div>
    );
  }

  const renderCard = (video) => {
    // 「前回この動画を開いた/処理した時点の件数」との差分だけをバッジにする。
    // 一度開いた動画は、Loading.jsxでその時点の件数がmarkSeenされているので、
    // 見終わった直後はバッジが0になり、その後さらにコメントが増えた分だけ表示される。
    const newCount = Math.max((video.comment_count ?? 0) - getSeenCount(video.video_id), 0);
    return (
      <button
        key={video.video_id}
        className="video-card"
        onClick={() => navigate(`/videos/${video.video_id}/loading`, { state: { video } })}
      >
        <div className="video-thumb-wrap">
          {video.thumbnail && <img src={video.thumbnail} alt="" loading="lazy" />}
          {newCount > 0 && <span className="video-badge">💚 {newCount}</span>}
          {video.duration && <span className="video-duration">{video.duration}</span>}
          <div className="video-title-overlay">
            <div className="video-title">{video.title}</div>
          </div>
        </div>
        <div className="video-caption">
          <div className="video-stats">
            <span>👁 {formatCount(video.view_count)}</span>
            <span>💬 {formatCount(video.comment_count)}</span>
            <span>{formatDate(video.published_at)}</span>
          </div>
        </div>
      </button>
    );
  };

  return (
    <div className="page">
      <Header showMenu onMenuSettings={() => navigate("/settings")} />
      <p className="page-lead">あなたのチャンネルの動画一覧です。動画を選ぶとコメントを確認・整理できます。</p>
      <div className="video-grid">{videos.map(renderCard)}</div>
      {videos.length === 0 && <div className="empty-state">動画が見つかりませんでした</div>}
      {nextPageToken && (
        <div className="load-more-wrap">
          <button className="btn-secondary" disabled={loadingMore} onClick={loadMore}>
            {loadingMore ? "読み込み中…" : "過去の動画をさらに読み込む"}
          </button>
        </div>
      )}
    </div>
  );
}
