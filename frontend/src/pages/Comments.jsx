import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import Header from "../components/Header.jsx";
import { api } from "../api.js";
import { formatCount, formatDate } from "../format.js";
import { dismissedAuthors, getProcessed, setProcessed } from "../videoCache.js";

const HIGH_SIMILARITY_THRESHOLD = 0.93;
const MID_SIMILARITY_THRESHOLD = 0.85;

function similarityTier(score) {
  if (score >= HIGH_SIMILARITY_THRESHOLD) return "high";
  if (score >= MID_SIMILARITY_THRESHOLD) return "grey";
  return "none";
}

// なぜ非表示になったかを、不安にならないよう画面で分かるようにする。
// personal_profile.pyのresolve_display_actionが返すreasonと対応。
const REASON_LABELS = {
  personal_similarity: "過去に似たコメントで自分が選んだ判断を、そのまま再現しています",
  personal_pattern: "初期設定の「特定の言い回し」設定に一致したため、AIが判定しました",
  attack_level: "初期設定の「攻撃的な言い方」設定に一致したため、AIが判定しました",
  error: "AI判定でエラーが発生したため、念のため非表示にしています",
};

// 実際にYouTube側のコメントを操作するボタンだけ、誤クリック防止の確認を挟む。
const YOUTUBE_HIDE_CONFIRM = "このコメントをYouTube上でも非表示にします。よろしいですか?";
function confirmYoutubeHide(action) {
  if (window.confirm(YOUTUBE_HIDE_CONFIRM)) action();
}

// トップレベルのタブ。「非表示」はYouTubeへの報告有無で分かれる「見たくない」の下位区分なので、
// ここには出さずhiddenタブの中でサブタブとして出す。プライバシーは「新着」の中に統合して選べるようにする。
const TABS = [
  { key: "new", label: "新着コメント" },
  { key: "hidden", label: "見たくない" },
  { key: "flagged", label: "要注意ユーザー" },
];

const HIDDEN_SUB_TABS = [
  { key: "hidden_regskip", label: "本サイトで非表示" },
  { key: "hidden_youtube", label: "YouTube上で非表示" },
];

function OpenCommentCard({ entry, tabKey, urgent, openMenuId, setOpenMenuId, onBan, onMove, onReply }) {
  const c = entry.comment;
  const judgment = entry.judgment;
  const tier = similarityTier(entry.similarity_score || 0);
  const [replyOpen, setReplyOpen] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [replySending, setReplySending] = useState(false);
  const [postedReply, setPostedReply] = useState(null);

  const submitReply = async () => {
    const text = replyText.trim();
    if (!text) return;
    setReplySending(true);
    try {
      const reply = await onReply(c.comment_id, text);
      setPostedReply(reply);
      setReplyOpen(false);
      setReplyText("");
    } catch (err) {
      alert(err.message);
    } finally {
      setReplySending(false);
    }
  };

  return (
    <div className="card comment-card">
      <div className="comment-header">
        <div>
          {urgent && (
            <div className="urgent-tag">🔴 プライバシー・緊急の可能性({judgment?.emergency})</div>
          )}
          <div className="comment-author">{c.author}</div>
          <div className="comment-date">{(c.published_at || "").slice(0, 10)}</div>
        </div>
        <div className="comment-menu">
          <button
            className="icon-btn"
            aria-label="メニュー"
            onClick={() => setOpenMenuId(openMenuId === c.comment_id ? null : c.comment_id)}
          >
            ⋮
          </button>
          {openMenuId === c.comment_id && (
            <div className="card comment-menu-dropdown">
              <button
                onClick={() => {
                  onBan(c.comment_id);
                  setOpenMenuId(null);
                }}
              >
                ユーザーを非表示にする
              </button>
            </div>
          )}
        </div>
      </div>
      <div className="comment-text">{c.text}</div>
      {tier === "grey" && <div className="comment-warning">⚠️ 似たコメントに反応したことがあります</div>}
      <div className="comment-actions">
        <button className="btn-secondary" onClick={() => onMove(tabKey, entry, "hide_regskip")}>
          本サイトで非表示
        </button>
        <button
          className="btn-primary"
          onClick={() => confirmYoutubeHide(() => onMove(tabKey, entry, "hide_youtube"))}
        >
          YouTube上で非表示
        </button>
      </div>
      {postedReply ? (
        <div className="posted-reply">
          <span className="posted-reply-label">あなたの返信</span>
          <p>{postedReply.text}</p>
        </div>
      ) : replyOpen ? (
        <div className="reply-box">
          <textarea
            value={replyText}
            onChange={(e) => setReplyText(e.target.value)}
            placeholder="返信を入力…"
            rows={2}
          />
          <div className="reply-box-actions">
            <button
              className="btn-secondary"
              onClick={() => {
                setReplyOpen(false);
                setReplyText("");
              }}
            >
              キャンセル
            </button>
            <button className="btn-primary" disabled={replySending || !replyText.trim()} onClick={submitReply}>
              {replySending ? "送信中…" : "送信"}
            </button>
          </div>
        </div>
      ) : (
        <button className="btn-text" onClick={() => setReplyOpen(true)}>
          返信する
        </button>
      )}
    </div>
  );
}

