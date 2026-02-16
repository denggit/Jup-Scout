#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Jup-Scout 连接测试脚本
在运行主程序前，使用此脚本测试环境配置和基础连接
"""

import asyncio
import os
import sys

import aiohttp
from loguru import logger

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from src.jito_client import JitoClient
from src.jupiter import JupiterClient


async def test_environment():
    """测试环境变量和基本配置"""
    print("🔍 测试环境配置...")

    # 检查私钥
    if not settings.KEYPAIR:
        print("❌ 错误: 未配置私钥 (PRIVATE_KEY)")
        return False
    print(f"✅ 私钥配置正常，公钥: {settings.PUB_KEY}")

    # 检查RPC URL
    if not settings.RPC_URL:
        print("❌ 错误: 未配置RPC_URL")
        return False
    print(f"✅ RPC URL: {settings.RPC_URL}")

    # 检查Jupiter API Keys
    if not settings.JUPITER_API_KEYS:
        print("⚠️  警告: 未配置JUPITER_API_KEYS，可能触发限流")
    else:
        print(f"✅ Jupiter API Keys: {len(settings.JUPITER_API_KEYS)} 个")

    # 检查Jito端点
    if not settings.JITO_ENGINE_URLS:
        print("❌ 错误: 未配置JITO_ENGINE_URLS")
        return False
    print(f"✅ Jito 端点: {len(settings.JITO_ENGINE_URLS)} 个")
    for i, url in enumerate(settings.JITO_ENGINE_URLS):
        print(f"   {i + 1}. {url}")

    # 检查代币地址
    print(f"✅ SOL Mint: {settings.SOL_MINT}")
    print(f"✅ USDC Mint: {settings.USDC_MINT}")

    return True


async def test_rpc_connection():
    """测试Solana RPC连接"""
    print("\n🌐 测试Solana RPC连接...")
    try:
        from solana.rpc.async_api import AsyncClient
        async with AsyncClient(settings.RPC_URL) as client:
            version = await client.get_version()
            if version.value:
                # 尝试多种方式获取版本信息
                version_info = version.value
                version_str = "未知"
                if hasattr(version_info, 'solana_core'):
                    version_str = version_info.solana_core
                elif hasattr(version_info, 'solana-core'):
                    version_str = getattr(version_info, 'solana-core')
                elif hasattr(version_info, '__dict__'):
                    # 尝试从__dict__中获取
                    version_str = str(version_info.__dict__)
                print(f"✅ RPC连接成功，版本信息: {version_str}")
                return True
            else:
                print("❌ RPC连接失败: 版本信息为空")
                return False
    except Exception as e:
        print(f"❌ RPC连接失败: {e}")
        return False


async def test_jito_endpoints():
    """测试Jito端点可达性（不发送交易）"""
    print("\n⚡️ 测试Jito端点可达性...")
    jito_client = JitoClient()
    successful = 0

    # 测试每个端点
    for url in settings.JITO_ENGINE_URLS:
        try:
            async with aiohttp.ClientSession() as session:
                # 发送一个简单的健康检查请求（Jito可能不提供健康端点，尝试RPC调用）
                # 使用getBundleStatuses方法查询一个不存在的bundle
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBundleStatuses",
                    "params": [["test_nonexistent_bundle"]]
                }
                async with session.post(url, json=payload, timeout=5) as resp:
                    status = resp.status
                    if status in (200, 400, 404):
                        # 200表示成功（即使bundle不存在），400/404表示端点响应但请求错误
                        print(f"✅ 端点 {url} 可达 (HTTP {status})")
                        successful += 1
                    else:
                        print(f"⚠️  端点 {url} 返回异常状态码: {status}")
        except aiohttp.ClientConnectorError:
            print(f"❌ 端点 {url} 连接失败")
        except asyncio.TimeoutError:
            print(f"❌ 端点 {url} 超时")
        except Exception as e:
            print(f"❌ 端点 {url} 测试异常: {e}")

    if successful == 0:
        print("❌ 所有Jito端点均不可达")
        return False
    elif successful < len(settings.JITO_ENGINE_URLS):
        print(f"⚠️  部分Jito端点可达 ({successful}/{len(settings.JITO_ENGINE_URLS)})")
    else:
        print(f"✅ 所有Jito端点均可达 ({successful}/{len(settings.JITO_ENGINE_URLS)})")
    return True


async def test_jupiter_api():
    """测试Jupiter API连接（仅询价，不交易）"""
    print("\n🪐 测试Jupiter API连接...")

    if not settings.JUPITER_API_KEYS:
        print("⚠️  跳过Jupiter API测试（未配置API Key）")
        return True

    jupiter_client = JupiterClient()
    try:
        # 尝试获取一个小的USDC->SOL报价（1 USDC）
        quote = await jupiter_client.get_quote(
            settings.USDC_MINT,
            settings.SOL_MINT,
            1_000_000  # 1 USDC (6 decimals)
        )
        if quote:
            print(f"✅ Jupiter API连接成功")
            print(f"   1 USDC ≈ {int(quote['outAmount']) / settings.LAMPORT_PER_SOL:.6f} SOL")
            return True
        else:
            print("❌ Jupiter API返回空报价")
            return False
    except Exception as e:
        print(f"❌ Jupiter API测试异常: {e}")
        return False


async def test_jito_client_initialization():
    """测试Jito客户端初始化"""
    print("\n🔧 测试Jito客户端初始化...")
    try:
        jito_client = JitoClient()
        print(f"✅ Jito客户端初始化成功")
        print(f"   小费金额: {jito_client.tip_amount} SOL")
        print(f"   可用端点: {len(settings.JITO_ENGINE_URLS)} 个")
        return True
    except Exception as e:
        print(f"❌ Jito客户端初始化失败: {e}")
        return False


async def test_vote_account_detection():
    """测试 vote program 检测功能（_is_vote_program / tx_touches_vote_account）"""
    print("\n🔒 测试vote account检测功能...")
    from solders.pubkey import Pubkey
    from src.jito_client import _is_vote_program, VOTE_PROGRAM_ID_STR

    # 当前只检测 Vote 程序 ID 本身（Vote111...111），不检测 112 变体
    test_cases = [
        (VOTE_PROGRAM_ID_STR, True),  # Vote program
        ("Vote111111111111111111111111111111111111112", False),
        ("So11111111111111111111111111111111111111112", False),  # SOL mint
        ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", False),  # USDC mint
        ("96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", False),  # Jito tip account
    ]

    all_passed = True
    for addr_str, expected in test_cases:
        try:
            pubkey = Pubkey.from_string(addr_str)
            result = _is_vote_program(pubkey)
            status = "✅" if result == expected else "❌"
            print(f"   {status} {addr_str[:20]}...: 预期={expected}, 实际={result}")
            if result != expected:
                all_passed = False
        except Exception as e:
            print(f"❌ 测试失败 {addr_str}: {e}")
            all_passed = False

    if all_passed:
        print("✅ Vote account检测功能正常")
    else:
        print("❌ Vote account检测功能异常")

    return all_passed


async def main():
    """运行所有测试"""
    print("🚀 Jup-Scout 连接测试开始")
    print("=" * 50)

    tests = [
        ("环境配置", test_environment),
        ("RPC连接", test_rpc_connection),
        ("Jito端点", test_jito_endpoints),
        ("Jupiter API", test_jupiter_api),
        ("Jito客户端", test_jito_client_initialization),
        ("Vote account检测", test_vote_account_detection),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            success = await test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"❌ 测试 '{test_name}' 异常: {e}")
            results.append((test_name, False))
        await asyncio.sleep(0.5)  # 短暂延迟

    print("\n" + "=" * 50)
    print("📊 测试结果汇总:")
    print("-" * 50)

    all_passed = True
    for test_name, success in results:
        status = "✅ 通过" if success else "❌ 失败"
        print(f"   {test_name:20} {status}")
        if not success:
            all_passed = False

    print("-" * 50)
    if all_passed:
        print("🎉 所有测试通过！可以运行主程序。")
        return 0
    else:
        print("⚠️  部分测试失败，请检查配置后再运行主程序。")
        return 1


if __name__ == "__main__":
    # 配置日志
    logger.remove()  # 移除默认日志处理器
    logger.add(sys.stderr, level="INFO")

    # 运行测试
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
