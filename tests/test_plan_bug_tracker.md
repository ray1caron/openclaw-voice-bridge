# Bug Tracker Test Plan
## GitHub Integration Testing

**Date:** 2026-02-25  
**Status:** Draft  
**Author:** Hal (OpenClaw Agent)

---

## Objective

Validate that the bug tracking system correctly:
1. Captures errors from the voice bridge
2. Formats bug reports for GitHub
3. Creates issues via GitHub API
4. Handles failures gracefully
5. Links local bugs to GitHub issues

---

## Test Environment Requirements

### Prerequisites
- GitHub token with `repo` scope: `~/.github_token`
- Test repository access: `ray1caron/voice-openclaw-bridge-v2`
- Internet connectivity for GitHub API
- Python dependencies: `requests`, `pytest`

### Configuration
```bash
export GITHUB_TOKEN="ghp_your_test_token"
export BUG_TRACKER_TEST_REPO="ray1caron/voice-openclaw-bridge-v2"
export BUG_TRACKER_AUTO_CREATE="true"  # Enable auto-creation for testing
```

---

## Test Cases

### 1. Unit Tests (tests/unit/test_bug_tracker.py)

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| BT-001 | Create bug report from exception | Bug stored in SQLite with stack trace | P0 |
| BT-002 | Capture system snapshot | System state includes: Python version, platform, memory, audio devices | P0 |
| BT-003 | Format bug for GitHub | JSON structure with title, body, labels | P0 |
| BT-004 | Severity to label mapping | CRITICAL → "critical", HIGH → "high", etc. | P1 |
| BT-005 | Component to label mapping | "websocket" → "component:websocket" | P1 |

### 2. Integration Tests (tests/integration/test_bug_tracker_github.py)

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| BT-I001 | Create GitHub issue with valid token | Issue # returned, API call successful | P0 |
| BT-I002 | Handle invalid token | Graceful failure, no exception | P0 |
| BT-I003 | Handle network failure | Issue creation fails, local bug saved | P0 |
| BT-I004 | Rate limit backoff | Retry after 429 response | P1 |
| BT-I005 | Auto-create on critical bug | Critical severity auto-triggers GitHub issue | P1 |
| BT-I006 | Update bug with GitHub issue number | DB record updated with issue # | P0 |
| BT-I007 | No token = no API call | Skip GitHub, only local storage | P0 |

### 3. E2E Scenario Tests

| Scenario | Steps | Expected Result |
|----------|-------|-----------------|
| **SC-001: WebSocket Error Flow** | 1. Simulate WebSocket disconnect<br>2. Bug tracker captures error<br>3. Auto-create GitHub issue | GitHub issue # linked to local bug |
| **SC-002: Audio Pipeline Crash** | 1. Simulate audio processing crash<br>2. Capture system state<br>3. Create GitHub issue with stack trace | Issue includes full system state |
| **SC-003: Network Degradation** | 1. Block GitHub API<br>2. Create bug<br>3. Restore connectivity<br>4. Manual retry | Bug preserved, can create issue later |
| **SC-004: Batch Bug Export** | 1. Create 10 bugs<br>2. Export to GitHub<br>3. Verify rate limiting | All issues created, no rate limit errors |

---

## Test Execution Steps

### Step 1: Verify Basic Bug Capture
```bash
cd /home/hal/.openclaw/workspace/voice-bridge-v2
python3 -c "
from bridge.bug_tracker import BugTracker, BugSeverity

tracker = BugTracker.get_instance()
try:
    raise ValueError('Test error')
except Exception as e:
    bug_id = tracker.capture_exception(
        e, 
        severity=BugSeverity.MEDIUM,
        component='test',
        title='Test bug capture'
    )
    print(f'Bug #{bug_id} captured')
"
```

### Step 2: Test GitHub API Connection
```bash
python3 -c "
import requests
token = open('/home/hal/.github_token').read().strip()
headers = {'Authorization': f'token {token}'}
response = requests.get(
    'https://api.github.com/repos/ray1caron/voice-openclaw-bridge-v2/issues',
    headers=headers
)
print(f'Status: {response.status_code}')
print(f'Rate limit: {response.headers.get(\"X-RateLimit-Remaining\")}')
"
```

### Step 3: Run Integration Tests
```bash
python3 -m pytest tests/integration/test_bug_tracker_github.py -v
```

### Step 4: Verify E2E Flow
```bash
python3 tests/integration/test_e2e_bug_workflow.py
```

---

## Manual Testing Checklist

- [ ] Create a real test issue on GitHub
- [ ] Verify labels are applied correctly
- [ ] Check issue body formatting
- [ ] Test CLI export to markdown
- [ ] Verify issue linking in bug database
- [ ] Test with missing GITHUB_TOKEN
- [ ] Test with invalid token
- [ ] Verify rate limit handling

---

## Success Criteria

| Criteria | Metric | Target |
|----------|--------|--------|
| Bug Capture Rate | % of errors captured | 100% |
| GitHub Issue Creation | Success rate with valid token | >95% |
| API Failure Handling | Graceful degradation | 100% |
| Local Storage Reliability | Data preserved on API failure | 100% |
| Test Pass Rate | Unit + Integration tests | >90% |

---

## Known Limitations

1. **Token Security**: Token must be stored securely, not in repo
2. **Rate Limiting**: GitHub has 5000 req/hour limit
3. **Network Dependency**: API calls require internet
4. **Privacy**: System state may include sensitive info — review before auto-creating

---

## Future Enhancements

- [ ] Bug deduplication (check for existing similar issues)
- [ ] Automatic issue closure when bug is marked fixed
- [ ] Comments on existing issues for recurring bugs
- [ ] Integration with GitHub Projects for triage workflow

---

## Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Test Author | Hal | 2026-02-25 | Draft |
| Reviewer | | | Pending |
| Approved | | | Pending |