// 「見たくない」に分類したコメントは、YouTubeへの報告有無に関わらず本文を表示しない。
// 元の言い方が気になる場合のみ「言い換えて見る」で穏やかな要約を見られるようにする。
function HiddenCommentCard({ entry, rephrased, onRephrase, onEscalate, onUnescalate, onExecute }) {
  const c = entry.comment;
  const judgment = entry.judgment;
  const [showOriginal, setShowOriginal] = useState(false);
  // onExecuteが渡されている(=YouTube上で非表示タブにいる)のに、まだ実行済みでない場合。
  // 類似コメント判定で自動的にこのタブに来ただけで、実際のYouTube側の非表示はまだ行われていない。
  const needsExecution = !!onExecute && !entry.executed_on_youtube;
  return (
    <div className="card comment-card">
      <div className="comment-author">{c.author}</div>
      {judgment && (
        <div className="category-tag">
          {judgment.categories.join("・")} / {judgment.tatemae_pattern}
        </div>
      )}
      {REASON_LABELS[entry.reason] && (
        <div className="reason-tag">{REASON_LABELS[entry.reason]}</div>
      )}
      {needsExecution && (
        <div className="pending-tag">
          まだYouTube上では非表示になっていません(似たコメントとして自動で振り分けられただけです)
        </div>
      )}
      {showOriginal && <div className="comment-text">{c.text}</div>}
      <div className="comment-actions">
        <button className="btn-secondary" onClick={() => setShowOriginal((v) => !v)}>
          {showOriginal ? "本文を隠す" : "本文を見る"}
        </button>
        <button className="btn-secondary" onClick={() => onRephrase(c.comment_id, c.text)}>
          言い換えて見る
        </button>
        {needsExecution && (
          <button className="btn-primary" onClick={() => confirmYoutubeHide(onExecute)}>
            実行する
          </button>
        )}
        {onEscalate && (
          <button className="btn-primary" onClick={() => confirmYoutubeHide(onEscalate)}>
            YouTube上でも非表示にする
          </button>
        )}
        {onUnescalate && (
          <button className="btn-secondary" onClick={onUnescalate}>
            本サイトだけの非表示に戻す
          </button>
        )}
      </div>
      {rephrased[c.comment_id] && <div className="rephrase-box">{rephrased[c.comment_id]}</div>}
    </div>
  );
}

function FlaggedAuthorCard({ author, allEntries, onBan, onDismiss }) {
  const [revealed, setRevealed] = useState(false);
  const breakdownLines = Object.entries(author.breakdown).map(([key, count]) => {
    const [cat, pat] = key.split("|");
    return `${cat}×${pat} ${count}件`;
  });
  const matches = allEntries.filter((e) => e.comment.author_channel_id === author.author_channel_id);
  const displayName = matches[0]?.comment.author ?? author.author_channel_id;

  return (
    <div className="card flagged-card">
      <div className="flagged-title">{displayName}</div>
      <div className="flagged-sub">直近{author.count}回、見たくない/非表示に分類</div>
      <div className="flagged-breakdown">{breakdownLines.join("、")}</div>
      <button className="btn-secondary" onClick={() => setRevealed((v) => !v)}>
        コメントを見る
      </button>
      {revealed && (
        <div className="flagged-reveal">
          {matches.map((e) => (
            <div key={e.comment.comment_id}>
              {e.judgment && (
                <div className="category-tag">
                  {e.judgment.categories.join("・")} / {e.judgment.tatemae_pattern}
                </div>
              )}
              <div className="comment-text">{e.comment.text}</div>
            </div>
          ))}
        </div>
      )}
      <div className="flagged-actions">
        <button className="btn-primary" onClick={() => onBan(author.author_channel_id, matches)}>
          ユーザーを非表示にする
        </button>
        <button className="btn-secondary" onClick={() => onDismiss(author.author_channel_id)}>
          このままにする
        </button>
      </div>
    </div>
  );
}

