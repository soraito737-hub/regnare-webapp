import { useNavigate } from "react-router-dom";
import Header from "../components/Header.jsx";
import { TermsBody } from "../components/LegalContent.jsx";

export default function Terms() {
  const navigate = useNavigate();
  return (
    <div className="page">
      {/* このページは検索やGoogle審査からの直接アクセスもあるため、ブラウザ履歴に
          依存するnavigate(-1)ではなく、明示的にトップへ戻す。 */}
      <Header showBack onBack={() => navigate("/")} />
      <div className="settings-lead">
        <h1>利用規約</h1>
      </div>
      <div className="card legal-doc">
        <TermsBody />
      </div>
    </div>
  );
}
