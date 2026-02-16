# src/ata_utils.py
"""
Stage 0：账户准备。确保路径所需 ATA 常驻，不 close、不 reclaim rent。
"""
from loguru import logger
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient

ATA_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")


def get_ata_address(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """ATA 地址 = PDA(owner, TOKEN_PROGRAM_ID, mint)。"""
    seeds = [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)]
    pda, _ = Pubkey.find_program_address(seeds, ATA_PROGRAM_ID)
    return pda


def create_ata_instruction(payer: Pubkey, owner: Pubkey, mint: Pubkey) -> Instruction:
    """创建一条 createAssociatedTokenAccount 指令（不包含 close）。"""
    ata = get_ata_address(owner, mint)
    return Instruction(
        program_id=ATA_PROGRAM_ID,
        data=bytes(),
        accounts=[
            AccountMeta(payer, is_signer=True, is_writable=True),
            AccountMeta(ata, is_signer=False, is_writable=True),
            AccountMeta(owner, is_signer=False, is_writable=False),
            AccountMeta(mint, is_signer=False, is_writable=False),
        ],
    )


async def ata_exists(rpc: AsyncClient, ata_pubkey: Pubkey) -> bool:
    """链上是否已存在该 ATA。"""
    try:
        resp = await rpc.get_account_info(ata_pubkey)
        return resp.value is not None
    except Exception:
        return False


async def ensure_ata_exists(rpc: AsyncClient, payer_keypair, mint_pubkey: Pubkey) -> bool:
    """
    若该 mint 的 ATA 不存在则创建（只做一次）。不 close、不 reclaim。
    返回 True 表示已存在或创建成功，False 表示创建失败。
    """
    owner = payer_keypair.pubkey()
    ata = get_ata_address(owner, mint_pubkey)
    if await ata_exists(rpc, ata):
        return True
    try:
        ix = create_ata_instruction(owner, owner, mint_pubkey)
        # 单条指令，用 blockhash 发一笔普通 tx（不走 Jito）
        bh = (await rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(owner, [ix], [], bh)
        tx = VersionedTransaction(msg, [payer_keypair])
        sig = await rpc.send_transaction(tx)
        if getattr(sig, "value", None):
            logger.info(f"✅ 已创建 ATA {ata} (mint={mint_pubkey})")
            return True
        logger.warning(f"⚠️ 创建 ATA 返回异常: {sig}")
        return False
    except Exception as e:
        logger.warning(f"⚠️ 创建 ATA 失败 (mint={mint_pubkey}): {e}")
        return False


async def ensure_atas_for_path(rpc: AsyncClient, payer_keypair, path_mint_pubkeys: list) -> None:
    """
    Stage 0：确保路径上所有代币的 ATA 存在（USDC、wSOL、中间 token）。
    只创建缺失的，不 close、不 reclaim。
    """
    for mint in path_mint_pubkeys:
        if await ata_exists(rpc, get_ata_address(payer_keypair.pubkey(), mint)):
            continue
        await ensure_ata_exists(rpc, payer_keypair, mint)
