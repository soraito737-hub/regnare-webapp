import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "../components/Header.jsx";
import diagnosticData from "../diagnosticData.json";

// アプリを開いた時の導線: splash(起動エフェクト)→ explain(ツール説明)→ start(診断の開始画面)→ quiz(質問)
const SPLASH_VISIBLE_MS = 1300;
const SPLASH_FADE_MS = 400;

function Splash({ onDone }) {
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const fadeTimer = setTimeout(() => setLeaving(true), SPLASH_VISIBLE_MS);
    const doneTimer = setTimeout(onDone, SPLASH_VISIBLE_MS + SPLASH_FADE_MS);
    return () => {
      clearTimeout(fadeTimer);
      clearTimeout(doneTimer);
    };
  }, [onDone]);

  return (
    <div className={`splash-screen${leaving ? " splash-leaving" : ""}`}>
      <p className="splash-logo">Regskip</p>
      <p className="splash-tagline">あなたの発信を、あなたらしく守る。</p>
    </div>
  );
}

const EXPLAIN_POINTS = [
  {
    title: "どんなコメントを見たくないか診断できる",
    body: "いくつかの質問に答えるだけで、あなたが見たくないコメントの基準を診断できます",
  },
  {
    title: "自分だけのコメント欄ができる",
    body: "見たくないコメントの初期設定と、実際のコメントの中から見たくないものを選ぶことで、自分だけのコメント欄を作ることができます",
  },
  {
    title: "見たくないコメントは、見ずに非表示にできる",
    body: "傷つく言い回しのコメントは本文を見なくても非表示にできます。実際に非表示にするかは、あなた自身の操作で決められます",
  },
];

function Explain({ onNext }) {
  return (
    <div className="page">
      <Header />
      <div className="settings-lead">
        <h1>Regskipってどんなツール?</h1>
        <p>
          YouTubeのコメント欄には、応援コメントに混じって、傷つく言い回しのアンチコメントも届きます。
          Regskipは、そうしたコメントを自動で見分けて、あなたの目に触れる前に整理するツールです。
        </p>
      </div>
      <div className="card explain-card">
        {EXPLAIN_POINTS.map((point, i) => (
          <div className="explain-point" key={point.title}>
            <span className="explain-badge">{i + 1}</span>
            <div>
              <p className="explain-point-title">{point.title}</p>
              <p className="explain-point-body">{point.body}</p>
            </div>
          </div>
        ))}
        <button className="btn-primary explain-next" onClick={onNext}>
          次へ
        </button>
      </div>
    </div>
  );
}

const ANSWER_SCALE = diagnosticData.answer_scale;
const ITEMS = diagnosticData.items;
const CATEGORIES = diagnosticData.categories;
const QUESTIONS = diagnosticData.questions;
const OTHER_PATTERN = "該当なし";

const actionOfValue = (value) => ANSWER_SCALE.find((o) => o.value === value).action;
const labelOfValue = (value) => ANSWER_SCALE.find((o) => o.value === value).label;

// 診断結果画面で出す要約(「あなたはこのコメントが見たくないようです」)用の重み付け。
// YouTube上での非表示を選んだ項目ほど、強い拒否反応とみなす。
const ACTION_WEIGHT = { hide_youtube: 2, hide_regskip: 1, normal: 0 };

// 判定理由を診断結果画面でそのまま説明文にできるようにした一覧。
const REASON_TEXT = {
  match: "2回の回答が一致したため、そのまま確定しました",
  protective_auto: "回答に少し差がありましたが、より見たくない方の回答を採用しました",
  weak_downgrade: "回答に差があり、YouTube上の非表示に関わる内容だったため、念のため一段弱めて確定しました",
  tiebreak_majority: "回答が大きく割れたため、3問目を追加で聞き、多数決で決めました",
  unresolved: "3問の回答がすべて割れたため、自動では判定できませんでした。ご自身で設定してください",
};

// 診断結果画面での項目名の表示(初期設定画面の行の言い方に揃える)。
const ITEM_LABEL = {
  基準: "その他の軽い批判",
  他者比較: "他者比較",
  ファン離脱: "ファン離脱",
  疑問形: "疑問形",
  主語肥大化: "主語肥大化",
  自己満足アドバイス: "自己満足アドバイス",
  レベル確認: "攻撃的な言い方(はっきりとした攻撃)",
};

const ACTION_LABEL = { normal: "非表示にしない", hide_regskip: "本サイトで非表示", hide_youtube: "YouTube上で非表示" };

