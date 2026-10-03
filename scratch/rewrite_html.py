import re
import os

with open("web/templates/index.html", "r") as f:
    content = f.read()

# Add a User Role Dropdown for RBAC in the header
header_replace = """<header>
      <div class="brand">
        <h1>AWS Tagging Utils</h1>
        <p>Enterprise Governance, FinOps & Security</p>
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
    </header>"""
content = re.sub(r'<header>.*?</header>', header_replace, content, flags=re.DOTALL)


# Update tabs
tabs_replace = """<div class="tabs">
        <button class="tab-btn active" data-tab="dashboard">📈 Dashboard</button>
        <button class="tab-btn" data-tab="compliance">✅ Compliance</button>
        <button class="tab-btn" data-tab="finops">💰 FinOps</button>
        <button class="tab-btn" data-tab="security">🔒 Security</button>
        <button class="tab-btn" data-tab="enforcement">⚖️ Enforcement</button>
        <button class="tab-btn" data-tab="organization">🏢 Organization</button>
        <button class="tab-btn" data-tab="schema">📋 Schema</button>
        
        <!-- Legacy Tools -->
        <button class="tab-btn" data-tab="read">🔍 Read</button>
        <button class="tab-btn" data-tab="write">✍️ Write</button>
        <button class="tab-btn" data-tab="gov">🛡️ Validate</button>
      </div>"""
content = re.sub(r'<div class="tabs">.*?</div>', tabs_replace, content, flags=re.DOTALL)


