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
import base58

# 加载 .env 文件
load_dotenv()


class Settings:
    # 基础配置
    RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
    ENV = os.getenv("ENV", "DEV")

    # ✅ 新地址 (Jupiter Swap API V1)
    # 官方文档: https://station.jup.ag/docs/apis/swap-api
    JUPITER_QUOTE_API = "https://api.jup.ag/swap/v1/quote"

    JUPITER_API_KEY=os.getenv("JUPITER_API_KEY")

    # 代币地址常量
    SOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # 这是一个常量，千万别改错

    # 精度常量 (用于数学计算)
    LAMPORT_PER_SOL = 1_000_000_000  # 10^9
    UNITS_PER_USDC = 1_000_000  # 10^6

    # 私钥处理 (自动把 Base58 转为 Keypair 对象)
    try:
        private_key_string = os.getenv("PRIVATE_KEY")
        if not private_key_string:
            raise ValueError("找不到 PRIVATE_KEY，请检查 .env 文件！")

        # 尝试解码
        KEYPAIR = Keypair.from_base58_string(private_key_string)
        PUB_KEY = KEYPAIR.pubkey()

    except Exception as e:
        print(f"⚠️  私钥配置错误: {e}")
        KEYPAIR = None
        PUB_KEY = None


# 实例化导出
settings = Settings()