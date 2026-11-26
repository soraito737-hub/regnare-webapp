"""運営チームだけが見られる利用状況。

Renderの環境変数 ADMIN_CHANNEL_IDS に、見てよいYouTubeチャンネルIDをカンマ区切りで入れる。
未設定なら誰も見られない。返すのは人数だけで、どのチャンネルが使っているかは返さない。
"""
from fastapi import APIRouter, HTTPException, Request

from login_log import login_stats
from routers.auth import _admin_channel_ids, get_credentials

router = APIRouter(prefix="/api/admin", tags=["admin"])


def is_admin(request: Request) -> bool:
    channel_id = request.session.get("channel_id")
    return bool(channel_id) and get_credentials(request) is not None and channel_id in _admin_channel_ids()


@router.get("/stats")
def stats(request: Request):
    if not is_admin(request):
        # 運営チーム以外には、この画面があること自体を見せない
        raise HTTPException(status_code=404, detail="Not Found")
    return login_stats()
