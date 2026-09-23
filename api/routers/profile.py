import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "modulus"))

from fastapi import APIRouter, HTTPException, Request  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from personal_classifier import Category, TatemaePattern  # noqa: E402
from personal_profile import PatternSetting, PersonalAction, PersonalProfile  # noqa: E402

from profile_store import load_profile, save_profile  # noqa: E402
from routers.auth import get_credentials  # noqa: E402

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileIn(BaseModel):
    attack_action: dict[str, str]              # {"容姿否定": "hide_regskip", ...}
    pattern_action: dict[str, dict[str, str]]   # {"容姿否定|褒め殺し型": {"action": "hide_regskip"}, ...}


def _profile_to_json(profile: PersonalProfile) -> dict:
    return {
        "attack_action": {k.value: v.value for k, v in profile.attack_action.items()},
        "pattern_action": {
            f"{k[0].value}|{k[1].value}": {"action": v.action.value, "source": v.source}
            for k, v in profile.pattern_action.items()
        },
    }


@router.get("")
def get_profile(request: Request):
    """ログイン済みユーザーの現在の設定を取得する(設定編集画面用)。"""
    channel_id = request.session.get("channel_id")
    if channel_id is None:
        raise HTTPException(status_code=401, detail="ログインしていません")
    profile = load_profile(channel_id)
    return _profile_to_json(profile)


@router.post("")
def post_profile(request: Request, body: ProfileIn):
    """診断・初期設定画面で選んだ内容を保存する。ログイン後に呼ばれる想定。"""
    channel_id = request.session.get("channel_id")
    if channel_id is None:
        raise HTTPException(status_code=401, detail="ログインしていません")

    attack_action = {Category(k): PersonalAction(v) for k, v in body.attack_action.items()}
    pattern_action = {}
    for key, value in body.pattern_action.items():
        cat_value, pat_value = key.split("|", 1)
        pattern_action[(Category(cat_value), TatemaePattern(pat_value))] = PatternSetting(
            action=PersonalAction(value["action"]), source=value.get("source", "manual")
        )

    profile = PersonalProfile(user_id=channel_id, attack_action=attack_action, pattern_action=pattern_action)
    save_profile(profile)
    return {"ok": True}
