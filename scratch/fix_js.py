#!/usr/bin/env python3
"""
Fix the enterprise tab switching + data loading JS to match new HTML IDs.
"""

with open("web/static/app.js", "r") as f:
    js = f.read()

# ─── Replace old tab switching (controls .config-panel) with enterprise tabs ──
old_tab_switch = """    // Tab Switching
    $$('.tab-btn').forEach(btn => {
      btn.onclick = () => {
        const tab = btn.dataset.tab;
        state.selectedTab = tab;
        $$('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
        $$('.config-panel').forEach(p => p.style.display = p.id === `config-${tab}` ? 'block' : 'none');
      };
    });"""

new_tab_switch = """    // Enterprise Tab Switching
    $$('.tab-btn').forEach(btn => {
      btn.onclick = () => {
        const tab = btn.dataset.tab;
        state.selectedTab = tab;
        $$('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));

        // Enterprise views
        const entViews = ['dashboard','compliance','finops','security','enforcement','organization','schema','cicd'];
        entViews.forEach(v => {
          const el = document.getElementById(`view-ent-${v}`);
          if (el) el.style.display = (tab === v) ? 'block' : 'none';
        });

        // Legacy tag-tools panel
        const legacyView = document.getElementById('view-ent-legacy');
        if (legacyView) legacyView.style.display = (tab === 'legacy') ? 'block' : 'none';

        // Legacy config panels only relevant in legacy tab
        $$('.config-panel').forEach(p => {
          p.style.display = (tab === 'legacy' && p.id === 'config-read') ? 'block' : 'none';
        });
      };
    });"""

js = js.replace(old_tab_switch, new_tab_switch)

# ─── Fix dashboard data loading to use correct element IDs ───────────────────
# Update security: add changed_at field and fix column count
old_sec_row = """            tbody.innerHTML += `<tr>
                <td class="arn-cell">${r.resource}</td>
                <td><span class="name-cell">${r.tag}</span></td>
                <td><span class="status-badge ok">${r.expected}</span></td>
                <td><span class="status-badge err">${r.actual}</span></td>
                <td><span class="arn-cell">${r.changed_by}</span></td>
                <td>${r.status}</td>
                <td><button class="btn-ghost rbac-sensitive" onclick="alert('Revert triggered')">Revert</button></td>
            </tr>`;"""
new_sec_row = """            tbody.innerHTML += `<tr>
                <td class="arn-cell">${r.resource}</td>
                <td><span class="name-cell">${r.tag}</span></td>
                <td><span class="status-badge ok">${r.expected}</span></td>
                <td><span class="status-badge err">${r.actual}</span></td>
                <td><span class="arn-cell" style="font-size:0.75rem">${r.changed_by}</span></td>
                <td style="font-size:0.8rem">${r.changed_at || '-'}</td>
                <td><span class="status-badge ${r.status === 'OPEN' ? 'err' : 'info'}">${r.status}</span></td>
                <td><button class="btn-ghost rbac-sensitive" onclick="alert('Revert triggered')">Revert</button></td>
            </tr>`;"""
js = js.replace(old_sec_row, new_sec_row)

# ─── Fix finops: add untagged + cost allocation tags ─────────────────────────
old_finops = """        const finops = await (await fetch('/api/finops')).json();
        $('#finops-total').innerText = '$' + finops.TotalSpend.toLocaleString();
        $('#finops-tagged').innerText = '$' + finops.TaggedSpend.toLocaleString();
        $('#finops-unallocated').innerText = '$' + finops.PotentiallyUnallocated.toLocaleString();"""
