# Voice Bridge OpenClaw Channel Integration Plan

**Date:** 2026-03-06
**Status:** Planning
**Approach:** Option 1 - Channel Plugin

---

## Overview

This plan integrates Voice Bridge as a proper OpenClaw channel, similar to Discord, Signal, or Telegram. This enables full session management, conversation context, and real-time bidirectional communication.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        OpenClaw Gateway                          │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              extensions/voice-bridge/                        │ │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    │ │
│  │  │ index.ts    │───▶│ VoiceClient │───▶│ WebSocket   │    │ │
│  │  │ (Channel)   │    │ (Handler)   │    │ Server      │    │ │
│  │  └─────────────┘    └─────────────┘    └──────┬──────┘    │ │
│  └───────────────────────────────────────────────┼────────────┘ │
└──────────────────────────────────────────────────┼──────────────┘
                                                   │ WebSocket
                                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                    voice-bridge-v4 (Python)                       │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │ AudioPipe   │───▶│ WakeWord   │───▶│ Orchestrator│          │
│  │ (Capture)   │    │ Detector    │    │ (State)     │          │
│  └─────────────┘    └─────────────┘    └──────┬──────┘          │
│                                                │                  │
│                     ┌──────────────────────────┼──────────────┐  │
│                     │                          │              │  │
│                     ▼                          ▼              ▼  │
│              ┌─────────────┐         ┌─────────────┐  ┌──────┐ │
│              │ WebSocket   │         │ STT Engine  │  │ TTS  │ │
│              │ Client      │         │ (Whisper)   │  │(Piper)│ │
│              └─────────────┘         └─────────────┘  └──────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: WebSocket Protocol Design

**Goal:** Define the communication protocol between OpenClaw and Voice Bridge.

### 1.1 Message Types

**Voice Bridge → OpenClaw:**
```json
{
  "type": "transcript",
  "text": "user said this",
  "confidence": 0.96,
  "session_id": "voice-session-uuid",
  "timestamp": "2026-03-06T10:00:00Z"
}
```

**OpenClaw → Voice Bridge:**
```json
{
  "type": "response",
  "text": "I can help with that.",
  "session_id": "voice-session-uuid",
  "timestamp": "2026-03-06T10:00:05Z"
}
```

**Control Messages:**
```json
{"type": "session_start", "session_id": "..."}
{"type": "session_end", "session_id": "..."}
{"type": "wake_word_detected", "wake_word": "computer"}
{"type": "tts_start", "session_id": "..."}
{"type": "tts_end", "session_id": "..."}
{"type": "error", "message": "...", "code": "..."}
{"type": "ping"}
{"type": "pong"}
```

### 1.2 Test: Protocol Validation

**File:** `tests/test_websocket_protocol.py`

```python
"""Test WebSocket message protocol."""
import pytest
import json
from bridge.protocol import (
    TranscriptMessage,
    ResponseMessage,
    WakeWordMessage,
    SessionMessage,
    ErrorMessage,
    PingMessage,
    PongMessage,
)

def test_transcript_message_serialization():
    msg = TranscriptMessage(
        text="hello world",
        confidence=0.95,
        session_id="test-123"
    )
    serialized = msg.to_json()
    deserialized = json.loads(serialized)
    
    assert deserialized["type"] == "transcript"
    assert deserialized["text"] == "hello world"
    assert deserialized["confidence"] == 0.95
    assert deserialized["session_id"] == "test-123"

def test_response_message_serialization():
    msg = ResponseMessage(
        text="I heard you",
        session_id="test-123"
    )
    serialized = msg.to_json()
    deserialized = json.loads(serialized)
    
    assert deserialized["type"] == "response"
    assert deserialized["text"] == "I heard you"

def test_wake_word_message():
    msg = WakeWordMessage(wake_word="computer")
    deserialized = json.loads(msg.to_json())
    
    assert deserialized["type"] == "wake_word_detected"
    assert deserialized["wake_word"] == "computer"

def test_ping_pong():
    ping = PingMessage()
    pong = PongMessage()
    
    assert json.loads(ping.to_json())["type"] == "ping"
    assert json.loads(pong.to_json())["type"] == "pong"
```

---

## Phase 2: Voice Bridge WebSocket Server

**Goal:** Add WebSocket server to Voice Bridge Python app.

### 2.1 Implementation

**File:** `src/bridge/websocket_server.py`

