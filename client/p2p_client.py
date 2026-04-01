"""
BlendLink P2P 客户端节点

负责：
1. libtorrent session 管理
2. 下载 / 做种资产
3. 强制做种检查
4. 积分上报
"""

import hashlib
import libtorrent as lt
import time
import json
import os
import threading
import requests
from pathlib import Path
from typing import Dict, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum


class SeedingStatus(Enum):
    NOT_STARTED = "not_started"
    SEEDING_REQUIRED = "seeding_required"   # 强制做种中
    SEEDING_OPTIONAL = "seeding_optional"   # 自愿做种
    SEEDING_COMPLETE = "seeding_complete"   # 已满足要求
    PAUSED = "paused"


# ==================== 做种要求配置 ====================

SEEDING_REQUIREMENTS = {
    # 必须满足其中一个条件
    "min_seed_hours": 24,          # 做种至少 24 小时
    "min_upload_bytes": 100 * 1024 * 1024,  # 或上传至少 100MB
}

PING_INTERVAL = 5 * 60  # 每 5 分钟 ping Tracker 一次


@dataclass
class AssetHandle:
    """资产句柄（封装 libtorrent handle）"""
    asset_id: int
    info_hash: str
    name: str
    download_path: str
    torrent_handle: any = None  # libtorrent handle
    
    # 做种状态
    seeding_status: SeedingStatus = SeedingStatus.NOT_STARTED
    seed_start_time: float = 0.0
    bytes_uploaded: int = 0
    bytes_downloaded: int = 0
    
    # 进度
    download_progress: float = 0.0
    download_speed: int = 0  # bytes/s
    upload_speed: int = 0    # bytes/s
    num_peers: int = 0
    
    def seed_hours(self) -> float:
        if self.seed_start_time == 0:
            return 0.0
        return (time.time() - self.seed_start_time) / 3600
    
    def meets_seeding_requirement(self) -> bool:
        return (
            self.seed_hours() >= SEEDING_REQUIREMENTS["min_seed_hours"]
            or self.bytes_uploaded >= SEEDING_REQUIREMENTS["min_upload_bytes"]
        )
    
    def seeding_progress(self) -> float:
        """返回 0-1 做种进度"""
        hour_progress = self.seed_hours() / SEEDING_REQUIREMENTS["min_seed_hours"]
        upload_progress = self.bytes_uploaded / SEEDING_REQUIREMENTS["min_upload_bytes"]
        return min(1.0, max(hour_progress, upload_progress))
    
    def format_seeding_status(self) -> str:
        if self.seeding_status == SeedingStatus.SEEDING_REQUIRED:
            if self.seed_hours() < SEEDING_REQUIREMENTS["min_seed_hours"]:
                remaining = SEEDING_REQUIREMENTS["min_seed_hours"] - self.seed_hours()
                return f"强制做种中 - 剩余 {remaining:.1f} 小时"
            else:
                remaining_mb = (
                    SEEDING_REQUIREMENTS["min_upload_bytes"] - self.bytes_uploaded
                ) / 1024 / 1024
                return f"强制做种中 - 再上传 {remaining_mb:.1f} MB"
        elif self.seeding_status == SeedingStatus.SEEDING_COMPLETE:
            return f"做种完成 ✓ (已上传 {self.bytes_uploaded / 1024 / 1024:.1f} MB)"
        return self.seeding_status.value


