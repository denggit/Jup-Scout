# src/jupiter.py
from loguru import logger
from config.settings import settings
import aiohttp


class JupiterClient:
    def __init__(self):
        self.api_url = settings.JUPITER_QUOTE_API

    async def get_quote(self, input_mint, output_mint, amount):
        # ... 这个函数不用变，保持原样 ...
        # 记得: amount 必须是整数 (Integers)
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": int(amount),
            "slippageBps": 50,  # 0.5%
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(self.api_url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                return None

    async def check_arb_opportunity(self, invest_amount_usdc_units):
        """
        新逻辑：USDC -> 中间代币(这里用SOL为例) -> USDC
        :param invest_amount_usdc_units: 投入多少 USDC (单位: 最小精度)
        """
        # 设定中间代币，这里我们用 SOL 做中转，以后可以换成 BONK, WIF 等
        intermediate_mint = settings.SOL_MINT

        # 显示人类可读数值
        human_amount = invest_amount_usdc_units / settings.UNITS_PER_USDC
        logger.info(f"🔎 开始巡逻: 投入 {human_amount} USDC, 路径: USDC -> SOL -> USDC")

        # 1. 第一腿: USDC -> SOL (买入 SOL)
        quote_buy = await self.get_quote(
            settings.USDC_MINT,  # 输入: USDC
            intermediate_mint,  # 输出: SOL
            invest_amount_usdc_units
        )

        if not quote_buy:
            logger.warning("第一腿询价失败")
            return

        # 拿到能买多少 SOL (outAmount)
        intermediate_amount = int(quote_buy['outAmount'])
        logger.info(f"  --> 第一步: 换得 {intermediate_amount / settings.LAMPORT_PER_SOL:.4f} SOL")

        # 2. 第二腿: SOL -> USDC (卖出 SOL)
        quote_sell = await self.get_quote(
            intermediate_mint,  # 输入: SOL
            settings.USDC_MINT,  # 输出: USDC
            intermediate_amount  # 输入数量: 刚才买到的 SOL
        )

        if not quote_sell:
            logger.warning("第二腿询价失败")
            return

        # 拿到最终变回多少 USDC
        final_usdc_units = int(quote_sell['outAmount'])

        # 3. 算账 (USDC 本位)
        profit_units = final_usdc_units - invest_amount_usdc_units
        profit_usdc = profit_units / settings.UNITS_PER_USDC

        logger.info(f"  --> 第二步: 变回 {final_usdc_units / settings.UNITS_PER_USDC:.4f} USDC")

        if profit_units > 0:
            logger.success(f"💰 发现利润! 净赚: ${profit_usdc:.4f} USDC")
        else:
            logger.info(f"📉 亏损: ${profit_usdc:.4f} USDC (滑点+价差不足)")