# BlendLink 完整架构与账本系统详解

## 架构概览（v0.2.0 - 守护进程 + 插件）

```
┌─────────────────────────────────────────────────────┐
│ blendlink/daemon/  ← 独立守护进程（手动启动）              │
│ ├── main.py      入口：整合 P2P + 身份 + 账本        │
│ ├── api.py       REST API（localhost:6789）          │
│ │   GET  /status        守护进程状态                  │
│ │   GET  /identity      用户身份信息                  │
│ │   GET  /assets        资产列表（转发 Tracker）       │
│ │   POST /download      触发下载                      │
│ │   GET  /seeds         做种列表                      │
│ │   DELETE /seeds/{hash} 停止做种                     │
│ │   POST /upload        上传新资产                    │
│ │   POST /sync-ledger   同步账本到 Tracker            │
│ │   GET  /ledger/history 本地账本历史                │
│ │   GET  /leaderboard   排行榜                       │
│ │   POST /config/tracker 更新 Tracker 地址           │
│ └── __init__.py                                    │
├─────────────────────────────────────────────────────┤
│ blendlink/blender_addon/  ← 轻量 Blender 插件              │
│ └── __init__.py     纯 HTTP 客户端 UI               │
│                      零 libtorrent 依赖               │
│                      零硬件指纹依赖                    │
├─────────────────────────────────────────────────────┤
│ blendlink/shared/  ← 共享库（守护进程独占）                 │
│ ├── hardware_fingerprint.py  硬件指纹 + 本地账本       │
│ └── ledger_sync.py          账本同步验证              │
├─────────────────────────────────────────────────────┤
│ blendlink/client/  ← P2P 客户端（守护进程内嵌）            │
│ └── p2p_client.py     libtorrent 封装                │
├─────────────────────────────────────────────────────┤
│ blendlink/tracker/  ← Tracker 服务（独立部署）             │
│ └── main.py       FastAPI + SQLite                  │
└─────────────────────────────────────────────────────┘

用户使用流程:
  1. python start_daemon.py   ← 手动启动守护进程
  2. 打开 Blender             ← 插件自动连接守护进程
  3. 关闭 Blender             ← 守护进程继续做种，积分持续累积
```

---

## 核心创新：去中心化身份 + 硬件账本

### 问题
在真正去中心化系统中，无法使用用户名密码（没有中央数据库）。
传统方案（如 BitTorrent）完全匿名，导致无法追踪贡献。

### 方案
基于硬件特征生成**不可伪造的身份指纹**：

```
硬件特征集合:
  - CPU ID (wmic cpu get processorid)
  - 磁盘序列号 (wmic logicaldisk get volumeserialnumber)
  - 主板序列号 (wmic baseboard get serialnumber)
  - MAC 地址 (uuid.getnode())
  
  ↓ SHA256 哈希
  
指纹: 6485f407fb6fc0c42a44a63b678b97b3546f2c36dc222bfd1becbfafa445edbc
昵称: tortoise_6485f4  (动物名 + 指纹前 6 位)
```

### 特性
- **一台电脑一个身份** — 无法注册多个账号
- **不可变** — 除非更换硬件，指纹永远相同
- **私密** — 指纹不能追踪个人（只是硬件标识）
- **可验证** — Tracker 可验证账本签名

---

## 1. 硬件指纹身份系统

### 架构变化（v0.2.0）

```
旧版 (v0.1):
  Blender 插件 → 直接调用 HardwareFingerprint → 生成身份
  问题: 依赖 cryptography 库，Blender 内置 Python 难安装

新版 (v0.2):
  守护进程 → HardwareFingerprint → 生成身份 → 保存到本地
  Blender 插件 → HTTP GET /identity → 获取身份
  优势: 插件零依赖，身份在守护进程统一管理
```

---

## 2. 本地账本系统（LocalLedger）

### 账本格式 (ledger.jsonl)

```jsonl
{"timestamp": 1712057100, "identity": "6485f407fb6f...", "transaction": {"type": "upload", "asset_id": "sha256_abcd", "amount": 50}, "balance_before": 0, "signature": "sha256_sig_here", "balance_after": 50}
{"timestamp": 1712057200, "identity": "6485f407fb6f...", "transaction": {"type": "download", "asset_id": "sha256_xyz", "amount": 0}, "balance_before": 50, "signature": "...", "balance_after": 50}
{"timestamp": 1712057300, "identity": "6485f407fb6f...", "transaction": {"type": "seed", "asset_id": "sha256_xyz", "amount": 30}, "balance_before": 50, "signature": "...", "balance_after": 80}
```

### 存储位置
- Windows: `%APPDATA%\BlendLink\ledger\\`
- Linux/Mac: `~/.blender/blendlink/ledger/`

---

## 3. Tracker 端的验证机制

### 验证流程

```
守护进程离线做种 24 小时 + 上传 100MB
  ↓
生成本地账本证明:
  - total_points = 30
  - ledger_hash = SHA256(所有记录)
  - entries_count = 3
  - signature = SHA256(证明 + 指纹)
  ↓
POST /api/users/submit-ledger
  ↓
Tracker 验证:
  1. 检查指纹格式 ✓
  2. 检查时间戳 ✓
  3. 检查积分范围 ✓
  4. 检查是否黑名单 ✓
  5. 验证签名 ✓
  6. 检查积分是否回滚 ← 关键！
  ↓
客户端被纳入排行榜，积分对全网可见
```

---

## 4. 强制做种与积分计算

### 做种流程

