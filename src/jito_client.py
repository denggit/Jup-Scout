# src/jito_client.py
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
        try:
            # 1. 获取最新 Blockhash (参考 SmartFlow3 逻辑)
            async with AsyncClient(settings.RPC_URL) as rpc_client:
                recent_blockhash = (await rpc_client.get_latest_blockhash()).value.blockhash

            # 2. 解析 Jupiter 交易
            raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
            swap_tx = VersionedTransaction.from_bytes(raw_tx_bytes)

            # 3. 构建并签署小费交易 (Tip)
            tip_account = random.choice(settings.JITO_TIP_ACCOUNTS).strip()
            tip_ix = transfer(TransferParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=Pubkey.from_string(tip_account),
                lamports=int(self.tip_amount * 10**9)
            ))
            tip_msg = MessageV0.try_compile(payer_keypair.pubkey(), [tip_ix], [], recent_blockhash)
            signed_tip_tx = VersionedTransaction(tip_msg, [payer_keypair])

            # 4. 重新签署 Swap 交易 (关键：确保使用 payer_keypair 完整覆盖签名位)
            signed_swap_tx = VersionedTransaction(swap_tx.message, [payer_keypair])

            # 5. 参考 SmartFlow3 的严格编码逻辑 (解决 Invalid Base58)
            b58_swap = base58.b58encode(bytes(signed_swap_tx)).decode('utf-8')
            b58_tip = base58.b58encode(bytes(signed_tip_tx)).decode('utf-8')

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [[b58_swap, b58_tip]]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(self.engine_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        logger.error(f"❌ Jito 拒绝 [{resp.status}]: {data.get('error')}")
                        return None
                    return data.get("result")

        except Exception as e:
            logger.error(f"💥 Jito 构建异常: {str(e)}")
            return None