import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "../components/Header.jsx";
import diagnosticData from "../diagnosticData.json";
import { CATEGORIES } from "../patternData.js";

// アプリを開いた時の導線: splash(起動エフェクト)→ explain(ツール説明)→ start(診断の開始画面)→ step1(攻撃分類の複数選択)→ quiz(IES-6)→ results
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

// ---- 「Regskip 初期診断アセスメントおよびAI非表示制御ロジックの設計」に基づく診断(v3) ----
// STEP1: James & Preeth (2023)をもとにした8つの攻撃分類から、受けた経験のあるものを複数選択。
// STEP2: 選択した項目ごとにIES-6(6項目簡易版, 侵入2・回避2・過覚醒2)で直近7日間の状態を測定。
const STEP1_ITEMS = diagnosticData.step1_items;
const IESR6_SCALE = diagnosticData.iesr6_scale;
const IESR6_QUESTIONS = diagnosticData.iesr6_questions;

// カテゴリ別IES-6スコア(0〜24点、修正済みの3段階)。YouTube上への実際の非表示APIの実行は、
// このスコアだけでは行わない。「YouTube上で非表示」タブに振り分けるところまでで、実際に
// YouTube側を操作するのは既存の「実行する」ボタン+確認ダイアログでの人の操作を必須にする。
function scoreToAction(total) {
  if (total <= 5) return "normal";
  if (total <= 14) return "hide_regskip";
  return "hide_youtube";
}
const SCORE_BAND_LABEL = { normal: "低ストレス(スルー可能)", hide_regskip: "中ストレス(不快・引きずる)", hide_youtube: "高ストレス(トラウマ・休止リスク)" };
// 侵入・回避・過覚醒という言葉だけでは伝わらないため、結果画面の最初に1回だけ説明を出す。
const SUBSCALE_INFO = [
  {
    key: "侵入",
    en: "Intrusion",
    color: "var(--warm)",
    short: "ネガティブなコメントが心の中に入り込み、考え続けてしまう傾向があります。",
    lead: "「思い出したくないのに、勝手に思い出してしまう」状態です。",
    points: ["そのコメントについて、考えていないのに、ふと考えてしまう", "そのときの場面が、頭の中に映像として浮かんでくる", "何か別のことがきっかけで、突然そのコメントを思い出してしまう"],
    summary: "記憶が、自分の意思とは関係なく「侵入」してくるイメージです。",
  },
  {
    key: "回避",
    en: "Avoidance",
    color: "var(--accent)",
    short: "つらいコメントを見ないようにしたり、投稿を控えたりする傾向があります。",
    lead: "「思い出さないように、自分から避けようとする」状態です。",
    points: ["そのコメントを思い出させるもの(似たような言葉、その動画のコメント欄など)を避ける", "あえて考えないようにする", "そのことについて話さないようにする"],
    summary: "辛い記憶から、自分を「守ろう」として距離を取るイメージです。",
  },
  {
    key: "過覚醒",
    en: "Hyperarousal",
    color: "var(--violet)",
    short: "常に警戒し、疲れやすく、小さなことでも強く反応してしまう傾向があります。",
    lead: "「神経が張り詰めて、警戒モードになっている」状態です。",
    points: ["イライラしたり、怒りっぽくなったりする", "集中するのが難しい", "眠れない、または眠り続けられない"],
    summary: "常に気を張っていて、休めていないイメージです。",
  },
];

function SubscaleLegend() {
  return (
    <details className="card diagnosis-result-card diagnosis-legend-teaser">
      <summary>
        <span className="diagnosis-legend-teaser-text">
          <span className="diagnosis-legend-teaser-icon">ⓘ</span>
          <span>
            <span className="diagnosis-result-item">「侵入・回避・過覚醒」とは?</span>
            <br />
            <span className="diagnosis-result-votes">詳しい内容や、それぞれの特徴について知りたい方はこちらから確認できます。</span>
          </span>
        </span>
        <span className="diagnosis-legend-teaser-more">詳しく見る ›</span>
      </summary>
      <div className="diagnosis-result-list" style={{ marginTop: "0.8rem" }}>
        {SUBSCALE_INFO.map((s) => (
          <div key={s.key}>
            <p className="diagnosis-result-item">
              {s.key}({s.en})
            </p>
            <p className="diagnosis-legend-lead">{s.lead}</p>
            <ul className="diagnosis-example-list" style={{ margin: "0.4rem 0" }}>
              {s.points.map((p) => (
                <li key={p} style={{ color: "var(--text-soft)" }}>
                  {p}
                </li>
              ))}
            </ul>
            <p className="diagnosis-result-reason">→ {s.summary}</p>
          </div>
        ))}
        <p className="diagnosis-result-votes">
          それぞれ2つの質問(0〜4点)の合計で、0〜8点。点数が高いほど、その反応が強く出ていることを示します。
        </p>
      </div>
    </details>
  );
}

