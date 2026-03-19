# -*- coding: utf-8 -*-
"""DingTalk OA Approval REST API client.

This module provides async client for DingTalk approval APIs including:
- Get access token
- Create approval instance
- Get approval instance details
- List approval instance IDs
- Get user todo count
- Get user by mobile
- List departments

Environment Variables (fallback):
- DINGTALK_APP_KEY: Enterprise app AppKey (Client ID)
- DINGTALK_APP_SECRET: Enterprise app AppSecret (Client Secret)
- DINGTALK_AGENT_ID: Application AgentId
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class DingTalkAPIError(Exception):
    """DingTalk API error with error code and message."""
    
    def __init__(self, errcode: int, errmsg: str):
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"DingTalk API Error [{errcode}]: {errmsg}")


class DingTalkApprovalClient:
    """DingTalk OA Approval REST API client.
    
    Provides async methods for interacting with DingTalk approval APIs.
    Supports access token caching with automatic refresh.
    
    Example:
        ```python
        client = DingTalkApprovalClient(
            app_key="your-app-key",
            app_secret="your-app-secret",
            agent_id="your-agent-id"
        )
        
        # Create approval instance
        instance_id = await client.create_process_instance(
            process_code="PROC-XXX",
            originator_user_id="user123",
            dept_id=12345,
            form_values=[{"name": "Amount", "value": "1000"}]
        )
        
        # Get approval details
        details = await client.get_process_instance(instance_id)
        ```
    """
    
    DEFAULT_BASE_URL = "https://oapi.dingtalk.com"
    
    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        agent_id: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
    ):
        """Initialize DingTalk approval client.
        
        Args:
            app_key: Enterprise app AppKey. Falls back to DINGTALK_APP_KEY env var.
            app_secret: Enterprise app AppSecret. Falls back to DINGTALK_APP_SECRET env var.
            agent_id: Application AgentId. Falls back to DINGTALK_AGENT_ID env var.
            base_url: DingTalk API base URL. Defaults to https://oapi.dingtalk.com.
        """
        self.app_key = app_key or os.environ.get("DINGTALK_APP_KEY", "")
        self.app_secret = app_secret or os.environ.get("DINGTALK_APP_SECRET", "")
        self.agent_id = agent_id or os.environ.get("DINGTALK_AGENT_ID", "")
        self.base_url = base_url.rstrip("/")
        
        # Access token cache
        self._access_token: Optional[str] = None
        self._token_expires: float = 0
        
        # HTTP session (created on demand)
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
            )
        return self._session
    
    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def __aenter__(self) -> "DingTalkApprovalClient":
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
    
    def _check_response(self, result: Dict[str, Any]) -> None:
        """Check API response for errors.
        
        Args:
            result: API response dict
            
        Raises:
            DingTalkAPIError: If errcode is non-zero
        """
        errcode = result.get("errcode", 0)
        if errcode != 0:
            errmsg = result.get("errmsg", "Unknown error")
            raise DingTalkAPIError(errcode, errmsg)
    
    # ==================== Authentication ====================
    
    async def get_access_token(self) -> str:
        """Get enterprise app access token.
        
        Access token is valid for 7200 seconds (2 hours).
        Token is cached and automatically refreshed 5 minutes before expiry.
        
        Returns:
            Access token string
            
        Raises:
            DingTalkAPIError: If token request fails
            ValueError: If app_key or app_secret is not configured
        """
        # Check cached token validity (refresh 5 minutes before expiry)
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        
        if not self.app_key or not self.app_secret:
            raise ValueError(
                "app_key and app_secret are required. "
                "Set via constructor or DINGTALK_APP_KEY/DINGTALK_APP_SECRET env vars."
            )
        
        url = f"{self.base_url}/gettoken"
        params = {
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        
        session = await self._get_session()
        async with session.get(url, params=params) as response:
            result = await response.json()
        
        self._check_response(result)
        
        # Cache token with 5-minute buffer before expiry
        self._access_token = result["access_token"]
        self._token_expires = time.time() + result.get("expires_in", 7200) - 300
        
        logger.debug("DingTalk access token refreshed, expires in %d seconds", 
                     result.get("expires_in", 7200))
        
        return self._access_token
    
    # ==================== Approval Instances ====================
    
    async def create_process_instance(
        self,
        process_code: str,
        originator_user_id: str,
        dept_id: int,
        form_values: List[Dict[str, str]],
        approvers: Optional[List[str]] = None,
        cc_list: Optional[List[str]] = None,
    ) -> str:
        """Create a new approval instance.
        
        Args:
            process_code: Approval template unique identifier (from DingTalk admin)
            originator_user_id: Initiator's userId
            dept_id: Initiator's department ID
            form_values: Form parameters, format: [{"name": "Amount", "value": "1000"}]
            approvers: Optional list of approver userIds. Uses template flow if not provided.
            cc_list: Optional list of CC userIds.
            
        Returns:
            Approval instance ID (process_instance_id)
            
        Raises:
            DingTalkAPIError: If creation fails
        """
        url = f"{self.base_url}/topapi/processinstance/create"
        access_token = await self.get_access_token()
        params = {"access_token": access_token}
        
        data = {
            "process_code": process_code,
            "originator_user_id": originator_user_id,
            "dept_id": dept_id,
            "form_component_values": json.dumps(form_values, ensure_ascii=False),
        }
        
        if approvers:
            data["approvers"] = ",".join(approvers)
        
        if cc_list:
            data["cc_list"] = ",".join(cc_list)
        
        session = await self._get_session()
        async with session.post(url, params=params, json=data) as response:
            result = await response.json()
        
        self._check_response(result)
        
        instance_id = result["process_instance_id"]
        logger.info("DingTalk approval instance created: %s", instance_id)
        
        return instance_id
    
    async def get_process_instance(self, process_instance_id: str) -> Dict[str, Any]:
        """Get approval instance details.
        
        Args:
            process_instance_id: Approval instance ID
            
        Returns:
            Approval instance details dict containing:
            - title: Approval title
            - status: NEW, RUNNING, TERMINATED, COMPLETED, CANCELED
            - result: agree, refuse, none
            - originator_userid: Initiator userId
            - form_component_values: Form data
            - operation_records: Operation history
            - tasks: Approval tasks
            
        Raises:
            DingTalkAPIError: If query fails
        """
        url = f"{self.base_url}/topapi/processinstance/get"
        access_token = await self.get_access_token()
        params = {"access_token": access_token}
        data = {"process_instance_id": process_instance_id}
        
        session = await self._get_session()
        async with session.post(url, params=params, json=data) as response:
            result = await response.json()
        
        self._check_response(result)
        
        return result.get("process_instance", {})
    
    async def list_process_instance_ids(
        self,
        process_code: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        size: int = 20,
        cursor: int = 0,
    ) -> Dict[str, Any]:
        """List approval instance IDs.
        
        Args:
            process_code: Approval template unique identifier
            start_time: Start timestamp in milliseconds. Defaults to 7 days ago.
            end_time: End timestamp in milliseconds. Defaults to now.
            size: Page size (max 20)
            cursor: Pagination cursor
            
        Returns:
            Dict containing:
            - list: List of instance IDs
            - next_cursor: Next page cursor (if more results)
            
        Raises:
            DingTalkAPIError: If query fails
        """
        url = f"{self.base_url}/topapi/processinstance/listids"
        access_token = await self.get_access_token()
        params = {"access_token": access_token}
        
        # Default to last 7 days
        if start_time is None:
            start_time = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
        if end_time is None:
            end_time = int(datetime.now().timestamp() * 1000)
        
        data = {
            "process_code": process_code,
            "start_time": start_time,
            "end_time": end_time,
            "size": min(size, 20),
            "cursor": cursor,
        }
        
        session = await self._get_session()
        async with session.post(url, params=params, json=data) as response:
            result = await response.json()
        
        self._check_response(result)
        
        return result.get("result", {})
    
    # ==================== Todo Statistics ====================
    
    async def get_todo_num(self, userid: str) -> Dict[str, Any]:
        """Get user's pending approval count.
        
        Args:
            userid: User's userId
            
        Returns:
            Dict containing:
            - count: Number of pending approvals
            
        Raises:
            DingTalkAPIError: If query fails
        """
        url = f"{self.base_url}/topapi/process/gettodonum"
        access_token = await self.get_access_token()
        params = {"access_token": access_token}
        data = {"userid": userid}
        
        session = await self._get_session()
        async with session.post(url, params=params, json=data) as response:
            result = await response.json()
        
        self._check_response(result)
        
        return {"count": result.get("count", 0)}
    
    # ==================== User Management ====================
    
    async def get_user_by_mobile(self, mobile: str) -> Dict[str, Any]:
        """Get user info by mobile number.
        
        Args:
            mobile: User's mobile number
            
        Returns:
            Dict containing:
            - userid: User's userId
            
        Raises:
            DingTalkAPIError: If query fails
        """
        url = f"{self.base_url}/topapi/v2/user/getbymobile"
        access_token = await self.get_access_token()
        params = {"access_token": access_token}
        data = {"mobile": mobile}
        
        session = await self._get_session()
        async with session.post(url, params=params, json=data) as response:
            result = await response.json()
        
        self._check_response(result)
        
        return result.get("result", {})
    
    async def get_user_detail(self, userid: str) -> Dict[str, Any]:
        """Get user details.
        
        Args:
            userid: User's userId
            
        Returns:
            User details dict containing name, dept_id_list, title, etc.
            
        Raises:
            DingTalkAPIError: If query fails
        """
        url = f"{self.base_url}/topapi/v2/user/get"
        access_token = await self.get_access_token()
        params = {"access_token": access_token}
        data = {"userid": userid}
        
        session = await self._get_session()
        async with session.post(url, params=params, json=data) as response:
            result = await response.json()
        
        self._check_response(result)
        
        return result.get("result", {})
    
    # ==================== Department Management ====================
    
    async def list_departments(self, dept_id: int = 1) -> List[Dict[str, Any]]:
        """List sub-departments.
        
        Args:
            dept_id: Parent department ID. Root department is 1.
            
        Returns:
            List of department dicts containing:
            - dept_id: Department ID
            - name: Department name
            
        Raises:
            DingTalkAPIError: If query fails
        """
        url = f"{self.base_url}/topapi/v2/department/listsub"
        access_token = await self.get_access_token()
        params = {"access_token": access_token}
        data = {"dept_id": dept_id}
        
        session = await self._get_session()
        async with session.post(url, params=params, json=data) as response:
            result = await response.json()
        
        self._check_response(result)
        
        return result.get("result", [])
