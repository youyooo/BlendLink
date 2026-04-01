"""
BlendLink 依赖安装脚本

在 Blender 的 Python 环境中安装所需依赖
在 Blender Scripting 窗口中运行此脚本
"""
import subprocess
import sys

REQUIRED_PACKAGES = [
    "fastapi",
    "uvicorn",
    "requests",
    "libtorrent",
    "cryptography",
    "Pillow",
]

def install_packages():
    python = sys.executable
    for pkg in REQUIRED_PACKAGES:
        print(f"正在安装 {pkg}...")
        try:
            subprocess.check_call([python, "-m", "pip", "install", pkg])
            print(f"✓ {pkg} 安装成功")
        except subprocess.CalledProcessError:
            print(f"✗ {pkg} 安装失败")

if __name__ == "__main__":
    install_packages()
    print("\n所有依赖安装完成！")
    print("请重启 Blender 并在 Preferences > Add-ons 中启用 BlendLink 插件")