const ACTION_RANK = { normal: 0, hide_regskip: 1, hide_youtube: 2 };
// 同じattack_action[category]に複数のSTEP1項目(カテゴリ項目+脅迫)が結果を書き込む場合、
// 後から処理した方が上書きしてしまわないよう、より強い(範囲の広い)方を採用する。
function strongerAction(a, b) {
  if (!a) return b;
  return ACTION_RANK[b] > ACTION_RANK[a] ? b : a;
}

const SEVERITY_DESC = {
  normal: "特に大きな負担なく受け止められている",
  hide_regskip: "受け止めやすく、心理的な負担を感じやすい",
  hide_youtube: "強く受け止めやすく、心理的な負担を感じやすい",
};

// 実際に初期設定に反映される内容を、項目の種類ごとに文章にする。
function appliedSettingText(item, action) {
  if (action === "normal") return `「${item.label}」については、特に設定を変更していません。`;
  const actionLabel = action === "hide_youtube" ? "YouTube上で非表示" : "本サイトで非表示";
  if (item.mapping.type === "category") {
    return `「${item.mapping.category}」に関する攻撃的なコメントを${actionLabel}にします。`;
  }
  if (item.mapping.type === "pattern") {
    return `「${item.mapping.patterns.join("・")}」の言い回しのコメントを、カテゴリを問わず${actionLabel}にします。`;
  }
  return `暴言・活動停止要求を含むコメントを、カテゴリを問わず${actionLabel}にします。`;
}

function ScoreGauge({ total, action }) {
  const pct = Math.min(100, Math.round((total / 24) * 100));
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - pct / 100);
  const color = action === "hide_youtube" ? "var(--warm)" : action === "hide_regskip" ? "var(--accent)" : "var(--text-faint)";
  return (
    <svg viewBox="0 0 100 100" className="diagnosis-gauge">
      <circle cx="50" cy="50" r={radius} fill="none" stroke="var(--border-soft)" strokeWidth="10" />
      <circle
        cx="50"
        cy="50"
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth="10"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform="rotate(-90 50 50)"
      />
      <text x="50" y="47" textAnchor="middle" fontSize="24" fontWeight="900" fill="var(--text)">
        {total}
      </text>
      <text x="50" y="64" textAnchor="middle" fontSize="10" fill="var(--text-faint)">
        / 24
      </text>
    </svg>
  );
}

function StressMeter({ total }) {
  const pct = Math.min(100, Math.round((total / 24) * 100));
  return (
    <div className="diagnosis-stress-meter">
      <p className="diagnosis-summary-label">ストレスレベルの目安</p>
      <div className="diagnosis-stress-track">
        <div className="diagnosis-stress-dot" style={{ left: `${pct}%` }} />
      </div>
      <div className="diagnosis-stress-scale">
        <span>0(低)</span>
        <span>{total}(現在)</span>
        <span>24(高)</span>
      </div>
    </div>
  );
}

function StepDots({ step }) {
  return (
    <div className="result-step-dots">
      {[1, 2].map((n) => (
        <span key={n} className={`result-step-dot${step === n ? " active" : step > n ? " done" : ""}`}>
          {n}
        </span>
      ))}
    </div>
  );
}

// STEP1: 攻撃分類の複数選択画面。
const STEP1_GROUPS = [
  { key: "category", title: "何について言われたか", items: STEP1_ITEMS.filter((it) => it.group === "category") },
  { key: "context", title: "どんな言い方をされたか", items: STEP1_ITEMS.filter((it) => it.group === "context") },
];

