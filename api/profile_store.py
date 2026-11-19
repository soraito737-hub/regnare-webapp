"""
PersonalProfile の永続化。

【既存ロジックとの関係】modulus/personal_profile.py の PersonalProfile 自体には
永続化の仕組みがない(Streamlitのst.session_stateがメモリ上で保持する前提の設計)。
FastAPI はリクエストごとに状態を持たないため、ここで新たに永続化を追加する
(保存先はjson_store.pyが決める)。
personal_profile.py の中身は一切変更していない。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "modulus"))

from personal_classifier import Category, TatemaePattern  # noqa: E402
from json_store import load_json, safe_name, save_json  # noqa: E402
from personal_profile import PatternSetting, PersonalAction, PersonalProfile  # noqa: E402


def _name(user_id: str) -> str:
    return safe_name("personal_profile", user_id)


def load_profile(user_id: str) -> PersonalProfile:
    raw = load_json(_name(user_id))
    if raw is None:
        return PersonalProfile(user_id=user_id)

    # カテゴリ体系の改訂で削除・改名された旧値が保存データに残っていても、
    # 該当分だけ無視して読み込みごと落とさないようにする。
    attack_action = {}
    for k, v in raw.get("attack_action", {}).items():
        try:
            attack_action[Category(k)] = PersonalAction(v)
        except ValueError:
            continue
    pattern_action = {}
    for key, value in raw.get("pattern_action", {}).items():
        cat_value, pat_value = key.split("|", 1)
        try:
            cat_key, pat_key = Category(cat_value), TatemaePattern(pat_value)
        except ValueError:
            continue
        pattern_action[(cat_key, pat_key)] = PatternSetting(
            action=PersonalAction(value["action"]), source=value.get("source", "manual")
        )
    return PersonalProfile(user_id=user_id, attack_action=attack_action, pattern_action=pattern_action)


def save_profile(profile: PersonalProfile) -> None:
    raw = {
        "attack_action": {k.value: v.value for k, v in profile.attack_action.items()},
        "pattern_action": {
            f"{k[0].value}|{k[1].value}": {"action": v.action.value, "source": v.source}
            for k, v in profile.pattern_action.items()
        },
    }
    save_json(_name(profile.user_id), raw)