new_finops = """        const finops = await (await fetch('/api/finops')).json();
        const fmt = (n) => n != null ? '$' + Number(n).toLocaleString() : '$--';
        if ($('#finops-total')) $('#finops-total').innerText = fmt(finops.TotalSpend);
        if ($('#finops-tagged')) $('#finops-tagged').innerText = fmt(finops.TaggedSpend);
        if ($('#finops-untagged')) $('#finops-untagged').innerText = fmt(finops.UntaggedSpend);
        if ($('#finops-unallocated')) $('#finops-unallocated').innerText = fmt(finops.PotentiallyUnallocated);
        const tagsList = $('#finops-tags-list');
        if (tagsList && finops.CostAllocationTags) {
          tagsList.innerHTML = finops.CostAllocationTags.map(t =>
            `<span class="status-badge info">💰 ${t}</span>`).join('');
        }"""
js = js.replace(old_finops, new_finops)

# ─── Fix dashboard: add security fields ──────────────────────────────────────
old_dash_sec = "    } catch(e) { console.error(\"Failed to load dashboard\", e); }"
# Append new fields right after basic dash load
new_dash_sec = """    } catch(e) { console.error("Failed to load dashboard", e); }

    // Also wire security summary into dashboard security card
    try {
        const sec = await (await fetch('/api/security')).json();
        if ($('#dash-drift')) $('#dash-drift').innerText = sec.drift_events ? sec.drift_events.length : sec.protected_tag_violations;
        if ($('#dash-reverted')) $('#dash-reverted').innerText = sec.reverted_changes;
        if ($('#dash-pending-sec')) $('#dash-pending-sec').innerText = sec.pending_review;
    } catch(e) { /* non-critical */ }"""

js = js.replace(old_dash_sec, new_dash_sec, 1)

# ─── Fix exemptions: add Actions column ──────────────────────────────────────
old_ex_row = """        $('#exemptions-table-body').innerHTML = ex.exemptions.map(r => `<tr>
            <td><span class="arn-cell">${r.scope}</span></td><td>${r.reason}</td>
            <td>${r.owner}</td><td>${r.expiration}</td>
            <td><span class="status-badge ok">${r.status}</span></td>
        </tr>`).join('');"""
new_ex_row = """        $('#exemptions-table-body').innerHTML = ex.exemptions.map(r => `<tr>
            <td><span class="arn-cell">${r.scope}</span></td><td>${r.reason}</td>
            <td>${r.owner}</td><td style="font-size:0.8rem">${r.expiration}</td>
            <td><span class="status-badge ok">${r.status}</span></td>
            <td><button class="btn-ghost rbac-sensitive" onclick="alert('Revoke triggered')">Revoke</button></td>
        </tr>`).join('');"""
js = js.replace(old_ex_row, new_ex_row)

# ─── Add filterComplianceTable function ──────────────────────────────────────
filter_fn = """
function filterComplianceTable() {
    const status = $('#comp-filter-status') ? $('#comp-filter-status').value.toLowerCase() : '';
    const text = $('#comp-filter-text') ? $('#comp-filter-text').value.toLowerCase() : '';
    $$('#compliance-table-body tr').forEach(row => {
        const rowText = row.textContent.toLowerCase();
        const statusMatch = !status || rowText.includes(status);
        const textMatch = !text || rowText.includes(text);
        row.style.display = (statusMatch && textMatch) ? '' : 'none';
    });
}
"""
js = js.rstrip() + "\n" + filter_fn

# ─── Fix DOMContentLoaded to also show dashboard by default ──────────────────
old_dcl = """// Automatically load enterprise data on load
document.addEventListener('DOMContentLoaded', () => {
    loadEnterpriseData();
});"""
new_dcl = """// Enterprise init
document.addEventListener('DOMContentLoaded', () => {
    // Ensure dashboard tab is active on load
    const dashBtn = document.getElementById('ent-tab-dashboard');
    if (dashBtn) dashBtn.click();
    loadEnterpriseData();
});"""
js = js.replace(old_dcl, new_dcl)

with open("web/static/app.js", "w") as f:
    f.write(js)

print(f"Done. app.js is now {len(js.splitlines())} lines.")
