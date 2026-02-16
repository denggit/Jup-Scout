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
import random

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

    # 3. 设定投入金额
    amount_usdc = settings.AMOUNT_USDC
    amount_lamports = int(amount_usdc * settings.UNITS_PER_USDC)

    logger.info(f"💵 每次投入: {amount_usdc} USDC")
    logger.info(f"🛑 最低净利要求: ${settings.MIN_NET_PROFIT_USDC}")
    logger.info(f"🛡️ 成本估算基准: SOL = ${settings.FIXED_SOL_PRICE_USDC}")
    if settings.JUPITER_API_KEYS:
        logger.info(f"🔑 Jupiter API Key 池: {len(settings.JUPITER_API_KEYS)} 个")
    if len(settings.JITO_ENGINE_URLS) > 1:
        logger.info(f"🌐 Jito 端点池: {len(settings.JITO_ENGINE_URLS)} 个")

    # --- 死循环：开始持续巡逻 ---
    while True:
        try:
            # 限流冷却期间不扫描，直接等待冷却结束
            rate_limit_wait = jito_client.get_rate_limit_wait_seconds()
            if rate_limit_wait > 0:
                logger.info(f"⏳ Jito 冷却中，剩余 {rate_limit_wait} 秒，暂停扫描...")
                await asyncio.sleep(min(rate_limit_wait, 5))  # 每5秒检查一次，避免长时间阻塞
                continue

            logger.info("🔎 正在扫描闭环套利机会 (USDC -> SOL -> USDC)...")

            # 使用check_arb_opportunity方法检查套利机会
            arb_result = await jup_client.check_arb_opportunity(amount_lamports)

            if not arb_result:
                # 未发现套利机会或询价失败，等待后继续（随机延迟避免规律请求）
                await asyncio.sleep(random.uniform(3, 6))  # 增加间隔以减少限流
                continue

            # 检查净利润是否满足最低要求
            net_profit = arb_result['net_profit_usdc']
            gross_profit = arb_result['gross_profit_usdc']
            
            # 调试日志：显示详细的利润信息
            logger.debug(f"📊 利润分析: 净利润=${net_profit:.6f}, 最低要求=${settings.MIN_NET_PROFIT_USDC:.6f}")
            logger.debug(f"   判断条件: net_profit > MIN_NET_PROFIT_USDC => {net_profit:.6f} > {settings.MIN_NET_PROFIT_USDC:.6f} = {net_profit > settings.MIN_NET_PROFIT_USDC}")

            # 关键：只有净利润大于最低要求时才执行套利（确保不会亏损）
            if net_profit > settings.MIN_NET_PROFIT_USDC:
                logger.warning(f"🔥 发现套利机会! 净利润: ${net_profit:.4f} USDC (毛利: ${gross_profit:.4f} USDC)")
                
                # 执行原子套利：构建包含两个swap的原子bundle
                logger.info("📦 构建原子套利交易bundle (USDC->SOL->USDC)...")
                
                # 关键：为了确保原子性，我们需要快速连续获取两个swap交易
                # 这样它们会使用相同或非常接近的blockhash
                # 1. 获取第一个swap交易 (USDC -> SOL)
                swap_buy_resp = await jup_client.get_swap_tx(arb_result['quote_buy'])
                if not swap_buy_resp:
                    logger.error("❌ 获取第一个swap交易失败 (USDC -> SOL)")
                    await asyncio.sleep(3)
                    continue

                # 2. 立即获取第二个swap交易 (SOL -> USDC)
                # 注意：第二个swap使用第一个swap的输出数量，确保闭环
                swap_sell_resp = await jup_client.get_swap_tx(arb_result['quote_sell'])
                if not swap_sell_resp:
                    logger.error("❌ 获取第二个swap交易失败 (SOL -> USDC)")
                    await asyncio.sleep(3)
                    continue

                # 3. 将两个swap交易打包成原子bundle并发送
                # 关键：两个swap在同一个bundle中，要么全部成功，要么全部失败
                # Bundle执行顺序：swap1 (USDC->SOL) -> swap2 (SOL->USDC) -> tip
                # Jito Bundle的原子性保证：如果任何一个swap失败，整个bundle都会回滚
                logger.info("🔒 打包原子bundle，确保零风险套利...")
                res = await jito_client.send_bundle(
                    swap_buy_resp['swapTransaction'],  # 第一个swap
                    settings.KEYPAIR,
                    additional_txs=[swap_sell_resp['swapTransaction']]  # 第二个swap，确保原子执行
                )

                if res == "RATE_LIMITED":
                    cooldown = max(30, jito_client.get_rate_limit_wait_seconds())
                    logger.info(f"⏳ 触发限流，进入 {cooldown} 秒冷却期...")
                    await asyncio.sleep(cooldown)
                elif res == "VOTE_ACCOUNT_LOCKED":
                    logger.error("❌ 交易锁定vote accounts，跳过此套利机会")
                    await asyncio.sleep(random.uniform(3, 5))  # 短暂延迟后继续扫描
                    continue
                elif res:
                    logger.success(f"🎉 原子套利Bundle已被Jito接受! Bundle ID: {res}")
                    logger.info("ℹ️ send_bundle 成功仅代表被接收，需等待真正上链确认")
                    # 轮询确认 bundle 是否真的上链（send_bundle 成功仅表示被接受，不代表已上链）
                    is_landed = False
                    for _ in range(12):  # 约 12 秒
                        await asyncio.sleep(1)
                        status = await jito_client.get_bundle_status(res)
                        if status:
                            conf = status.get("confirmation_status") or status.get("confirmationStatus")
                            inflight_status = status.get("status")
                            if conf in ("confirmed", "finalized"):
                                logger.success(f"✅ Bundle 已上链! 状态: {conf}")
                                is_landed = True
                                break
                            if inflight_status == "Landed":
                                landed_slot = status.get("landed_slot") or status.get("landedSlot")
                                logger.success(f"✅ Bundle 已落地区块! landed_slot={landed_slot}")
                                is_landed = True
                                break
                            if inflight_status in ("Failed", "Invalid"):
                                logger.error(f"❌ Bundle 未上链: {inflight_status}, 详情: {status}")
                                break
                            if conf == "processed":
                                logger.info(f"📦 Bundle 已处理, 等待确认...")
                            elif inflight_status:
                                logger.info(f"📦 Bundle Inflight 状态: {inflight_status}")
                        else:
                            logger.debug(f"⏳ 等待 Bundle 上链...")

                    if not is_landed:
                        logger.warning(f"⚠️ Bundle 在轮询窗口内未确认上链，可能已过期/被丢弃。Bundle ID: {res}")
                    await asyncio.sleep(random.uniform(5, 10))  # 增加间隔以减少限流
                else:
                    logger.error("❌ Bundle提交失败")
                    await asyncio.sleep(random.uniform(5, 10))  # 增加间隔以减少限流
            else:
                # 利润不足，继续扫描（随机延迟避免规律请求）
                logger.info(f"📉 利润不足，继续扫描... (净利润: ${net_profit:.4f} < ${settings.MIN_NET_PROFIT_USDC})")
                await asyncio.sleep(random.uniform(5, 8))  # 增加间隔以减少限流

        except Exception as e:
            logger.error(f"主循环异常: {e}")
            await asyncio.sleep(random.uniform(10, 15))  # 增加间隔以减少限流


if __name__ == "__main__":
    asyncio.run(main())
