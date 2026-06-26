"""Telegram Bot 管理端：在 Bot 内查看/修改配置、触发同步与补抓。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from bridge import ChannelBridge
from config import Settings, load_settings, patch_settings

from roster_store import (
    count_library_persons,
    count_publishable_persons,
    get_person,
    list_library_persons,
)

log = logging.getLogger("bot-admin")
LIB_PAGE_SIZE = 12

BOT_COMMANDS = [
    {"command": "start", "description": "启动欢迎与主菜单"},
    {"command": "menu", "description": "控制台（开关/同步按钮）"},
    {"command": "library", "description": "人员库（预览与发布）"},
    {"command": "status", "description": "查看当前配置"},
    {"command": "sync", "description": "源频道同步（删帖/去重）"},
    {"command": "roster", "description": "出勤名单同步"},
    {"command": "fetch", "description": "立即补抓源频道"},
    {"command": "help", "description": "完整指令说明"},
    {"command": "toggle", "description": "切换开关，如 /toggle dedup"},
    {"command": "set", "description": "改配置，如 /set target @频道"},
]

MAIN_REPLY_KEYBOARD = {
    "keyboard": [
        [{"text": "📚 人员库"}, {"text": "⚙️ 控制台"}],
        [{"text": "🔄 源站同步"}, {"text": "📋 出勤同步"}],
        [{"text": "📥 补抓"}, {"text": "❓ 帮助"}],
    ],
    "resize_keyboard": True,
}

TEXT_ACTIONS = {
    "📚 人员库": "library",
    "⚙️ 控制台": "menu",
    "🔄 源站同步": "sync",
    "📋 出勤同步": "roster",
    "📥 补抓": "fetch",
    "❓ 帮助": "help",
}

BOOL_KEYS = {
    "ai": "ai_enabled",
    "auto": "auto_publish",
    "sync": "sync_enabled",
    "dedup": "content_dedup_enabled",
    "strip": "strip_source_refs",
    "media": "require_media",
    "notify": "bot_enabled",
    "del_target": "delete_from_target_on_source_removed",
    "daily": "daily_fetch_enabled",
    "nightly": "nightly_job_enabled",
    "pub_nightly": "auto_publish_after_roster",
    "region": "region_filter_enabled",
}

SETTABLE_KEYS = {
    "source": ("source_channels", "源频道（逗号分隔）"),
    "target": ("target_channel", "目标频道"),
    "filter": ("filter_keywords", "关键词过滤"),
    "blocked": ("blocked_keywords", "广告屏蔽词"),
    "header": ("forward_header", "发布抬头"),
    "sync_interval": ("sync_interval_minutes", "同步间隔(分钟)"),
    "sync_limit": ("sync_scan_limit", "同步扫描条数"),
    "fetch_limit": ("daily_fetch_limit", "每日补抓条数"),
    "fetch_time": ("daily_fetch_time", "每日补抓时间 HH:MM"),
    "roster_time": ("roster_sync_time", "凌晨任务时间 HH:MM"),
    "pub_interval": ("publish_interval_seconds", "批量发布间隔(秒)"),
    "regions": ("allowed_regions", "允许地区(逗号分隔)"),
}


class BotAdmin:
    def __init__(self, bridge: ChannelBridge) -> None:
        self.bridge = bridge
        self._offset = 0
        self._running = False
        self._http: httpx.AsyncClient | None = None
        self._bulk_publish_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._running

    def _api_base(self, token: str) -> str:
        return f"https://api.telegram.org/bot{token}"

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=35.0)
        return self._http

    async def close(self) -> None:
        self._running = False
        if self._http:
            await self._http.aclose()
            self._http = None

    def _admin_ids(self, settings: Settings) -> set[str]:
        ids = {str(x).strip() for x in settings.bot_admin_ids if str(x).strip()}
        if settings.bot_chat_id:
            ids.add(str(settings.bot_chat_id).strip())
        return ids

    def _is_admin(self, settings: Settings, user_id: int | None) -> bool:
        if user_id is None:
            return False
        admins = self._admin_ids(settings)
        if not admins:
            return False
        return str(user_id) in admins

    async def _call(self, token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = await self._client()
        url = f"{self._api_base(token)}/{method}"
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or "Telegram API error")
        return data

    async def _send(
        self,
        token: str,
        chat_id: int | str,
        text: str,
        reply_markup: dict | None = None,
        reply_keyboard: dict | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        elif reply_keyboard:
            payload["reply_markup"] = reply_keyboard
        await self._call(token, "sendMessage", payload)

    async def _register_bot_commands(self, token: str) -> None:
        try:
            await self._call(token, "setMyCommands", {"commands": BOT_COMMANDS})
        except Exception as e:
            log.warning("注册 Bot 命令菜单失败: %s", e)

    async def _answer_callback(self, token: str, callback_id: str, text: str = "") -> None:
        await self._call(
            token,
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text[:180], "show_alert": bool(text)},
        )

    def _on_off(self, value: bool) -> str:
        return "开" if value else "关"

    def _status_text(self, settings: Settings) -> str:
        return (
            "📋 当前配置\n\n"
            f"源频道: {', '.join(settings.source_channels) or '（未设置）'}\n"
            f"目标频道: {settings.target_channel or '（未设置）'}\n"
            f"AI 复写: {self._on_off(settings.ai_enabled)}\n"
            f"自动发布: {self._on_off(settings.auto_publish)}（默认关，请在人员库手动发布）\n"
            f"源站同步: {self._on_off(settings.sync_enabled)} "
            f"（每 {settings.sync_interval_minutes} 分钟）\n"
            f"内容去重: {self._on_off(settings.content_dedup_enabled)}\n"
            f"去源站痕迹: {self._on_off(settings.strip_source_refs)}\n"
            f"仅带图帖子: {self._on_off(settings.require_media)}\n"
            f"源删同步删目标: {self._on_off(settings.delete_from_target_on_source_removed)}\n"
            f"状态推送: {self._on_off(settings.bot_enabled)}\n"
            f"每日补抓: {self._on_off(settings.daily_fetch_enabled)} "
            f"@ {settings.daily_fetch_time} × {settings.daily_fetch_limit}\n"
            f"凌晨任务: {self._on_off(settings.nightly_job_enabled)} "
            f"@ {settings.roster_sync_time}\n"
            f"凌晨后自动发布: {self._on_off(settings.auto_publish_after_roster)}\n"
            f"批量发布间隔: {settings.publish_interval_seconds} 秒\n"
            f"本地区限制: {self._on_off(settings.region_filter_enabled)} "
            f"({', '.join(settings.allowed_regions[:4])}{'…' if len(settings.allowed_regions) > 4 else ''})\n"
            f"监听: {self._on_off(self.bridge.running)}"
        )

    def _menu_keyboard(self, settings: Settings) -> dict:
        rows = [
            [
                {"text": f"AI {self._on_off(settings.ai_enabled)}", "callback_data": "toggle:ai_enabled"},
                {"text": f"自动发布 {self._on_off(settings.auto_publish)}", "callback_data": "toggle:auto_publish"},
            ],
            [
                {"text": f"同步 {self._on_off(settings.sync_enabled)}", "callback_data": "toggle:sync_enabled"},
                {"text": f"去重 {self._on_off(settings.content_dedup_enabled)}", "callback_data": "toggle:content_dedup_enabled"},
            ],
            [
                {"text": f"去源站 {self._on_off(settings.strip_source_refs)}", "callback_data": "toggle:strip_source_refs"},
                {"text": f"仅带图 {self._on_off(settings.require_media)}", "callback_data": "toggle:require_media"},
            ],
            [
                {"text": "立即同步", "callback_data": "action:sync"},
                {"text": "出勤同步", "callback_data": "action:roster"},
                {"text": "立即补抓", "callback_data": "action:fetch"},
            ],
            [
                {"text": "人员库", "callback_data": "lib:p:0"},
                {"text": "刷新状态", "callback_data": "action:status"},
            ],
        ]
        return {"inline_keyboard": rows}

    def _person_status_icon(self, roster_status: str) -> str:
        if roster_status == "online":
            return "🟢"
        if roster_status == "resting":
            return "🔴"
        if roster_status == "inactive":
            return "⚫"
        return "⚪"

    def _person_button_label(self, person) -> str:
        icon = self._person_status_icon(person.roster_status)
        name = person.name or "未命名"
        region = person.region or "?"
        if person.library_status == "published":
            return f"{icon}{name}·{region} ✓"
        return f"{icon}{name}·{region}"

    def _library_list_keyboard(self, page: int) -> tuple[str, dict]:
        total = count_library_persons()
        if total == 0:
            return "人员库为空。先抓取频道帖或运行出勤同步。", {"inline_keyboard": [[{"text": "返回菜单", "callback_data": "action:status"}]]}

        pages = max(1, (total + LIB_PAGE_SIZE - 1) // LIB_PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        persons = list_library_persons(limit=LIB_PAGE_SIZE, offset=page * LIB_PAGE_SIZE)
        rows: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []
        for p in persons:
            label = self._person_button_label(p)[:28]
            row.append({"text": label, "callback_data": f"lib:v:{p.person_id}"})
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append({"text": "◀ 上页", "callback_data": f"lib:p:{page - 1}"})
        nav.append({"text": f"{page + 1}/{pages}", "callback_data": f"lib:p:{page}"})
        if page < pages - 1:
            nav.append({"text": "下页 ▶", "callback_data": f"lib:p:{page + 1}"})
        rows.append(nav)
        ready = count_publishable_persons(only_unpublished=True)
        if ready > 0:
            rows.append(
                [{"text": f"📢 发布全部未发 ({ready})", "callback_data": "lib:puball:ask"}]
            )
        rows.append([{"text": "返回菜单", "callback_data": "action:status"}])
        text = (
            f"📚 人员库（共 {total} 人，未发 {ready} 人）\n"
            "点名字 → 预览 → 单条发布；或点「发布全部未发」间隔批量发布。"
        )
        return text, {"inline_keyboard": rows}

    def _person_preview_keyboard(self, person_id: str) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ 发布到频道", "callback_data": f"lib:pub:{person_id}"},
                    {"text": "« 返回列表", "callback_data": "lib:p:0"},
                ]
            ]
        }

    async def _show_library(self, token: str, chat_id: int | str, page: int = 0) -> None:
        text, markup = self._library_list_keyboard(page)
        await self._send(token, chat_id, text, reply_markup=markup)

    async def _run_bulk_publish(self, token: str, chat_id: int | str) -> None:
        async with self._bulk_publish_lock:
            try:
                result = await self.bridge.publish_all_ready(only_unpublished=True)
                if result.get("ok"):
                    msg = (
                        f"✅ 批量发布完成\n"
                        f"成功 {result.get('published', 0)}，"
                        f"失败 {result.get('failed', 0)}，"
                        f"共 {result.get('total', 0)} 人\n"
                        f"间隔 {result.get('interval_seconds', 0)} 秒"
                    )
                    if result.get("reason") == "无可发布人员":
                        msg = "没有可发布人员（需在岗且有预览文案）。"
                else:
                    msg = f"❌ 批量发布失败: {result.get('reason') or '未知错误'}"
                await self._send(token, chat_id, msg, reply_keyboard=MAIN_REPLY_KEYBOARD)
            except Exception as e:
                log.exception("批量发布失败")
                await self._send(token, chat_id, f"❌ 批量发布异常: {e}")

    async def _show_person_preview(self, token: str, chat_id: int | str, person_id: str) -> None:
        person = get_person(person_id)
        if not person or not person.preview_text:
            await self._send(token, chat_id, "未找到该人员或尚无预览内容。")
            return
        icon = self._person_status_icon(person.roster_status)
        header = f"{icon} {person.name} · {person.region}\n状态: {person.library_status}\n\n"
        body = person.preview_text[:3600]
        await self._send(token, chat_id, header + body, reply_markup=self._person_preview_keyboard(person_id))

    def _welcome_text(self) -> str:
        total = count_library_persons()
        return (
            "👋 频道抓取管理 Bot\n\n"
            f"人员库当前: {total} 人\n"
            "流程: 抓取 → 人员库预览 → 手动发布\n\n"
            "底部键盘可快捷操作；输入 / 可看到命令列表。\n"
            "发送 /help 查看完整说明。"
        )

    def _help_text(self) -> list[str]:
        """分段返回，避免超长。"""
        p1 = (
            "📖 指令一览\n\n"
            "【常用】\n"
            "/library — 人员库：点名字预览，再点发布\n"
            "/menu — 控制台按钮（开关、同步）\n"
            "/status — 查看当前配置\n"
            "/sync — 源频道同步（删源帖标记、内容去重）\n"
            "/roster — 出勤名单同步（更新在岗、刷新人员库）\n"
            "/fetch — 立即补抓源频道最近帖子\n"
            "/help — 本说明\n\n"
            "【底部键盘】\n"
            "📚人员库 | ⚙️控制台 | 🔄源站同步\n"
            "📋出勤同步 | 📥补抓 | ❓帮助"
        )
        p2 = (
            "【开关 /toggle <项>】\n"
            "ai — AI 复写\n"
            "auto — 自动发布（默认关，建议用手动发布）\n"
            "sync — 定时源站同步\n"
            "dedup — 内容指纹去重\n"
            "strip — 去源站痕迹\n"
            "media — 仅抓带图帖子\n"
            "notify — 状态 Bot 推送\n"
            "del_target — 源删时同步删目标帖\n"
            "daily — 每日定时补抓\n"
            "nightly — 凌晨 02:30 比对任务\n"
            "pub_nightly — 凌晨比对后自动间隔发布\n"
            "region — 仅允许淮安本地区入库\n\n"
            "示例: /toggle dedup"
        )
        p3 = "【配置 /set <项> <值>】\n"
        for key, (_, label) in SETTABLE_KEYS.items():
            p3 += f"{key} — {label}\n"
        p3 += (
            "\n示例:\n"
            "/set source @huaian008,@huaian0901\n"
            "/set target @huaianbendi\n"
            "/set blocked vpn,机场,广告\n"
            "/set sync_interval 60\n"
            "/set regions 清江浦区,淮阴区,淮安区,洪泽区,涟水县,盱眙县,金湖县"
        )
        p4 = (
            "【人员库】\n"
            "点名字 → 预览 → ✅ 发布到频道（单条）\n"
            "📢 发布全部未发 — 按 PUBLISH_INTERVAL_SECONDS 间隔批量发\n"
            "含「商k」或非淮安本地区 → 不入库，出勤同步时自动清理\n"
            "🟢 在线 | 🔴 休息 | ✓ 已发布\n\n"
            "【控制台按钮】\n"
            "立即同步 — 对比源频道删帖/去重\n"
            "出勤同步 — 抓群出勤名单，刷新人员库\n"
            "立即补抓 — 补抓源频道帖子入人员库"
        )
        return [p1, p2, p3, p4]

    async def _send_help(self, token: str, chat_id: int | str) -> None:
        for part in self._help_text():
            await self._send(token, chat_id, part, reply_keyboard=MAIN_REPLY_KEYBOARD)

    async def _apply_toggle(self, field: str) -> Settings:
        settings = load_settings()
        current = getattr(settings, field, None)
        if not isinstance(current, bool):
            raise ValueError(f"不支持切换: {field}")
        updated = patch_settings(**{field: not current})
        self.bridge.reload_settings()
        return updated

    async def _apply_set(self, key: str, raw_value: str) -> Settings:
        if key not in SETTABLE_KEYS:
            raise ValueError(f"未知配置项: {key}")
        field, _ = SETTABLE_KEYS[key]
        value: str | int | list[str] = raw_value.strip()
        if field in (
            "sync_interval_minutes",
            "sync_scan_limit",
            "daily_fetch_limit",
            "publish_interval_seconds",
        ):
            value = max(5 if field == "publish_interval_seconds" else 1, int(value))
        elif field in ("source_channels", "filter_keywords", "blocked_keywords", "allowed_regions"):
            value = [x.strip() for x in raw_value.replace("；", ",").split(",") if x.strip()]
        updated = patch_settings(**{field: value})
        self.bridge.reload_settings()
        return updated

    async def _run_action(self, token: str, chat_id: int, action: str) -> None:
        if action == "library":
            await self._show_library(token, chat_id, 0)
        elif action == "menu":
            s = load_settings()
            await self._send(token, chat_id, "⚙️ 控制台", reply_markup=self._menu_keyboard(s))
        elif action == "help":
            await self._send_help(token, chat_id)
        elif action == "sync":
            result = await self.bridge.sync_with_source()
            await self._send(token, chat_id, f"同步完成: {result}", reply_keyboard=MAIN_REPLY_KEYBOARD)
        elif action == "roster":
            result = await self.bridge.sync_roster()
            await self._send(token, chat_id, f"出勤同步完成: {result}", reply_keyboard=MAIN_REPLY_KEYBOARD)
        elif action == "fetch":
            s = load_settings()
            result = await self.bridge.fetch_recent_once(limit_per_channel=s.daily_fetch_limit)
            await self._send(token, chat_id, f"补抓完成: {result}", reply_keyboard=MAIN_REPLY_KEYBOARD)

    async def _handle_command(self, token: str, chat_id: int, user_id: int, text: str) -> None:
        settings = load_settings()
        if not self._is_admin(settings, user_id):
            await self._send(token, chat_id, "无权限。请让管理员把你的 Telegram 用户 ID 加入 BOT_ADMIN_IDS。")
            return

        cmd = text.strip()
        lower = cmd.lower().split("@")[0]
        if lower == "/start":
            await self._send(
                token, chat_id, self._welcome_text(), reply_keyboard=MAIN_REPLY_KEYBOARD
            )
            return
        if lower in ("/help",):
            await self._send_help(token, chat_id)
            return
        if lower == "/menu":
            s = load_settings()
            await self._send(token, chat_id, "⚙️ 控制台", reply_markup=self._menu_keyboard(s))
            return
        if lower == "/library":
            await self._show_library(token, chat_id, 0)
            return
        if lower == "/status":
            await self._send(
                token,
                chat_id,
                self._status_text(load_settings()),
                reply_keyboard=MAIN_REPLY_KEYBOARD,
            )
            return
        if lower == "/sync":
            await self._run_action(token, chat_id, "sync")
            return
        if lower == "/roster":
            await self._run_action(token, chat_id, "roster")
            return
        if lower == "/fetch":
            await self._run_action(token, chat_id, "fetch")
            return
        if lower.startswith("/toggle"):
            parts = cmd.split(maxsplit=1)
            if len(parts) < 2:
                await self._send(token, chat_id, "用法: /toggle ai|auto|sync|dedup|strip|media|notify|del_target|daily")
                return
            alias = parts[1].strip().lower()
            field = BOOL_KEYS.get(alias)
            if not field:
                await self._send(token, chat_id, f"未知开关: {alias}")
                return
            updated = await self._apply_toggle(field)
            await self._send(
                token,
                chat_id,
                f"已切换 {alias} -> {self._on_off(getattr(updated, field))}",
            )
            return
        if lower.startswith("/set"):
            parts = cmd.split(maxsplit=2)
            if len(parts) < 3:
                await self._send(token, chat_id, "用法: /set source @a,@b")
                return
            try:
                await self._apply_set(parts[1].strip().lower(), parts[2])
                await self._send(token, chat_id, f"已更新 {parts[1].strip()}")
                await self._send(token, chat_id, self._status_text(load_settings()))
            except Exception as e:
                await self._send(token, chat_id, f"设置失败: {e}")
            return

        await self._send(
            token, chat_id, "未知命令。发送 /help 查看说明。", reply_keyboard=MAIN_REPLY_KEYBOARD
        )

    async def _handle_callback(self, token: str, callback: dict[str, Any]) -> None:
        settings = load_settings()
        user = callback.get("from") or {}
        user_id = user.get("id")
        chat = callback.get("message", {}).get("chat", {})
        chat_id = chat.get("id")
        data = str(callback.get("data") or "")
        callback_id = str(callback.get("id") or "")

        if not self._is_admin(settings, user_id):
            await self._answer_callback(token, callback_id, "无权限")
            return

        try:
            if data.startswith("toggle:"):
                field = data.split(":", 1)[1]
                updated = await self._apply_toggle(field)
                await self._answer_callback(
                    token,
                    callback_id,
                    f"{field} -> {self._on_off(getattr(updated, field))}",
                )
                await self._send(
                    token,
                    chat_id,
                    self._status_text(updated),
                    reply_markup=self._menu_keyboard(updated),
                )
                return

            if data.startswith("lib:p:"):
                page = int(data.split(":")[2])
                await self._answer_callback(token, callback_id)
                await self._show_library(token, chat_id, page)
                return

            if data.startswith("lib:v:"):
                person_id = data.split(":", 2)[2]
                await self._answer_callback(token, callback_id)
                await self._show_person_preview(token, chat_id, person_id)
                return

            if data.startswith("lib:pub:"):
                person_id = data.split(":", 2)[2]
                await self._answer_callback(token, callback_id, "发布中…")
                result = await self.bridge.publish_person_by_id(person_id)
                if result.get("ok"):
                    await self._send(token, chat_id, f"✅ 已发布: {person_id}")
                    await self._show_person_preview(token, chat_id, person_id)
                else:
                    await self._send(
                        token,
                        chat_id,
                        f"❌ 发布失败: {result.get('reason') or '未知错误'}",
                    )
                return

            if data == "lib:puball:ask":
                ready = count_publishable_persons(only_unpublished=True)
                s = load_settings()
                if ready == 0:
                    await self._answer_callback(token, callback_id, "没有可发布人员")
                    return
                await self._answer_callback(token, callback_id)
                text = (
                    f"确认发布全部未发？\n\n"
                    f"共 {ready} 人，每条间隔 {s.publish_interval_seconds} 秒。\n"
                    "过程可能较久，完成后会通知。"
                )
                markup = {
                    "inline_keyboard": [
                        [
                            {"text": "✅ 确认发布", "callback_data": "lib:puball:yes"},
                            {"text": "取消", "callback_data": "lib:p:0"},
                        ]
                    ]
                }
                await self._send(token, chat_id, text, reply_markup=markup)
                return

            if data == "lib:puball:yes":
                if self._bulk_publish_lock.locked():
                    await self._answer_callback(token, callback_id, "已有批量发布进行中")
                    return
                await self._answer_callback(token, callback_id, "开始批量发布…")
                await self._send(token, chat_id, "📢 批量发布已开始，请稍候…")
                asyncio.create_task(self._run_bulk_publish(token, chat_id))
                return

            if data == "action:status":
                s = load_settings()
                await self._answer_callback(token, callback_id)
                await self._send(token, chat_id, self._status_text(s), reply_markup=self._menu_keyboard(s))
                return

            if data == "action:sync":
                await self._answer_callback(token, callback_id, "同步中…")
                result = await self.bridge.sync_with_source()
                await self._send(token, chat_id, f"同步完成: {result}")
                return

            if data == "action:fetch":
                s = load_settings()
                await self._answer_callback(token, callback_id, "补抓中…")
                result = await self.bridge.fetch_recent_once(limit_per_channel=s.daily_fetch_limit)
                await self._send(token, chat_id, f"补抓完成: {result}")
                return

            if data == "action:roster":
                await self._answer_callback(token, callback_id, "出勤同步中…")
                result = await self.bridge.sync_roster()
                await self._send(token, chat_id, f"出勤同步完成: {result}")
                return

            await self._answer_callback(token, callback_id)
        except Exception as e:
            log.exception("Bot 回调处理失败")
            await self._answer_callback(token, callback_id, str(e)[:120])

    async def _handle_update(self, token: str, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(token, update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        text = message.get("text") or ""
        chat_id = message.get("chat", {}).get("id")
        user_id = message.get("from", {}).get("id")
        if chat_id is None:
            return
        if text in TEXT_ACTIONS:
            settings = load_settings()
            if not self._is_admin(settings, user_id):
                await self._send(token, chat_id, "无权限。")
                return
            await self._run_action(token, chat_id, TEXT_ACTIONS[text])
            return
        if not text.startswith("/"):
            return
        await self._handle_command(token, chat_id, user_id, text)

    async def run_loop(self) -> None:
        self._running = True
        log.info("管理 Bot 轮询已启动")
        commands_registered = False
        while self._running:
            settings = load_settings()
            if not settings.bot_token or not self._admin_ids(settings):
                await asyncio.sleep(5)
                continue
            if not commands_registered:
                await self._register_bot_commands(settings.bot_token)
                commands_registered = True
            try:
                client = await self._client()
                resp = await client.get(
                    f"{self._api_base(settings.bot_token)}/getUpdates",
                    params={"offset": self._offset, "timeout": 25},
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    log.warning("getUpdates 失败: %s", data.get("description"))
                    await asyncio.sleep(3)
                    continue
                for update in data.get("result", []):
                    self._offset = int(update["update_id"]) + 1
                    await self._handle_update(settings.bot_token, update)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("管理 Bot 轮询异常: %s", e)
                await asyncio.sleep(3)
        await self.close()
        log.info("管理 Bot 轮询已停止")
