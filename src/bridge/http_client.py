"""
HTTP Client for OpenClaw API Integration

Provides synchronous and async HTTP communication with OpenClaw's
OpenAI-compatible /v1/chat/completions endpoint.
"""
import json
import time
import asyncio
from dataclasses import dataclass, field
from typing import Optional, Any
from urllib.parse import urljoin

import structlog
import aiohttp

from bridge.config import get_config, OpenClawConfig

logger = structlog.get_logger()


class OpenClawHTTPError(Exception):
    """Base error for OpenClaw HTTP operations."""
    pass


class OpenClawHTTPTimeoutError(OpenClawHTTPError):
    """Raised when HTTP request times out."""
    pass


class OpenClawHTTPConnectionError(OpenClawHTTPError):
    """Raised when connection to OpenClaw fails."""
    pass


@dataclass
class HTTPClientStats:
    """HTTP client statistics."""
    requests_sent: int = 0
    successful_responses: int = 0
    failed_requests: int = 0
    timeout_count: int = 0
    total_latency_ms: float = 0.0
    last_request_time: Optional[float] = None
    last_response_time: Optional[float] = None


@dataclass
class ChatResponse:
    """Response from chat completions endpoint."""
    content: str
    model: str
    finish_reason: str
    raw_response: dict = field(default_factory=dict)
    
    @classmethod
    def from_openai_response(cls, response: dict) -> "ChatResponse":
        """Parse OpenAI-compatible response format."""
        try:
            choices = response.get("choices", [])
            if not choices:
                raise ValueError("No choices in response")
            
            first_choice = choices[0]
            message = first_choice.get("message", {})
            content = message.get("content", "")
            finish_reason = first_choice.get("finish_reason", "stop")
            model = response.get("model", "unknown")
            
            return cls(
                content=content,
                model=model,
                finish_reason=finish_reason,
                raw_response=response,
            )
        except Exception as e:
            logger.error("Failed to parse OpenAI response", error=str(e), response=response)
            raise OpenClawHTTPError(f"Invalid response format: {e}")


