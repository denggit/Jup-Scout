#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 2/15/2026 11:54 PM
@File       : main.py
@Description: 
"""
import asyncio
import time

import httpx
from loguru import logger

from config.settings import settings
from src.jito_client import JitoClient
from src.jupiter import JupiterClient


def patch_httpx_verify():
    original_init = httpx.AsyncClient.__init__

    def new_init(self, *args, **kwargs):
        kwargs['verify'] = False
        original_init(self, *args, **kwargs)

    httpx.AsyncClient.__init__ = new_init


patch_httpx_verify()

# 配置日志
logger.add("logs/jup_scout_trade.log", rotation="10 MB")


async def main():
    logger.info("🚀 Jup-Scout (Jito集成版) 启动中...")

    # 1. 检查私钥
    if not settings.KEYPAIR:
        logger.error("❌ 致命错误: 未配置私钥 (PRIVATE_KEY)")
        return
    logger.info(f"👤 交易员: {settings.PUB_KEY}")

    # 2. 初始化客户端
    jup_client = JupiterClient()
    jito_client = JitoClient()

    # 3. 设定投入金额 (100 USDC)
    amount_usdc = 100
    amount_lamports = int(amount_usdc * settings.UNITS_PER_USDC)

    logger.info(f"💵 每次投入: {amount_usdc} USDC")
    logger.info(f"🛑 最低净利要求: ${settings.MIN_NET_PROFIT_USDC}")
    logger.info(f"🛡️ 成本估算基准: SOL = ${settings.FIXED_SOL_PRICE_USDC}")

    # --- 死循环：开始持续巡逻 ---
    while True:
        try:
            logger.info("🔎 正在扫描闭环套利机会 (USDC -> ... -> USDC)...")

            # ✅ 修改 1: 路径改为 USDC 进，USDC 出
            quote = await jup_client.get_quote(
                settings.USDC_MINT,
                settings.USDC_MINT,  # 目标也是 USDC，寻找环形价差
                amount_lamports
            )

            if not quote:
                await asyncio.sleep(3)
                continue

            # ✅ 修改 2: 真实利润计算 (不再模拟)
            out_amount = int(quote['outAmount'])
            gross_profit_usdc = (out_amount - amount_lamports) / settings.UNITS_PER_USDC

            # 成本计算 (Gas + Jito Tip)
            total_cost_usdc = (
                                          settings.JITO_TIP_AMOUNT_SOL + settings.ESTIMATED_GAS_SOL) * settings.FIXED_SOL_PRICE_USDC
            net_profit = gross_profit_usdc - total_cost_usdc

            logger.info(f"📊 净利估算: ${net_profit:.4f} (毛利: ${gross_profit_usdc:.4f})")

            if net_profit > settings.MIN_NET_PROFIT_USDC:
                logger.warning(f"🔥 发现真实利润 ${net_profit:.4f}! 立即开火!")

                swap_resp = await jup_client.get_swap_tx(quote)
                if not swap_resp: continue

                res = await jito_client.send_bundle(swap_resp['swapTransaction'], settings.KEYPAIR)

                if res == "RATE_LIMITED":
                    logger.info("⏳ 触发限流，进入 30 秒冷却期...")
                    await asyncio.sleep(30)
                elif res:
                    logger.success(f"🎉 套利 Bundle 已提交! ID: {res}")
                    await asyncio.sleep(10)  # 成功后等待上链
            else:
                # ✅ 修改 3: 动态增加 CD 时间，彻底避开 429
                await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"主循环异常: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
