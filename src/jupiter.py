# src/jupiter.py
import aiohttp
from loguru import logger

from config.settings import settings


class JupiterClient:
    def __init__(self):
        self.api_url = settings.JUPITER_QUOTE_API

    @staticmethod
    def _get_headers():
        headers = {
            "Accept": "application/json"
        }
        # 从 settings 读取 Key
        if settings.JUPITER_API_KEY:
            headers["x-api-key"] = settings.JUPITER_API_KEY
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

    async def get_swap_tx(self, quote_response, recent_blockhash=None):
        """
        拿着 Quote 结果，去换取 Transaction 数据
        
        :param quote_response: Jupiter quote响应
        :param recent_blockhash: 可选的blockhash，用于确保多个swap使用相同的blockhash（原子性）
        :return: swap交易响应
        """
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": str(settings.PUB_KEY),
            "wrapAndUnwrapSol": True,
            # 关键点：Jito 模式下这里设为 0 或 auto，因为我们会单独付小费
            # 如果不走 Jito，这里要设很高才能抢到
            "computeUnitPriceMicroLamports": 0
        }
        
        # 如果提供了blockhash，尝试传递给Jupiter API（如果API支持）
        # 注意：Jupiter API可能不支持此参数，但我们可以尝试
        if recent_blockhash:
            # Jupiter API可能不支持直接传递blockhash，但我们可以尝试
            # 如果API不支持，Jupiter会使用自己的blockhash
            pass  # 暂时保留，后续可以根据API文档调整

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
        检查USDC->SOL->USDC的套利机会
        
        :param invest_amount_usdc_units: 投入的USDC数量（单位：最小精度）
        :return: 如果发现套利机会，返回包含以下字段的字典：
            - quote_buy: USDC->SOL的quote响应
            - quote_sell: SOL->USDC的quote响应
            - intermediate_amount: 中间得到的SOL数量（lamports）
            - final_usdc_units: 最终得到的USDC数量（最小精度）
            - gross_profit_usdc: 毛利润（USDC）
            - net_profit_usdc: 净利润（USDC，扣除成本）
            如果未发现套利机会或出错，返回None
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
            logger.warning("⚠️ 第一腿询价失败 (USDC -> SOL)")
            return None

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
            logger.warning("⚠️ 第二腿询价失败 (SOL -> USDC)")
            return None

        # 拿到最终变回多少 USDC
        final_usdc_units = int(quote_sell['outAmount'])

        # 3. 算账 (USDC 本位)
        profit_units = final_usdc_units - invest_amount_usdc_units
        gross_profit_usdc = profit_units / settings.UNITS_PER_USDC

        # 计算成本 (Gas + Jito Tip)
        total_cost_usdc = (
            settings.JITO_TIP_AMOUNT_SOL + settings.ESTIMATED_GAS_SOL
        ) * settings.FIXED_SOL_PRICE_USDC
        
        # 净利润 = 毛利 - 成本
        net_profit_usdc = gross_profit_usdc - total_cost_usdc

        logger.info(f"  --> 第二步: 变回 {final_usdc_units / settings.UNITS_PER_USDC:.4f} USDC")
        logger.info(f"📊 毛利润: ${gross_profit_usdc:.4f} USDC, 净利润: ${net_profit_usdc:.4f} USDC")

        # 返回套利结果
        return {
            'quote_buy': quote_buy,
            'quote_sell': quote_sell,
            'intermediate_amount': intermediate_amount,
            'final_usdc_units': final_usdc_units,
            'gross_profit_usdc': gross_profit_usdc,
            'net_profit_usdc': net_profit_usdc
        }
