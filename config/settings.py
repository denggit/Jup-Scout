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
    SOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    # --- 精度换算 ---
    LAMPORT_PER_SOL = 1_000_000_000
    UNITS_PER_USDC = 1_000_000

    # 每次交易的USDC数量
    AMOUNT_USDC = 100

    # --- ⚡️ 成本与风控配置 (你的核心要求) ---
    # 1. 假定 SOL 价格 (用于快速计算 Gas 和 小费成本)
    # 设为 1000U 是一个保守策略：如果按 500U 算都能覆盖成本，实际肯定赚
    FIXED_SOL_PRICE_USDC = 500.0

    # 2. 预估 Gas 费 (Solana 基础费是 0.000005，我们按 2 个签名算 0.00001)
    ESTIMATED_GAS_SOL = 0.00001

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
    # 示例 .env: JITO_ENGINE_URLS=https://mainnet.block-engine.jito.wtf/api/v1/bundles;https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles
    JITO_ENGINE_URLS = ["https://mainnet.block-engine.jito.wtf/api/v1/bundles", "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles"]
    if not JITO_ENGINE_URLS:
        JITO_ENGINE_URLS = ["https://mainnet.block-engine.jito.wtf/api/v1/bundles"]
    JITO_ENGINE_URL = JITO_ENGINE_URLS[0]  # 兼容旧代码

    # Jito 官方小费账户 (随机选一个转账)
    JITO_TIP_ACCOUNTS = [
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
        "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
        "ADaUMid9yfUytqMBgopXSjb3uJC66ewJR605UwYJ7r3n",
        "DfXygSm4jCyNCybVYYK6DwvWqjKkNEqGdQI15a5Q1jvI",
        "ADuUkR4ykGytmnb5LHydo2iamqrpobyRGmurdZG5iDkD",
        "DttWaMuVvTiduZRNguLF8983agHzztVXiMVB3yKDhKS5",
        "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnIzKZ6jJ"
    ]

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
