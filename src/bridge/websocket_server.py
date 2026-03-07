"""
WebSocket Server for OpenClaw Voice Bridge.

Handles incoming connections from OpenClaw Gateway and implements
the WebSocket protocol for bidirectional voice communication.

Features:
- Asyncio-based WebSocket server
- Thread-safe client management
- Protocol message handling
- Auto-reconnect support for clients
- Broadcast capability
"""
import asyncio
import json
import threading
import time
import uuid
from dataclasses import asdict
from typing import Optional, Callable, Dict, List, Set
from concurrent.futures import ThreadPoolExecutor

import structlog
import websockets
from websockets.server import serve, WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed

from bridge.protocol import (
    BaseMessage,
    TranscriptMessage,
    WakeWordMessage,
    SessionStartMessage,
    SessionEndMessage,
    TTSStartMessage,
    TTSEndMessage,
    ErrorMessage,
    ResponseMessage,
    AcknowledgementMessage,
    ControlMessage,
    ConfigUpdateMessage,
    PingMessage,
    PongMessage,
    HelloMessage,
    parse_message,
    validate_message,
    generate_session_id,
)

logger = structlog.get_logger()


class ClientConnection:
    """Represents a connected WebSocket client."""
    
    def __init__(self, websocket: WebSocketServerProtocol, client_id: str):
        self.websocket = websocket
        self.client_id = client_id
        self.session_id: Optional[str] = None
        self.connected_at = time.time()
        self.last_activity = time.time()
        self.connection_state = "connected"
    
    async def send(self, message: BaseMessage):
        """Send a message to this client."""
        self.last_activity = time.time()
        json_str = message.to_json()
        await self.websocket.send(json_str)
        logger.debug("sent_message", client_id=self.client_id, message_type=message.type)
    
    async def send_json(self, data: dict):
        """Send raw JSON to this client."""
        self.last_activity = time.time()
        await self.websocket.send(json.dumps(data))
        logger.debug("sent_json", client_id=self.client_id, data=data)
    
    def is_alive(self) -> bool:
        """Check if connection is still active."""
        return self.connection_state == "connected"
    
    def close(self):
        """Mark connection as closed."""
        self.connection_state = "disconnected"