class BlendLinkClient:
    """
    BlendLink P2P 客户端
    
    管理 libtorrent session，处理下载/做种
    """
    
    def __init__(
        self,
        download_dir: str,
        tracker_url: str,
        fingerprint: str,
        on_progress: Optional[Callable] = None,
    ):
        """
        Args:
            download_dir: 资产下载目录
            tracker_url: Tracker 服务器地址
            fingerprint: 用户硬件指纹
            on_progress: 进度回调 (asset_handle) -> None
        """
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
        self.tracker_url = tracker_url
        self.fingerprint = fingerprint
        self.on_progress = on_progress
        
        # libtorrent session
        self.session = self._create_session()
        
        # 活跃资产
        self.active_assets: Dict[str, AssetHandle] = {}  # info_hash → handle
        
        # 后台线程
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="blendlink_monitor"
        )
        self._monitor_thread.start()
        
        # 做种状态持久化
        self.state_file = self.download_dir / ".blendlink_state.json"
        self._load_state()
    
    def _create_session(self) -> lt.session:
        """创建 libtorrent session"""
        settings = {
            # 网络端口
            "listen_interfaces": "0.0.0.0:6881",
            
            # 速率限制（防止占满带宽）
            "upload_rate_limit": 5 * 1024 * 1024,    # 5 MB/s 上传限制
            "download_rate_limit": 0,                  # 不限下载
            
            # DHT 设置
            "enable_dht": True,
            "enable_lsd": True,     # 局域网发现
            "enable_upnp": True,
            "enable_natpmp": True,
            
            # 连接设置
            "connections_limit": 200,
            "peer_timeout": 30,
            
            # 缓存设置
            "cache_size": 512,  # 512 * 16 KB = 8 MB 缓存
        }
        
        sess = lt.session(settings)
        
        # 添加 DHT 路由节点
        sess.add_dht_router("router.bittorrent.com", 6881)
        sess.add_dht_router("router.utorrent.com", 6881)
        sess.add_dht_router("dht.transmissionbt.com", 6881)
        
        return sess
    
    def download_asset(
        self,
        asset_id: int,
        asset_name: str,
        torrent_data: bytes = None,
        magnet_link: str = None,
    ) -> AssetHandle:
        """
        开始下载资产
        
        Args:
            asset_id: 资产 ID
            asset_name: 资产名称
            torrent_data: .torrent 文件二进制
            magnet_link: magnet URI
            
        Returns:
            AssetHandle: 资产句柄
        """
        if torrent_data is None and magnet_link is None:
            raise ValueError("必须提供 torrent_data 或 magnet_link")
        
        # 资产保存目录
        asset_dir = self.download_dir / f"asset_{asset_id}_{asset_name[:20]}"
        asset_dir.mkdir(exist_ok=True)
        
        # 添加到 libtorrent session
        add_params = lt.add_torrent_params()
        add_params.save_path = str(asset_dir)
        
        if torrent_data:
            ti = lt.torrent_info(lt.bdecode(torrent_data))
            add_params.ti = ti
            info_hash = str(ti.info_hash())
        else:
            add_params.url = magnet_link
            # magnet link 的 info_hash 从 URL 解析
            info_hash = self._extract_info_hash_from_magnet(magnet_link)
        
        handle = self.session.add_torrent(add_params)
        
        asset_handle = AssetHandle(
            asset_id=asset_id,
            info_hash=info_hash,
            name=asset_name,
            download_path=str(asset_dir),
            torrent_handle=handle,
            seeding_status=SeedingStatus.NOT_STARTED,
        )
        
        self.active_assets[info_hash] = asset_handle
        return asset_handle
    
    def _monitor_loop(self):
        """
        后台监控循环
        
        每秒更新进度，每 5 分钟 ping Tracker
        """
        last_ping = 0
        
        while self._running:
            now = time.time()
            
            for info_hash, asset_handle in list(self.active_assets.items()):
                handle = asset_handle.torrent_handle
                if not handle or not handle.is_valid():
                    continue
                
                # 获取状态
                status = handle.status()
                
                # 更新进度
                asset_handle.download_progress = status.progress
                asset_handle.download_speed = status.download_rate
                asset_handle.upload_speed = status.upload_rate
                asset_handle.num_peers = status.num_peers
                asset_handle.bytes_uploaded = status.all_time_upload
                asset_handle.bytes_downloaded = status.all_time_download
                
                # 下载完成 → 切换为做种模式
                if (status.state == lt.torrent_status.seeding and 
                    asset_handle.seeding_status == SeedingStatus.NOT_STARTED):
                    
                    asset_handle.seeding_status = SeedingStatus.SEEDING_REQUIRED
                    asset_handle.seed_start_time = time.time()
                    print(f"[BlendLink] {asset_handle.name} 下载完成，开始强制做种 24 小时...")
                
                # 检查是否满足做种要求
                if (asset_handle.seeding_status == SeedingStatus.SEEDING_REQUIRED and
                    asset_handle.meets_seeding_requirement()):
                    
                    asset_handle.seeding_status = SeedingStatus.SEEDING_COMPLETE
                    print(f"[BlendLink] {asset_handle.name} 做种要求已满足 ✓")
                    self._report_seeding_complete(asset_handle)
                
                # 触发进度回调
                if self.on_progress:
                    self.on_progress(asset_handle)
            
            # 每 5 分钟 ping Tracker
            if now - last_ping >= PING_INTERVAL:
                self._ping_tracker_all()
                last_ping = now
                self._save_state()
            
            time.sleep(1)
    
    def _ping_tracker_all(self):
        """向 Tracker 汇报所有活跃做种的状态"""
        for info_hash, asset_handle in self.active_assets.items():
            if asset_handle.seeding_status in (
                SeedingStatus.SEEDING_REQUIRED,
                SeedingStatus.SEEDING_OPTIONAL,
                SeedingStatus.SEEDING_COMPLETE,
            ):
                try:
                    requests.post(
                        f"{self.tracker_url}/api/seeding/ping",
                        json={
                            "fingerprint": self.fingerprint,
                            "info_hash": info_hash,
                            "bytes_uploaded": asset_handle.bytes_uploaded,
                            "bytes_downloaded": asset_handle.bytes_downloaded,
                        },
                        timeout=10,
                    )
                except Exception as e:
                    print(f"[BlendLink] Ping 失败 ({info_hash[:8]}): {e}")
    
    def _report_seeding_complete(self, asset_handle: AssetHandle):
        """汇报做种完成，获取积分"""
        try:
            resp = requests.post(
                f"{self.tracker_url}/api/seeding/complete",
                json={
                    "fingerprint": self.fingerprint,
                    "info_hash": asset_handle.info_hash,
                    "bytes_uploaded": asset_handle.bytes_uploaded,
                    "bytes_downloaded": asset_handle.bytes_downloaded,
                },
                timeout=10,
            )
            data = resp.json()
            print(f"[BlendLink] 做种完成奖励: {data.get('points_earned', 0)} 积分")
        except Exception as e:
            print(f"[BlendLink] 汇报做种失败: {e}")
    
    def force_delete_asset(self, info_hash: str) -> bool:
        """
        删除本地资产（仅允许在完成做种后）
        
        Returns:
            bool: 是否允许删除
        """
        asset_handle = self.active_assets.get(info_hash)
        if not asset_handle:
            return False
        
        # 检查是否完成做种
        if asset_handle.seeding_status not in (
            SeedingStatus.SEEDING_COMPLETE,
            SeedingStatus.SEEDING_OPTIONAL,
        ):
            print(f"[BlendLink] 无法删除：{asset_handle.name} 尚未完成强制做种")
            print(f"       进度: {asset_handle.seeding_progress() * 100:.1f}%")
            return False
        
        # 从 session 移除
        if asset_handle.torrent_handle:
            self.session.remove_torrent(
                asset_handle.torrent_handle, 
                option=lt.session_handle.delete_files
            )
        
        del self.active_assets[info_hash]
        self._save_state()
        return True
    
    def get_all_assets_status(self) -> list:
        """获取所有资产的状态列表"""
        result = []
        for info_hash, asset_handle in self.active_assets.items():
            result.append({
                "asset_id": asset_handle.asset_id,
                "name": asset_handle.name,
                "info_hash": info_hash[:8] + "...",
                "download_progress": f"{asset_handle.download_progress * 100:.1f}%",
                "seeding_status": asset_handle.format_seeding_status(),
                "seeding_progress": f"{asset_handle.seeding_progress() * 100:.1f}%",
                "upload_speed": f"{asset_handle.upload_speed / 1024:.1f} KB/s",
                "download_speed": f"{asset_handle.download_speed / 1024:.1f} KB/s",
                "peers": asset_handle.num_peers,
            })
        return result
    
    def _extract_info_hash_from_magnet(self, magnet: str) -> str:
        """从 magnet URI 提取 info_hash"""
        import re
        match = re.search(r'btih:([a-fA-F0-9]{40})', magnet)
        if match:
            return match.group(1).lower()
        return hashlib.sha256(magnet.encode()).hexdigest()[:40]
    
    def _save_state(self):
        """持久化保存做种状态"""
        state = {}
        for info_hash, asset_handle in self.active_assets.items():
            state[info_hash] = {
                "asset_id": asset_handle.asset_id,
                "name": asset_handle.name,
                "download_path": asset_handle.download_path,
                "seeding_status": asset_handle.seeding_status.value,
                "seed_start_time": asset_handle.seed_start_time,
                "bytes_uploaded": asset_handle.bytes_uploaded,
            }
        
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def _load_state(self):
        """从文件恢复做种状态"""
        if not self.state_file.exists():
            return
        
        with open(self.state_file, 'r') as f:
            state = json.load(f)
        
        for info_hash, data in state.items():
            # 检查本地文件是否还在
            if not os.path.exists(data["download_path"]):
                continue
            
            asset_handle = AssetHandle(
                asset_id=data["asset_id"],
                info_hash=info_hash,
                name=data["name"],
                download_path=data["download_path"],
                seeding_status=SeedingStatus(data["seeding_status"]),
                seed_start_time=data["seed_start_time"],
                bytes_uploaded=data["bytes_uploaded"],
            )
            
            self.active_assets[info_hash] = asset_handle
            print(f"[BlendLink] 恢复做种: {data['name']} ({asset_handle.format_seeding_status()})")
    
    def shutdown(self):
        """关闭客户端"""
        self._running = False
        self._save_state()
        self._ping_tracker_all()  # 最后一次 ping
        print("[BlendLink] 客户端已关闭")


if __name__ == "__main__":
    print("=== BlendLink P2P 客户端测试 ===")
    print("（注：需要安装 libtorrent-python 才能真正运行）")
    print()
    print("做种要求:")
    print(f"  • 最少做种时长: {SEEDING_REQUIREMENTS['min_seed_hours']} 小时")
    print(f"  • 最少上传量: {SEEDING_REQUIREMENTS['min_upload_bytes'] / 1024 / 1024:.0f} MB")
    print()
    print("设计原则:")
    print("  1. 下载完成 → 自动进入强制做种模式")
    print("  2. 满足要求前 → 不允许删除本地副本")
    print("  3. 满足要求后 → 自动上报 Tracker 领取积分")
    print("  4. 自愿继续做种 → 持续积累积分")
    print("  5. 每 5 分钟 ping Tracker 更新状态")
