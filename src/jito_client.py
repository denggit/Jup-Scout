# src/jito_client.py
import base58
import itertools
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


def _rebuild_message_with_blockhash(orig_message, recent_blockhash):
    """
    用统一的 recent_blockhash 重建 message，保证 bundle 内所有 tx 同 blockhash。
    Jupiter 返回的 tx 自带旧 blockhash，直接复用会导致 Jito 在一致性检查时拒绝 bundle。
    仅替换 blockhash，完整保留：
    - address_table_lookups（ALT）
    - header（writable/readonly 标志）
    - account_keys 顺序与 instructions
    这样 Jito simulation 能正确识别 write lock，不会误报 tip 未 write-lock。
    """
    msg = getattr(orig_message, "value", orig_message)
    if not isinstance(msg, MessageV0):
        return orig_message
    new_msg = MessageV0(
        msg.header,
        msg.account_keys,
        recent_blockhash,
        msg.instructions,
        msg.address_table_lookups,
    )
    return new_msg


class JitoClient:
    _url_iter = None

    def __init__(self):
        self.tip_amount = settings.JITO_TIP_AMOUNT_SOL
        if JitoClient._url_iter is None:
            JitoClient._url_iter = itertools.cycle(settings.JITO_ENGINE_URLS)

    def _get_engine_url(self):
        return next(JitoClient._url_iter)

    async def send_bundle(self, jupiter_tx_base64: str, payer_keypair: Keypair, additional_txs: list = None):
        """
        发送Jito Bundle，支持多个交易原子执行
        
        :param jupiter_tx_base64: 第一个Jupiter swap交易的base64编码
        :param payer_keypair: 支付者密钥对
        :param additional_txs: 额外的交易列表（base64编码），用于构建原子套利bundle
        :return: Bundle ID或错误信息
        """
        try:
            # 1. 先取统一的 recent_blockhash（bundle 内所有 tx 必须同 blockhash，否则 Jito 直接拒）
            async with AsyncClient(settings.RPC_URL) as rpc_client:
                recent_blockhash = (await rpc_client.get_latest_blockhash()).value.blockhash

            # 2. 解析 Jupiter 交易并用同一 blockhash 重建 message，再签署
            signed_txs = []

            def _parse_and_rebuild_swap(raw_tx_bytes, label="swap"):
                tx = VersionedTransaction.from_bytes(raw_tx_bytes)
                new_message = _rebuild_message_with_blockhash(tx.message, recent_blockhash)
                return VersionedTransaction(new_message, [payer_keypair])

            try:
                raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
                signed_swap_tx = _parse_and_rebuild_swap(raw_tx_bytes, "第一个swap")
                signed_txs.append(signed_swap_tx)
                logger.debug("✅ 第一个swap交易解析并签署成功（已统一 blockhash）")
            except Exception as e:
                logger.error(f"❌ 解析第一个交易失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return None

            if additional_txs:
                for idx, additional_tx_base64 in enumerate(additional_txs):
                    try:
                        additional_raw = base64.b64decode(additional_tx_base64)
                        signed_additional_tx = _parse_and_rebuild_swap(additional_raw, f"额外交易 {idx+1}")
                        signed_txs.append(signed_additional_tx)
                        logger.debug(f"✅ 额外交易 {idx+1} 解析并签署成功（已统一 blockhash）")
                    except Exception as e:
                        logger.error(f"❌ 解析额外交易 {idx+1} 失败: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        return None

            # 3. 构建小费交易 (Tip)，并选一个可解析的 tip 账户（避免 Invalid Base58）
            tip_pubkey = None
            candidates = list(settings.JITO_TIP_ACCOUNTS) or []
            random.shuffle(candidates)
            for raw in candidates:
                s = (raw or "").strip().replace("\ufeff", "").replace("\r", "").replace("\n", "")
                if not s:
                    continue
                try:
                    tip_pubkey = Pubkey.from_string(s)
                    break
                except Exception:
                    continue
            if tip_pubkey is None:
                logger.error("❌ 无有效 Jito tip 账户 (JITO_TIP_ACCOUNTS 均无法解析为 Base58)")
                return None
            tip_ix = transfer(TransferParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=tip_pubkey,
                lamports=int(self.tip_amount * 10**9)
            ))
            tip_msg = MessageV0.try_compile(payer_keypair.pubkey(), [tip_ix], [], recent_blockhash)
            signed_tip_tx = VersionedTransaction(tip_msg, [payer_keypair])
            # tip 必须是 bundle 最后一笔：[swap..., tip]。auction 顺序模拟时先执行 swap，tip 最后才能被正确计入 write-lock eligibility
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
                        
                        # Base58编码（确保为 bytes，避免异常编码）
                        try:
                            raw = bytes(tx_bytes) if not isinstance(tx_bytes, bytes) else tx_bytes
                            b58_tx = base58.b58encode(raw).decode("utf-8")
                            if not b58_tx or len(b58_tx) < 100:
                                logger.error(f"❌ 交易 {idx+1} Base58编码结果异常，长度: {len(b58_tx)}")
                                return None
                            b58_txs.append(b58_tx)
                            logger.debug(f"✅ 交易 {idx+1} Base58编码成功，长度: {len(b58_tx)}")
                        except Exception as e:
                            logger.error(f"❌ 交易 {idx+1} Base58编码失败: {type(e).__name__}: {e}")
                            logger.error(f"   tx_bytes 长度: {len(tx_bytes) if tx_bytes else 0}, 前32字节: {tx_bytes[:32].hex() if tx_bytes and len(tx_bytes) >= 32 else 'N/A'}")
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

            # 6. 发送请求（轮询 Jito 端点以降低 429）
            engine_url = self._get_engine_url()
            async with aiohttp.ClientSession() as session:
                async with session.post(engine_url, json=payload, timeout=15) as resp:
                    data = await resp.json()
                    if resp.status == 429:
                        logger.error(f"⚠️ Jito 触发全局限流 (429)，请增加等待时间")
                        return "RATE_LIMITED"
                    if resp.status != 200:
                        logger.error(f"❌ Jito 拒绝: {data.get('error')}")
                        return None
                    err = data.get("error")
                    if err:
                        msg = err.get("message", err) if isinstance(err, dict) else str(err)
                        logger.error(f"❌ Jito JSON-RPC 错误: {msg}")
                        return None
                    return data.get("result")

        except Exception as e:
            logger.error(f"💥 Jito 模块异常: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    async def get_bundle_status(self, bundle_id: str) -> dict | None:
        """
        查询 bundle 是否已上链。
        sendBundle 返回 bundle_id 仅表示已被 Jito 接受，不代表已上链。
        需用 getBundleStatuses 确认。
        """
        if not bundle_id:
            return None
        try:
            engine_url = self._get_engine_url()
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBundleStatuses",
                "params": [[bundle_id]],
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(engine_url, json=payload, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    result = data.get("result", {})
                    if isinstance(result, dict):
                        value = result.get("value")
                        if value and isinstance(value, list) and len(value) > 0:
                            return value[0]
                    return None
        except Exception as e:
            logger.debug(f"getBundleStatus 异常: {e}")
            return None
