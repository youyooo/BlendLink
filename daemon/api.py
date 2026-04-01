"""
BlendLink REST API - 本地 HTTP 接口
==============================

供 Blender 插件调用，所有端点仅监听 localhost。
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import logging
import platform
import sys

logger = logging.getLogger("blendlink.api")

try:
    from version import __version__
except ImportError:
    __version__ = "unknown"

app = FastAPI(title="BlendLink Daemon API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)






# ─── 请求模型 ───────────────────────────────────────

class DownloadReq(BaseModel):
    asset_id: int
    asset_name: str
    magnet_link: Optional[str] = None
    torrent_data: Optional[str] = None  # base64

class UploadReq(BaseModel):
    file_path: str
    name: str
    description: Optional[str] = None
    category: str
    tags: Optional[List[str]] = None

class TrackerReq(BaseModel):
    tracker_url: str

class LikeReq(BaseModel):
    fingerprint: str


# ─── API 端点 ───────────────────────────────────────

@app.get("/version")
async def get_version():
    """返回守护进程版本信息"""
    return {
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.system(),
    }


@app.get("/status")
async def get_status(request: Request):
    core = request.app.state.daemon_core
    return core.get_status()


@app.get("/identity")
async def get_identity(request: Request):
    core = request.app.state.daemon_core
    return core.get_identity()


@app.get("/assets")
async def get_assets(
    request: Request,
    page: int = 1,
    limit: int = 20,
    category: Optional[str] = None,
):
    core = request.app.state.daemon_core
    import requests as http
    params = {"page": page, "limit": limit}
    if category and category != "ALL":
        params["category"] = category
    try:
        resp = http.get(f"{core.tracker_url}/api/assets/hot", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except http.ConnectionError:
        raise HTTPException(status_code=502, detail="无法连接 Tracker")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/assets/search")
async def search_assets(
    request: Request,
    q: str,
    category: Optional[str] = None,
    limit: int = 20,
):
    core = request.app.state.daemon_core
    import requests as http
    params = {"q": q, "limit": limit}
    if category and category != "ALL":
        params["category"] = category
    try:
        resp = http.get(f"{core.tracker_url}/api/assets/search", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except http.ConnectionError:
        raise HTTPException(status_code=502, detail="无法连接 Tracker")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/download")
async def download_asset(req: DownloadReq, request: Request):
    core = request.app.state.daemon_core
    import base64
    if not core.p2p_client:
        raise HTTPException(status_code=503, detail="P2P 不可用（libtorrent 未安装）")
    try:
        torrent_data = base64.b64decode(req.torrent_data) if req.torrent_data else None
        handle = core.p2p_client.download_asset(
            asset_id=req.asset_id,
            asset_name=req.asset_name,
            torrent_data=torrent_data,
            magnet_link=req.magnet_link,
        )
        core.ledger.record_transaction({
            "type": "download",
            "asset_id": req.asset_id,
            "asset_name": req.asset_name,
            "amount": 0,
            "metadata": {"requires_seeding_hours": 24},
        })
        return {"status": "downloading", "asset_name": req.asset_name,
                "info_hash": handle.info_hash[:16] + "..."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"下载失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/assets/{asset_id}/like")
async def like_asset(asset_id: int, req: LikeReq, request: Request):
    core = request.app.state.daemon_core
    import requests as http
    try:
        resp = http.post(
            f"{core.tracker_url}/api/assets/{asset_id}/like",
            params={"fingerprint": req.fingerprint},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except http.ConnectionError:
        raise HTTPException(status_code=502, detail="无法连接 Tracker")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/seeds")
async def get_seeds(request: Request):
    core = request.app.state.daemon_core
    if not core.p2p_client:
        return {"seeds": [], "message": "P2P 未初始化"}
    return {
        "seeds": core.p2p_client.get_all_assets_status(),
        "total": len(core.p2p_client.active_assets),
    }


@app.delete("/seeds/{info_hash}")
async def stop_seeding(info_hash: str, request: Request):
    core = request.app.state.daemon_core
    if not core.p2p_client:
        raise HTTPException(status_code=503, detail="P2P 未初始化")
    success = core.p2p_client.force_delete_asset(info_hash)
    if not success:
        raise HTTPException(
            status_code=403,
            detail="尚未完成强制做种（需 24 小时或上传 100MB）",
        )
    return {"status": "stopped", "info_hash": info_hash}


@app.post("/upload")
async def upload_asset(req: UploadReq, request: Request):
    core = request.app.state.daemon_core
    import os, requests as http
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {req.file_path}")
    if not req.file_path.endswith(".blend"):
        raise HTTPException(status_code=400, detail="仅支持 .blend 文件")
    file_size = os.path.getsize(req.file_path)
    identity = core.get_identity()
    info_hash = f"mock_{hash(req.file_path)}"
    try:
        resp = http.post(
            f"{core.tracker_url}/api/assets/register",
            json={
                "info_hash": info_hash,
                "name": req.name,
                "description": req.description or "",
                "creator_fingerprint": identity["fingerprint"],
                "category": req.category,
                "tags": req.tags or [],
                "file_size": file_size,
                "magnet_link": f"magnet:?xt=urn:btih:{info_hash}",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        core.ledger.record_transaction({
            "type": "upload",
            "asset_id": data.get("asset_id", 0),
            "asset_name": req.name,
            "amount": data.get("upload_points_earned", 50),
            "metadata": {"file_size": file_size, "category": req.category},
        })
        return {
            "status": "uploaded",
            "asset_id": data.get("asset_id"),
            "points_earned": data.get("upload_points_earned", 50),
        }
    except http.ConnectionError:
        raise HTTPException(status_code=502, detail="无法连接 Tracker")
    except http.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        logger.error(f"上传失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sync-ledger")
async def sync_ledger(request: Request):
    core = request.app.state.daemon_core
    from shared.ledger_sync import LedgerSyncManager
    try:
        ledger_file = str(core.ledger_dir / "ledger.jsonl")
        sync_mgr = LedgerSyncManager(ledger_file)
        proof = sync_mgr.generate_proof()
        if not proof:
            return {"status": "empty", "message": "没有可同步的记录"}
        result = sync_mgr.submit_proof_to_tracker(core.tracker_url, proof)
        return {"status": "synced", "result": result}
    except Exception as e:
        logger.error(f"账本同步失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ledger/history")
async def get_ledger_history(request: Request, limit: int = 20):
    core = request.app.state.daemon_core
    return {
        "history": core.ledger.get_ledger_history(limit=limit),
        "balance": core.ledger.get_balance(),
    }


@app.get("/leaderboard")
async def get_leaderboard(request: Request, limit: int = 50):
    core = request.app.state.daemon_core
    import requests as http
    try:
        resp = http.get(
            f"{core.tracker_url}/api/leaderboard/users",
            params={"limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except http.ConnectionError:
        raise HTTPException(status_code=502, detail="无法连接 Tracker")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/config/tracker")
async def update_tracker(req: TrackerReq, request: Request):
    core = request.app.state.daemon_core
    core.tracker_url = req.tracker_url
    if core.p2p_client:
        core.p2p_client.tracker_url = req.tracker_url
    return {"status": "updated", "tracker_url": req.tracker_url}
