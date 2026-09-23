import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import Header from "../components/Header.jsx";
import { api } from "../api.js";

export default function Connect() {
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState(null);
  const [searchParams] = useSearchParams();
  const oauthError = searchParams.get("error");

  const handleConnect = async () => {
    setConnecting(true);
    setError(null);
    try {
      const { url } = await api.getLoginUrl();
      window.location.href = url;
    } catch (err) {
      setError(err.message);
      setConnecting(false);
    }
  };

  return (
    <div className="page">
      <Header showBack />
      <div className="card connect-card">
        <h1>Googleアカウントと連携する</h1>
        <p>
          コメントを取得・整理するために、YouTubeチャンネルへのアクセスを許可してください。
          設定した内容はこのあと自動的に反映されます。
        </p>
        <button className="btn-primary" style={{ width: "100%" }} disabled={connecting} onClick={handleConnect}>
          {connecting ? "接続しています…" : "Googleで連携する"}
        </button>
        {(error || oauthError) && (
          <p className="error-text">
            {oauthError === "no_channel"
              ? "YouTubeチャンネルが見つかりませんでした。チャンネルを持つアカウントで再度お試しください。"
              : error}
          </p>
        )}
      </div>
    </div>
  );
}
