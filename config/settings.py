#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/15/2026 11:59 PM
@File       : settings.py
@Description: 
"""
import os

from dotenv import load_dotenv
from solders.keypair import Keypair
from solders.pubkey import Pubkey

load_dotenv()


class Settings:
    # --- 基础配置 ---
    RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
    ENV = os.getenv("ENV", "DEV")

    # --- 核心: Jupiter API (V1) ---
    JUPITER_QUOTE_API = "https://api.jup.ag/swap/v1/quote"
    JUPITER_SWAP_API = "https://api.jup.ag/swap/v1/swap"

    # Jupiter API Key 池（用分号分隔多个 key，轮询使用以降低 429 概率）
    # 示例 .env: JUPITER_API_KEYS=key1;key2;key3
    _jupiter_keys_raw = os.getenv("JUPITER_API_KEYS", "")
    JUPITER_API_KEYS = [k.strip() for k in _jupiter_keys_raw.split(";") if k.strip()]
    # 兼容旧配置：若无 JUPITER_API_KEYS，则使用 JUPITER_API_KEY
    if not JUPITER_API_KEYS and os.getenv("JUPITER_API_KEY"):
        JUPITER_API_KEYS = [os.getenv("JUPITER_API_KEY").strip()]

    # --- 代币地址 (常量) ---
    # 路径中出现的代币必须在 settings 中配置 XX_MINT，否则会报错
    # 黄金规则：用 wSOL，不临时 wrap。SOL_MINT 即 wSOL mint；请提前创建好 wSOL ATA，bundle 内不 wrap/unwrap
    SOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    # 示例：若路径含 BONK，则添加 BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    # --- 套利路径（首尾必须为 USDC，中间为代币符号）---
    # 示例 .env: ARB_PATH=USDC,SOL,USDC  或  ARB_PATH=USDC,SOL,BONK,USDC
    _arb_path_raw = os.getenv("ARB_PATH", "USDC,SOL,USDC")
    ARB_PATH = [s.strip().upper() for s in _arb_path_raw.split(",") if s.strip()]
    if not ARB_PATH or ARB_PATH[0] != "USDC" or ARB_PATH[-1] != "USDC":
        ARB_PATH = ["USDC", "SOL", "USDC"]  # 默认

    @staticmethod
    def get_mint(symbol: str) -> str:
        """根据路径中的代币符号取 mint 地址；路径中出现的代币必须在 settings 中配置 XX_MINT。"""
        sym = (symbol or "").strip().upper()
        if not sym:
            raise ValueError("代币符号为空")
        mint = getattr(Settings, f"{sym}_MINT", None)
        if mint is None:
            raise ValueError(f"未配置 {sym}_MINT，请在 config/settings.py 或 .env 中配置该代币的 mint 地址")
        return mint

    # --- 精度换算 ---
    LAMPORT_PER_SOL = 1_000_000_000
    UNITS_PER_USDC = 1_000_000

    # 每次交易的USDC数量
    AMOUNT_USDC = 10

    # --- ⚡️ 成本与风控配置 (你的核心要求) ---
    # 1. 假定 SOL 价格 (用于快速计算 Gas 和 小费成本)
    # 设为 1000U 是一个保守策略：如果按 500U 算都能覆盖成本，实际肯定赚
    FIXED_SOL_PRICE_USDC = 500.0

    # 2. 预估 Gas 费 (Solana 基础费是 0.000005，我们按 2 个签名算 0.00001)
    ESTIMATED_GAS_SOL = 0.00002

    # 3. Jito 贿赂费 (起步给 0.0001 SOL, 约 $0.02 - $0.1)
    # 如果抢不到单，可以适当调高这个值 (比如 0.001)
    JITO_TIP_AMOUNT_SOL = 0.0001

    # 4. 最低净利润要求 (USDC)
    # 只有当 (预期利润 - 交易成本 - 贿赂成本) > 这个值，才开火
    MIN_NET_PROFIT_USDC = 0.01

    # --- Jito 引擎配置 ---
    # # 纽约节点 (延迟最低)
    # JITO_ENGINE_URL = "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles"

    # Jito 引擎 URL 池（分号分隔，轮询使用以降低 429 概率）
    # 示例 .env: JITO_ENGINE_URLS=https://mainnet.block-engine.jito.wtf/api/v1/bundles;https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles;https://am.mainnet.block-engine.jito.wtf/api/v1/bundles
    _jito_urls_raw = os.getenv("JITO_ENGINE_URLS", "")
    JITO_ENGINE_URLS = [u.strip() for u in _jito_urls_raw.split(";") if u.strip()]
    # 默认端点池（如果未配置环境变量）- 按优先级顺序
    if not JITO_ENGINE_URLS:
        JITO_ENGINE_URLS = [
            "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles",  # 第一优先级：纽约（延迟最低）
            "https://mainnet.block-engine.jito.wtf/api/v1/bundles",  # 第二优先级：主节点
            "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles",  # 第三优先级：法兰克福
            "https://tokyo.mainnet.block-engine.jito.wtf/api/v1/bundles"  # 第四优先级：东京（兜底）
        ]
    JITO_ENGINE_URL = JITO_ENGINE_URLS[0]  # 兼容旧代码

    # Jito 官方小费账户 (仅保留可解析为 Pubkey 的，避免 Invalid Base58)
    _JITO_TIP_ACCOUNTS_RAW = [
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
        "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
        "ADaUMid9yfUytqMBgopXSjb3uJC66ewJR605UwYJ7r3n",
        "DfXygSm4jCyNCybVYYK6DwvWqjKkNEqGdQI15a5Q1jvI",
        "ADuUkR4ykGytmnb5LHydo2iamqrpobyRGmurdZG5iDkD",
        "DttWaMuVvTiduZRNguLF8983agHzztVXiMVB3yKDhKS5",
        "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnIzKZ6jJ"
    ]
    JITO_TIP_ACCOUNTS = []
    for _a in _JITO_TIP_ACCOUNTS_RAW:
        a = (_a or "").strip().replace("\ufeff", "").replace("\r", "").replace("\n", "")
        if not a:
            continue
        try:
            Pubkey.from_string(a)
            JITO_TIP_ACCOUNTS.append(a)
        except Exception:
            pass
    if not JITO_TIP_ACCOUNTS:
        JITO_TIP_ACCOUNTS = list(_JITO_TIP_ACCOUNTS_RAW)

    # --- 钱包加载 ---
    try:
        private_key_string = os.getenv("PRIVATE_KEY")
        if not private_key_string:
            raise ValueError("找不到 PRIVATE_KEY")
        KEYPAIR = Keypair.from_base58_string(private_key_string)
        PUB_KEY = KEYPAIR.pubkey()
    except Exception as e:
        print(f"⚠️  私钥配置错误: {e}")
        KEYPAIR = None
        PUB_KEY = None


settings = Settings()
