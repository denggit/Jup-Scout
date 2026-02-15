import base58
import aiohttp
import random
import base64
import json
from loguru import logger
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient
from config.settings import settings


class JitoClient:
    def __init__(self):
        self.engine_url = settings.JITO_ENGINE_URL
        self.tip_amount = settings.JITO_TIP_AMOUNT_SOL

    async def send_bundle(self, jupiter_tx_base64: str, payer_keypair: Keypair):
        """
        参考 SmartFlow3 逻辑优化版：构建并发送 Jito Bundle
        """
        try:
            # 1. 立即获取最新的 Blockhash (解决 400 错误的关键)
            async with AsyncClient(settings.RPC_URL) as rpc_client:
                latest_blockhash_resp = await rpc_client.get_latest_blockhash()
                recent_blockhash = latest_blockhash_resp.value.blockhash

            # 2. 解析并重签 Jupiter 交易
            raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
            jupiter_tx = VersionedTransaction.from_bytes(raw_tx_bytes)
            # 使用最新的 blockhash 重新签署消息
            signed_jupiter_tx = VersionedTransaction(jupiter_tx.message, [payer_keypair])

            # 3. 构建小费交易 (Tip Transaction)
            # 随机选择小费账户并清洗字符串
            tip_account_str = random.choice(settings.JITO_TIP_ACCOUNTS).strip()
            tip_account_pubkey = Pubkey.from_string(tip_account_str)
            tip_lamports = int(self.tip_amount * settings.LAMPORT_PER_SOL)

            tip_ix = transfer(
                TransferParams(
                    from_pubkey=payer_keypair.pubkey(),
                    to_pubkey=tip_account_pubkey,
                    lamports=tip_lamports
                )
            )

            # 使用与 Swap 交易一致的最新 blockhash 编译
            tip_msg = MessageV0.try_compile(
                payer_keypair.pubkey(),
                [tip_ix],
                [],
                recent_blockhash
            )
            signed_tip_tx = VersionedTransaction(tip_msg, [payer_keypair])

            # 4. 编码为 Base58 (Jito API 要求格式)
            b58_swap = base58.b58encode(bytes(signed_jupiter_tx)).decode('utf-8')
            b58_tip = base58.b58encode(bytes(signed_tip_tx)).decode('utf-8')

            # 5. 发送请求
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [[b58_swap, b58_tip]]
            }

            logger.info(f"🚀 发送 Jito Bundle... (节点: {self.engine_url})")

            # 参考旧项目的超时设置
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.engine_url, json=payload) as resp:
                    resp_data = await resp.json()

                    if resp.status != 200:
                        logger.error(f"❌ Jito API 请求失败 [{resp.status}]: {resp_data}")
                        return None

                    if "result" in resp_data:
                        bundle_id = resp_data["result"]
                        logger.success(f"✅ Bundle 已提交! ID: {bundle_id}")
                        return bundle_id
                    elif "error" in resp_data:
                        # 打印详细的 Jito 拒绝原因
                        logger.error(f"❌ Jito 拒绝交易包: {json.dumps(resp_data['error'])}")
                        return None
                    else:
                        logger.error(f"❌ Jito 响应异常格式: {resp_data}")
                        return None

        except Exception as e:
            logger.error(f"💥 Jito Bundle 构建异常: {str(e)}")
            return None