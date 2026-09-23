"""
PersonalProfile の永続化。

【既存ロジックとの関係】modulus/personal_profile.py の PersonalProfile 自体には
永続化の仕組みがない(Streamlitのst.session_stateがメモリ上で保持する前提の設計)。
FastAPI はリクエストごとに状態を持たないため、ここで新たにJSON永続化を追加する。
personal_profile.py の中身は一切変更していない。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "modulus"))

from personal_classifier import Category, TatemaePattern  # noqa: E402
from personal_profile import PatternSetting, PersonalAction, PersonalProfile  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / "data"


def _path(user_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() else "_" for c in user_id)
    return DATA_DIR / f"personal_profile_{safe_id}.json"


def load_profile(user_id: str) -> PersonalProfile:
    path = _path(user_id)
    if not path.exists():
        return PersonalProfile(user_id=user_id)

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    attack_action = {Category(k): PersonalAction(v) for k, v in raw.get("attack_action", {}).items()}
    pattern_action = {}
    for key, value in raw.get("pattern_action", {}).items():
        cat_value, pat_value = key.split("|", 1)
        pattern_action[(Category(cat_value), TatemaePattern(pat_value))] = PatternSetting(
            action=PersonalAction(value["action"]), source=value.get("source", "manual")
        )
    return PersonalProfile(user_id=user_id, attack_action=attack_action, pattern_action=pattern_action)


def save_profile(profile: PersonalProfile) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = {
        "attack_action": {k.value: v.value for k, v in profile.attack_action.items()},
        "pattern_action": {
            f"{k[0].value}|{k[1].value}": {"action": v.action.value, "source": v.source}
            for k, v in profile.pattern_action.items()
        },
    }
    with open(_path(profile.user_id), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
