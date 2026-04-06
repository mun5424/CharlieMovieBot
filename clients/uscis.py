import aiohttp
import logging
from typing import Optional, Dict, Any
import config

logger = logging.getLogger(__name__)

API_BASE = "https://api.mycaseshub.com"


class UscisClient:
    def __init__(self) -> None:
        self._token: Optional[str] = getattr(config, "MYCASESHUB_AUTH_TOKEN", None)
        self._refresh_token: Optional[str] = getattr(config, "MYCASESHUB_REFRESH_TOKEN", None)

    async def _refresh_auth_token(self) -> str:
        if not self._refresh_token:
            raise RuntimeError("Missing MYCASESHUB_REFRESH_TOKEN — cannot refresh auth")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/auth/refresh",
                json={"refreshToken": self._refresh_token},
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Token refresh failed: {resp.status} {text}")

                data = await resp.json()
                # Handle nested or flat response
                inner = data.get("data", data)
                self._token = inner.get("token", inner.get("access_token"))
                new_refresh = inner.get("refreshToken", inner.get("refresh_token"))
                if new_refresh:
                    self._refresh_token = new_refresh

                if not self._token:
                    raise RuntimeError(f"No token in refresh response: {data}")

                logger.info("[USCIS] Auth token refreshed successfully")
                return self._token

    async def get_case_status(self, receipt_number: str) -> Dict[str, Any]:
        receipt = receipt_number.strip().upper()

        for attempt in range(2):
            if not self._token:
                await self._refresh_auth_token()

            headers = {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Origin": "https://www.mycaseshub.com",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{API_BASE}/case/{receipt}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        logger.info("[USCIS] Token expired, refreshing...")
                        self._token = None
                        continue

                    text = await resp.text()
                    if resp.status != 200:
                        raise RuntimeError(f"Case status request failed: {resp.status} {text}")
                    return await resp.json()
