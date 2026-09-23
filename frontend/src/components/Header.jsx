import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";

// アカウントアイコンは、ページごとに出たり出なかったりしないよう、
// Header自身がログイン状態を見て「ログイン中なら常に表示する」ようにする。
export default function Header({ showBack = false, onBack, showMenu = false, onMenuSettings }) {
  const navigate = useNavigate();
  const [avatarUrl, setAvatarUrl] = useState("");
  const [loggedIn, setLoggedIn] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [menuOpen]);

  const goSettings = () => {
    setMenuOpen(false);
    onMenuSettings?.();
  };

  const handleLogout = async () => {
    setMenuOpen(false);
    try {
      await api.logout();
    } finally {
      // ログイン状態やアバターなど、Header含め画面全体の状態をきれいにリセットするため
      // SPA遷移ではなくページを丸ごと読み込み直す。
      window.location.href = "/";
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.getMe();
        if (cancelled) return;
        setLoggedIn(!!me.logged_in);
        setAvatarUrl(me.avatar_url || "");
      } catch {
        // 未ログイン、またはAPI未起動。アイコンを出さないだけ。
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="header">
      <div className="header-side header-side-left">
        {showBack && (
          <button className="icon-btn icon-btn--back" onClick={onBack ?? (() => navigate(-1))} aria-label="戻る">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path
                d="M15 5l-7 7 7 7"
                stroke="currentColor"
                strokeWidth="2.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}
        {showMenu && (
          <div className="header-menu" ref={menuRef}>
            <button
              className="icon-btn"
              onClick={() => setMenuOpen((v) => !v)}
              aria-label="メニュー"
            >
              ☰
            </button>
            {menuOpen && (
              <div className="card header-menu-dropdown">
                <button onClick={goSettings}>初期設定</button>
                <button onClick={handleLogout}>ログアウト</button>
              </div>
            )}
          </div>
        )}
      </div>
      <div className="header-brand">
        <div className="header-brand-text">
          <span className="logo">Regskip</span>
          <span className="tagline">あなたの発信を、あなたらしく守る。</span>
        </div>
      </div>
      <div className="header-side header-side-right">
        {loggedIn && (
          <button className="icon-btn header-account" aria-label="アカウント">
            {avatarUrl ? <img src={avatarUrl} alt="" /> : "👤"}
          </button>
        )}
      </div>
    </div>
  );
}
