# -*- coding: utf-8 -*-
"""
===================================
机器人 Webhook 接口
===================================

处理各平台机器人的 Webhook 回调。

支持的平台：
- 企业微信（/bot/wecom）—— 需要 WECOM_CORPID / WECOM_TOKEN / WECOM_ENCODING_AES_KEY
- Discord（/bot/discord）—— 需要 DISCORD_INTERACTIONS_PUBLIC_KEY

接入说明：
1. 在企业微信管理后台 -> 自建应用 -> 接收消息 -> API 接收
2. 填写 URL: https://<your-domain>/api/v1/bot/wecom
3. 填写 Token 和 EncodingAESKey（与 .env 中的配置保持一致）
4. 保存后企业微信会发一次 GET 请求做 URL 验证，通过后即可收发消息
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/wecom")
async def wecom_verify(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> Response:
    """
    企业微信 URL 验证（GET 请求）。

    企业微信在管理后台配置回调 URL 时会发一次 GET 请求，
    需要解密 echostr 后原样返回，验证通过即完成接入。
    """
    from bot.platforms.wecom import WecomPlatform
    platform = WecomPlatform()
    plain = platform.verify_url(msg_signature, timestamp, nonce, echostr)
    if plain is None:
        logger.warning("[WeCom] URL 验证失败")
        return Response(content="forbidden", status_code=403)
    logger.info("[WeCom] URL 验证通过")
    return Response(content=plain, media_type="text/plain")


@router.post("/wecom")
async def wecom_callback(
    request: Request,
    msg_signature: Optional[str] = Query(None, alias="msg_signature"),
    timestamp: Optional[str] = Query(None),
    nonce: Optional[str] = Query(None),
) -> Response:
    """
    企业微信消息回调（POST 请求）。

    企业微信将消息以加密 XML 格式 POST 到此接口，
    解密后分发到命令处理器，后台异步执行并通过推送渠道返回结果。
    """
    from bot.handler import handle_webhook_async

    body = await request.body()

    # 将 URL query 参数注入 headers 供签名验证使用
    headers = dict(request.headers)
    if msg_signature:
        headers["x-wecom-msg-signature"] = msg_signature
    if timestamp:
        headers["x-wecom-timestamp"] = timestamp
    if nonce:
        headers["x-wecom-nonce"] = nonce

    webhook_response = await handle_webhook_async("wecom", headers, body)

    # 企业微信要求：正常处理后回复 HTTP 200，body 为 "success"（纯文本）
    if webhook_response.status_code != 200:
        return Response(
            content=str(webhook_response.body.get("error", "error")),
            status_code=webhook_response.status_code,
            media_type="text/plain",
        )
    return Response(content="success", media_type="text/plain")


@router.post("/discord")
async def discord_callback(request: Request) -> Response:
    """
    Discord Interaction 回调（POST 请求）。

    Discord 会将 interaction payload（含签名头）POST 到此接口：
    - type=1：Ping 验证
    - type=2：Slash Command 调用
    """
    from bot.handler import handle_webhook_async

    body = await request.body()
    headers = dict(request.headers)
    webhook_response = await handle_webhook_async("discord", headers, body)

    return JSONResponse(
        content=webhook_response.body,
        status_code=webhook_response.status_code,
        headers=webhook_response.headers,
    )
