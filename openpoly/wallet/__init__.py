"""Wallet — system-level single wallet for mainnet live trading (M4).

Polymarket V2 model (post-2026-04-28): no on-the-fly BIP-44 derivation —
the operator supplies the EOA private key directly (as a *_ref) plus the
on-chain DepositWallet address. State lives in runtime_state.py.
"""
