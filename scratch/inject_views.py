#!/usr/bin/env python3
"""
Inject all enterprise dashboard views into web/templates/index.html.
Inserts:
  - RBAC role selector in the header
  - Tab bar above the main-grid
  - Enterprise panels (dashboard, compliance, finops, security, enforcement, org, schema, cicd)
  - Wraps the existing legacy grid in its own tab panel
"""
import re

with open("web/templates/index.html", "r") as f:
    html = f.read()

# ─── 1. Header update: inject RBAC selector ───────────────────────────────────
old_header = '''    <header>
      <div class="brand">
        <h1>AWS Tagging Utils</h1>
        <p>Enterprise Governance, FinOps &amp; Security</p>
      </div>
      <div style="display: flex; gap: 1rem; align-items: center;">
        <label style="margin: 0; color: var(--muted);">Role:</label>
        <select id="user-role-select" style="width: auto; background: rgba(0,0,0,0.4); font-size: 0.85rem;" onchange="updateRBAC()">
            <option value="PlatformAdmin">Platform Admin</option>
            <option value="SecurityAdmin">Security Admin</option>
            <option value="FinOps">FinOps</option>
            <option value="ApplicationOwner">App Owner</option>
            <option value="Viewer">Viewer</option>
        </select>
      </div>
    </header>'''

# The existing header in the split file:
old_header_simple = '''    <header>
      <div class="brand">
        <h1>AWS Tagging Utils</h1>
        <p>Enterprise Governance, FinOps &amp; Security</p>
      </div>
    </header>'''

new_header = '''    <header>
      <div class="brand">
        <h1>AWS Tagging Utils</h1>
        <p>Enterprise Governance, FinOps &amp; Security</p>
      </div>
      <div style="display: flex; gap: 1rem; align-items: center;">
        <label style="margin: 0; color: var(--muted);">Role:</label>
        <select id="user-role-select" style="width: auto; background: rgba(0,0,0,0.4); font-size: 0.85rem;" onchange="updateRBAC()">
            <option value="PlatformAdmin">Platform Admin</option>
            <option value="SecurityAdmin">Security Admin</option>
            <option value="FinOps">FinOps</option>
            <option value="ApplicationOwner">App Owner</option>
            <option value="Viewer">Viewer</option>
        </select>
      </div>
    </header>'''

# Replace either variant
if old_header in html:
    html = html.replace(old_header, new_header)
elif old_header_simple in html:
    html = html.replace(old_header_simple, new_header)
else:
    # Try regex
    html = re.sub(r'<header>.*?</header>', new_header, html, flags=re.DOTALL)

