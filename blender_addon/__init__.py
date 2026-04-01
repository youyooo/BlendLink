"""
BlendLink Blender 插件 - 轻量 HTTP 客户端版

版本: 0.2.0
Blender: 4.0+
架构: 守护进程(daemon) + 轻量插件(addon)

本插件仅通过 HTTP 调用本地守护进程 (localhost:6789)，
零 libtorrent 依赖，零硬件指纹依赖。
"""

bl_info = {
    "name": "BlendLink - Blender Asset Library P2P",
    "author": "BlendLink Contributors",
    "version": (0, 2, 0),
    "blender": (4, 0, 0),
    "location": "3D Viewport > N Panel > BlendLink",
    "description": "去中心化 P2P Blender 资产库（需运行 blendlink-daemon 守护进程）",
    "category": "Import-Export",
    "warning": "需要先启动 blendlink-daemon 守护进程",
}

import bpy
import threading
import json
import time
from pathlib import Path
from bpy.props import (
    StringProperty, IntProperty, FloatProperty,
    BoolProperty, EnumProperty, CollectionProperty,
)
from bpy.types import Panel, Operator, PropertyGroup, UIList


# ==================== 配置 ====================

DAEMON_URL = "http://127.0.0.1:6789"
REQUEST_TIMEOUT = 8  # 秒


def _daemon_get(path: str, params: dict = None) -> dict:
    """向守护进程发送 GET 请求"""
    import urllib.request
    import urllib.parse
    import urllib.error

    url = f"{DAEMON_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError:
        return {"error": "无法连接守护进程，请确保 blendlink-daemon 正在运行"}
    except Exception as e:
        return {"error": str(e)}


def _daemon_post(path: str, data: dict = None) -> dict:
    """向守护进程发送 POST 请求"""
    import urllib.request
    import urllib.error

    url = f"{DAEMON_URL}{path}"
    payload = json.dumps(data or {}).encode()

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            return {"error": body.get("detail", str(e))}
        except:
            return {"error": str(e)}
        except urllib.error.URLError:
            return {"error": "无法连接守护进程，请确保 blendlink-daemon 正在运行"}
        except Exception as e:
            return {"error": str(e)}


def _daemon_delete(path: str) -> dict:
    """向守护进程发送 DELETE 请求"""
    import urllib.request
    import urllib.error

    url = f"{DAEMON_URL}{path}"
    try:
        req = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            return {"error": body.get("detail", str(e))}
        except:
            return {"error": str(e)}
    except urllib.error.URLError:
        return {"error": "无法连接守护进程"}
    except Exception as e:
        return {"error": str(e)}


# ==================== 缓存状态 ====================

_daemon_online = False
_status_cache = {}
_assets_cache = []
_seeds_cache = []
_identity_cache = {}


def check_daemon_status():
    """检查守护进程是否在线，更新缓存"""
    global _daemon_online, _status_cache, _identity_cache

    result = _daemon_get("/status")
    if "error" in result:
        _daemon_online = False
        _status_cache = {"status": "offline"}
    else:
        _daemon_online = True
        _status_cache = result

        # 缓存身份信息
        identity = _daemon_get("/identity")
        if "error" not in identity:
            _identity_cache = identity

    return _daemon_online


def refresh_assets(category: str = "ALL") -> list:
    """从守护进程获取资产列表"""
    global _assets_cache
    params = {}
    if category and category != "ALL":
        params["category"] = category

    result = _daemon_get("/assets", params)
    if "error" in result:
        print(f"[BlendLink] 获取资产失败: {result['error']}")
        return _assets_cache

    _assets_cache = result.get("assets", [])
    return _assets_cache


def search_assets(query: str) -> list:
    """搜索资产"""
    global _assets_cache
    result = _daemon_get("/assets/search", {"q": query})
    if "error" in result:
        return []
    _assets_cache = result.get("assets", [])
    return _assets_cache


def refresh_seeds() -> list:
    """获取做种列表"""
    global _seeds_cache
    result = _daemon_get("/seeds")
    if "error" in result:
        return []
    _seeds_cache = result.get("seeds", [])
    return _seeds_cache


