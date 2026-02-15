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
            try:
                raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
                swap_tx = VersionedTransaction.from_bytes(raw_tx_bytes)
                # 重新签署交易，确保使用我们的密钥对
                signed_swap_tx = VersionedTransaction(swap_tx.message, [payer_keypair])
                signed_txs.append(signed_swap_tx)
                logger.debug("✅ 第一个swap交易解析并签署成功")
            except Exception as e:
                logger.error(f"❌ 解析第一个交易失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return None
            
            # 处理额外的交易（用于原子套利：第二个swap）
            if additional_txs:
                for idx, additional_tx_base64 in enumerate(additional_txs):
                    try:
                        additional_raw = base64.b64decode(additional_tx_base64)
                        additional_tx = VersionedTransaction.from_bytes(additional_raw)
                        # 重新签署，使用相同的blockhash确保原子性
                        signed_additional_tx = VersionedTransaction(additional_tx.message, [payer_keypair])
                        signed_txs.append(signed_additional_tx)
                        logger.debug(f"✅ 额外交易 {idx+1} 解析并签署成功")
                    except Exception as e:
                        logger.error(f"❌ 解析额外交易 {idx+1} 失败: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        return None

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

            # 4. 安全序列化所有交易为Base58格式（Jito Bundle要求）
            try:
                b58_txs = []
                for idx, signed_tx in enumerate(signed_txs):
                    try:
                        # VersionedTransaction序列化：尝试多种方式确保正确序列化
                        tx_bytes = None
                        
                        # 方法1：直接转换为bytes（solders的标准方式）
                        try:
                            tx_bytes = bytes(signed_tx)
                            if len(tx_bytes) > 0:
                                logger.debug(f"✅ 交易 {idx+1} 使用方法1序列化成功，长度: {len(tx_bytes)}")
                        except Exception as e1:
                            logger.warning(f"⚠️ 交易 {idx+1} 方法1序列化失败: {e1}")
                            
                            # 方法2：尝试使用serialize方法（如果存在）
                            if hasattr(signed_tx, 'serialize'):
                                try:
                                    tx_bytes = signed_tx.serialize()
                                    logger.debug(f"✅ 交易 {idx+1} 使用方法2序列化成功，长度: {len(tx_bytes)}")
                                except Exception as e2:
                                    logger.warning(f"⚠️ 交易 {idx+1} 方法2序列化失败: {e2}")
                            
                            # 方法3：尝试使用to_bytes方法（如果存在）
                            if tx_bytes is None and hasattr(signed_tx, 'to_bytes'):
                                try:
                                    tx_bytes = signed_tx.to_bytes()
                                    logger.debug(f"✅ 交易 {idx+1} 使用方法3序列化成功，长度: {len(tx_bytes)}")
                                except Exception as e3:
                                    logger.warning(f"⚠️ 交易 {idx+1} 方法3序列化失败: {e3}")
                        
                        if tx_bytes is None or len(tx_bytes) == 0:
                            logger.error(f"❌ 交易 {idx+1} 所有序列化方法都失败")
                            return None
                        
                        # Base58编码
                        try:
                            b58_tx = base58.b58encode(tx_bytes).decode('utf-8')
                            if not b58_tx or len(b58_tx) < 100:  # Base58编码的交易应该很长
                                logger.error(f"❌ 交易 {idx+1} Base58编码结果异常，长度: {len(b58_tx)}")
                                logger.error(f"   原始bytes长度: {len(tx_bytes)}")
                                return None
                            b58_txs.append(b58_tx)
                            logger.debug(f"✅ 交易 {idx+1} Base58编码成功，长度: {len(b58_tx)}")
                        except Exception as e:
                            logger.error(f"❌ 交易 {idx+1} Base58编码失败: {e}")
                            logger.error(f"   tx_bytes类型: {type(tx_bytes)}, 长度: {len(tx_bytes) if tx_bytes else 0}")
                            import traceback
                            logger.error(traceback.format_exc())
                            return None
                            
                    except Exception as e:
                        logger.error(f"❌ 交易 {idx+1} 处理过程异常: {e}")
                        logger.error(f"   交易类型: {type(signed_tx)}")
                        import traceback
                        logger.error(traceback.format_exc())
                        return None
            except Exception as e:
                logger.error(f"❌ 交易序列化过程异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
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