class WebSocketServer:
    """
    WebSocket server for OpenClaw Voice Bridge communication.
    
    This server handles connections from OpenClaw Gateway and manages
    bidirectional voice communication with the Voice Bridge orchestrator.
    
    Attributes:
        host: Server host (default: 0.0.0.0)
        port: Server port (default: 18790)
    """
    
    _instance: Optional['WebSocketServer'] = None
    _lock: threading.Lock = threading.Lock()
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 18790,
        max_connections: int = 10
    ):
        """
        Initialize the WebSocket server.
        
        Args:
            host: Server host address
            port: Server port number
            max_connections: Maximum number of concurrent connections
        """
        self.host = host
        self.port = port
        self.max_connections = max_connections
        
        # Client management (thread-safe)
        self._clients: Dict[str, ClientConnection] = {}
        self._clients_lock = asyncio.Lock()
        
        # Message handlers
        self._message_handlers: Dict[str, Callable[[BaseMessage, str], None]] = {}
        self._on_response_callback: Optional[Callable[[str], None]] = None
        
        # Server state
        self._server: Optional[WebSocketServerProtocol] = None
        self._running = False
        self._shutdown_event: Optional[asyncio.Event] = None
        
        # Background tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # Setup message handlers
        self._setup_message_handlers()
        
        logger.info(
            "websocket_server_initialized",
            host=self.host,
            port=self.port,
            max_connections=self.max_connections
        )
    
    def _setup_message_handlers(self):
        """Setup default message handlers."""
        self._message_handlers = {
            "transcript": self._handle_transcript,
            "wake_word_detected": self._handle_wake_word,
            "session_start": self._handle_session_start,
            "session_end": self._handle_session_end,
            "tts_start": self._handle_tts_start,
            "tts_end": self._handle_tts_end,
            "error": self._handle_error,
            "response": self._handle_response,
            "acknowledgement": self._handle_acknowledgement,
            "control": self._handle_control,
            "config_update": self._handle_config_update,
            "ping": self._handle_ping,
            "hello": self._handle_hello,
        }
    
    def _handle_transcript(self, message: BaseMessage, client_id: str):
        """Handle incoming transcript message."""
        if isinstance(message, TranscriptMessage):
            logger.debug(
                "transcript_received",
                client_id=client_id,
                text=message.text[:50] + "..." if len(message.text) > 50 else message.text,
                confidence=message.confidence
            )
    
    def _handle_wake_word(self, message: BaseMessage, client_id: str):
        """Handle incoming wake word message."""
        if isinstance(message, WakeWordMessage):
            logger.info(
                "wake_word_received",
                client_id=client_id,
                wake_word=message.wake_word,
                confidence=message.confidence
            )
    
    def _handle_session_start(self, message: BaseMessage, client_id: str):
        """Handle incoming session start message."""
        if isinstance(message, SessionStartMessage):
            logger.info(
                "session_start_received",
                client_id=client_id,
                session_id=message.session_id
            )
    
    def _handle_session_end(self, message: BaseMessage, client_id: str):
        """Handle incoming session end message."""
        if isinstance(message, SessionEndMessage):
            logger.info(
                "session_end_received",
                client_id=client_id,
                session_id=message.session_id,
                reason=message.reason
            )
    
    def _handle_tts_start(self, message: BaseMessage, client_id: str):
        """Handle incoming TTS start message."""
        if isinstance(message, TTSStartMessage):
            logger.info("tts_start_received", client_id=client_id, session_id=message.session_id)
    
    def _handle_tts_end(self, message: BaseMessage, client_id: str):
        """Handle incoming TTS end message."""
        if isinstance(message, TTSEndMessage):
            logger.info(
                "tts_end_received",
                client_id=client_id,
                session_id=message.session_id,
                was_interrupted=message.was_interrupted
            )
    
    def _handle_error(self, message: BaseMessage, client_id: str):
        """Handle incoming error message."""
        if isinstance(message, ErrorMessage):
            logger.error(
                "error_received",
                client_id=client_id,
                message=message.message,
                code=message.code,
                session_id=message.session_id
            )
    
    def _handle_response(self, message: BaseMessage, client_id: str):
        """Handle incoming response message."""
        if isinstance(message, ResponseMessage):
            logger.info(
                "response_received",
                client_id=client_id,
                session_id=message.session_id,
                text=message.text[:50] + "..." if len(message.text) > 50 else message.text
            )
            
            # Call registered callback
            if self._on_response_callback:
                try:
                    self._on_response_callback(message.text)
                except Exception as e:
                    logger.error("on_response_callback_error", error=str(e))
    
    def _handle_acknowledgement(self, message: BaseMessage, client_id: str):
        """Handle incoming acknowledgement message."""
        if isinstance(message, AcknowledgementMessage):
            logger.info(
                "acknowledgement_received",
                client_id=client_id,
                session_id=message.session_id,
                has_text=message.text is not None
            )
    
    def _handle_control(self, message: BaseMessage, client_id: str):
        """Handle incoming control message."""
        if isinstance(message, ControlMessage):
            logger.info(
                "control_received",
                client_id=client_id,
                action=message.action,
                session_id=message.session_id
            )
    
    def _handle_config_update(self, message: BaseMessage, client_id: str):
        """Handle incoming config update message."""
        if isinstance(message, ConfigUpdateMessage):
            logger.info(
                "config_update_received",
                client_id=client_id,
                config_keys=list(message.config.keys())
            )
    
    def _handle_ping(self, message: BaseMessage, client_id: str):
        """Handle incoming ping message - send pong response."""
        logger.debug("ping_received", client_id=client_id)
        # Find the client and send pong
        for cid, client in self._clients.items():
            if cid == client_id:
                import asyncio
                asyncio.create_task(client.send(PongMessage()))
                break
    
    def _handle_hello(self, message: BaseMessage, client_id: str):
        """Handle incoming hello message."""
        if isinstance(message, HelloMessage):
            logger.info(
                "hello_received",
                client_id=client_id,
                version=message.version,
                capabilities=message.capabilities
            )
    
    async def handle_client(self, websocket: WebSocketServerProtocol, path: str):
        """
        Handle a new WebSocket client connection.
        
        Args:
            websocket: The WebSocket protocol instance
            path: Connection path
        """
        client_id = str(uuid.uuid4())[:8]  # Short ID for logging
        
        logger.info(
            "client_connected",
            client_id=client_id,
            path=path
        )
        
        # Create client connection
        client = ClientConnection(websocket, client_id)
        
        # Register client
        async with self._clients_lock:
            if len(self._clients) >= self.max_connections:
                logger.warning(
                    "max_connections_reached",
                    client_id=client_id,
                    max_connections=self.max_connections
                )
                await websocket.close(1013, "Server full")
                return
            
            self._clients[client_id] = client
        
        try:
            # Send hello to client
            hello_msg = HelloMessage(
                version="1.0.0",
                capabilities=[
                    "transcript",
                    "wake_word_detected",
                    "session_start",
                    "session_end",
                    "tts_start",
                    "tts_end",
                    "error",
                    "response",
                    "acknowledgement",
                    "control",
                    "config_update",
                ]
            )
            await client.send(hello_msg)
            
            # Main message loop
            async for raw_message in websocket:
                self.last_activity = time.time()
                
                try:
                    # Parse message
                    message = parse_message(raw_message)
                    
                    # Handle message
                    handler = self._message_handlers.get(message.type)
                    if handler:
                        handler(message, client_id)
                    else:
                        logger.warning(
                            "unknown_message_type",
                            client_id=client_id,
                            type=message.type
                        )
                
                except ValueError as e:
                    logger.error(
                        "message_parse_error",
                        client_id=client_id,
                        error=str(e)
                    )
                    error_msg = ErrorMessage(
                        message=f"Parse error: {str(e)}",
                        code="PARSE_ERROR"
                    )
                    await client.send(error_msg)
        
        except ConnectionClosed as e:
            logger.info(
                "client_disconnected",
                client_id=client_id,
                code=e.code,
                reason=e.reason
            )
        
        finally:
            # Unregister client
            async with self._clients_lock:
                if client_id in self._clients:
                    del self._clients[client_id]
            
            logger.info("client_cleanup_complete", client_id=client_id)
    
    async def start(self):
        """Start the WebSocket server."""
        if self._running:
            logger.warning("websocket_server_already_running")
            return False
        
        logger.info("starting_websocket_server", host=self.host, port=self.port)
        
        self._shutdown_event = asyncio.Event()
        
        # Start server (not using context manager to allow proper shutdown)
        self._server = await serve(
            self.handle_client,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        
        self._running = True
        
        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        logger.info(
            "websocket_server_started",
            host=self.host,
            port=self.port,
            server=self._server
        )
        
        return True
    
    async def serve_forever(self):
        """Run the server forever until stopped. Used internally for the main loop."""
        # Wait until shutdown event is set
        if self._shutdown_event:
            await self._shutdown_event.wait()
    
    async def stop(self):
        """Stop the WebSocket server."""
        if not self._running:
            logger.warning("websocket_server_not_running")
            return
        
        logger.info("stopping_websocket_server")
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        
        # Close all client connections
        async with self._clients_lock:
            for client_id, client in self._clients.items():
                try:
                    await client.websocket.close(1001, "Server shutting down")
                except Exception as e:
                    logger.debug("error_closing_client", client_id=client_id, error=str(e))
            self._clients.clear()
        
        # Stop server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        
        self._running = False
        
        logger.info("websocket_server_stopped")
    
    async def _cleanup_loop(self):
        """Periodically clean up stale connections."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                current_time = time.time()
                stale_threshold = 300  # 5 minutes
                
                async with self._clients_lock:
                    stale_clients = [
                        client_id
                        for client_id, client in self._clients.items()
                        if (current_time - client.last_activity) > stale_threshold
                    ]
                
                for client_id in stale_clients:
                    logger.info("stale_client_cleanup", client_id=client_id)
                    async with self._clients_lock:
                        if client_id in self._clients:
                            client = self._clients[client_id]
                            try:
                                await client.websocket.close(
                                    1001,
                                    "Connection timeout"
                                )
                            except Exception as e:
                                logger.debug(
                                    "error_closing_stale_client",
                                    client_id=client_id,
                                    error=str(e)
                                )
                            del self._clients[client_id]
            
            except asyncio.CancelledError:
                break
            
            except Exception as e:
                logger.error("cleanup_loop_error", error=str(e))
    
    def send_transcript(self, text: str, confidence: float, session_id: Optional[str] = None):
        """
        Send a transcript message to all connected clients.
        
        Args:
            text: Transcribed text
            confidence: Confidence score (0.0 - 1.0)
            session_id: Session ID (generates new if None)
        """
        message = TranscriptMessage(
            text=text,
            confidence=confidence,
            session_id=session_id or generate_session_id()
        )
        self.broadcast(message)
    
    def send_wake_word(self, wake_word: str = "computer", confidence: float = 1.0, session_id: Optional[str] = None):
        """
        Send a wake word detection message to all connected clients.
        
        Args:
            wake_word: Detected wake word
            confidence: Detection confidence (0.0 - 1.0)
            session_id: Session ID (generates new if None)
        """
        message = WakeWordMessage(
            wake_word=wake_word,
            confidence=confidence,
            session_id=session_id or generate_session_id()
        )
        self.broadcast(message)
    
    def broadcast(self, message: BaseMessage):
        """
        Send a message to all connected clients.
        
        Args:
            message: Message to broadcast
        """
        if not self._running:
            logger.warning("cannot_broadcast_server_not_running")
            return
        
        async def broadcast_async():
            async with self._clients_lock:
                disconnected = []
                for client_id, client in self._clients.items():
                    if client.is_alive():
                        try:
                            await client.send(message)
                        except ConnectionClosed:
                            disconnected.append(client_id)
                        except Exception as e:
                            logger.error(
                                "broadcast_error",
                                client_id=client_id,
                                error=str(e)
                            )
                
                # Clean up disconnected clients
                for client_id in disconnected:
                    if client_id in self._clients:
                        del self._clients[client_id]
        
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(broadcast_async())
        except RuntimeError:
            asyncio.run(broadcast_async())
    
    def broadcast_json(self, data: dict):
        """
        Send raw JSON to all connected clients.
        
        Args:
            data: Dictionary to serialize and send
        """
        if not self._running:
            return
        
        async def broadcast_json_async():
            json_str = json.dumps(data)
            async with self._clients_lock:
                disconnected = []
                for client_id, client in self._clients.items():
                    if client.is_alive():
                        try:
                            await client.websocket.send(json_str)
                        except ConnectionClosed:
                            disconnected.append(client_id)
                        except Exception as e:
                            logger.error(
                                "broadcast_json_error",
                                client_id=client_id,
                                error=str(e)
                            )
                
                # Clean up disconnected clients
                for client_id in disconnected:
                    if client_id in self._clients:
                        del self._clients[client_id]
        
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(broadcast_json_async())
        except RuntimeError:
            asyncio.run(broadcast_json_async())
    
    def on_response(self, callback: Callable[[str], None]):
        """
        Register a callback for response messages from OpenClaw.
        
        Args:
            callback: Function to call when a response message is received
        """
        self._on_response_callback = callback
        logger.info("on_response_callback_registered")
    
    def get_client_count(self) -> int:
        """Get the number of connected clients."""
        return len(self._clients)
    
    def get_connected_clients(self) -> List[str]:
        """Get list of connected client IDs."""
        return list(self._clients.keys())
    
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running
    
    @classmethod
    def get_instance(
        cls,
        host: str = "0.0.0.0",
        port: int = 18790,
        max_connections: int = 10
    ) -> 'WebSocketServer':
        """
        Get or create the singleton WebSocket server instance.
        
        Args:
            host: Server host address
            port: Server port number
            max_connections: Maximum number of concurrent connections
            
        Returns:
            WebSocketServer instance
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(host=host, port=port, max_connections=max_connections)
            return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (useful for testing)."""
        with cls._lock:
            cls._instance = None


# Legacy compatibility aliases (for __init__.py imports)
VoiceBridgeWebSocketServer = WebSocketServer
ClientSession = ClientConnection
ServerStats = None
ServerState = None
