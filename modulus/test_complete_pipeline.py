"""
レグナレ 完全版パイプライン(実データ版)
========================================
悪質被害スコア診断 → カテゴリ初期提案 → 実際のYouTubeコメント取得
→ 5カテゴリ判定 → タブ振り分け、までを一気通貫で実行する。

【実行方法】
1. export GEMINI_API_KEY="発行したAPIキー" (Windowsは $env:GEMINI_API_KEY="...")
2. py test_complete_pipeline.py
"""

from damage_score import SurveyAnswers, diagnose
from embedding_classifier import CommentClassifier
from policy_action import UserPolicy, sort_comments
from youtube_client import YouTubeClient


def run_diagnosis() -> UserPolicy:
    """STEP 1-2: 診断アンケートに回答してもらい、ポリシーの初期提案を作る"""
    print("=" * 70)
    print("STEP 1: 発信スタイル診断")
    print("=" * 70)
    print("7つの質問に、1(あてはまらない)〜5(よくあてはまる)の数字で答えてください。\n")

    def ask(question: str) -> int:
        while True:
            try:
                value = int(input(f"{question} (1-5): ").strip())
                if 1 <= value <= 5:
                    return value
            except ValueError:
                pass
            print("  1から5の数字で入力してください。")

    answers = SurveyAnswers(
        face_exposure=ask("顔出しでの配信や、容姿・服装がはっきり分かる投稿をしているか"),
        private_disclosure=ask("本名・年齢・日常の出来事など、プライベートな情報を開示しているか"),
        living_area=ask("居住地域や活動エリアが推測できる情報を出しているか"),
        criticism=ask("他者の行動や話題に対し、批判的・否定的な意見を発信することがあるか"),
        harsh_language=ask("あえて強い言葉(毒舌・皮肉・煽り)を使うスタイルをとっているか"),
        strong_opinion=ask("賛否が分かれるテーマについて、自分の主張を強く打ち出しているか"),
        style_change=ask("最近、活動の方向性・キャラクターを大きく変える予定があるか"),
    )

    result = diagnose(answers)

    print("\n" + "=" * 70)
    print("STEP 2: 診断結果")
    print("=" * 70)
    print(f"悪質被害リスク: {result.risk_level}(確率 {result.probability * 100:.1f}%)")
    print(f"{result.dominant_factor_label}")
    print(f"※ {result.disclaimer}")
    print(f"\n初期提案カテゴリ: {result.suggested_categories}")

    # 実際のUIではここでユーザーがチェックを調整するが、今回は簡易確認のみ
    print("\nこのカテゴリを「見たくない」設定として確定します。")
    confirm = input("よろしいですか？(y/n、nの場合は自分でカテゴリを選び直せます): ").strip().lower()

    if confirm == "y":
        selected_categories = list(result.suggested_categories)
    else:
        from embedding_classifier import CATEGORIES
        print(f"\n選択できるカテゴリ: {CATEGORIES}")
        raw = input("見たくないカテゴリをカンマ区切りで入力してください: ").strip()
        selected_categories = [c.strip() for c in raw.split(",") if c.strip()]

    policy = UserPolicy(user_id="local_test_user", selected_categories=selected_categories)
    print(f"\n確定したポリシー: {policy.selected_categories}")
    return policy


def run_comment_pipeline(policy: UserPolicy) -> None:
    """STEP 3-5: YouTubeコメント取得 → 判定 → タブ振り分け"""
    print("\n" + "=" * 70)
    print("STEP 3: YouTubeとの連携")
    print("=" * 70)

    client = YouTubeClient(credentials_path="client_secret.json")
    client.authenticate()
    print("認証に成功しました。")

    video_id = input("\n判定したい動画のIDを入力してください: ").strip()
    raw_comments = client.fetch_comments(video_id, max_results=20)

    if not raw_comments:
        print("コメントが取得できませんでした。")
        return

    print(f"\n{len(raw_comments)}件のコメントを取得しました。")

    print("\n" + "=" * 70)
    print("STEP 4: 5カテゴリ判定")
    print("=" * 70)
    classifier = CommentClassifier()
    classifier.fit()

    classification_results = [classifier.classify(c.text) for c in raw_comments]

    print("\n" + "=" * 70)
    print("STEP 5: タブ振り分け")
    print("=" * 70)
    tabs = sort_comments(classification_results, policy)

    for tab_name, comments in tabs.items():
        print(f"\n【{tab_name}タブ】 ({len(comments)}件)")
        for c in comments:
            print(f"  ・「{c.comment[:50]}」")
            print(f"     カテゴリ: {c.category} / 類似度: {c.similarity}")
            if tab_name == "確認待ち":
                answer = input("     → これは見たくないコメントですか？(y/n): ").strip().lower()
                if answer == "y" and c.category not in policy.selected_categories:
                    policy.selected_categories.append(c.category)
                    print(f"     → ポリシーを更新しました: {policy.selected_categories}")

    print("\n" + "=" * 70)
    print("完了しました。")
    print("=" * 70)
    print(f"最終的なポリシー: {policy.selected_categories}")


if __name__ == "__main__":
    policy = run_diagnosis()
    run_comment_pipeline(policy)