// 診断の項目名 → 実際に保存する設定のキー。
// 「基準」はどの建前パターンにも当てはまらない軽い一言の受け皿(初期設定の「その他」と同じ枠)。
// 「レベル確認」は建前パターンではなく、侮蔑語を含む明確な攻撃への対応(初期設定の「攻撃的な言い方」)。
const ITEM_TO_PATTERN = {
  基準: OTHER_PATTERN,
  他者比較: "他者比較",
  ファン離脱: "ファン離脱",
  疑問形: "疑問形",
  主語肥大化: "主語肥大化",
  自己満足アドバイス: "自己満足アドバイス",
};

// カテゴリ×項目のペア(全28個)。各項目には3つの言い回し(base/retest/tiebreak)がある。
// base・retestの2問の答えがずれた場合に、そのずれ幅に応じてtiebreakまで聞く。
// 同じカテゴリの質問が連続すると「今は容姿否定の話をしている」と気づかれてしまい、
// 回答が実際の感じ方より一貫させよう(または逆に)とバイアスがかかりかねない。
// そのため項目を主軸にして4カテゴリを毎回1周させ、カテゴリが連続しない順番にする。
const ITEM_PAIRS = [];
ITEMS.forEach((item) => {
  CATEGORIES.forEach((category) => {
    ITEM_PAIRS.push({ category, item });
  });
});
const TOTAL_ITEMS = ITEM_PAIRS.length;
// 進捗バー表示用に、質問順を4つの区間に分けるだけの数値(カテゴリとは無関係)
const STAGE_COUNT = 4;
const STAGE_SIZE = TOTAL_ITEMS / STAGE_COUNT;
// 一息つく区切り画面は、何度も出ると煩わしいのでちょうど真ん中の1回だけ出す
const MIDPOINT = TOTAL_ITEMS / 2;

// base(v1)・retest(v2)・(必要な場合のみ)tiebreak(v3)の回答から、この項目の最終的な
// アクションと、手動設定画面に出す警告の強さを決める。
// - 完全一致、またはYouTube上の非表示が絡まない軽いずれ → 自動確定(警告なし)
// - YouTube上の非表示が絡む軽いずれ → 一段弱い「見たくない」を暫定値にして、軽い警告を出す
// - 大きくずれた場合はtiebreakで多数決。多数決が成立しなければ「非表示にしない」に留め、強い警告を出す
function resolveItem(v1, v2, v3) {
  const votes = v3 === undefined ? [v1, v2] : [v1, v2, v3];
  if (v3 === undefined) {
    const diff = Math.abs(v1 - v2);
    if (diff === 0) {
      return { action: actionOfValue(v1), warning: null, reason: "match", votes };
    }
    if (diff <= 2) {
      if (v1 === 1 || v2 === 1) {
        return { action: "hide_regskip", warning: "weak", reason: "weak_downgrade", votes };
      }
      return { action: actionOfValue(Math.min(v1, v2)), warning: null, reason: "protective_auto", votes };
    }
    return { needsTiebreak: true, votes };
  }
  const counts = {};
  votes.forEach((v) => {
    const a = actionOfValue(v);
    counts[a] = (counts[a] || 0) + 1;
  });
  const majority = Object.entries(counts).find(([, c]) => c >= 2);
  if (majority) {
    return { action: majority[0], warning: null, reason: "tiebreak_majority", votes };
  }
  return { action: "normal", warning: "strong", reason: "unresolved", votes };
}

// 診断結果画面: 「初期設定」はあくまで設定を編集する画面なので、そちらには要約を
// 乗せず、診断が終わった直後にこの専用画面で、カテゴリごとの詳しい内訳(各項目の
// 回答・判定結果・判定理由)をすべて見せる。ここを見終わったら初期設定に進む。
function DiagnosisResults({ data, onNext }) {
  const { categoryScores, categoryDetail, topCategory, topExampleText } = data;
  const maxScore = Math.max(1, ...Object.values(categoryScores || {}));
  return (
    <div className="page">
      <Header />
      <div className="settings-lead">
        <h1>診断結果</h1>
        <p className="diagnosis-summary-lead">
          {topCategory ? (
            <>あなたは「{topExampleText}」のようなコメントが見たくないようです。</>
          ) : (
            "今回の診断では、特に見たくないコメントの傾向は見つかりませんでした。"
          )}
        </p>
      </div>

      {CATEGORIES.map((category) => {
        const score = categoryScores?.[category] || 0;
        const pct = Math.round((score / maxScore) * 100);
        return (
          <details className="card diagnosis-result-card" key={category} open>
            <summary className="diagnosis-result-summary">
              <span className="diagnosis-summary-label">{category}</span>
              <div className="diagnosis-summary-bar">
                <div className="diagnosis-summary-bar-fill" style={{ width: `${pct}%` }} />
              </div>
            </summary>
            <div className="diagnosis-result-list">
              {(categoryDetail?.[category] || []).map((row) => (
                <div className="diagnosis-result-row" key={row.item}>
                  <div className="diagnosis-result-row-head">
                    <span className="diagnosis-result-item">{ITEM_LABEL[row.item]}</span>
                    <span
                      className={`action-tag ${row.action === "hide_youtube" ? "action-tag--strong" : row.action === "hide_regskip" ? "action-tag--mid" : "action-tag--normal"}`}
                    >
                      {ACTION_LABEL[row.action]}
                    </span>
                  </div>
                  <p className="diagnosis-result-votes">
                    回答:{row.votes.map((v) => labelOfValue(v)).join(" / ")}
                  </p>
                  <p className="diagnosis-result-reason">{REASON_TEXT[row.reason]}</p>
                </div>
              ))}
            </div>
          </details>
        );
      })}

      <div className="footer-bar">
        <button className="btn-primary" style={{ width: "100%" }} onClick={onNext}>
          初期設定に進む
        </button>
      </div>
    </div>
  );
}