```
下载资产 (100 MB .blend 文件)
  ↓
libtorrent 完成下载
  ↓
自动进入 SEEDING_REQUIRED 状态
  ↓
计时开始: seed_start_time = now()
  ↓
每 5 分钟 ping Tracker:
  POST /api/seeding/ping
  ↓
24 小时后 (或上传 100MB):
  自动检测 meets_seeding_requirement() = True
  状态转换: SEEDING_REQUIRED → SEEDING_COMPLETE
  ↓
汇报做种完成 → 计算积分
```

### 积分公式

```
基础积分 = 做种时长(小时) × 文件大小(MB) × 0.01
上传奖励 = 实际上传字节 / 1 MB
热度加成 = 1 + (资产被下载次数 / 100) × 0.1（上限 2.0x）

最终积分 = (基础 + 上传) × 热度
  例: (24 + 52) × 2.0 = 152 积分
```

---

## 5. 热度排名算法

```python
def calculate_hot_score(downloads, likes, seed_count, created_days_ago):
    score = downloads * 2 + likes * 5 + seed_count * 0.5
    decay_factor = max(1, created_days_ago ** 0.5)
    score = score / decay_factor
    if created_days_ago < 1:
        score *= 1.5  # 新资产加成
    return score
```

---

## 6. 守护进程 REST API 详解

### 端点列表

| 方法 | 路径 | 说明 | Blender 插件调用 |
|------|------|------|----------------|
| GET | /status | 运行状态、身份、积分 | 打开面板时 |
| GET | /identity | 完整身份信息 | 个人页 |
| GET | /assets | 热门资产列表（转发 Tracker） | 浏览页 |
| GET | /assets/search?q=xxx | 搜索资产 | 搜索 |
| POST | /download | 触发 P2P 下载 | 下载按钮 |
| POST | /assets/{id}/like | 点赞/取消 | 点赞按钮 |
| GET | /seeds | 做种列表 | 做种页 |
| DELETE | /seeds/{hash} | 停止做种 | 停止按钮 |
| POST | /upload | 上传新资产 | 上传页 |
| POST | /sync-ledger | 同步账本 | 同步按钮 |
| GET | /ledger/history | 本地账本历史 | 个人页 |
| GET | /leaderboard | 排行榜（转发 Tracker） | 排行榜 |
| POST | /config/tracker | 更新 Tracker 地址 | 设置页 |

### 插件与守护进程通信

```
Blender 插件                    守护进程 (localhost:6789)
┌──────────────┐    HTTP     ┌────────────────────────┐
│ urllib       │◄──────────►│ FastAPI + uvicorn      │
│ (无外部依赖) │             │                        │
│              │             │ DaemonCore:            │
│ 只需要       │             │   ├─ LocalLedger       │
│ Python 标准库│             │   ├─ HardwareFingerprint│
│              │             │   └─ BlendLinkClient        │
│              │             │       └─ libtorrent    │
└──────────────┘             └────────────────────────┘
```

---

## 7. 安全考虑

### 做种作弊防护
- 上传量/下载量比率异常检测
- 速率限制
- 积分不可回滚机制

### 恶意资产防护
- 社区举报机制
- 签名验证
- 病毒扫描（可选）

---

## 8. 数据流总结

```
┌─────────────────────────────────────────┐
│ 守护进程 (blendlink/daemon/)                 │
│ ├─ main.py (DaemonCore)                 │
│ ├─ api.py (REST API localhost:6789)     │
│ ├─ shared/hardware_fingerprint.py       │
│ ├─ shared/ledger_sync.py                │
│ └─ client/p2p_client.py (libtorrent)    │
└──────────────┬──────────────────────────┘
               │ libtorrent P2P (资产文件)
               │ HTTP 账本证明 (积分)
               ▼
┌──────────────────────────────────────────┐
│ Tracker 服务 (FastAPI + SQLite)          │
│ ├─ 用户注册 (fingerprint)                │
│ ├─ 资产索引                              │
│ ├─ 热度排名                              │
│ ├─ 账本验证                              │
│ └─ 做种心跳                              │
└──────────────┬──────────────────────────┘
               │ 联邦同步
               ▼
        全球 Tracker 网络

另外：
┌──────────────────────────────────────────┐
│ Blender 插件 (blender_addon/)            │
│ ├─ 纯 HTTP 客户端                        │
│ ├─ 零外部依赖（仅用 urllib）             │
│ └─ 只调用 localhost:6789                 │
└──────────────────────────────────────────┘
```

数据不对称:
- 文件传输: P2P (去中心化，高速)
- 元数据: Tracker (半中心化，索引)
- 身份: 硬件 (完全去中心化，无法篡改)
- 本地控制: 守护进程 API (完全本地，无外部暴露)

---

## 对标分析

| 特性 | BitTorrent | npm | GitHub | **BlendLink** |
|------|-----------|-----|--------|---------|
| 去中心化 | ✓ | ✗ | ✗ | ✓ |
| 贡献排名 | ✗ | ✓ | ✓ | ✓ |
| 强制做种 | ✗ | ✗ | ✗ | ✓ |
| 硬件指纹 | ✗ | ✗ | ✗ | ✓ |
| 本地账本 | ✗ | ✗ | ✗ | ✓ |
| 3D 资产 | ✗ | ✗ | ✗ | ✓ |
| 独立守护进程 | ✗ | ✗ | ✗ | ✓ |

---

## 总结

BlendLink v0.2.0 通过「守护进程 + 轻量插件」架构解决了以下问题：

1. **硬件指纹** — 用户身份（守护进程管理）
2. **本地账本** — 交易记录（离线可用）
3. **强制做种** — 资源保活（Blender 关闭后继续做种）
4. **热度算法** — 质量排序
5. **积分系统** — 贡献激励
6. **守护进程** — 独立运行，不依赖 Blender

最终实现：**真正的去中心化、自洽的、激励相容的 Blender 资产库**。
