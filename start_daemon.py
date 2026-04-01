"""
BlendLink 守护进程启动器
===================

用法:
    python start_daemon.py          # 默认
    python start_daemon.py --port 9999
    python start_daemon.py --tracker http://tracker.example.com:8000

提示:
    首次运行会自动检查并安装缺少的依赖（需网络连接）
"""

import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def check_and_install_deps():
    """启动前检查依赖，缺少则尝试自动安装"""
    MISSING = []

    for name, mod in [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("requests", "requests")]:
        try:
            __import__(mod)
        except ImportError:
            MISSING.append(name)

    if not MISSING:
        return True  # 全部已有

    print(f"\n📦 检测到缺少依赖: {', '.join(MISSING)}")
    print("  正在自动安装...")
    try:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install"] + MISSING,
            cwd=SCRIPT_DIR,
        )
        print("  ✓ 安装完成\n")
        return True
    except subprocess.CalledProcessError:
        print("  ✗ 自动安装失败，请手动运行: pip install " + " ".join(MISSING))
        return False


def main():
    # 检查依赖
    if not check_and_install_deps():
        print("无法继续，依赖未就绪。")
        return

    # 解析参数
    port = 6789
    tracker = "http://localhost:8000"

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--port", "-p") and i + 1 < len(args):
            port = int(args[i + 1]); i += 2
        elif arg in ("--tracker", "-t") and i + 1 < len(args):
            tracker = args[i + 1]; i += 2
        elif arg in ("--help", "-h"):
            print(__doc__); return
        else:
            i += 1

    print(f"""
╔═══════════════════════════════════════════╗
║         BlendLink 守护进程 启动中               ║
╠═══════════════════════════════════════════╣
║  API:    http://127.0.0.1:{port}             ║
║  Tracker: {tracker}       ║
║                                           ║
║  打开 Blender → N面板 → BlendLink 使用         ║
║  按 Ctrl+C 停止                           ║
╚═══════════════════════════════════════════╝
""")

    # 启动
    from daemon.main import main as daemon_main
    sys.argv = ["blendlink-daemon", "--port", str(port), "--tracker", tracker]
    daemon_main()


if __name__ == "__main__":
    main()
