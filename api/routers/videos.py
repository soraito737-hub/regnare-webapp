"""
動画一覧・コメント判定・コメントへのアクションのエンドポイント。
webapp/streamlit_app_redesign.py の同名関数と同じロジックを、
FastAPI用に「リクエスト単位」で呼び出せる形にしたもの。
判定ロジック(personal_classifier / personal_profile)自体は一切変更していない。
"""
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "modulus"))

from fastapi import APIRouter, HTTPException, Request  # noqa: E402
from google.genai import types as genai_types  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from personal_classifier import (  # noqa: E402
    BATCH_SIZE, Category, CommentJudgment, EmergencyType, PersonalJudgmentClassifier, SurfaceLevel, TatemaePattern,
)
from personal_profile import (  # noqa: E402
    DisplayAction, PersonalAction, PersonalSimilarityList, default_threshold,
    is_emergency, resolve_display_action,
)

from comment_cache_store import load_comment_cache, mark_refreshed, save_comment_cache  # noqa: E402
from config import GEMINI_API_KEY  # noqa: E402
from profile_store import load_profile  # noqa: E402
from routers.auth import get_credentials  # noqa: E402
from youtube_service import (  # noqa: E402
    MAX_COMMENTS_PER_VIDEO, ban_author_on_youtube, delete_comment_on_youtube, delete_comments_on_youtube,
    fetch_comments, get_video_details, list_channel_videos, reply_to_comment,
)

router = APIRouter(prefix="/api", tags=["videos"])

# AI判定のルール(プロンプト・モデル設定)を変えたら上げる。キャッシュ済みの判定のうち
# これと違う版のものは、次に動画を開いたときに判定し直す(自分でボタンを押したものは除く)。
JUDGMENT_VERSION = 3

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
        if (
            entry.get("reason") in ("manual", "emergency")
            or entry.get("user_restored")
            or entry.get("judgment") is None
        ):
            continue
        try:
            judgment = _judgment_from_dict(entry["judgment"])
        except ValueError:
            # カテゴリ名の変更前に保存された判定。次に動画を開いたとき判定し直される
            continue
        similarity_action = None
        mark_id = entry.get("matched_mark_id")
        if mark_id:
            mark = similarity_list.get_mark(channel_id, mark_id)
            if mark is not None:
                similarity_action = mark.action
            else:
                mark_id = None  # マークが削除済みなら参照も消す
        display = resolve_display_action(judgment, profile, similarity_action=similarity_action)
        new_tab = "hidden_regskip" if display.hide else "new"
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


def _judge_comments(texts: list[str]) -> list[CommentJudgment | None]:
    """AI判定。BATCH_SIZE件ずつまとめて全部を同時に送り、まとめた答えから読み取れなかった
    コメントだけ1件ずつ判定し直す。それでも失敗したコメントはNone。"""
    classifier = get_classifier()
    batches = [list(range(i, min(i + BATCH_SIZE, len(texts)))) for i in range(0, len(texts), BATCH_SIZE)]
    results: list[CommentJudgment | None] = [None] * len(texts)
    if not batches:
        return results

    def _run_batch(idxs: list[int]) -> list[CommentJudgment | None]:
        try:
            return classifier.classify_many([texts[i] for i in idxs])
        except Exception:
            print(f"[process_video] まとめた判定に失敗({len(idxs)}件)。1件ずつやり直す", flush=True)
            traceback.print_exc()
            return [None] * len(idxs)

    with ThreadPoolExecutor(max_workers=min(len(batches), 20)) as executor:
        for idxs, judged in zip(batches, executor.map(_run_batch, batches)):
            for i, j in zip(idxs, judged):
                results[i] = j

    retry = [i for i, j in enumerate(results) if j is None]
    if retry:
        print(f"[process_video] まとめた判定で読み取れなかった{len(retry)}件を1件ずつ判定", flush=True)

        def _run_single(i: int) -> CommentJudgment | None:
            try:
                return classifier.classify(texts[i])
            except Exception:
                print(f"[process_video] 判定失敗 index={i}", flush=True)
                traceback.print_exc()
                return None

        with ThreadPoolExecutor(max_workers=min(len(retry), 20)) as executor:
            for i, j in zip(retry, executor.map(_run_single, retry)):
                results[i] = j
    return results


