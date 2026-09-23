import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "../components/Header.jsx";
import { ATTACK_EXAMPLE, CATEGORIES, OTHER_EXAMPLE, PATTERN_GROUPS, PATTERNS, PATTERN_UI } from "../patternData.js";
import { api } from "../api.js";

// 「その他」= 7パターンのどれにも当てはまらないが、軽い言い方でも隠したい場合の受け皿。
// personal_classifier.py の TatemaePattern.NONE ("該当なし") をそのまま流用する。
const OTHER_PATTERN = "該当なし";

// 各行(攻撃的な言い方/その他/個別パターン)は、それぞれ独立に
// 「非表示にしない/本サイトで非表示/YouTube上で非表示」を選ぶ。カテゴリ単位で
// まとめてYouTube上の非表示を決めてしまうと、AIの判定だけで広い範囲を
// 自動的にYouTubeへ報告することになり得るため、行ごとに個別に選べるようにしている。
function emptyProfile() {
  const attackAction = {};
  const patternAction = {};
  CATEGORIES.forEach((cat) => {
    attackAction[cat] = "normal";
    PATTERNS.forEach((pat) => {
      patternAction[`${cat}|${pat}`] = "normal";
    });
    patternAction[`${cat}|${OTHER_PATTERN}`] = "normal";
  });
  // extraPatternAction: 診断が答えるが、この画面には行として出していないパターン
  // (例:自己満足アドバイス)の値を、UIには出さずそのまま保存まで持ち運ぶための入れ物。
  return { attackAction, patternAction, extraPatternAction: {} };
}

function profileFromServer(json) {
  const profile = emptyProfile();
  Object.entries(json.attack_action || {}).forEach(([cat, action]) => {
    if (cat in profile.attackAction) profile.attackAction[cat] = action;
  });
  Object.entries(json.pattern_action || {}).forEach(([key, value]) => {
    if (key in profile.patternAction) {
      profile.patternAction[key] = value.action;
    } else {
      profile.extraPatternAction[key] = value;
    }
  });
  return profile;
}

function ActionRow({ label, example, value, onChange, master = false }) {
  const [labelHover, setLabelHover] = useState(false);
  const [ytHover, setYtHover] = useState(false);
  return (
    <div className={`action-row${master ? " action-row--master" : ""}`}>
      <span
        className="action-row-label"
        onMouseEnter={() => setLabelHover(true)}
        onMouseLeave={() => setLabelHover(false)}
      >
        {label}
        {example && labelHover && <span className="tooltip">例:{example}</span>}
      </span>
      <div className="action-btn-group" role="radiogroup" aria-label={label}>
        <button
          type="button"
          className={`action-btn ${value === "normal" ? "active" : ""}`}
          onClick={() => onChange("normal")}
        >
          非表示にしない
        </button>
        <button
          type="button"
          className={`action-btn regskip ${value === "hide_regskip" ? "active" : ""}`}
          onClick={() => onChange("hide_regskip")}
        >
          本サイトで非表示
        </button>
        <button
          type="button"
          className={`action-btn youtube ${value === "hide_youtube" ? "active" : ""}`}
          onClick={() => onChange("hide_youtube")}
          onMouseEnter={() => setYtHover(true)}
          onMouseLeave={() => setYtHover(false)}
        >
          YouTube上で非表示
          {ytHover && (
            <span className="tooltip">
              選ぶと「見たくない」タブの中の別フォルダ(YouTube上で非表示)に振り分けられます
            </span>
          )}
        </button>
      </div>
    </div>
  );
}

