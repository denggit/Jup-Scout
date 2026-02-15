#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/16/2026 1:18 AM
@File       : jito_client.py
@Description: 
"""
import base58
import aiohttp
import random
import base64
from loguru import logger
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from config.settings import settings


class JitoClient:
    def __init__(self):
        self.engine_url = settings.JITO_ENGINE_URL
        self.tip_amount = settings.JITO_TIP_AMOUNT_SOL

    async def send_bundle(self, jupiter_tx_base64: str, payer_keypair: Keypair):
        """
        核心功能：构建 [你的Swap交易 + 给矿工的小费] 的原子包
        """
        try:
            # 1. 准备 Swap 交易
            # 从 Base64 解码回来
            raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
            jupiter_tx = VersionedTransaction.from_bytes(raw_tx_bytes)

            # 重要：用我们的私钥重新签名 (Jupiter 返回的交易需要我们授权)
            # 我们只用 Message 部分，丢弃原来的空签名，换上我们的
            signed_jupiter_tx = VersionedTransaction(jupiter_tx.message, [payer_keypair])

            # 2. 准备小费交易 (Tip)
            # 随机选一个 Jito 官方账户收钱
            tip_account = Pubkey.from_string(random.choice(settings.JITO_TIP_ACCOUNTS))
            tip_ix = transfer(
                TransferParams(
                    from_pubkey=payer_keypair.pubkey(),
                    to_pubkey=tip_account,
                    lamports=int(self.tip_amount * settings.LAMPORT_PER_SOL)
                )
            )

            # 编译小费交易
            # 技巧：必须使用和 Swap 交易完全一样的 Blockhash，确保它们在同一个区块执行
            recent_blockhash = jupiter_tx.message.recent_blockhash
            tip_msg = MessageV0.try_compile(
                payer_keypair.pubkey(),
                [tip_ix],
                [],
                recent_blockhash
            )
            signed_tip_tx = VersionedTransaction(tip_msg, [payer_keypair])

            # 3. 打包 (Bundle)
            # Jito 要求传 base58 编码的交易字符串
            b58_swap = base58.b58encode(bytes(signed_jupiter_tx)).decode("utf-8")
            b58_tip = base58.b58encode(bytes(signed_tip_tx)).decode("utf-8")

            # 4. 发送给 Block Engine
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [[b58_swap, b58_tip]]
            }

            logger.info(f"🚚 发送 Jito Bundle... (小费: {self.tip_amount} SOL)")

            async with aiohttp.ClientSession() as session:
                async with session.post(self.engine_url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error(f"❌ Jito 网络错误: {resp.status}")
                        return None

                    data = await resp.json()

                    if "result" in data:
                        bundle_id = data["result"]
                        logger.success(f"✅ Bundle 已发射! ID: {bundle_id}")
                        logger.info(f"🔗 查看进度: https://explorer.jito.wtf/bundle/{bundle_id}")
                        return bundle_id
                    else:
                        logger.error(f"❌ Jito 拒收: {data}")
                        return None

        except Exception as e:
            logger.error(f"💥 Jito 客户端内部错误: {e}")
            return None