// retest/tiebreakであることは画面上には出さない。「確認のための質問です」と分かると、
// 前の回答に合わせようとする/わざと変えようとするバイアスがかかり、正確な判定ができなくなるため。
// 短い区切り(チャプター)は挟むが、カテゴリ名など内容が推測できる情報は出さない。
function ChapterBreak({ onNext }) {
  return (
    <div className="page">
      <Header />
      <div className="card intro chapter-break">
        <p className="chapter-break-done">ここまでのご回答、ありがとうございます</p>
        <h1>もう少しだけ、質問にお付き合いください</h1>
        <button className="btn-primary" onClick={onNext}>
          次へ
        </button>
      </div>
    </div>
  );
}

export default function Diagnosis() {
  const navigate = useNavigate();
  const [stage, setStage] = useState("splash"); // splash | explain | start | quiz | chapter
  const [itemIndex, setItemIndex] = useState(0);
  const [phase, setPhase] = useState("base"); // base | retest | tiebreak
  const [pending, setPending] = useState({}); // 現在の項目でこれまでに得た回答値
  const [results, setResults] = useState({}); // key -> { action, warning, reason, votes }
  const [resultsData, setResultsData] = useState(null); // 診断結果画面(DiagnosisResults)に渡すデータ

  const currentPair = ITEM_PAIRS[itemIndex];
  const variantIndex = phase === "base" ? 0 : phase === "retest" ? 1 : 2;
  const currentText = currentPair ? QUESTIONS[currentPair.category][currentPair.item][variantIndex] : "";
  const stageIndex = Math.floor(itemIndex / STAGE_SIZE);
  const itemInStage = itemIndex % STAGE_SIZE;

  const finish = (finalResults) => {
    const attack_action = {};
    const pattern_action = {};
    const warnings = {};
    const categoryScores = {};
    // 診断結果画面でカテゴリごとに出す、全項目分の詳しい内訳(回答・判定・理由)。
    const categoryDetail = {};
    // 個々の項目の中で最も拒否反応が強かった1問を、要約の書き出し文に引用する例文にする。
    let topItem = { weight: 0, category: null, text: null };

    CATEGORIES.forEach((category) => {
      let score = 0;
      const detail = [];
      const attackResult = finalResults[`${category}|レベル確認`];
      const attackAction = attackResult?.action || "normal";
      attack_action[category] = attackAction;
      if (attackResult?.warning) warnings[category] = attackResult.warning;
      const attackWeight = ACTION_WEIGHT[attackAction] || 0;
      score += attackWeight;
      detail.push({ item: "レベル確認", ...attackResult, action: attackAction });
      if (attackWeight > topItem.weight) {
        topItem = { weight: attackWeight, category, text: QUESTIONS[category]["レベル確認"][0] };
      }

      ITEMS.forEach((item) => {
        if (item === "レベル確認") return;
        const pattern = ITEM_TO_PATTERN[item];
        const result = finalResults[`${category}|${item}`];
        const action = result?.action || "normal";
        pattern_action[`${category}|${pattern}`] = { action, source: "diagnosis" };
        if (result?.warning) warnings[`${category}|${pattern}`] = result.warning;
        detail.push({ item, ...result, action });
        const weight = ACTION_WEIGHT[action] || 0;
        score += weight;
        if (weight > topItem.weight) {
          topItem = { weight, category, text: QUESTIONS[category][item][0] };
        }
      });

      categoryScores[category] = score;
      categoryDetail[category] = detail;
    });

    localStorage.setItem("diagnosisProfile", JSON.stringify({ attack_action, pattern_action, warnings }));
    setResultsData({
      categoryScores,
      categoryDetail,
      topCategory: topItem.category,
      topExampleText: topItem.text,
    });
    setStage("results");
  };

  const goToNextItem = (finalResults) => {
    setPending({});
    setPhase("base");
    if (itemIndex + 1 < TOTAL_ITEMS) {
      const reachedMidpoint = itemIndex + 1 === MIDPOINT;
      setItemIndex(itemIndex + 1);
      setStage(reachedMidpoint ? "chapter" : "quiz");
    } else {
      finish(finalResults);
    }
  };

  const handleAnswer = (value) => {
    const key = `${currentPair.category}|${currentPair.item}`;
    if (phase === "base") {
      setPending({ v1: value });
      setPhase("retest");
      return;
    }
    if (phase === "retest") {
      const resolved = resolveItem(pending.v1, value);
      if (resolved.needsTiebreak) {
        setPending({ v1: pending.v1, v2: value });
        setPhase("tiebreak");
        return;
      }
      const nextResults = { ...results, [key]: resolved };
      setResults(nextResults);
      goToNextItem(nextResults);
      return;
    }
    // tiebreak
    const resolved = resolveItem(pending.v1, pending.v2, value);
    const nextResults = { ...results, [key]: resolved };
    setResults(nextResults);
    goToNextItem(nextResults);
  };

  // 開発中の確認用: 84問すべてを手で押さなくても、ランダムな回答で結果画面(初期設定の要約)を
  // すぐ見られるようにするショートカット。本番ビルドには含めない。
  const handleSampleFill = () => {
    const sampleResults = {};
    ITEM_PAIRS.forEach(({ category, item }) => {
      const v = Math.floor(Math.random() * 5) + 1;
      sampleResults[`${category}|${item}`] = resolveItem(v, v);
    });
    finish(sampleResults);
  };

  if (stage === "splash") {
    return <Splash onDone={() => setStage("explain")} />;
  }

  if (stage === "explain") {
    return <Explain onNext={() => setStage("start")} />;
  }

  if (stage === "start") {
    return (
      <div className="page">
        <Header showBack onBack={() => setStage("explain")} />
        <div className="card intro">
          <h1>どんなコメントを見たくないか、診断できます</h1>
          <p>
            いくつかのコメント例を見て、自分がもらったらどう感じるかを答えるだけ。
            <br />
            答え終わると、あなた専用の「見たくないコメント」の基準が自動でできあがります。
          </p>
          <button className="btn-primary" onClick={() => setStage("quiz")}>
            はじめる
          </button>
          <button className="btn-later" onClick={() => navigate("/home")}>
            あとでやる
          </button>
          {import.meta.env.DEV && (
            <button className="btn-later" onClick={handleSampleFill}>
              (開発用)サンプル回答で結果画面を見る
            </button>
          )}
        </div>
      </div>
    );
  }

  if (stage === "chapter") {
    return <ChapterBreak onNext={() => setStage("quiz")} />;
  }

  if (stage === "results") {
    return <DiagnosisResults data={resultsData} onNext={() => navigate("/settings")} />;
  }

  // ヘッダーの戻るボタン: retest/tiebreak中はその項目の1つ前の質問へ、
  // 項目の最初の質問(base)にいる時は前の項目のbaseへ(1項目目ならstartへ)戻る。
  const goBack = () => {
    if (phase === "retest") {
      setPhase("base");
      setPending({});
      return;
    }
    if (phase === "tiebreak") {
      setPhase("retest");
      setPending({ v1: pending.v1 });
      return;
    }
    if (itemIndex === 0) {
      setStage("start");
      return;
    }
    setItemIndex(itemIndex - 1);
    setPending({});
    setPhase("base");
  };

  return (
    <div className="page">
      <Header showBack onBack={goBack} />
      <div className="settings-lead">
        <h1>診断</h1>
        <p>
          コメント例を見て、自分がそのコメントをもらったらどう感じるかに一番近いものを選んでください。
          答え終わると、あなた専用の設定が自動でできあがります。
        </p>
      </div>
      <div className="card">
        <div className="diagnosis-progress">
          {Array.from({ length: STAGE_COUNT }).map((_, i) => (
            <div className="diagnosis-progress-segment" key={i}>
              <div
                className="diagnosis-progress-fill"
                style={{
                  width: i < stageIndex ? "100%" : i === stageIndex ? `${Math.round((itemInStage / STAGE_SIZE) * 100)}%` : "0%",
                }}
              />
            </div>
          ))}
        </div>
        <p className="diagnosis-question">「{currentText}」</p>
        <div className="diagnosis-options">
          {ANSWER_SCALE.map((opt) => (
            <button key={opt.value} type="button" className="diagnosis-option" onClick={() => handleAnswer(opt.value)}>
              {opt.label}
            </button>
          ))}
        </div>
        <div className="diagnosis-footer">
          <button type="button" className="diagnosis-skip" onClick={() => navigate("/settings")}>
            診断をスキップして手動で設定する
          </button>
        </div>
      </div>
    </div>
  );
}
