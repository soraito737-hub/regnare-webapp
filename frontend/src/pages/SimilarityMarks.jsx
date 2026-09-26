import { useEffect, useState } from "react";
import Header from "../components/Header.jsx";
import { api } from "../api.js";
import { clearProcessed } from "../videoCache.js";

const ACTION_LABEL = { normal: "非表示にしない", hide_regskip: "本サイトで非表示", hide_youtube: "YouTube上で削除" };

const MARK_TABS = [
  { key: "hide_regskip", label: "本サイトで非表示" },
  { key: "hide_youtube", label: "YouTube上で削除" },
];

const DEFAULT_THRESHOLD = 0.85;

function MarkCard({ mark, onRefine, onResetThreshold }) {
  const [refineOpen, setRefineOpen] = useState(false);
  const [exceptionNote, setExceptionNote] = useState("");
  const [refining, setRefining] = useState(false);
  const [resettingThreshold, setResettingThreshold] = useState(false);
  const isStricter = mark.threshold > DEFAULT_THRESHOLD;

  const submitRefine = async () => {
    const note = exceptionNote.trim();
    if (!note) return;
    setRefining(true);
    try {
      await onRefine(mark.index, note);
      setRefineOpen(false);
      setExceptionNote("");
    } catch (err) {
      alert(err.message);
    } finally {
      setRefining(false);
    }
  };

  const handleResetThreshold = async () => {
    setResettingThreshold(true);
    try {
      await onResetThreshold(mark.index);
    } catch (err) {
      alert(err.message);
    } finally {
      setResettingThreshold(false);
    }
  };

  return (
    <div className="card mark-card">
      <div className="mark-card-body">
        {mark.video_id && (
          <img
            className="mark-card-thumb"
            src={`https://i.ytimg.com/vi/${mark.video_id}/mqdefault.jpg`}
            alt=""
            loading="lazy"
          />
        )}
        <div className="mark-card-content">
          <div className="mark-card-tags">
            <span className="category-tag">
              {mark.categories.join("・")} / {mark.tatemae_pattern}
            </span>
            <span className="mark-card-action">{ACTION_LABEL[mark.action]}</span>
            {isStricter && (
              <span className="category-tag mark-card-strict">必要な一致度 {mark.threshold.toFixed(2)}</span>
            )}
          </div>
          <p className="mark-card-comment">「{mark.comment}」</p>
          {mark.note && <p className="mark-card-note">見たくない理由: {mark.note}</p>}
        </div>
      </div>

      {refineOpen ? (
        <div className="hide-note-box">
          <p className="hide-note-label">
            このコメントをもとに戻す理由を記入してください(任意)
          </p>
          <textarea
            value={exceptionNote}
            onChange={(e) => setExceptionNote(e.target.value)}
            placeholder="例:容姿の話には当てはまらない"
            rows={2}
          />
          <div className="hide-note-actions">
            <button
              className="btn-secondary"
              onClick={() => {
                setRefineOpen(false);
                setExceptionNote("");
              }}
            >
              キャンセル
            </button>
            <button className="btn-primary" disabled={refining || !exceptionNote.trim()} onClick={submitRefine}>
              {refining ? "更新しています…" : "この内容で直す"}
            </button>
          </div>
        </div>
      ) : (
        <div className="mark-card-actions">
          <button className="btn-secondary" onClick={() => setRefineOpen(true)}>
            視聴者コメントに戻す
          </button>
          {isStricter && (
            <button className="btn-text" disabled={resettingThreshold} onClick={handleResetThreshold}>
              {resettingThreshold ? "戻しています…" : "厳しさを元に戻す"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function SimilarityMarks() {
  const [marks, setMarks] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [resetting, setResetting] = useState(false);
  const [markTab, setMarkTab] = useState("hide_regskip");

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.getSimilarityMarks();
      setMarks(data.marks);
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleRefine = async (index, exceptionNote) => {
    await api.refineSimilarityMark(index, exceptionNote);
    // マークの中身が変わったので、次に動画を開いたときに判定をやり直させる。
    clearProcessed();
    await load();
  };

  const handleResetThreshold = async (index) => {
    await api.resetSimilarityMarkThreshold(index);
    clearProcessed();
    await load();
  };

  const handleReset = async () => {
    if (!window.confirm("すべての学習データを削除します。よろしいですか?")) return;
    setResetting(true);
    try {
      await api.resetSimilarityMarks();
      clearProcessed();
      await load();
    } catch (err) {
      alert(err.message);
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="page">
      <Header showBack />
      <div className="settings-lead">
        <h1>本サイトのコメント管理</h1>
        <p>
          「本サイトで非表示」「YouTube上で削除する」を押すと、そのコメントの内容をRegskipが記録し、
          次に似た言い回しのコメントが来たときに同じ判断を自動で繰り返せるようにします。
          関係のないコメントまで同じ扱いになってしまった場合は、それぞれの記録から「視聴者コメントに戻す」で修正できます。
        </p>
      </div>

      {loading && <div className="centered-state">読み込み中…</div>}
      {!loading && error && <div className="centered-state error-text">{error}</div>}

      {!loading && !error && marks && (
        <>
          {marks.length > 0 && (
            <div className="mark-toolbar">
              <div className="subtabs">
                {MARK_TABS.map((tab) => (
                  <button
                    key={tab.key}
                    className={`subtab-btn ${markTab === tab.key ? "active" : ""}`}
                    onClick={() => setMarkTab(tab.key)}
                  >
                    {tab.label} ({marks.filter((m) => m.action === tab.key).length})
                  </button>
                ))}
              </div>
              <button className="btn-secondary" disabled={resetting} onClick={handleReset}>
                {resetting ? "リセットしています…" : "全てリセットする"}
              </button>
            </div>
          )}

          {marks.length === 0 ? (
            <div className="empty-state">まだ学習データはありません</div>
          ) : marks.filter((m) => m.action === markTab).length === 0 ? (
            <div className="empty-state">このタブにはまだ学習データはありません</div>
          ) : (
            marks
              .filter((m) => m.action === markTab)
              .map((mark) => (
                <MarkCard
                  key={mark.index}
                  mark={mark}
                  onRefine={handleRefine}
                  onResetThreshold={handleResetThreshold}
                />
              ))
          )}
        </>
      )}
    </div>
  );
}
