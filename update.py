"""
BlendLink 一键升级脚本
====================

从 GitHub Release 检查并下载最新版本，覆盖更新后提示重启守护进程。

用法:
    python update.py              # 检查并升级到最新版
    python update.py --check      # 仅检查，不下载
    python update.py --force      # 强制重新安装当前版本

GitHub: https://github.com/youyooo/BlendLink
"""

import sys
import os
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ==================== 配置 ====================

GITHUB_REPO = "youyooo/BlendLink"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"
BACKUP_DIR = ".blendlink_backup"


# ==================== 工具函数 ====================

def info(msg):
    print(f"[INFO] {msg}")


def ok(msg):
    print(f"[OK] {msg}")


def warn(msg):
    print(f"[WARN] {msg}")


def fail(msg):
    print(f"[FAIL] {msg}")


def get_project_root():
    """获取项目根目录"""
    return Path(__file__).resolve().parent


def get_local_version():
    """从 version.py 读取本地版本号"""
    version_file = get_project_root() / "version.py"
    if not version_file.exists():
        return "0.0.0"
    content = version_file.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    return match.group(1) if match else "0.0.0"


def parse_version(version_str):
    """解析版本号为可比较的元组 (major, minor, patch)"""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version_str)
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())


def compare_versions(v1, v2):
    """比较两个版本号: 1=v1>v2, 0=equal, -1=v1<v2"""
    t1, t2 = parse_version(v1), parse_version(v2)
    if t1 > t2:
        return 1
    elif t1 < t2:
        return -1
    return 0


# ==================== GitHub API ====================

def github_get(url):
    """请求 GitHub API，返回 JSON"""
    req = Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "BlendLink-Updater",
    })
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 403:
            fail("GitHub API 速率限制，请稍后重试")
        elif e.code == 404:
            fail("仓库不存在或没有 Release")
        else:
            fail(f"GitHub API 错误: {e.code}")
        return None
    except URLError:
        fail("无法连接 GitHub，请检查网络")
        return None


def get_latest_release():
    """获取 GitHub 最新 Release 信息"""
    info("正在检查最新版本...")
    data = github_get(f"{GITHUB_API}/releases/latest")
    if not data:
        return None
    return {
        "tag": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "version": data.get("tag_name", "").lstrip("v"),
        "url": data.get("html_url", ""),
        "body": data.get("body", ""),
        "assets": [
            {
                "name": a["name"],
                "url": a["browser_download_url"],
                "size": a["size"],
            }
            for a in data.get("assets", [])
        ],
        "zipball_url": data.get("zipball_url", ""),
        "tarball_url": data.get("tarball_url", ""),
        "published_at": data.get("published_at", ""),
    }


# ==================== 更新逻辑 ====================

def download_file(url, dest_path):
    """下载文件到指定路径"""
    req = Request(url, headers={"User-Agent": "BlendLink-Updater"})
    try:
        with urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 8192
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 // total
                        mb = downloaded / 1024 / 1024
                        total_mb = total / 1024 / 1024
                        print(f"\r  下载中: {mb:.1f}/{total_mb:.1f} MB ({pct}%)", end="", flush=True)
            print()  # 换行
            return True
    except Exception as e:
        fail(f"下载失败: {e}")
        return False


def backup_current(root):
    """备份当前代码"""
    backup_path = root / BACKUP_DIR
    if backup_path.exists():
        shutil.rmtree(backup_path)

    # 需要备份的目录和文件
    items_to_backup = [
        "daemon", "client", "shared", "tracker",
        "blender_addon",
        "version.py", "install.py", "start_daemon.py",
        "pyproject.toml",
    ]

    backup_path.mkdir(exist_ok=True)
    backed_up = 0

    for item in items_to_backup:
        src = root / item
        dst = backup_path / item
        if src.exists():
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            backed_up += 1

    ok(f"已备份 {backed_up} 个文件/目录到 {BACKUP_DIR}/")
    return backup_path


