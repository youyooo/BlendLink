"""
BlendLink 一键安装脚本
==================

自动检测并安装所有依赖项，支持 Windows / macOS / Linux。
运行一次即可，后续直接用 start_daemon.py 启动。

用法:
    python install.py          # 默认安装
    python install.py --only-deps    # 仅安装依赖，不启动

依赖列表:
    必需: fastapi, uvicorn[standard], requests, libtorrent
    可选: Pillow (缩略图支持)
"""

import sys
import os
import subprocess
import platform
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def get_local_version():
    """读取 version.py 中的版本号"""
    try:
        from version import __version__
        return __version__
    except ImportError:
        return "0.2.0"

# 彩色输出（兼容 Windows GBK 控制台）
def green(msg): return f"[OK] {msg}"
def red(msg):   return f"[FAIL] {msg}"
def yellow(msg): return f"[WARN] {msg}"
def cyan(msg):  return f"[INFO] {msg}"


# ==================== 平台检测 ====================

def get_platform():
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    return system


def get_python():
    return sys.executable


# ==================== 依赖信息 ====================

REQUIRED = ["fastapi", "uvicorn[standard]", "requests"]
OPTIONAL = ["Pillow"]

# libtorrent 包名在不同平台不同
LIBTORRENT_PACKAGES = {
    "windows": "python-libtorrent",
    "macos": "python-libtorrent",
    "linux": "python3-libtorrent",
}

SYSTEM_PACKAGES = {
    "windows": [],  # pip 即可
    "macos": ["libtorrent-python"],  # brew install libtorrent-python
    "linux": ["libtorrent-python", "libtorrent-dev", "libsodium-dev"],
}


# ==================== 安装逻辑 ====================

