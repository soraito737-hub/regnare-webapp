import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Header from "../components/Header.jsx";
import { api } from "../api.js";

// Googleとの連携に失敗したときの画面。利用規約の画面に戻すと「何が起きたのか」が
// 分からないので、理由と、次に何をすればよいかをここで説明する。
const REASONS = {
  no_channel: {
    title: "YouTubeチャンネルが見つかりませんでした",
    lead: "選んだGoogleアカウントに、YouTubeチャンネルがありませんでした。次のどちらかを確かめてください。",
    steps: [
      {
        heading: "チャンネルを別の名前で作っている場合(ブランドアカウント)",
        body: "連携の途中で「アカウントまたはチャンネルを選択」という画面が出たら、自分の名前ではなく、チャンネル名のほうを選んでください。",
      },
      {
        heading: "まだチャンネルを作っていない場合",
        body: "YouTubeの右上のアイコンから「チャンネルを作成」を選ぶと作れます。動画がなくても、Regskipは使えます。",
      },
    ],
    retry: true,
  },
  not_invited: {
    title: "このチャンネルは、まだ利用できません",
    lead: "Regskipは今、招待したチャンネルだけが使える期間中です。利用したい場合は、運営チーム(Team Regulus)にご連絡ください。",
    steps: [],
    retry: false,
  },
  denied: {
    title: "連携が完了しませんでした",
    lead: "Googleの画面で「キャンセル」を押したか、YouTubeへのアクセスが許可されませんでした。",
    steps: [
      {
        heading: "もう一度連携するとき",
        body: "Googleの確認画面で、YouTubeへのアクセスにチェックが入っていることを確かめてから「続行」を押してください。",
      },
    ],
    retry: true,
  },
  failed: {
    title: "連携中にエラーが起きました",
    lead: "Googleとのやりとりの途中で問題が起きました。少し時間をおいて、もう一度お試しください。",
    steps: [],
    retry: true,
  },
};

export default function ConnectError() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const reason = REASONS[searchParams.get("reason")] ? searchParams.get("reason") : "failed";
  const info = REASONS[reason];
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");

  // 利用規約にはもう同意して連携を始めているので、ここからはそのままGoogleの画面に進める
  const retry = async () => {
    setConnecting(true);
    setError("");
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
      <Header showBack onBack={() => navigate("/connect")} />
      <div className="card connect-card connect-error-card" role="alert">
        <h1>{info.title}</h1>
        <p>{info.lead}</p>
        {info.steps.length > 0 && (
          <div className="connect-error-steps">
            {info.steps.map((s) => (
              <div key={s.heading} className="connect-error-step">
                <h2>{s.heading}</h2>
                <p>{s.body}</p>
              </div>
            ))}
          </div>
        )}
        {error && <p className="error-text">{error}</p>}
        <div className="connect-error-actions">
          {info.retry && (
            <button className="btn-primary" disabled={connecting} onClick={retry}>
              {connecting ? "接続しています…" : "もう一度Googleで連携する"}
            </button>
          )}
          <button className="btn-secondary" onClick={() => navigate("/")}>
            トップに戻る
          </button>
        </div>
      </div>
    </div>
  );
}
