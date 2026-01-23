import time
import aiohttp
from typing import Dict, List, Optional

import config

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_STREAMS_URL = "https://api.twitch.tv/helix/streams"

class TwitchClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.client_id = getattr(config, "TWITCH_API_CLIENT", "").strip()
        self.client_secret = getattr(config, "TWITCH_API_CLIENT_SECRET", "").strip()
        if not self.client_id or not self.client_secret:
            raise RuntimeError("Missing TWITCH_API_CLIENT or TWITCH_API_CLIENT_SECRET in config")

        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0  # epoch seconds

    async def _refresh_token(self) -> None:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        async with self.session.post(TWITCH_TOKEN_URL, data=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            payload = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Twitch token refresh failed: {resp.status} {payload}")
            self._access_token = payload["access_token"]
            expires_in = int(payload.get("expires_in", 3600))
            # refresh a bit early
            self._expires_at = time.time() + max(60, expires_in - 120)

    async def _get_token(self) -> str:
        if not self._access_token or time.time() >= self._expires_at:
            await self._refresh_token()
        assert self._access_token
        return self._access_token

    async def get_live_streams(self, user_logins: List[str]) -> Dict[str, dict]:
        """
        Returns dict keyed by user_login (lowercase) for streams that are LIVE.
        """
        if not user_logins:
            return {}

        token = await self._get_token()
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {token}",
        }

        # Twitch supports multiple user_login query params.
        params = []
        for login in user_logins:
            login = login.strip().lower()
            if login:
                params.append(("user_login", login))

        async with self.session.get(
            TWITCH_STREAMS_URL,
            headers=headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10, connect=3),
        ) as resp:
            payload = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Twitch get streams failed: {resp.status} {payload}")

        live = {}
        for item in payload.get("data", []):
            login = (item.get("user_login") or "").lower()
            if login:
                live[login] = item
        return live
