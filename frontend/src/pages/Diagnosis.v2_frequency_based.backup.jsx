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

// ---- 「アンチコメント被害測定アンケート(Regskip独自設計)」パート1・パート2A・パート2Bをもとにした診断 ----
// 【重要】パート2B(IES-R)は著作権保護された心理検査(Weiss & Marmar, 1997)。
// 原著者(Daniel Weiss, UCSF)・版元への利用許諾確認が済むまでは社内検証用にとどめ、
// 一般公開しないこと(diagnosticData.jsonの_meta.iesr_license_warningを参照)。
const FREQUENCY_SCALE = diagnosticData.frequency_scale;
const K6_SCALE = diagnosticData.k6_scale;
const IESR_SCALE = diagnosticData.iesr_scale;
const CATEGORIES = diagnosticData.categories;
const CATEGORY_LEVEL_Q = diagnosticData.category_level_questions;
const PATTERN_Q = diagnosticData.pattern_questions;
const PATTERN_NAMES = Object.keys(PATTERN_Q);
const K6_QUESTIONS = diagnosticData.k6_questions;
const IESR_QUESTIONS = diagnosticData.iesr_questions;
const IESR_LICENSE_WARNING = diagnosticData._meta.iesr_license_warning;
const OTHER_PATTERN = "該当なし";
const DIRECT_FLAG = "直接的な指摘";

// パート2Bの導入(組み合わせ選択)用。LEVEL_1×建前フラグ区分(パターン5種+直接的な指摘)×カテゴリ
// = 24通り、LEVEL_2単独×カテゴリ = 4通り、合計28通り。カテゴリごとにまとめて表示する。
const IESR_COMBO_GROUPS = CATEGORIES.map((category) => ({
  category,
  combos: [
    ...[...PATTERN_NAMES, DIRECT_FLAG].map((flag) => ({
      id: `${category}|LEVEL_1|${flag}`,
      label: flag,
    })),
    { id: `${category}|LEVEL_2`, label: "はっきりとした攻撃(LEVEL_2)" },
  ],
}));

// 頻度(0〜4)を実際の非表示設定に変換する。YouTube上の非表示は、自己申告のアンケートだけを
// 根拠に自動選択することは絶対にしない(実際のコメントへの人の直接操作でのみ到達させる方針)。
// 月1回程度(2)以上の頻度があれば、本サイトでの非表示を暫定的に選んでおく。
function freqToAction(value) {
  return value >= 2 ? "hide_regskip" : "normal";
}

// カテゴリ×レベル(8項目)→ パターン(5項目、カテゴリを問わず1回だけ質問)の順で並べ、
// そのあとにK6(6項目)を続ける。質問順はアンケート原本のパート1→パート2Aの構成に合わせている。
const FLAT_ITEMS = [];
CATEGORIES.forEach((category) => {
  ["LEVEL_1", "LEVEL_2"].forEach((level) => {
    FLAT_ITEMS.push({ type: "category_level", category, level, text: CATEGORY_LEVEL_Q[category][level] });
  });
});
PATTERN_NAMES.forEach((pattern) => {
  FLAT_ITEMS.push({ type: "pattern", pattern, text: PATTERN_Q[pattern] });
});
const PART1_LENGTH = FLAT_ITEMS.length; // 13
K6_QUESTIONS.forEach((text, i) => {
  FLAT_ITEMS.push({ type: "k6", index: i, text });
});
const PART2A_END = FLAT_ITEMS.length; // 19 (この位置の直前でIES-Rの組み合わせ選択画面を挟む)
IESR_QUESTIONS.forEach((q, i) => {
  FLAT_ITEMS.push({ type: "iesr", index: i, text: q.text, subscale: q.subscale });
});
const TOTAL_ITEMS = FLAT_ITEMS.length; // 41

