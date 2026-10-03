The application is currently becoming unresponsive/hanging after the recent Compliance Audit UI changes.

STOP adding new features for now.

Focus exclusively on fixing the application's responsiveness, data loading architecture, persistence, and user experience.

## Objective

The application must behave like a fast dashboard:

```text
First visit
    ↓
Load persisted/latest results immediately
    ↓
Render UI
    ↓
Background refresh from AWS
    ↓
Persist new results
    ↓
Update UI

Page refresh
    ↓
Immediately load persisted results
    ↓
Render UI
    ↓
Refresh in background
```

The user should NEVER have to wait for a long AWS compliance/resource scan just to see the page.

A page refresh must not trigger a blocking full AWS scan before rendering the existing results.

---

# 1. First diagnose the hanging problem

Before changing architecture, inspect the recent Compliance Audit UI changes and determine exactly why the application becomes slow/unresponsive.

Investigate:

- `web/app.py`
- `web/static/app.js`
- Compliance Audit related JS/HTML
- API endpoints involved
- `generate_report()`
- AWS API calls
- Resource discovery
- Compliance calculation
- Any loops over AWS resources
- Any sequential AWS API calls
- Any frontend polling
- Any `Promise` chains
- Any repeated API calls
- Any `setInterval` / recursive refresh
- Any page-load event triggering multiple requests
- Any synchronous/blocking operations
- Any frontend rendering of very large datasets
- Any API request that is executed multiple times

Use browser Network/Console behavior and application logs where possible.

Do not guess.

Identify the actual bottleneck.

---

# 2. Critical requirement: page load must NOT perform a full AWS scan

Do not make this pattern:

```text
GET /compliance
    ↓
scan AWS
    ↓
discover resources
    ↓
evaluate every resource
    ↓
return response
```

if that operation can take significant time.

Instead use:

```text
GET /compliance
    ↓
return latest persisted result immediately
```

and separately:

```text
POST /compliance/refresh
    ↓
perform AWS scan
    ↓
persist result
    ↓
return/update status
```

The UI should never block on the expensive operation.

---

# 3. Persist Compliance Audit results

I want Compliance Audit results to persist when I:

- Navigate away from the page
- Return to the page
- Refresh the browser
- Restart the Flask application

Do NOT use only JavaScript memory/state for persistence.

Use an appropriate persistent mechanism based on what already exists in the application.

First inspect whether the project already has a suitable persistence layer.

Possible approaches may include:

- Existing DynamoDB integration
- Local SQLite
- Local JSON/file-based cache
- Another existing application storage mechanism

For the LOCAL application mode, choose the simplest reliable persistence mechanism.

Do not introduce DynamoDB merely because it is an AWS application.

The local application should remain easy to run.

---

# 4. Persist the actual result, not just UI state

Persist enough information to reconstruct the Compliance Audit page.

For example:

```json
{
  "generated_at": "...",
  "region": "...",
  "resource_count": 19,
  "compliant_count": 0,
  "non_compliant_count": 19,
  "compliance_percentage": 0,
  "resources": [...],
  "summary": {...}
}
```

Use the application's actual data model rather than inventing a parallel representation.

Do not persist AWS credentials or secrets.

---

# 5. Add result metadata

The UI should clearly show:

```text
Last updated: 2 minutes ago
Source: AWS
Status: Fresh
```

or:

```text
Last updated: 2 hours ago
Source: Cached result
Status: Refreshing...
```

If the AWS refresh fails:

```text
Last successful update: 2 hours ago
Status: Refresh failed
Showing cached results
```

The user should still be able to use the existing results.

Never replace valid cached data with:

```text
0 resources
0% compliance
```

just because the latest AWS request failed.

---

# 6. Background refresh

Implement a non-blocking refresh model.

Preferred behavior:

### Initial page visit

```text
1. Request cached results
2. Render immediately
3. Trigger background refresh
4. Show "Refreshing..." indicator
5. When refresh completes:
   - persist result
   - update UI
   - update timestamp
```

### Subsequent page visit

```text
1. Load cached result immediately
2. Render immediately
3. Background refresh only if appropriate
```

Do not automatically launch multiple concurrent refresh operations.

There must be only ONE active compliance refresh at a time.

---

# 7. Prevent duplicate requests

Audit the frontend carefully.

Make sure a single page load does NOT result in something like:

```text
/api/dashboard
/api/compliance
/api/compliance
/api/compliance
/api/audit
/api/compliance/refresh
/api/compliance/refresh
```

unless each request is genuinely required.

Add request deduplication where appropriate.

If two UI components need the same compliance data, fetch it once and share the result.

---

# 8. Add refresh locking

The backend must protect against multiple expensive scans running simultaneously.

For example:

```text
Refresh requested
    ↓
Is refresh already running?
    ├── YES → return "already running"
    └── NO  → start refresh
```

Do not allow every browser refresh or UI component to start another AWS scan.

---

# 9. Add stale-while-revalidate behavior

Use a simple cache policy.

For example:

```text
Cached result < 5 minutes old
    → immediately show cached result
    → optionally refresh in background

Cached result > 5 minutes old
    → immediately show cached result
    → definitely refresh in background

No cached result
    → show loading state
    → perform initial refresh
```

Do not hardcode the exact TTL if the application already has a configuration pattern. Make the value configurable.

Example:

```text
COMPLIANCE_CACHE_TTL_SECONDS=300
```

---

# 10. Never block the Flask request unnecessarily

Inspect `web/app.py`.

Expensive operations such as:

- AWS resource discovery
- compliance evaluation
- Cost Explorer queries
- large report generation

