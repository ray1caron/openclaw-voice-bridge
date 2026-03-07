"""Voice-OpenClaw Bridge v2.

Bidirectional voice interface for OpenClaw.
"""

__version__ = "0.1.0"

from bridge.config import AppConfig, get_config, reload_config
from bridge.audio_discovery import AudioDiscovery, run_discovery, print_discovery_report
from bridge.response_filter import (
    ResponseFilter,
    ResponseFilterManager,
    ResponseType,
    FilterDecision,
    FilteredMessage,
)
from bridge.audio_buffer import AudioBuffer
from bridge.vad import (
    WebRTCVAD,
    VADConfig,
    VADMode,
    VADState,
    SpeechSegmenter,
    SpeechSegment,
    MockVAD,
)
from bridge.audio_pipeline import (
    AudioPipeline,
    AudioDeviceManager,
    AudioDeviceInfo,
    AudioDeviceType,
    PipelineState,
    PipelineStats,
)
from bridge.openclaw_middleware import (
    OpenClawMiddleware,
    MessageMetadata,
    TaggedMessage,
    MessageType,
    Speakability,
    mark_tool_call,
    wrap_tool_execution,
)
from bridge.middleware_integration import (
    MiddlewareResponseFilter,
    filter_message,
)
from bridge.tool_chain_manager import (
    ToolChainManager,
    ToolStep,
    ToolChainResult,
    ToolChainState,
    ToolResultStatus,
    execute_tool_chain,
    get_tool_chain_manager,
)
from bridge.bug_tracker import (
    BugTracker,
    BugSeverity,
    BugStatus,
    capture_bug,
    install_global_handler,
)
# Sprint 3: Conversation Persistence
from bridge.conversation_store import (
    ConversationStore,
    get_conversation_store,
    get_session_db_path,
)
from bridge.session_manager import (
    SessionManager,
    Session,
    SessionState,
    SessionError,
    get_session_manager,
)
from bridge.history_manager import (
    HistoryManager,
    ConversationTurn,
    ConversationSession,
    get_history_manager,
)
from bridge.context_window import (
    ContextWindow,
    ContextMessage,
    ContextWindowManager,
    get_context_manager,
)
from bridge.session_recovery import (
    SessionRecovery,
    RecoveryStatus,
    RecoveryResult,
    get_session_recovery,
)
from bridge.wake_word import (
    WakeWordDetector,
    WakeWordConfig,
)
from bridge.stt import (
    STTEngine,
    STTConfig,
    STTState,
)
from bridge.tts import (
    TTSEngine,
    TTSState,
    create_tts,
)
from bridge.orchestrator import (
    VoiceOrchestrator,
    OrchestratorState,
    OrchestratorStats,
)
from bridge.websocket_server import (
    VoiceBridgeWebSocketServer,
    ClientSession,
    ServerStats,
    ServerState,
)

__all__ = [
    "AppConfig",
    "get_config",
    "reload_config",
    "AudioDiscovery",
    "run_discovery",
    "print_discovery_report",
    "ResponseFilter",
    "ResponseFilterManager",
    "ResponseType",
    "FilterDecision",
    "FilteredMessage",
    "AudioBuffer",
    "WebRTCVAD",
    "VADConfig",
    "VADMode",
    "VADState",
    "SpeechSegmenter",
    "SpeechSegment",
    "MockVAD",
    "AudioPipeline",
    "AudioDeviceManager",
    "AudioDeviceInfo",
    "AudioDeviceType",
    "PipelineState",
    "PipelineStats",
    # OpenClaw Middleware
    "OpenClawMiddleware",
    "MessageMetadata",
    "TaggedMessage",
    "MessageType",
    "Speakability",
    "mark_tool_call",
    "wrap_tool_execution",
    # Middleware Integration
    "MiddlewareResponseFilter",
    "filter_message",
    # Tool Chain Manager
    "ToolChainManager",
    "ToolStep",
    "ToolChainResult",
    "ToolChainState",
    "ToolResultStatus",
    "execute_tool_chain",
    "get_tool_chain_manager",
    # Bug Tracker
    "BugTracker",
    "BugSeverity",
    "BugStatus",
    "capture_bug",
    "install_global_handler",
    # Conversation Persistence (Sprint 3)
    "ConversationStore",
    "get_conversation_store",
    "get_session_db_path",
    "SessionManager",
    "Session",
    "SessionState",
    "SessionError",
    "get_session_manager",
    "HistoryManager",
    "ConversationTurn",
    "ConversationSession",
    "get_history_manager",
    "ContextWindow",
    "ContextMessage",
    "ContextWindowManager",
    "get_context_manager",
    "SessionRecovery",
    "RecoveryStatus",
    "RecoveryResult",
    "get_session_recovery",
    # Voice Bridge v4 Components
    "WakeWordDetector",
    "WakeWordConfig",
    "STTEngine",
    "STTConfig",
    "STTState",
    "TTSEngine",
    "TTSState",
    "create_tts",
    "VoiceOrchestrator",
    "OrchestratorState",
    "OrchestratorStats",
    # WebSocket Server
    "VoiceBridgeWebSocketServer",
    "ClientSession",
    "ServerStats",
    "ServerState",
]