# ==================== 属性定义 ====================

class BlendLinkAssetItem(PropertyGroup):
    """资产列表项"""
    asset_id: IntProperty()
    name: StringProperty()
    description: StringProperty()
    category: StringProperty()
    creator_name: StringProperty()
    file_size: IntProperty()
    downloads: IntProperty()
    likes: IntProperty()
    seed_count: IntProperty()
    hot_score: FloatProperty()
    thumbnail_url: StringProperty()
    magnet_link: StringProperty()
    info_hash: StringProperty()


class BlendLinkSeedItem(PropertyGroup):
    """做种列表项"""
    asset_name: StringProperty()
    info_hash: StringProperty()
    seeding_status: StringProperty()
    seeding_progress: FloatProperty()
    upload_speed: StringProperty()
    peers: IntProperty()


class BlendLinkSettings(PropertyGroup):
    """插件设置"""
    daemon_url: StringProperty(
        name="守护进程地址",
        default=DAEMON_URL,
    )
    tracker_url: StringProperty(
        name="Tracker 地址",
        default="http://localhost:8000",
    )
    search_query: StringProperty(
        name="搜索",
        description="搜索资产名称或标签",
    )
    category_filter: EnumProperty(
        name="分类",
        items=[
            ('ALL', '全部', '显示所有分类'),
            ('model', '模型', '3D 模型'),
            ('material', '材质', 'PBR 材质'),
            ('shader', '着色器', 'Shader 节点'),
            ('texture', '贴图', '纹理贴图'),
            ('hdri', 'HDRI', '环境光照图'),
        ],
        default='ALL',
    )
    active_asset_index: IntProperty(default=0)
    active_seed_index: IntProperty(default=0)
    current_tab: EnumProperty(
        name="标签",
        items=[
            ('BROWSE', '浏览', '浏览资产市场'),
            ('MY_SEEDS', '做种中', '我的做种资产'),
            ('UPLOAD', '上传', '上传新资产'),
            ('PROFILE', '我的', '用户信息'),
        ],
        default='BROWSE',
    )


# ==================== UI List ====================

class BlendLink_UL_AssetList(UIList):
    """资产列表 UI"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)

            cat_icons = {
                'model': 'MESH_CUBE',
                'material': 'MATERIAL',
                'shader': 'NODE_MATERIAL',
                'texture': 'IMAGE_RGB',
                'hdri': 'WORLD',
            }
            cat_icon = cat_icons.get(item.category, 'FILE')
            row.label(text="", icon=cat_icon)

            col = row.column()
            col.label(text=item.name[:30])

            col2 = row.column()
            col2.label(text=f"↓{item.downloads}  ♥{item.likes}  🌱{item.seed_count}")


class BlendLink_UL_SeedList(UIList):
    """做种列表 UI"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)

            if "完成" in item.seeding_status or "optional" in item.seeding_status.lower():
                row.label(text="", icon='CHECKMARK')
            else:
                row.label(text="", icon='TIME')

            col = row.column()
            col.label(text=item.asset_name[:25])
            col.label(text=item.seeding_status, icon='NONE')

            row.label(text=f"↑{item.upload_speed}")


# ==================== 面板 ====================

