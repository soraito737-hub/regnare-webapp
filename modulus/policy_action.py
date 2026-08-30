"""
policy_action.py

ユーザーが選んだ「見たくないカテゴリ」ポリシーに基づいて、
分類済みコメントを3つのタブ（通常 / 気になる / 確認待ち）に振り分ける。

判定ロジック:
- judgement == "非該当"                         -> 通常タブ
- judgement == "該当" かつ category が選択済み    -> 気になるタブ
- judgement == "グレー"                          -> 確認待ちタブ
- judgement == "該当" だが category が未選択      -> 通常タブ（本人が気にしていないカテゴリなので表示）
"""

from dataclasses import dataclass, field


@dataclass
class UserPolicy:
    user_id: str
    selected_categories: list[str] = field(default_factory=list)


def sort_comments(classification_results, policy: UserPolicy) -> dict[str, list]:
    tabs: dict[str, list] = {
        "通常": [],
        "気になる": [],
        "確認待ち": [],
    }

    for result in classification_results:
        judgement = result.judgement

        if judgement == "グレー":
            tabs["確認待ち"].append(result)
        elif judgement == "該当":
            if result.category in policy.selected_categories:
                tabs["気になる"].append(result)
            else:
                tabs["通常"].append(result)
        else:  # "非該当"
            tabs["通常"].append(result)

    return tabs