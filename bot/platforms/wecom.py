# -*- coding: utf-8 -*-
"""
===================================
企业微信平台适配器
===================================

处理企业微信自建应用的回调消息（Webhook 模式）。

企业微信消息接收文档：
https://developer.work.weixin.qq.com/document/path/90930

接入步骤：
1. 在企业微信管理后台创建自建应用
2. 配置消息接收服务器 URL（本服务的 /bot/wecom）
3. 将 WECOM_CORPID / WECOM_TOKEN / WECOM_ENCODING_AES_KEY / WECOM_AGENT_ID 写入 .env
4. 启动服务后，在管理后台完成 URL 验证即可收发消息

加解密说明：
- 企业微信使用 AES-256-CBC 加密消息体
- 密钥 = base64.b64decode(encoding_aes_key + "=")，共 32 字节
- 消息格式：16字节随机串 + 4字节内容长度(big-endian) + 内容 + corpid
- 依赖 Python `cryptography` 库，已在 requirements.txt 中

URL 验证（GET 请求）由 /bot/wecom 路由处理（见 api/v1/endpoints/bot.py），
本类只处理 POST 消息回调。
"""

import base64
import hashlib
import logging
import struct
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

from bot.platforms.base import BotPlatform
from bot.models import BotMessage, BotResponse, WebhookResponse, ChatType

logger = logging.getLogger(__name__)