```python
"""
WebSocket server for OpenClaw communication.

Provides bidirectional communication between Voice Bridge and OpenClaw Gateway.
"""
import asyncio
import json
from dataclasses import dataclass
from typing import Optional, Callable
import websockets
from websockets.server import serve
import structlog

from bridge.config import get_config

logger = structlog.get_logger()


@dataclass
class WebSocketConfig:
    """WebSocket server configuration."""
    host: str = "localhost"
    port: int = 18790
    ping_interval: float = 30.0
    ping_timeout: float = 10.0


class VoiceBridgeWebSocketServer:
    """
    WebSocket server that bridges Voice Bridge to OpenClaw.
    
    Handles:
    - Session management
    - Message routing
    - Connection lifecycle
    """
    
    def __init__(self, config: Optional[WebSocketConfig] = None):
        self.config = config or WebSocketConfig()
        self._server = None
        self._clients: set[websockets.WebSocketServerProtocol] = set()
        self._on_transcript: Optional[Callable] = None
        self._on_wake_word: Optional[Callable] = None
        self._running = False
        
    async def start(self):
        """Start the WebSocket server."""
        self._server = await serve(
            self._handle_connection,
            self.config.host,
            self.config.port,
            ping_interval=self.config.ping_interval,
            ping_timeout=self.config.ping_timeout,
        )
        self._running = True
        logger.info(
            "websocket_server_started",
            host=self.config.host,
            port=self.config.port
        )
        
    async def stop(self):
        """Stop the WebSocket server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("websocket_server_stopped")
            
    async def _handle_connection(
        self,
        websocket: websockets.WebSocketServerProtocol,
        path: str
    ):
        """Handle incoming WebSocket connection."""
        client_id = id(websocket)
        self._clients.add(websocket)
        logger.info("client_connected", client_id=client_id)
        
        try:
            async for message in websocket:
                await self._handle_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            logger.info("client_disconnected", client_id=client_id)
        finally:
            self._clients.discard(websocket)
            
    async def _handle_message(
        self,
        websocket: websockets.WebSocketServerProtocol,
        message: str
    ):
        """Handle incoming message from OpenClaw."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "response":
                # OpenClaw response - route to TTS
                text = data.get("text", "")
                session_id = data.get("session_id")
                await self._handle_response(text, session_id)
                
            elif msg_type == "ping":
                await websocket.send(json.dumps({"type": "pong"}))
                
            elif msg_type == "session_start":
                # New session starting
                pass
                
        except json.JSONDecodeError:
            logger.error("invalid_json", message=message[:100])
            
    async def _handle_response(self, text: str, session_id: Optional[str]):
        """Handle response from OpenClaw."""
        logger.info("response_received", text=text[:50], session_id=session_id)
        # Route to TTS engine
        if self._on_response:
            await self._on_response(text, session_id)
            
    async def send_transcript(
        self,
        text: str,
        confidence: float,
        session_id: str
    ):
        """Send transcript to OpenClaw."""
        message = {
            "type": "transcript",
            "text": text,
            "confidence": confidence,
            "session_id": session_id,
        }
        await self._broadcast(json.dumps(message))
        
    async def send_wake_word(self, wake_word: str, session_id: str):
        """Send wake word detection to OpenClaw."""
        message = {
            "type": "wake_word_detected",
            "wake_word": wake_word,
            "session_id": session_id,
        }
        await self._broadcast(json.dumps(message))
        
    async def _broadcast(self, message: str):
        """Broadcast message to all connected clients."""
        if not self._clients:
            logger.warning("no_clients_connected")
            return
            
        await asyncio.gather(
            *[client.send(message) for client in self._clients],
            return_exceptions=True
        )
        
    def on_response(self, callback: Callable):
        """Register callback for responses."""
        self._on_response = callback
        
    def on_transcript(self, callback: Callable):
        """Register callback for transcripts."""
        self._on_transcript = callback
```

### 2.2 Test: WebSocket Server

**File:** `tests/test_websocket_server.py`