export default function InitialSettings() {
  const navigate = useNavigate();
  const [profile, setProfile] = useState(emptyProfile);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loggedIn, setLoggedIn] = useState(false);
  const [fromDiagnosis, setFromDiagnosis] = useState(false);

  useEffect(() => {
    (async () => {
      // 診断(/)を答え終えた直後はここに、その結果が反映された状態で遷移してくる。
      // 診断結果はまだ保存されていない下書きなので、サーバーの設定より優先して表示し、
      // 使い終わったらローカルから消す(次回訪問時に古い診断結果が復活しないように)。
      const diagnosisRaw = localStorage.getItem("diagnosisProfile");
      const applyDiagnosis = () => {
        const parsed = JSON.parse(diagnosisRaw);
        setProfile(profileFromServer(parsed));
        setFromDiagnosis(true);
      };
      try {
        const me = await api.getMe();
        if (me.logged_in) {
          setLoggedIn(true);
          if (diagnosisRaw) {
            applyDiagnosis();
          } else {
            const serverProfile = await api.getProfile();
            setProfile(profileFromServer(serverProfile));
          }
        } else if (diagnosisRaw) {
          applyDiagnosis();
        }
      } catch {
        // 未ログイン状態。診断結果があればそれを、なければ空プロフィールのまま進める。
        if (diagnosisRaw) {
          try {
            applyDiagnosis();
          } catch {
            // 破損したデータは無視する。
          }
        }
      } finally {
        localStorage.removeItem("diagnosisProfile");
        setLoading(false);
      }
    })();
  }, []);

  const setAttackAction = (category, action) => {
    setProfile((p) => ({ ...p, attackAction: { ...p.attackAction, [category]: action } }));
  };

  const setPatternAction = (key, action) => {
    setProfile((p) => ({ ...p, patternAction: { ...p.patternAction, [key]: action } }));
  };

  // 初期設定画面では「疑問形」「褒め殺し型」を1行(揶揄・あざけり)にまとめて表示・操作する
  // (PATTERN_GROUPS参照)。保存データ上は今まで通り2つの別キーとして持つ。
  const getGroupAction = (category, group) => {
    const values = group.patterns.map((pattern) => profile.patternAction[`${category}|${pattern}`]);
    return values.every((v) => v === values[0]) ? values[0] : null;
  };

  const setGroupAction = (category, group, action) => {
    setProfile((p) => {
      const patternAction = { ...p.patternAction };
      group.patterns.forEach((pattern) => {
        patternAction[`${category}|${pattern}`] = action;
      });
      return { ...p, patternAction };
    });
  };

  // 「このカテゴリのアンチコメント全体」は、攻撃的な言い方+その他+下の5パターンの
  // すべてに同じ選択をまとめて反映する一括スイッチ(トリガー)。
  // 自分自身の値は持たず、他の行がすべて同じ選択で揃っている時だけ、その選択を表示する。
  const setAllActions = (category, action) => {
    setProfile((p) => {
      const patternAction = { ...p.patternAction, [`${category}|${OTHER_PATTERN}`]: action };
      PATTERNS.forEach((pattern) => {
        patternAction[`${category}|${pattern}`] = action;
      });
      return {
        ...p,
        attackAction: { ...p.attackAction, [category]: action },
        patternAction,
      };
    });
  };

  const getUniformAction = (category) => {
    const values = [
      profile.attackAction[category],
      profile.patternAction[`${category}|${OTHER_PATTERN}`],
      ...PATTERNS.map((pattern) => profile.patternAction[`${category}|${pattern}`]),
    ];
    return values.every((v) => v === values[0]) ? values[0] : null;
  };

  // 「特定の言い回し」内の一括操作(5パターン+その他)。YouTube報告は行ごとに
  // 個別判断してほしいため、ここでまとめて選べるのは「本サイトで非表示」までにとどめる。
  const arePatternsAllRegskip = (category) =>
    PATTERNS.every((pattern) => profile.patternAction[`${category}|${pattern}`] === "hide_regskip") &&
    profile.patternAction[`${category}|${OTHER_PATTERN}`] === "hide_regskip";

  const togglePatternsAll = (category) => {
    const next = arePatternsAllRegskip(category) ? "normal" : "hide_regskip";
    setProfile((p) => {
      const patternAction = { ...p.patternAction, [`${category}|${OTHER_PATTERN}`]: next };
      PATTERNS.forEach((pattern) => {
        patternAction[`${category}|${pattern}`] = next;
      });
      return { ...p, patternAction };
    });
  };

  const buildPayload = () => ({
    attack_action: { ...profile.attackAction },
    pattern_action: {
      ...Object.fromEntries(
        Object.entries(profile.patternAction)
          .filter(([, action]) => action !== "normal")
          .map(([key, action]) => [key, { action, source: "manual" }])
      ),
      ...Object.fromEntries(
        Object.entries(profile.extraPatternAction).filter(([, v]) => v.action !== "normal")
      ),
    },
  });

  const handleSubmit = async () => {
    const payload = buildPayload();
    setSaving(true);
    try {
      if (loggedIn) {
        await api.saveProfile(payload);
        navigate("/home");
      } else {
        localStorage.setItem("pendingProfile", JSON.stringify(payload));
        navigate("/connect");
      }
    } catch (err) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="page">
        <Header showBack />
        <div className="centered-state">読み込み中…</div>
      </div>
    );
  }

  return (
    <div className="page">
      <Header showBack />
      <div className="settings-lead">
        <h1>初期設定</h1>
        <p>
          攻撃的な言い方をされた時、どう扱ってほしいかをカテゴリごとに選んでください。「YouTube上で非表示」は、その言い回し・パターンごとに個別に選べます。あとからいつでも変更できます。
        </p>
        {fromDiagnosis && (
          <p className="diagnosis-banner">
            診断の回答をもとに設定を自動で反映しました。内容を確認し、必要であれば調整してから保存してください。
          </p>
        )}
      </div>

      {CATEGORIES.map((category) => (
        <div className="card category-card" key={category}>
          <div className="category-title">{category}</div>

          <ActionRow
            master
            value={getUniformAction(category)}
            onChange={(v) => setAllActions(category, v)}
            label="このカテゴリのアンチコメント全体(下のすべての項目に反映されます)"
          />
          <p className="master-hint">自分で細かく設定したい場合は、下から個別に選んでください</p>

          <ActionRow
            value={profile.attackAction[category]}
            onChange={(v) => setAttackAction(category, v)}
            label="攻撃的な言い方(侮蔑語・命令形など、はっきりとした攻撃)"
            example={ATTACK_EXAMPLE[category]}
          />

          <details className="pattern-advanced">
            <summary>特定の言い回し(任意)</summary>
            <div className="pattern-list">
              <div className="pattern-note">
                建前・遠回しな言い方の種類ごとに、個別に選べます。強さに関わらず、この言い回し自体が出たら対応したいものがあれば選んでください
              </div>
              <button type="button" className="select-all-btn" onClick={() => togglePatternsAll(category)}>
                {arePatternsAllRegskip(category) ? "すべて解除" : "すべて本サイトで非表示にする"}
              </button>
              {PATTERN_GROUPS.map((group) => {
                const example = group.patterns.map((pattern) => PATTERN_UI[category][pattern][1]).join(" / ");
                return (
                  <ActionRow
                    key={group.key}
                    label={group.label}
                    example={example}
                    value={getGroupAction(category, group)}
                    onChange={(v) => setGroupAction(category, group, v)}
                  />
                );
              })}
              <ActionRow
                value={profile.patternAction[`${category}|${OTHER_PATTERN}`]}
                onChange={(v) => setPatternAction(`${category}|${OTHER_PATTERN}`, v)}
                label="その他(上のパターン以外の批判)"
                example={OTHER_EXAMPLE[category]}
              />
            </div>
          </details>
        </div>
      ))}

      <div className="card category-card">
        <div className="category-title">プライバシー</div>
        <p className="privacy-note">個人情報・特定につながる投稿は自動的に検出し、専用タブに振り分けます。</p>
      </div>

      <div className="footer-bar">
        <button className="btn-primary" style={{ width: "100%" }} disabled={saving} onClick={handleSubmit}>
          {saving ? "保存しています…" : loggedIn ? "この設定を保存する" : "この設定で始める"}
        </button>
      </div>
    </div>
  );
}
