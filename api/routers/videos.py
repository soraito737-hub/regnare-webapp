"""
動画一覧・コメント判定・コメントへのアクションのエンドポイント。
webapp/streamlit_app_redesign.py の同名関数と同じロジックを、
FastAPI用に「リクエスト単位」で呼び出せる形にしたもの。
判定ロジック(personal_classifier / personal_profile)自体は一切変更していない。
"""
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "modulus"))

from fastapi import APIRouter, HTTPException, Request  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from personal_classifier import Category, PersonalJudgmentClassifier, SurfaceLevel, TatemaePattern  # noqa: E402
from personal_profile import (  # noqa: E402
    DisplayAction, PersonalAction, PersonalSimilarityList,
    find_flagged_authors, is_emergency, resolve_display_action,
)

from config import GEMINI_API_KEY  # noqa: E402
from profile_store import load_profile  # noqa: E402
from routers.auth import get_credentials  # noqa: E402
from youtube_service import (  # noqa: E402
    MAX_COMMENTS_PER_VIDEO, ban_author_on_youtube, delete_comment_on_youtube,
    fetch_comments, get_video_details, list_channel_videos, reply_to_comment,
)

router = APIRouter(prefix="/api", tags=["videos"])

_classifier: PersonalJudgmentClassifier | None = None
_similarity_list: PersonalSimilarityList | None = None


def get_classifier() -> PersonalJudgmentClassifier:
    global _classifier
    if _classifier is None:
        _classifier = PersonalJudgmentClassifier(api_key=GEMINI_API_KEY)
    return _classifier


def get_similarity_list() -> PersonalSimilarityList:
    global _similarity_list
    if _similarity_list is None:
        _similarity_list = PersonalSimilarityList(api_key=GEMINI_API_KEY)
    return _similarity_list


def _require_session(request: Request):
    credentials = get_credentials(request)
    channel_id = request.session.get("channel_id")
    if credentials is None or channel_id is None:
        raise HTTPException(status_code=401, detail="ログインしていません")
    return credentials, channel_id


def _judgment_to_dict(judgment) -> dict:
    return {
        "categories": [c.value for c in judgment.categories],
        "surface_level": judgment.surface_level.value,
        "tatemae_pattern": judgment.tatemae_pattern.value,
        "emergency": judgment.emergency.value,
    }


@router.get("/videos")
def get_videos(request: Request, page_token: str | None = None):
    credentials, _ = _require_session(request)
    uploads_playlist_id = request.session.get("uploads_playlist_id")
    if uploads_playlist_id is None:
        raise HTTPException(status_code=401, detail="チャンネル情報がありません")
    videos, next_token = list_channel_videos(credentials, uploads_playlist_id, page_token=page_token)
    details = get_video_details(credentials, [v["video_id"] for v in videos])
    for v in videos:
        d = details.get(v["video_id"], {})
        v["view_count"] = d.get("view_count", 0)
        v["comment_count"] = d.get("comment_count", 0)
        v["duration"] = d.get("duration", "")
        v["is_short"] = d.get("is_short", False)
        if d.get("thumbnail"):
            v["thumbnail"] = d["thumbnail"]
    return {"videos": videos, "next_page_token": next_token}


def _process_single_comment(comment: dict, channel_id: str, profile) -> dict:
    judgment = get_classifier().classify(comment["text"])

    matched_mark_index = None
    if is_emergency(judgment):
        display = DisplayAction(hide=True, escalate_to_youtube=False, reason="emergency")
        sim_score = 0.0
        tab = "privacy"
    else:
        similarity_list = get_similarity_list()
        matched_action, sim_score, matched_mark_index = similarity_list.check_similarity(channel_id, comment["text"])
        display = resolve_display_action(judgment, profile, similarity_action=matched_action)
        if not display.hide:
            tab = "new"
        elif display.escalate_to_youtube:
            tab = "hidden_youtube"
        else:
            tab = "hidden_regskip"

    return {
        "comment": comment,
        "judgment": _judgment_to_dict(judgment),
        "similarity_score": sim_score,
        "tab": tab,
        # なぜ非表示になったか(personal_similarity/personal_pattern/attack_level/emergency/none)。
        # 「AIが今の内容だけで判定した」のか「過去に押した判断を覚えて再現した」のかが
        # 画面で分かるように、フロントに渡す。
        "reason": display.reason,
        # reason="personal_similarity"のとき、原因になったマークのindex。
        # 「元に戻す」でこのマークにだけ例外を追記して育てるために使う。
        "matched_mark_index": matched_mark_index,
        # 自動処理(process_video)では実際のYouTube側の非表示APIは一切呼ばないため、
        # tab="hidden_youtube"になったコメントも、この時点ではまだ実行されていない。
        # フロント側でボタンを押して個別に実行するまでFalseのまま。
        "executed_on_youtube": False,
    }


