import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import Header from "../components/Header.jsx";
import { api } from "../api.js";
import { markSeen, setProcessed } from "../videoCache.js";

export default function Loading() {
  const { videoId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const video = location.state?.video;
  const [errorMsg, setErrorMsg] = useState("");
  const started = useRef(false);

  useEffect(() => {
    if (!video) {
      navigate("/home", { replace: true });
      return;
    }
    if (started.current) return;
    started.current = true;

    (async () => {
      try {
        const result = await api.processVideo(videoId);
        setProcessed(videoId, result);
        if (video.comment_count != null) {
          markSeen(videoId, video.comment_count);
        }
        navigate(`/videos/${videoId}/comments`, { state: { video }, replace: true });
      } catch (err) {
        setErrorMsg(err.message);
      }
    })();
  }, [video, videoId, navigate]);

  return (
    <div className="page">
      <Header showBack />
      {video && (
        <div className="video-meta-row">
          {video.thumbnail && <img src={video.thumbnail} alt="" />}
          <div className="video-meta-title">{video.title}</div>
        </div>
      )}
      {errorMsg ? (
        <div className="centered-state error-text">{errorMsg}</div>
      ) : (
        <div className="centered-state">コメントを読み込み中です…</div>
      )}
    </div>
  );
}
