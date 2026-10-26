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
        <div className="privacy-guarantee">
          <p className="privacy-guarantee-title">個人情報はこう守られます</p>
          <ul>
            <li><strong>パスワードは一切取得しません。</strong>Google連携(OAuth)のため、あなたのGoogleパスワードがRegskipを通ることはありません。</li>
            <li><strong>メールアドレス・本名は取得しません。</strong>取得するのはチャンネルID・アイコン画像だけです。</li>
            <li><strong>コメント内容はAIの学習に使われません。</strong>有料APIを利用しているため、送信したコメントがGoogleのAIモデルの学習データとして使われることはありません。</li>
            <li><strong>第三者への販売・共有は一切行いません。</strong></li>
          </ul>
        </div>
        <div className="privacy-detail">
          <p className="privacy-detail-title">連携すると何が行われるか</p>
          <ul>
            <li>あなたのYouTubeチャンネルのコメント取得・削除・返信・ユーザー非表示を、あなたの操作に応じて行います。</li>
            <li>コメント本文は、内容を判定するためGoogleのAI(Gemini API)に送信されます。</li>
            <li>連携情報(アクセストークン)は、この機能を提供するためだけに保存し、ログアウトまたは連携解除でいつでも取り消せます。</li>
          </ul>
        </div>
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
