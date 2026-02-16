# src/jupiter.py
import itertools
import aiohttp
from loguru import logger

from config.settings import settings


class JupiterClient:
    _key_iter = None  # 轮询用的迭代器

    def __init__(self):
        self.api_url = settings.JUPITER_QUOTE_API
        if JupiterClient._key_iter is None and settings.JUPITER_API_KEYS:
            JupiterClient._key_iter = itertools.cycle(settings.JUPITER_API_KEYS)

    def _get_headers(self):
        headers = {"Accept": "application/json"}
        if JupiterClient._key_iter is not None:
            key = next(JupiterClient._key_iter)
            if key:
                headers["x-api-key"] = key
        return headers

    async def get_quote(self, input_mint, output_mint, amount):
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": int(amount),
            "slippageBps": 50,
        }

        async with aiohttp.ClientSession() as session:
            try:
                # ✅ 修改点：把 headers 加进请求里
                async with session.get(
                        self.api_url,
                        params=params,
                        headers=self._get_headers()  # <--- 重点在这里
                ) as response:

                    if response.status != 200:
                        error_msg = await response.text()
                        logger.error(f"❌ API 报错! 状态码: {response.status}")
                        logger.error(f"❌ 错误详情: {error_msg}")
                        # 401 的话通常不需要打印 URL 了，因为知道是被拦了
                        return None

                    return await response.json()
            except Exception as e:
                logger.error(f"❌ 网络请求异常: {e}")
                return None

    async def get_swap_tx(self, quote_response):
        """
        拿着 Quote 结果，去换取 Transaction 数据
        """
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": str(settings.PUB_KEY),
            "wrapAndUnwrapSol": True,
            # 关键点：Jito 模式下这里设为 0 或 auto，因为我们会单独付小费
            # 如果不走 Jito，这里要设很高才能抢到
            "computeUnitPriceMicroLamports": 0
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                        settings.JUPITER_SWAP_API,
                        json=payload,
                        headers=self._get_headers()
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"❌ Swap API 报错: {await resp.text()}")
                        return None
                    return await resp.json()
            except Exception as e:
                logger.error(f"❌ Swap 请求异常: {e}")
        return None

    async def check_arb_opportunity(self, invest_amount_usdc_units):
        """
        按 settings.ARB_PATH 做闭环套利机会检查（首尾须为 USDC）。
        :param invest_amount_usdc_units: 投入 USDC 数量（最小精度）
        :return: 成功时返回 dict(quotes, final_usdc_units, gross_profit_usdc, net_profit_usdc)，失败返回 None
        """
        path = list(settings.ARB_PATH)
        if len(path) < 2 or path[0] != "USDC" or path[-1] != "USDC":
            logger.error("ARB_PATH 首尾必须为 USDC")
            return None

        try:
            mints = [settings.get_mint(s) for s in path]
        except ValueError as e:
            logger.error(str(e))
            return None

        path_str = " -> ".join(path)
        human_amount = invest_amount_usdc_units / settings.UNITS_PER_USDC
        logger.info(f"🔎 开始巡逻: 投入 {human_amount} USDC, 路径: {path_str}")

        quotes = []
        amount_in = invest_amount_usdc_units
        for i in range(len(path) - 1):
            input_mint = mints[i]
            output_mint = mints[i + 1]
            q = await self.get_quote(input_mint, output_mint, amount_in)
            if not q:
                logger.warning(f"第 {i + 1} 腿询价失败 ({path[i]} -> {path[i + 1]})")
                return None
            quotes.append(q)
            amount_in = int(q["outAmount"])
            logger.info(f"  --> 第 {i + 1} 步: 换得 {path[i + 1]} (raw amount: {amount_in})")

        final_usdc_units = amount_in
        profit_units = final_usdc_units - invest_amount_usdc_units
        gross_profit_usdc = profit_units / settings.UNITS_PER_USDC
        total_cost_usdc = (
            settings.JITO_TIP_AMOUNT_SOL + settings.ESTIMATED_GAS_SOL
        ) * settings.FIXED_SOL_PRICE_USDC
        net_profit_usdc = gross_profit_usdc - total_cost_usdc

        logger.info(f"  --> 最终: {final_usdc_units / settings.UNITS_PER_USDC:.4f} USDC")
        logger.info(f"📊 毛利润: ${gross_profit_usdc:.4f} USDC, 净利润: ${net_profit_usdc:.4f} USDC")

        return {
            "quotes": quotes,
            "final_usdc_units": final_usdc_units,
            "gross_profit_usdc": gross_profit_usdc,
            "net_profit_usdc": net_profit_usdc,
        }