def run_cmd(cmd, shell=False, check=True, capture=False):
    """执行命令，返回 (success, stdout+stderr)"""
    print(f"  执行: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    try:
        if capture:
            result = subprocess.run(
                cmd, shell=shell, check=check,
                capture_output=True, text=True
            )
            return result.returncode == 0, result.stdout + result.stderr
        else:
            subprocess.run(cmd, shell=shell, check=check)
            return True, ""
    except subprocess.CalledProcessError as e:
        return False, str(e)


def install_pip_packages(packages, upgrade=False):
    """通过 pip 安装包"""
    pkgs = packages[:]
    if upgrade:
        pkgs = [f"{p} --upgrade" for p in pkgs]
    cmd = [get_python(), "-m", "pip", "install"] + pkgs
    return run_cmd(cmd)


def check_package_available(package):
    """检查包是否可导入"""
    mod_name = package.replace("-", "_").lower()
    # 特殊处理
    if "libtorrent" in package:
        mod_name = "libtorrent"
    elif "pillow" in package:
        mod_name = "pil"
    try:
        __import__(mod_name)
        return True
    except ImportError:
        return False


def ensure_pip():
    """确保 pip 可用"""
    print(cyan("[1/5] 检查 pip..."))
    try:
        subprocess.run([get_python(), "-m", "pip", "--version"], check=True, capture_output=True)
        print(green("  [OK] pip 可用"))
        return True
    except subprocess.CalledProcessError:
        print(yellow("  ! pip 不可用，尝试安装..."))
        try:
            subprocess.run([get_python(), "-m", "ensurepip"], check=True, capture_output=True)
            return True
        except:
            print(red("  [FAIL] pip 安装失败，请手动安装"))
            return False


def install_system_dependencies(platform_type):
    """安装系统级依赖（仅 Linux/macOS 需要）"""
    if platform_type == "windows":
        return True, "Windows: 无需系统依赖"

    print(cyan(f"[2/5] 安装系统依赖 ({platform_type})..."))

    if platform_type == "macos":
        print(yellow("  请确保已安装 Homebrew: https://brew.sh"))
        print(yellow("  需要手动执行: brew install libtorrent-python"))
        print("  或者: pip install python-libtorrent")
        return True, "macOS 提示已给出"

    elif platform_type == "linux":
        pkg_install_cmd = None
        # 检测包管理器
        for pm, cmd in [
            ("apt", ["sudo", "apt", "install", "-y"]),
            ("yum", ["sudo", "yum", "install", "-y"]),
            ("dnf", ["sudo", "dnf", "install", "-y"]),
        ]:
            if shutil.which(pm):
                pkg_install_cmd = cmd
                break

        if not pkg_install_cmd:
            return True, "未检测到包管理器，跳过系统依赖"

        packages = SYSTEM_PACKAGES["linux"]
        if packages:
            full_cmd = pkg_install_cmd + packages
            print(f"  {' '.join(full_cmd)}")
            success, _ = run_cmd(full_cmd, capture=True)
            if success:
                return True, "系统依赖安装成功"
            else:
                print(yellow("  系统依赖安装失败，继续尝试 pip 安装"))
                return True, "继续 pip 安装"
        return True, "无需系统依赖"


def install_python_dependencies(platform_type, packages, optional=False):
    """安装 Python 包"""
    label = "可选" if optional else "必需"
    status = "in_progress" if not optional else "optional"

    to_install = []
    for pkg in packages:
        if check_package_available(pkg):
            print(green(f"  [OK] {pkg} 已安装"))
        else:
            to_install.append(pkg)
            print(f"  -> 需安装: {pkg}")

    if not to_install:
        return True

    print(cyan(f"  安装 {label} 包: {', '.join(to_install)}"))
    success, output = install_pip_packages(to_install)

    if success:
        print(green(f"  [OK] {', '.join(to_install)} 安装成功"))
    else:
        if optional:
            print(yellow(f"  [WARN] 可选包安装失败: {output}"))
            return True  # 可选包失败不阻断
        else:
            print(red(f"  [FAIL] {', '.join(to_install)} 安装失败"))
            print(output)
            return False
    return True


def install_libtorrent(platform_type):
    """安装 libtorrent（最麻烦的依赖）"""
    print(cyan("[4/5] 安装 libtorrent (P2P 核心)..."))

    if check_package_available("libtorrent"):
        print(green("  [OK] libtorrent 已安装"))
        return True

    # 方案1: 直接 pip 安装
    pip_package = LIBTORRENT_PACKAGES.get(platform_type, "python-libtorrent")
    print(f"  尝试 pip install {pip_package}...")
    success, output = install_pip_packages([pip_package])
    if success and check_package_available("libtorrent"):
        print(green(f"  [OK] {pip_package} 安装成功!"))
        return True

    # 方案2: 平台特定
    if platform_type == "windows":
        # Windows 尝试不同版本
        for variant in ["python-libtorrent", "libtorrent", "lb", "pylibtorrent"]:
            print(f"  尝试 {variant}...")
            success, _ = install_pip_packages([variant])
            if success and check_package_available("libtorrent"):
                print(green(f"  [OK] {variant} 安装成功!"))
                return True

    elif platform_type == "macos":
        print(yellow("  macOS 推荐使用 conda 安装 libtorrent:"))
        print("    conda install -c conda-forge python-libtorrent")
        print("  或使用 homebrew:")
        print("    brew install libtorrent-python")

    elif platform_type == "linux":
        print(yellow("  Linux 推荐使用系统包管理器:"))
        print("    sudo apt install python3-libtorrent    # Ubuntu/Debian")
        print("    sudo yum install python3-libtorrent    # CentOS/RHEL")
        print("  或 conda:")
        print("    conda install -c conda-forge python-libtorrent")

    print(yellow("  [WARN] libtorrent 安装失败，P2P 功能将不可用"))
    print("  但守护进程仍可运行，可通过界面管理资产")
    return False  # libtorrent 失败不阻断主流程


def verify_installation():
    """验证安装"""
    print(cyan("[5/5] 验证安装..."))

    all_ok = True
    for pkg, mod in [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("requests", "requests")]:
        if check_package_available(pkg):
            print(green(f"  [OK] {pkg}"))
        else:
            print(red(f"  [FAIL] {pkg} 未安装"))
            all_ok = False

    lt_ok = check_package_available("libtorrent")
    if lt_ok:
        print(green("  [OK] libtorrent (P2P 已就绪)"))
    else:
        print(yellow("  [WARN] libtorrent (P2P 暂不可用)"))

    return all_ok


# ==================== 主流程 ====================

def main():
    plat = get_platform()
    print(f"""
+=================================================+
|          BlendLink Install v{get_local_version()}            |
|   Blender Decentralized Asset Library          |
|   Platform: {plat:<37}|
+=================================================+
""")

    # Step 1: pip
    if not ensure_pip():
        sys.exit(1)

    # Step 2: 系统依赖
    install_system_dependencies(plat)

    # Step 3: 必需 Python 包
    print(cyan("[3/5] 安装必需 Python 包..."))
    if not install_python_dependencies(plat, REQUIRED):
        print(red("\n[FAIL] 必需依赖安装失败!"))
        sys.exit(1)

    # Step 4: libtorrent
    install_libtorrent(plat)

    # Step 5: 验证
    verify_installation()

    print(f"""
+=================================================+
|               Install Complete!                  |
+=================================================+
|  Next step:                                      |
|    python start_daemon.py  <- Start daemon      |
|                                                  |
|  Then open Blender -> N Panel -> BlendLink       |
+=================================================+
""")


if __name__ == "__main__":
    main()
