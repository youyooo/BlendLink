"""
BlendLink Tracker 服务 (FastAPI + SQLite)

集中式索引层：元数据、排名、账本验证
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import time
import json
import hashlib
import os
from contextlib import contextmanager


# ==================== 数据库初始化 ====================

DB_PATH = os.environ.get("BLENDLINK_DB_PATH", "blendlink_tracker.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """初始化数据库表结构"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 用户表（基于硬件指纹，无需注册）
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT UNIQUE NOT NULL,       -- 硬件指纹（身份）
        peer_id TEXT,                            -- P2P Peer ID
        public_key TEXT,                         -- ED25519 公钥
        identity_name TEXT,                      -- 展示名称（如 dragon_ab12cd）
        total_points INTEGER DEFAULT 0,          -- 总积分（来自账本证明）
        reputation_score INTEGER DEFAULT 50,     -- 信誉分 0-100
        uploads INTEGER DEFAULT 0,
        downloads INTEGER DEFAULT 0,
        seed_hours REAL DEFAULT 0,
        bytes_uploaded INTEGER DEFAULT 0,
        is_banned BOOLEAN DEFAULT FALSE,
        last_seen TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # 资产表
    c.execute("""
    CREATE TABLE IF NOT EXISTS assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        info_hash TEXT UNIQUE NOT NULL,           -- torrent info hash（唯一标识）
        name TEXT NOT NULL,
        description TEXT,
        creator_fingerprint TEXT,                 -- 创作者指纹
        category TEXT,                            -- model / material / shader / texture / hdri
        tags TEXT,                                -- JSON 数组
        file_size INTEGER,
        blender_version TEXT,                     -- 兼容的 Blender 版本
        thumbnail_url TEXT,                       -- 预览图（160x120）
        preview_gif_url TEXT,                     -- 动画预览 GIF
        torrent_data BLOB,                        -- .torrent 文件二进制
        magnet_link TEXT,                         -- magnet URI
        downloads INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        seed_count INTEGER DEFAULT 0,             -- 当前活跃做种节点
        leech_count INTEGER DEFAULT 0,
        hot_score REAL DEFAULT 0.0,               -- 热度分（每小时更新）
        is_reported BOOLEAN DEFAULT FALSE,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (creator_fingerprint) REFERENCES users(fingerprint)
    )""")
    
    # 贡献记录表
    c.execute("""
    CREATE TABLE IF NOT EXISTS contributions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL,
        asset_id INTEGER,
        action TEXT NOT NULL,                     -- upload / download / seed / like
        points_earned INTEGER DEFAULT 0,
        bytes_uploaded INTEGER DEFAULT 0,
        seed_duration_seconds INTEGER DEFAULT 0,
        ledger_proof_hash TEXT,                   -- 对应账本证明哈希
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (fingerprint) REFERENCES users(fingerprint),
        FOREIGN KEY (asset_id) REFERENCES assets(id)
    )""")
    
    # 做种会话表
    c.execute("""
    CREATE TABLE IF NOT EXISTS seeding_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL,
        asset_id INTEGER NOT NULL,
        info_hash TEXT NOT NULL,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_ping TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ended_at TIMESTAMP,
        bytes_uploaded INTEGER DEFAULT 0,
        bytes_downloaded INTEGER DEFAULT 0,
        is_active BOOLEAN DEFAULT TRUE,
        FOREIGN KEY (fingerprint) REFERENCES users(fingerprint),
        FOREIGN KEY (asset_id) REFERENCES assets(id)
    )""")
    
    # 点赞表
    c.execute("""
    CREATE TABLE IF NOT EXISTS likes (
        fingerprint TEXT NOT NULL,
        asset_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (fingerprint, asset_id),
        FOREIGN KEY (fingerprint) REFERENCES users(fingerprint),
        FOREIGN KEY (asset_id) REFERENCES assets(id)
    )""")
    
    # 账本证明记录
    c.execute("""
    CREATE TABLE IF NOT EXISTS ledger_proofs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL,
        total_points INTEGER,
        ledger_hash TEXT,
        entries_count INTEGER,
        signature TEXT,
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        accepted BOOLEAN DEFAULT TRUE,
        FOREIGN KEY (fingerprint) REFERENCES users(fingerprint)
    )""")
    
    # 索引（加速查询）
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_hot ON assets(hot_score DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_contributions_fingerprint ON contributions(fingerprint)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_seeding_sessions_active ON seeding_sessions(is_active)")
    
    conn.commit()
    conn.close()


# ==================== FastAPI 应用 ====================

app = FastAPI(
    title="BlendLink Tracker",
    description="Blender Decentralized Asset Library - Tracker Service",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Pydantic 模型 ====================

class UserRegisterRequest(BaseModel):
    fingerprint: str
    peer_id: str
    public_key: Optional[str] = None
    identity_name: Optional[str] = None


class LedgerProofRequest(BaseModel):
    fingerprint: str
    total_points: int
    ledger_hash: str
    last_entry_timestamp: int
    entries_count: int
    signature: str


class AssetRegisterRequest(BaseModel):
    info_hash: str
    name: str
    description: Optional[str] = None
    creator_fingerprint: str
    category: str
    tags: Optional[List[str]] = None
    file_size: Optional[int] = None
    blender_version: Optional[str] = None
    thumbnail_url: Optional[str] = None
    preview_gif_url: Optional[str] = None
    magnet_link: Optional[str] = None


class SeedingPingRequest(BaseModel):
    fingerprint: str
    info_hash: str
    bytes_uploaded: int
    bytes_downloaded: int


class ContributionReportRequest(BaseModel):
    fingerprint: str
    asset_id: int
    action: str
    points_earned: int
    bytes_uploaded: Optional[int] = 0
    seed_duration_seconds: Optional[int] = 0
    ledger_proof_hash: Optional[str] = None


# ==================== 热度分计算 ====================

def calculate_hot_score(downloads: int, likes: int, seed_count: int, created_days_ago: float) -> float:
    """
    热度分算法
    
    公式参考 Reddit/Hacker News 时间衰减算法
    """
    # 基础分
    score = downloads * 2 + likes * 5 + seed_count * 0.5
    
    # 时间衰减（每天衰减约 10%）
    decay = max(1, created_days_ago ** 0.5)
    score = score / decay
    
    # 新资产加成（发布 24 小时内给予额外加成）
    if created_days_ago < 1:
        score *= 1.5
    
    return score


# ==================== API 路由 ====================

# ── 用户管理 ──────────────────────────────────────

@app.post("/api/users/register", summary="注册/更新用户（基于硬件指纹）")
def register_user(request: UserRegisterRequest, db=Depends(get_db)):
    """
    不需要用户名密码，直接用硬件指纹注册
    已存在则更新最后在线时间
    """
    # 检查指纹是否合法（基础验证）
    if len(request.fingerprint) != 64:
        raise HTTPException(status_code=400, detail="指纹格式错误")
    
    # 插入或更新用户
    db.execute("""
        INSERT INTO users (fingerprint, peer_id, public_key, identity_name, last_seen)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(fingerprint) DO UPDATE SET
            last_seen = CURRENT_TIMESTAMP,
            peer_id = excluded.peer_id
    """, (request.fingerprint, request.peer_id, request.public_key, request.identity_name))
    
    db.commit()
    
    # 获取用户信息
    user = db.execute(
        "SELECT * FROM users WHERE fingerprint = ?",
        (request.fingerprint,)
    ).fetchone()
    
    return {
        "status": "ok",
        "identity_name": user["identity_name"],
        "total_points": user["total_points"],
        "reputation_score": user["reputation_score"],
    }


@app.post("/api/users/submit-ledger", summary="提交账本证明")
def submit_ledger(request: LedgerProofRequest, db=Depends(get_db)):
    """
    客户端定期提交账本证明
    Tracker 验证后更新积分
    """
    # 基础验证
    if request.total_points < 0 or request.total_points > 1000000:
        raise HTTPException(status_code=400, detail="积分超出范围")
    
    if request.last_entry_timestamp > int(time.time()):
        raise HTTPException(status_code=400, detail="时间戳不合理")
    
    # 获取上次提交的账本哈希（防止回滚）
    last_proof = db.execute("""
        SELECT * FROM ledger_proofs 
        WHERE fingerprint = ?
        ORDER BY submitted_at DESC LIMIT 1
    """, (request.fingerprint,)).fetchone()
    
    if last_proof and last_proof["total_points"] > request.total_points:
        raise HTTPException(status_code=400, detail="积分不可回滚")
    
    # 记录账本证明
    db.execute("""
        INSERT INTO ledger_proofs 
        (fingerprint, total_points, ledger_hash, entries_count, signature)
        VALUES (?, ?, ?, ?, ?)
    """, (
        request.fingerprint,
        request.total_points,
        request.ledger_hash,
        request.entries_count,
        request.signature,
    ))
    
    # 更新用户积分
    db.execute("""
        UPDATE users SET total_points = ?, last_seen = CURRENT_TIMESTAMP
        WHERE fingerprint = ?
    """, (request.total_points, request.fingerprint))
    
    db.commit()
    
    return {
        "status": "accepted",
        "total_points": request.total_points,
    }


@app.get("/api/users/{fingerprint}/stats", summary="获取用户统计")
def get_user_stats(fingerprint: str, db=Depends(get_db)):
    user = db.execute(
        "SELECT * FROM users WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 获取用户上传的资产
    assets = db.execute(
        "SELECT id, name, downloads, likes FROM assets WHERE creator_fingerprint = ?",
        (fingerprint,)
    ).fetchall()
    
    return {
        "identity_name": user["identity_name"],
        "fingerprint": fingerprint[:8] + "...",  # 脱敏
        "total_points": user["total_points"],
        "reputation_score": user["reputation_score"],
        "uploads": user["uploads"],
        "downloads": user["downloads"],
        "seed_hours": user["seed_hours"],
        "bytes_uploaded": user["bytes_uploaded"],
        "assets": [dict(a) for a in assets],
    }


# ── 资产管理 ──────────────────────────────────────

@app.post("/api/assets/register", summary="注册新资产")
def register_asset(request: AssetRegisterRequest, db=Depends(get_db)):
    """
    上传新资产元数据（实际文件通过 P2P 传输）
    """
    # 检查 info_hash 是否已存在
    existing = db.execute(
        "SELECT id FROM assets WHERE info_hash = ?",
        (request.info_hash,)
    ).fetchone()
    
    if existing:
        raise HTTPException(status_code=409, detail="资产已存在")
    
    # 验证分类
    valid_categories = {"model", "material", "shader", "texture", "hdri", "other"}
    if request.category not in valid_categories:
        raise HTTPException(status_code=400, detail=f"无效分类，应为 {valid_categories}")
    
    # 插入资产
    db.execute("""
        INSERT INTO assets 
        (info_hash, name, description, creator_fingerprint, category, tags,
         file_size, blender_version, thumbnail_url, preview_gif_url, magnet_link)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        request.info_hash,
        request.name,
        request.description,
        request.creator_fingerprint,
        request.category,
        json.dumps(request.tags or []),
        request.file_size,
        request.blender_version,
        request.thumbnail_url,
        request.preview_gif_url,
        request.magnet_link,
    ))
    
    # 更新用户上传数
    db.execute(
        "UPDATE users SET uploads = uploads + 1 WHERE fingerprint = ?",
        (request.creator_fingerprint,)
    )
    
    db.commit()
    
    asset = db.execute(
        "SELECT * FROM assets WHERE info_hash = ?",
        (request.info_hash,)
    ).fetchone()
    
    # 上传奖励积分（写入贡献记录）
    upload_points = 50  # 固定上传奖励
    db.execute("""
        INSERT INTO contributions (fingerprint, asset_id, action, points_earned)
        VALUES (?, ?, 'upload', ?)
    """, (request.creator_fingerprint, asset["id"], upload_points))
    db.commit()
    
    return {
        "status": "registered",
        "asset_id": asset["id"],
        "info_hash": request.info_hash,
        "upload_points_earned": upload_points,
    }


@app.get("/api/assets/hot", summary="获取热门资产列表")
def get_hot_assets(
    page: int = 1,
    limit: int = 20,
    category: Optional[str] = None,
    db=Depends(get_db)
):
    """获取按热度排名的资产列表"""
    offset = (page - 1) * limit
    
    if category:
        assets = db.execute("""
            SELECT a.*, u.identity_name as creator_name
            FROM assets a
            LEFT JOIN users u ON a.creator_fingerprint = u.fingerprint
            WHERE a.is_active = TRUE AND a.category = ?
            ORDER BY a.hot_score DESC
            LIMIT ? OFFSET ?
        """, (category, limit, offset)).fetchall()
    else:
        assets = db.execute("""
            SELECT a.*, u.identity_name as creator_name
            FROM assets a
            LEFT JOIN users u ON a.creator_fingerprint = u.fingerprint
            WHERE a.is_active = TRUE
            ORDER BY a.hot_score DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    
    return {
        "page": page,
        "limit": limit,
        "assets": [dict(a) for a in assets],
    }


@app.get("/api/assets/search", summary="搜索资产")
def search_assets(
    q: str,
    category: Optional[str] = None,
    limit: int = 20,
    db=Depends(get_db)
):
    if category:
        assets = db.execute("""
            SELECT a.*, u.identity_name as creator_name
            FROM assets a
            LEFT JOIN users u ON a.creator_fingerprint = u.fingerprint
            WHERE a.is_active = TRUE 
              AND (a.name LIKE ? OR a.description LIKE ? OR a.tags LIKE ?)
              AND a.category = ?
            ORDER BY a.hot_score DESC
            LIMIT ?
        """, (f"%{q}%", f"%{q}%", f"%{q}%", category, limit)).fetchall()
    else:
        assets = db.execute("""
            SELECT a.*, u.identity_name as creator_name
            FROM assets a
            LEFT JOIN users u ON a.creator_fingerprint = u.fingerprint
            WHERE a.is_active = TRUE 
              AND (a.name LIKE ? OR a.description LIKE ? OR a.tags LIKE ?)
            ORDER BY a.hot_score DESC
            LIMIT ?
        """, (f"%{q}%", f"%{q}%", f"%{q}%", limit)).fetchall()
    
    return {"assets": [dict(a) for a in assets], "query": q}


@app.get("/api/assets/{asset_id}/torrent", summary="获取 torrent 文件")
def get_torrent(asset_id: int, db=Depends(get_db)):
    from fastapi.responses import Response
    
    asset = db.execute(
        "SELECT torrent_data, name, info_hash FROM assets WHERE id = ?",
        (asset_id,)
    ).fetchone()
    
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    
    if not asset["torrent_data"]:
        raise HTTPException(status_code=404, detail="Torrent 文件不存在")
    
    return Response(
        content=asset["torrent_data"],
        media_type="application/x-bittorrent",
        headers={"Content-Disposition": f'attachment; filename="{asset["name"]}.torrent"'}
    )


@app.post("/api/assets/{asset_id}/like", summary="点赞资产")
def like_asset(asset_id: int, fingerprint: str, db=Depends(get_db)):
    # 检查是否已点赞
    existing = db.execute(
        "SELECT * FROM likes WHERE fingerprint = ? AND asset_id = ?",
        (fingerprint, asset_id)
    ).fetchone()
    
    if existing:
        # 取消点赞
        db.execute(
            "DELETE FROM likes WHERE fingerprint = ? AND asset_id = ?",
            (fingerprint, asset_id)
        )
        db.execute("UPDATE assets SET likes = likes - 1 WHERE id = ?", (asset_id,))
        db.commit()
        return {"status": "unliked"}
    else:
        db.execute(
            "INSERT INTO likes (fingerprint, asset_id) VALUES (?, ?)",
            (fingerprint, asset_id)
        )
        db.execute("UPDATE assets SET likes = likes + 1 WHERE id = ?", (asset_id,))
        db.commit()
        return {"status": "liked"}


# ── 做种管理 ──────────────────────────────────────

@app.post("/api/seeding/ping", summary="做种节点心跳")
def seeding_ping(request: SeedingPingRequest, db=Depends(get_db)):
    """
    做种节点每 5 分钟 ping 一次
    更新上传量和做种时长
    """
    # 获取或创建做种会话
    asset = db.execute(
        "SELECT id FROM assets WHERE info_hash = ?",
        (request.info_hash,)
    ).fetchone()
    
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    
    session = db.execute("""
        SELECT * FROM seeding_sessions 
        WHERE fingerprint = ? AND info_hash = ? AND is_active = TRUE
    """, (request.fingerprint, request.info_hash)).fetchone()
    
    if not session:
        # 创建新做种会话
        db.execute("""
            INSERT INTO seeding_sessions 
            (fingerprint, asset_id, info_hash, bytes_uploaded, bytes_downloaded)
            VALUES (?, ?, ?, ?, ?)
        """, (
            request.fingerprint,
            asset["id"],
            request.info_hash,
            request.bytes_uploaded,
            request.bytes_downloaded,
        ))
        # 更新资产做种数
        db.execute("UPDATE assets SET seed_count = seed_count + 1 WHERE id = ?", (asset["id"],))
    else:
        # 更新现有会话
        db.execute("""
            UPDATE seeding_sessions 
            SET last_ping = CURRENT_TIMESTAMP,
                bytes_uploaded = ?,
                bytes_downloaded = ?
            WHERE id = ?
        """, (request.bytes_uploaded, request.bytes_downloaded, session["id"]))
    
    db.commit()
    
    return {"status": "ok", "asset_id": asset["id"]}


@app.post("/api/seeding/complete", summary="完成做种汇报")
def seeding_complete(request: SeedingPingRequest, db=Depends(get_db)):
    """
    用户主动停止做种时汇报
    计算积分并记录到账本
    """
    session = db.execute("""
        SELECT s.*, a.file_size, a.downloads as asset_downloads
        FROM seeding_sessions s
        JOIN assets a ON s.asset_id = a.id
        WHERE s.fingerprint = ? AND s.info_hash = ? AND s.is_active = TRUE
    """, (request.fingerprint, request.info_hash)).fetchone()
    
    if not session:
        raise HTTPException(status_code=404, detail="做种会话不存在")
    
    # 计算做种时长
    started = session["started_at"]
    seed_seconds = int(time.time()) - int(time.mktime(
        time.strptime(started, "%Y-%m-%d %H:%M:%S")
    ))
    seed_hours = seed_seconds / 3600
    
    # 计算积分
    file_size_mb = (session["file_size"] or 0) / (1024 * 1024)
    base_points = seed_hours * file_size_mb * 0.01
    upload_bonus = request.bytes_uploaded / (1024 * 1024)  # MB 上传量
    hotness = 1 + (session["asset_downloads"] / 100) * 0.1
    points_earned = int((base_points + upload_bonus) * min(hotness, 2.0))
    
    # 更新会话
    db.execute("""
        UPDATE seeding_sessions
        SET is_active = FALSE, ended_at = CURRENT_TIMESTAMP,
            bytes_uploaded = ?, bytes_downloaded = ?
        WHERE fingerprint = ? AND info_hash = ? AND is_active = TRUE
    """, (
        request.bytes_uploaded, 
        request.bytes_downloaded,
        request.fingerprint,
        request.info_hash,
    ))
    
    # 记录贡献
    db.execute("""
        INSERT INTO contributions 
        (fingerprint, asset_id, action, points_earned, bytes_uploaded, seed_duration_seconds)
        VALUES (?, ?, 'seed', ?, ?, ?)
    """, (
        request.fingerprint,
        session["asset_id"],
        points_earned,
        request.bytes_uploaded,
        seed_seconds,
    ))
    
    # 更新资产做种数
    db.execute(
        "UPDATE assets SET seed_count = MAX(0, seed_count - 1) WHERE id = ?",
        (session["asset_id"],)
    )
    
    # 更新用户积分
    db.execute("""
        UPDATE users SET 
            total_points = total_points + ?,
            seed_hours = seed_hours + ?,
            bytes_uploaded = bytes_uploaded + ?
        WHERE fingerprint = ?
    """, (points_earned, seed_hours, request.bytes_uploaded, request.fingerprint))
    
    db.commit()
    
    return {
        "status": "seeding_completed",
        "seed_hours": round(seed_hours, 2),
        "points_earned": points_earned,
    }


# ── 排行榜 ──────────────────────────────────────

@app.get("/api/leaderboard/users", summary="用户贡献排行榜")
def get_user_leaderboard(limit: int = 50, db=Depends(get_db)):
    users = db.execute("""
        SELECT identity_name, fingerprint, total_points, 
               uploads, seed_hours, bytes_uploaded, reputation_score
        FROM users
        WHERE is_banned = FALSE
        ORDER BY total_points DESC
        LIMIT ?
    """, (limit,)).fetchall()
    
    result = []
    for i, u in enumerate(users, 1):
        result.append({
            "rank": i,
            "identity_name": u["identity_name"],
            "fingerprint_hint": u["fingerprint"][:8] + "...",
            "total_points": u["total_points"],
            "uploads": u["uploads"],
            "seed_hours": round(u["seed_hours"], 1),
            "bytes_uploaded_gb": round(u["bytes_uploaded"] / 1024**3, 2),
            "reputation": u["reputation_score"],
        })
    
    return {"leaderboard": result}


# ── 热度分更新（后台任务）──────────────────────────

@app.post("/internal/update-hot-scores", include_in_schema=False)
def update_hot_scores(db=Depends(get_db)):
    """
    每小时调用一次，更新所有资产的热度分
    （生产环境使用 celery / apscheduler 定时触发）
    """
    assets = db.execute("""
        SELECT id, downloads, likes, seed_count, created_at
        FROM assets WHERE is_active = TRUE
    """).fetchall()
    
    for asset in assets:
        # 计算创建天数
        from datetime import datetime
        created = datetime.strptime(asset["created_at"], "%Y-%m-%d %H:%M:%S")
        days_ago = (datetime.now() - created).days + 0.01
        
        score = calculate_hot_score(
            asset["downloads"],
            asset["likes"],
            asset["seed_count"],
            days_ago,
        )
        
        db.execute(
            "UPDATE assets SET hot_score = ? WHERE id = ?",
            (score, asset["id"])
        )
    
    db.commit()
    return {"updated": len(assets)}


# ==================== 启动 ====================

@app.on_event("startup")
def startup():
    init_db()
    print("[BlendLink Tracker] 数据库初始化完成")
    print(f"[BlendLink Tracker] 数据库路径: {DB_PATH}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
