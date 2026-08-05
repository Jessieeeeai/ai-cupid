from app import chains


def test_helius_only_incoming_stable(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "solana_address", "MyAddr")
    payload = [{
        "signature": "sig1",
        "tokenTransfers": [
            {"toUserAccount": "MyAddr", "tokenSymbol": "USDC", "tokenAmount": 1.000002},
            {"toUserAccount": "SomeoneElse", "tokenSymbol": "USDC", "tokenAmount": 5.0},   # 转出/无关
            {"toUserAccount": "MyAddr", "tokenSymbol": "BONK", "tokenAmount": 99999.0},    # 非稳定币
        ],
    }]
    events = list(chains.parse_events("solana", payload))
    assert events == [(1.000002, "sig1")]


def test_alchemy_only_incoming_stable(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "base_address", "0xABCDEF")
    payload = {"event": {"activity": [
        {"toAddress": "0xabcdef", "asset": "USDC", "value": 1.000003, "hash": "h1"},
        {"toAddress": "0xother", "asset": "USDC", "value": 1.000003, "hash": "h2"},
        {"toAddress": "0xabcdef", "asset": "ETH", "value": 0.5, "hash": "h3"},
    ]}}
    events = list(chains.parse_events("base", payload))
    assert events == [(1.000003, "h1")]


def test_generic_test_format():
    assert list(chains.parse_events("solana", {"amount": 1.5, "txhash": "t"})) == [(1.5, "t")]
