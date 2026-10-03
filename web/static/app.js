const AWS_REGIONS = [
      { id: 'us-east-1', name: 'N. Virginia' }, { id: 'us-east-2', name: 'Ohio' },
      { id: 'us-west-1', name: 'N. California' }, { id: 'us-west-2', name: 'Oregon' },
      { id: 'ap-south-1', name: 'Mumbai' }, { id: 'ap-northeast-3', name: 'Osaka' },
      { id: 'ap-northeast-2', name: 'Seoul' }, { id: 'ap-southeast-1', name: 'Singapore' },
      { id: 'ap-southeast-2', name: 'Sydney' }, { id: 'ap-northeast-1', name: 'Tokyo' },
      { id: 'ca-central-1', name: 'Central' }, { id: 'eu-central-1', name: 'Frankfurt' },
      { id: 'eu-west-1', name: 'Ireland' }, { id: 'eu-west-2', name: 'London' },
      { id: 'eu-west-3', name: 'Paris' }, { id: 'eu-north-1', name: 'Stockholm' },
      { id: 'sa-east-1', name: 'São Paulo' }
    ];

    const state = {
      resourceMap: {},
      lastResults: [],
      selectedTab: 'read',
      regions: { read: [], write: [], gov: [], report: [], sync: [] },
      types: { gov: [] }
    };

    class RegionPicker {
      constructor(containerId, tabKey, defaultAll = false) {
        this.container = document.getElementById(containerId);
        this.tabKey = tabKey;
        this.selected = defaultAll ? AWS_REGIONS.map(r => r.id) : ['us-east-2'];
        this.searchTerm = '';
        this.render();
      }

      render() {
        let html = `
            <div class="ms-container" id="msc-${this.tabKey}">
                <div class="ms-header" onclick="document.getElementById('msc-${this.tabKey}').classList.toggle('open')">
                    <div class="ms-tags">
                       ${this.selected.length === AWS_REGIONS.length ? `<span class="ms-tag">All Regions</span>` :
            this.selected.length === 0 ? `<span class="ms-placeholder">Select regions...</span>` :
              this.selected.slice(0, 3).map(id => {
                const r = AWS_REGIONS.find(x => x.id === id);
                return `<span class="ms-tag">${r ? r.name : id}</span>`;
              }).join('') + (this.selected.length > 3 ? `<span class="ms-tag">+${this.selected.length - 3}</span>` : '')
          }
                    </div>
                    <div class="ms-arrow">▼</div>
                </div>
                <div class="ms-dropdown">
                    <div class="ms-controls" style="flex-direction: column; gap: 10px;">
                        <input type="text" placeholder="Search regions..." class="region-search-input"
                               style="background: rgba(255,255,255,0.05); border: 1px solid var(--border); padding: 6px 10px; font-size: 0.85rem;"
                               onclick="event.stopPropagation()"
                               oninput="regionPickers['${this.tabKey}'].filterList(this.value)">
                        <div style="display: flex; gap: 8px;">
                            <button class="btn-ghost" style="flex:1; background: rgba(255,255,255,0.05)" onclick="event.stopPropagation(); regionPickers['${this.tabKey}'].selectAll(true)">All</button>
                            <button class="btn-ghost" style="flex:1; background: rgba(255,255,255,0.05)" onclick="event.stopPropagation(); regionPickers['${this.tabKey}'].selectAll(false)">Clear</button>
                        </div>
                    </div>
                    <div class="ms-list">
                        ${AWS_REGIONS.map(r => `
                            <div class="ms-opt" data-region-id="${r.id}" data-region-name="${r.name}"
                                 onclick="event.stopPropagation(); regionPickers['${this.tabKey}'].toggle('${r.id}')">
                                <input type="checkbox" ${this.selected.includes(r.id) ? 'checked' : ''} style="pointer-events:none">
                                <div class="ms-opt-text">
                                    <span class="ms-opt-name">${r.name}</span>
                                    <span class="ms-opt-id">${r.id}</span>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>
            `;
        this.container.innerHTML = html;
        state.regions[this.tabKey] = this.selected.length === AWS_REGIONS.length ? ['all'] : this.selected;
      }

      filterList(query) {
        this.searchTerm = query;
        const q = query.toLowerCase();
        const items = this.container.querySelectorAll('.ms-opt');
        let visibleCount = 0;
        items.forEach(item => {
          const regionId = item.dataset.regionId.toLowerCase();
          const regionName = item.dataset.regionName.toLowerCase();
          const matches = !q || regionId.includes(q) || regionName.includes(q);
          item.style.display = matches ? '' : 'none';
          if (matches) visibleCount++;
        });

        // Show/hide "no results" message
        let noResults = this.container.querySelector('.ms-no-results');
        if (visibleCount === 0) {
          if (!noResults) {
            noResults = document.createElement('div');
            noResults.className = 'ms-no-results';
            noResults.style.cssText = 'padding: 20px; text-align: center; color: var(--muted); font-size: 0.85rem;';
            noResults.textContent = 'No regions found';
            this.container.querySelector('.ms-list').appendChild(noResults);
          }
          noResults.style.display = '';
        } else if (noResults) {
          noResults.style.display = 'none';
        }
      }

      toggle(id) {
        if (this.selected.includes(id)) {
          this.selected = this.selected.filter(x => x !== id);
        } else {
          this.selected.push(id);
        }
        this.render();
        // Re-apply search filter and restore search text after re-render
        if (this.searchTerm) {
          const input = this.container.querySelector('.region-search-input');
          if (input) {
            input.value = this.searchTerm;
            this.filterList(this.searchTerm);
          }
        }
        document.getElementById(`msc-${this.tabKey}`).classList.add('open');
      }

      selectAll(isAll) {
        this.selected = isAll ? AWS_REGIONS.map(r => r.id) : [];
        this.render();
        if (this.searchTerm) {
          const input = this.container.querySelector('.region-search-input');
          if (input) {
            input.value = this.searchTerm;
            this.filterList(this.searchTerm);
          }
        }
        document.getElementById(`msc-${this.tabKey}`).classList.add('open');
      }
    }

    const regionPickers = {};

    document.addEventListener('click', (e) => {
      ['read', 'write', 'gov', 'report', 'sync'].forEach(tab => {
        const msc = document.getElementById(`msc-${tab}`);
        if (msc && !msc.contains(e.target)) { msc.classList.remove('open'); }
      });
    });

    const $ = (s) => document.querySelector(s);
    const $$ = (s) => document.querySelectorAll(s);

    // Initial Load
    async function loadMeta() {
      try {
        const r = await fetch("/api/meta/resource-types");
        const data = await r.json();
        state.resourceMap = data.map || {};
        renderTypeList();
        renderGovTypeList();
        regionPickers['read'] = new RegionPicker('read-region-picker', 'read', false);
        regionPickers['write'] = new RegionPicker('write-region-picker', 'write', false);
        regionPickers['gov'] = new RegionPicker('gov-region-picker', 'gov', true);
        regionPickers['report'] = new RegionPicker('report-region-picker', 'report', true);
        regionPickers['sync'] = new RegionPicker('sync-region-picker', 'sync', false);
      } catch (e) {
        console.error("Critical Metadata Failure", e);
      }
    }

    function renderTypeList(filter = '') {
      const list = $('#type-list');
      list.innerHTML = '';
      const query = filter.toLowerCase();

      Object.entries(state.resourceMap)
        .sort(([a], [b]) => a.localeCompare(b))
        .forEach(([friendly, raw]) => {
          if (query && !friendly.toLowerCase().includes(query) && !raw.toLowerCase().includes(query)) return;

          const div = document.createElement('div');
          div.className = 'type-item';
          div.innerHTML = `
            <input type="checkbox" value="${friendly}">
            <div class="label-group">
              <span class="friendly">${friendly}</span>
              <span class="aws-raw">${raw}</span>
            </div>
          `;
          div.onclick = (e) => {
            if (e.target.tagName !== 'INPUT') {
              const cb = div.querySelector('input');
              cb.checked = !cb.checked;
            }
          };
          list.appendChild(div);
        });
    }

    function renderGovTypeList(filter = '') {
      const list = $('#gov-type-list');
      if (!list) return;
      list.innerHTML = '';
      const query = filter.toLowerCase();

      Object.entries(state.resourceMap)
        .sort(([a], [b]) => a.localeCompare(b))
        .forEach(([friendly, raw]) => {
          if (query && !friendly.toLowerCase().includes(query) && !raw.toLowerCase().includes(query)) return;

          const div = document.createElement('div');
          div.className = 'type-item';
          div.innerHTML = `
            <input type="checkbox" data-id="${raw}" ${state.types.gov.includes(raw) ? 'checked' : ''}>
            <div class="label-group">
              <span class="friendly">${friendly}</span>
              <span class="aws-raw">${raw}</span>
            </div>
          `;
          div.onclick = (e) => {
            if (e.target.tagName !== 'INPUT') {
              const cb = div.querySelector('input');
              cb.checked = !cb.checked;
              cb.dispatchEvent(new Event('change'));
            }
          };
          div.querySelector('input').onchange = (e) => {
            if (e.target.checked) {
              if (!state.types.gov.includes(raw)) state.types.gov.push(raw);
            } else {
              state.types.gov = state.types.gov.filter(x => x !== raw);
            }
          };
          list.appendChild(div);
        });
    }

    $('#type-search').oninput = (e) => renderTypeList(e.target.value);
    $('#gov-type-search').oninput = (e) => renderGovTypeList(e.target.value);
    $('#types-all').onclick = () => $$('#type-list input').forEach(i => i.checked = true);
    $('#types-clear').onclick = () => $$('#type-list input').forEach(i => i.checked = false);

    function addTagRow(containerId) {
      const row = document.createElement('div');
      row.className = 'tag-row';
      row.innerHTML = `<input type="text" placeholder="Key" class="tag-key"><input type="text" placeholder="Value" class="tag-val"><button class="rm">✕</button>`;
      row.querySelector('.rm').onclick = () => row.remove();
      $(`#${containerId}`).appendChild(row);
    }

    $('#add-read-filter').onclick = () => addTagRow('read-filters');
    $('#add-write-tag').onclick = () => addTagRow('write-tags');

    // Enterprise Tab Switching
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
      };
    });

    $$('.legacy-nav-btn').forEach(btn => {
      btn.onclick = () => {
        const tab = btn.dataset.legacyTab;
        $$('.legacy-nav-btn').forEach(b => {
          if (b === btn) {
            b.classList.add('active');
            b.style.color = 'var(--text)';
            b.style.borderColor = 'var(--border)';
          } else {
            b.classList.remove('active');
            b.style.color = 'var(--muted)';
            b.style.borderColor = 'transparent';
          }
        });

        $$('.config-panel').forEach(p => {
          p.style.display = (p.id === `config-${tab}`) ? 'block' : 'none';
        });
      };
    });

    $('#read-missing-tag-enable').onchange = (e) => {
      $('#missing-tag-group').style.display = e.target.checked ? 'block' : 'none';
    };

    $('#btn-read').onclick = async () => {
      const btn = $('#btn-read');
      const types = Array.from($$('#type-list input:checked')).map(i => i.value);
      if (!types.length) return alert("Please select at least one AWS Service.");

      btn.disabled = true;
      btn.innerHTML = '<div class="loading-spinner"></div> Initializing Scan...';

      const filters = {};
      $$('#read-filters .tag-row').forEach(row => {
        const k = row.querySelector('.tag-key').value.trim();
        const v = row.querySelector('.tag-val').value.trim();
        if (k && v) filters[k] = v;
      });

      const payload = {
        resources: types,
        filters: filters,
        regions: state.regions['read']
      };

      if ($('#read-missing-tag-enable').checked) {
        payload.missing_tag = $('#read-missing-tag-key').value;
      }

      try {
        const res = await fetch("/api/read", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        renderResults(data);
      } catch (e) {
        alert("Discovery failed: " + e.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = 'Run Discovery Scan';
      }
    };

    function renderResults(data) {
      const container = $('#result-content');
      const actionHeader = $('#result-actions');
      const output = $('#output');

      output.textContent = JSON.stringify(data, null, 2);
      $('#toggle-json').style.display = 'block';

      const resources = data.resources || [];
      state.lastResults = resources;
      document.getElementById('stat-discovered').textContent = resources.length;
      document.getElementById('stats-bar').style.display = 'flex';

      if (!resources.length) {
        actionHeader.style.display = 'none';
        container.innerHTML = `<div class="empty-state"><p>Zero resources found matching your specified filters.</p></div>`;
        return;
      }

      actionHeader.style.display = 'block';
      let html = `<div class="table-container"><table><thead><tr><th style="width: 40px"><input type="checkbox" id="select-all-results"></th><th>Resource Name</th><th>Region</th><th>ARN</th><th>Tags</th></tr></thead><tbody>`;

      resources.forEach((r, idx) => {
        const arnParts = (r.ResourceARN || '').split(':');
        const region = arnParts[3] || 'global';
        const tags = Object.entries(r.Tags || {}).map(([k, v]) => `<span class="status-badge info">${k}: ${v}</span>`).join('');
        html += `<tr><td><input type="checkbox" class="result-check" data-idx="${idx}"></td><td class="name-cell">${r.Name || '---'}</td><td><span class="status-badge" style="background: rgba(255,255,255,0.05)">${region}</span></td><td class="arn-cell">${r.ResourceARN} <span class="help-icon" style="cursor:pointer; background:none" onclick="navigator.clipboard.writeText('${r.ResourceARN}'); toast('ARN Copied', 'success')">📋</span></td><td>${tags}</td></tr>`;
      });

      html += `</tbody></table></div>`;
      container.innerHTML = html;

      $('#select-all-results').onclick = (e) => {
        $$('.result-check').forEach(c => c.checked = e.target.checked);
      };
    }

    $('#btn-transfer').onclick = () => {
      const selected = Array.from($$('.result-check:checked'))
        .map(c => state.lastResults[c.dataset.idx].ResourceARN);

      if (!selected.length) return alert("Select at least one resource from the table.");

      $('#write-arns').value = selected.join('\n');

      Array.from($$('.tab-btn')).find(b => b.dataset.tab === 'legacy').click();
      Array.from($$('.legacy-nav-btn')).find(b => b.dataset.legacyTab === 'write').click();
      $('#write-arns').focus();
    };

    $('#btn-write').onclick = async () => {
      const btn = $('#btn-write');
      const arns = $('#write-arns').value.split('\n').filter(s => s.trim());
      if (!arns.length) return alert("Resource Inventory is empty.");

      const tags = {};
      $$('#write-tags .tag-row').forEach(row => {
        const k = row.querySelector('.tag-key').value.trim();
        const v = row.querySelector('.tag-val').value.trim();
        if (k && v) tags[k] = v;
      });

      if (!Object.keys(tags).length) return alert("Please define at least one tag metadata key-pair.");

      btn.disabled = true;
      btn.innerHTML = '<div class="loading-spinner"></div> Executing Write Operations...';

      const payload = {
        arns: arns,
        tags: tags,
        regions: state.regions['write']
      };

      try {
        const res = await fetch("/api/write", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        renderWriteResults(data, res.ok);
      } catch (e) {
        alert("Batch tagging failed: " + e.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = 'Commit Batch Changes';
      }
    };

    function renderWriteResults(data, isOk = true) {
      const container = $('#result-content');
      const output = $('#output');
      output.textContent = JSON.stringify(data, null, 2);
      $('#toggle-json').style.display = 'block';

      if (!isOk) {
        let html = `<div class="section-title">Validation Error</div>`;
        html += `<div class="card" style="border-color: var(--danger);"><div style="color:var(--danger); font-weight:700">⚠ ${data.message || 'Error occurred'}</div>`;
        
        if (data.violations && data.violations.length > 0) {
          html += `<table style="margin-top:12px; font-size:0.8rem; text-align: left;">
            <thead><tr><th>Tag</th><th>Violation</th><th>Expected</th></tr></thead><tbody>
            ${data.violations.map(v => `<tr>
              <td style="color:var(--danger)">${v.tag}</td>
              <td>${v.type}</td>
              <td>${v.expected}</td>
            </tr>`).join('')}
            </tbody></table>`;
        }
        
        html += `</div>`;
        container.innerHTML = html;
        toast(data.message || 'Operation failed', 'error');
        return;
      }

      const failed = data.failed_resources || {};
      const successArns = (data.arns || []).filter(a => !failed[a]);
      const failedArns = Object.keys(failed);

      let html = `<div class="section-title">Batch Execution Summary</div>`;

      if (successArns.length) {
        html += `<div class="card" style="border-color: var(--success); margin-bottom:1.5rem;"><div style="color:var(--success); font-weight:700">✓ Successful Operations (${successArns.length})</div><div style="font-size:0.75rem; margin-top:10px; color:var(--muted); line-height:1.4">${successArns.join('<br>')}</div></div>`;
      }

      if (failedArns.length) {
        html += `<div class="card" style="border-color: var(--danger);"><div style="color:var(--danger); font-weight:700">⚠ Partial Failures (${failedArns.length})</div><table style="margin-top:12px; font-size:0.8rem;">${failedArns.map(a => `<tr><td class="arn-cell">${a}</td><td style="color:var(--danger)">${failed[a].ErrorMessage || 'Access Denied / Not Found'}</td></tr>`).join('')}</table></div>`;
      }

      document.getElementById('stat-tagged').textContent = (data.count || 0);
      document.getElementById('stats-bar').style.display = 'flex';
      const writeMsg = failedArns.length ? `Tagged ${data.count || 0} resources (${failedArns.length} failed)` : `Successfully tagged ${data.count || 0} resources`;
      toast(writeMsg, failedArns.length ? 'error' : 'success');
      container.innerHTML = html;
    }

    $('#toggle-json').onclick = () => {
      const container = $('#json-output-container');
      const isVisible = container.style.display === 'block';
      container.style.display = isVisible ? 'none' : 'block';
      $('#toggle-json').textContent = isVisible ? 'Show JSON Debug' : 'Hide JSON Debug';
    };

    $('#btn-gov').onclick = async () => {
      const btn = $('#btn-gov');
      btn.disabled = true;
      btn.innerHTML = '<div class="loading-spinner"></div> Identifying Owners...';
      const payload = {
        action: 'scan',
        regions: state.regions['gov'],
        types: state.types.gov
      };

      try {
        const res = await fetch("/api/gov", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        renderGovResults(data);
      } catch (e) {
        alert("Governance scan failed: " + e.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = 'Run Auto-Tagging Scan';
      }
    };

    function renderGovResults(data) {
      const container = $('#result-content');
      const output = $('#output');
      output.textContent = JSON.stringify(data, null, 2);

      const regionData = data.regions || {};
      let html = `<div class="section-title">Reconciliation Summary: ${data.total_tagged || 0} Resources Tagged</div>`;

      Object.entries(regionData).forEach(([reg, res]) => {
        const tagged = res.tagged || [];
        const failed = res.failed || {};
        const failedArns = Object.keys(failed);

        html += `<div style="margin-bottom: 24px;">
          <h4 style="margin: 0 0 10px 0; color: #c4b5fd; font-size: 0.95rem;">Region: ${reg}</h4>`;

        if (tagged.length) {
          html += `<div class="card" style="border-color: rgba(16, 185, 129, 0.4); margin-bottom:1rem; padding: 1rem; background: rgba(16, 185, 129, 0.05);">
            <div style="color:var(--success); font-weight:700; margin-bottom: 8px;">✓ Automatically Tagged (${tagged.length})</div>
            <div style="font-family: var(--mono); font-size:0.75rem; color: #cbd5e1; line-height:1.6; word-break: break-all;">
              ${tagged.join('<br>')}
            </div>
          </div>`;
        }

        if (failedArns.length) {
          html += `<div class="card" style="border-color: rgba(239, 68, 68, 0.4); padding: 1rem; background: rgba(239, 68, 68, 0.05);">
            <div style="color:var(--danger); font-weight:700; margin-bottom: 8px;">⚠ Tagging Failed (${failedArns.length})</div>
            <table style="width: 100%; border: none; background: transparent;">
              ${failedArns.map(a => `
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                  <td style="padding: 8px 0; font-family: var(--mono); font-size: 0.75rem; color: #cbd5e1; word-break: break-all; border: none;">${a}</td>
                  <td style="padding: 8px 0 8px 12px; font-size: 0.8rem; color: var(--danger); border: none;">${failed[a].ErrorMessage || 'Access Denied / Not Found'}</td>
                </tr>
              `).join('')}
            </table>
          </div>`;
        }

        if (!tagged.length && !failedArns.length) {
          html += `<div style="font-size: 0.85rem; color: var(--muted); padding: 12px; background: rgba(0,0,0,0.2); border-radius: 8px; border: 1px solid rgba(255,255,255,0.05);">No untagged resources found or identified.</div>`;
        }
        html += `</div>`;
      });

      container.innerHTML = html;
    }

    // Toast notifications
    function toast(msg, type = 'info') {
      const tc = document.getElementById('toast-container');
      const el = document.createElement('div');
      el.className = `toast toast-${type}`;
      const icons = { success: '✓', error: '✕', info: 'ℹ' };
      el.innerHTML = `<span style="font-size:1.1rem">${icons[type] || 'ℹ'}</span><span>${msg}</span>`;
      tc.appendChild(el);
      setTimeout(() => el.remove(), 4000);
    }




    document.getElementById('btn-report').onclick = async () => {
      const btn = document.getElementById('btn-report');
      btn.disabled = true;
      btn.innerHTML = '<div class="loading-spinner"></div> Auditing…';
      const payload = {
        regions: state.regions['report'] || [],
        mandatory_tags: document.getElementById('report-mandatory').value.split(',').map(s => s.trim()).filter(s => s),
        export_bucket: document.getElementById('report-bucket').value.trim() || null
      };
      try {
        const res = await fetch('/api/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const data = await res.json();
        document.getElementById('output').textContent = JSON.stringify(data, null, 2);
        document.getElementById('toggle-json').style.display = 'block';
        renderReportResults(data);
        // Sync cache so the Enterprise Compliance tab reflects this scan immediately
        if (typeof loadEnterpriseData === 'function') loadEnterpriseData();
      } catch (e) { toast('Report generation failed: ' + e.message, 'error'); }
      finally { btn.disabled = false; btn.innerHTML = 'Generate Compliance Report'; }
    };

    function renderReportResults(data) {
      const summary = data.summary || {};
      const score = summary.compliance_score !== undefined ? summary.compliance_score : 0;
      document.getElementById('stat-score').textContent = score + '%';
      document.getElementById('stats-bar').style.display = 'flex';

      // Update Dashboard UI with latest audit data
      if (document.getElementById('dash-compliance')) document.getElementById('dash-compliance').innerText = score + '%';
      if (document.getElementById('dash-violations')) document.getElementById('dash-violations').innerText = summary.non_compliant || 0;
      if (document.getElementById('dash-total-res')) document.getElementById('dash-total-res').innerText = (summary.total_resources || 0).toLocaleString();
      if (document.getElementById('dash-comp-res')) document.getElementById('dash-comp-res').innerText = (summary.compliant || 0).toLocaleString();
      if (document.getElementById('dash-noncomp-res')) document.getElementById('dash-noncomp-res').innerText = (summary.non_compliant || 0).toLocaleString();
      if (document.getElementById('stat-discovered')) document.getElementById('stat-discovered').innerText = (summary.total_resources || 0).toLocaleString();

      // Update Compliance Tab UI with latest audit data
      const compTbody = document.getElementById('compliance-table-body');
      if (compTbody) {
        let hasResources = false;
        let tbodyHtml = '';
        Object.entries(data.regions || {}).forEach(([reg, res]) => {
          (res.resources || []).forEach(r => {
            hasResources = true;
            const violations = r.Violations || [];
            const missing = violations.filter(v => v.type === 'MISSING_REQUIRED').map(v => v.tag);
            const arnParts = (r.ResourceARN || '').split(':');
            const type = arnParts.length > 2 ? arnParts[2] : 'unknown';
            const account = arnParts.length > 4 ? arnParts[4] : 'unknown';
            const status = r.IsCompliant ? 'COMPLIANT' : 'NON_COMPLIANT';
            const missingJson = JSON.stringify(missing).replace(/'/g, "&apos;");
            const arnEsc = (r.ResourceARN || '').replace(/'/g, "&apos;");
            const fixBtn = !r.IsCompliant && missing.length
              ? `<button class="btn-ghost rbac-sensitive" onclick="fixTags('${arnEsc}', '${reg}', ${missingJson})">Fix Tags</button>`
              : `<button class="btn-ghost" disabled style="opacity:0.3">Fix Tags</button>`;

            tbodyHtml += `<tr>
                <td class="arn-cell">${r.ResourceARN}</td>
                <td>${account}</td>
                <td><span class="status-badge info">${type}</span></td>
                <td><span class="status-badge ${status === 'COMPLIANT' ? 'ok' : 'err'}">${status}</span></td>
                <td>${missing.length ? missing.join(', ') : '-'}</td>
                <td>None</td>
                <td>${fixBtn}</td>
            </tr>`;
          });
        });
        if (!hasResources) {
          tbodyHtml = `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:2rem;font-size:0.85rem">No resources found.</td></tr>`;
        }
        compTbody.innerHTML = tbodyHtml;
      }

      const col = score > 80 ? '#34d399' : score > 50 ? '#fbbf24' : '#f87171';
      const circ = 2 * Math.PI * 44;
      const dash = (score / 100) * circ;
      let html = `
        <div class="score-ring">
          <div class="ring-wrap">
            <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="44" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="8"/><circle cx="50" cy="50" r="44" fill="none" stroke="${col}" stroke-width="8" stroke-dasharray="${dash} ${circ}" stroke-linecap="round"/></svg>
            <div class="ring-label"><span class="ring-pct" style="color:${col}">${score}%</span><span class="ring-sub">Compliant</span></div>
          </div>
          <div class="score-meta">
            <div style="display:flex; justify-content:space-between; align-items:flex-start">
              <h3 style="color:${col}; margin:0">Compliance Scorecard</h3>
              <button class="btn btn-primary" onclick="transferReportToBatch()" style="width:auto; padding:8px 16px; font-size:0.8rem; background:linear-gradient(135deg,#a78bfa,#8b5cf6)">Transfer to Batch Fix</button>
            </div>
            <p>${summary.compliant || 0} of ${summary.total_resources || 0} resources fully tagged<br>
               <span style="color:var(--danger)">${summary.non_compliant || 0} resources</span> need attention</p>
          </div>
        </div>`;
      Object.entries(data.regions || {}).forEach(([reg, res]) => {
        const regScore = res.compliance_score !== undefined ? res.compliance_score : 0;
        const rc = regScore > 80 ? 'var(--success)' : regScore > 50 ? 'var(--warning)' : 'var(--danger)';
        const nonCompliant = (res.resources || []).filter(r => !r.IsCompliant);
        html += `<div class="region-section">
          <div class="region-section-title">
            <h4>${reg}</h4>
            <div style="display:flex;gap:8px;align-items:center">
              <span style="font-size:0.8rem;color:${rc};font-weight:700">${regScore}%</span>
              <span class="region-badge">${nonCompliant.length} issues</span>
            </div>
          </div>`;
        if (nonCompliant.length) {
          html += `<div class="table-container"><table><thead><tr><th style="width:40px"><input type="checkbox" class="report-select-all"></th><th>Resource ARN</th><th>Tag Violations</th></tr></thead><tbody>`;
          nonCompliant.forEach(r => {
            html += `<tr><td><input type="checkbox" class="report-res-check" data-arn="${r.ResourceARN}"></td><td class="arn-cell">${r.ResourceARN}</td><td>${(r.Violations || []).map(v => `<span class="status-badge err">${v.tag}</span>`).join('')}</td></tr>`;
          });
          html += `</tbody></table></div>`;
        } else {
          html += `<div style="padding:10px;font-size:0.85rem;color:var(--success)">✓ All resources compliant in this region</div>`;
        }
        html += `</div>`;
      });
      if (data.export_location) html += `<div class="stat-card" style="margin-top:1rem;text-align:center"><div class="stat-label">Report Exported To</div><code style="color:var(--accent);font-size:0.8rem">${data.export_location}</code></div>`;
      document.getElementById('result-content').innerHTML = html;
      
      // Update RBAC state for new buttons in compliance tab
      if (typeof updateRBAC === 'function') updateRBAC();
    }

    document.getElementById('btn-sync').onclick = async () => {
      const btn = document.getElementById('btn-sync');
      const vpcId = document.getElementById('sync-vpc-id').value.trim();
      if (!vpcId) return toast('VPC ID is required.', 'error');
      const reg = (state.regions['sync'] || [])[0] || 'us-east-2';
      btn.disabled = true;
      btn.innerHTML = '<div class="loading-spinner"></div> Propagating Tags…';
      try {
        const res = await fetch('/api/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'sync_vpc', vpc_id: vpcId, region: reg }) });
        const data = await res.json();
        document.getElementById('output').textContent = JSON.stringify(data, null, 2);
        document.getElementById('toggle-json').style.display = 'block';
        renderSyncResults(data, vpcId);
      } catch (e) { toast('Sync failed: ' + e.message, 'error'); }
      finally { btn.disabled = false; btn.innerHTML = 'Propagate VPC Tags'; }
    };

    function renderSyncResults(data, vpcId) {
      const updated = data.updated_resources || [];
      let html = `<div class="section-title" style="margin-bottom:1.5rem">VPC Tag Propagation — <code style="font-size:0.85rem;color:#a78bfa">${vpcId}</code></div>`;
      if (data.error) {
        html += `<div style="padding:1rem;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:10px;color:var(--danger)">Error: ${data.error}</div>`;
      } else {
        html += `<div class="score-ring" style="margin-bottom:1.5rem">
          <div style="width:80px;height:80px;border-radius:50%;background:linear-gradient(135deg,rgba(139,92,246,0.2),rgba(217,70,239,0.2));border:2px solid rgba(139,92,246,0.4);display:flex;align-items:center;justify-content:center;flex-direction:column">
            <span style="font-size:1.5rem;font-weight:700;color:#e879f9">${updated.length}</span>
          </div>
          <div class="score-meta"><h3 style="color:#e879f9">Resources Updated</h3><p>Tags propagated from <b>${vpcId}</b> to all child network resources.</p></div>
        </div>`;
        if (updated.length) {
          html += `<div class="sync-resource-list">${updated.map(a => `• ${a}`).join('<br>')}</div>`;
        } else {
          html += `<div style="color:var(--muted);font-size:0.85rem;padding:1rem">No child resources were found or updated.</div>`;
        }
        if (data.errors && data.errors.length) {
          html += `<div style="margin-top:1rem;padding:1rem;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.2);border-radius:10px"><div style="color:var(--danger);font-weight:700;margin-bottom:8px">⚠ Errors</div><div style="font-size:0.8rem;color:#fca5a5">${data.errors.join('<br>')}</div></div>`;
        }
      }
      document.getElementById('result-content').innerHTML = html;
      toast(data.error ? 'Sync failed.' : `Propagated tags to ${updated.length} resources.`, data.error ? 'error' : 'success');
    }

    // Replace alerts with toasts
    const _origAlert = window.alert;
    window.alert = (msg) => {
      const type = msg.toLowerCase().includes('fail') || msg.toLowerCase().includes('error') ? 'error' : 'info';
      toast(msg, type);
    };

    // Update stat bar on results
    const _origRenderResults = window.renderResults || null;

    addTagRow('read-filters');
    addTagRow('write-tags');

    function transferReportToBatch() {
      const selected = Array.from(document.querySelectorAll('.report-res-check:checked')).map(cb => cb.dataset.arn);
      if (!selected.length) return toast('Please select at least one resource to fix.', 'warning');

      const textarea = document.getElementById('write-arns');
      const currentVal = textarea.value.trim();
      const newVal = (currentVal ? currentVal + '\n' : '') + selected.join('\n');

      // Remove duplicates
      const uniqueArns = [...new Set(newVal.split('\n'))].filter(s => s.trim());
      textarea.value = uniqueArns.join('\n');

      // Switch to Write tab
      Array.from($$('.tab-btn')).find(b => b.dataset.tab === 'legacy').click();
      Array.from($$('.legacy-nav-btn')).find(b => b.dataset.legacyTab === 'write').click();
      toast(`Transferred ${selected.length} resources to Batch Tagging`, 'success');

      // Auto-focus the tag key field
      setTimeout(() => {
        const rows = document.querySelectorAll('#write-tags .tag-key');
        if (rows.length) rows[0].focus();
        else {
          addTagRow('write-tags');
          setTimeout(() => document.querySelector('#write-tags .tag-key').focus(), 50);
        }
      }, 100);
    }

    // Handle Select All in Report
    document.addEventListener('change', (e) => {
      if (e.target.classList.contains('report-select-all')) {
        const table = e.target.closest('table');
        const cbs = table.querySelectorAll('.report-res-check');
        cbs.forEach(cb => cb.checked = e.target.checked);
      }
    });

    loadMeta();

    // Table Filtering
    document.getElementById('table-filter').addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase();
      const rows = document.querySelectorAll('#result-content tbody tr');
      rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        row.classList.toggle('filtered', !text.includes(q));
      });
    });

    // CSV Export
    document.getElementById('btn-export-csv').onclick = () => {
      const rows = document.querySelectorAll('#result-content table tr');
      let csv = [];
      rows.forEach(row => {
        const cols = row.querySelectorAll('td, th');
        let rowData = [];
        cols.forEach((col, idx) => {
          if (idx === 0) return; // skip checkbox
          let text = col.innerText.replace(/\n/g, ' ').replace(/"/g, '""');
          rowData.push('"' + text + '"');
        });
        if (rowData.length) csv.push(rowData.join(','));
      });

      const blob = new Blob([csv.join('\n')], { type: 'text/csv' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.setAttribute('hidden', '');
      a.setAttribute('href', url);
      a.setAttribute('download', `aws-resources-${new Date().toISOString().slice(0, 10)}.csv`);
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      toast('CSV exported successfully', 'success');
    };

    // Auto-populate Region Pickers on load if meta fails or before it loads
    window.addEventListener('DOMContentLoaded', () => {
      if (!regionPickers['read']) {
        // Fallback if loadMeta hasn't run
        console.log("Waiting for metadata...");
      }
    });
// -----------------------------------------------------------------------------
// NEW ENTERPRISE DASHBOARD LOGIC
// -----------------------------------------------------------------------------

async function loadEnterpriseData() {
    // Helper: fetch JSON and check for API errors
    async function safeFetch(url) {
        const resp = await fetch(url);
        const data = await resp.json();
        if (!resp.ok) return { _error: true, _status: resp.status, ...data };
        return data;
    }
    function showUnavailable(tbodyId, colSpan, message) {
        const el = $(tbodyId);
        if (el) el.innerHTML = `<tr><td colspan="${colSpan}" style="text-align:center;color:var(--muted);padding:2rem;font-size:0.85rem">${message}</td></tr>`;
    }

    try {
        const dashboard = await safeFetch('/api/dashboard');
        if (dashboard._error) {
            console.error("Dashboard API error:", dashboard.message || dashboard.error);
            if ($('#dash-compliance')) $('#dash-compliance').innerText = 'N/A';
            if ($('#dash-spend')) $('#dash-spend').innerText = '$--';
        } else {
            $('#dash-compliance').innerText = dashboard.compliance_pct + '%';
            $('#dash-spend').innerText = '$' + (dashboard.total_spend || 0).toLocaleString();
            $('#dash-violations').innerText = dashboard.active_violations;
            $('#dash-allocation').innerText = dashboard.allocation_pct + '%';
            $('#dash-protected').innerText = dashboard.protected_violations;
            $('#dash-pending').innerText = dashboard.pending_remediation;
            $('#dash-exemptions').innerText = dashboard.active_exemptions;
            $('#dash-total-res').innerText = (dashboard.total_resources || 0).toLocaleString();
            $('#dash-comp-res').innerText = (dashboard.compliant_resources || 0).toLocaleString();
            $('#dash-noncomp-res').innerText = (dashboard.non_compliant_resources || 0).toLocaleString();

            // Update top-level hero blocks (stats-bar)
            if ($('#stat-discovered')) $('#stat-discovered').innerText = (dashboard.total_resources || 0).toLocaleString();
            if ($('#stat-score')) $('#stat-score').innerText = dashboard.compliance_pct + '%';
            
            if (dashboard._meta) {
                if (dashboard._meta.generated_at) {
                    const ago = Math.round(Date.now()/1000 - dashboard._meta.generated_at);
                    const agoText = ago < 60 ? `${ago} seconds ago` : `${Math.floor(ago/60)} minutes ago`;
                    if ($('#meta-last-updated')) $('#meta-last-updated').innerText = `Last updated: ${agoText}`;
                    if ($('#meta-status-text')) $('#meta-status-text').innerText = 'Showing cached results';
                } else if (dashboard._meta.status) {
                    if ($('#meta-status-text')) $('#meta-status-text').innerText = dashboard._meta.status;
                    if ($('#meta-last-updated')) $('#meta-last-updated').innerText = '';
                }
            }
            
            // Check cache status for stale-while-revalidate
            fetch('/api/compliance/status').then(r => r.json()).then(s => {
                if (s.is_refreshing) {
                    if ($('#meta-status-text')) $('#meta-status-text').innerText = 'Refreshing from AWS...';
                    const btn = document.getElementById('btn-refresh-compliance');
                    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="icon">↻</span> Refreshing...'; }
                    if (typeof pollRefreshStatus === 'function') pollRefreshStatus();
                } else if (s.is_stale) {
                    if (typeof triggerBackgroundRefresh === 'function') triggerBackgroundRefresh();
                }
            }).catch(e => console.error(e));
        }
    } catch(e) { console.error("Failed to load dashboard", e); }
    // Security summary for dashboard card — handle 501 gracefully
    try {
        const sec = await safeFetch('/api/security');
        if (!sec._error) {
            if ($('#dash-drift')) $('#dash-drift').innerText = sec.drift_events ? sec.drift_events.length : sec.protected_tag_violations;
            if ($('#dash-reverted')) $('#dash-reverted').innerText = sec.reverted_changes;
            if ($('#dash-pending-sec')) $('#dash-pending-sec').innerText = sec.pending_review;
        } else {
            if ($('#dash-drift')) $('#dash-drift').innerText = 'N/A';
            if ($('#dash-reverted')) $('#dash-reverted').innerText = 'N/A';
            if ($('#dash-pending-sec')) $('#dash-pending-sec').innerText = 'N/A';
            if ($('#sec-protected')) $('#sec-protected').innerText = 'N/A';
            if ($('#sec-unauth')) $('#sec-unauth').innerText = 'N/A';
            if ($('#sec-reverted')) $('#sec-reverted').innerText = 'N/A';
        }
    } catch(e) { /* non-critical */ }

    try {
        const schema = await safeFetch('/api/schema');
        const tbody = $('#schema-table-body');
        if (tbody && !schema._error) {
            let html = '';
            schema.forEach(rule => {
                html += `<tr>
                    <td class="name-cell">${rule.tag}</td>
                    <td>${rule.required ? '✅' : '-'}</td>
                    <td>${rule.protected ? '🔒' : '-'}</td>
                    <td>${rule.finops ? '💰' : '-'}</td>
                    <td>${rule.allowed_values.length ? rule.allowed_values.join(', ') : '*'}</td>
                    <td><span style="font-size: 0.8rem; color: var(--muted)">${rule.description}</span></td>
                </tr>`;
            });
            tbody.innerHTML = html;
        }
    } catch(e) { console.error("Failed to load schema", e); }

    try {
        const comp = await safeFetch('/api/compliance');
        const tbody = $('#compliance-table-body');
        if (tbody && !comp._error && comp.resources) {
            // Store full resource list for pagination
            window._complianceResources = comp.resources;
            window._compliancePage = 0;
            if (comp.resources.length === 0) {
                showUnavailable('#compliance-table-body', 7,
                    'No resources found. Run a Compliance Audit first, or ensure AWS credentials and resources exist in the target region.');
            } else {
                renderCompliancePage(0);
            }
            // Update metadata timestamp from /api/compliance response
            if (comp._meta && comp._meta.generated_at) {
                const ago = Math.round(Date.now()/1000 - comp._meta.generated_at);
                const agoText = ago < 60 ? `${ago} seconds ago` : `${Math.floor(ago/60)} minutes ago`;
                if ($('#meta-last-updated')) $('#meta-last-updated').innerText = `Last updated: ${agoText}`;
                if ($('#meta-status-text')) $('#meta-status-text').innerText = 'Showing cached results';
            }
        } else if (tbody && comp._error) {
            showUnavailable('#compliance-table-body', 7, comp.message || 'Failed to load compliance data.');
        }
    } catch(e) { console.error("Failed to load compliance", e); }

    try {
        const finops = await safeFetch('/api/finops');
        if (!finops._error) {
            const fmt = (n) => n != null ? '$' + Number(n).toLocaleString() : '$--';
            if ($('#finops-total')) $('#finops-total').innerText = fmt(finops.TotalSpend);
            if ($('#finops-tagged')) $('#finops-tagged').innerText = fmt(finops.TaggedSpend);
            if ($('#finops-untagged')) $('#finops-untagged').innerText = fmt(finops.UntaggedSpend);
            if ($('#finops-unallocated')) $('#finops-unallocated').innerText = fmt(finops.PotentiallyUnallocated);
            const tagsList = $('#finops-tags-list');
            if (tagsList && finops.CostAllocationTags) {
              tagsList.innerHTML = finops.CostAllocationTags.map(t =>
                `<span class="status-badge info">💰 ${t}</span>`).join('');
            }
        } else {
            if ($('#finops-total')) $('#finops-total').innerText = 'N/A';
            if ($('#finops-tagged')) $('#finops-tagged').innerText = 'N/A';
            if ($('#finops-untagged')) $('#finops-untagged').innerText = 'N/A';
            if ($('#finops-unallocated')) $('#finops-unallocated').innerText = 'N/A';
            console.warn("FinOps data unavailable:", finops.message);
        }
    } catch(e) { console.error("Failed to load finops", e); }
    
    // NOTE: /api/security is fetched once at the top of this function (dashboard card).
    // Drift table — show unavailable state since security is not implemented.
    try {
        showUnavailable('#drift-table-body', 8, 'Security drift detection requires DynamoDB and AWS Config.');
    } catch(e) { /* non-critical */ }


    try {
        const rem = await safeFetch('/api/remediation');
        if (!rem._error && rem.tasks) {
            $('#remediation-table-body').innerHTML = rem.tasks.map(r => `<tr>
                <td class="arn-cell">${r.resource}</td><td>${r.account}</td><td>${r.violation}</td>
                <td>${r.deadline}</td><td><span class="status-badge err">${r.status}</span></td>
                <td><span style="font-size:0.8rem">${r.last_action}</span></td>
            </tr>`).join('');
        } else {
            showUnavailable('#remediation-table-body', 6, rem.message || 'Remediation engine requires DynamoDB state table.');
        }
    } catch(e) { console.error("Failed to load remediation", e); }
    
    try {
        const ex = await safeFetch('/api/exemptions');
        if (!ex._error && ex.exemptions) {
            $('#exemptions-table-body').innerHTML = ex.exemptions.map(r => `<tr>
                <td><span class="arn-cell">${r.scope}</span></td><td>${r.reason}</td>
                <td>${r.owner}</td><td style="font-size:0.8rem">${r.expiration}</td>
                <td><span class="status-badge ok">${r.status}</span></td>
                <td><button class="btn-ghost rbac-sensitive" onclick="alert('Revoke triggered')">Revoke</button></td>
            </tr>`).join('');
        } else {
            showUnavailable('#exemptions-table-body', 6, ex.message || 'Exemptions require DynamoDB state table.');
        }
    } catch(e) { console.error("Failed to load exemptions", e); }
    
    try {
        const aud = await safeFetch('/api/audit');
        if (!aud._error && aud.events) {
            $('#audit-table-body').innerHTML = aud.events.map(r => `<tr>
                <td>${r.timestamp}</td><td><span class="status-badge info">${r.action}</span></td>
                <td class="arn-cell">${r.resource}</td><td class="name-cell">${r.actor}</td>
                <td><span class="status-badge ${r.result === 'SUCCESS' ? 'ok' : 'err'}">${r.result}</span></td>
                <td><span style="font-size:0.8rem">${r.reason}</span></td>
            </tr>`).join('');
        } else {
            showUnavailable('#audit-table-body', 6, aud.message || 'Audit log requires DynamoDB state table.');
        }
    } catch(e) { console.error("Failed to load audit", e); }
    
    try {
        const org = await safeFetch('/api/organization');
        if (!org._error && org.accounts) {
            $('#org-table-body').innerHTML = org.accounts.map(r => `<tr>
                <td class="name-cell">${r.name}</td><td>${r.ou}</td>
                <td>${r.compliance}%</td><td>${r.resources}</td>
                <td>${r.violations}</td><td>$${r.spend.toLocaleString()}</td>
            </tr>`).join('');
        } else {
            showUnavailable('#org-table-body', 6, org.message || 'Organization view requires AWS Organizations.');
        }
    } catch(e) { console.error("Failed to load organization", e); }
    
    try {
        const cicd = await safeFetch('/api/cicd');
        if (!cicd._error && cicd.runs) {
            $('#cicd-table-body').innerHTML = cicd.runs.map(r => `<tr>
                <td class="name-cell">${r.repo}</td><td><span class="status-badge info">${r.branch}</span></td>
                <td class="arn-cell">${r.commit}</td>
                <td><span class="status-badge ${r.status === 'PASSED' ? 'ok' : 'err'}">${r.status}</span></td>
                <td>${r.violations}</td>
                <td><button class="btn-ghost">View SARIF</button></td>
            </tr>`).join('');
        } else {
            showUnavailable('#cicd-table-body', 6, cicd.message || 'CI/CD metrics not available in local mode.');
        }
    } catch(e) { console.error("Failed to load cicd", e); }

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

// Enterprise init
document.addEventListener('DOMContentLoaded', () => {
    // Ensure dashboard tab is active on load
    const dashBtn = document.getElementById('ent-tab-dashboard');
    if (dashBtn) dashBtn.click();
    loadEnterpriseData();
});

const COMPLIANCE_PAGE_SIZE = 50;

function renderCompliancePage(page) {
    const resources = window._complianceResources || [];
    const totalPages = Math.ceil(resources.length / COMPLIANCE_PAGE_SIZE);
    const start = page * COMPLIANCE_PAGE_SIZE;
    const slice = resources.slice(start, start + COMPLIANCE_PAGE_SIZE);
    window._compliancePage = page;

    const tbody = document.getElementById('compliance-table-body');
    if (!tbody) return;

    let html = '';
    slice.forEach(r => {
        const missingJson = JSON.stringify(r.missing_tags || []);
        const arnEsc = (r.id || '').replace(/"/g, '&quot;');
        const regionEsc = (r.region || '').replace(/"/g, '&quot;');
        const fixBtn = r.status !== 'COMPLIANT' && r.missing_tags && r.missing_tags.length
            ? `<button class="btn-ghost rbac-sensitive" onclick='fixTags("${arnEsc}","${regionEsc}",${missingJson})'>Fix Tags</button>`
            : `<button class="btn-ghost" disabled style="opacity:0.3">Fix Tags</button>`;
        html += `<tr>
            <td class="arn-cell">${r.id}</td>
            <td>${r.account}</td>
            <td><span class="status-badge info">${r.type}</span></td>
            <td><span class="status-badge ${r.status === 'COMPLIANT' ? 'ok' : 'err'}">${r.status}</span></td>
            <td>${r.missing_tags && r.missing_tags.length ? r.missing_tags.join(', ') : '-'}</td>
            <td>${r.remediation_state || 'None'}</td>
            <td>${fixBtn}</td>
        </tr>`;
    });
    tbody.innerHTML = html;

    // Render pagination controls
    let paginationEl = document.getElementById('compliance-pagination');
    if (!paginationEl) {
        paginationEl = document.createElement('div');
        paginationEl.id = 'compliance-pagination';
        paginationEl.style.cssText = 'display:flex;gap:8px;align-items:center;justify-content:flex-end;margin-top:12px;font-size:0.85rem;';
        tbody.closest('.table-container').after(paginationEl);
    }
    if (totalPages <= 1) {
        paginationEl.innerHTML = '';
        return;
    }
    paginationEl.innerHTML = `
        <span style="color:var(--muted)">Showing ${start+1}–${Math.min(start+COMPLIANCE_PAGE_SIZE, resources.length)} of ${resources.length}</span>
        <button class="btn-ghost" onclick="renderCompliancePage(${page-1})" ${page === 0 ? 'disabled' : ''}>← Prev</button>
        <span style="color:var(--muted)">Page ${page+1} of ${totalPages}</span>
        <button class="btn-ghost" onclick="renderCompliancePage(${page+1})" ${page >= totalPages-1 ? 'disabled' : ''}>Next →</button>
    `;
    if (typeof updateRBAC === 'function') updateRBAC();
}

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

async function triggerBackgroundRefresh() {
    const btn = document.getElementById('btn-refresh-compliance');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="icon">↻</span> Refreshing...';
    }
    const statusEl = document.getElementById('meta-status-text');
    if (statusEl) statusEl.innerText = 'Refreshing from AWS...';
    
    try {
        const res = await fetch('/api/compliance/refresh', { method: 'POST', body: '{}' });
        if (res.ok) {
            pollRefreshStatus();
        } else {
            if (statusEl) statusEl.innerText = 'Refresh failed.';
            if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon">↻</span> Refresh'; }
        }
    } catch(e) {
        if (statusEl) statusEl.innerText = 'Refresh error.';
        if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon">↻</span> Refresh'; }
    }
}

function pollRefreshStatus() {
    const interval = setInterval(async () => {
        try {
            const res = await fetch('/api/compliance/status');
            const status = await res.json();
            if (!status.is_refreshing) {
                clearInterval(interval);
                loadEnterpriseData();
                const statusEl = document.getElementById('meta-status-text');
                if (statusEl) {
                    statusEl.innerText = status.last_refresh_error
                        ? `Refresh failed: ${status.last_refresh_error}`
                        : 'Updated just now.';
                }
                const btn = document.getElementById('btn-refresh-compliance');
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '<span class="icon">↻</span> Refresh';
                }
            }
        } catch(e) {
            clearInterval(interval);
            const btn = document.getElementById('btn-refresh-compliance');
            if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon">↻</span> Refresh'; }
        }
    }, 2000);
}


/**
 * Open the Fix Tags modal for a single non-compliant resource.
 * @param {string} arn      - Full resource ARN
 * @param {string} region   - AWS region where the resource lives
 * @param {string[]} missingTags - List of tag keys that are missing
 */
function fixTags(arn, region, missingTags) {
    const modal = document.getElementById('fix-tags-modal');
    if (!modal) return;

    document.getElementById('fix-tags-arn').textContent = arn;
    document.getElementById('fix-tags-region').textContent = region;

    // Store state on the modal element for fixTagsConfirm()
    modal._arn = arn;
    modal._region = region;
    modal._missingTags = missingTags;

    // Build one input row per missing tag
    const fields = document.getElementById('fix-tags-fields');
    fields.innerHTML = missingTags.map(tag => `
        <div>
          <label style="font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px;">${tag} <span style="color:var(--danger)">*</span></label>
          <input
            id="fix-tag-val-${tag.replace(/[^a-zA-Z0-9]/g,'-')}"
            type="text"
            placeholder="Enter value for ${tag}"
            style="width:100%;font-size:0.9rem;"
          />
        </div>
    `).join('');

    const statusEl = document.getElementById('fix-tags-status');
    statusEl.style.display = 'none';
    statusEl.textContent = '';

    const submitBtn = document.getElementById('fix-tags-submit');
    submitBtn.disabled = false;
    submitBtn.textContent = 'Apply Tags';

    modal.style.display = 'flex';

    // Focus first input
    const first = fields.querySelector('input');
    if (first) setTimeout(() => first.focus(), 50);

    // Close on backdrop click
    modal.onclick = (e) => { if (e.target === modal) closeFixTagsModal(); };
}

function closeFixTagsModal() {
    const modal = document.getElementById('fix-tags-modal');
    if (modal) modal.style.display = 'none';
}

async function fixTagsConfirm() {
    const modal = document.getElementById('fix-tags-modal');
    if (!modal) return;

    const arn = modal._arn;
    const region = modal._region;
    const missingTags = modal._missingTags || [];

    // Collect values — validate all are filled
    const tags = {};
    let allFilled = true;
    missingTags.forEach(tag => {
        const input = document.getElementById('fix-tag-val-' + tag.replace(/[^a-zA-Z0-9]/g, '-'));
        const val = input ? input.value.trim() : '';
        if (!val) { allFilled = false; if (input) input.style.border = '1px solid var(--danger)'; }
        else { tags[tag] = val; if (input) input.style.border = ''; }
    });

    if (!allFilled) {
        showFixTagsStatus('Please fill in all required tag values.', 'error');
        return;
    }

    const submitBtn = document.getElementById('fix-tags-submit');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<div class="loading-spinner" style="width:14px;height:14px;border-width:2px;"></div> Applying…';

    try {
        const payload = {
            action: 'write',
            resource_arn: arn,
            region: region,
            tags: tags
        };
        const res = await fetch('/api/write', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (res.ok && !data.error) {
            showFixTagsStatus(`✅ Tags applied successfully! The resource will be re-evaluated on the next compliance scan.`, 'success');
            submitBtn.textContent = 'Done';
            submitBtn.onclick = closeFixTagsModal;
            // Trigger a background refresh so the compliance table updates
            setTimeout(() => {
                if (typeof triggerBackgroundRefresh === 'function') triggerBackgroundRefresh();
            }, 800);
        } else {
            const msg = data.message || data.error || 'Unknown error';
            showFixTagsStatus(`❌ Failed: ${msg}`, 'error');
            submitBtn.disabled = false;
            submitBtn.textContent = 'Retry';
        }
    } catch(e) {
        showFixTagsStatus(`❌ Network error: ${e.message}`, 'error');
        submitBtn.disabled = false;
        submitBtn.textContent = 'Retry';
    }
}

function showFixTagsStatus(msg, type) {
    const el = document.getElementById('fix-tags-status');
    if (!el) return;
    el.style.display = 'block';
    el.textContent = msg;
    el.style.background = type === 'success'
        ? 'rgba(52,211,153,0.12)'
        : 'rgba(248,113,113,0.12)';
    el.style.color = type === 'success' ? '#34d399' : '#f87171';
    el.style.border = `1px solid ${type === 'success' ? 'rgba(52,211,153,0.3)' : 'rgba(248,113,113,0.3)'}`;
}
