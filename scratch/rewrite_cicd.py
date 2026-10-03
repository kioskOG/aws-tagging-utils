import re

with open("web/templates/index.html", "r") as f:
    content = f.read()

# Add CI/CD tab
content = content.replace('<button class="tab-btn" data-tab="schema">📋 Schema</button>', '<button class="tab-btn" data-tab="schema">📋 Schema</button>\n        <button class="tab-btn" data-tab="cicd">🚀 CI/CD Validation</button>')

# Add CI/CD view
cicd_view = """
<!-- CICD VIEW -->
<div id="view-cicd" class="view-section" style="display: none;">
    <div class="section-title">CI/CD PIPELINE VALIDATION (SARIF)</div>
    <div class="table-container">
        <table>
            <thead><tr><th>Repository</th><th>Branch</th><th>Commit</th><th>Status</th><th>Violations</th><th>Actions</th></tr></thead>
            <tbody id="cicd-table-body"></tbody>
        </table>
    </div>
</div>
"""
content = content.replace('<!-- SCHEMA VIEW -->', cicd_view + '\n<!-- SCHEMA VIEW -->')

with open("web/templates/index.html", "w") as f:
    f.write(content)

with open("web/static/app.js", "r") as f:
    js = f.read()

# Add fetch logic
cicd_js = """
    try {
        const cicd = await (await fetch('/api/cicd')).json();
        $('#cicd-table-body').innerHTML = cicd.runs.map(r => `<tr>
            <td class="name-cell">${r.repo}</td><td><span class="status-badge info">${r.branch}</span></td>
            <td class="arn-cell">${r.commit}</td>
            <td><span class="status-badge ${r.status === 'PASSED' ? 'ok' : 'err'}">${r.status}</span></td>
            <td>${r.violations}</td>
            <td><button class="btn-ghost">View SARIF</button></td>
        </tr>`).join('');
    } catch(e) { console.error("Failed to load cicd", e); }
"""
js = js.replace('updateRBAC();\n}', cicd_js + '\n    updateRBAC();\n}')

with open("web/static/app.js", "w") as f:
    f.write(js)