class BlendLink_PT_MainPanel(Panel):
    """BlendLink 主面板"""
    bl_label = "BlendLink 资产库"
    bl_idname = "BlendLink_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlendLink'

    def draw_header(self, context):
        layout = self.layout
        if _daemon_online:
            layout.label(text="", icon='WORLD')
        else:
            layout.label(text="", icon='ERROR')

    def draw(self, context):
        layout = self.layout
        settings = context.scene.blendlink_settings

        # ── 守护进程状态 ────────────────────────
        box = layout.box()

        if not _daemon_online:
            box.label(text="⚠ 守护进程离线", icon='ERROR')
            box.label(text="请先运行: python start_daemon.py", icon='INFO')
            box.separator()
            box.operator("blendlink.check_daemon", text="重新检测", icon='FILE_REFRESH')
            return

        # 在线状态
        identity_name = _status_cache.get("identity", {}).get("identity_name", "未知")
        total_points = _status_cache.get("stats", {}).get("total_points", 0)
        active_seeds = _status_cache.get("stats", {}).get("active_seeds", 0)

        row = box.row()
        row.label(text=f"🆔 {identity_name}", icon='USER')
        row.label(text=f"  {total_points} 积分", icon='SOLO_ON')
        if active_seeds > 0:
            box.label(text=f"🌱 正在做种 {active_seeds} 个资产", icon='MOD_WIREFRAME')

        # ── 标签切换 ────────────────────────────
        row = layout.row(align=True)
        row.prop(settings, "current_tab", expand=True)

        layout.separator()

        if settings.current_tab == 'BROWSE':
            self._draw_browse_tab(context, layout, settings)
        elif settings.current_tab == 'MY_SEEDS':
            self._draw_seeds_tab(context, layout, settings)
        elif settings.current_tab == 'UPLOAD':
            self._draw_upload_tab(context, layout, settings)
        elif settings.current_tab == 'PROFILE':
            self._draw_profile_tab(context, layout, settings)

    def _draw_browse_tab(self, context, layout, settings):
        """浏览资产 Tab"""
        row = layout.row(align=True)
        row.prop(settings, "search_query", text="", icon='VIEWZOOM')
        row.operator("blendlink.search_assets", text="", icon='VIEWZOOM')

        layout.prop(settings, "category_filter", text="分类")

        row = layout.row(align=True)
        row.operator("blendlink.refresh_assets", text="刷新热门", icon='FILE_REFRESH')

        layout.separator()

        col = layout.column()
        col.template_list(
            "BlendLink_UL_AssetList", "",
            context.scene, "blendlink_assets",
            settings, "active_asset_index",
            rows=8,
        )

        if context.scene.blendlink_assets:
            idx = settings.active_asset_index
            if 0 <= idx < len(context.scene.blendlink_assets):
                asset = context.scene.blendlink_assets[idx]
                self._draw_asset_detail(layout, asset)

    def _draw_asset_detail(self, layout, asset):
        """资产详情和操作按钮"""
        box = layout.box()

        col = box.column()
        col.label(text=asset.name, icon='ASSET_MANAGER')
        col.label(text=f"作者: {asset.creator_name}")
        if asset.file_size > 0:
            col.label(text=f"分类: {asset.category}  大小: {asset.file_size // 1024 // 1024} MB")

        row = col.row()
        row.label(text=f"↓ {asset.downloads} 次下载")
        row.label(text=f"♥ {asset.likes} 点赞")
        row.label(text=f"🌱 {asset.seed_count} 做种")

        col.separator()

        row = box.row(align=True)
        op = row.operator("blendlink.download_asset", text="下载", icon='IMPORT')
        op.asset_id = asset.asset_id
        op.asset_name = asset.name
        op.magnet_link = asset.magnet_link
        op.info_hash = asset.info_hash

        op2 = row.operator("blendlink.like_asset", text="", icon='HEART')
        op2.asset_id = asset.asset_id

    def _draw_seeds_tab(self, context, layout, settings):
        """做种中 Tab"""
        row = layout.row(align=True)
        row.operator("blendlink.refresh_seeds", text="刷新做种列表", icon='FILE_REFRESH')

        layout.separator()

        col = layout.column()
        col.template_list(
            "BlendLink_UL_SeedList", "",
            context.scene, "blendlink_seeds",
            settings, "active_seed_index",
            rows=6,
        )

        if context.scene.blendlink_seeds:
            idx = settings.active_seed_index
            if 0 <= idx < len(context.scene.blendlink_seeds):
                seed = context.scene.blendlink_seeds[idx]

                box = layout.box()
                box.label(text=seed.asset_name, icon='FILE')
                box.label(text=seed.seeding_status)

                row = box.row()
                row.label(text="做种进度:")
                row.progress(
                    factor=seed.seeding_progress,
                    type='BAR',
                    text=f"{seed.seeding_progress * 100:.0f}%",
                )

                row = box.row(align=True)
                op = row.operator("blendlink.stop_seeding", text="停止做种", icon='X')
                op.info_hash = seed.info_hash

                if seed.seeding_progress < 1.0:
                    box.label(
                        text="⚠ 未完成强制做种，不可删除",
                        icon='ERROR',
                    )

    def _draw_upload_tab(self, context, layout, settings):
        """上传资产 Tab"""
        layout.label(text="上传新资产到 BlendLink 网络", icon='EXPORT')
        layout.separator()

        col = layout.column()
        col.operator("blendlink.upload_blend_file", text="选择 .blend 文件上传", icon='FILE_BLEND')
        col.operator("blendlink.upload_current_scene", text="上传当前场景资产", icon='SCENE_DATA')

        layout.separator()
        layout.label(text="上传须知:", icon='INFO')

        info_box = layout.box()
        col = info_box.column()
        col.label(text="• 上传后获得 50 积分奖励")
        col.label(text="• 他人下载后追加积分")
        col.label(text="• 必须持续做种至少 24 小时")
        col.label(text="• 资产一经上传永久存储于网络")

    def _draw_profile_tab(self, context, layout, settings):
        """用户信息 Tab"""
        if _identity_cache:
            box = layout.box()
            col = box.column()

            col.label(text="我的身份", icon='USER')
            col.separator()
            col.label(text=f"昵称: {_identity_cache.get('identity_name', 'N/A')}")
            fp = _identity_cache.get('fingerprint', 'N/A')
            col.label(text=f"指纹: {fp[:16]}..." if len(fp) > 16 else f"指纹: {fp}")
            pid = _identity_cache.get('peer_id', 'N/A')
            col.label(text=f"Peer ID: {pid[:16]}..." if len(pid) > 16 else f"Peer ID: {pid}")
        else:
            layout.label(text="无法获取身份信息", icon='ERROR')

        # 从账本获取统计
        ledger_result = _daemon_get("/ledger/history", {"limit": 1})
        if "error" not in ledger_result:
            balance = ledger_result.get("balance", {})
            layout.separator()
            box2 = layout.box()
            col2 = box2.column()
            col2.label(text="贡献统计", icon='SOLO_ON')
            col2.separator()
            col2.label(text=f"总积分: {balance.get('total_points', 0)}")
            col2.label(text=f"累计获得: {balance.get('lifetime_earned', 0)}")
            col2.label(text=f"累计消费: {balance.get('lifetime_spent', 0)}")

        layout.separator()
        row = layout.row(align=True)
        row.operator("blendlink.sync_ledger", text="同步账本", icon='URL')
        row.operator("blendlink.view_leaderboard", text="排行榜", icon='SORTALPHA')

        # Tracker 设置
        layout.separator()
        layout.prop(settings, "tracker_url")