new_views = """
<!-- DASHBOARD VIEW -->
<div id="view-dashboard" class="view-section">
    <div class="section-title">EXECUTIVE OVERVIEW</div>
    <div class="stats-bar">
        <div class="stat-card">
            <div>
                <div class="stat-label">Overall Compliance</div>
                <div class="stat-value blue" id="dash-compliance">--%</div>
            </div>
        </div>
        <div class="stat-card">
            <div>
                <div class="stat-label">Total Spend</div>
                <div class="stat-value green" id="dash-spend">$ --</div>
            </div>
        </div>
        <div class="stat-card">
            <div>
                <div class="stat-label">Active Violations</div>
                <div class="stat-value red" id="dash-violations">--</div>
            </div>
        </div>
        <div class="stat-card">
            <div>
                <div class="stat-label">Tag Allocation</div>
                <div class="stat-value green" id="dash-allocation">--%</div>
            </div>
        </div>
    </div>
    
    <div class="main-grid">
        <div class="card">
            <div class="section-title">Security & Enforcement</div>
            <p>Protected Violations: <strong class="red" id="dash-protected">--</strong></p>
            <p>Pending Remediation: <strong class="red" id="dash-pending">--</strong></p>
            <p>Active Exemptions: <strong class="blue" id="dash-exemptions">--</strong></p>
        </div>
        <div class="card">
            <div class="section-title">Resources</div>
            <p>Total Resources: <strong id="dash-total-res">--</strong></p>
            <p>Compliant: <strong class="green" id="dash-comp-res">--</strong></p>
            <p>Non-Compliant: <strong class="red" id="dash-noncomp-res">--</strong></p>
        </div>
    </div>
</div>

<!-- COMPLIANCE VIEW -->
<div id="view-compliance" class="view-section" style="display: none;">
    <div class="section-title">RESOURCE COMPLIANCE</div>
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>Resource ID</th>
                    <th>Account</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Missing Tags</th>
                    <th>Remediation</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="compliance-table-body">
                <tr><td colspan="7" class="empty-state">Loading...</td></tr>
            </tbody>
        </table>
    </div>
</div>

<!-- FINOPS VIEW -->
<div id="view-finops" class="view-section" style="display: none;">
    <div class="section-title">FINOPS & COST ATTRIBUTION</div>
    
    <div class="stats-bar">
        <div class="stat-card">
            <div><div class="stat-label">Total Spend</div><div class="stat-value" id="finops-total">$ --</div></div>
        </div>
        <div class="stat-card">
            <div><div class="stat-label">Tagged Spend</div><div class="stat-value green" id="finops-tagged">$ --</div></div>
        </div>
        <div class="stat-card">
            <div><div class="stat-label">Unallocated (Estimate)</div><div class="stat-value red" id="finops-unallocated">$ --</div></div>
        </div>
    </div>
    
    <div class="card">
        <p style="color: var(--muted); font-size: 0.9rem;">
            * <strong>Potentially Unallocated Spend:</strong> Some AWS services do not provide resource-level cost attribution. 
            This value should be treated as an allocation estimate. Cost Explorer data is up to 24 hours delayed.
        </p>
    </div>
</div>

<!-- SECURITY VIEW -->
<div id="view-security" class="view-section" style="display: none;">
    <div class="section-title">SECURITY & DRIFT</div>
    
    <div class="stats-bar">
        <div class="stat-card"><div><div class="stat-label">Protected Tag Violations</div><div class="stat-value red" id="sec-protected">--</div></div></div>
        <div class="stat-card"><div><div class="stat-label">Unauthorized Changes</div><div class="stat-value red" id="sec-unauth">--</div></div></div>
        <div class="stat-card"><div><div class="stat-label">Reverted Automatically</div><div class="stat-value green" id="sec-reverted">--</div></div></div>
    </div>
    
    <div class="section-title" style="margin-top: 2rem;">Recent Drift Events</div>
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>Resource</th>
                    <th>Tag</th>
                    <th>Expected</th>
                    <th>Actual</th>
                    <th>Changed By</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="drift-table-body">
                <tr><td colspan="7" class="empty-state">Loading...</td></tr>
            </tbody>
        </table>
    </div>
</div>

<!-- ENFORCEMENT VIEW -->
<div id="view-enforcement" class="view-section" style="display: none;">
    <div class="section-title">REMEDIATION TASKS</div>
    <div class="table-container" style="margin-bottom: 2rem;">
        <table>
            <thead><tr><th>Resource</th><th>Account</th><th>Violation</th><th>Deadline</th><th>Status</th><th>Last Action</th></tr></thead>
            <tbody id="remediation-table-body"></tbody>
        </table>
    </div>
    
    <div class="section-title">ACTIVE EXEMPTIONS</div>
    <div class="table-container" style="margin-bottom: 2rem;">
        <table>
            <thead><tr><th>Scope</th><th>Reason</th><th>Owner</th><th>Expiration</th><th>Status</th></tr></thead>
            <tbody id="exemptions-table-body"></tbody>
        </table>
    </div>
    
    <div class="section-title">AUDIT LOG</div>
    <div class="table-container">
        <table>
            <thead><tr><th>Timestamp</th><th>Action</th><th>Resource</th><th>Actor</th><th>Result</th><th>Reason</th></tr></thead>
            <tbody id="audit-table-body"></tbody>
        </table>
    </div>
</div>

<!-- ORGANIZATION VIEW -->
<div id="view-organization" class="view-section" style="display: none;">
    <div class="section-title">MULTI-ACCOUNT COMPLIANCE</div>
    <div class="table-container">
        <table>
            <thead><tr><th>Account</th><th>OU</th><th>Compliance %</th><th>Resources</th><th>Violations</th><th>Spend</th></tr></thead>
            <tbody id="org-table-body"></tbody>
        </table>
    </div>
</div>

<!-- SCHEMA VIEW -->
<div id="view-schema" class="view-section" style="display: none;">
    <div class="section-title">GOVERNANCE SCHEMA</div>
    <div class="table-container">
        <table>
            <thead><tr><th>Tag</th><th>Required</th><th>Protected</th><th>FinOps</th><th>Allowed Values</th><th>Description</th></tr></thead>
            <tbody id="schema-table-body"></tbody>
        </table>
    </div>
</div>
"""

# Inject views right after the tabs and before the legacy read view
content = content.replace('<div id="view-read" class="view-section">', new_views + '\n\n<div id="view-read" class="view-section" style="display: none;">')

with open("web/templates/index.html", "w") as f:
    f.write(content)
