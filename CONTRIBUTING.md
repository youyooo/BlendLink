# 贡献指南

欢迎加入 BlendLink！🎉

BlendLink（Blender Decentralized Asset Library）是一个去中心化 P2P Blender 资产库项目，旨在让 3D 创作者可以自由分享和获取资产。

## 如何贡献

### 方式一：报告问题
- 前往 [GitHub Issues](https://github.com/your-repo/blendlink/issues)
- 提交 Bug 或功能建议
- 请描述清楚复现步骤

### 方式二：代码贡献
1. **Fork** 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交代码：`git commit -m 'Add some feature'`
4. 推送分支：`git push origin feature/your-feature`
5. 提交 **Pull Request**

### 方式三：完善文档
- 纠正错别字
- 补充缺失的使用说明
- 翻译成其他语言

## 项目结构

```
blendlink/
├── daemon/            # 守护进程（Python）
│   ├── main.py       # 入口
│   └── api.py        # REST API
├── client/           # P2P 客户端（libtorrent）
├── shared/           # 共享模块
│   ├── hardware_fingerprint.py   # 硬件指纹身份
│   └── ledger_sync.py            # 账本同步
├── tracker/          # Tracker 服务（可选自建）
├── blender_addon/    # Blender 插件
├── install.py        # 一键安装脚本
└── start_daemon.py   # 启动脚本
```

## 开发环境

```bash
# 克隆后先安装依赖
python install.py

# 启动守护进程
python start_daemon.py

# 运行 Blender，安装插件（从 blender_addon/ 目录）
```

## 技术栈

- **Python 3.10+** — 守护进程、Tracker、P2P 客户端
- **FastAPI** — REST API
- **libtorrent** — P2P 下载引擎
- **Blender Python API** — 插件

## 规则

- ⚠️ 不要提交任何个人密钥或凭证
- ✅ 提交前请测试代码
- ✅ 代码风格保持一致
- ✅ 重要改动请更新文档

## 行为准则

- 友善交流，尊重他人
- 欢迎所有人，无论经验多少
- 聚焦项目目标，避免无关争论

## 许可证

贡献的代码将采用 [MIT 许可证](LICENSE)。

---

有问题？ 欢迎提交 Issue 或联系维护者。