# ==================== 操作符 ====================

class BlendLink_OT_CheckDaemon(Operator):
    """检测守护进程是否在线"""
    bl_idname = "blendlink.check_daemon"
    bl_label = "检测守护进程"

    def execute(self, context):
        if check_daemon_status():
            self.report({'INFO'}, "守护进程在线 ✓")
        else:
            self.report({'WARNING'}, "守护进程离线 ✗ 请运行: python start_daemon.py")
        return {'FINISHED'}


class BlendLink_OT_RefreshAssets(Operator):
    """刷新热门资产列表"""
    bl_idname = "blendlink.refresh_assets"
    bl_label = "刷新资产"

    def execute(self, context):
        settings = context.scene.blendlink_settings

        def fetch():
            assets = refresh_assets(settings.category_filter)
            bpy.app.timers.register(
                lambda: self._update_ui(context, assets),
                first_interval=0.1,
            )

        threading.Thread(target=fetch, daemon=True).start()
        return {'FINISHED'}

    def _update_ui(self, context, assets):
        context.scene.blendlink_assets.clear()
        for a in assets:
            item = context.scene.blendlink_assets.add()
            item.asset_id = a.get("id", 0)
            item.name = a.get("name", "")
            item.description = a.get("description", "")
            item.category = a.get("category", "")
            item.creator_name = a.get("creator_name", "unknown")
            item.file_size = a.get("file_size", 0)
            item.downloads = a.get("downloads", 0)
            item.likes = a.get("likes", 0)
            item.seed_count = a.get("seed_count", 0)
            item.hot_score = a.get("hot_score", 0.0)
            item.thumbnail_url = a.get("thumbnail_url", "")
            item.magnet_link = a.get("magnet_link", "")
            item.info_hash = a.get("info_hash", "")
        return None