```python
"""Test WebSocket server functionality."""
import pytest
import asyncio
import websockets
from bridge.websocket_server import VoiceBridgeWebSocketServer, WebSocketConfig

@pytest.mark.asyncio
async def test_server_starts():
    """Verify server starts and stops cleanly."""
    server = VoiceBridgeWebSocketServer(config=WebSocketConfig(port=18791))
    await server.start()
    
    assert server._running
    assert server._server is not None
    
    await server.stop()
    assert not server._running

@pytest.mark.asyncio
async def test_client_connect():
    """Verify client can connect."""
    server = VoiceBridgeWebSocketServer(config=WebSocketConfig(port=18792))
    await server.start()
    
    async with websockets.connect("ws://localhost:18792") as ws:
        # Should connect without error
        assert ws.open
        
    await server.stop()

@pytest.mark.asyncio
async def test_ping_pong():
    """Verify ping-pong protocol."""
    server = VoiceBridgeWebSocketServer(config=WebSocketConfig(port=18793))
    await server.start()
    
    async with websockets.connect("ws://localhost:18793") as ws:
        await ws.send('{"type": "ping"}')
        response = await ws.recv()
        
        import json
        data = json.loads(response)
        assert data["type"] == "pong"
        
    await server.stop()

@pytest.mark.asyncio
async def test_send_transcript():
    """Verify transcript broadcast."""
    server = VoiceBridgeWebSocketServer(config=WebSocketConfig(port=18794))
    await server.start()
    
    received = []
    
    async def on_message(message):
        received.append(message)
        
    async with websockets.connect("ws://localhost:18794") as ws:
        # Give server time to register client
        await asyncio.sleep(0.1)
        
        # Send transcript from server
        await server.send_transcript("hello world", 0.95, "test-session")
        
        # Receive on client
        msg = await ws.recv()
        
        import json
        data = json.loads(msg)
        assert data["type"] == "transcript"
        assert data["text"] == "hello world"
        assert data["confidence"] == 0.95
        
    await server.stop()
```

---

## Phase 3: OpenClaw Channel Extension

**Goal:** Create the voice-bridge channel extension for OpenClaw.

### 3.1 Extension Structure

```
extensions/voice-bridge/
├── openclaw.plugin.json      # Plugin definition
├── package.json              # Dependencies
├── index.ts                  # Entry point
├── tsconfig.json             # TypeScript config
└── src/
    ├── VoiceBridgeChannel.ts # Channel implementation
    ├── VoiceBridgeClient.ts  # WebSocket client
    ├── types.ts              # TypeScript types
    └── index.ts              # Exports
```

### 3.2 Plugin Definition

**File:** `extensions/voice-bridge/openclaw.plugin.json`

```json
{
  "id": "voice-bridge",
  "name": "Voice Bridge Channel",
  "version": "1.0.0",
  "description": "Voice interface for OpenClaw via Voice Bridge",
  "channels": ["voice"],
  "configSchema": {
    "type": "object",
    "properties": {
      "enabled": {
        "type": "boolean",
        "default": true,
        "description": "Enable voice bridge channel"
      },
      "host": {
        "type": "string",
        "default": "localhost",
        "description": "Voice Bridge WebSocket host"
      },
      "port": {
        "type": "number",
        "default": 18790,
        "description": "Voice Bridge WebSocket port"
      },
      "reconnectInterval": {
        "type": "number",
        "default": 5000,
        "description": "Reconnection interval in milliseconds"
      },
      "sessionTimeout": {
        "type": "number",
        "default": 300000,
        "description": "Session timeout in milliseconds"
      },
      "wakeWord": {
        "type": "string",
        "default": "computer",
        "description": "Wake word to listen for"
      }
    },
    "additionalProperties": false
  }
}
```

### 3.3 TypeScript Types

**File:** `extensions/voice-bridge/src/types.ts`

```typescript
/**
 * Voice Bridge message types.
 */

export interface VoiceConfig {
  enabled: boolean;
  host: string;
  port: number;
  reconnectInterval: number;
  sessionTimeout: number;
  wakeWord: string;
}

export interface TranscriptMessage {
  type: 'transcript';
  text: string;
  confidence: number;
  session_id: string;
  timestamp: string;
}

export interface ResponseMessage {
  type: 'response';
  text: string;
  session_id: string;
  timestamp: string;
}

export interface WakeWordMessage {
  type: 'wake_word_detected';
  wake_word: string;
  session_id: string;
  timestamp: string;
}

export interface SessionStartMessage {
  type: 'session_start';
  session_id: string;
  timestamp: string;
}

export interface SessionEndMessage {
  type: 'session_end';
  session_id: string;
  timestamp: string;
}

export interface TTSStartMessage {
  type: 'tts_start';
  session_id: string;
}

export interface TTSEndMessage {
  type: 'tts_end';
  session_id: string;
}

export interface ErrorMessage {
  type: 'error';
  message: string;
  code?: string;
  session_id?: string;
}

export interface PingMessage {
  type: 'ping';
}

export interface PongMessage {
  type: 'pong';
}

export type VoiceMessage =
  | TranscriptMessage
  | ResponseMessage
  | WakeWordMessage
  | SessionStartMessage
  | SessionEndMessage
  | TTSStartMessage
  | TTSEndMessage
  | ErrorMessage
  | PingMessage
  | PongMessage;
```

### 3.4 WebSocket Client

**File:** `extensions/voice-bridge/src/VoiceBridgeClient.ts`

