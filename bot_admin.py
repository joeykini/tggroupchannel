"""Telegram Bot 管理端：在 Bot 内查看/修改配置、触发同步与补抓。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from bridge import ChannelBridge
from config import Settings, load_settings, patch_settings

log = logging.getLogger("bot-admin")

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
}


class BotAdmin:
    def __init__(self, bridge: ChannelBridge) -> None:
        self.bridge = bridge
        self._offset = 0
        self._running = False
        self._http: httpx.AsyncClient | None = None

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
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._call(token, "sendMessage", payload)

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
            f"自动发布: {self._on_off(settings.auto_publish)}\n"
            f"源站同步: {self._on_off(settings.sync_enabled)} "
            f"（每 {settings.sync_interval_minutes} 分钟）\n"
            f"内容去重: {self._on_off(settings.content_dedup_enabled)}\n"
            f"去源站痕迹: {self._on_off(settings.strip_source_refs)}\n"
            f"仅带图帖子: {self._on_off(settings.require_media)}\n"
            f"源删同步删目标: {self._on_off(settings.delete_from_target_on_source_removed)}\n"
            f"状态推送: {self._on_off(settings.bot_enabled)}\n"
            f"每日补抓: {self._on_off(settings.daily_fetch_enabled)} "
            f"@ {settings.daily_fetch_time} × {settings.daily_fetch_limit}\n"
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
                {"text": "立即补抓", "callback_data": "action:fetch"},
            ],
            [
                {"text": "刷新状态", "callback_data": "action:status"},
            ],
        ]
        return {"inline_keyboard": rows}

    def _help_text(self) -> str:
        lines = [
            "🤖 管理 Bot 命令",
            "",
            "/menu — 按钮面板",
            "/status — 查看配置",
            "/toggle <项> — 切换开关",
            "  项: ai auto sync dedup strip media notify del_target daily",
            "/set <项> <值> — 修改参数",
        ]
        for key, (_, label) in SETTABLE_KEYS.items():
            lines.append(f"  {key} — {label}")
        lines.extend(
            [
                "",
                "示例:",
                "/set source @a,@b",
                "/set target @my_channel",
                "/set sync_interval 30",
                "/toggle auto",
            ]
        )
        return "\n".join(lines)

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
        if field in ("sync_interval_minutes", "sync_scan_limit", "daily_fetch_limit"):
            value = max(1, int(value))
        elif field in ("source_channels", "filter_keywords", "blocked_keywords"):
            value = [x.strip() for x in raw_value.replace("；", ",").split(",") if x.strip()]
        updated = patch_settings(**{field: value})
        self.bridge.reload_settings()
        return updated

    async def _handle_command(self, token: str, chat_id: int, user_id: int, text: str) -> None:
        settings = load_settings()
        if not self._is_admin(settings, user_id):
            await self._send(token, chat_id, "无权限。请让管理员把你的 Telegram 用户 ID 加入 BOT_ADMIN_IDS。")
            return

        cmd = text.strip()
        lower = cmd.lower()
        if lower in ("/start", "/help"):
            await self._send(token, chat_id, self._help_text())
            return
        if lower == "/menu":
            s = load_settings()
            await self._send(token, chat_id, "点击下方按钮调整：", self._menu_keyboard(s))
            return
        if lower == "/status":
            await self._send(token, chat_id, self._status_text(load_settings()))
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

        await self._send(token, chat_id, "未知命令。发送 /help 查看说明。")

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
                    self._menu_keyboard(updated),
                )
                return

            if data == "action:status":
                s = load_settings()
                await self._answer_callback(token, callback_id)
                await self._send(token, chat_id, self._status_text(s), self._menu_keyboard(s))
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
        if not text.startswith("/"):
            return
        chat_id = message.get("chat", {}).get("id")
        user_id = message.get("from", {}).get("id")
        if chat_id is None:
            return
        await self._handle_command(token, chat_id, user_id, text)

    async def run_loop(self) -> None:
        self._running = True
        log.info("管理 Bot 轮询已启动")
        while self._running:
            settings = load_settings()
            if not settings.bot_token or not self._admin_ids(settings):
                await asyncio.sleep(5)
                continue
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