function Step1Select({ selected, onToggle, onNext }) {
  return (
    <div className="page">
      <Header />
      <div className="settings-lead">
        <StepDots step={1} />
        <h1>受けたことのあるコメント</h1>
        <p>過去に受けたことのあるコメントの分類を、当てはまるものすべて選んでください。</p>
      </div>
      {STEP1_GROUPS.map((group) => (
        <div className="card" key={group.key}>
          <p className="diagnosis-summary-label" style={{ marginBottom: "0.6rem" }}>
            {group.title}
          </p>
          <div className="combo-checklist">
            {group.items.map((item) => (
              <label className="combo-checkbox" key={item.id}>
                <input type="checkbox" checked={selected.has(item.id)} onChange={() => onToggle(item.id)} />
                <span>
                  {item.label}
                  <span className="diagnosis-result-votes"> (例:「{item.examples[0]}」)</span>
                </span>
              </label>
            ))}
          </div>
        </div>
      ))}
      <div className="footer-bar">
        <button className="btn-primary" style={{ width: "100%" }} onClick={onNext}>
          次へ
        </button>
      </div>
    </div>
  );
}

function Transition({ itemLabel, onNext }) {
  return (
    <div className="page">
      <Header />
      <div className="card intro chapter-break">
        <p className="chapter-break-done">ここまでのご回答、ありがとうございます</p>
        <h1>ここからは「{itemLabel}」について、直近7日間の状態を教えてください</h1>
        <button className="btn-primary" onClick={onNext}>
          次へ
        </button>
      </div>
    </div>
  );
}

// 診断結果画面: 選んだ攻撃分類ごとに、IES-6のスコアと反映結果を一覧で見せる。
const RESULT_GROUP_TITLES = { category: "何について言われたか", context: "どんな言い方をされたか" };

function ResultItemCard({ item, total, action, subscales }) {
  return (
    <div className="card diagnosis-result-card" key={item.id}>
      <div className="diagnosis-result-top">
        <div className="diagnosis-gauge-wrap">
          <ScoreGauge total={total} action={action} />
          <p className={`diagnosis-band-label diagnosis-band-label--${action}`}>{SCORE_BAND_LABEL[action]}</p>
        </div>
        <div className="diagnosis-result-summary-block">
          <span className="diagnosis-summary-tagpill">診断結果</span>
          <p className="diagnosis-result-item" style={{ fontSize: "1rem", marginTop: "0.3rem" }}>
            {item.label}
          </p>
          <p className="diagnosis-result-reason" style={{ marginTop: "0.3rem" }}>
            あなたは、「{item.label}」に関するコメントを{SEVERITY_DESC[action]}状態です。
          </p>
        </div>
        <StressMeter total={total} />
      </div>

      <div className="diagnosis-result-breakdown">
        <p className="diagnosis-summary-label">負担の内訳</p>
        <p className="diagnosis-result-votes" style={{ marginBottom: "0.5rem" }}>
          3つの観点から、あなたの負担の強さを測定しました。
        </p>
        <div className="diagnosis-result-list">
          {SUBSCALE_INFO.map((s) => (
            <div className="diagnosis-subscale-row" key={s.key}>
              <div className="diagnosis-subscale-body">
                <p className="diagnosis-result-item">
                  <span className="diagnosis-subscale-dot" style={{ background: s.color }} />
                  {s.key}
                </p>
                <p className="diagnosis-result-votes">{s.short}</p>
                <div className="diagnosis-subscale-bar">
                  <div
                    className="diagnosis-subscale-bar-fill"
                    style={{ width: `${(subscales[s.key] / 8) * 100}%`, background: s.color }}
                  />
                </div>
              </div>
              <span className="diagnosis-subscale-score">{subscales[s.key]} / 8</span>
            </div>
          ))}
        </div>
      </div>

      <div className="diagnosis-recommend">
        <p className="diagnosis-summary-label">初期設定への反映</p>
        <p className="diagnosis-result-votes" style={{ margin: "0.2rem 0 0.6rem" }}>
          あなたの状態に合わせて、以下の設定に反映しました。この設定はいつでも変更できます。
        </p>
        <div className="diagnosis-applied-row">
          <span className="diagnosis-applied-check">{action === "normal" ? "−" : "✓"}</span>
          <span className="diagnosis-result-reason">{appliedSettingText(item, action)}</span>
        </div>
      </div>
    </div>
  );
}