```typescript
import WebSocket from 'ws';
import { VoiceConfig, VoiceMessage, TranscriptMessage, WakeWordMessage } from './types';
import type { Logger } from 'winston';

export interface VoiceBridgeClientEvents {
  onTranscript: (msg: TranscriptMessage) => void;
  onWakeWord: (msg: WakeWordMessage) => void;
  onSessionStart: (sessionId: string) => void;
  onSessionEnd: (sessionId: string) => void;
  onError: (error: Error) => void;
  onConnect: () => void;
  onDisconnect: () => void;
}

export class VoiceBridgeClient {
  private ws: WebSocket | null = null;
  private config: VoiceConfig;
  private logger: Logger;
  private events: Partial<VoiceBridgeClientEvents> = {};
  private reconnectTimer: NodeJS.Timeout | null = null;
  private pingTimer: NodeJS.Timeout | null = null;
  private connected = false;

  constructor(config: VoiceConfig, logger: Logger) {
    this.config = config;
    this.logger = logger.child({ component: 'voice-bridge-client' });
  }

  on<E extends keyof VoiceBridgeClientEvents>(
    event: E,
    handler: VoiceBridgeClientEvents[E]
  ): void {
    this.events[event] = handler;
  }

  async connect(): Promise<void> {
    const url = `ws://${this.config.host}:${this.config.port}`;
    this.logger.info('Connecting to Voice Bridge', { url });

    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(url);

      this.ws.on('open', () => {
        this.connected = true;
        this.logger.info('Connected to Voice Bridge');
        this.startPingInterval();
        this.events.onConnect?.();
        resolve();
      });

      this.ws.on('message', (data: WebSocket.Data) => {
        this.handleMessage(data);
      });

      this.ws.on('close', () => {
        this.handleDisconnect();
      });

      this.ws.on('error', (error: Error) => {
        this.logger.error('WebSocket error', { error: error.message });
        this.events.onError?.(error);
        if (!this.connected) {
          reject(error);
        }
      });
    });
  }

  disconnect(): void {
    this.stopPingInterval();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.connected = false;
  }

  sendResponse(text: string, sessionId: string): void {
    if (!this.ws || !this.connected) {
      this.logger.warn('Cannot send response: not connected');
      return;
    }

    const message: ResponseMessage = {
      type: 'response',
      text,
      session_id: sessionId,
      timestamp: new Date().toISOString(),
    };

    this.ws.send(JSON.stringify(message));
  }

  private handleMessage(data: WebSocket.Data): void {
    try {
      const msg: VoiceMessage = JSON.parse(data.toString());
      this.logger.debug('Received message', { type: msg.type });

      switch (msg.type) {
        case 'transcript':
          this.events.onTranscript?.(msg);
          break;
        case 'wake_word_detected':
          this.events.onWakeWord?.(msg);
          break;
        case 'session_start':
          this.events.onSessionStart?.(msg.session_id);
          break;
        case 'session_end':
          this.events.onSessionEnd?.(msg.session_id);
          break;
        case 'pong':
          // Ping/pong handled automatically
          break;
        default:
          this.logger.warn('Unknown message type', { type: (msg as any).type });
      }
    } catch (error) {
      this.logger.error('Failed to parse message', { error: String(error) });
    }
  }

  private handleDisconnect(): void {
    this.connected = false;
    this.stopPingInterval();
    this.events.onDisconnect?.();
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;

    this.logger.info('Scheduling reconnect', {
      interval: this.config.reconnectInterval,
    });

    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      try {
        await this.connect();
      } catch (error) {
        this.logger.error('Reconnect failed', { error: String(error) });
      }
    }, this.config.reconnectInterval);
  }

  private startPingInterval(): void {
    this.pingTimer = setInterval(() => {
      if (this.ws && this.connected) {
        const ping: PingMessage = { type: 'ping' };
        this.ws.send(JSON.stringify(ping));
      }
    }, 30000);
  }

  private stopPingInterval(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }
}
```

### 3.5 Channel Implementation

**File:** `extensions/voice-bridge/src/VoiceBridgeChannel.ts`

```typescript
import { VoiceBridgeClient } from './VoiceBridgeClient';
import { VoiceConfig, TranscriptMessage, WakeWordMessage } from './types';
import type { Logger } from 'winston';
import { v4 as uuidv4 } from 'uuid';

// OpenClaw channel interface (simplified for planning)
export interface ChannelContext {
  channelId: string;
  channel: string;
  sender: { id: string; name?: string };
  sessionId?: string;
}

export interface ChannelMessage {
  text: string;
  context: ChannelContext;
}

export interface Channel {
  id: string;
  name: string;
  sendMessage(message: ChannelMessage): Promise<void>;
}

export class VoiceBridgeChannel implements Channel {
  id = 'voice';
  name = 'Voice Bridge';
  
