"""
BlendLink 守护进程 - 核心入口
=========================

独立后台运行，负责任务：
1. P2P 下载/做种（libtorrent）
2. 硬件指纹身份（无需注册）
3. 本地账本（积分管理）
4. REST API（localhost:6789）供 Blender 插件调用

用法:
    python start_daemon.py
    python -m blendlink.daemon --port 6789 --tracker http://localhost:8000
"""

import argparse
import logging
import os
import sys
import time
import signal
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.hardware_fingerprint import HardwareFingerprint, LocalLedger

# ─── 日志配置 ───────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("blendlink")


# ─── 守护进程核心 ───────────────────────────────────

class DaemonCore:
    """整合 P2P + 身份 + 账本的核心对象"""

    def __init__(self, tracker_url: str, data_dir: str = None):
        self.tracker_url = tracker_url
        self.p2p_client = None
        self._identity = None

        # 数据目录
        if data_dir is None:
            base = os.environ.get("APPDATA") if os.name == "nt" else str(Path.home())
            self.data_dir = Path(base) / "BlendLink"
        else:
            self.data_dir = Path(data_dir)

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_dir = self.data_dir / "ledger"
        self.download_dir = self.data_dir / "downloads"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        # 本地账本
        self.ledger = LocalLedger(ledger_dir=str(self.ledger_dir))
        self._identity = self.ledger.get_identity_info()

        logger.info(f"数据目录: {self.data_dir}")
        logger.info(f"身份: {self._identity.get('identity_name', 'unknown')} "
                    f"({self._identity.get('fingerprint', '')[:12]}...)")

    def get_identity(self) -> dict:
        return self._identity

    def get_status(self) -> dict:
        """返回守护进程状态摘要"""
        points = self.ledger.get_balance().get("total_points", 0)
        seeds = 0
        if self.p2p_client and self.p2p_client.active_assets:
            seeds = len(self.p2p_client.active_assets)
        return {
            "status": "running",
            "identity": self._identity.get("identity_name", "unknown"),
            "points": points,
            "active_seeds": seeds,
            "tracker": self.tracker_url,
        }

    def init_p2p(self):
        """初始化 P2P 客户端"""
        try:
            from client.p2p_client import BlendLinkClient
            self.p2p_client = BlendLinkClient(
                download_dir=str(self.download_dir),
                tracker_url=self.tracker_url,
                fingerprint=self._identity["fingerprint"],
            )
            logger.info("✓ P2P 引擎已就绪（libtorrent）")
        except ImportError:
            logger.warning("⚠ libtorrent 未安装，P2P 功能暂不可用")
            logger.warning("  运行 python install.py 安装")
            self.p2p_client = None
        except Exception as e:
            logger.error(f"P2P 初始化失败: {e}")
            self.p2p_client = None

    def shutdown(self):
        logger.info("正在关闭守护进程...")
        if self.p2p_client:
            self.p2p_client.shutdown()
        logger.info("✓ 守护进程已停止")


# ─── REST API 挂载 ──────────────────────────────────

def mount_api(core: DaemonCore):
    """将 DaemonCore 挂载到 FastAPI app 的 state 上"""
    from fastapi import FastAPI
    from daemon import api

    app = api.app
    app.state.daemon_core = core

    # 让每个请求都能访问 core
    async def get_core(request):
        return request.app.state.daemon_core

    app.dependency_overrides[api.get_core] = get_core
    return app


# ─── Tracker 注册 ───────────────────────────────────

def register_with_tracker(core: DaemonCore):
    """向 Tracker 注册本节点"""
    import requests

    identity = core.get_identity()
    try:
        resp = requests.post(
            f"{core.tracker_url}/api/users/register",
            json={
                "fingerprint": identity["fingerprint"],
                "peer_id": identity["peer_id"],
                "public_key": identity.get("public_key", ""),
                "identity_name": identity["identity_name"],
            },
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info(f"✓ Tracker 注册成功: {identity['identity_name']}")
        else:
            logger.warning(f"Tracker 注册失败: {resp.status_code}")
    except requests.ConnectionError:
        logger.warning(f"⚠ 无法连接 Tracker ({core.tracker_url})，将在网络恢复后重试")
    except Exception as e:
        logger.warning(f"注册失败: {e}")


# ─── 主入口 ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BlendLink 守护进程 - 去中心化 Blender 资产库")
    parser.add_argument("--port", "-p", type=int, default=6789,
                        help="本地 API 端口 (默认 6789)")
    parser.add_argument("--tracker", "-t", type=str,
                        default="http://localhost:8000",
                        help="Tracker 服务器 (默认 http://localhost:8000)")
    parser.add_argument("--data-dir", "-d", type=str, default=None,
                        help="数据存储目录")
    args = parser.parse_args()

    # 横幅
    print(f"""
╔═══════════════════════════════════════════╗
║        BlendLink v0.2.0  守护进程          ║
║   Blender Decentralized Asset Library     ║
╠═══════════════════════════════════════════╣
║  身份    │ 硬件指纹（无需注册）             ║
║  积分    │ 本地账本 + Tracker 验证         ║
║  P2P     │ libtorrent                     ║
║  API     │ http://127.0.0.1:{args.port}        ║
║  Tracker │ {args.tracker}   ║
╚═══════════════════════════════════════════╝
""")

    # 初始化
    core = DaemonCore(tracker_url=args.tracker, data_dir=args.data_dir)
    core.init_p2p()

    # 挂载 API
    app = mount_api(core)

    # 注册到 Tracker
    register_with_tracker(core)

    # 信号处理（优雅退出）
    def signal_handler(sig, frame):
        logger.info("收到终止信号...")
        core.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动 FastAPI
    import uvicorn
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
