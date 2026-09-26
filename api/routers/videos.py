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

from personal_classifier import (  # noqa: E402
    Category, CommentJudgment, EmergencyType, PersonalJudgmentClassifier, SurfaceLevel, TatemaePattern,
)
from personal_profile import (  # noqa: E402
    DisplayAction, PersonalAction, PersonalSimilarityList,
    find_flagged_authors, is_emergency, resolve_display_action,
)

from comment_cache_store import load_comment_cache, save_comment_cache  # noqa: E402
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


def _judgment_from_dict(d: dict) -> CommentJudgment:
    return CommentJudgment(
        comment_id="",
        categories=[Category(c) for c in d["categories"]],
        surface_level=SurfaceLevel(d["surface_level"]),
        tatemae_pattern=TatemaePattern(d["tatemae_pattern"]),
        emergency=EmergencyType(d["emergency"]),
        reasoning="",
    )


def reapply_profile_to_cache(channel_id: str, profile) -> None:
    """初期設定を保存したときに呼ぶ。LLM・エンベディングは一切呼ばず、キャッシュ済みの
    判定結果(judgment)と、過去に一致していたマークの現在のactionだけを使って、
    tab/reasonを再計算しなおす(コストは完全にゼロ)。
    自分でボタンを押した(manual)コメントと緊急判定(emergency)のコメントは、
    設定変更で上書きされるべきではないため対象外にする。"""
    cache = load_comment_cache(channel_id)
    if not cache:
        return
    similarity_list = get_similarity_list()
    changed = False
    for entry in cache.values():
        if entry.get("reason") in ("manual", "emergency") or entry.get("judgment") is None:
            continue
        judgment = _judgment_from_dict(entry["judgment"])
        similarity_action = None
        mark_id = entry.get("matched_mark_id")
        if mark_id:
            mark = similarity_list.get_mark(channel_id, mark_id)
            if mark is not None:
                similarity_action = mark.action
            else:
                mark_id = None  # マークが削除済みなら参照も消す
        display = resolve_display_action(judgment, profile, similarity_action=similarity_action)
        if not display.hide:
            new_tab = "new"
        elif display.escalate_to_youtube:
            new_tab = "hidden_youtube"
        else:
            new_tab = "hidden_regskip"
        if new_tab != entry.get("tab") or display.reason != entry.get("reason") or mark_id != entry.get("matched_mark_id"):
            entry["tab"] = new_tab
            entry["reason"] = display.reason
            entry["matched_mark_id"] = mark_id
            changed = True
    if changed:
        save_comment_cache(channel_id, cache)


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

    matched_mark_id = None
    if is_emergency(judgment):
        display = DisplayAction(hide=True, escalate_to_youtube=False, reason="emergency")
        sim_score = 0.0
        tab = "privacy"
    else:
        similarity_list = get_similarity_list()
        matched_action, sim_score, matched_mark_id = similarity_list.check_similarity(channel_id, comment["text"])
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
        # reason="personal_similarity"のとき、原因になったマークのid。
        # 「視聴者コメントに戻す」でこのマークにだけ例外を追記して育てるために使う。
        "matched_mark_id": matched_mark_id,
        # 自分でボタンを押して非表示にした場合(mark_comment参照)だけ入る、
        # そのとき作られたマークのid。「視聴者コメントに戻す」でこのマーク自体を
        # 削除して完全に元通りにするために使う。自動処理では常にNone。
        "own_mark_id": None,
        # 自動処理(process_video)では実際のYouTube側の削除APIは一切呼ばないため、
        # tab="hidden_youtube"になったコメントも、この時点ではまだ実行されていない。
        # フロント側でボタンを押して個別に実行するまでFalseのまま。
        "executed_on_youtube": False,
    }


