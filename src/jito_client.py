# src/jito_client.py
import base58
import itertools
import aiohttp
import random
import base64
import time
from loguru import logger
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta, CompiledInstruction
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.address_lookup_table_account import AddressLookupTableAccount
from solana.rpc.async_api import AsyncClient
from config.settings import settings

# ALT 账户数据：前 56 字节为 meta，随后 4 字节为 address 数量 (u32 LE)，再 32*N 为地址
_ALT_META_SIZE = 56


def _parse_alt_addresses(data: bytes) -> list:
    if len(data) < _ALT_META_SIZE + 4:
        return []
    n = int.from_bytes(data[_ALT_META_SIZE : _ALT_META_SIZE + 4], "little")
    start = _ALT_META_SIZE + 4
    end = start + 32 * n
    if len(data) < end:
        return []
    return [Pubkey.from_bytes(data[start + i * 32 : start + (i + 1) * 32]) for i in range(n)]


# Vote account 前缀（Jito 禁止锁定 vote accounts）
_VOTE_ACCOUNT_PREFIXES = [
    "Vote111111111111111111111111111111111111111",
    "Vote111111111111111111111111111111111111112",
]

def _is_vote_account(pubkey: Pubkey) -> bool:
    """检查是否为 vote account"""
    key_str = str(pubkey)
    return any(key_str.startswith(prefix) for prefix in _VOTE_ACCOUNT_PREFIXES)


async def _fetch_alt_account(rpc_client: AsyncClient, lookup_table_pubkey: Pubkey) -> list:
    """从 RPC 拉取 ALT 账户并解析出 address 列表。"""
    try:
        resp = await rpc_client.get_account_info(lookup_table_pubkey, encoding="base64")
        if not resp.value or not resp.value.data:
            return []
        data = base64.b64decode(resp.value.data)
        return _parse_alt_addresses(data)
    except Exception as e:
        logger.debug(f"拉取 ALT {lookup_table_pubkey} 失败: {e}")
        return []


def _decompile_to_instructions(msg: MessageV0, full_account_keys: list, is_writable_by_index: dict) -> list:
    """将 MessageV0 的 CompiledInstruction 反编译为 Instruction，用于 try_compile。"""
    len_static = len(msg.account_keys)
    instructions = []
    for ci in msg.instructions:
        program_id_index = getattr(ci, "program_id_index", 0)
        accounts_bytes = getattr(ci, "accounts", b"")
        data = getattr(ci, "data", b"")
        if program_id_index >= len(full_account_keys):
            continue
        program_id = full_account_keys[program_id_index]
        account_metas = []
        for i in accounts_bytes:
            if i >= len(full_account_keys):
                continue
            account_key = full_account_keys[i]
            is_signer = msg.is_signer(i) if i < len_static and hasattr(msg, "is_signer") else False
            is_writable = is_writable_by_index.get(i, False)
            if _is_vote_account(account_key):
                is_writable = False
                logger.debug(f"🔒 检测到 vote account {account_key}，强制 readonly")
            account_metas.append(AccountMeta(account_key, is_signer, is_writable))
        instructions.append(Instruction(program_id, data, account_metas))
    return instructions


def _to_index_list(val) -> list:
    """将 writable_indexes/readonly_indexes 转为 index 列表，支持 bytes/list/tuple"""
    if val is None:
        return []
    if isinstance(val, bytes):
        return list(val)
    if isinstance(val, (list, tuple)):
        return list(val)
    return []


