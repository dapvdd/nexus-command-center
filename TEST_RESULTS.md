# Test Results — NEXUS Command Center

**Executed:** `python -m pytest tests/ -v`
**Result: 51 passed, 0 failed**
**Platform:** Windows (Python 3.13.7, pytest-8.4.2)

---

## Store Tests (35 passed)

### TestSchemaInit
| Test | Result |
|---|---|
| `test_wal_mode` | ✅ PASS |
| `test_health_check` | ✅ PASS |

### TestTaskCreation
| Test | Result |
|---|---|
| `test_create_minimal_task` | ✅ PASS |
| `test_create_full_task` | ✅ PASS |
| `test_create_idempotency` | ✅ PASS |
| `test_idempotency_key_uniqueness_per_parent` | ✅ PASS |

### TestValidation
| Test | Result |
|---|---|
| `test_title_required` | ✅ PASS |
| `test_title_too_long` | ✅ PASS |
| `test_priority_out_of_range` | ✅ PASS |
| `test_timeout_out_of_range` | ✅ PASS |

### TestTaskRetrieval
| Test | Result |
|---|---|
| `test_get_task` | ✅ PASS |
| `test_get_task_not_found` | ✅ PASS |
| `test_list_tasks` | ✅ PASS |
| `test_list_tasks_with_status_filter` | ✅ PASS |
| `test_list_tasks_pagination` | ✅ PASS |
| `test_count_tasks` | ✅ PASS |

### TestStateTransitions
| Test | Result |
|---|---|
| `test_claim_and_finish` | ✅ PASS |
| `test_claim_wrong_token` | ✅ PASS |
| `test_finish_unclaimed_task` | ✅ PASS |
| `test_heartbeat` | ✅ PASS |
| `test_cancel` | ✅ PASS |
| `test_cancel_terminal_task` | ✅ PASS |
| `test_request_cancel_running` | ✅ PASS |
| `test_retry_failed` | ✅ PASS |

### TestApproval
| Test | Result |
|---|---|
| `test_approve_waiting_task` | ✅ PASS |
| `test_approve_non_waiting_task` | ✅ PASS |
| `test_reject_task` | ✅ PASS |

### TestRestartRecovery
| Test | Result |
|---|---|
| `test_store_reopens_on_restart` | ✅ PASS |
| `test_claimed_task_persists_across_restart` | ✅ PASS |

### TestAuditLog
| Test | Result |
|---|---|
| `test_audit_on_create` | ✅ PASS |
| `test_audit_on_cancel` | ✅ PASS |

### TestAgents
| Test | Result |
|---|---|
| `test_register_and_list_agent` | ✅ PASS |
| `test_get_agent` | ✅ PASS |

### TestUsage
| Test | Result |
|---|---|
| `test_record_and_summary` | ✅ PASS |

---

## API Tests (16 passed)

### TestHealthEndpoint
| Test | Result |
|---|---|
| `test_health` | ✅ PASS |

### TestTaskCreationAPI
| Test | Result |
|---|---|
| `test_create_task` | ✅ PASS |
| `test_create_task_validation` | ✅ PASS |
| `test_idempotency` | ✅ PASS |
| `test_deduplicated_flag` | ✅ PASS |

### TestTaskRetrievalAPI
| Test | Result |
|---|---|
| `test_list_tasks_empty` | ✅ PASS |
| `test_list_tasks` | ✅ PASS |
| `test_list_tasks_with_status_filter` | ✅ PASS |
| `test_get_task` | ✅ PASS |
| `test_get_task_not_found` | ✅ PASS |

### TestTaskCancelAPI
| Test | Result |
|---|---|
| `test_cancel_queued_task` | ✅ PASS |

### TestTaskRetryAPI
| Test | Result |
|---|---|
| `test_retry_failed_task` | ✅ PASS |

### TestDashboardRoot
| Test | Result |
|---|---|
| `test_root_returns_html` | ✅ PASS |
| `test_static_file_not_found` | ✅ PASS |

### TestAgentAPI
| Test | Result |
|---|---|
| `test_register_agent` | ✅ PASS |
| `test_list_agents` | ✅ PASS |

### TestAuditAPI
| Test | Result |
|---|---|
| `test_list_audit` | ✅ PASS |

---

## Start & Test Commands

```bash
# Start the server
python main.py

# Run all tests
python -m pytest tests/ -v

# Run only store tests
python -m pytest tests/test_store.py -v

# Run only API tests
python -m pytest tests/test_api.py -v
```
