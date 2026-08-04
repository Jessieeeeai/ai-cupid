"""扫链 webhook 解析。

生产接法:
- Solana: Helius webhook(Enhanced tx),过滤到账地址 = 你的收款地址
- Base:   Alchemy address activity webhook
两家的原始 payload 格式不同,这里各写一个解析器,统一产出 (amount, txhash)。
本地/测试模式:直接 POST {"amount": 1.000001, "txhash": "..."}。
"""
from typing import Iterator


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
    """Helius enhanced webhook:list[tx],tokenTransfers 里含 USDC/USDT 转账。"""
    if not isinstance(payload, list):
        return
    for tx in payload:
        sig = tx.get("signature", "")
        for tt in tx.get("tokenTransfers", []):
            amt = tt.get("tokenAmount")
            if amt and sig:
                yield float(amt), sig


def _parse_alchemy(payload) -> Iterator[tuple[float, str]]:
    """Alchemy address activity webhook。"""
    if not isinstance(payload, dict):
        return
    for act in payload.get("event", {}).get("activity", []):
        amt = act.get("value")
        h = act.get("hash", "")
        if amt and h:
            yield float(amt), h
