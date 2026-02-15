# src/jito_client.py
import base64
import random

import aiohttp
import base58
from loguru import logger
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.transaction import VersionedTransaction

from config.settings import settings


class JitoClient:
    def __init__(self):
        self.engine_url = settings.JITO_ENGINE_URL
        self.tip_amount = settings.JITO_TIP_AMOUNT_SOL

    async def send_bundle(self, jupiter_tx_base64: str, payer_keypair: Keypair):
        """
        参考 SmartFlow3 逻辑优化版
        """
        try:
            # 1. 获取最新 Blockhash
            async with AsyncClient(settings.RPC_URL) as rpc_client:
                recent_blockhash = (await rpc_client.get_latest_blockhash()).value.blockhash

            # 2. 解析并重新签署 Swap 交易
            raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
            swap_tx = VersionedTransaction.from_bytes(raw_tx_bytes)
            # 必须使用 payer 重新签名，确保签名位完整
            signed_swap_tx = VersionedTransaction(swap_tx.message, [payer_keypair])

            # 3. 构建并签署小费交易 (Tip)
            tip_account = random.choice(settings.JITO_TIP_ACCOUNTS).strip()
            tip_ix = transfer(TransferParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=Pubkey.from_string(tip_account),
                lamports=int(self.tip_amount * 10 ** 9)
            ))
            tip_msg = MessageV0.try_compile(payer_keypair.pubkey(), [tip_ix], [], recent_blockhash)
            signed_tip_tx = VersionedTransaction(tip_msg, [payer_keypair])

            # 4. 严格按照 SmartFlow3 的方式进行 Base58 编码
            b58_swap = base58.b58encode(bytes(signed_swap_tx)).decode('utf-8')
            b58_tip = base58.b58encode(bytes(signed_tip_tx)).decode('utf-8')

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [[b58_swap, b58_tip]]
            }

            # 5. 发送请求 (增加超时控制)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        self.engine_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        # 重点：打印详细错误，识别是否为全局限流
                        logger.error(f"❌ Jito 拒绝 [{resp.status}]: {data.get('error')}")
                        return None
                    return data.get("result")

        except Exception as e:
            logger.error(f"💥 Jito 构建异常: {str(e)}")
            return None