def apply_update(root, archive_path, is_zip=True):
    """解压更新包并覆盖代码文件"""
    # 解压到临时目录
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        info(f"正在解压更新包...")
        if is_zip:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(tmpdir)
        else:
            shutil.unpack_archive(archive_path, tmpdir)

        # GitHub zipball 结构: BlendLink-<hash>/<files>
        # 找到实际的代码目录
        extracted_dirs = [d for d in tmpdir.iterdir() if d.is_dir()]
        if not extracted_dirs:
            fail("解压后未找到代码目录")
            return False

        src_dir = extracted_dirs[0]
        info(f"更新来源: {src_dir.name}")

        # 需要更新的文件/目录
        items_to_update = [
            "daemon", "client", "shared", "tracker",
            "blender_addon",
            "version.py", "install.py", "start_daemon.py",
            "pyproject.toml", "update.py",
        ]

        updated = 0
        for item in items_to_update:
            src = src_dir / item
            dst = root / item
            if not src.exists():
                continue

            # 删除旧版本
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()

            # 复制新版本
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            updated += 1

        ok(f"已更新 {updated} 个文件/目录")
        return True


def check_daemon_running():
    """检测守护进程是否在运行"""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", 6789))
        sock.close()
        return result == 0
    except Exception:
        return False


def rollback(root):
    """回滚到备份版本"""
    backup_path = root / BACKUP_DIR
    if not backup_path.exists():
        fail("没有可用的备份")
        return False

    warn("正在回滚到更新前的版本...")
    items = [d for d in backup_path.iterdir()]
    for src in items:
        dst = root / src.name
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    ok("回滚完成")
    return True


# ==================== 主流程 ====================

def main():
    root = get_project_root()
    args = sys.argv[1:]

    check_only = "--check" in args
    force = "--force" in args

    print("""
+=========================================+
|       BlendLink Updater v0.3.0          |
|   One-click update from GitHub Release  |
+=========================================+
""")

    # 1. 获取本地版本
    local_ver = get_local_version()
    info(f"当前版本: v{local_ver}")

    # 2. 获取最新 Release
    release = get_latest_release()
    if not release:
        sys.exit(1)

    latest_ver = release["version"]
    info(f"最新版本: v{latest_ver}")

    # 3. 比较版本
    cmp = compare_versions(local_ver, latest_ver)
    if cmp == 0 and not force:
        ok("已经是最新版本!")
        return
    elif cmp > 0 and not force:
        warn(f"本地版本 (v{local_ver}) 比远程 (v{latest_ver}) 更新，跳过")
        return

    if check_only:
        print()
        info(f"有新版本可用: v{latest_ver}")
        info(f"发布地址: {release['url']}")
        if release.get("body"):
            print()
            print("--- 更新内容 ---")
            # 只显示前 10 行
            lines = release["body"].strip().split("\n")[:10]
            for line in lines:
                print(f"  {line}")
            if len(release["body"].strip().split("\n")) > 10:
                print("  ...")
        return

    # 4. 下载
    # 优先下载 Source code (zip)，因为 Release asset 可能不存在
    download_url = None
    is_zip = True

    # 检查是否有 Release asset
    if release["assets"]:
        # 找到 source code zip
        for asset in release["assets"]:
            if asset["name"].endswith(".zip") and "source" in asset["name"].lower():
                download_url = asset["url"]
                break
            elif asset["name"].endswith(".zip"):
                download_url = asset["url"]

    if not download_url and release["zipball_url"]:
        download_url = release["zipball_url"]
        is_zip = False  # tar.gz

    if not download_url:
        fail("没有找到可下载的更新包")
        sys.exit(1)

    print()
    info(f"准备下载: v{local_ver} -> v{latest_ver}")

    # 5. 备份
    backup_current(root)

    # 6. 下载到临时文件
    with tempfile.NamedTemporaryFile(suffix=".zip" if is_zip else ".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    if not download_file(download_url, tmp_path):
        # 下载失败，回滚
        warn("下载失败，正在恢复备份...")
        rollback(root)
        sys.exit(1)

    # 7. 应用更新
    print()
    if not apply_update(root, tmp_path, is_zip):
        warn("更新失败，正在恢复备份...")
        rollback(root)
        sys.exit(1)

    # 8. 清理
    try:
        os.unlink(tmp_path)
    except Exception:
        pass

    # 9. 完成
    print(f"""
+=========================================+
|           更新完成!                      |
+=========================================+
|  v{local_ver} -> v{latest_ver:<30}|
+=========================================+
|  更新内容: {release['url']:<29}|
""")

    # 检查守护进程
    if check_daemon_running():
        warn("检测到守护进程正在运行，请手动重启:")
        warn("  1. 按 Ctrl+C 停止当前守护进程")
        warn("  2. 运行: python start_daemon.py")
        print()
        info("或者直接运行: python start_daemon.py (会自动使用新版本)")
    else:
        ok("运行以下命令启动新版本:")
        print(f"  python start_daemon.py")

    print()


if __name__ == "__main__":
    main()