// 結果画面用の短い説明文。カテゴリ名・パターン名から機械的に組み立てるのではなく、
// 読んで意味が通る一言をあらかじめ用意しておく。
const CATEGORY_TYPE_LABEL = {
  容姿否定: "容姿否定型",
  人格否定: "人格否定型",
  社会モラルマナー説教: "説教型",
  活動クオリティ: "活動クオリティ型",
};
const CATEGORY_BLURB = {
  容姿否定: "外見に関する攻撃を受けやすい傾向があります",
  人格否定: "性格や人柄に関する攻撃を受けやすい傾向があります",
  社会モラルマナー説教: "マナーや常識を理由にした説教を受けやすい傾向があります",
  活動クオリティ: "活動の内容や質に関する批判を受けやすい傾向があります",
};
const SHORT_PATTERN_DESC = {
  他者比較: "他の配信者と比べて貶められる",
  ファン離脱: "「もう見ない」などの匂わせ",
  疑問形: "遠回しに批判される問いかけ",
  褒め殺し型: "皮肉っぽい褒め言葉",
  主語肥大化: "「みんな」を主語にされる",
  直接的な指摘: "遠回しでなく直接指摘される",
};

// K6の合計点(0〜24)のカットオフ基準。臨床的な言い切りを避け、寄り添う言い方に調整している。
function k6Band(total) {
  if (total >= 13) {
    return {
      label: "かなり負担がかかっているようです",
      note: "つらさが強く出ているサインかもしれません。信頼できる人や専門家に相談することも考えてみてください。",
    };
  }
  if (total >= 10) {
    return { label: "負担が続いているようです", note: "気分や不安の面で、しんどさが続いている可能性があります。無理をしすぎないようにしてください。" };
  }
  if (total >= 5) {
    return { label: "ストレスを感じているようです", note: "ここ最近、心理的な負担が出てきている可能性があります。" };
  }
  return { label: "大きな負担は見られません", note: "" };
}

function ChapterBreak({ title, onNext }) {
  return (
    <div className="page">
      <Header />
      <div className="card intro chapter-break">
        <p className="chapter-break-done">ここまでのご回答、ありがとうございます</p>
        <h1>{title}</h1>
        <button className="btn-primary" onClick={onNext}>
          次へ
        </button>
      </div>
    </div>
  );
}

