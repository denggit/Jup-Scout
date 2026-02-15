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
from loguru import logger
from config.settings import settings
from src.jupiter import JupiterClient
from src.jito_client import JitoClient
from solana.rpc.async_api import AsyncClient

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
            start_time = time.time()

            # A. 询价 (USDC -> SOL -> USDC)
            # 这里简化逻辑，先做第一腿询价演示，实际套利需要更复杂的路径搜索
            # 暂时我们先测试 "能否跑通整个交易流程"

            # 注意：真实的套利通常是找特定代币，这里为了演示 Jito 上链，
            # 我们模拟一个 "USDC -> SOL" 的单边买入，或者你可以换成其他你认为有价差的币
            # 为了安全起见，我们先不做自动循环买卖，而是 "运行一次测试"
            # 如果你想做循环监控，就把下面的 break 去掉

            logger.info("🔎 正在询价...")

            # --- 模拟：获取 USDC -> SOL 的报价 ---
            quote = await jup_client.get_quote(
                settings.USDC_MINT,
                settings.SOL_MINT,
                amount_lamports
            )

            if not quote:
                await asyncio.sleep(1)
                continue

            # B. 算账 (核心逻辑)
            out_amount_lamports = int(quote['outAmount'])

            # 把 SOL 换算回 USDC 价值 (基于我们的 1000U 假设)
            # 实际套利中，这里应该是第二腿 (SOL -> USDC) 的询价结果
            # 但为了演示 Jito 发送，我们假设这就是最终结果

            # 假设：我们通过某种神操作，这一单能赚 0.5 USDC (这里强行模拟一个利润，为了触发交易)
            # 在真实代码里，这里应该是: estimated_profit = final_usdc - input_usdc
            estimated_profit_usdc = 0.5  # <--- 强行模拟利润，测试 Jito 是否工作！

            # C. 计算成本 (USDC)
            # 成本 = (Jito小费 + Gas费) * SOL价格
            total_cost_sol = settings.JITO_TIP_AMOUNT_SOL + settings.ESTIMATED_GAS_SOL
            total_cost_usdc = total_cost_sol * settings.FIXED_SOL_PRICE_USDC

            net_profit = estimated_profit_usdc - total_cost_usdc

            logger.info(f"📊 财务分析:")
            logger.info(f"   预期毛利: ${estimated_profit_usdc:.4f}")
            logger.info(f"   预估成本: ${total_cost_usdc:.4f} (Tip: {settings.JITO_TIP_AMOUNT_SOL} SOL)")
            logger.info(f"   预期净利: ${net_profit:.4f}")

            # D. 决策开火
            if net_profit > settings.MIN_NET_PROFIT_USDC:
                logger.warning("🔥 发现利润！准备开火！")

                # 1. 获取交易数据
                swap_resp = await jup_client.get_swap_tx(quote)
                if not swap_resp: continue

                tx_base64 = swap_resp['swapTransaction']

                # 2. 发送 Jito Bundle
                bundle_id = await jito_client.send_bundle(tx_base64, settings.KEYPAIR)

                if bundle_id:
                    logger.success(f"🎉 交易已提交! Bundle ID: {bundle_id}")
                    # 真实跑的时候，这里可以 break 或者 sleep 此时
                    break
            else:
                logger.info("📉 利润不足，跳过...")

            # 避免 API 限流，稍作休息
            await asyncio.sleep(2)

        except KeyboardInterrupt:
            logger.info("用户停止脚本")
            break
        except Exception as e:
            logger.error(f"主循环异常: {e}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())