# ─── 2. Inject tab bar + enterprise panels right before the main-grid ──────────
TAB_BAR = '''
    <!-- Enterprise Navigation -->
    <div class="tabs" style="margin-bottom: 2rem; flex-wrap: wrap;">
      <button class="tab-btn active" data-tab="dashboard" id="ent-tab-dashboard">📈 Dashboard</button>
      <button class="tab-btn" data-tab="compliance" id="ent-tab-compliance">✅ Compliance</button>
      <button class="tab-btn" data-tab="finops" id="ent-tab-finops">💰 FinOps</button>
      <button class="tab-btn" data-tab="security" id="ent-tab-security">🔒 Security</button>
      <button class="tab-btn" data-tab="enforcement" id="ent-tab-enforcement">⚖️ Enforcement</button>
      <button class="tab-btn" data-tab="organization" id="ent-tab-organization">🏢 Organization</button>
      <button class="tab-btn" data-tab="schema" id="ent-tab-schema">📋 Schema</button>
      <button class="tab-btn" data-tab="cicd" id="ent-tab-cicd">🚀 CI/CD</button>
      <button class="tab-btn" data-tab="legacy" id="ent-tab-legacy">🛠️ Tag Tools</button>
    </div>

    <!-- ─── ENTERPRISE VIEWS ──────────────────────────────────────────── -->

    <!-- DASHBOARD -->
    <div id="view-ent-dashboard" class="ent-view">
      <div class="section-title">📈 EXECUTIVE OVERVIEW</div>
      <div class="stats-bar" style="flex-wrap: wrap; margin-bottom: 2rem;">
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Overall Compliance</div>
          <div class="stat-value blue" id="dash-compliance">--%</div>
          <div class="stat-sub">across all accounts</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Total Monthly Spend</div>
          <div class="stat-value green" id="dash-spend">$--</div>
          <div class="stat-sub">AWS Cost Explorer</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Active Violations</div>
          <div class="stat-value red" id="dash-violations">--</div>
          <div class="stat-sub">requiring action</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Tag Allocation %</div>
          <div class="stat-value green" id="dash-allocation">--%</div>
          <div class="stat-sub">estimated attribution</div>
        </div></div>
      </div>
      <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1.5rem;">
        <div class="card">
          <div class="section-title">Resources</div>
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Total</span><strong id="dash-total-res">--</strong></div>
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Compliant</span><strong class="status-badge ok" id="dash-comp-res">--</strong></div>
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Non-Compliant</span><strong class="status-badge err" id="dash-noncomp-res">--</strong></div>
          </div>
        </div>
        <div class="card">
          <div class="section-title">Remediation</div>
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Pending</span><strong class="status-badge err" id="dash-pending">--</strong></div>
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Active Exemptions</span><strong class="status-badge info" id="dash-exemptions">--</strong></div>
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Protected Violations</span><strong class="status-badge err" id="dash-protected">--</strong></div>
          </div>
        </div>
        <div class="card">
          <div class="section-title">Security</div>
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Drift Events</span><strong class="status-badge err" id="dash-drift">--</strong></div>
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Auto-Reverted</span><strong class="status-badge ok" id="dash-reverted">--</strong></div>
            <div style="display: flex; justify-content: space-between;"><span style="color:var(--muted)">Pending Review</span><strong class="status-badge info" id="dash-pending-sec">--</strong></div>
          </div>
        </div>
      </div>
    </div>

    <!-- COMPLIANCE -->
    <div id="view-ent-compliance" class="ent-view" style="display:none;">
      <div class="section-title">✅ RESOURCE COMPLIANCE</div>
      <div style="display: flex; gap: 10px; margin-bottom: 1.25rem; flex-wrap: wrap;">
        <select id="comp-filter-status" style="width: auto; font-size: 0.85rem;" onchange="filterComplianceTable()">
          <option value="">All Statuses</option>
          <option value="COMPLIANT">Compliant</option>
          <option value="NON_COMPLIANT">Non-Compliant</option>
        </select>
        <input type="text" id="comp-filter-text" placeholder="Filter by resource ID or account..." style="flex: 1; font-size: 0.85rem;" oninput="filterComplianceTable()">
      </div>
      <div class="table-container">
        <table>
          <thead><tr>
            <th>Resource ID</th><th>Account</th><th>Type</th><th>Status</th>
            <th>Missing Tags</th><th>Remediation</th><th>Actions</th>
          </tr></thead>
          <tbody id="compliance-table-body"><tr><td colspan="7" class="empty-state" style="text-align:center; padding: 2rem; color:var(--muted);">Loading compliance data...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- FINOPS -->
    <div id="view-ent-finops" class="ent-view" style="display:none;">
      <div class="section-title">💰 FINOPS &amp; COST ATTRIBUTION</div>
      <div class="card" style="margin-bottom: 1.5rem; border-left: 3px solid var(--warning);">
        <p style="margin: 0; font-size: 0.88rem; color: var(--muted);">
          ⚠️ <strong>Attribution Transparency:</strong>
          <em>Actual AWS Tagged Spend</em> is reported directly from AWS Cost Explorer.
          <em>Potentially Unallocated Spend</em> is estimated — some AWS services don't provide resource-level cost attribution.
          Cost Explorer data may be up to 24 hours delayed.
        </p>
      </div>
      <div class="stats-bar" style="flex-wrap: wrap; margin-bottom: 2rem;">
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Total Spend</div>
          <div class="stat-value" id="finops-total">$--</div>
          <div class="stat-sub">current month</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">AWS-Reported Tagged</div>
          <div class="stat-value green" id="finops-tagged">$--</div>
          <div class="stat-sub">actual Cost Explorer data</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Untagged Spend</div>
          <div class="stat-value red" id="finops-untagged">$--</div>
          <div class="stat-sub">attribution gap</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Potentially Unallocated</div>
          <div class="stat-value yellow" id="finops-unallocated">$--</div>
          <div class="stat-sub">allocation estimate</div>
        </div></div>
      </div>
      <div class="card">
        <div class="section-title">Cost Allocation Tags (Activated)</div>
        <div id="finops-tags-list" style="display: flex; gap: 8px; flex-wrap: wrap;"></div>
      </div>
    </div>

    <!-- SECURITY -->
    <div id="view-ent-security" class="ent-view" style="display:none;">
      <div class="section-title">🔒 SECURITY &amp; PROTECTED-TAG DRIFT</div>
      <div class="stats-bar" style="flex-wrap: wrap; margin-bottom: 2rem;">
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Protected Violations</div>
          <div class="stat-value red" id="sec-protected">--</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Unauthorized Changes</div>
          <div class="stat-value red" id="sec-unauth">--</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Auto-Reverted</div>
          <div class="stat-value green" id="sec-reverted">--</div>
        </div></div>
        <div class="stat-card"><div class="stat-info">
          <div class="stat-label">Pending Review</div>
          <div class="stat-value yellow" id="sec-pending">--</div>
        </div></div>
      </div>
      <div class="section-title" style="margin-top: 1.5rem;">Recent Drift Events</div>
      <div class="table-container">
        <table>
          <thead><tr>
            <th>Resource</th><th>Tag</th><th>Expected</th><th>Actual</th>
            <th>Changed By</th><th>Changed At</th><th>Status</th><th>Actions</th>
          </tr></thead>
          <tbody id="drift-table-body"><tr><td colspan="8" style="text-align:center; padding: 2rem; color:var(--muted);">Loading drift events...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- ENFORCEMENT / REMEDIATION / AUDIT -->
    <div id="view-ent-enforcement" class="ent-view" style="display:none;">
      <div class="section-title">⚖️ REMEDIATION TASKS</div>
      <div class="table-container" style="margin-bottom: 2rem;">
        <table>
          <thead><tr><th>Resource</th><th>Account</th><th>Violation</th><th>Detected</th><th>Deadline</th><th>Status</th><th>Last Action</th></tr></thead>
          <tbody id="remediation-table-body"><tr><td colspan="7" style="text-align:center; padding: 1.5rem; color:var(--muted);">Loading...</td></tr></tbody>
        </table>
      </div>
      <div class="section-title">ACTIVE EXEMPTIONS</div>
      <div class="table-container" style="margin-bottom: 2rem;">
        <table>
          <thead><tr><th>Scope</th><th>Reason</th><th>Owner</th><th>Expiration</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody id="exemptions-table-body"><tr><td colspan="6" style="text-align:center; padding: 1.5rem; color:var(--muted);">Loading...</td></tr></tbody>
        </table>
      </div>
      <div class="section-title">AUDIT LOG</div>
      <div class="table-container">
        <table>
          <thead><tr><th>Timestamp</th><th>Action</th><th>Resource</th><th>Actor</th><th>Result</th><th>Reason</th></tr></thead>
          <tbody id="audit-table-body"><tr><td colspan="6" style="text-align:center; padding: 1.5rem; color:var(--muted);">Loading...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- ORGANIZATION -->
    <div id="view-ent-organization" class="ent-view" style="display:none;">
      <div class="section-title">🏢 MULTI-ACCOUNT ORGANIZATION</div>
      <div class="table-container">
        <table>
          <thead><tr><th>Account</th><th>OU</th><th>Compliance %</th><th>Resources</th><th>Violations</th><th>Monthly Spend</th></tr></thead>
          <tbody id="org-table-body"><tr><td colspan="6" style="text-align:center; padding: 2rem; color:var(--muted);">Loading...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- SCHEMA -->
    <div id="view-ent-schema" class="ent-view" style="display:none;">
      <div class="section-title">📋 GOVERNANCE SCHEMA (from tag-schema.yaml)</div>
      <p style="color: var(--muted); font-size: 0.88rem; margin-bottom: 1.5rem;">
        This schema is the single source of truth from the backend <code>SchemaProvider</code>. Changes require updating the YAML configuration.
      </p>
      <div class="table-container">
        <table>
          <thead><tr><th>Tag</th><th>Required</th><th>Protected</th><th>FinOps</th><th>Allowed Values</th><th>Regex</th><th>Description</th></tr></thead>
          <tbody id="schema-table-body"><tr><td colspan="7" style="text-align:center; padding: 2rem; color:var(--muted);">Loading schema...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- CI/CD -->
    <div id="view-ent-cicd" class="ent-view" style="display:none;">
      <div class="section-title">🚀 CI/CD PIPELINE VALIDATION (SARIF)</div>
      <p style="color: var(--muted); font-size: 0.88rem; margin-bottom: 1.5rem;">
        Validation results are generated by the CLI: <code>aws-tagging-utils validate terraform --format sarif</code>
      </p>
      <div class="table-container">
        <table>
          <thead><tr><th>Repository</th><th>Branch</th><th>Commit</th><th>Status</th><th>Violations</th><th>Actions</th></tr></thead>
          <tbody id="cicd-table-body"><tr><td colspan="6" style="text-align:center; padding: 2rem; color:var(--muted);">Loading CI/CD runs...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- LEGACY TAG TOOLS (existing sidebar+main grid) -->
    <div id="view-ent-legacy" class="ent-view" style="display:none;">
'''

LEGACY_CLOSE = '''    </div>
    <!-- ─── END ENTERPRISE VIEWS ──────────────────────────────────────────── -->
'''

# Find the main-grid div
main_grid_marker = '    <div class="main-grid">'
if main_grid_marker in html:
    html = html.replace(main_grid_marker, TAB_BAR + main_grid_marker)
else:
    print("WARNING: Could not find main-grid marker!")

# Close the legacy wrapper right before </div></div> at end of body
# Find the last </div> before </body>
body_close = '  </div>\n\n  <script src="/static/app.js"></script>'
if body_close in html:
    html = html.replace(body_close, '    </div>' + '\n' + LEGACY_CLOSE + '\n  </div>\n\n  <script src="/static/app.js"></script>')

with open("web/templates/index.html", "w") as f:
    f.write(html)

print(f"Done. index.html is now {len(html.splitlines())} lines.")
print("Views injected: dashboard, compliance, finops, security, enforcement, organization, schema, cicd, legacy")
