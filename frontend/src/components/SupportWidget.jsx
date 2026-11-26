import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api.js";

// 右下の吹き出し。ログインしてから使う画面にだけ出す
// (送った人のチャンネル名・IDを一緒に届けるため、ログイン前の画面では出さない)。
const SHOWN_PATHS = [/^\/home/, /^\/videos\//, /^\/learned-marks/, /^\/settings/];
const MAX_LENGTH = 2000;

function IconChat() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.6c-.5.4-1.3.1-1.3-.6V16A2.5 2.5 0 0 1 4 13.5v-8Z"
        fill="currentColor"
      />
    </svg>
  );
}

export default function SupportWidget() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [replyEmail, setReplyEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const textareaRef = useRef(null);

  useEffect(() => {
    if (open && !sent) textareaRef.current?.focus();
  }, [open, sent]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const shown = SHOWN_PATHS.some((re) => re.test(location.pathname));

  // 吹き出しがページのいちばん下のボタンに重ならないよう、下に余白を足す
  useEffect(() => {
    document.body.classList.toggle("has-support-widget", shown);
    return () => document.body.classList.remove("has-support-widget");
  }, [shown]);

  if (!shown) return null;

  const submit = async (e) => {
    e.preventDefault();
    if (!message.trim()) return;
    setSending(true);
    setError("");
    try {
      await api.sendSupportMessage(message.trim(), replyEmail.trim(), location.pathname);
      setSent(true);
      setMessage("");
    } catch (err) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  };

  const close = () => {
    setOpen(false);
    if (sent) setSent(false);
  };

  return (
    <div className="support-widget">
      {open && (
        <div className="card support-panel" role="dialog" aria-labelledby="support-title">
          <div className="support-panel-head">
            <h2 id="support-title">お問い合わせ</h2>
            <button type="button" className="icon-btn" aria-label="閉じる" onClick={close}>
              ×
            </button>
          </div>
          {sent ? (
            <div className="support-sent">
              <p>送信しました。ありがとうございます。</p>
              <p className="support-note">
                返信先のメールアドレスを入力した場合は、そちらにお返事します。
              </p>
              <button type="button" className="btn-secondary" onClick={() => setSent(false)}>
                続けて送る
              </button>
            </div>
          ) : (
            <form onSubmit={submit} className="support-form">
              <p className="support-note">
                使い方の質問、不具合、直してほしいところなどを送ってください。運営チーム(Team Regulus)に届きます。
              </p>
              <label htmlFor="support-message" className="support-label">
                内容
              </label>
              <textarea
                id="support-message"
                ref={textareaRef}
                value={message}
                maxLength={MAX_LENGTH}
                rows={5}
                placeholder="例:「アンチではない」を押しても、似たコメントがまた隠れます"
                onChange={(e) => setMessage(e.target.value)}
              />
              <label htmlFor="support-reply-email" className="support-label">
                返信先メールアドレス(任意)
              </label>
              <input
                id="support-reply-email"
                type="email"
                value={replyEmail}
                placeholder="返事がほしい場合に入力"
                onChange={(e) => setReplyEmail(e.target.value)}
              />
              <p className="support-note support-note--small">
                送信すると、あなたのYouTubeチャンネル名とチャンネルIDも一緒に届きます。
              </p>
              {error && <p className="support-error">{error}</p>}
              <button type="submit" className="btn-primary" disabled={sending || !message.trim()}>
                {sending ? "送信しています…" : "送信する"}
              </button>
            </form>
          )}
        </div>
      )}
      <button
        type="button"
        className="support-fab"
        aria-label={open ? "お問い合わせを閉じる" : "お問い合わせ"}
        aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <IconChat />
      </button>
    </div>
  );
}
