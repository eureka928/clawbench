# Incident History

## INC-2025-0089 — November 15, 2025

**Duration:** 4 hours (1am-5am)
**Severity:** P1
**Root Cause:** Kubernetes auto-scaler increased pod count from 6 to 12 during a traffic spike. DB connection pool limit was 100. With 12 pods × 10 connections = 120, exceeding the pool limit. Connections were refused, causing cascading 503 errors.

**Resolution:** Manually scaled back pods and restarted services.

**Action Items (from postmortem):**
1. [INCOMPLETE] Create an automated connection pool scaling policy that adjusts DB limits when pod count changes — assigned to Tom Anderson, never prioritized
2. [COMPLETE] Add pod count to the monitoring dashboard — completed Dec 2025
3. [INCOMPLETE] Add a deploy checklist item for DB connection math verification — assigned to James Liu, deprioritized in Sprint 12
4. [INCOMPLETE] Set up connection pool utilization alerting at 80% threshold — assigned to Tom Anderson, not started

**Notes:** 3 of 4 action items were never completed. The same root cause pattern (auto-scaling without pool adjustment) could recur.

## INC-2025-0072 — September 8, 2025

**Duration:** 45 minutes
**Severity:** P2
**Root Cause:** Redis cache timeout misconfiguration after infrastructure migration. Unrelated to connection pool issues.
**Resolution:** Updated Redis timeout configuration. All action items completed.