function DiagnosisResults({ itemResults, onNext }) {
  const sorted = [...itemResults].sort((a, b) => b.total - a.total);
  const top = sorted[0];
  const byGroup = { category: sorted.filter((r) => r.item.group === "category"), context: sorted.filter((r) => r.item.group === "context") };
  return (
    <div className="page">
      <Header />
      <div className="settings-lead">
        <h1>診断結果</h1>
        <p className="diagnosis-summary-lead">
          {top ? (
            <>あなたは「{top.item.label}」で、特に心理的な負担を感じているようです</>
          ) : (
            "今回は、特に負担を感じているコメントの分類は見つかりませんでした。"
          )}
        </p>
      </div>

      <SubscaleLegend />

      {["category", "context"].map((groupKey) =>
        byGroup[groupKey].length > 0 ? (
          <div key={groupKey}>
            <p className="diagnosis-summary-label" style={{ margin: "0.2rem 0 0.6rem" }}>
              {RESULT_GROUP_TITLES[groupKey]}
            </p>
            {byGroup[groupKey].map((r) => (
              <ResultItemCard key={r.item.id} {...r} />
            ))}
          </div>
        ) : null
      )}

      <div className="footer-bar">
        <button className="btn-primary" style={{ width: "100%" }} onClick={onNext}>
          初期設定に進む
        </button>
      </div>
    </div>
  );
}

