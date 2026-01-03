import httpx
import hashlib
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)


class YeastarClient:
    """Client for Yeastar PBX API communication (supports both Cloud and On-Premise)."""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.yeastar_base_url
        self.token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._last_login_attempt: Optional[datetime] = None
        self._login_backoff_seconds: int = 0

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0, verify=True)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _md5_password(self, password: str) -> str:
        """Convert password to MD5 hash as required by Yeastar API."""
        return hashlib.md5(password.encode()).hexdigest()

    async def login(self, webhook_port: Optional[int] = None) -> bool:
        """
        Login to Yeastar PBX and obtain API token.
        Uses OAuth2 for Cloud PBX, username/password for On-Premise.
        """
        if self.settings.is_cloud_pbx:
            return await self._login_cloud()
        else:
            return await self._login_onpremise(webhook_port)

    async def _login_cloud(self) -> bool:
        """Login to Yeastar Cloud PBX using API credentials."""
        # Rate limit protection - don't hammer the API if login keeps failing
        if self._last_login_attempt and self._login_backoff_seconds > 0:
            elapsed = (datetime.now() - self._last_login_attempt).total_seconds()
            if elapsed < self._login_backoff_seconds:
                wait_time = int(self._login_backoff_seconds - elapsed)
                logger.warning(f"Login rate limited, waiting {wait_time}s before retry")
                return False

        self._last_login_attempt = datetime.now()
        client = await self.get_client()

        # Token endpoint for Yeastar Cloud PBX
        # Per docs: username = client_id, password = client_secret
        token_url = f"{self.base_url}/openapi/v1.0/get_token"

        payload = {
            "username": self.settings.yeastar_client_id,
            "password": self.settings.yeastar_client_secret,
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "OpenAPI",
        }

        try:
            logger.info(f"Attempting Cloud PBX login to {token_url}")
            response = await client.post(token_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

            if data.get("errcode") == 0:
                self.token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                expires_in = data.get("access_token_expire_time", 1800)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
                self._login_backoff_seconds = 0  # Reset backoff on success
                logger.info("Successfully logged in to Yeastar Cloud PBX")
                return True
            else:
                error_msg = data.get('errmsg', 'Unknown error')
                logger.error(f"Cloud login failed: {error_msg}")
                # If rate limited, set backoff to avoid hammering
                if "MAX LIMITATION" in error_msg.upper():
                    self._login_backoff_seconds = 300  # Wait 5 minutes
                    logger.warning("Rate limited by PBX - backing off for 5 minutes")
                return False

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during cloud login: {e}")
            return False

    async def _login_onpremise(self, webhook_port: Optional[int] = None) -> bool:
        """Login to Yeastar On-Premise PBX using username/password."""
        client = await self.get_client()

        payload = {
            "username": self.settings.yeastar_username,
            "password": self._md5_password(self.settings.yeastar_password),
            "port": webhook_port or self.settings.webhook_port,
            "version": "1.0.1",
        }

        try:
            response = await client.post(
                f"{self.base_url}/api/v1.1.0/login",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "Success":
                self.token = data.get("token")
                logger.info("Successfully logged in to Yeastar On-Premise PBX")
                return True
            else:
                logger.error(f"Login failed: {data.get('errmsg', 'Unknown error')}")
                return False

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during login: {e}")
            return False

    async def _refresh_cloud_token(self) -> bool:
        """Refresh the access token for Cloud PBX."""
        if not self.refresh_token:
            return await self._login_cloud()

        client = await self.get_client()
        token_url = f"{self.base_url}/openapi/v1.0/refresh_token"

        payload = {
            "refresh_token": self.refresh_token,
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "OpenAPI",
        }

        try:
            response = await client.post(token_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

            if data.get("errcode") == 0:
                self.token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                expires_in = data.get("access_token_expire_time", 1800)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
                logger.info("Successfully refreshed Cloud PBX token")
                return True
            else:
                logger.warning("Token refresh failed, attempting full login")
                return await self._login_cloud()

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during token refresh: {e}")
            return await self._login_cloud()

    async def ensure_authenticated(self) -> bool:
        """Ensure we have a valid token, login or refresh if necessary."""
        if not self.token:
            return await self.login()

        # For Cloud PBX, check token expiry and refresh if needed
        if self.settings.is_cloud_pbx and self.token_expiry:
            if datetime.now() >= self.token_expiry:
                return await self._refresh_cloud_token()

        return True

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        retry_auth: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Make an authenticated request to the Yeastar API."""
        if not await self.ensure_authenticated():
            logger.error("Failed to authenticate")
            return None

        client = await self.get_client()

        # Cloud PBX uses different API path and access_token as query param
        if self.settings.is_cloud_pbx:
            # Convert old API path to Cloud OpenAPI path
            cloud_endpoint = endpoint.replace("/api/v1.1.0/", "/openapi/v1.0/")
            url = f"{self.base_url}{cloud_endpoint}?access_token={self.token}"
            headers = {"User-Agent": "OpenAPI"}
        else:
            url = f"{self.base_url}{endpoint}?token={self.token}"
            headers = {}

        try:
            # Cloud PBX uses GET for most queries
            if self.settings.is_cloud_pbx:
                if data:
                    # Add data as query params for GET
                    params = "&".join(f"{k}={v}" for k, v in data.items())
                    url = f"{url}&{params}"
                response = await client.get(url, headers=headers)
            elif method.upper() == "GET":
                response = await client.get(url, headers=headers)
            else:
                response = await client.post(url, json=data or {}, headers=headers)

            response.raise_for_status()
            result = response.json()

            # Check for token expiry (different error formats for cloud vs on-premise)
            if self.settings.is_cloud_pbx:
                if result.get("errcode") in (10002, 10003):  # Token expired/invalid
                    if retry_auth:
                        self.token = None
                        return await self._request(method, endpoint, data, retry_auth=False)
                    return None
            else:
                if result.get("status") == "Failed" and "token" in result.get("errmsg", "").lower():
                    if retry_auth:
                        self.token = None
                        return await self._request(method, endpoint, data, retry_auth=False)
                    return None

            return result

        except httpx.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            return None

    async def _cloud_post(
        self,
        endpoint: str,
        data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Make a POST request to Cloud PBX API."""
        if not await self.ensure_authenticated():
            logger.error("Failed to authenticate")
            return None

        client = await self.get_client()
        url = f"{self.base_url}{endpoint}?access_token={self.token}"
        headers = {"User-Agent": "OpenAPI", "Content-Type": "application/json"}

        try:
            response = await client.post(url, json=data, headers=headers)
            response.raise_for_status()
            result = response.json()
            return result
        except httpx.HTTPError as e:
            logger.error(f"HTTP error in cloud POST: {e}")
            return None

    # ==================== Device Info ====================

    async def get_device_info(self) -> Optional[Dict[str, Any]]:
        """Query PBX device information."""
        if self.settings.is_cloud_pbx:
            # Cloud PBX doesn't have device info endpoint
            # Return success if authenticated
            if await self.ensure_authenticated():
                return {
                    "errcode": 0,
                    "device_name": "Yeastar Cloud PBX",
                    "firmware_version": "Cloud",
                    "uptime": "N/A",
                }
            return None
        return await self._request("POST", "/api/v1.1.0/deviceinfo/query")

    # ==================== Extensions ====================

    async def get_extension_list(self) -> Optional[List[Dict[str, Any]]]:
        """Get list of all extensions."""
        if self.settings.is_cloud_pbx:
            result = await self._request("GET", "/api/v1.1.0/extension/list")
            if result and result.get("errcode") == 0:
                return result.get("data", [])
            return None
        result = await self._request("POST", "/api/v1.1.0/extensionlist/query")
        if result and result.get("status") == "Success":
            return result.get("extlist", [])
        return None

    async def get_extension(self, extension: str) -> Optional[Dict[str, Any]]:
        """Get details for a specific extension."""
        if self.settings.is_cloud_pbx:
            result = await self._request(
                "GET",
                "/api/v1.1.0/extension/query",
                {"number": extension}
            )
            if result and result.get("errcode") == 0:
                data = result.get("data", [])
                return data[0] if data else None
            return None
        result = await self._request(
            "POST",
            "/api/v1.1.0/extension/query",
            {"extid": extension}
        )
        if result and result.get("status") == "Success":
            extinfos = result.get("extinfos", [])
            return extinfos[0] if extinfos else None
        return None

    # ==================== Call Operations ====================

    async def make_call(
        self,
        caller_extension: str,
        callee_number: str,
        auto_answer: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Initiate an outbound call.

        Args:
            caller_extension: The extension making the call
            callee_number: The number to dial
            auto_answer: Whether to auto-answer the call on the extension
        """
        if self.settings.is_cloud_pbx:
            return await self._cloud_post(
                "/openapi/v1.0/call/dial",
                {
                    "caller": caller_extension,
                    "callee": callee_number,
                    "auto_answer": "yes" if auto_answer else "no",
                }
            )
        result = await self._request(
            "POST",
            "/api/v1.1.0/extension/dial_outbound",
            {
                "extid": caller_extension,
                "outto": callee_number,
                "autoanswer": "yes" if auto_answer else "no",
            }
        )
        return result

    async def make_internal_call(
        self,
        caller_extension: str,
        callee_extension: str,
        auto_answer: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Initiate an internal call between extensions."""
        if self.settings.is_cloud_pbx:
            return await self._cloud_post(
                "/openapi/v1.0/call/dial",
                {
                    "caller": caller_extension,
                    "callee": callee_extension,
                    "auto_answer": "yes" if auto_answer else "no",
                }
            )
        result = await self._request(
            "POST",
            "/api/v1.1.0/extension/dial_extension",
            {
                "extid": caller_extension,
                "ext": callee_extension,
                "autoanswer": "yes" if auto_answer else "no",
            }
        )
        return result

    async def hangup_call(self, extension: str, call_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Hang up a call on an extension."""
        if self.settings.is_cloud_pbx:
            payload = {"caller": extension}
            if call_id:
                payload["call_id"] = call_id
            return await self._cloud_post("/openapi/v1.0/call/hangup", payload)
        return await self._request(
            "POST",
            "/api/v1.1.0/extension/hangup",
            {"extid": extension}
        )

    async def hold_call(self, extension: str) -> Optional[Dict[str, Any]]:
        """Put a call on hold."""
        if self.settings.is_cloud_pbx:
            return await self._cloud_post(
                "/openapi/v1.0/call/hold",
                {"caller": extension}
            )
        return await self._request(
            "POST",
            "/api/v1.1.0/extension/hold",
            {"extid": extension}
        )

    async def unhold_call(self, extension: str) -> Optional[Dict[str, Any]]:
        """Resume a held call."""
        if self.settings.is_cloud_pbx:
            return await self._cloud_post(
                "/openapi/v1.0/call/unhold",
                {"caller": extension}
            )
        return await self._request(
            "POST",
            "/api/v1.1.0/extension/unhold",
            {"extid": extension}
        )

    async def transfer_call(
        self,
        extension: str,
        transfer_to: str,
    ) -> Optional[Dict[str, Any]]:
        """Transfer a call to another number."""
        if self.settings.is_cloud_pbx:
            return await self._cloud_post(
                "/openapi/v1.0/call/transfer",
                {
                    "caller": extension,
                    "callee": transfer_to,
                }
            )
        return await self._request(
            "POST",
            "/api/v1.1.0/extension/transfer",
            {
                "extid": extension,
                "transferto": transfer_to,
            }
        )

    # ==================== Active Calls ====================

    async def get_inbound_calls(self) -> Optional[List[Dict[str, Any]]]:
        """Get list of active inbound calls."""
        result = await self._request("POST", "/api/v1.1.0/inbound/query")
        if result and result.get("status") == "Success":
            return result.get("inbound", [])
        return None

    async def get_outbound_calls(self) -> Optional[List[Dict[str, Any]]]:
        """Get list of active outbound calls."""
        result = await self._request("POST", "/api/v1.1.0/outbound/query")
        if result and result.get("status") == "Success":
            return result.get("outbound", [])
        return None

    # ==================== Queue Operations ====================

    async def get_queue_status(self) -> Optional[List[Dict[str, Any]]]:
        """Get queue status including waiting callers and agents."""
        result = await self._request("POST", "/api/v1.1.0/queuestatus/query")
        if result and result.get("status") == "Success":
            return result.get("queues", [])
        return None

    async def pause_queue_agent(
        self,
        queue_number: str,
        extension: str,
    ) -> Optional[Dict[str, Any]]:
        """Pause an agent in a queue."""
        return await self._request(
            "POST",
            "/api/v1.1.0/queue/pause_agent",
            {
                "queueid": queue_number,
                "extid": extension,
            }
        )

    async def unpause_queue_agent(
        self,
        queue_number: str,
        extension: str,
    ) -> Optional[Dict[str, Any]]:
        """Unpause an agent in a queue."""
        return await self._request(
            "POST",
            "/api/v1.1.0/queue/unpause_agent",
            {
                "queueid": queue_number,
                "extid": extension,
            }
        )

    # ==================== CDR & Recordings ====================

    async def get_cdr_list(
        self,
        page: int = 1,
        page_size: int = 100,
        sort_by: str = "time",
        order_by: str = "desc",
    ) -> Optional[Dict[str, Any]]:
        """Get CDR list for Cloud PBX."""
        if self.settings.is_cloud_pbx:
            result = await self._request(
                "GET",
                "/api/v1.1.0/cdr/list",
                {
                    "page": page,
                    "page_size": page_size,
                    "sort_by": sort_by,
                    "order_by": order_by,
                }
            )
            return result
        return None

    async def download_cdr(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Request CDR download.

        Args:
            start_time: Start time in format "YYYY-MM-DD HH:MM:SS"
            end_time: End time in format "YYYY-MM-DD HH:MM:SS"
        """
        # For Cloud PBX, use the CDR list endpoint
        if self.settings.is_cloud_pbx:
            return await self.get_cdr_list(page=1, page_size=100)

        payload = {}
        if start_time:
            payload["starttime"] = start_time
        if end_time:
            payload["endtime"] = end_time

        return await self._request(
            "POST",
            "/api/v1.1.0/cdr/get_random",
            payload
        )

    async def download_recording(self, recording_file: str) -> Optional[Dict[str, Any]]:
        """Get download URL for a recording."""
        if not await self.ensure_authenticated():
            return None

        if self.settings.is_cloud_pbx:
            # Cloud PBX: Use the download endpoint with file parameter
            # URL encode the filename to handle special characters like +
            from urllib.parse import quote
            encoded_file = quote(recording_file, safe='')

            client = await self.get_client()
            url = f"{self.base_url}/openapi/v1.0/recording/download?file={encoded_file}&access_token={self.token}"
            headers = {"User-Agent": "OpenAPI"}

            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                result = response.json()

                logger.info(f"Recording API response for {recording_file}: {result}")

                if result.get("errcode") == 0:
                    download_resource_url = result.get("download_resource_url")
                    if download_resource_url:
                        # Construct full download URL
                        full_url = f"{self.base_url}{download_resource_url}?access_token={self.token}"
                        return {"status": "Success", "download_url": full_url}
                    else:
                        logger.warning(f"No download_resource_url in response: {result}")
                        return {"status": "Failed", "errmsg": "Recording file not available"}
                else:
                    logger.warning(f"Recording API error: {result}")
                return result
            except Exception as e:
                logger.error(f"Error getting recording URL: {e}")
                return {"status": "Failed", "errmsg": str(e)}

        # On-premise PBX
        result = await self._request(
            "POST",
            "/api/v1.1.0/recording/get_random",
            {"recording": recording_file}
        )
        if result and result.get("status") == "Success":
            random_str = result.get("random")
            if random_str:
                download_url = f"{self.base_url}/api/v1.1.0/recording/download?recording={recording_file}&random={random_str}&token={self.token}"
                return {"status": "Success", "download_url": download_url}
        return result

    async def get_recording_list(
        self,
        page: int = 1,
        page_size: int = 100,
        uid: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get list of recordings (Cloud PBX). Can filter by uid for specific call."""
        if self.settings.is_cloud_pbx:
            params = {
                "page": page,
                "page_size": page_size,
                "sort_by": "id",
                "order_by": "desc",
            }
            # Add UID filter if provided (reduces API calls)
            if uid:
                params["uid"] = uid
            return await self._request(
                "GET",
                "/api/v1.1.0/recording/list",
                params
            )
        return None

    # ==================== Voicemail ====================

    async def get_voicemails(self, extension: str) -> Optional[List[Dict[str, Any]]]:
        """Get voicemails for an extension."""
        result = await self._request(
            "POST",
            "/api/v1.1.0/voicemail/query",
            {"extid": extension}
        )
        if result and result.get("status") == "Success":
            return result.get("voicemails", [])
        return None

    async def delete_voicemail(
        self,
        extension: str,
        voicemail_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Delete a voicemail."""
        return await self._request(
            "POST",
            "/api/v1.1.0/voicemail/delete",
            {
                "extid": extension,
                "voicemail": voicemail_id,
            }
        )

    # ==================== SMS ====================

    async def send_sms(
        self,
        trunk: str,
        phone_number: str,
        message: str,
    ) -> Optional[Dict[str, Any]]:
        """Send an SMS message via trunk."""
        return await self._request(
            "POST",
            "/api/v1.1.0/sms/send",
            {
                "trunk": trunk,
                "phonenumber": phone_number,
                "message": message,
            }
        )

    # ==================== Logout ====================

    async def logout(self) -> bool:
        """Logout from the PBX API."""
        if not self.token:
            return True

        result = await self._request("POST", "/api/v1.1.0/logout")
        if result:
            self.token = None
            logger.info("Successfully logged out from Yeastar PBX")
            return True
        return False


# Global client instance
_yeastar_client: Optional[YeastarClient] = None


def get_yeastar_client() -> YeastarClient:
    """Get or create the global Yeastar client instance."""
    global _yeastar_client
    if _yeastar_client is None:
        _yeastar_client = YeastarClient()
    return _yeastar_client
