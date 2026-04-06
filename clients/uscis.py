import time
import aiohttp
import logging
from typing import Optional, Dict, Any
import config

logger = logging.getLogger(__name__)

USCIS_CLIENT_ID = getattr(config, "USCIS_CLIENT_ID", "")
USCIS_CLIENT_SECRET = getattr(config, "USCIS_CLIENT_SECRET", "")

# Sandbox values from USCIS docs.
TOKEN_URL = "https://api-int.uscis.gov/oauth/accesstoken"
CASE_STATUS_BASE = "https://api-int.uscis.gov/case-status"


class UscisClient:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_expiry_epoch: float = 0

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expiry_epoch - 60:
            return self._token

        if not USCIS_CLIENT_ID or not USCIS_CLIENT_SECRET:
            raise RuntimeError("Missing USCIS_CLIENT_ID / USCIS_CLIENT_SECRET")

        payload = {
            "grant_type": "client_credentials",
            "client_id": USCIS_CLIENT_ID,
            "client_secret": USCIS_CLIENT_SECRET,
        }

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        async with aiohttp.ClientSession() as session:
            async with session.post(TOKEN_URL, data=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Token request failed: {resp.status} {text}")

                data = await resp.json()
                self._token = data["access_token"]
                expires_in = int(data.get("expires_in", 1800))
                self._token_expiry_epoch = now + expires_in
                return self._token

    async def get_case_status(self, receipt_number: str) -> Dict[str, Any]:
        token = await self._get_access_token()
        url = f"{CASE_STATUS_BASE}/{receipt_number.strip().upper()}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Case status request failed: {resp.status} {text}")
                return await resp.json()
