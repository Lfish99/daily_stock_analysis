# -*- coding: utf-8 -*-
"""
===================================
Discord Gateway 模式适配器
===================================

通过 discord.py 的 WebSocket 长连接接收 Discord 消息，无需公网 IP。

优势：
- 不需要公网 IP 或域名
- 不需要配置 Webhook URL
- 通过 WebSocket 长连接接收消息
- 本地电脑或服务器常驻运行即可

前置步骤（开发者后台配置一次）：
1. https://discord.com/developers/applications -> 选择你的 App -> Bot
2. 开启 "Message Content Intent"（Privileged Gateway Intents）
3. 在 OAuth2 -> URL Generator 中勾选 bot + Send Messages + Read Message History
4. 用生成的链接邀请 Bot 进入你的 Server

依赖：discord.py（requirements.txt 已包含）

用法：
- 在任意频道发送 /stock AAPL 或 /digest
- @机器人 + 命令 同样有效
- 直接发送股票代码如 AAPL 也会触发查询
"""

import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

# 尝试导入 discord.py
try:
    import discord

    DISCORD_PY_AVAILABLE = True
except ImportError:
    DISCORD_PY_AVAILABLE = False
    logger.warning("[Discord Gateway] discord.py 未安装，Gateway 模式不可用")
    logger.warning("[Discord Gateway] 请运行: pip install discord.py")

from bot.models import BotMessage, BotResponse, ChatType

# 命令前缀
COMMAND_PREFIX = "/"
# Discord 单条消息字数上限
_DISCORD_MSG_LIMIT = 1990


class DiscordGatewayClient:
    """
    Discord Gateway 模式客户端

    使用 discord.py 通过 WebSocket 长连接接收消息。
    在独立后台线程中运行自己的 asyncio 事件循环，
    不影响主线程的同步/异步模型。

    触发条件（满足任一即处理）：
    - 消息以 / 开头（命令）
    - 消息 @了机器人
    - 私信
    """

    def __init__(self, token: Optional[str] = None):
        if not DISCORD_PY_AVAILABLE:
            raise ImportError(
                "discord.py 未安装。\n请运行: pip install discord.py"
            )

        from src.config import get_config
        config = get_config()

        self._token = token or getattr(config, "discord_bot_token", None)
        if not self._token:
            raise ValueError(
                "Discord Gateway 模式需要配置 DISCORD_BOT_TOKEN"
            )

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[discord.Client] = None

    # ------------------------------------------------------------------
    # 内部构建
    # ------------------------------------------------------------------

    def _build_client(self) -> discord.Client:
        """构建 discord.Client 并注册事件处理器"""
        intents = discord.Intents.default()
        intents.message_content = True   # Privileged Intent，须在开发者后台开启
        intents.dm_messages = True

        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:
            logger.info(
                "[Discord Gateway] 连接成功: %s (id=%s)", client.user, client.user.id
            )

        @client.event
        async def on_message(message: discord.Message) -> None:
            await self._handle_message(client, message)

        return client

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------

    async def _handle_message(
        self, client: discord.Client, message: discord.Message
    ) -> None:
        """处理收到的 Discord 消息"""
        # 忽略自身消息
        if message.author == client.user:
            return

        raw_content = message.content.strip()

        # 判断是否需要响应
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_command = raw_content.startswith(COMMAND_PREFIX)
        is_mentioned = client.user in message.mentions

        if not (is_dm or is_command or is_mentioned):
            return

        # 清理内容：去掉 mention 标签和命令前缀
        content = raw_content
        if is_mentioned:
            content = (
                content
                .replace(f"<@{client.user.id}>", "")
                .replace(f"<@!{client.user.id}>", "")
                .strip()
            )

        # 若仍有 / 前缀保留（dispatcher 已能处理带 / 的命令）
        # 不再额外剥离，让 dispatcher 统一处理

        # 裸股票代码自动补 /stock 前缀
        # 例如：@ Bot 发 "TSLA" → "/stock TSLA"；"600519" → "/stock 600519"
        if not content.startswith("/"):
            import re as _re
            bare = content.strip().upper()
            if _re.match(r'^(\d{6}|HK\d{5}|[A-Z]{1,5}(\.[A-Z]{1,2})?)$', bare):
                content = f"/stock {bare}"

        if not content:
            return

        # 会话类型
        if is_dm:
            chat_type = ChatType.PRIVATE
        elif isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            chat_type = ChatType.GROUP
        else:
            chat_type = ChatType.UNKNOWN

        bot_message = BotMessage(
            platform="discord",
            message_id=str(message.id),
            user_id=str(message.author.id),
            user_name=message.author.display_name,
            chat_id=str(message.channel.id),
            chat_type=chat_type,
            content=content,
            raw_content=raw_content,
            mentioned=is_mentioned,
            mentions=[str(u.id) for u in message.mentions if u != client.user],
            timestamp=message.created_at.replace(tzinfo=None),
            raw_data={
                "channel_id": str(message.channel.id),
                "guild_id": str(message.guild.id) if message.guild else None,
            },
        )

        logger.info(
            "[Discord Gateway] 收到消息: user=%s chat=%s content=%s",
            bot_message.user_name,
            bot_message.chat_id,
            (content[:100] + "...") if len(content) > 100 else content,
        )

        try:
            from bot.dispatcher import get_dispatcher
            dispatcher = get_dispatcher()
            response: BotResponse = await dispatcher.dispatch_async(bot_message)

            if response and response.text:
                await self._send_reply(message, response)

        except Exception as exc:
            logger.error("[Discord Gateway] 处理消息失败: %s", exc)
            logger.exception(exc)

    @staticmethod
    async def _send_reply(
        message: discord.Message, response: BotResponse
    ) -> None:
        """将 BotResponse 发送回 Discord 频道（自动分段）"""
        text = response.text
        # Discord 单条消息上限 2000 字符，超出自动分段
        chunks = [text[i: i + _DISCORD_MSG_LIMIT] for i in range(0, len(text), _DISCORD_MSG_LIMIT)]
        for chunk in chunks:
            await message.channel.send(chunk)

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动 Gateway 客户端（阻塞，直到连接断开）"""
        logger.info("[Discord Gateway] 正在启动...")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._client = self._build_client()
        try:
            self._loop.run_until_complete(self._client.start(self._token))
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            logger.error("[Discord Gateway] 运行异常: %s", exc)
        finally:
            try:
                self._loop.run_until_complete(self._client.close())
            except Exception:
                pass
            self._loop.close()
        logger.info("[Discord Gateway] 客户端已停止")

    def start_background(self) -> None:
        """在后台 daemon 线程中启动（非阻塞）"""
        self._thread = threading.Thread(
            target=self.start,
            name="discord-gateway",
            daemon=True,
        )
        self._thread.start()
        logger.info("[Discord Gateway] 后台线程已启动")


# ------------------------------------------------------------------
# 模块级单例辅助函数
# ------------------------------------------------------------------

_discord_gateway_client: Optional[DiscordGatewayClient] = None


def get_discord_gateway_client() -> Optional[DiscordGatewayClient]:
    """获取当前 Discord Gateway 客户端单例"""
    return _discord_gateway_client


def start_discord_gateway_background() -> bool:
    """
    创建并在后台启动 Discord Gateway 客户端。

    Returns:
        True 表示启动成功，False 表示失败（缺少配置或 SDK）。
    """
    global _discord_gateway_client
    try:
        _discord_gateway_client = DiscordGatewayClient()
        _discord_gateway_client.start_background()
        return True
    except Exception as exc:
        logger.error("[Discord Gateway] 启动失败: %s", exc)
        return False
