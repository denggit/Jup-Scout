# src/jito_client.py
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
from solana.rpc.async_api import AsyncClient
from config.settings import settings

class JitoClient:
    def __init__(self):
        self.engine_url = settings.JITO_ENGINE_URL
        self.tip_amount = settings.JITO_TIP_AMOUNT_SOL

    async def send_bundle(self, jupiter_tx_base64: str, payer_keypair: Keypair, additional_txs: list = None):
        """
        发送Jito Bundle，支持多个交易原子执行
        
        :param jupiter_tx_base64: 第一个Jupiter swap交易的base64编码
        :param payer_keypair: 支付者密钥对
        :param additional_txs: 额外的交易列表（base64编码），用于构建原子套利bundle
        :return: Bundle ID或错误信息
        """
        try:
            # 1. 获取最新 Blockhash (所有交易必须使用相同的blockhash以确保原子性)
            async with AsyncClient(settings.RPC_URL) as rpc_client:
                recent_blockhash = (await rpc_client.get_latest_blockhash()).value.blockhash

            # 2. 解析并签署所有Jupiter交易
            signed_txs = []
            
            # 处理第一个交易
            raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
            swap_tx = VersionedTransaction.from_bytes(raw_tx_bytes)
            signed_swap_tx = VersionedTransaction(swap_tx.message, [payer_keypair])
            signed_txs.append(signed_swap_tx)
            
            # 处理额外的交易（用于原子套利：第二个swap）
            if additional_txs:
                for additional_tx_base64 in additional_txs:
                    additional_raw = base64.b64decode(additional_tx_base64)
                    additional_tx = VersionedTransaction.from_bytes(additional_raw)
                    # 重新签署，使用相同的blockhash确保原子性
                    signed_additional_tx = VersionedTransaction(additional_tx.message, [payer_keypair])
                    signed_txs.append(signed_additional_tx)

            # 3. 构建并签署小费交易 (Tip) - 放在最后
            tip_account = random.choice(settings.JITO_TIP_ACCOUNTS).strip()
            tip_ix = transfer(TransferParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=Pubkey.from_string(tip_account),
                lamports=int(self.tip_amount * 10**9)
            ))
            tip_msg = MessageV0.try_compile(payer_keypair.pubkey(), [tip_ix], [], recent_blockhash)
            signed_tip_tx = VersionedTransaction(tip_msg, [payer_keypair])
            signed_txs.append(signed_tip_tx)

            # 4. 安全序列化所有交易
            try:
                b58_txs = []
                for signed_tx in signed_txs:
                    b58_tx = base58.b58encode(bytes(signed_tx)).decode('utf-8')
                    b58_txs.append(b58_tx)
            except Exception as e:
                logger.error(f"❌ 交易 Base58 编码失败: {e}")
                return None

            # 5. 构建Bundle payload
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [b58_txs]  # 所有交易打包在一起，确保原子执行
            }

            # 6. 发送请求
            async with aiohttp.ClientSession() as session:
                async with session.post(self.engine_url, json=payload, timeout=15) as resp:
                    data = await resp.json()
                    if resp.status == 429:
                        logger.error(f"⚠️ Jito 触发全局限流 (429)，请增加等待时间")
                        return "RATE_LIMITED"
                    if resp.status != 200:
                        logger.error(f"❌ Jito 拒绝: {data.get('error')}")
                        return None
                    return data.get("result")

        except Exception as e:
            logger.error(f"💥 Jito 模块异常: {str(e)}")
            return None
