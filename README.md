# BlendLink - Blender Decentralized Asset Library

**去中心化 P2P Blender 资产库** · 开源免费 · MIT 许可证

> 核心理念：下载即做种，贡献即排名，硬件即身份

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## 🚀 一键安装

```bash
cd blendlink/
python install.py    # 自动安装所有依赖（含 libtorrent）

# 启动
python start_daemon.py
```

然后打开 **Blender → Preferences → Add-ons → Install**，选择 `blender_addon/` 目录。

---

## 架构（v0.2.0 - 守护进程 + 轻量插件）

```
┌─────────────────────────────────────────────┐
│  守护进程层 (手动启动，常驻后台)              │
│  daemon/main.py   - P2P + 身份 + 账本        │
│  daemon/api.py    - REST API :6789          │
├─────────────────────────────────────────────┤
│  Blender 插件层 (零外部依赖，urllib only)     │
│  blender_addon/__init__.py                  │
├─────────────────────────────────────────────┤
│  共享层                                      │
│  shared/hardware_fingerprint.py  硬件指纹身份 │
│  shared/ledger_sync.py          账本同步     │
│  client/p2p_client.py          libtorrent   │
│  tracker/main.py               Tracker 服务  │
└─────────────────────────────────────────────┘
```

**使用流程：**
```
python start_daemon.py    ← 启动守护进程
打开 Blender              ← 插件自动连接
关闭 Blender              ← 守护进程继续做种，积分不断
```

---

## 核心特性

### 🆔 硬件指纹身份
- CPU ID + 磁盘序列号 + 主板序列号 + MAC 地址 → SHA256
- **无需注册**，一台电脑一个身份
- 自动生成昵称（如 `dragon_ab12cd`）

### 🌱 强制做种
- 下载后必须做种 **24 小时** 或上传 **100 MB**
- **Blender 关闭后守护进程继续做种** ← v0.2.0 核心改进

### 🏆 积分公式
```
积分 = (做种时长h × 文件MB × 0.01 + 上传MB) × 热度加成(上限 2x)
```

### 📦 零依赖插件
- Blender 插件仅用 Python 标准库 urllib
- P2P / 账本 / 身份全部在守护进程运行

---

## 快速开始

### 首次安装

```bash
cd blendlink/
python install.py    # 一键安装所有依赖
python start_daemon.py
```

### 自建 Tracker（可选）

```bash
cd blendlink/tracker/
pip install fastapi uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000
```

### API 文档

- 守护进程: http://127.0.0.1:6789/docs
- Tracker: http://localhost:8000/docs

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 守护进程 | FastAPI + uvicorn |
| Tracker | FastAPI + SQLite |
| P2P | libtorrent（可选） |
| Blender 插件 | Python 标准库（零依赖） |
| 身份 | SHA256 硬件指纹 |
| 账本 | JSON Lines |

---

## 路线图

- [x] 硬件指纹身份
- [x] 本地账本 + 积分
- [x] Tracker API
- [x] P2P + 强制做种
- [x] Blender 插件 UI
- [x] **守护进程 + 插件分离**（v0.2.0）
- [ ] 预览图/GIF 生成
- [ ] 分布式 Tracker 联邦
- [ ] 独立 Web 管理界面
- [ ] 资产打包工具

---

## 🌍 开源与贡献

**BlendLink 是完全开源的项目，采用 MIT 许可证。**

欢迎所有人参与：
- 🐛 报告 Bug
- 💡 提出功能建议
- 📝 完善文档
- 🔧 提交代码（Pull Request）

参见 [CONTRIBUTING.md](CONTRIBUTING.md) 了解如何开始。

```
任何人都可以运行 Tracker 节点
任何人都可以上传资产
任何人都可以下载并做种
热门资产永远存活在网络中
```

**贡献者:** 感谢每一个参与 BlendLink 的人 🌟

---

## 文件结构

```
blendlink/
├── daemon/               # 守护进程
│   ├── __init__.py
│   ├── main.py          # 入口
│   └── api.py           # REST API
├── client/              # P2P 客户端
│   └── p2p_client.py   # libtorrent 封装
├── shared/              # 共享库
│   ├── hardware_fingerprint.py
│   └── ledger_sync.py
├── tracker/             # Tracker 服务
│   └── main.py
├── blender_addon/       # Blender 插件
│   └── __init__.py
├── install.py           # 一键安装脚本
├── start_daemon.py      # 启动脚本
├── ARCHITECTURE.md      # 详细架构文档
├── CONTRIBUTING.md      # 贡献指南
└── LICENSE              # MIT 许可证
```