def _build_entry(comment: dict, judgment: CommentJudgment, similarity, profile) -> dict:
    """similarityは (一致したマークのaction, 一致度, マークid)。緊急のコメントでは使わない。"""
    matched_mark_id = None
    if is_emergency(judgment):
        display = DisplayAction(hide=True, reason="emergency")
        sim_score = 0.0
        tab = "privacy"
    else:
        matched_action, sim_score, matched_mark_id = similarity
        display = resolve_display_action(judgment, profile, similarity_action=matched_action)
        tab = "hidden_regskip" if display.hide else "new"

    return {
        "comment": comment,
        "judgment": _judgment_to_dict(judgment),
        "judgment_version": JUDGMENT_VERSION,
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
    }


@router.post("/videos/{video_id}/process")
def process_video(video_id: str, request: Request):
    credentials, channel_id = _require_session(request)
    profile = load_profile(channel_id)
    comments = fetch_comments(credentials, video_id, max_results=MAX_COMMENTS_PER_VIDEO)

    # 表示ルールや初期設定の変更を、キャッシュ済みの判定にも反映する(LLMは呼ばない)
    reapply_profile_to_cache(channel_id, profile)

    # 一度判定したコメント(comment_id)は結果を丸ごと覚えておき、二度と分類・
    # 類似度チェックにかけない。新しく届いたコメントと、古い版のルールで判定したものだけを判定する。
    cache = load_comment_cache(channel_id)

    def _needs_judgment(c: dict) -> bool:
        entry = cache.get(c["comment_id"])
        if entry is None:
            return True
        if entry.get("reason") == "manual" or entry.get("user_restored"):
            return False
        # 視聴者が本文を書き換えていたら、新しい本文で判定し直す
        if entry["comment"].get("text") != c["text"]:
            return True
        return entry.get("judgment_version") != JUDGMENT_VERSION

    to_process = [c for c in comments if _needs_judgment(c)]

    # YouTube API利用規約の30日ルール: 今YouTubeから取れたコメントは本文を最新に
    # 上書きし、取り直した時刻を記録する(30日間取り直せなかったものだけが消える)
    refreshed_mark_texts: dict[str, str] = {}
    for c in comments:
        entry = cache.get(c["comment_id"])
        if entry is not None:
            entry["comment"] = c
            mark_refreshed(entry)
            if entry.get("own_mark_id"):
                refreshed_mark_texts[entry["own_mark_id"]] = c["text"]
    get_similarity_list().refresh_marks(channel_id, refreshed_mark_texts)

    # 判定に失敗したコメント(Gemini APIの残高切れ・障害など)は今回だけ非表示扱いにし、
    # キャッシュには保存しない。保存すると、復旧後も二度と判定し直されなくなるため。
    failed: dict[str, dict] = {}

    def _mark_failed(c: dict) -> None:
        # 原因はRenderのLogsで確かめられるように残してある(コメント本文は出さない)
        failed[c["comment_id"]] = {
            "comment": c,
            "judgment": None,
            "similarity_score": 0.0,
            "tab": "hidden_regskip",
            "reason": "error",
            "matched_mark_id": None,
            "own_mark_id": None,
        }

    if to_process:
        texts = [c["text"] for c in to_process]
        # AI判定と、過去のマークとの類似度は互いの結果を使わないので、同時に走らせて
        # 待ち時間を長い方の1つ分にする(類似度は緊急のコメントでは使わないが、待たないために全件調べる)
        with ThreadPoolExecutor(max_workers=1) as executor:
            similarity_future = executor.submit(get_similarity_list().check_similarity_many, channel_id, texts)
            judgments = _judge_comments(texts)
            try:
                similarities = similarity_future.result()
            except Exception:
                print(f"[process_video] 類似度の確認に失敗({len(texts)}件)", flush=True)
                traceback.print_exc()
                similarities = None

        for i, (c, j) in enumerate(zip(to_process, judgments)):
            if j is None or (similarities is None and not is_emergency(j)):
                _mark_failed(c)
                continue
            similarity = similarities[i] if similarities is not None else None
            cache[c["comment_id"]] = _build_entry(c, j, similarity, profile)
    save_comment_cache(channel_id, cache)

    tabs: dict[str, list] = {"new": [], "hidden_regskip": [], "privacy": []}
    for c in comments:
        entry = cache.get(c["comment_id"]) or failed[c["comment_id"]]
        # コメント本文・投稿者名などの表示情報だけは常に最新のものを使う
        # (判定結果自体(tab/reason等)はキャッシュのまま変えない)。
        tabs[entry["tab"]].append({**entry, "comment": c})

    return {"tabs": tabs, "flagged_authors": _find_flagged_authors(cache)}


