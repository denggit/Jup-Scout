#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/15/2026 11:54 PM
@File       : main.py
@Description: 
"""
import asyncio
from loguru import logger
from config.settings import settings
from src.jupiter import JupiterClient
from solana.rpc.async_api import AsyncClient


async def main():
    logger.add("logs/jup_scout.log", rotation="10 MB")  # 日志文件
    logger.info("🚀 Jup-Scout (MVP版) 正在启动...")

    # 1. 检查配置
    if not settings.KEYPAIR:
        logger.error("❌ 私钥未配置，程序退出")
        return
    logger.info(f"当前钱包地址: {settings.PUB_KEY}")
    logger.info(f"当前 RPC: {settings.RPC_URL.split('api-key=')[0]+'*****'+settings.RPC_URL.split('-')[-1]}")

    # 2. 连接 Solana 链检查余额
    async with AsyncClient(settings.RPC_URL) as solana_client:
        try:
            balance_resp = await solana_client.get_balance(settings.PUB_KEY)
            balance = balance_resp.value / 1e9
            logger.info(f"💰 钱包余额: {balance:.4f} SOL")
        except Exception as e:
            logger.error(f"连接 RPC 失败: {e}")
            return

    # 3. 运行一次套利模拟
    jup_client = JupiterClient()

    # 注意：虽然是 USDC 本位，但你的钱包里必须留一点 SOL！
    # 因为 Solana 链上的 Gas 费 (手续费) 必须用 SOL 支付，不能用 USDC 支付。

    # 模拟投入 100 USDC
    # 100 * 10^6 = 100,000,000
    invest_amount = 100 * settings.UNITS_PER_USDC

    jup_client = JupiterClient()
    logger.info("⚡ 启动 USDC 本位套利引擎...")

    # 跑一次测试
    await jup_client.check_arb_opportunity(invest_amount)

    logger.info("✅ MVP 测试结束. 你已经成功连通了 Jupiter!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序已停止")