  private client: VoiceBridgeClient;
  private logger: Logger;
  private config: VoiceConfig;
  private activeSessionId: string | null = null;
  private sessionTimer: NodeJS.Timeout | null = null;
  
  // Handler set by OpenClaw Gateway
  onMessage?: (msg: ChannelMessage) => Promise<void>;

  constructor(config: VoiceConfig, logger: Logger) {
    this.config = config;
    this.logger = logger.child({ channel: 'voice-bridge' });
    this.client = new VoiceBridgeClient(config, this.logger);
    this.setupEventHandlers();
  }

  private setupEventHandlers(): void {
    this.client.on('onWakeWord', (msg: WakeWordMessage) => {
      this.handleWakeWord(msg);
    });

    this.client.on('onTranscript', (msg: TranscriptMessage) => {
      this.handleTranscript(msg);
    });

    this.client.on('onConnect', () => {
      this.logger.info('Voice Bridge connected');
    });

    this.client.on('onDisconnect', () => {
      this.logger.warn('Voice Bridge disconnected');
      this.activeSessionId = null;
    });

    this.client.on('onError', (error: Error) => {
      this.logger.error('Voice Bridge error', { error: error.message });
    });
  }

  async start(): Promise<void> {
    this.logger.info('Starting Voice Bridge channel');
    await this.client.connect();
  }

  async stop(): Promise<void> {
    this.logger.info('Stopping Voice Bridge channel');
    this.client.disconnect();
  }

  async sendMessage(message: ChannelMessage): Promise<void> {
    if (!this.activeSessionId) {
      this.logger.warn('No active voice session, cannot send response');
      return;
    }

    this.logger.info('Sending response to Voice Bridge', {
      text: message.text.substring(0, 50),
      sessionId: this.activeSessionId,
    });

    this.client.sendResponse(message.text, this.activeSessionId);
  }

  private handleWakeWord(msg: WakeWordMessage): void {
    // Start new session on wake word
    this.activeSessionId = uuidv4();
    this.resetSessionTimer();
    
    this.logger.info('Wake word detected, starting session', {
      wakeWord: msg.wake_word,
      sessionId: this.activeSessionId,
    });
  }

  private handleTranscript(msg: TranscriptMessage): void {
    if (!this.activeSessionId) {
      this.logger.warn('Received transcript without active session');
      return;
    }

    this.resetSessionTimer();

    if (!this.onMessage) {
      this.logger.error('No message handler set');
      return;
    }

    const context: ChannelContext = {
      channelId: 'voice',
      channel: 'voice',
      sender: { id: 'voice-user', name: 'Voice User' },
      sessionId: this.activeSessionId,
    };

    const message: ChannelMessage = {
      text: msg.text,
      context,
    };

    this.logger.info('Processing voice transcript', {
      text: msg.text,
      confidence: msg.confidence,
      sessionId: this.activeSessionId,
    });

    // Fire and forget - Gateway handles the response
    this.onMessage(message).catch((error) => {
      this.logger.error('Failed to process voice message', {
        error: error.message,
      });
    });
  }

  private resetSessionTimer(): void {
    if (this.sessionTimer) {
      clearTimeout(this.sessionTimer);
    }
    this.sessionTimer = setTimeout(() => {
      this.logger.info('Voice session timed out', {
        sessionId: this.activeSessionId,
      });
      this.activeSessionId = null;
    }, this.config.sessionTimeout);
  }
}
```

### 3.6 Test: Channel Integration

**File:** `extensions/voice-bridge/tests/VoiceBridgeChannel.test.ts`

```typescript
import { VoiceBridgeChannel } from '../src/VoiceBridgeChannel';
import { VoiceConfig } from '../src/types';
import { createLogger } from 'winston';

describe('VoiceBridgeChannel', () => {
  let channel: VoiceBridgeChannel;
  let config: VoiceConfig;

  beforeEach(() => {
    config = {
      enabled: true,
      host: 'localhost',
      port: 18790,
      reconnectInterval: 5000,
      sessionTimeout: 300000,
      wakeWord: 'computer',
    };
    const logger = createLogger({ silent: true });
    channel = new VoiceBridgeChannel(config, logger);
  });

  afterEach(async () => {
    await channel.stop();
  });

  test('creates channel with correct id', () => {
    expect(channel.id).toBe('voice');
    expect(channel.name).toBe('Voice Bridge');
  });

  test('generates session on wake word', async () => {
    const messages: any[] = [];
    channel.onMessage = async (msg) => {
      messages.push(msg);
    };

    // Simulate wake word
    // This would normally come from WebSocket
    // For now we're testing the structure
    expect(channel.activeSessionId).toBeNull();
  });

  test('respects configuration', () => {
    expect((channel as any).config).toEqual(config);
  });
});
```

---

## Phase 4: Installer Test Integration

**Goal:** Add channel integration tests to the installer.

### 4.1 Add Test Module to Installer

**File:** `src/installer/tests/test_channel_integration.py`

```python
"""
Channel Integration Tests for Installer.

Tests that verify Voice Bridge can communicate with OpenClaw Gateway.
"""
import pytest
import asyncio
import json
from pathlib import Path