export default function Diagnosis() {
  const navigate = useNavigate();
  const [stage, setStage] = useState("splash"); // splash | explain | start | step1 | transition | quiz | results
  const [selectedStep1, setSelectedStep1] = useState(() => new Set());
  const [queue, setQueue] = useState([]); // {itemId, subIndex, text, subscale}[]
  const [qIndex, setQIndex] = useState(0);
  const [answers, setAnswers] = useState({}); // `${itemId}|${subIndex}` -> 0〜4
  const [resultsData, setResultsData] = useState(null);

  const toggleStep1 = (id) => {
    setSelectedStep1((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const finish = (finalAnswers, selectedIds = selectedStep1) => {
    const attack_action = {};
    const pattern_action = {};
    const itemResults = [];

    STEP1_ITEMS.forEach((item) => {
      if (!selectedIds.has(item.id)) return;
      let total = 0;
      const subVals = { 侵入: [], 回避: [], 過覚醒: [] };
      IESR6_QUESTIONS.forEach((q, i) => {
        const value = finalAnswers[`${item.id}|${i}`] ?? 0;
        total += value;
        subVals[q.subscale].push(value);
      });
      const action = scoreToAction(total);
      const sum = (arr) => arr.reduce((a, b) => a + b, 0);
      itemResults.push({
        item,
        total,
        action,
        subscales: { 侵入: sum(subVals.侵入), 回避: sum(subVals.回避), 過覚醒: sum(subVals.過覚醒) },
      });

      if (item.mapping.type === "category") {
        const category = item.mapping.category;
        attack_action[category] = strongerAction(attack_action[category], action);
        pattern_action[`${category}|該当なし`] = { action, source: "diagnosis" };
      } else if (item.mapping.type === "pattern") {
        item.mapping.patterns.forEach((pattern) => {
          CATEGORIES.forEach((category) => {
            pattern_action[`${category}|${pattern}`] = { action, source: "diagnosis" };
          });
        });
      } else if (item.mapping.type === "attack_all") {
        // 脅迫・プライベート侵入: 特定のカテゴリに紐づかないため、「攻撃的な言い方」
        // (LEVEL_2相当)として全カテゴリのattack_actionに共通で反映する。
        CATEGORIES.forEach((category) => {
          attack_action[category] = strongerAction(attack_action[category], action);
        });
      }
    });

    localStorage.setItem("diagnosisProfile", JSON.stringify({ attack_action, pattern_action, warnings: {} }));
    setResultsData(itemResults);
    setStage("results");
  };

  const confirmStep1 = () => {
    const selectedItems = STEP1_ITEMS.filter((it) => selectedStep1.has(it.id));
    if (selectedItems.length === 0) {
      finish({});
      return;
    }
    const newQueue = [];
    selectedItems.forEach((item) => {
      IESR6_QUESTIONS.forEach((q, i) => newQueue.push({ itemId: item.id, subIndex: i, text: q.text, subscale: q.subscale }));
    });
    setQueue(newQueue);
    setQIndex(0);
    setStage("transition");
  };

  const currentQ = queue[qIndex];
  const currentItem = currentQ ? STEP1_ITEMS.find((it) => it.id === currentQ.itemId) : null;
  const currentItemLabel = currentItem ? currentItem.label : "";

  const handleAnswer = (value) => {
    const key = `${currentQ.itemId}|${currentQ.subIndex}`;
    const nextAnswers = { ...answers, [key]: value };
    setAnswers(nextAnswers);
    if (qIndex + 1 >= queue.length) {
      finish(nextAnswers);
      return;
    }
    const nextIndex = qIndex + 1;
    setQIndex(nextIndex);
    setStage(queue[nextIndex].itemId !== currentQ.itemId ? "transition" : "quiz");
  };

  const goBack = () => {
    if (qIndex === 0) {
      setStage("step1");
      return;
    }
    setQIndex(qIndex - 1);
    setStage("quiz");
  };

  // 開発中の確認用: 手で押さなくても、ランダムな回答で結果画面をすぐ見られるようにする
  // ショートカット。本番ビルドには含めない。
  const handleSampleFill = () => {
    const sampleSelected = new Set(STEP1_ITEMS.filter(() => Math.random() < 0.5).map((it) => it.id));
    if (sampleSelected.size === 0) sampleSelected.add(STEP1_ITEMS[0].id);
    const sampleAnswers = {};
    sampleSelected.forEach((id) => {
      IESR6_QUESTIONS.forEach((_, i) => {
        sampleAnswers[`${id}|${i}`] = Math.floor(Math.random() * 5);
      });
    });
    setSelectedStep1(sampleSelected);
    finish(sampleAnswers, sampleSelected);
  };

  // 開発中の確認用: URLに ?sample=1 を付けて開くと、起動エフェクトも含め質問を一切飛ばして
  // サンプル回答の結果画面まで直接ジャンプする。本番ビルドには含めない。
  useEffect(() => {
    if (import.meta.env.DEV && new URLSearchParams(window.location.search).has("sample")) {
      handleSampleFill();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
            過去に受けたことのあるコメントの種類と、それぞれについてのそのときのご自身の状態を、いくつか質問に答えるだけで、
            あなた専用の「見たくないコメント」の基準が自動でできあがります。
          </p>
          <button className="btn-primary" onClick={() => setStage("step1")}>
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

  if (stage === "step1") {
    return <Step1Select selected={selectedStep1} onToggle={toggleStep1} onNext={confirmStep1} />;
  }

  if (stage === "transition") {
    return <Transition itemLabel={currentItemLabel} onNext={() => setStage("quiz")} />;
  }

  if (stage === "results") {
    return <DiagnosisResults itemResults={resultsData} onNext={() => navigate("/settings")} />;
  }

  return (
    <div className="page">
      <Header showBack onBack={goBack} />
      <div className="settings-lead">
        <StepDots step={2} />
        <h1>「{currentItemLabel}」について</h1>
        <p>選んだ分類を振り返って、過去7日間、以下のようなことがどのくらいありましたか。</p>
      </div>
      <div className="card diagnosis-example-card">
        <p className="diagnosis-summary-label" style={{ marginBottom: "0.4rem" }}>
          こんなコメントを思い出しながら答えてください
        </p>
        <ul className="diagnosis-example-list">
          {currentItem.examples.map((ex) => (
            <li key={ex}>「{ex}」</li>
          ))}
        </ul>
      </div>
      <div className="card">
        <p className="diagnosis-question">「{currentQ.text}」</p>
        <div className="diagnosis-options">
          {IESR6_SCALE.map((opt) => (
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