@router.post("/videos/{video_id}/process")
def process_video(video_id: str, request: Request):
    credentials, channel_id = _require_session(request)
    profile = load_profile(channel_id)
    comments = fetch_comments(credentials, video_id, max_results=MAX_COMMENTS_PER_VIDEO)

    results: list[dict | None] = [None] * len(comments)
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_idx = {
            executor.submit(_process_single_comment, c, channel_id, profile): i
            for i, c in enumerate(comments)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception:
                results[idx] = {
                    "comment": comments[idx],
                    "judgment": None,
                    "similarity_score": 0.0,
                    "tab": "hidden_regskip",
                    "reason": "error",
                    "executed_on_youtube": False,
                }

    tabs: dict[str, list] = {"new": [], "hidden_regskip": [], "hidden_youtube": [], "privacy": []}
    for r in results:
        tabs[r["tab"]].append(r)

    flagged_authors = [
        {
            "author_channel_id": a["author_channel_id"],
            "count": a["count"],
            "breakdown": {f"{cat.value}|{pat.value}": count for (cat, pat), count in a["breakdown"].items()},
        }
        for a in find_flagged_authors(channel_id, get_similarity_list())
    ]

    return {"tabs": tabs, "flagged_authors": flagged_authors}


class MarkIn(BaseModel):
    action: str  # "normal" | "hide_regskip" | "hide_youtube"
    text: str
    categories: list[str]
    tatemae_pattern: str
    surface_level: int
    author_channel_id: str | None = None
    note: str | None = None  # ユーザーが書いた「見たくない理由」。類似度マッチの精度向上に使う。
    video_id: str | None = None  # 学習データ管理画面でサムネイルを出すために保持する。


@router.post("/comments/{comment_id}/mark")
def mark_comment(comment_id: str, body: MarkIn, request: Request):
    credentials, channel_id = _require_session(request)
    action = PersonalAction(body.action)
    get_similarity_list().mark_comment(
        user_id=channel_id,
        comment=body.text,
        action=action,
        categories=[Category(c) for c in body.categories] or [Category.NONE],
        tatemae_pattern=TatemaePattern(body.tatemae_pattern),
        surface_level=SurfaceLevel(body.surface_level),
        author_channel_id=body.author_channel_id,
        note=body.note,
        video_id=body.video_id,
    )
    if action == PersonalAction.HIDE_YOUTUBE:
        # 完全な削除なので元に戻せない。「本サイトだけの非表示に戻す」のような降格操作はない。
        try:
            delete_comment_on_youtube(credentials, comment_id)
        except Exception:
            pass
    return {"ok": True}


@router.get("/similarity-marks")
def list_similarity_marks(request: Request):
    """『見たくない』と学習させたマークの一覧(学習データ管理画面用)。"""
    _, channel_id = _require_session(request)
    marks = get_similarity_list().list_marks(channel_id)
    return {
        "marks": [
            {
                "index": i,
                "comment": m.comment,
                "note": m.note,
                "action": m.action.value,
                "categories": [c.value for c in m.categories],
                "tatemae_pattern": m.tatemae_pattern.value,
                "author_channel_id": m.author_channel_id,
                "video_id": m.video_id,
                "threshold": m.threshold,
            }
            for i, m in enumerate(marks)
        ]
    }


@router.delete("/similarity-marks/{index}")
def delete_similarity_mark(index: int, request: Request):
    _, channel_id = _require_session(request)
    get_similarity_list().delete_mark(channel_id, index)
    return {"ok": True}


class RefineMarkIn(BaseModel):
    exception_note: str  # 「元に戻す」で書いてもらった、なぜ違うと思ったかの理由


@router.post("/similarity-marks/{index}/refine")
def refine_similarity_mark(index: int, body: RefineMarkIn, request: Request):
    """『元に戻す』で指定されたコメントの原因マークに例外を追記し、再埋め込みする。
    マークは削除せず、次から同じ間違いを繰り返しにくくする(育てる)。
    合わせて、そのマークだけ必要な一致度(threshold)を段階的に上げる。"""
    _, channel_id = _require_session(request)
    get_similarity_list().refine_mark(channel_id, index, body.exception_note)
    return {"ok": True}


@router.post("/similarity-marks/{index}/reset-threshold")
def reset_similarity_mark_threshold(index: int, request: Request):
    """個別に上げた必要一致度を、共通の初期値(0.85)に戻す。"""
    _, channel_id = _require_session(request)
    get_similarity_list().reset_threshold(channel_id, index)
    return {"ok": True}


@router.delete("/similarity-marks")
def reset_similarity_marks(request: Request):
    _, channel_id = _require_session(request)
    get_similarity_list().reset(channel_id)
    return {"ok": True}


@router.post("/comments/{comment_id}/ban")
def ban_comment_author(comment_id: str, request: Request):
    credentials, _ = _require_session(request)
    try:
        ban_author_on_youtube(credentials, comment_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"操作に失敗しました: {e}")
    return {"ok": True}


class ReplyIn(BaseModel):
    text: str


@router.post("/comments/{comment_id}/reply")
def reply_comment(comment_id: str, body: ReplyIn, request: Request):
    credentials, _ = _require_session(request)
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="返信内容を入力してください")
    try:
        reply = reply_to_comment(credentials, comment_id, body.text.strip())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"返信に失敗しました: {e}")
    return reply


class RephraseIn(BaseModel):
    text: str


@router.post("/comments/rephrase")
def rephrase_comment(body: RephraseIn):
    client = get_classifier().client
    prompt = (
        "このコメントの言いたいことを、攻撃的な言葉を使わずに伝えてください。"
        "伝えるべき内容が特になければ、その旨を伝えてください。\n\n"
        f"コメント:「{body.text}」"
    )
    response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    return {"rephrased": response.text.strip()}