def _build_full_account_keys_and_alt_accounts(msg: MessageV0, alt_addresses_by_key: dict) -> tuple:
    """
    按 V0 顺序构建完整 account 列表，并构建 try_compile 所需的 AddressLookupTableAccount 列表。
    返回 (full_account_keys, address_lookup_table_accounts, is_writable_by_index)。
    """
    full_keys = list(msg.account_keys)
    is_writable_by_index = {}
    for i in range(len(msg.account_keys)):
        is_writable_by_index[i] = msg.is_maybe_writable(i) if hasattr(msg, "is_maybe_writable") else False
    lookup_accounts = []
    idx = len(msg.account_keys)
    for lookup in msg.address_table_lookups:
        key = lookup.account_key
        addresses = alt_addresses_by_key.get(key) or []
        lookup_accounts.append(AddressLookupTableAccount(key=key, addresses=addresses))
        writable = _to_index_list(getattr(lookup, "writable_indexes", []) or [])
        readonly = _to_index_list(getattr(lookup, "readonly_indexes", []) or [])
        for i in writable:
            if i < len(addresses):
                full_keys.append(addresses[i])
                is_writable_by_index[idx] = True
                idx += 1
        for i in readonly:
            if i < len(addresses):
                full_keys.append(addresses[i])
                is_writable_by_index[idx] = False
                idx += 1
    return full_keys, lookup_accounts, is_writable_by_index


async def _rebuild_message_with_blockhash_async(rpc_client: AsyncClient, orig_message, recent_blockhash):
    """
    用统一 blockhash 重建 message，通过拉取 ALT + 反编译 + try_compile 正确保留 writable/readonly，
    避免 Jito 报 "bundles cannot lock any vote accounts"。
    """
    msg = getattr(orig_message, "value", orig_message)
    if not isinstance(msg, MessageV0):
        return orig_message
    payer = msg.account_keys[0]
    alt_addresses_by_key = {}
    for lookup in msg.address_table_lookups:
        key = lookup.account_key
        if key not in alt_addresses_by_key:
            alt_addresses_by_key[key] = await _fetch_alt_account(rpc_client, key)
    full_keys, address_lookup_table_accounts, is_writable_by_index = _build_full_account_keys_and_alt_accounts(msg, alt_addresses_by_key)
    instructions = _decompile_to_instructions(msg, full_keys, is_writable_by_index)
    if not instructions:
        logger.error("反编译得到 0 条 instruction，拒绝使用裸构造（会导致 vote account lock）")
        raise ValueError("decompile failed: no instructions")
    try:
        return MessageV0.try_compile(
            payer,
            instructions,
            address_lookup_table_accounts,
            recent_blockhash,
        )
    except Exception as e:
        logger.error(f"try_compile 失败 ({e})，拒绝使用裸构造（会导致 vote account lock）")
        raise