// パート2Bの導入: 辛いと感じたコメントの組み合わせを複数選択してもらう。
// この後に続く22問のIES-Rは、ここで選んだ組み合わせ全体を振り返って1回だけ回答してもらう。
function ComboSelect({ selected, onToggle, onNext }) {
  return (
    <div className="page">
      <Header />
      <div className="settings-lead">
        <h1>辛いと感じたコメント</h1>
        <p>
          あなたが辛いと感じたコメントについて、以下の組み合わせから当てはまるものをいくつでも選んでください。
          特になければ、選ばずに次へ進んでいただいて構いません。
        </p>
      </div>
      {IESR_COMBO_GROUPS.map((group) => (
        <div className="card diagnosis-result-card" key={group.category}>
          <p className="diagnosis-summary-label" style={{ marginBottom: "0.6rem" }}>
            {group.category}
          </p>
          <div className="combo-checklist">
            {group.combos.map((combo) => (
              <label className="combo-checkbox" key={combo.id}>
                <input type="checkbox" checked={selected.has(combo.id)} onChange={() => onToggle(combo.id)} />
                {combo.label}
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

const RESULT_STEP_TITLES = ["あなたのアンチコメント傾向", "苦手な言い方", "心理的な影響", "あなたの結果まとめ"];

// 診断結果画面: 「初期設定」はあくまで設定を編集する画面なので、そちらには要約を
// 乗せず、診断が終わった直後にこの専用画面で4ステップに分けて見せる。
// ここを見終わったら初期設定に進む。
function DiagnosisResults({ data, onNext }) {
  const [step, setStep] = useState(0);
  const {
    categoryLevelScores, topCategory, topCategoryInfo, topPattern,
    topCombos, k6Total, k6ItemScores, k6BandInfo, comboCount, iesrTotal, iesrSubscales,
  } = data;
  const maxCategoryScore = Math.max(1, ...CATEGORIES.map((c) => categoryLevelScores[c].total));

  return (
    <div className="page">
      <Header showBack={step > 0} onBack={() => setStep(step - 1)} />
      <div className="settings-lead">
        <div className="result-step-dots">
          {RESULT_STEP_TITLES.map((title, i) => (
            <span key={title} className={`result-step-dot${i === step ? " active" : i < step ? " done" : ""}`}>
              {i + 1}
            </span>
          ))}
        </div>
        <h1>{RESULT_STEP_TITLES[step]}</h1>
      </div>

      {step === 0 && (
        <>
          <div className="card diagnosis-result-card">
            <p className="diagnosis-summary-lead">
              あなたは「{CATEGORY_TYPE_LABEL[topCategory]}」が多めです
            </p>
            <p className="diagnosis-result-reason">{CATEGORY_BLURB[topCategory]}</p>
          </div>
          <div className="card diagnosis-result-card">
            <p className="diagnosis-summary-label" style={{ marginBottom: "0.7rem" }}>
              カテゴリ別の強さ
            </p>
            <div className="diagnosis-result-list">
              {CATEGORIES.map((category) => {
                const score = categoryLevelScores[category].total;
                const pct = Math.round((score / maxCategoryScore) * 100);
                return (
                  <div className="diagnosis-result-row" key={category}>
                    <div className="diagnosis-result-row-head">
                      <span className="diagnosis-result-item">{category}</span>
                      <span className="action-tag action-tag--mid">{score} / 8</span>
                    </div>
                    <div className="diagnosis-summary-bar">
                      <div className="diagnosis-summary-bar-fill" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="card diagnosis-result-card">
            <p className="diagnosis-summary-label">ポイント</p>
            <p className="diagnosis-result-reason">
              {topCategory}のスコアが最も高く、特に{topCategoryInfo.l2Value > topCategoryInfo.l1Value ? "侮蔑語を含む攻撃的なコメント" : "遠回し・控えめな指摘"}
              を受けやすい傾向が見られます。
            </p>
          </div>
        </>
      )}

      {step === 1 && (
        <>
          <div className="card diagnosis-result-card">
            <p className="diagnosis-summary-lead">この言われ方が、特に苦手なようです</p>
            <p className="diagnosis-result-reason">
              頻度の多さではなく、あなたが「辛い」と選んだ組み合わせをもとにしています。
            </p>
          </div>
          {topCombos.length > 0 ? (
            <div className="card diagnosis-result-card">
              <div className="diagnosis-result-list">
                {topCombos.map((c, i) => (
                  <p className="diagnosis-result-reason" key={i}>
                    {i + 1}. {c.category}×{c.flag}
                    {c.desc && <span className="diagnosis-result-votes">({c.desc})</span>}
                  </p>
                ))}
              </div>
            </div>
          ) : (
            <div className="card diagnosis-result-card">
              <p className="diagnosis-result-reason">特に苦手だと選んだ組み合わせはありませんでした。</p>
            </div>
          )}
        </>
      )}

      {step === 2 && (
        <>
          <div className="card diagnosis-result-card">
            <p className="diagnosis-summary-label">全般的な心理的ストレス(K6)</p>
            <p className="diagnosis-k6-score">{k6Total} / 24</p>
            <p className="diagnosis-result-item">{k6BandInfo.label}</p>
            {k6BandInfo.note && <p className="diagnosis-result-reason">{k6BandInfo.note}</p>}
            <details style={{ marginTop: "0.7rem" }}>
              <summary className="diagnosis-summary-label" style={{ cursor: "pointer" }}>
                内訳を見る
              </summary>
              <div className="diagnosis-result-list" style={{ marginTop: "0.6rem" }}>
                {k6ItemScores.map((row) => (
                  <div className="diagnosis-result-row-head" key={row.text}>
                    <span className="diagnosis-result-votes">{row.text}</span>
                    <span className="action-tag action-tag--normal">{row.value}</span>
                  </div>
                ))}
              </div>
            </details>
          </div>
          <div className="card diagnosis-result-card">
            <p className="license-warning">⚠️ {IESR_LICENSE_WARNING}</p>
            <p className="diagnosis-summary-label" style={{ marginTop: "0.8rem" }}>
              コメントによる影響(IES-R)
            </p>
            <p className="diagnosis-k6-score">{iesrTotal} / 88</p>
            <p className="diagnosis-result-votes">しんどいと感じた組み合わせ:{comboCount} / 28</p>
            <div className="diagnosis-result-list" style={{ marginTop: "0.6rem" }}>
              {["侵入", "回避", "過覚醒"].map((sub) => (
                <div className="diagnosis-result-row-head" key={sub}>
                  <span className="diagnosis-result-item">{sub}</span>
                  <span className="action-tag action-tag--mid">{iesrSubscales[sub].toFixed(1)} / 4</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {step === 3 && (
        <>
          <div className="card diagnosis-result-card">
            <p className="diagnosis-summary-lead">
              あなたは「{topCategory}」が中心で「{topPattern}」の形が多い傾向です
            </p>
          </div>
          <div className="card diagnosis-result-card result-summary-chain">
            <div className="result-summary-step">
              <p className="diagnosis-summary-label">アンチコメントの傾向</p>
              <p className="diagnosis-result-item">
                {topCategory}が多め({categoryLevelScores[topCategory].total} / 8)
              </p>
            </div>
            <div className="result-summary-arrow">↓</div>
            <div className="result-summary-step">
              <p className="diagnosis-summary-label">よくある言い方</p>
              <p className="diagnosis-result-item">{topPattern}のパターンが目立つ</p>
            </div>
            <div className="result-summary-arrow">↓</div>
            <div className="result-summary-step">
              <p className="diagnosis-summary-label">心理的な影響</p>
              <p className="diagnosis-result-item">
                {k6BandInfo.label}(K6:{k6Total} / 24、IES-R:{iesrTotal} / 88)
              </p>
            </div>
          </div>
          <div className="card diagnosis-result-card">
            <p className="diagnosis-summary-label">あなたの場合…</p>
            <p className="diagnosis-result-reason">
              「{topCategory}」に関するコメントで、「{topPattern}」のような遠回しな言い方が多い傾向があります。
              この結果をもとに、次の初期設定であなた専用の非表示の基準を作ります。
            </p>
          </div>
        </>
      )}

      <div className="footer-bar diagnosis-result-nav">
        {step > 0 && (
          <button className="btn-secondary" onClick={() => setStep(step - 1)}>
            前へ
          </button>
        )}
        {step < RESULT_STEP_TITLES.length - 1 ? (
          <button className="btn-primary" onClick={() => setStep(step + 1)}>
            次へ
          </button>
        ) : (
          <button className="btn-primary" onClick={onNext}>
            結果を保存して初期設定に進む
          </button>
        )}
      </div>
    </div>
  );
}

export default function Diagnosis() {
  const navigate = useNavigate();
  const [stage, setStage] = useState("splash"); // splash | explain | start | quiz | chapter | combo | results
  const [itemIndex, setItemIndex] = useState(0);
  const [answers, setAnswers] = useState({}); // itemIndex -> 0〜4
  const [selectedCombos, setSelectedCombos] = useState(() => new Set());
  const [resultsData, setResultsData] = useState(null);

  const currentItem = FLAT_ITEMS[itemIndex];
  const scale = currentItem?.type === "k6" ? K6_SCALE : currentItem?.type === "iesr" ? IESR_SCALE : FREQUENCY_SCALE;
  const progress = Math.round((itemIndex / TOTAL_ITEMS) * 100);

  const toggleCombo = (id) => {
    setSelectedCombos((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const finish = (finalAnswers, comboIds = selectedCombos) => {
    const attack_action = {};
    const pattern_action = {};
    const categoryLevelScores = {}; // カテゴリ別の強さ(0〜8点、LEVEL_1+LEVEL_2のみ)

    CATEGORIES.forEach((category) => {
      const l1Idx = FLAT_ITEMS.findIndex((it) => it.type === "category_level" && it.category === category && it.level === "LEVEL_1");
      const l2Idx = FLAT_ITEMS.findIndex((it) => it.type === "category_level" && it.category === category && it.level === "LEVEL_2");
      const l1Value = finalAnswers[l1Idx] ?? 0;
      const l2Value = finalAnswers[l2Idx] ?? 0;
      const l1Action = freqToAction(l1Value);
      const l2Action = freqToAction(l2Value);

      pattern_action[`${category}|${OTHER_PATTERN}`] = { action: l1Action, source: "diagnosis" };
      attack_action[category] = l2Action;
      categoryLevelScores[category] = { total: l1Value + l2Value, l1Value, l2Value };
    });

    const patternDetail = [];
    PATTERN_NAMES.forEach((pattern) => {
      const idx = FLAT_ITEMS.findIndex((it) => it.type === "pattern" && it.pattern === pattern);
      const value = finalAnswers[idx] ?? 0;
      const action = freqToAction(value);
      // パターンはカテゴリを問わず1回だけ聞いているため、同じ回答を4カテゴリすべてに反映する。
      CATEGORIES.forEach((category) => {
        pattern_action[`${category}|${pattern}`] = { action, source: "diagnosis" };
      });
      patternDetail.push({ pattern, value, action, freqLabel: FREQUENCY_SCALE[value].label });
    });

    let k6Total = 0;
    const k6ItemScores = [];
    K6_QUESTIONS.forEach((text, i) => {
      const idx = FLAT_ITEMS.findIndex((it) => it.type === "k6" && it.index === i);
      const value = finalAnswers[idx] ?? 0;
      k6Total += value;
      k6ItemScores.push({ text, value });
    });

    // カテゴリ別の強さ(0〜8点)で最も高いカテゴリを「傾向」として1つ選ぶ。
    const topCategory = CATEGORIES.reduce((best, cat) =>
      categoryLevelScores[cat].total > categoryLevelScores[best].total ? cat : best
    , CATEGORIES[0]);
    const topCategoryInfo = categoryLevelScores[topCategory];

    // 「よくある言い方」: IES-Rの組み合わせ選択(実際に辛かったと選んだもの)を優先し、
    // 選択がなければパート1のパターン頻度が一番高いものにフォールバックする。
    const selectedFlags = {};
    comboIds.forEach((id) => {
      const parts = id.split("|");
      if (parts.length === 3) {
        const flag = parts[2];
        selectedFlags[flag] = (selectedFlags[flag] || 0) + 1;
      }
    });
    let topPattern = Object.entries(selectedFlags).sort((a, b) => b[1] - a[1])[0]?.[0];
    if (!topPattern) {
      topPattern = [...patternDetail].sort((a, b) => b.value - a.value)[0]?.pattern;
    }

    // ②の画面用: 「辛い」と選んだ組み合わせの一覧(頻度ではなく苦手さの自己申告)。
    const topCombos = [...comboIds]
      .map((id) => id.split("|"))
      .filter((parts) => parts.length === 3)
      .slice(0, 3)
      .map(([category, , flag]) => ({ category, flag, desc: SHORT_PATTERN_DESC[flag] || "" }));

    // パート2B(IES-R): 侵入・回避・過覚醒の3下位尺度ごとに平均を、22項目合計を総合点として出す。
    let iesrTotal = 0;
    const subscaleValues = { 侵入: [], 回避: [], 過覚醒: [] };
    FLAT_ITEMS.forEach((item, idx) => {
      if (item.type !== "iesr") return;
      const value = finalAnswers[idx] ?? 0;
      iesrTotal += value;
      subscaleValues[item.subscale].push(value);
    });
    const avg = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0);
    const iesrSubscales = {
      侵入: avg(subscaleValues.侵入),
      回避: avg(subscaleValues.回避),
      過覚醒: avg(subscaleValues.過覚醒),
    };

    localStorage.setItem("diagnosisProfile", JSON.stringify({ attack_action, pattern_action, warnings: {} }));
    setResultsData({
      categoryLevelScores,
      topCategory,
      topCategoryInfo,
      topPattern,
      topCombos,
      k6Total,
      k6ItemScores,
      k6BandInfo: k6Band(k6Total),
      comboCount: comboIds.size,
      iesrTotal,
      iesrSubscales,
    });
    setStage("results");
  };

  const handleAnswer = (value) => {
    const nextAnswers = { ...answers, [itemIndex]: value };
    setAnswers(nextAnswers);
    if (itemIndex + 1 >= TOTAL_ITEMS) {
      finish(nextAnswers);
      return;
    }
    const nextIndex = itemIndex + 1;
    setItemIndex(nextIndex);
    if (nextIndex === PART1_LENGTH) {
      setStage("chapter");
    } else if (nextIndex === PART2A_END) {
      setStage("combo");
    } else {
      setStage("quiz");
    }
  };

  const goBack = () => {
    if (itemIndex === 0) {
      setStage("start");
      return;
    }
    setItemIndex(itemIndex - 1);
    setStage("quiz");
  };

  // 開発中の確認用: 41問すべてを手で押さなくても、ランダムな回答で結果画面をすぐ見られる
  // ようにするショートカット。本番ビルドには含めない。
  const handleSampleFill = () => {
    const sampleAnswers = {};
    FLAT_ITEMS.forEach((_, idx) => {
      sampleAnswers[idx] = Math.floor(Math.random() * 5);
    });
    const allComboIds = IESR_COMBO_GROUPS.flatMap((g) => g.combos.map((c) => c.id));
    const sampleCombos = new Set(allComboIds.filter(() => Math.random() < 0.2));
    setSelectedCombos(sampleCombos);
    finish(sampleAnswers, sampleCombos);
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
            過去に受けたコメントの頻度と、最近のご自身の状態について、いくつか質問に答えるだけ。
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
    return <ChapterBreak title="最後に、最近のご自身の状態について少しだけ聞かせてください" onNext={() => setStage("quiz")} />;
  }

  if (stage === "combo") {
    return <ComboSelect selected={selectedCombos} onToggle={toggleCombo} onNext={() => setStage("quiz")} />;
  }

  if (stage === "results") {
    return <DiagnosisResults data={resultsData} onNext={() => navigate("/settings")} />;
  }

  return (
    <div className="page">
      <Header showBack onBack={goBack} />
      <div className="settings-lead">
        <h1>診断</h1>
        <p>
          {currentItem.type === "k6" &&
            "過去30日の間に、どのくらいの頻度で次のことがありましたか。"}
          {currentItem.type === "iesr" &&
            "選んだ組み合わせ全体を振り返って、過去7日間、以下のようなことがどのくらいありましたか。"}
          {(currentItem.type === "category_level" || currentItem.type === "pattern") &&
            "過去1年間で、以下のようなコメントを受けた経験がありますか。頻度をお答えください。"}
        </p>
      </div>
      <div className="card">
        <div className="diagnosis-progress">
          <div className="diagnosis-progress-segment">
            <div className="diagnosis-progress-fill" style={{ width: `${progress}%` }} />
          </div>
        </div>
        <p className="diagnosis-question">「{currentItem.text}」</p>
        <div className="diagnosis-options">
          {scale.map((opt) => (
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