def _verify_signature(token: str, timestamp: str, nonce: str, encrypted_msg: str = "") -> str:
    """计算企业微信签名（SHA1 排序拼接）。"""
    parts = sorted([token, timestamp, nonce, encrypted_msg])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def _decrypt_message(aes_key_b64: str, encrypted_b64: str) -> Tuple[str, str]:
    """
    AES-256-CBC 解密企业微信消息。

    Returns:
        (decrypted_xml_str, from_corpid)
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    # 密钥：base64decode(key + "=")
    aes_key = base64.b64decode(aes_key_b64 + "=")
    iv = aes_key[:16]

    cipher_text = base64.b64decode(encrypted_b64)
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    raw = decryptor.update(cipher_text) + decryptor.finalize()

    # PKCS7 去填充
    pad_len = raw[-1]
    raw = raw[:-pad_len]

    # 格式：16字节随机 + 4字节长度(big-endian) + 内容 + corpid
    msg_len = struct.unpack(">I", raw[16:20])[0]
    xml_content = raw[20: 20 + msg_len].decode("utf-8")
    from_corpid = raw[20 + msg_len:].decode("utf-8")
    return xml_content, from_corpid


class WecomPlatform(BotPlatform):
    """
    企业微信平台适配器

    配置要求（.env）：
        WECOM_CORPID           企业 ID
        WECOM_TOKEN            回调 Token（管理后台自建应用中设置）
        WECOM_ENCODING_AES_KEY 消息加解密 Key（管理后台自建应用中设置）
        WECOM_AGENT_ID         应用 AgentId（可选，用于发送应用消息）
    """

    def __init__(self):
        from src.config import get_config
        config = get_config()

        self._corpid = getattr(config, "wecom_corpid", None) or ""
        self._token = getattr(config, "wecom_token", None) or ""
        self._aes_key = getattr(config, "wecom_encoding_aes_key", None) or ""
        self._agent_id = getattr(config, "wecom_agent_id", None) or ""

    @property
    def platform_name(self) -> str:
        return "wecom"

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._aes_key and self._corpid)

    # ------------------------------------------------------------------
    # URL 验证（GET）—— 供 API 层直接调用
    # ------------------------------------------------------------------

    def verify_url(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        echostr: str,
    ) -> Optional[str]:
        """
        验证 URL 有效性（企业微信首次配置时调用）。

        验证通过后返回解密后的明文 echostr；失败返回 None。
        """
        if not self.is_configured:
            logger.warning("[WeCom] 未完整配置 Token / AES Key / CorpId，跳过 URL 验证")
            return None

        expected = _verify_signature(self._token, timestamp, nonce, echostr)
        if expected != msg_signature:
            logger.warning("[WeCom] URL 验证签名不匹配")
            return None

        try:
            plain, from_corpid = _decrypt_message(self._aes_key, echostr)
            if from_corpid and from_corpid != self._corpid:
                logger.warning("[WeCom] URL 验证 corpid 不匹配: %s", from_corpid)
                return None
            return plain
        except Exception as exc:
            logger.warning("[WeCom] URL 验证解密失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 签名验证（POST）
    # ------------------------------------------------------------------

    def verify_request(self, headers: Dict[str, str], body: bytes) -> bool:
        """验证企业微信回调签名（从 URL query 参数注入至 headers）。"""
        if not self._token:
            logger.warning("[WeCom] 未配置 WECOM_TOKEN，跳过签名验证")
            return True

        msg_sig = headers.get("x-wecom-msg-signature", "")
        timestamp = headers.get("x-wecom-timestamp", "")
        nonce = headers.get("x-wecom-nonce", "")

        if not all([msg_sig, timestamp, nonce]):
            # 宽松策略：缺少参数时放行并记录（便于本地调试）
            logger.debug("[WeCom] 签名参数不完整，宽松放行")
            return True

        try:
            # 从 body 解析 Encrypt 字段参与签名
            root = ET.fromstring(body.decode("utf-8"))
            encrypt = root.findtext("Encrypt") or ""
        except ET.ParseError:
            encrypt = ""

        expected = _verify_signature(self._token, timestamp, nonce, encrypt)
        if expected != msg_sig:
            logger.warning("[WeCom] 请求签名验证失败")
            return False

        return True

    # ------------------------------------------------------------------
    # 消息解析
    # ------------------------------------------------------------------

    def parse_message(self, data: Dict[str, Any]) -> Optional[BotMessage]:
        """
        解析企业微信消息。

        data 字段约定：
          _xml_root: ET.Element  原始解密后的 XML 根节点（由 handle_webhook 注入）

        消息格式参考：
          https://developer.work.weixin.qq.com/document/path/90239
        """
        xml_root: Optional[ET.Element] = data.get("_xml_root")
        if xml_root is None:
            return None

        msg_type = xml_root.findtext("MsgType") or ""
        if msg_type != "text":
            logger.debug("[WeCom] 忽略非文本消息: %s", msg_type)
            return None

        raw_content = xml_root.findtext("Content") or ""
        content = self._extract_command(raw_content)

        # 群/单聊判断
        chat_type_raw = xml_root.findtext("ChatType") or ""
        if chat_type_raw == "group":
            chat_type = ChatType.GROUP
        elif chat_type_raw == "single":
            chat_type = ChatType.PRIVATE
        else:
            chat_type = ChatType.PRIVATE  # 默认私聊

        create_time_str = xml_root.findtext("CreateTime") or ""
        try:
            timestamp = datetime.fromtimestamp(int(create_time_str))
        except (ValueError, TypeError):
            timestamp = datetime.now()

        from_user = xml_root.findtext("FromUserName") or ""
        to_user = xml_root.findtext("ToUserName") or ""
        msg_id = xml_root.findtext("MsgId") or ""
        agent_id = xml_root.findtext("AgentID") or self._agent_id

        return BotMessage(
            platform=self.platform_name,
            message_id=msg_id,
            user_id=from_user,
            user_name=from_user,
            chat_id=to_user,
            chat_type=chat_type,
            content=content,
            raw_content=raw_content,
            mentioned=True,   # 发给应用即视为触发
            mentions=[],
            timestamp=timestamp,
            raw_data={"agent_id": agent_id, "_xml_root": xml_root},
        )

    def _extract_command(self, text: str) -> str:
        """去掉开头的 @机器人名 部分，提取命令。"""
        import re
        text = re.sub(r"^@[\S]+\s*", "", text.strip())
        return text.strip()

    # ------------------------------------------------------------------
    # 响应格式化
    # ------------------------------------------------------------------

    def format_response(
        self,
        response: BotResponse,
        message: BotMessage,
    ) -> WebhookResponse:
        """
        企业微信被动回复：立即返回 HTTP 200 "success" 告知已收到消息。

        实际报告通过后台线程经 NotificationService（WECHAT_WEBHOOK_URL）推送，
        无需通过被动回复发送（被动回复有 5 秒超时且不支持 Markdown）。
        """
        return WebhookResponse.success({"result": "success"})

    # ------------------------------------------------------------------
    # 核心消息处理（覆盖 base.py 的 handle_webhook）
    # ------------------------------------------------------------------

    def handle_webhook(
        self,
        headers: Dict[str, str],
        body: bytes,
        data: Dict[str, Any],
    ) -> Tuple[Optional[BotMessage], Optional[WebhookResponse]]:
        """
        处理企业微信 POST 回调：解密 XML → 解析消息。
        """
        if not self.verify_request(headers, body):
            return None, WebhookResponse.error("签名验证失败", 403)

        # 解密消息体
        try:
            root = ET.fromstring(body.decode("utf-8"))
            encrypt = root.findtext("Encrypt") or ""
        except ET.ParseError as exc:
            logger.warning("[WeCom] XML 解析失败: %s", exc)
            return None, WebhookResponse.error("XML 解析失败", 400)

        if not encrypt:
            logger.warning("[WeCom] 消息体缺少 Encrypt 字段")
            return None, WebhookResponse.success("success")

        if not self._aes_key:
            logger.error("[WeCom] 未配置 WECOM_ENCODING_AES_KEY，无法解密消息")
            return None, WebhookResponse.error("未配置解密密钥", 500)

        try:
            xml_str, from_corpid = _decrypt_message(self._aes_key, encrypt)
        except Exception as exc:
            logger.warning("[WeCom] 消息解密失败: %s", exc)
            return None, WebhookResponse.error("消息解密失败", 400)

        if self._corpid and from_corpid and from_corpid != self._corpid:
            logger.warning("[WeCom] CorpId 不匹配: %s", from_corpid)
            return None, WebhookResponse.error("CorpId 不匹配", 403)

        try:
            xml_root = ET.fromstring(xml_str)
        except ET.ParseError as exc:
            logger.warning("[WeCom] 解密后 XML 解析失败: %s", exc)
            return None, WebhookResponse.error("XML 解析失败", 400)

        data["_xml_root"] = xml_root
        message = self.parse_message(data)
        return message, None
