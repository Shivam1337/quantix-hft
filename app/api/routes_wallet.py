"""
API Routes for Wallet Management.
Exposes endpoints for viewing wallet details, balances, revealing keys,
refreshing live on-chain balances, and generating/importing wallets.
"""
from typing import Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.wallet_manager import wallet_manager

router = APIRouter(prefix="/api/wallet", tags=["Wallet"])


class ImportWalletRequest(BaseModel):
    private_key: str


@router.get("", response_model=Dict[str, Any])
async def get_wallet():
    """Returns the current server wallet summary with masked private keys."""
    return wallet_manager.get_summary(mask_keys=True)


@router.get("/reveal", response_model=Dict[str, str])
async def reveal_wallet():
    """Returns unmasked credentials for 1-click clipboard export."""
    return wallet_manager.get_unmasked_credentials()


@router.post("/refresh", response_model=Dict[str, Any])
async def refresh_wallet_balance():
    """Forces an asynchronous balance refresh from Arbitrum RPC and Lighter.xyz."""
    await wallet_manager.refresh_balances()
    return wallet_manager.get_summary(mask_keys=True)


@router.post("/generate", response_model=Dict[str, Any])
async def generate_wallet():
    """Generates a brand new server wallet and Lighter zk-key pair."""
    wallet_manager.generate_new_wallet()
    return wallet_manager.get_summary(mask_keys=True)


@router.post("/import", response_model=Dict[str, Any])
async def import_wallet(req: ImportWalletRequest):
    """Imports an existing Ethereum private key."""
    if not req.private_key or len(req.private_key.strip()) < 32:
        raise HTTPException(status_code=400, detail="Invalid Ethereum private key length.")
    try:
        wallet_manager.import_private_key(req.private_key.strip())
        await wallet_manager.refresh_balances()
        return wallet_manager.get_summary(mask_keys=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to import private key: {e}")