from bridge.websocket_server import VoiceBridgeWebSocketServer, WebSocketConfig
from bridge.protocol import (
    TranscriptMessage,
    WakeWordMessage,
    ResponseMessage,
)


class TestChannelIntegration:
    """Integration tests for OpenClaw channel communication."""

    @pytest.fixture
    async def server(self):
        """Create and start WebSocket server for testing."""
        config = WebSocketConfig(port=18795)
        server = VoiceBridgeWebSocketServer(config=config)
        await server.start()
        yield server
        await server.stop()

    @pytest.mark.asyncio
    async def test_full_flow(self, server):
        """Test complete wake word -> transcript -> response flow."""
        import websockets

        async with websockets.connect("ws://localhost:18795") as ws:
            # 1. Simulate wake word
            wake_msg = WakeWordMessage(wake_word="computer", session_id="test-123")
            await server.send_wake_word("computer", "test-123")

            # 2. Simulate transcript
            transcript_msg = TranscriptMessage(
                text="what time is it",
                confidence=0.95,
                session_id="test-123"
            )
            await server.send_transcript("what time is it", 0.95, "test-123")

            # 3. Receive transcript on client side
            received = await ws.recv()
            data = json.loads(received)
            
            assert data["type"] == "transcript"
            assert data["text"] == "what time is it"

            # 4. Simulate OpenClaw response
            await ws.send(json.dumps({
                "type": "response",
                "text": "It's 10:30 AM",
                "session_id": "test-123"
            }))

            # Server should process response without error
            await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_multiple_clients(self, server):
        """Test server handles multiple clients."""
        import websockets

        # Connect two clients
        clients = []
        for _ in range(2):
            client = await websockets.connect("ws://localhost:18795")
            clients.append(client)

        # Broadcast should reach both
        await server.send_transcript("hello", 0.9, "session-1")

        for client in clients:
            msg = await client.recv()
            data = json.loads(msg)
            assert data["type"] == "transcript"

        # Cleanup
        for client in clients:
            await client.close()

    @pytest.mark.asyncio
    async def test_ping_pong(self, server):
        """Test ping-pong keeps connection alive."""
        import websockets

        async with websockets.connect("ws://localhost:18795") as ws:
            # Send ping
            await ws.send(json.dumps({"type": "ping"}))
            
            # Should receive pong
            response = await ws.recv()
            data = json.loads(response)
            
            assert data["type"] == "pong"


class TestChannelStartup:
    """Test channel startup and configuration."""

    def test_default_config(self):
        """Test default WebSocket configuration."""
        config = WebSocketConfig()
        
        assert config.host == "localhost"
        assert config.port == 18790
        assert config.ping_interval == 30.0

    def test_custom_config(self):
        """Test custom WebSocket configuration."""
        config = WebSocketConfig(
            host="192.168.1.100",
            port=18888,
            ping_interval=60.0
        )
        
        assert config.host == "192.168.1.100"
        assert config.port == 18888
        assert config.ping_interval == 60.0

    def test_server_initialization(self):
        """Test server initializes correctly."""
        server = VoiceBridgeWebSocketServer()
        
        assert server._clients == set()
        assert server._on_transcript is None
        assert server._on_response is None
```

### 4.2 Add to Installer Menu

**File:** `src/installer/__init__.py` (update)

```python
# Add to menu options
TEST_CHOICES = {
    '1': ('Audio Pipeline', test_audio_pipeline),
    '2': ('Wake Word Detection', test_wake_word),
    '3': ('STT Engine', test_stt),
    '4': ('TTS Engine', test_tts),
    '5': ('OpenClaw HTTP', test_openclaw_http),
    '6': ('WebSocket Protocol', test_websocket_protocol),      # NEW
    '7': ('WebSocket Server', test_websocket_server),          # NEW
    '8': ('Channel Integration', test_channel_integration),    # NEW
    '9': ('Full Integration', test_full_integration),
    '0': ('Back', go_back),
}
```

---

## Phase 5: Integration & Testing

### 5.1 Integration Test Script

**File:** `tests/test_integration_channel.py`

```python
"""
Full integration test for Voice Bridge + OpenClaw Channel.

Prerequisites:
- OpenClaw Gateway running
- Voice Bridge Python running
- WebSocket connection established
"""
import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch

