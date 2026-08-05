"""扫链 webhook 解析。

生产接法:
- Solana: Helius webhook(Enhanced tx),监听你的收款地址
- Base:   Alchemy address activity webhook
两家的原始 payload 格式不同,这里各写一个解析器,统一产出 (amount, txhash)。
安全:只认"转入我方收款地址"的稳定币入账,转出/其他代币一律忽略。
本地/测试模式:直接 POST {"amount": 1.000001, "txhash": "..."}。
"""
from typing import Iterator

from .config import get_settings

STABLE_TOKENS = {"USDT", "USDC"}
# Solana 上 USDC/USDT 的 mint 地址(Helius 有时只给 mint 不给 symbol)
SOLANA_STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def parse_events(chain: str, payload: dict | list) -> Iterator[tuple[float, str]]:
    # 通用/测试格式
    if isinstance(payload, dict) and "amount" in payload and "txhash" in payload:
        yield float(payload["amount"]), str(payload["txhash"])
        return
    if chain == "solana":
        yield from _parse_helius(payload)
    elif chain == "base":
        yield from _parse_alchemy(payload)


def _parse_helius(payload) -> Iterator[tuple[float, str]]:
    """Helius enhanced webhook:list[tx],tokenTransfers 含代币转账明细。"""
    if not isinstance(payload, list):
        return
    my_addr = get_settings().solana_address.strip()
    for tx in payload:
        if not isinstance(tx, dict):
            continue
        sig = tx.get("signature", "")
        for tt in tx.get("tokenTransfers", []):
            if tt.get("toUserAccount") != my_addr:
                continue  # 只认转入
            mint_ok = tt.get("mint") in SOLANA_STABLE_MINTS
            symbol_ok = str(tt.get("tokenSymbol", "")).upper() in STABLE_TOKENS
            if not (mint_ok or symbol_ok):
                continue
            amt = tt.get("tokenAmount")
            if amt and sig:
                yield float(amt), sig


def _parse_alchemy(payload) -> Iterator[tuple[float, str]]:
    """Alchemy address activity webhook。"""
    if not isinstance(payload, dict):
        return
    my_addr = get_settings().base_address.strip().lower()
    for act in payload.get("event", {}).get("activity", []):
        if str(act.get("toAddress", "")).lower() != my_addr:
            continue  # 只认转入
        if str(act.get("asset", "")).upper() not in STABLE_TOKENS:
            continue
        amt = act.get("value")
        h = act.get("hash", "")
        if amt and h:
            yield float(amt), h
