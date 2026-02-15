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
        参考 SmartFlow3 成功经验修复版
        """
        try:
            # 1. 获取最新 Blockhash (确保时效性)
            async with AsyncClient(settings.RPC_URL) as rpc_client:
                recent_blockhash = (await rpc_client.get_latest_blockhash()).value.blockhash

            # 2. 解析 Jupiter 返回的原始交易
            raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
            swap_tx = VersionedTransaction.from_bytes(raw_tx_bytes)

            # 3. 构建小费交易 (解决 400 错误的关键：账户锁定)
            tip_acc_str = random.choice(settings.JITO_TIP_ACCOUNTS).strip()
            tip_account_pubkey = Pubkey.from_string(tip_acc_str)

            tip_ix = transfer(TransferParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=tip_account_pubkey,
                lamports=int(self.tip_amount * 10 ** 9)
            ))

            # 编译消息：显式包含小费账户并确保其在指令中被正确引用
            tip_msg = MessageV0.try_compile(
                payer_keypair.pubkey(),
                [tip_ix],
                [],  # 不引用额外的查找表
                recent_blockhash
            )
            signed_tip_tx = VersionedTransaction(tip_msg, [payer_keypair])

            # 4. 重新签署 Swap 交易 (修复 Invalid Base58)
            # 必须使用 payer 完整重签，确保 bytes(tx) 序列化成功
            signed_swap_tx = VersionedTransaction(swap_tx.message, [payer_keypair])

            # 5. 序列化编码 (参考 SmartFlow3 的严格模式)
            try:
                b58_swap = base58.b58encode(bytes(signed_swap_tx)).decode('utf-8')
                b58_tip = base58.b58encode(bytes(signed_tip_tx)).decode('utf-8')
            except Exception as e:
                logger.error(f"❌ 序列化失败 (Base58异常): {e}")
                return None

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [[b58_swap, b58_tip]]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                        self.engine_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        logger.error(f"❌ Jito 拒绝 [{resp.status}]: {data.get('error')}")
                        return None

                    bundle_id = data.get("result")
                    if bundle_id:
                        logger.success(f"✅ Bundle 成功提交! ID: {bundle_id}")
                    return bundle_id

        except Exception as e:
            logger.error(f"💥 Jito 发送流程异常: {str(e)}")
            return None