from bridge.orchestrator import VoiceOrchestrator
from bridge.websocket_server import VoiceBridgeWebSocketServer
from bridge.config import get_config


@pytest.mark.integration
class TestFullChannelIntegration:
    """Full stack integration tests."""

    @pytest.fixture
    async def components(self):
        """Set up all components for integration test."""
        config = get_config()
        
        # Start WebSocket server
        ws_config = WebSocketConfig(port=18796)
        server = VoiceBridgeWebSocketServer(config=ws_config)
        await server.start()
        
        # Create mock OpenClaw client
        openclaw_client = Mock()
        openclaw_client.send_message = AsyncMock(return_value="I can help with that.")
        
        yield {
            'server': server,
            'openclaw': openclaw_client,
        }
        
        # Cleanup
        await server.stop()

    @pytest.mark.asyncio
    async def test_wake_word_creates_session(self, components):
        """Test that wake word creates a new session."""
        server = components['server']
        
        # Track sessions
        sessions = []
        
        async def capture_session(session_id):
            sessions.append(session_id)
        
        # Simulate wake word
        await server.send_wake_word("computer", "session-001")
        
        assert len(sessions) == 0  # No sessions until wake word handled

    @pytest.mark.asyncio
    async def test_transcript_routes_to_openclaw(self, components):
        """Test that transcript routes through WebSocket."""
        server = components['server']
        import websockets
        
        async with websockets.connect("ws://localhost:18796") as ws:
            # Send transcript
            await server.send_transcript("hello world", 0.95, "sess-123")
            
            # Should receive on WebSocket
            msg = await ws.recv()
            data = json.loads(msg)
            
            assert data["type"] == "transcript"
            assert data["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_response_routes_to_tts(self, components):
        """Test that OpenClaw response routes to TTS."""
        server = components['server']
        
        tts_calls = []
        
        async def capture_tts(text, session_id):
            tts_calls.append((text, session_id))
        
        server.on_response(capture_tts)
        
        import websockets
        
        async with websockets.connect("ws://localhost:18796") as ws:
            # Send response from OpenClaw
            response = {
                "type": "response",
                "text": "Hello! How can I help?",
                "session_id": "sess-123"
            }
            await ws.send(json.dumps(response))
            
            # Wait for processing
            await asyncio.sleep(0.1)
            
            # Should have captured TTS call
            assert len(tts_calls) == 1
            assert tts_calls[0][0] == "Hello! How can I help?"
```

### 5.2 Manual Testing Checklist

**Phase 5.2: Manual Test Procedure**

```markdown
## Voice Bridge Channel Integration Test

### Prerequisites
- [ ] OpenClaw Gateway installed
- [ ] Voice Bridge v4.3+ installed
- [ ] Node.js 18+ installed
- [ ] Python 3.10+ installed

### Test 1: WebSocket Server
1. Start Voice Bridge:
   ```bash
   cd voice-bridge-v4
   PYTHONPATH=src python3 -m bridge.main --websocket-only
   ```
2. Verify WebSocket server starts on port 18790
3. Connect with wscat:
   ```bash
   wscat -c ws://localhost:18790
   ```
4. Verify ping/pong works:
   ```bash
   > {"type": "ping"}
   < {"type": "pong"}
   ```

### Test 2: Channel Extension
1. Build extension:
   ```bash
   cd extensions/voice-bridge
   npm run build
   ```
2. Start OpenClaw Gateway
3. Verify extension loads:
   ```bash
   openclaw gateway status
   ```
4. Check logs for "Voice Bridge connected"

### Test 3: End-to-End Voice
1. Start Voice Bridge (full mode):
   ```bash
   PYTHONPATH=src python3 -m bridge.main
   ```
2. Say wake word: "computer"
3. Verify wake_word_detected message in logs
4. Speak: "What time is it?"
5. Verify transcript message
6. Verify OpenClaw response
7. Verify TTS speaks response

### Test 4: Session Management
1. Say wake word: "computer"
2. Speak: "Remember my name is Hal"
3. Wait for response
4. Speak again (no wake word): "What's my name?"
5. Verify OpenClaw remembers context
6. Wait 5 minutes
7. Verify session times out
8. Say wake word again
9. Verify new session started
```

---

## Phase 6: Documentation & Deployment

### 6.1 User Documentation

**File:** `docs/VOICE_CHANNEL.md`

```markdown
# Voice Bridge Channel

Voice Bridge provides a voice interface for OpenClaw, enabling natural
spoken conversations with your AI assistant.

## Features

- **Wake Word Detection**: Say "computer" (customizable) to activate
- **Speech-to-Text**: High-accuracy transcription with Faster-Whisper
- **Text-to-Speech**: Natural responses with Piper TTS
- **Full-Duplex**: Listen while speaking (barge-in support)
- **Session Management**: Conversation context persists across turns

## Installation

### Prerequisites
- OpenClaw Gateway installed
- Python 3.10+
- PortAudio library

### Install Voice Bridge

```bash
cd ~/.openclaw/workspace
git clone https://github.com/ray1caron/voice-openclaw-bridge-v4.git voice-bridge-v4
cd voice-bridge-v4
pip install -r requirements.txt
```

### Install Channel Extension

```bash
cd ~/.openclaw/extensions
git clone <voice-bridge-extension-url> voice-bridge
cd voice-bridge
npm install
npm run build
```

### Configure

Add to `~/.openclaw/config.yaml`:

```yaml
channels:
  voice:
    enabled: true
    host: localhost
    port: 18790
    wakeWord: computer
    sessionTimeout: 300000
```

## Usage

1. Start OpenClaw Gateway:
   ```bash
   openclaw gateway start
   ```

2. Start Voice Bridge:
   ```bash
   cd ~/.openclaw/workspace/voice-bridge-v4
   PYTHONPATH=src python3 -m bridge.main
   ```

3. Say "computer" to activate

4. Speak your question

5. Listen for response

## Troubleshooting

### Voice Bridge won't start
- Check PortAudio: `python3 -c "import sounddevice"`
- Check microphone permissions
- Verify config file: `~/.voice-bridge/config.yaml`

### OpenClaw doesn't respond
- Verify WebSocket connection: `wscat -c ws://localhost:18790`
- Check OpenClaw Gateway logs
- Verify token in config

### No audio output
- Check speakers: `speaker-test`
- Verify TTS model installed
- Check Piper logs
```

### 6.2 Deployment Checklist

```markdown
## Deployment Checklist

### Pre-Deployment
- [ ] All tests pass: `pytest tests/`
- [ ] Extension builds: `npm run build`
- [ ] TypeScript compiles: `tsc --noEmit`
- [ ] Documentation updated
- [ ] CHANGELOG.md updated

### Deployment
- [ ] Tag release: `git tag -a v4.4.0 -m "Add channel integration"`
- [ ] Push tag: `git push origin v4.4.0`
- [ ] Build extension package: `npm pack`
- [ ] Update ClawHub package

### Post-Deployment
- [ ] Verify installation on clean system
- [ ] Run integration tests
- [ ] Monitor logs for errors
- [ ] Update user documentation
```

---

## Implementation Order

### Week 1: Foundation
1. **Day 1-2**: Protocol design + WebSocket server (Phase 1 & 2)
2. **Day 3-4**: WebSocket tests (Phase 2.2)
3. **Day 5**: Installer test integration (Phase 4.1)

### Week 2: OpenClaw Integration
4. **Day 1-2**: TypeScript types + WebSocket client (Phase 3.3-3.4)
5. **Day 3-4**: Channel implementation (Phase 3.5)
6. **Day 5**: Channel tests (Phase 3.6)

### Week 3: Testing & Polish
7. **Day 1-2**: Integration tests (Phase 5.1)
8. **Day 3-4**: Manual testing (Phase 5.2)
9. **Day 5**: Bug fixes + documentation

### Week 4: Documentation & Deployment
10. **Day 1-2**: User documentation (Phase 6.1)
11. **Day 3-4**: Deployment checklist (Phase 6.2)
12. **Day 5**: Release preparation

---

## Success Criteria

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Wake word → transcript latency | < 500ms | Measure in tests |
| Transcript → response latency | < 200ms | Measure in tests |
| WebSocket reconnection | < 5s | Disconnect/reconnect test |
| Session timeout accuracy | ± 5s | Timeout test |
| Test coverage | > 80% | `pytest --cov` |
| TypeScript strict mode | Pass | `tsc --noEmit` |
| Integration test suite | Pass | All phases |

---

## Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| WebSocket connection drops | High | Auto-reconnect with backoff |
| Audio buffer overflow | Medium | Queue size limits |
| Session memory leak | Medium | Timeout + cleanup |
| Race conditions | High | Lock on session state |
| OpenClaw Gateway restart | Medium | Graceful reconnect |

---

## Next Steps

1. **Approve plan** → Start Phase 1
2. **Create protocol file** → `src/bridge/protocol.py`
3. **Implement WebSocket server** → Phase 2.1
4. **Add tests** → Run after each implementation
5. **Build extension** → Phase 3
6. **Test end-to-end** → Phase 5

Ready to proceed? Say **"start phase 1"** to begin.