class OpenClawHTTPClient:
    """
    HTTP client for OpenAI-compatible API communication with OpenClaw.
    
    Uses /v1/chat/completions endpoint for:
    - Wake word acknowledgement
    - User message submission
    
    Features:
    - Async and sync methods
    - Automatic timeout handling
    - Connection retry logic
    - Request/response logging
    """
    
    def __init__(
        self,
        config: Optional[OpenClawConfig] = None,
        timeout: Optional[float] = None,
    ):
        """
        Initialize HTTP client.
        
        Args:
            config: OpenClaw configuration (loads from defaults if None)
            timeout: Request timeout in seconds (uses config if None)
        """
        self.config = config or get_config().openclaw
        self.timeout = timeout or self.config.timeout
        
        # Build base URL
        protocol = "https" if self.config.secure else "http"
        self.base_url = f"{protocol}://{self.config.host}:{self.config.port}"
        self.chat_endpoint = "/v1/chat/completions"
        self.model = "openclaw:main"
        
        # Stats
        self._stats = HTTPClientStats()
        
        # Session (for async operations)
        self._session: Optional[aiohttp.ClientSession] = None
        
        logger.info(
            "HTTP client initialized",
            base_url=self.base_url,
            timeout=self.timeout,
            model=self.model,
        )
    
    @property
    def stats(self) -> HTTPClientStats:
        """Get client statistics."""
        return self._stats
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=self._get_headers(),
            )
        return self._session
    
    def _get_headers(self) -> dict:
        """Get request headers including auth if configured."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Use get_auth_token() to check config and env var
        auth_token = None
        if hasattr(self.config, 'get_auth_token'):
            auth_token = self.config.get_auth_token()
        elif hasattr(self.config, 'auth_token'):
            auth_token = self.config.auth_token or self.config.api_key
        
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        return headers
    
    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def send_chat_request(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        **kwargs
    ) -> ChatResponse:
        """
        Send a chat completion request to OpenClaw.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use (defaults to openclaw:main)
            **kwargs: Additional parameters (temperature, max_tokens, etc.)
            
        Returns:
            ChatResponse with the generated content
            
        Raises:
            OpenClawHTTPTimeoutError: If request times out
            OpenClawHTTPConnectionError: If connection fails
            OpenClawHTTPError: For other errors
        """
        start_time = time.time()
        model = model or self.model
        url = urljoin(self.base_url, self.chat_endpoint)
        
        payload = {
            "model": model,
            "messages": messages,
            **kwargs
        }
        
        self._stats.requests_sent += 1
        self._stats.last_request_time = start_time
        
        logger.debug(
            "Sending HTTP request",
            url=url,
            model=model,
            message_count=len(messages),
        )
        
        try:
            session = await self._get_session()
            
            async with session.post(url, json=payload) as response:
                response_time = time.time()
                latency_ms = (response_time - start_time) * 1000
                self._stats.total_latency_ms += latency_ms
                self._stats.last_response_time = response_time
                
                if response.status == 200:
                    data = await response.json()
                    self._stats.successful_responses += 1
                    
                    chat_response = ChatResponse.from_openai_response(data)
                    
                    logger.info(
                        "HTTP request successful",
                        status=response.status,
                        latency_ms=round(latency_ms, 2),
                        content_length=len(chat_response.content),
                    )
                    
                    return chat_response
                    
                else:
                    error_text = await response.text()
                    self._stats.failed_requests += 1
                    
                    logger.error(
                        "HTTP request failed",
                        status=response.status,
                        error=error_text[:500],
                    )
                    raise OpenClawHTTPError(f"HTTP {response.status}: {error_text[:200]}")
                    
        except asyncio.TimeoutError:
            self._stats.timeout_count += 1
            self._stats.failed_requests += 1
            logger.warning("HTTP request timed out", timeout=self.timeout)
            raise OpenClawHTTPTimeoutError(f"Request timed out after {self.timeout}s")
            
        except aiohttp.ClientError as e:
            self._stats.failed_requests += 1
            logger.error("HTTP connection error", error=str(e))
            raise OpenClawHTTPConnectionError(f"Connection error: {e}")
            
        except OpenClawHTTPError:
            raise
            
        except Exception as e:
            self._stats.failed_requests += 1
            logger.error("Unexpected HTTP error", error=str(e), exc_info=True)
            raise OpenClawHTTPError(f"Unexpected error: {e}")
    
    async def send_wake_ack(self, wake_word: Optional[str] = None) -> Optional[str]:
        """
        Send wake word acknowledgement to OpenClaw and get response.
        
        Constructs a minimal message prompting OpenClaw to respond
        with an acknowledgement phrase for wake word activation.
        
        Args:
            wake_word: Optional detected wake word phrase
            
        Returns:
            Response text from OpenClaw, or None if no meaningful response
            
        Raises:
            OpenClawHTTPTimeoutError: If request times out
            OpenClawHTTPConnectionError: If connection fails
        """
        # Notify OpenClaw that the wake word was detected. No system prompt —
        # OpenClaw's own persona and configuration determine the response.
        user_content = "Wake word detected."
        if wake_word:
            user_content = f"Wake word '{wake_word}' detected."

        messages = [
            {"role": "user", "content": user_content}
        ]
        
        logger.info("Sending wake word acknowledgement via HTTP", wake_word=wake_word)
        
        try:
            response = await self.send_chat_request(messages, max_tokens=50)
            return response.content.strip() if response.content else None
            
        except OpenClawHTTPTimeoutError:
            logger.warning("Wake ack request timed out")
            raise
            
        except OpenClawHTTPError as e:
            logger.error("Wake ack request failed", error=str(e))
            raise
    
    async def send_message(self, text: str, conversation_history: Optional[list] = None) -> Optional[str]:
        """
        Send user message to OpenClaw and get response.
        
        Args:
            text: Transcribed user message
            conversation_history: Optional previous conversation context
            
        Returns:
            Response text from OpenClaw, or None if no meaningful response
            
        Raises:
            OpenClawHTTPTimeoutError: If request times out
            OpenClawHTTPConnectionError: If connection fails
        """
        # Build messages list
        messages = [
            {"role": "user", "content": text}
        ]
        
        # Add conversation history if provided
        if conversation_history:
            history_messages = [
                {"role": turn.get("role", "user"), "content": turn.get("content", "")}
                for turn in conversation_history[-10:]  # Limit history
            ]
            messages = history_messages + messages
        
        logger.info(
            "Sending user message via HTTP",
            text_length=len(text),
            has_history=bool(conversation_history),
        )
        
        try:
            response = await self.send_chat_request(messages)
            return response.content.strip() if response.content else None
            
        except OpenClawHTTPTimeoutError:
            logger.warning("Message request timed out")
            raise
            
        except OpenClawHTTPError as e:
            logger.error("Message request failed", error=str(e))
            raise
    
    # Synchronous wrappers for use in non-async contexts
    
    def send_wake_ack_sync(self, wake_word: Optional[str] = None) -> Optional[str]:
        """
        Synchronous wrapper for send_wake_ack.
        
        Creates a new event loop if needed and properly closes the session.
        """
        async def _run():
            try:
                result = await self.send_wake_ack(wake_word)
                return result
            finally:
                await self.close()
        
        try:
            loop = asyncio.get_running_loop()
            return asyncio.create_task(_run())
        except RuntimeError:
            return asyncio.run(_run())
    
    def send_message_sync(self, text: str, conversation_history: Optional[list] = None) -> Optional[str]:
        """
        Synchronous wrapper for send_message.
        
        Creates a new event loop if needed and properly closes the session.
        """
        async def _run():
            try:
                result = await self.send_message(text, conversation_history)
                return result
            finally:
                await self.close()
        
        try:
            loop = asyncio.get_running_loop()
            return asyncio.create_task(_run())
        except RuntimeError:
            return asyncio.run(_run())
    
    def get_stats_dict(self) -> dict[str, Any]:
        """Get statistics as dictionary."""
        return {
            "requests_sent": self._stats.requests_sent,
            "successful_responses": self._stats.successful_responses,
            "failed_requests": self._stats.failed_requests,
            "timeout_count": self._stats.timeout_count,
            "total_latency_ms": round(self._stats.total_latency_ms, 2),
            "last_request_time": self._stats.last_request_time,
            "last_response_time": self._stats.last_response_time,
        }


# Convenience function for getting singleton instance
_http_client: Optional[OpenClawHTTPClient] = None


def get_http_client(config: Optional[OpenClawConfig] = None) -> OpenClawHTTPClient:
    """
    Get or create the global HTTP client instance.
    
    Args:
        config: Optional config override
        
    Returns:
        OpenClawHTTPClient instance
    """
    global _http_client
    if _http_client is None:
        _http_client = OpenClawHTTPClient(config)
    return _http_client


def close_http_client() -> None:
    """Close the global HTTP client if initialized."""
    global _http_client
    if _http_client is not None:
        # Can't await in sync context, just clear reference
        _http_client = None