class BlendLink_OT_SearchAssets(Operator):
    bl_idname = "blendlink.search_assets"
    bl_label = "搜索资产"

    def execute(self, context):
        settings = context.scene.blendlink_settings
        if not settings.search_query:
            return bpy.ops.blendlink.refresh_assets()

        def fetch():
            assets = search_assets(settings.search_query)
            bpy.app.timers.register(
                lambda: BlendLink_OT_RefreshAssets._update_ui(self, context, assets),
                first_interval=0.1,
            )

        threading.Thread(target=fetch, daemon=True).start()
        return {'FINISHED'}


class BlendLink_OT_DownloadAsset(Operator):
    """下载资产"""
    bl_idname = "blendlink.download_asset"
    bl_label = "下载资产"

    asset_id: IntProperty()
    asset_name: StringProperty()
    magnet_link: StringProperty()
    info_hash: StringProperty()

    def execute(self, context):
        if not _daemon_online:
            self.report({'ERROR'}, "守护进程离线，无法下载")
            return {'CANCELLED'}

        result = _daemon_post("/download", {
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "magnet_link": self.magnet_link,
        })

        if "error" in result:
            self.report({'ERROR'}, f"下载失败: {result['error']}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"开始下载: {self.asset_name}")
        return {'FINISHED'}


class BlendLink_OT_LikeAsset(Operator):
    """点赞资产"""
    bl_idname = "blendlink.like_asset"
    bl_label = "点赞"

    asset_id: IntProperty()

    def execute(self, context):
        if not _daemon_online:
            self.report({'ERROR'}, "守护进程离线")
            return {'CANCELLED'}

        # 从身份缓存获取指纹
        fingerprint = _identity_cache.get("fingerprint", "")
        if not fingerprint:
            self.report({'ERROR'}, "无法获取用户身份")
            return {'CANCELLED'}

        result = _daemon_post(f"/assets/{self.asset_id}/like", {
            "fingerprint": fingerprint,
        })

        if "error" in result:
            self.report({'ERROR'}, f"点赞失败: {result['error']}")
        else:
            self.report({'INFO'}, "点赞成功")

        return {'FINISHED'}


class BlendLink_OT_SyncLedger(Operator):
    """同步账本到 Tracker"""
    bl_idname = "blendlink.sync_ledger"
    bl_label = "同步账本"

    def execute(self, context):
        if not _daemon_online:
            self.report({'ERROR'}, "守护进程离线")
            return {'CANCELLED'}

        def sync():
            result = _daemon_post("/sync-ledger")
            if "error" in result:
                print(f"[BlendLink] 账本同步失败: {result['error']}")
            else:
                print(f"[BlendLink] 账本同步完成: {result}")

        threading.Thread(target=sync, daemon=True).start()
        self.report({'INFO'}, "账本同步中...")
        return {'FINISHED'}


class BlendLink_OT_RefreshSeeds(Operator):
    """刷新做种列表"""
    bl_idname = "blendlink.refresh_seeds"
    bl_label = "刷新做种列表"

    def execute(self, context):
        if not _daemon_online:
            self.report({'ERROR'}, "守护进程离线")
            return {'CANCELLED'}

        def fetch():
            seeds = refresh_seeds()
            bpy.app.timers.register(
                lambda: self._update_ui(context, seeds),
                first_interval=0.1,
            )

        threading.Thread(target=fetch, daemon=True).start()
        return {'FINISHED'}

    def _update_ui(self, context, seeds):
        context.scene.blendlink_seeds.clear()
        for s in seeds:
            item = context.scene.blendlink_seeds.add()
            item.asset_name = s.get("name", "unknown")
            item.info_hash = s.get("info_hash", "")
            item.seeding_status = s.get("seeding_status", "unknown")

            # 解析做种进度
            progress_str = s.get("seeding_progress", "0%")
            try:
                item.seeding_progress = float(progress_str.replace("%", "")) / 100
            except ValueError:
                item.seeding_progress = 0.0

            item.upload_speed = s.get("upload_speed", "0 KB/s")
            item.peers = s.get("peers", 0)
        return None


class BlendLink_OT_UploadBlendFile(Operator):
    """上传 .blend 文件"""
    bl_idname = "blendlink.upload_blend_file"
    bl_label = "选择文件上传"

    filepath: StringProperty(subtype='FILE_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        import os

        if not self.filepath.endswith(".blend"):
            self.report({'ERROR'}, "请选择 .blend 文件")
            return {'CANCELLED'}

        if not _daemon_online:
            self.report({'ERROR'}, "守护进程离线，无法上传")
            return {'CANCELLED'}

        filename = os.path.basename(self.filepath)
        # TODO: 实现完整上传流程（需要选择分类、填写描述等）
        self.report({'INFO'}, f"准备上传: {filename}")
        return {'FINISHED'}


class BlendLink_OT_StopSeeding(Operator):
    """停止做种"""
    bl_idname = "blendlink.stop_seeding"
    bl_label = "停止做种"

    info_hash: StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if not _daemon_online:
            self.report({'ERROR'}, "守护进程离线")
            return {'CANCELLED'}

        result = _daemon_delete(f"/seeds/{self.info_hash}")
        if "error" in result:
            self.report({'WARNING'}, result["error"])
            return {'CANCELLED'}

        self.report({'INFO'}, "已停止做种")
        return {'FINISHED'}


class BlendLink_OT_ViewLeaderboard(Operator):
    """查看排行榜"""
    bl_idname = "blendlink.view_leaderboard"
    bl_label = "排行榜"

    def execute(self, context):
        import webbrowser
        webbrowser.open(f"{DAEMON_URL}/leaderboard")
        return {'FINISHED'}


class BlendLink_OT_UploadCurrentScene(Operator):
    """上传当前场景"""
    bl_idname = "blendlink.upload_current_scene"
    bl_label = "上传当前场景"

    def execute(self, context):
        # TODO: 实现当前场景打包上传
        self.report({'INFO'}, "功能开发中...")
        return {'FINISHED'}


# ==================== 注册 ====================

classes = [
    BlendLinkAssetItem,
    BlendLinkSeedItem,
    BlendLinkSettings,
    BlendLink_UL_AssetList,
    BlendLink_UL_SeedList,
    BlendLink_PT_MainPanel,
    BlendLink_OT_CheckDaemon,
    BlendLink_OT_RefreshAssets,
    BlendLink_OT_SearchAssets,
    BlendLink_OT_DownloadAsset,
    BlendLink_OT_LikeAsset,
    BlendLink_OT_SyncLedger,
    BlendLink_OT_RefreshSeeds,
    BlendLink_OT_UploadBlendFile,
    BlendLink_OT_StopSeeding,
    BlendLink_OT_ViewLeaderboard,
    BlendLink_OT_UploadCurrentScene,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.blendlink_settings = bpy.props.PointerProperty(type=BlendLinkSettings)
    bpy.types.Scene.blendlink_assets = bpy.props.CollectionProperty(type=BlendLinkAssetItem)
    bpy.types.Scene.blendlink_seeds = bpy.props.CollectionProperty(type=BlendLinkSeedItem)

    # 检测守护进程状态
    check_daemon_status()
    print(f"[BlendLink] 插件 v0.2.0 已加载 | 守护进程: {'在线' if _daemon_online else '离线'}")


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.blendlink_settings
    del bpy.types.Scene.blendlink_assets
    del bpy.types.Scene.blendlink_seeds


if __name__ == "__main__":
    register()
