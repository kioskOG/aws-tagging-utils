import re

with open("web/static/app.js", "r") as f:
    content = f.read()

# Add API fetching logic for the new tabs
new_js = """
// -----------------------------------------------------------------------------
// NEW ENTERPRISE DASHBOARD LOGIC
// -----------------------------------------------------------------------------

async function loadEnterpriseData() {
    try {
        const dashboard = await (await fetch('/api/dashboard')).json();
        $('#dash-compliance').innerText = dashboard.compliance_pct + '%';
        $('#dash-spend').innerText = '$' + dashboard.total_spend.toLocaleString();
        $('#dash-violations').innerText = dashboard.active_violations;
        $('#dash-allocation').innerText = dashboard.allocation_pct + '%';
        $('#dash-protected').innerText = dashboard.protected_violations;
        $('#dash-pending').innerText = dashboard.pending_remediation;
        $('#dash-exemptions').innerText = dashboard.active_exemptions;
        $('#dash-total-res').innerText = dashboard.total_resources.toLocaleString();
        $('#dash-comp-res').innerText = dashboard.compliant_resources.toLocaleString();
        $('#dash-noncomp-res').innerText = dashboard.non_compliant_resources.toLocaleString();
    } catch(e) { console.error("Failed to load dashboard", e); }
    
    try {
        const schema = await (await fetch('/api/schema')).json();
        const tbody = $('#schema-table-body');
        tbody.innerHTML = '';
        schema.forEach(rule => {
            tbody.innerHTML += `<tr>
                <td class="name-cell">${rule.tag}</td>
                <td>${rule.required ? '✅' : '-'}</td>
                <td>${rule.protected ? '🔒' : '-'}</td>
                <td>${rule.finops ? '💰' : '-'}</td>
                <td>${rule.allowed_values.length ? rule.allowed_values.join(', ') : '*'}</td>
                <td><span style="font-size: 0.8rem; color: var(--muted)">${rule.description}</span></td>
            </tr>`;
        });
    } catch(e) { console.error("Failed to load schema", e); }
    
    try {
        const comp = await (await fetch('/api/compliance')).json();
        const tbody = $('#compliance-table-body');
        tbody.innerHTML = '';
        comp.resources.forEach(r => {
            tbody.innerHTML += `<tr>
                <td class="arn-cell">${r.id}</td>
                <td>${r.account}</td>
                <td><span class="status-badge info">${r.type}</span></td>
                <td><span class="status-badge ${r.status === 'COMPLIANT' ? 'ok' : 'err'}">${r.status}</span></td>
                <td>${r.missing_tags.length ? r.missing_tags.join(', ') : '-'}</td>
                <td>${r.remediation_state}</td>
                <td><button class="btn-ghost rbac-sensitive" onclick="alert('Fix triggered')">Fix Tags</button></td>
            </tr>`;
        });
    } catch(e) { console.error("Failed to load compliance", e); }
    
    try {
        const finops = await (await fetch('/api/finops')).json();
        $('#finops-total').innerText = '$' + finops.TotalSpend.toLocaleString();
        $('#finops-tagged').innerText = '$' + finops.TaggedSpend.toLocaleString();
        $('#finops-unallocated').innerText = '$' + finops.PotentiallyUnallocated.toLocaleString();
    } catch(e) { console.error("Failed to load finops", e); }
    
    try {
        const sec = await (await fetch('/api/security')).json();
        $('#sec-protected').innerText = sec.protected_tag_violations;
        $('#sec-unauth').innerText = sec.unauthorized_changes;
        $('#sec-reverted').innerText = sec.reverted_changes;
        
        const tbody = $('#drift-table-body');
        tbody.innerHTML = '';
        sec.drift_events.forEach(r => {
            tbody.innerHTML += `<tr>
                <td class="arn-cell">${r.resource}</td>
                <td><span class="name-cell">${r.tag}</span></td>
                <td><span class="status-badge ok">${r.expected}</span></td>
                <td><span class="status-badge err">${r.actual}</span></td>
                <td><span class="arn-cell">${r.changed_by}</span></td>
                <td>${r.status}</td>
                <td><button class="btn-ghost rbac-sensitive" onclick="alert('Revert triggered')">Revert</button></td>
            </tr>`;
        });
    } catch(e) { console.error("Failed to load security", e); }
    
    try {
        const rem = await (await fetch('/api/remediation')).json();
        $('#remediation-table-body').innerHTML = rem.tasks.map(r => `<tr>
            <td class="arn-cell">${r.resource}</td><td>${r.account}</td><td>${r.violation}</td>
            <td>${r.deadline}</td><td><span class="status-badge err">${r.status}</span></td>
            <td><span style="font-size:0.8rem">${r.last_action}</span></td>
        </tr>`).join('');
    } catch(e) { console.error("Failed to load remediation", e); }
    
    try {
        const ex = await (await fetch('/api/exemptions')).json();
        $('#exemptions-table-body').innerHTML = ex.exemptions.map(r => `<tr>
            <td><span class="arn-cell">${r.scope}</span></td><td>${r.reason}</td>
            <td>${r.owner}</td><td>${r.expiration}</td>
            <td><span class="status-badge ok">${r.status}</span></td>
        </tr>`).join('');
    } catch(e) { console.error("Failed to load exemptions", e); }
    
    try {
        const aud = await (await fetch('/api/audit')).json();
        $('#audit-table-body').innerHTML = aud.events.map(r => `<tr>
            <td>${r.timestamp}</td><td><span class="status-badge info">${r.action}</span></td>
            <td class="arn-cell">${r.resource}</td><td class="name-cell">${r.actor}</td>
            <td><span class="status-badge ${r.result === 'SUCCESS' ? 'ok' : 'err'}">${r.result}</span></td>
            <td><span style="font-size:0.8rem">${r.reason}</span></td>
        </tr>`).join('');
    } catch(e) { console.error("Failed to load audit", e); }
    
    try {
        const org = await (await fetch('/api/organization')).json();
        $('#org-table-body').innerHTML = org.accounts.map(r => `<tr>
            <td class="name-cell">${r.name}</td><td>${r.ou}</td>
            <td>${r.compliance}%</td><td>${r.resources}</td>
            <td>${r.violations}</td><td>$${r.spend.toLocaleString()}</td>
        </tr>`).join('');
    } catch(e) { console.error("Failed to load organization", e); }
    
    updateRBAC();
}

function updateRBAC() {
    const role = $('#user-role-select').value;
    const isSensitive = ['PlatformAdmin', 'SecurityAdmin', 'ApplicationOwner'].includes(role);
    
    $$('.rbac-sensitive').forEach(el => {
        if (!isSensitive) {
            el.setAttribute('disabled', 'true');
            el.style.opacity = '0.3';
            el.title = "Unauthorized for current role";
        } else {
            el.removeAttribute('disabled');
            el.style.opacity = '1';
            el.title = "";
        }
    });
}

// Automatically load enterprise data on load
document.addEventListener('DOMContentLoaded', () => {
    loadEnterpriseData();
});
"""

content += new_js

# Ensure tabs default to dashboard if possible, but keep existing JS tab logic working.
# Tab logic starts at line 250 in app.js
content = content.replace("state.selectedTab = 'read';", "state.selectedTab = 'dashboard';")
content = content.replace("document.getElementById(`view-read`).style.display = 'block';", "document.getElementById(`view-dashboard`).style.display = 'block';")

with open("web/static/app.js", "w") as f:
    f.write(content)