should not run synchronously inside normal page-data requests if they can take significant time.

A request like:

```text
GET /api/compliance
```

should be fast.

Target:

```text
cached response → milliseconds
```

rather than:

```text
AWS scan → potentially many seconds
```

---

# 11. Compliance Audit UI

The Compliance Audit page should have clear states:

### Loading

```text
Loading saved results...
```

### Cached

```text
Showing results from 2 minutes ago
```

### Refreshing

```text
Refreshing from AWS...
```

### Refresh successful

```text
Updated just now
```

### Refresh failed

```text
Unable to refresh from AWS.
Showing last successful results.
[Retry]
```

### No data

```text
No Compliance Audit results available yet.
[Run Audit]
```

Do not leave the user staring at a blank page/spinner indefinitely.

---

# 12. Add explicit refresh

Provide a:

```text
Refresh
```

button.

When clicked:

```text
Refresh
  ↓
Disable button
  ↓
Show "Refreshing..."
  ↓
Run one refresh
  ↓
Persist result
  ↓
Update UI
  ↓
Enable button
```

Prevent double-clicks from creating multiple scans.

---

# 13. Large result sets

The Compliance Audit page may eventually contain many AWS resources.

Do NOT render thousands of rows into the DOM at once.

Inspect the current implementation.

If necessary implement:

- pagination
- client-side pagination
- server-side pagination
- virtualized rendering

Use the simplest approach appropriate for the current application.

The summary should load quickly even if the detailed resource list is large.

---

# 14. AWS API performance

Inspect the compliance report implementation.

Look for:

- Sequential API calls
- Repeated calls for the same resource
- Repeated `Describe*` calls
- Repeated tag lookups
- Duplicate resource discovery
- Unnecessary multi-region scans
- Large unbounded API requests

Reuse data already fetched during the report generation.

Do not make one AWS request per UI row if the backend can obtain the information in one operation.

If pagination is required by an AWS API, implement it correctly.

---

# 15. Separate summary from detailed data

The UI should not need to download the entire compliance dataset simply to display:

```text
Compliance: 72%
Resources: 1,248
Non-compliant: 349
```

Prefer an architecture such as:

```text
/api/compliance/summary
/api/compliance/resources?page=1
```

if that is beneficial for the current application.

However, do not unnecessarily split APIs if the current dataset is small.

Base the decision on actual performance/data size.

---

# 16. Persist across Flask restarts

Verify this exact scenario:

```text
Start application
↓
Run compliance audit
↓
Results appear
↓
Stop Flask
↓
Start Flask again
↓
Open Compliance Audit
↓
Previous results still appear immediately
```

This is a mandatory requirement.

---

# 17. Handle AWS failures correctly

If AWS fails during background refresh:

DO:

```text
Existing cached result
+
Refresh failed notification
```

DO NOT:

```text
AWS failure
↓
empty response
↓
0 resources
↓
0% compliance
```

The last known good result must remain available.

---

# 18. Tests

Add tests for:

### Persistence

- Save result
- Load result
- Application restart simulation
- Corrupt cache handling

### Cache

- Fresh cache
- Stale cache
- No cache

### Refresh

- Successful refresh
- Failed refresh
- Concurrent refresh prevention

### API

- Cached response is fast
- Refresh endpoint starts refresh
- AWS error does not destroy cached result

### Frontend

- Cached results render
- Refresh state renders
- Error state renders
- Retry works
- Duplicate requests are prevented

Run the complete existing test suite after changes.

---

# 19. Performance validation

Measure before and after.

At minimum capture:

```text
Current page-load/API response time
New cached page-load/API response time
AWS refresh duration
Number of AWS API calls
Number of browser API requests
```

The cached page should return significantly faster than performing a fresh AWS scan.

Do not claim performance improvements without measuring them.

---

# 20. Important architectural rule

The application should follow:

```text
                 ┌────────────────────┐
                 │   Compliance UI     │
                 └─────────┬──────────┘
                           │
                    Fast cached GET
                           │
                           ▼
                 ┌────────────────────┐
                 │ Persisted Results  │
                 └─────────┬──────────┘
                           │
                     Render instantly
                           │
                           ▼
                 Background refresh
                           │
                           ▼
                 ┌────────────────────┐
                 │ AWS Compliance     │
                 │ Report Generation  │
                 └─────────┬──────────┘
                           │
                           ▼
                 ┌────────────────────┐
                 │ Persist new result │
                 └─────────┬──────────┘
                           │
                           ▼
                     Update UI
```

The key principle is:

**AWS scanning is a background data-refresh operation, not a page-render operation.**

---

# Final validation

After implementing:

1. Start Flask.
2. Open Compliance Audit.
3. Verify initial behavior.
4. Run a refresh.
5. Navigate away.
6. Return.
7. Refresh browser.
8. Restart Flask.
9. Open Compliance Audit again.
10. Verify results appear immediately from persistence.
11. Trigger refresh.
12. Simulate AWS failure.
13. Verify old results remain visible.
14. Trigger multiple refreshes rapidly.
15. Verify only one refresh occurs.
16. Test with a larger result set if possible.
17. Check browser Network tab.
18. Check browser console.
19. Run all tests.

Do not consider this complete until the application remains responsive during AWS refresh operations.

## Final report

Provide:

- Root cause of the hanging
- Files changed
- Persistence mechanism chosen and why
- Cache/refresh architecture
- API changes
- Frontend changes
- AWS performance improvements
- Before/after timings
- Tests added
- Test results
- Remaining issues

Most importantly, confirm:

**"Can I refresh/navigate to the Compliance Audit page and immediately see my previous results without waiting for AWS?"**