@router.post("/videos/{video_id}/process")
def process_video(video_id: str, request: Request):
    credentials, channel_id = _require_session(request)
    profile = load_profile(channel_id)
    comments = fetch_comments(credentials, video_id, max_results=MAX_COMMENTS_PER_VIDEO)

    # 一度判定したコメント(comment_id)は結果を丸ごと覚えておき、二度と分類・
    # 類似度チェックにかけない。新しく届いたコメントだけを判定する。
    cache = load_comment_cache(channel_id)
    to_process = [c for c in comments if c["comment_id"] not in cache]

    if to_process:
        with ThreadPoolExecutor(max_workers=20) as executor:
            future_to_comment = {
                executor.submit(_process_single_comment, c, channel_id, profile): c for c in to_process
            }
            for future in as_completed(future_to_comment):
                c = future_to_comment[future]
                try:
                    cache[c["comment_id"]] = future.result()
                except Exception:
                    cache[c["comment_id"]] = {
                        "comment": c,
                        "judgment": None,
                        "similarity_score": 0.0,
                        "tab": "hidden_regskip",
                        "reason": "error",
                        "matched_mark_id": None,
                        "own_mark_id": None,
                        "executed_on_youtube": False,
                    }
        save_comment_cache(channel_id, cache)

    tabs: dict[str, list] = {"new": [], "hidden_regskip": [], "hidden_youtube": [], "privacy": []}
    for c in comments:
        entry = cache[c["comment_id"]]
        # コメント本文・投稿者名などの表示情報だけは常に最新のものを使う
        # (判定結果自体(tab/reason等)はキャッシュのまま変えない)。
        tabs[entry["tab"]].append({**entry, "comment": c})

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
    new_mark_id = get_similarity_list().mark_comment(
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

    # このコメント自身の永続キャッシュを、今押した内容で上書きする。次に動画を
    # 開いたときにAI分類・類似度チェックをやり直さず、この結果をそのまま使うため。
    # reason="manual"は「自分でボタンを押した」ことを示し、own_mark_idを記録しておくと、
    # 「視聴者コメントに戻す」で今作ったこのマーク自体を削除して完全に元通りにできる。
    cache = load_comment_cache(channel_id)
    existing = cache.get(comment_id, {})
    cache[comment_id] = {
        **existing,
        "comment": existing.get(
            "comment", {"comment_id": comment_id, "text": body.text, "author_channel_id": body.author_channel_id}
        ),
        "tab": "hidden_youtube" if action == PersonalAction.HIDE_YOUTUBE else "hidden_regskip",
        "reason": "manual",
        "matched_mark_id": None,
        "own_mark_id": new_mark_id,
        "executed_on_youtube": action == PersonalAction.HIDE_YOUTUBE,
    }
    save_comment_cache(channel_id, cache)
    return {"ok": True}


class RestoreIn(BaseModel):
    note: str = ""  # なぜ違うと思ったかの理由(任意)。personal_similarityのときだけ使う。


@router.post("/comments/{comment_id}/restore")
def restore_comment(comment_id: str, body: RestoreIn, request: Request):
    """『視聴者コメントに戻す』。原因(reason)によって対応が変わる。
    - manual(自分でボタンを押した): そのとき作られたマーク自体を削除する(完全に元通り)
    - personal_similarity(過去のマークと一致): そのマークに例外を追記して育てる(部分的な改善)
    - それ以外(ルールのみ・マーク無し): マークは無いので、キャッシュの表示だけ書き換える
    どの場合も、このコメント自身は次から「新着」として永続的に扱われる。"""
    _, channel_id = _require_session(request)
    cache = load_comment_cache(channel_id)
    entry = cache.get(comment_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="このコメントの判定結果が見つかりません")

    similarity_list = get_similarity_list()
    if entry.get("reason") == "manual" and entry.get("own_mark_id"):
        similarity_list.delete_mark(channel_id, entry["own_mark_id"])
    elif entry.get("reason") == "personal_similarity" and entry.get("matched_mark_id"):
        similarity_list.refine_mark(channel_id, entry["matched_mark_id"], body.note or "この一致は間違いだった")

    cache[comment_id] = {
        **entry,
        "tab": "new",
        "reason": "none",
        "matched_mark_id": None,
        "own_mark_id": None,
    }
    save_comment_cache(channel_id, cache)
    return {"ok": True}


@router.get("/similarity-marks")
def list_similarity_marks(request: Request):
    """『見たくない』と学習させたマークの一覧(学習データ管理画面用)。"""
    _, channel_id = _require_session(request)
    marks = get_similarity_list().list_marks(channel_id)
    return {
        "marks": [
            {
                "id": m.id,
                "comment": m.comment,
                "note": m.note,
                "action": m.action.value,
                "categories": [c.value for c in m.categories],
                "tatemae_pattern": m.tatemae_pattern.value,
                "author_channel_id": m.author_channel_id,
                "video_id": m.video_id,
                "threshold": m.threshold,
            }
            for m in marks
        ]
    }


@router.delete("/similarity-marks/{mark_id}")
def delete_similarity_mark(mark_id: str, request: Request):
    _, channel_id = _require_session(request)
    get_similarity_list().delete_mark(channel_id, mark_id)
    return {"ok": True}


class RefineMarkIn(BaseModel):
    exception_note: str  # 「元に戻す」で書いてもらった、なぜ違うと思ったかの理由


@router.post("/similarity-marks/{mark_id}/refine")
def refine_similarity_mark(mark_id: str, body: RefineMarkIn, request: Request):
    """『元に戻す』で指定されたコメントの原因マークに例外を追記し、再埋め込みする。
    マークは削除せず、次から同じ間違いを繰り返しにくくする(育てる)。
    合わせて、そのマークだけ必要な一致度(threshold)を段階的に上げる。"""
    _, channel_id = _require_session(request)
    get_similarity_list().refine_mark(channel_id, mark_id, body.exception_note)
    return {"ok": True}


@router.post("/similarity-marks/{mark_id}/reset-threshold")
def reset_similarity_mark_threshold(mark_id: str, request: Request):
    """個別に上げた必要一致度を、共通の初期値(0.85)に戻す。"""
    _, channel_id = _require_session(request)
    get_similarity_list().reset_threshold(channel_id, mark_id)
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