FLAGGED_MIN_COUNT = 3


def _find_flagged_authors(cache: dict) -> list[dict]:
    """要注意ユーザー: これまで開いた全動画のキャッシュから、隠したタブ(AI判定・自分で押したもの)と
    プライバシーのタブに入ったコメントを投稿者ごとに数え、FLAGGED_MIN_COUNT件以上の人を返す。
    判定に失敗しただけのコメントはキャッシュに入らないので数えない。"""
    by_author: dict[str, dict] = {}
    for entry in cache.values():
        if entry.get("tab") not in ("hidden_regskip", "privacy"):
            continue
        author_id = entry.get("comment", {}).get("author_channel_id")
        if not author_id:
            continue
        a = by_author.setdefault(
            author_id,
            {"author_channel_id": author_id, "author": entry["comment"].get("author", ""), "count": 0, "breakdown": {}},
        )
        a["count"] += 1
        judgment = entry.get("judgment")
        if not judgment:
            continue
        if entry["tab"] == "privacy":
            keys = [f"緊急|{judgment['emergency']}"]
        else:
            keys = [f"{cat}|{judgment['tatemae_pattern']}" for cat in judgment["categories"]]
        for key in keys:
            a["breakdown"][key] = a["breakdown"].get(key, 0) + 1
    return [a for a in by_author.values() if a["count"] >= FLAGGED_MIN_COUNT]


class MarkIn(BaseModel):
    action: str  # "normal" | "hide_regskip"
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
        "tab": "hidden_regskip",
        "reason": "manual",
        "matched_mark_id": None,
        "own_mark_id": new_mark_id,
    }
    save_comment_cache(channel_id, cache)
    return {"ok": True}


@router.post("/comments/{comment_id}/delete-youtube")
def delete_comment_youtube(comment_id: str, request: Request):
    """個別コメント(視聴者コメント・見たくないコメントのどちらのタブからでも)を、実際にYouTube上から削除する。
    初期設定や自動判定では一切呼ばれず、ユーザーがこのコメント1件を選んで明示的に
    操作した場合のみ実行される(フロント側でも確認ダイアログを挟む)。元に戻せない。"""
    credentials, channel_id = _require_session(request)
    try:
        delete_comment_on_youtube(credentials, comment_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"YouTube上での削除に失敗しました: {e}")

    # 本当にYouTube上から削除できたので、Regskip側のキャッシュからも消す。
    cache = load_comment_cache(channel_id)
    cache.pop(comment_id, None)
    save_comment_cache(channel_id, cache)
    return {"ok": True}


MAX_BULK_DELETE = 500


class BulkDeleteIn(BaseModel):
    comment_ids: list[str]


@router.post("/comments/delete-youtube-bulk")
def delete_comments_youtube_bulk(body: BulkDeleteIn, request: Request):
    """『見たくないコメント』をまとめてYouTube上から削除する。ユーザーが一括削除ボタンを押し、
    確認画面(元に戻せないこと・対象を見られること)でもう一度同意した場合にのみ呼ばれる。
    どのコメントを消すかはフロントでユーザーが確認した一覧をそのまま受け取り、AIの判定だけで
    自動的に消すことはしない。"""
    credentials, channel_id = _require_session(request)
    ids = list(dict.fromkeys(body.comment_ids))  # 重複を除く(順番は保つ)
    if not ids:
        return {"deleted": [], "failed": []}
    if len(ids) > MAX_BULK_DELETE:
        raise HTTPException(status_code=400, detail=f"一度に削除できるのは{MAX_BULK_DELETE}件までです")
    deleted, failed = delete_comments_on_youtube(credentials, ids)

    if deleted:
        cache = load_comment_cache(channel_id)
        for comment_id in deleted:
            cache.pop(comment_id, None)
        save_comment_cache(channel_id, cache)
    return {"deleted": deleted, "failed": failed}


