# Contributing to Voice-OpenClaw Bridge v2

Thank you for your interest in contributing! This document provides guidelines for development.

## Development Setup

```bash
# 1. Clone repository
git clone https://github.com/ray1caron/voice-openclaw-bridge-v2.git
cd voice-openclaw-bridge-v2

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install in development mode
pip install -e ".[dev]"

# 4. Install pre-commit hooks
pre-commit install
```

## Project Structure

```
src/
├── bridge/      # Core WebSocket client and orchestration
├── audio/       # Audio I/O (not yet implemented)
├── stt/         # Speech-to-text (not yet implemented)
├── tts/         # Text-to-speech (not yet implemented)
└── wake/        # Wake word detection (not yet implemented)

tests/           # Test files
docs/            # Documentation
```

## Development Workflow

### 1. Pick an Issue
- Check the GitHub Projects board
- Comment on the issue to claim it
- Move to "In Progress" column

### 2. Create a Branch
```bash
git checkout -b feature/issue-number-short-description
# Examples:
# git checkout -b feature/1-websocket-client
# git checkout -b fix/3-response-filter-bug
```

### 3. Write Code
- Follow PEP 8 style (enforced by ruff)
- Add type hints (enforced by mypy)
- Add tests for new functionality

### 4. Test Locally
```bash
# Run tests
pytest tests/ -v

# Run linting
ruff check src/
ruff format src/

# Type checking
mypy src/

# Check all
./scripts/check.sh
```

### 5. Commit
```bash
# Format: type(scope): description
git commit -m "feat(bridge): implement WebSocket reconnection"

git commit -m "fix(filter): correct heuristic for tool calls"

git commit -m "docs(readme): update installation instructions"
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `test`: Tests
- `refactor`: Code refactoring
- `chore`: Maintenance

### 6. Push and Create PR
```bash
git push origin feature/your-branch-name
```

Create a Pull Request on GitHub:
- Link to the issue: `Closes #123`
- Describe what changed and why
- Request review from maintainers

### 7. Code Review
- Address feedback
- Push updates
- Once approved, squash and merge

---

## Code Standards

### Python Style
- Use type hints for all function signatures
- Docstrings for modules, classes, and functions
- Maximum line length: 100 characters
- Use f-strings for string formatting

Example:
```python
from typing import Optional

def send_message(
    text: str,
    priority: int = 0,
    timeout: Optional[float] = None,
) -> bool:
    """
    Send a message to the WebSocket server.
    
    Args:
        text: Message content to send
        priority: Message priority (0=normal, 1=high)
        timeout: Maximum time to wait for confirmation
        
    Returns:
        True if message was sent successfully
    """
    # Implementation
```

### Testing
- All new code should have tests
- Aim for >80% coverage
- Use pytest fixtures for shared setup
- Mock external dependencies

Example:
```python
import pytest
from src.bridge.response_filter import ResponseFilter

def test_filter_silences_thinking():
    filter = ResponseFilter()
    result = filter.classify({"content": "Let me check that..."})
    
    assert result.message_type == "thinking"
    assert result.should_speak is False
```

---

## Architecture Decisions

### Why These Choices?

**WebSocket over HTTP Polling:**
- Lower latency for conversations
- Server push for responses
- Better connection state awareness

**Async Everything:**
- Audio I/O is naturally async
- Prevents blocking on network
- Clean cancellation handling

**Explicit over Implicit:**
- Prefer message type markers over heuristics
- Clearer intent, easier to debug
- But heuristics as fallback

### Performance Guidelines

**Audio Path:**
- Keep audio callbacks <10ms
- Use ring buffers, not dynamic allocation
- Pre-allocate where possible

**Network:**
- Batch small messages if possible
- Use binary protocols where appropriate
- Handle backpressure gracefully

**Memory:**
- Don't hold large audio buffers
- Stream TTS output, don't buffer entire response
- Use context managers for resources

---

## Debugging

### Enable Debug Logging
```yaml
# config/user.yaml
logging:
  level: "DEBUG"
  
debug:
  save_audio: true
  verbose_filter: true
```

### Common Issues

**WebSocket Connection Fails:**
```bash
# Check OpenClaw is running
curl http://localhost:3000/health

# Test WebSocket directly
wscat -c ws://localhost:3000/api/voice
```

**Audio Device Not Found:**
```bash
# List devices
python -c "import sounddevice; print(sounddevice.query_devices())"

# Test capture
python scripts/test-audio.py
```

**TTS Not Speaking:**
- Check `piper_binary` path in config
- Verify voice model files exist
- Test Piper directly: `./piper/piper --help`

---

## Questions?

- Open an issue for bugs
- Start a discussion for questions
- Join our Discord (if applicable)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