export default function Comments() {
  const { videoId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const video = location.state?.video;

  const [data, setData] = useState(() => getProcessed(videoId));
  const [activeTab, setActiveTab] = useState("new");
  const [hiddenSubTab, setHiddenSubTab] = useState("hidden_regskip");
  const [dismissed, setDismissed] = useState(() => new Set(dismissedAuthors));
  const [rephrased, setRephrased] = useState({});
  const [openMenuId, setOpenMenuId] = useState(null);

  useEffect(() => {
    if (!video) {
      navigate("/home", { replace: true });
      return;
    }
    if (!data) {
      navigate(`/videos/${videoId}/loading`, { state: { video }, replace: true });
    }
  }, [video, data, videoId, navigate]);

  if (!video || !data) {
    return (
      <div className="page">
        <Header showBack />
        <div className="centered-state">読み込み中…</div>
      </div>
    );
  }

  const allEntries = Object.values(data.tabs).flat();

  const moveComment = async (tabKey, entry, newAction) => {
    const c = entry.comment;
    // AI判定が失敗したコメント(judgment===null)でも押せるように、その場合は
    // 「該当なし」扱いのフォールバック値を使う。
    const judgment = entry.judgment;
    try {
      await api.markComment(c.comment_id, {
        action: newAction,
        text: c.text,
        categories: judgment?.categories ?? ["該当なし"],
        tatemae_pattern: judgment?.tatemae_pattern ?? "該当なし",
        surface_level: judgment?.surface_level ?? 1,
        author_channel_id: c.author_channel_id,
      });
    } catch (err) {
      alert(err.message);
      return;
    }
    const tabs = { ...data.tabs };
    tabs[tabKey] = tabs[tabKey].filter((e) => e.comment.comment_id !== c.comment_id);
    const newTab = newAction === "hide_youtube" ? "hidden_youtube" : "hidden_regskip";
    // ボタンを押した(=mark_commentが呼ばれ、hide_youtubeなら実際にYouTube側も非表示にした)ので、
    // 実行済みとしてマークする。
    const movedEntry = newAction === "hide_youtube" ? { ...entry, executed_on_youtube: true } : entry;
    tabs[newTab] = [...tabs[newTab], movedEntry];
    const next = { ...data, tabs };
    setProcessed(videoId, next);
    setData(next);
  };

  // 「見たくない」タブのYouTube上で非表示サブタブで、類似判定により自動で来ただけの
  // (まだ実際にはYouTube側で非表示になっていない)コメントを、その場で実際に非表示にする。
  const executeYoutubeHide = async (entry) => {
    const c = entry.comment;
    const judgment = entry.judgment;
    try {
      await api.markComment(c.comment_id, {
        action: "hide_youtube",
        text: c.text,
        categories: judgment?.categories ?? ["該当なし"],
        tatemae_pattern: judgment?.tatemae_pattern ?? "該当なし",
        surface_level: judgment?.surface_level ?? 1,
        author_channel_id: c.author_channel_id,
      });
    } catch (err) {
      alert(err.message);
      return;
    }
    const tabs = { ...data.tabs };
    tabs.hidden_youtube = tabs.hidden_youtube.map((e) =>
      e.comment.comment_id === c.comment_id ? { ...e, executed_on_youtube: true } : e
    );
    const next = { ...data, tabs };
    setProcessed(videoId, next);
    setData(next);
  };

  const handleBan = async (commentId) => {
    try {
      await api.banAuthor(commentId);
    } catch (err) {
      alert(err.message);
    }
  };

  const handleReply = (commentId, text) => api.replyToComment(commentId, text);

  const handleRephrase = async (commentId, text) => {
    try {
      const { rephrased: r } = await api.rephrase(text);
      setRephrased((prev) => ({ ...prev, [commentId]: r }));
    } catch (err) {
      alert(err.message);
    }
  };

  const dismissAuthor = (authorId) => {
    dismissedAuthors.add(authorId);
    setDismissed(new Set(dismissedAuthors));
  };

  const banAuthorFromFlag = async (authorId, matches) => {
    if (matches[0]) {
      await handleBan(matches[0].comment.comment_id);
    }
    dismissAuthor(authorId);
  };

  const visibleFlagged = data.flagged_authors.filter((a) => !dismissed.has(a.author_channel_id));

  return (
    <div className="page">
      <Header showBack />
      <div className="video-meta-row">
        {video.thumbnail && <img src={video.thumbnail} alt="" />}
        <div className="video-meta-info">
          <div className="video-meta-title">{video.title}</div>
          <div className="video-meta-stats">
            <span>👁 {formatCount(video.view_count)}</span>
            <span>{formatDate(video.published_at)}</span>
          </div>
          <a
            className="video-meta-link"
            href={`https://www.youtube.com/watch?v=${video.video_id}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            YouTubeで元動画を見る ↗
          </a>
        </div>
      </div>

      <div className="tabs">
        {TABS.map((tab) => {
          let count = null;
          if (tab.key === "flagged") count = data.flagged_authors.length;
          else if (tab.key === "new") count = data.tabs.new.length + data.tabs.privacy.length;
          return (
            <button
              key={tab.key}
              className={`tab-btn ${activeTab === tab.key ? "active" : ""}`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
              {count !== null && ` (${count})`}
            </button>
          );
        })}
      </div>

      {activeTab === "new" &&
        (data.tabs.privacy.length + data.tabs.new.length ? (
          <>
            <div className="new-tab-note">
              本サイトで非表示/YouTube上で非表示を選ぶと、似た言い回しのコメントは新着ではなく選んだ方のタブに直接振り分けられます
            </div>
            {data.tabs.privacy.map((entry) => (
              <OpenCommentCard
                key={entry.comment.comment_id}
                entry={entry}
                tabKey="privacy"
                urgent
                openMenuId={openMenuId}
                setOpenMenuId={setOpenMenuId}
                onBan={handleBan}
                onMove={moveComment}
                onReply={handleReply}
              />
            ))}
            {data.tabs.new.map((entry) => (
              <OpenCommentCard
                key={entry.comment.comment_id}
                entry={entry}
                tabKey="new"
                openMenuId={openMenuId}
                setOpenMenuId={setOpenMenuId}
                onBan={handleBan}
                onMove={moveComment}
                onReply={handleReply}
              />
            ))}
          </>
        ) : (
          <div className="empty-state">新着コメントはありません</div>
        ))}

      {activeTab === "hidden" && (
        <>
          <div className="subtabs">
            {HIDDEN_SUB_TABS.map((tab) => (
              <button
                key={tab.key}
                className={`subtab-btn ${hiddenSubTab === tab.key ? "active" : ""}`}
                onClick={() => setHiddenSubTab(tab.key)}
              >
                {tab.label} ({data.tabs[tab.key].length})
              </button>
            ))}
          </div>
          {data.tabs[hiddenSubTab].length ? (
            data.tabs[hiddenSubTab].map((entry) => (
              <HiddenCommentCard
                key={entry.comment.comment_id}
                entry={entry}
                rephrased={rephrased}
                onRephrase={handleRephrase}
                onEscalate={
                  hiddenSubTab === "hidden_regskip"
                    ? () => moveComment("hidden_regskip", entry, "hide_youtube")
                    : undefined
                }
                onUnescalate={
                  hiddenSubTab === "hidden_youtube"
                    ? () => moveComment("hidden_youtube", entry, "hide_regskip")
                    : undefined
                }
                onExecute={hiddenSubTab === "hidden_youtube" ? () => executeYoutubeHide(entry) : undefined}
              />
            ))
          ) : (
            <div className="empty-state">
              {hiddenSubTab === "hidden_regskip" ? "見たくないコメントはありません" : "非表示にしたコメントはありません"}
            </div>
          )}
        </>
      )}

      {activeTab === "flagged" &&
        (visibleFlagged.length ? (
          visibleFlagged.map((author) => (
            <FlaggedAuthorCard
              key={author.author_channel_id}
              author={author}
              allEntries={allEntries}
              onBan={banAuthorFromFlag}
              onDismiss={dismissAuthor}
            />
          ))
        ) : (
          <div className="empty-state">要注意ユーザーはいません</div>
        ))}
    </div>
  );
}