class RestoreIn(BaseModel):
    note: str = ""  # なぜ違うと思ったかの理由(任意)。personal_similarityのときだけ使う。


@router.post("/comments/{comment_id}/restore")
def restore_comment(comment_id: str, body: RestoreIn, request: Request):
    """『視聴者コメントに戻す』。原因(reason)によって対応が変わる。
    - manual(自分でボタンを押した): そのとき作られたマーク自体を削除する(完全に元通り)
    - personal_similarity(過去のマークと一致): そのマークに例外を追記して育てる(部分的な改善)
    - それ以外(初期設定のルールでAIが隠した): 「アンチではない」として通常表示のマークを作る
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
    elif entry.get("judgment") and entry.get("comment", {}).get("text"):
        # 初期設定のルール(personal_pattern/attack_level)でAIが隠したもの。「アンチではない」
        # という本人の判断を「通常表示」のマークとして覚え、次から似たコメントも隠さないようにする
        # (緊急のコメントは_build_entryで類似度より先に扱うので、このマークでは表示に戻らない)。
        judgment = _judgment_from_dict(entry["judgment"])
        similarity_list.mark_comment(
            user_id=channel_id,
            comment=entry["comment"]["text"],
            action=PersonalAction.NORMAL,
            categories=judgment.categories,
            tatemae_pattern=judgment.tatemae_pattern,
            surface_level=judgment.surface_level,
            author_channel_id=entry["comment"].get("author_channel_id"),
            note=body.note or None,
        )

    cache[comment_id] = {
        **entry,
        "tab": "new",
        "reason": "none",
        "matched_mark_id": None,
        "own_mark_id": None,
        # 自分で戻したことの目印。設定の再反映や判定のやり直しで隠し直さないために使う。
        "user_restored": True,
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
                "default_threshold": default_threshold(m.action),
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
    """個別に上げた必要一致度を、初期値(0.95)に戻す。"""
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
    text: str = Field(max_length=2000)
    comment_id: str | None = None


@router.post("/comments/rephrase")
def rephrase_comment(body: RephraseIn, request: Request):
    # 誰でも呼べるとAIの費用を使われてしまうので、ログインした人だけにする
    _, channel_id = _require_session(request)

    # 同じコメントを何度押しても、AIに送るのは最初の1回だけにする(結果はキャッシュに暗号化して保存)
    cache = load_comment_cache(channel_id) if body.comment_id else {}
    entry = cache.get(body.comment_id) if body.comment_id else None
    if entry and entry.get("rephrased") and entry.get("rephrased_source") == body.text:
        return {"rephrased": entry["rephrased"]}

    client = get_classifier().client
    prompt = (
        "このコメントの言いたいことを、攻撃的な言葉を使わずに、1〜2文の短い日本語で伝えてください。"
        "前置きや説明は書かず、言い換えた文だけを答えてください。"
        "伝えるべき内容が特になければ「特に伝えたい内容はないコメントです」とだけ答えてください。\n\n"
        f"コメント:「{body.text}」"
    )
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            # 料金と待ち時間の大半は「考える」分と長い答えなので、どちらも最小限にする
            # (low=平均3.5秒、minimal=平均1.9秒で、言い換えの質はほぼ同じだった)
            thinking_config=genai_types.ThinkingConfig(thinking_level="minimal"),
            max_output_tokens=1024,
        ),
    )
    rephrased = (response.text or "").strip()
    if not rephrased:
        raise HTTPException(status_code=502, detail="言い換えに失敗しました。もう一度お試しください")

    if entry is not None:
        entry["rephrased"] = rephrased
        entry["rephrased_source"] = body.text
        save_comment_cache(channel_id, cache)
    return {"rephrased": rephrased}
