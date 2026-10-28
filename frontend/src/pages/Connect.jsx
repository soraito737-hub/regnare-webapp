import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import Header from "../components/Header.jsx";
import { api } from "../api.js";
import { TermsBody, PrivacyBody } from "../components/LegalContent.jsx";

export default function Connect() {
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState(null);
  const [agreed, setAgreed] = useState(false);
  const [hasScrolledToEnd, setHasScrolledToEnd] = useState(false);
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

  // ページ遷移せずその場で読めるように、利用規約・プライバシーポリシー本文を
  // スクロールボックスに埋め込む。最後まで読んだこと(一番下までスクロール)が
  // 確認できるまでは、同意チェックボックス自体を押せないようにする。
  const handleLegalScroll = (e) => {
    if (hasScrolledToEnd) return;
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    if (scrollHeight - (scrollTop + clientHeight) < 16) {
      setHasScrolledToEnd(true);
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

        <p className="diagnosis-summary-label" style={{ marginBottom: "0.4rem" }}>
          利用規約・プライバシーポリシー
        </p>
        <div className="legal-doc legal-scrollbox" onScroll={handleLegalScroll}>
          <TermsBody />
          <PrivacyBody />
        </div>
        {!hasScrolledToEnd && (
          <p className="diagnosis-result-votes" style={{ margin: "0.4rem 0 0.8rem" }}>
            一番下まで読み進めると、同意できるようになります。
          </p>
        )}

        <label className="combo-checkbox" style={{ marginTop: "0.8rem", marginBottom: "1rem" }}>
          <input
            type="checkbox"
            checked={agreed}
            disabled={!hasScrolledToEnd}
            onChange={(e) => setAgreed(e.target.checked)}
          />
          <span>利用規約とプライバシーポリシーに同意します</span>
        </label>

        <button className="btn-primary" style={{ width: "100%" }} disabled={connecting || !agreed} onClick={handleConnect}>
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