class JitoClient:

    def __init__(self):
        self.tip_amount = settings.JITO_TIP_AMOUNT_SOL
        self._rate_limited_until = 0.0
        self._bundle_engine_map = {}
        self._engine_cooldown = {}  # 端点冷却时间记录 {url: 冷却结束时间戳}

    def _get_engine_url(self):
        """获取第一个不在冷却中的端点（按优先级顺序）"""
        now = time.time()
        # 按优先级顺序检查所有端点
        for engine_url in settings.JITO_ENGINE_URLS:
            cooldown_until = self._engine_cooldown.get(engine_url, 0)
            if now >= cooldown_until:
                return engine_url

        # 所有端点都在冷却中，返回冷却时间最短的
        if self._engine_cooldown:
            return min(self._engine_cooldown.items(), key=lambda x: x[1])[0]

        # 回退到第一个端点
        return settings.JITO_ENGINE_URLS[0] if settings.JITO_ENGINE_URLS else ""

    def _set_engine_cooldown(self, engine_url, retry_after=None):
        """标记特定端点进入冷却"""
        base_cooldown = 45
        if retry_after:
            try:
                base_cooldown = max(base_cooldown, int(float(retry_after)))
            except Exception:
                pass
        current = self._engine_cooldown.get(engine_url, 0)
        now = time.time()
        if current > now:
            base_cooldown = int((current - now) * 2.5)
        self._engine_cooldown[engine_url] = now + base_cooldown
        return base_cooldown

    def _set_all_engines_cooldown(self, retry_after=None):
        """任一端点触发限流时，全端点一起冷却"""
        cooldown = self._set_rate_limit_cooldown(retry_after)
        now = time.time()
        end_time = now + cooldown
        for url in settings.JITO_ENGINE_URLS:
            self._engine_cooldown[url] = end_time
        return cooldown

    @staticmethod
    async def _post_json_rpc(engine_url: str, payload: dict, timeout: int = 10):
        async with aiohttp.ClientSession() as session:
            async with session.post(engine_url, json=payload, timeout=timeout) as resp:
                data = await resp.json(content_type=None)
                return resp.status, data, resp.headers

    def _set_rate_limit_cooldown(self, retry_after_header=None):
        retry_after = 0
        try:
            retry_after = int(float(retry_after_header)) if retry_after_header else 0
        except Exception:
            retry_after = 0
        cooldown = max(45, retry_after)  # 从30秒增加到45秒
        self._rate_limited_until = max(self._rate_limited_until, time.time() + cooldown)
        return cooldown

    def get_rate_limit_wait_seconds(self) -> int:
        return max(0, int(self._rate_limited_until - time.time()))

    async def send_bundle(self, jupiter_tx_base64: str, payer_keypair: Keypair, additional_txs: list = None):
        """
        发送Jito Bundle，支持多个交易原子执行
        
        :param jupiter_tx_base64: 第一个Jupiter swap交易的base64编码
        :param payer_keypair: 支付者密钥对
        :param additional_txs: 额外的交易列表（base64编码），用于构建原子套利bundle
        :return: Bundle ID或错误信息
        """
        try:
            wait_seconds = self.get_rate_limit_wait_seconds()
            if wait_seconds > 0:
                logger.warning(f"⏳ Jito 全局冷却中，剩余 {wait_seconds} 秒")
                return "RATE_LIMITED"

            # 1. 取统一 blockhash，并在同一 RPC 会话内拉取 ALT、用 try_compile 重建 swap message
            async with AsyncClient(settings.RPC_URL) as rpc_client:
                recent_blockhash = (await rpc_client.get_latest_blockhash()).value.blockhash

                signed_txs = []

                async def _parse_and_rebuild_swap(raw_tx_bytes):
                    tx = VersionedTransaction.from_bytes(raw_tx_bytes)
                    new_message = await _rebuild_message_with_blockhash_async(
                        rpc_client, tx.message, recent_blockhash
                    )
                    return VersionedTransaction(new_message, [payer_keypair])

                try:
                    raw_tx_bytes = base64.b64decode(jupiter_tx_base64)
                    signed_swap_tx = await _parse_and_rebuild_swap(raw_tx_bytes)
                    signed_txs.append(signed_swap_tx)
                    logger.debug("✅ 第一个swap交易解析并签署成功（已统一 blockhash + try_compile）")
                except Exception as e:
                    logger.error(f"❌ 解析第一个交易失败: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    return None

                if additional_txs:
                    for idx, additional_tx_base64 in enumerate(additional_txs):
                        try:
                            additional_raw = base64.b64decode(additional_tx_base64)
                            signed_additional_tx = await _parse_and_rebuild_swap(additional_raw)
                            signed_txs.append(signed_additional_tx)
                            logger.debug(f"✅ 额外交易 {idx+1} 解析并签署成功（已统一 blockhash + try_compile）")
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

            # 4.1 验证交易：确保没有 vote accounts 被锁定为 writable
            for idx, signed_tx in enumerate(signed_txs):
                msg = signed_tx.message
                for i, key in enumerate(msg.account_keys):
                    if _is_vote_account(key):
                        is_writable = msg.is_maybe_writable(i) if hasattr(msg, "is_maybe_writable") else False
                        if is_writable:
                            logger.error(f"❌ 交易 {idx+1} 锁定 vote account {key} 为 writable，拒绝发送")
                            return "VOTE_ACCOUNT_LOCKED"
                logger.debug(f"✅ 交易 {idx+1} 验证通过，无 vote accounts 锁定")

            # 4.2 安全序列化所有交易为Base58格式（Jito Bundle要求）
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

            # 5. 构建 Bundle payload
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",  # Jito JSON-RPC 方法名固定为 sendBundle
                "params": [b58_txs]  # 所有交易打包在一起，确保原子执行
            }

            # 6. 按优先级尝试所有 Jito 端点，若有 429 则全端点一起冷却
            got_rate_limited = False
            retry_after_header = None
            for engine_url in settings.JITO_ENGINE_URLS:
                now = time.time()
                cooldown_until = self._engine_cooldown.get(engine_url, 0)
                if now < cooldown_until:
                    remaining = int(cooldown_until - now)
                    logger.info(f"⏳ 端点 {engine_url} 冷却中，剩余 {remaining} 秒，跳过")
                    continue

                logger.info(f"📡 尝试使用端点: {engine_url}")
                status, data, headers = await self._post_json_rpc(engine_url, payload, timeout=15)

                if status == 429:
                    logger.error(f"⚠️ 端点 {engine_url} 触发限流")
                    got_rate_limited = True
                    retry_after_header = retry_after_header or headers.get("Retry-After")
                    continue

                err = data.get("error") if isinstance(data, dict) else None
                if err:
                    err_msg = err.get("message", err) if isinstance(err, dict) else str(err)
                    logger.error(f"❌ Jito 端点 {engine_url} 拒绝: {err_msg}")

                    if "429" in str(err_msg).lower() or "rate" in str(err_msg).lower():
                        got_rate_limited = True
                        continue

                    # vote account 等 bundle 无效错误：不再尝试其他端点
                    if "vote" in str(err_msg).lower() or "lock" in str(err_msg).lower():
                        return "VOTE_ACCOUNT_LOCKED"
                    continue

                if status != 200:
                    logger.error(f"❌ Jito 端点 {engine_url} HTTP {status}: {data}")
                    continue

                bundle_id = data.get("result") if isinstance(data, dict) else None
                if bundle_id:
                    self._bundle_engine_map[bundle_id] = engine_url
                    logger.success(f"✅ 端点 {engine_url} 成功接受Bundle! Bundle ID: {bundle_id}")
                    return bundle_id

                logger.warning(f"⚠️ 端点 {engine_url} 返回空 bundle_id")
                continue

            # 若有端点触发限流，全端点一起冷却
            if got_rate_limited:
                cooldown = self._set_all_engines_cooldown(retry_after_header)
                logger.warning(f"⏳ 全端点进入 {cooldown} 秒冷却")
                return "RATE_LIMITED"
            return None

        except Exception as e:
            logger.error(f"💥 Jito 模块异常: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    async def get_bundle_status(self, bundle_id: str) -> dict | None:
        """
        查询 bundle 是否已上链。
        send_bundle 返回 bundle_id 仅表示已被 Jito 接受，不代表已上链。
        需用 getBundleStatuses 确认。
        """
        if not bundle_id:
            return None
        try:
            engine_url = self._bundle_engine_map.get(bundle_id) or self._get_engine_url()
            status_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBundleStatuses",
                "params": [[bundle_id]],
            }

            inflight_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getInflightBundleStatuses",
                "params": [[bundle_id]],
            }

            merged = {}

            status_code, data, _ = await self._post_json_rpc(engine_url, status_payload, timeout=10)
            if status_code == 200 and isinstance(data, dict):
                result = data.get("result", {})
                if isinstance(result, dict):
                    value = result.get("value")
                    if value and isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                        merged.update(value[0])

            inflight_code, inflight_data, _ = await self._post_json_rpc(engine_url, inflight_payload, timeout=10)
            if inflight_code == 200 and isinstance(inflight_data, dict):
                inflight_result = inflight_data.get("result", {})
                if isinstance(inflight_result, dict):
                    inflight_value = inflight_result.get("value")
                    if inflight_value and isinstance(inflight_value, list) and len(inflight_value) > 0 and isinstance(inflight_value[0], dict):
                        merged.update(inflight_value[0])

            return merged or None
        except Exception as e:
            logger.debug(f"getBundleStatus 异常: {e}")
            return None
