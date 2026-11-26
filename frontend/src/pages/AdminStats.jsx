import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "../components/Header.jsx";
import { api } from "../api.js";

// 運営チームだけが見られる利用状況。サーバーは人数だけを返し、どのチャンネルかは返さない。
export default function AdminStats() {
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .getAdminStats()
      .then(setStats)
      .catch(() => setError("この画面は運営チームだけが見られます。運営チームのチャンネルでログインしてください。"));
  }, []);

  const tiles = stats && [
    { label: "これまでにログインした人", value: stats.total_users },
    { label: "今日ログインした人", value: stats.active_today },
    { label: "この7日間にログインした人", value: stats.active_7days },
    { label: "この30日間にログインした人", value: stats.active_30days },
    { label: "この7日間に初めてログインした人", value: stats.new_7days },
    { label: "ログインの合計回数", value: stats.total_logins },
  ];

  return (
    <div className="page">
      <Header showBack onBack={() => navigate("/home")} />
      <div className="card admin-stats-card">
        <h1>利用状況</h1>
        <p className="admin-stats-note">
          記録はこの機能を入れた日から始まっています。同じ人が何回ログインしても1人と数えます。運営チーム自身も含みます。30日以上ログインしなかった人がまた来たときは、初めての人として数えます。
        </p>
        {error && <p className="error-text" role="alert">{error}</p>}
        {!stats && !error && <p>読み込んでいます…</p>}
        {stats && (
          <>
            <div className="admin-stats-tiles">
              {tiles.map((t) => (
                <div key={t.label} className="admin-stats-tile">
                  <span className="admin-stats-value">{t.value}</span>
                  <span className="admin-stats-label">{t.label}</span>
                </div>
              ))}
            </div>
            <h2>日ごとの人数(直近14日)</h2>
            <div className="admin-stats-table-wrap">
              <table className="admin-stats-table">
                <thead>
                  <tr>
                    <th>日付</th>
                    <th>ログインした人</th>
                    <th>うち初めての人</th>
                  </tr>
                </thead>
                <tbody>
                  {[...stats.daily].reverse().map((d) => (
                    <tr key={d.date}>
                      <td>{d.date.slice(5).replace("-", "/")}</td>
                      <td>{d.users}</td>
                      <td>{d.new_users}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
