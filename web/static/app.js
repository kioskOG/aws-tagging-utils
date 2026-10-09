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

    // One identity lookup shared by the whole page (default region, roles, permissions)
    window.mePromise = fetch('/api/me')
      .then(r => r.ok ? r.json() : null)
      .catch(() => null);

    // POST JSON and normalise the result; errors carry the server message and details.
    async function apiPost(url, payload) {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      let data = {};
      try { data = await res.json(); } catch (e) { data = { message: `HTTP ${res.status}` }; }
      return { ok: res.ok, status: res.status, data };
    }

    function escapeHtml(s) {
      return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    // Render an API error into the results pane and toast it
    function renderApiError(title, data) {
      const details = data.details || data.violations || [];
      let html = `<div class="section-title">${escapeHtml(title)}</div>
        <div class="card" style="border-color: var(--danger);">
          <div style="color:var(--danger); font-weight:700">⚠ ${escapeHtml(data.message || 'Request failed')}</div>`;
      if (Array.isArray(details) && details.length) {
        html += `<table style="margin-top:12px; font-size:0.8rem; text-align:left;">
          <thead><tr><th>Tag</th><th>Violation</th><th>Expected</th><th>Actual</th></tr></thead><tbody>
          ${details.map(v => `<tr><td style="color:var(--danger)">${escapeHtml(v.tag)}</td><td>${escapeHtml(v.type)}</td>
            <td>${escapeHtml(v.expected || '')}</td><td>${escapeHtml(v.actual || '')}</td></tr>`).join('')}
          </tbody></table>`;
      }
      if (data.request_id) html += `<div class="chart-sub" style="margin-top:10px">Request ID: ${escapeHtml(data.request_id)}</div>`;
      html += `</div>`;
      $('#result-content').innerHTML = html;
      $('#output').textContent = JSON.stringify(data, null, 2);
      $('#toggle-json').style.display = 'block';
      toast(data.message || 'Request failed', 'error');
    }

    const state = {
      defaultRegion: 'us-east-1',
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
        this.selected = defaultAll ? AWS_REGIONS.map(r => r.id) : [state.defaultRegion];
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
                               style="font-size: 0.85rem;"
                               onclick="event.stopPropagation()"
                               oninput="regionPickers['${this.tabKey}'].filterList(this.value)">
                        <div style="display: flex; gap: 8px;">
                            <button class="btn-ghost" style="flex:1; justify-content:center" onclick="event.stopPropagation(); regionPickers['${this.tabKey}'].selectAll(true)">All</button>
                            <button class="btn-ghost" style="flex:1; justify-content:center" onclick="event.stopPropagation(); regionPickers['${this.tabKey}'].selectAll(false)">Clear</button>
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
        const me = await window.mePromise;
        if (me && me.default_region) state.defaultRegion = me.default_region;
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
        document.body.classList.remove('nav-open');
        window.scrollTo({ top: 0 });

        // Enterprise views
        const entViews = ['dashboard','compliance','mine','finops','security','enforcement','organization','propagation','changes','schema','cicd'];
        entViews.forEach(v => {
          const el = document.getElementById(`view-ent-${v}`);
          if (el) el.style.display = (tab === v) ? 'block' : 'none';
        });

        // Legacy tag-tools panel
        const legacyView = document.getElementById('view-ent-legacy');
        if (legacyView) legacyView.style.display = (tab === 'legacy') ? 'block' : 'none';

        // Deep link: /#compliance etc. (replaceState: no history entry per click)
        if (location.hash !== `#${tab}`) history.replaceState(null, '', `#${tab}`);
        if (tab === 'dashboard' && typeof renderTrend === 'function' && typeof ent !== 'undefined') renderTrend(ent.history);
        if (typeof onEnterpriseTab === 'function') onEnterpriseTab(tab);
      };
    });

    $$('.legacy-nav-btn').forEach(btn => {
      btn.onclick = () => {
        const tab = btn.dataset.legacyTab;
        $$('.legacy-nav-btn').forEach(b => {
          b.classList.toggle('active', b === btn);
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
        const { ok, data } = await apiPost("/api/read", payload);
        if (!ok) renderApiError('Discovery failed', data);
        else renderResults(data);
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
        const tags = Object.entries(r.Tags || {}).map(([k, v]) => `<span class="status-badge info">${escapeHtml(k)}: ${escapeHtml(v)}</span>`).join('');
        html += `<tr><td><input type="checkbox" class="result-check" data-idx="${idx}"></td><td class="name-cell">${escapeHtml(r.Name || '---')}</td><td><span class="status-badge">${escapeHtml(r.Region || region)}</span></td><td class="arn-cell">${escapeHtml(r.ResourceARN)} <span class="help-icon copy-arn" style="cursor:pointer; background:none" data-arn="${escapeHtml(r.ResourceARN)}">📋</span></td><td>${tags}</td></tr>`;
      });

      html += `</tbody></table></div>`;
      container.innerHTML = html;
      container.querySelectorAll('.copy-arn').forEach(el => el.onclick = () => {
        navigator.clipboard.writeText(el.dataset.arn); toast('ARN Copied', 'success');
      });

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
        const { ok, data } = await apiPost("/api/write", payload);
        if (!ok) renderApiError('Tag write failed', data);
        else renderWriteResults(data, arns);
      } catch (e) {
        alert("Batch tagging failed: " + e.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = 'Commit Batch Changes';
      }
    };

    function renderWriteResults(data, submittedArns) {
      const container = $('#result-content');
      const output = $('#output');
      output.textContent = JSON.stringify(data, null, 2);
      $('#toggle-json').style.display = 'block';

      // 200 → {count}; 207 → {details: {tagged_count, failed_resources}}
      const failed = (data.details && data.details.failed_resources) || {};
      const successArns = (submittedArns || []).map(a => a.trim()).filter(a => a && !failed[a]);
      data.count = data.count ?? (data.details && data.details.tagged_count) ?? successArns.length;
      const failedArns = Object.keys(failed);

      let html = `<div class="section-title">Batch Execution Summary</div>`;

      if (successArns.length) {
        html += `<div class="card" style="border-color: var(--success); margin-bottom:1.5rem;"><div style="color:var(--success); font-weight:700">✓ Successful Operations (${successArns.length})</div><div style="font-size:0.75rem; margin-top:10px; color:var(--muted); line-height:1.4">${successArns.map(escapeHtml).join('<br>')}</div></div>`;
      }

      if (failedArns.length) {
        html += `<div class="card" style="border-color: var(--danger);"><div style="color:var(--danger); font-weight:700">⚠ Partial Failures (${failedArns.length})</div><table style="margin-top:12px; font-size:0.8rem;">${failedArns.map(a => `<tr><td class="arn-cell">${escapeHtml(a)}</td><td style="color:var(--danger)">${escapeHtml(failed[a].ErrorMessage || 'Access Denied / Not Found')}</td></tr>`).join('')}</table></div>`;
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
        const { ok, data } = await apiPost("/api/gov", payload);
        if (!ok) renderApiError('Auto-tagging scan failed', data);
        else renderGovResults(data);
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
          <h4 style="margin: 0 0 10px 0; font-size: 0.9rem; font-weight: 600;">Region: <code>${reg}</code></h4>`;

        if (tagged.length) {
          html += `<div class="card" style="border-color: color-mix(in srgb, var(--success) 35%, transparent); margin-bottom:1rem; padding: 1rem; background: var(--success-soft);">
            <div style="color:var(--success); font-weight:700; margin-bottom: 8px;">✓ Automatically Tagged (${tagged.length})</div>
            <div style="font-family: var(--mono); font-size:0.75rem; color: var(--text-2); line-height:1.6; word-break: break-all;">
              ${tagged.join('<br>')}
            </div>
          </div>`;
        }

        if (failedArns.length) {
          html += `<div class="card" style="border-color: color-mix(in srgb, var(--danger) 35%, transparent); padding: 1rem; background: var(--danger-soft);">
            <div style="color:var(--danger); font-weight:700; margin-bottom: 8px;">⚠ Tagging Failed (${failedArns.length})</div>
            <table style="width: 100%; border: none; background: transparent;">
              ${failedArns.map(a => `
                <tr>
                  <td style="padding: 8px 0; font-family: var(--mono); font-size: 0.75rem; color: var(--text-2); word-break: break-all; border: none;">${a}</td>
                  <td style="padding: 8px 0 8px 12px; font-size: 0.8rem; color: var(--danger); border: none;">${failed[a].ErrorMessage || 'Access Denied / Not Found'}</td>
                </tr>
              `).join('')}
            </table>
          </div>`;
        }

        if (!tagged.length && !failedArns.length) {
          html += `<div style="font-size: 0.85rem; color: var(--muted); padding: 12px; background: var(--surface-2); border-radius: 8px; border: 1px solid var(--border);">No untagged resources found or identified.</div>`;
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
      el.innerHTML = `<span style="font-size:1.1rem">${icons[type] || 'ℹ'}</span><span>${escapeHtml(msg)}</span>`;
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
        const { ok, data } = await apiPost('/api/report', payload);
        if (!ok) { renderApiError('Compliance report failed', data); return; }
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

      // The Compliance tab is refreshed from the cache by loadEnterpriseData() after the report.

      const col = score > 80 ? 'var(--success)' : score > 50 ? 'var(--warning)' : 'var(--danger)';
      const circ = 2 * Math.PI * 44;
      const dash = (score / 100) * circ;
      let html = `
        <div class="score-ring">
          <div class="ring-wrap">
            <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="44" fill="none" style="stroke:var(--surface-3)" stroke-width="8"/><circle cx="50" cy="50" r="44" fill="none" style="stroke:${col}" stroke-width="8" stroke-dasharray="${dash} ${circ}" stroke-linecap="round"/></svg>
            <div class="ring-label"><span class="ring-pct" style="color:${col}">${score}%</span><span class="ring-sub">Compliant</span></div>
          </div>
          <div class="score-meta">
            <div style="display:flex; justify-content:space-between; align-items:flex-start">
              <h3 style="margin:0">Compliance scorecard</h3>
              <button class="btn btn-primary" onclick="transferReportToBatch()" style="width:auto; padding:7px 14px; font-size:0.82rem">Send to batch fix</button>
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
            html += `<tr><td><input type="checkbox" class="report-res-check" data-arn="${escapeHtml(r.ResourceARN)}"></td><td class="arn-cell">${escapeHtml(r.ResourceARN)}</td><td>${(r.Violations || []).map(v => `<span class="status-badge err" title="${escapeHtml(v.type)}">${escapeHtml(v.tag)}</span>`).join('')}</td></tr>`;
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
      if (typeof applyPermissions === 'function') applyPermissions();
    }

    async function runSync(dryRun) {
      const btn = document.getElementById(dryRun ? 'btn-sync-preview' : 'btn-sync');
      const parent = document.getElementById('sync-vpc-id').value.trim();
      if (!parent) return toast('Parent ID, name or ARN is required.', 'error');
      const rule = document.getElementById('sync-rule').value;
      const reg = (state.regions['sync'] || []).filter(r => r !== 'all')[0] || state.defaultRegion;
      const label = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<div class="loading-spinner"></div>';
      try {
        const { ok, data } = await apiPost('/api/sync', {
          action: 'propagate', rule, parent, region: reg,
          overwrite: document.getElementById('sync-overwrite').checked, dry_run: dryRun,
        });
        if (!ok) { renderApiError('Tag propagation failed', data); return; }
        document.getElementById('output').textContent = JSON.stringify(data, null, 2);
        document.getElementById('toggle-json').style.display = 'block';
        renderSyncResults(data, parent, dryRun);
      } catch (e) { toast('Sync failed: ' + e.message, 'error'); }
      finally { btn.disabled = false; btn.innerHTML = label; }
    }
    document.getElementById('btn-sync').onclick = () => runSync(false);
    document.getElementById('btn-sync-preview').onclick = () => runSync(true);

    function renderSyncResults(data, parent, dryRun) {
      const findings = data.findings || [];
      const tagFindings = findings.filter(f => f.kind === 'tags');
      const configFindings = findings.filter(f => f.kind === 'config');
      let html = `<div class="section-title" style="margin-bottom:1rem">${dryRun ? 'Preview' : 'Result'} — ${escapeHtml(data.rule)} <code>${escapeHtml(parent)}</code></div>`;
      if (!findings.length) {
        html += `<div class="card" style="color:var(--success)">✓ ${escapeHtml(data.message || 'Every child already carries the parent\'s tags.')}</div>`;
      } else {
        html += `<div class="table-container"><table class="preview-table"><thead><tr><th>Child</th><th>Missing</th><th>Mismatched</th></tr></thead><tbody>
          ${tagFindings.map(f => `<tr><td class="arn-cell">${escapeHtml(f.child_arn)}<div class="chart-sub">${escapeHtml(f.child_type)}</div></td>
            <td>${Object.entries(f.missing || {}).map(([k, v]) => `<span class="tag-chip diff-add"><b>${escapeHtml(k)}</b> ${escapeHtml(v)}</span>`).join('') || '-'}</td>
            <td>${Object.entries(f.mismatched || {}).map(([k, v]) => `<span class="tag-chip diff-change"><b>${escapeHtml(k)}</b> ${escapeHtml(v.actual)} → ${escapeHtml(v.expected)}</span>`).join('') || '-'}</td></tr>`).join('')}
          ${configFindings.map(f => `<tr><td colspan="3"><span class="status-badge warn">${escapeHtml(f.issue)}</span> ${escapeHtml(f.detail)}</td></tr>`).join('')}
        </tbody></table></div>`;
      }
      if (!dryRun && data.change_set_id) {
        html += `<div class="card" style="margin-top:1rem">Change set <code>${escapeHtml(data.change_set_id)}</code> — ${escapeHtml(data.status)}.
          <button class="btn-ghost" data-perm="modify_tags" onclick="undoChangeSet('${escapeHtml(data.change_set_id)}')">Undo</button></div>`;
      }
      if (data.errors && data.errors.length) {
        html += `<div style="margin-top:1rem;padding:1rem;background:var(--danger-soft);border-radius:10px"><div style="color:var(--danger);font-weight:700;margin-bottom:8px">⚠ Errors</div><div style="font-size:0.8rem;color:var(--danger)">${data.errors.map(escapeHtml).join('<br>')}</div></div>`;
      }
      document.getElementById('result-content').innerHTML = html;
      const n = (data.updated_resources || []).length;
      toast(dryRun ? `${tagFindings.length} children would change` : `Propagated tags to ${n} resources.`, 'success');
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
// ENTERPRISE DASHBOARD
// -----------------------------------------------------------------------------

const ent = {
    me: null,
    schemaByTag: {},
    resources: [],
    filtered: [],
    page: 0,
    history: [],
    polling: null,
    autoRefreshTried: false,
    selected: new Set(),
    mine: null,
    lbDim: 'team',
    prop: { run: null, selected: new Set(), polling: null },
};
const COMPLIANCE_PAGE_SIZE = 50;
const FIXABLE = new Set(['MISSING_REQUIRED', 'INVALID_VALUE', 'INVALID_FORMAT']);

const escHtml = escapeHtml;

function fmtAgo(epochSeconds) {
    if (!epochSeconds) return '';
    const s = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
    const unit = (n, word) => `${n} ${word}${n === 1 ? '' : 's'} ago`;
    if (s < 60) return unit(s, 'second');
    if (s < 3600) return unit(Math.floor(s / 60), 'minute');
    if (s < 86400) return unit(Math.floor(s / 3600), 'hour');
    return unit(Math.floor(s / 86400), 'day');
}

function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d) ? String(iso) : d.toLocaleString();
}

function fmtMoney(n) {
    return n != null && !isNaN(n) ? '$' + Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }) : '$--';
}

function setText(sel, value) {
    const el = $(sel);
    if (el) el.innerText = value;
}

async function safeFetch(url, opts) {
    try {
        const resp = await fetch(url, opts);
        let data = {};
        try { data = await resp.json(); } catch (e) { data = { message: `HTTP ${resp.status}` }; }
        if (!resp.ok) return { _error: true, _status: resp.status, ...data };
        return { _status: resp.status, ...data };
    } catch (e) {
        return { _error: true, _status: 0, message: `Network error: ${e.message}` };
    }
}

function showUnavailable(tbodySel, colSpan, message) {
    const el = $(tbodySel);
    if (el) el.innerHTML = `<tr><td colspan="${colSpan}" style="text-align:center;color:var(--muted);padding:2rem;font-size:0.85rem">${escHtml(message)}</td></tr>`;
}

// ── Identity & permissions (server-side RBAC is authoritative; this only hides what would 403) ──

async function loadIdentity() {
    ent.me = await window.mePromise;
    const badge = $('#identity-badge');
    if (!badge) return;
    if (!ent.me) {
        badge.innerHTML = `<span class="status-badge err">Not signed in</span>`;
        return;
    }
    const roles = (ent.me.roles || []).map(r => `<span class="status-badge info">${escHtml(r)}</span>`).join('')
        || `<span class="status-badge err" title="No recognised role: read-only access">no role</span>`;
    badge.innerHTML = `<span class="identity-user">${escHtml(ent.me.user_id)}</span>${roles}
        <span class="chart-sub" title="Application version">v${escHtml(ent.me.version || '')}</span>`;
    applyPermissions();
}

function can(permission) {
    return !!(ent.me && ent.me.permissions && ent.me.permissions[permission]);
}

function applyPermissions() {
    const legacyWriteButtons = ['#btn-write', '#btn-gov', '#btn-sync'];
    legacyWriteButtons.forEach(sel => { const el = $(sel); if (el) el.dataset.perm = 'modify_tags'; });
    $$('[data-perm]').forEach(el => {
        const allowed = ent.me === null ? true : can(el.dataset.perm);
        el.disabled = !allowed || el.dataset.empty === '1';
        el.style.opacity = allowed ? '' : '0.35';
        el.title = allowed ? (el.dataset.title || '') : 'Your role does not allow this action';
    });
}
const updateRBAC = applyPermissions;  // backwards compatibility

// ── Tooltip ──────────────────────────────────────────────────────────

function showTip(html, x, y) {
    const tip = $('#chart-tooltip');
    if (!tip) return;
    tip.innerHTML = html;
    tip.style.display = 'block';
    const pad = 12;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + pad, top = y + pad;
    if (left + w > window.innerWidth - 8) left = x - w - pad;
    if (top + h > window.innerHeight - 8) top = y - h - pad;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
}

function hideTip() {
    const tip = $('#chart-tooltip');
    if (tip) tip.style.display = 'none';
}

// ── Loaders ──────────────────────────────────────────────────────────

let _loadingEnterprise = null;
async function loadEnterpriseData() {
    if (_loadingEnterprise) return _loadingEnterprise;
    _loadingEnterprise = (async () => {
        if (!ent.me) await loadIdentity();
        await loadSchema();
        await Promise.allSettled([
            loadDashboard(), loadCompliance(), loadHistory(), loadFinops(),
            loadRemediation(), loadExemptions(), loadAudit(), loadCoverage(),
            loadLeaderboard(), loadOrganization(), loadChanges(), loadPropagation(),
        ]);
        if (ent.mine !== null) loadMine();
        renderStaticUnavailable();
        applyPermissions();
        await checkRefreshStatus();
    })();
    try { await _loadingEnterprise; } finally { _loadingEnterprise = null; }
}

async function loadDashboard() {
    const d = await safeFetch('/api/dashboard');
    if (d._error) {
        setText('#dash-compliance', 'N/A');
        setText('#meta-status-text', d.message || 'Dashboard unavailable');
        return;
    }
    setText('#dash-compliance', d.compliance_pct + '%');
    setText('#dash-violations', (d.active_violations || 0).toLocaleString());
    setText('#dash-protected', (d.protected_violations || 0).toLocaleString());
    setText('#dash-total-res', (d.total_resources || 0).toLocaleString());
    setText('#dash-comp-res', (d.compliant_resources || 0).toLocaleString());
    setText('#dash-noncomp-res', (d.non_compliant_resources || 0).toLocaleString());
    if (ent.me && ent.me.features && ent.me.features.finops && d.total_spend) {
        setText('#dash-spend', fmtMoney(d.total_spend));
        setText('#dash-allocation', d.allocation_pct + '%');
    } else {
        setText('#dash-spend', '$--');
        setText('#dash-allocation', '--%');
    }
    if ($('#stat-discovered')) setText('#stat-discovered', (d.total_resources || 0).toLocaleString());
    if ($('#stat-score')) setText('#stat-score', d.compliance_pct + '%');

    const banner = $('#dash-failed-regions');
    if (banner) {
        const failed = d.failed_regions || [];
        banner.style.display = failed.length ? 'block' : 'none';
        banner.textContent = failed.length
            ? `⚠ The latest scan could not read ${failed.length} region(s): ${failed.join(', ')}. Their resources are missing from these numbers.`
            : '';
    }

    const meta = d._meta || {};
    if (meta.generated_at) {
        setText('#meta-last-updated', `Last scan: ${fmtAgo(meta.generated_at)}`);
        setText('#meta-status-text', 'Showing cached results');
    } else {
        setText('#meta-last-updated', '');
        setText('#meta-status-text', meta.status === 'No data' ? 'No compliance scan yet' : (meta.status || ''));
    }

    renderTopViolations(d.top_violations || []);
    renderByService(d.by_service || []);
}

async function loadSchema() {
    const schema = await safeFetch('/api/schema');
    const tbody = $('#schema-table-body');
    if (schema._error) {
        if (tbody) showUnavailable('#schema-table-body', 7, schema.message || 'Failed to load schema.');
        return;
    }
    const rules = Array.isArray(schema) ? schema : Object.values(schema).filter(v => v && v.tag);
    ent.schemaByTag = {};
    rules.forEach(r => { ent.schemaByTag[r.tag] = r; });
    if (!tbody) return;
    tbody.innerHTML = rules.map(rule => `<tr>
        <td class="name-cell">${escHtml(rule.tag)}${(rule.aliases || []).length ? `<div class="chart-sub">aliases: ${rule.aliases.map(escHtml).join(', ')}</div>` : ''}</td>
        <td>${rule.required ? '✅ Required' : '-'}</td>
        <td>${rule.protected ? '🔒' : '-'}</td>
        <td>${rule.finops ? '💰' : '-'}</td>
        <td>${rule.allowed_values.length ? rule.allowed_values.map(escHtml).join(', ') : '*'}</td>
        <td><code style="font-size:0.75rem">${escHtml(rule.regex || '-')}</code></td>
        <td><span style="font-size: 0.8rem; color: var(--muted)">${escHtml(rule.description || '')}</span></td>
    </tr>`).join('');
}

async function loadCompliance() {
    const comp = await safeFetch('/api/compliance?include_exemptions=1');
    if (comp._error) {
        ent.resources = [];
        showUnavailable('#compliance-table-body', 9, comp.message || 'Failed to load compliance data.');
        return;
    }
    ent.resources = comp.resources || [];
    populateComplianceFilters();
    filterComplianceTable(true);
}

async function loadHistory() {
    const h = await safeFetch('/api/compliance/history?limit=30');
    ent.history = h._error ? [] : (h.scans || []);
    renderTrend(ent.history);
}

async function loadFinops() {
    const ids = ['#finops-total', '#finops-tagged', '#finops-untagged', '#finops-unallocated'];
    const features = (ent.me && ent.me.features) || {};
    if (features.finops === false) {
        ids.forEach(id => setText(id, 'Off'));
        return;
    }
    if (ent.me && !can('view_finops')) {
        ids.forEach(id => setText(id, '🔒'));
        const tags = $('#finops-tags-list');
        if (tags) tags.innerHTML = `<span class="chart-sub">FinOps data requires the FinOps or PlatformAdmin role.</span>`;
        return;
    }
    const f = await safeFetch('/api/finops');
    if (f._error || f.error === 'not_ready') {
        ids.forEach(id => setText(id, f.error === 'not_ready' ? '…' : 'N/A'));
        const tags = $('#finops-tags-list');
        if (tags) tags.innerHTML = `<span class="chart-sub">${escHtml(f.message || 'FinOps data unavailable.')}</span>`;
        return;
    }
    setText('#finops-total', fmtMoney(f.TotalSpend));
    setText('#finops-tagged', fmtMoney(f.TaggedSpend));
    setText('#finops-untagged', fmtMoney(f.UntaggedSpend));
    setText('#finops-unallocated', fmtMoney(f.PotentiallyUnallocated));
    const tagsList = $('#finops-tags-list');
    if (tagsList) {
        tagsList.innerHTML = (f.CostAllocationTags || []).length
            ? f.CostAllocationTags.map(t => `<span class="status-badge info">💰 ${escHtml(t)}</span>`).join('')
            : `<span class="chart-sub">No schema tag is marked <code>finops.cost_allocation: true</code>.</span>`;
    }
}

const REMEDIATION_BADGE = {
    COMPLETED: 'ok', SKIPPED_EXEMPT: 'info', PENDING: 'warn', IN_PROGRESS: 'warn',
    FAILED_RETRYABLE: 'warn', FAILED: 'err',
};

async function loadRemediation() {
    const rem = await safeFetch('/api/remediation?limit=100');
    if (rem._error) {
        setText('#dash-pending', 'N/A');
        showUnavailable('#remediation-table-body', 7, `Remediation state unavailable (DynamoDB state table): ${rem.message || 'not reachable'}`);
        return;
    }
    setText('#dash-pending', (rem.pending || 0).toLocaleString());
    const actions = rem.actions || [];
    if (!actions.length) {
        showUnavailable('#remediation-table-body', 7, 'No remediation actions yet.');
        return;
    }
    $('#remediation-table-body').innerHTML = actions.map(a => {
        const tags = Object.entries(a.requested_tags || {})
            .map(([k, v]) => `<span class="tag-chip"><b>${escHtml(k)}</b> ${escHtml(v)}</span>`).join('');
        const last = a.last_error_code ? `error: ${a.last_error_code}` : (a.updated_at ? fmtDate(a.updated_at) : '');
        return `<tr>
            <td class="arn-cell">${escHtml(a.resource_arn)}</td>
            <td>${escHtml(a.account_id || '')}</td>
            <td>${tags || '-'}</td>
            <td style="font-size:0.8rem">${escHtml(fmtDate(a.created_at))}</td>
            <td>-</td>
            <td><span class="status-badge ${REMEDIATION_BADGE[a.status] || 'info'}">${escHtml(a.status)}</span>
                <div class="chart-sub">attempts: ${escHtml(a.attempt_count ?? 0)}</div></td>
            <td><span style="font-size:0.8rem">${escHtml(last)}</span></td>
        </tr>`;
    }).join('');
}

function exemptionScope(ex) {
    if (ex.resource_id) return ex.resource_id;
    return [
        ex.account_id && `account ${ex.account_id}`,
        ex.resource_type && `type ${ex.resource_type}`,
        ex.environment && `env ${ex.environment}`,
    ].filter(Boolean).join(' · ') || '-';
}

async function loadExemptions() {
    const ex = await safeFetch('/api/exemptions');
    if (ex._error) {
        setText('#dash-exemptions', 'N/A');
        showUnavailable('#exemptions-table-body', 6, `Exemptions unavailable (DynamoDB state table): ${ex.message || 'not reachable'}`);
        return;
    }
    const list = ex.exemptions || [];
    setText('#dash-exemptions', list.length.toLocaleString());
    if (!list.length) {
        showUnavailable('#exemptions-table-body', 6, 'No active exemptions.');
        return;
    }
    $('#exemptions-table-body').innerHTML = list.map(r => `<tr>
        <td><span class="arn-cell">${escHtml(exemptionScope(r))}</span></td>
        <td>${escHtml(r.reason || '-')}</td>
        <td>${escHtml(r.created_by || '-')}</td>
        <td style="font-size:0.8rem">${r.expires_at ? escHtml(fmtDate(r.expires_at)) : '<span style="color:var(--warning)">Never</span>'}</td>
        <td><span class="status-badge ok">${escHtml(r.status)}</span></td>
        <td><button class="btn-ghost" data-perm="manage_exemptions" data-action="revoke-exemption" data-id="${escHtml(r.id)}">Revoke</button></td>
    </tr>`).join('');
}

async function revokeExemption(id) {
    if (!confirm('Revoke this exemption? The resource will be evaluated normally again.')) return;
    const res = await safeFetch(`/api/exemptions/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (res._error) return toast(res.message || 'Failed to revoke exemption', 'error');
    toast('Exemption revoked', 'success');
    loadEnterpriseData();
}

async function loadAudit() {
    const aud = await safeFetch('/api/audit?limit=100');
    if (aud._error) {
        showUnavailable('#audit-table-body', 6, aud.message || 'Audit log unavailable.');
        return;
    }
    const events = aud.audit_events || [];
    if (!events.length) {
        showUnavailable('#audit-table-body', 6, 'No audit events yet. Tag writes, remediations and exemption changes are recorded here.');
        return;
    }
    $('#audit-table-body').innerHTML = events.map(e => {
        const d = e.details || {};
        let reason = d.reason || d.error_code || '';
        if (!reason && d.tags_requested) {
            reason = Object.entries(d.tags_requested).map(([k, v]) => `${k}=${v}`).join(', ');
        }
        if (!reason && d.exemption_id) reason = `exemption ${d.exemption_id}`;
        return `<tr>
            <td style="font-size:0.8rem">${escHtml(fmtDate(e.timestamp))}</td>
            <td><span class="status-badge info">${escHtml(e.action)}</span></td>
            <td class="arn-cell">${escHtml(e.resource_arn || '-')}</td>
            <td class="name-cell">${escHtml(e.actor || '-')}</td>
            <td><span class="status-badge ${e.result === 'SUCCESS' || e.result === 'COMPLETED' ? 'ok' : 'err'}">${escHtml(e.result)}</span></td>
            <td><span style="font-size:0.8rem">${escHtml(reason)}</span></td>
        </tr>`;
    }).join('');
}

function renderStaticUnavailable() {
    // These areas need AWS Config / Organizations integrations that aren't configured.
    ['#dash-drift', '#dash-reverted', '#dash-pending-sec', '#sec-protected', '#sec-unauth', '#sec-reverted', '#sec-pending']
        .forEach(id => setText(id, 'N/A'));
    showUnavailable('#drift-table-body', 8, 'Protected-tag drift detection requires AWS Config and the DynamoDB state table.');
    showUnavailable('#cicd-table-body', 6, 'CI/CD results come from the CLI: ./aws-tagging-utils validate <dir> --format sarif');
}

// ── Charts ───────────────────────────────────────────────────────────

function renderTrend(scans) {
    const host = $('#dash-trend');
    if (!host) return;
    renderTrendTable(scans);
    if (!scans.length) {
        host.innerHTML = '<div class="chart-empty">No scans yet. Run a compliance refresh to start the trend.</div>';
        return;
    }
    const W = Math.max(320, host.clientWidth || 800), H = 220;
    const m = { top: 16, right: 52, bottom: 28, left: 40 };
    const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
    const n = scans.length;
    const x = i => m.left + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
    const y = v => m.top + ih - (Math.max(0, Math.min(100, v)) / 100) * ih;
    const pts = scans.map((s, i) => [x(i), y(Number(s.compliance_score) || 0)]);

    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img"
        aria-label="Compliance score trend over the last ${n} scans">`;
    [0, 50, 100].forEach(t => {
        svg += `<line x1="${m.left}" x2="${W - m.right}" y1="${y(t)}" y2="${y(t)}" stroke="var(--chart-grid)" stroke-width="1"/>
                <text class="chart-axis-text" x="${m.left - 8}" y="${y(t) + 4}" text-anchor="end">${t}%</text>`;
    });
    const fmtDay = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); };
    svg += `<text class="chart-axis-text" x="${pts[0][0]}" y="${H - 6}" text-anchor="${n === 1 ? 'middle' : 'start'}">${escHtml(fmtDay(scans[0].timestamp))}</text>`;
    if (n > 1) svg += `<text class="chart-axis-text" x="${pts[n - 1][0]}" y="${H - 6}" text-anchor="end">${escHtml(fmtDay(scans[n - 1].timestamp))}</text>`;

    if (n > 1) {
        const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
        const area = `${line} L${pts[n - 1][0].toFixed(1)},${y(0)} L${pts[0][0].toFixed(1)},${y(0)} Z`;
        svg += `<path d="${area}" fill="var(--chart-series-wash)" stroke="none"/>
                <path d="${line}" fill="none" stroke="var(--chart-series)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    }
    const last = pts[n - 1];
    svg += `<line id="trend-crosshair" x1="0" x2="0" y1="${m.top}" y2="${m.top + ih}" stroke="var(--muted)" stroke-width="1" style="display:none"/>
            <circle id="trend-hover-dot" r="5" fill="var(--chart-series)" stroke="var(--chart-surface)" stroke-width="2" style="display:none"/>
            <circle cx="${last[0]}" cy="${last[1]}" r="5" fill="var(--chart-series)" stroke="var(--chart-surface)" stroke-width="2"/>
            <text class="chart-label-text" x="${last[0] + 10}" y="${last[1] + 4}">${escHtml(scans[n - 1].compliance_score)}%</text>`;
    // Hit bands wider than the marks, one per scan
    const band = n === 1 ? iw : iw / (n - 1);
    pts.forEach((p, i) => {
        svg += `<rect class="trend-hit" data-i="${i}" x="${p[0] - band / 2}" y="${m.top}" width="${band}" height="${ih}" fill="transparent"/>`;
    });
    svg += `</svg>`;
    host.innerHTML = svg;

    const cross = host.querySelector('#trend-crosshair');
    const dot = host.querySelector('#trend-hover-dot');
    host.querySelectorAll('.trend-hit').forEach(r => {
        r.addEventListener('mousemove', ev => {
            const i = Number(r.dataset.i), s = scans[i], p = pts[i];
            cross.setAttribute('x1', p[0]); cross.setAttribute('x2', p[0]); cross.style.display = '';
            dot.setAttribute('cx', p[0]); dot.setAttribute('cy', p[1]); dot.style.display = '';
            showTip(`<b>${escHtml(s.compliance_score)}%</b> compliant<br>
                <span class="tt-muted">${escHtml(fmtDate(s.timestamp))}</span><br>
                ${escHtml(s.compliant)} / ${escHtml(s.total_resources)} resources ·
                <span class="tt-muted">${escHtml((s.regions || []).join(', '))}</span>`, ev.clientX, ev.clientY);
        });
        r.addEventListener('mouseleave', () => { cross.style.display = 'none'; dot.style.display = 'none'; hideTip(); });
    });
}

function renderTrendTable(scans) {
    const host = $('#dash-trend-table');
    if (!host) return;
    host.innerHTML = scans.length ? `<table class="trend-table"><thead><tr>
        <th>Scan time</th><th>Regions</th><th>Resources</th><th>Compliant</th><th>Score</th></tr></thead><tbody>
        ${scans.slice().reverse().map(s => `<tr><td>${escHtml(fmtDate(s.timestamp))}</td><td>${escHtml((s.regions || []).join(', '))}</td>
        <td>${escHtml(s.total_resources)}</td><td>${escHtml(s.compliant)}</td><td>${escHtml(s.compliance_score)}%</td></tr>`).join('')}
        </tbody></table>` : '';
}

function toggleTrendTable() {
    const t = $('#dash-trend-table');
    const btn = $('#trend-table-toggle');
    const show = t.style.display === 'none';
    t.style.display = show ? 'block' : 'none';
    btn.textContent = show ? 'Hide table' : 'View as table';
}

function renderHBars(hostSel, rows, emptyText) {
    const host = $(hostSel);
    if (!host) return;
    if (!rows.length) {
        host.innerHTML = `<div class="chart-empty">${escHtml(emptyText)}</div>`;
        return;
    }
    host.innerHTML = `<div class="hbar-list">${rows.map((r, i) => `
        <div class="hbar-row" tabindex="0" data-i="${i}">
            <div class="hbar-name" title="${escHtml(r.name)}">${r.label}</div>
            <div class="hbar-track"><div class="hbar-fill" style="width:${Math.max(0, Math.min(100, r.pct)).toFixed(1)}%"></div></div>
            <div class="hbar-value">${escHtml(r.value)}</div>
        </div>`).join('')}</div>`;
    host.querySelectorAll('.hbar-row').forEach(el => {
        const r = rows[Number(el.dataset.i)];
        el.addEventListener('mousemove', ev => showTip(r.tip, ev.clientX, ev.clientY));
        el.addEventListener('mouseleave', hideTip);
        el.addEventListener('focus', () => { const b = el.getBoundingClientRect(); showTip(r.tip, b.right, b.top); });
        el.addEventListener('blur', hideTip);
    });
}

const VIOLATION_LABEL = {
    MISSING_REQUIRED: 'missing', INVALID_VALUE: 'invalid value', INVALID_FORMAT: 'invalid format',
    NORMALIZATION_CONFLICT: 'conflicting aliases', UNKNOWN_TAG: 'unknown tag',
};

function renderTopViolations(list) {
    const max = Math.max(1, ...list.map(v => v.count));
    renderHBars('#dash-top-violations', list.slice(0, 8).map(v => ({
        name: `${v.tag} ${v.type}`,
        label: `${escHtml(v.tag)} <small>${escHtml(VIOLATION_LABEL[v.type] || v.type)}</small>`,
        pct: (v.count / max) * 100,
        value: v.count.toLocaleString(),
        tip: `<b>${escHtml(v.tag)}</b> · ${escHtml(VIOLATION_LABEL[v.type] || v.type)}<br>${escHtml(v.count)} resources affected`,
    })), 'No violations in the latest scan. 🎉');
}

function renderByService(list) {
    renderHBars('#dash-by-service', list.slice(0, 8).map(s => ({
        name: s.name,
        label: escHtml(s.name),
        pct: s.compliance_pct,
        value: `${s.compliance_pct}%`,
        tip: `<b>${escHtml(s.name)}</b><br>${escHtml(s.compliant)} of ${escHtml(s.total)} resources compliant`,
    })), 'No resources in the latest scan.');
}

// ── Compliance table ─────────────────────────────────────────────────

function populateComplianceFilters() {
    const fill = (sel, values, allLabel) => {
        const el = $(sel);
        if (!el) return;
        const current = el.value;
        el.innerHTML = `<option value="">${allLabel}</option>` +
            [...new Set(values)].sort().map(v => `<option value="${escHtml(v)}">${escHtml(v)}</option>`).join('');
        if ([...el.options].some(o => o.value === current)) el.value = current;
    };
    fill('#comp-filter-service', ent.resources.map(r => r.type), 'All Services');
    fill('#comp-filter-region', ent.resources.map(r => r.region), 'All Regions');
}

function filterComplianceTable(keepPage = false) {
    const status = $('#comp-filter-status') ? $('#comp-filter-status').value : '';
    const service = $('#comp-filter-service') ? $('#comp-filter-service').value : '';
    const region = $('#comp-filter-region') ? $('#comp-filter-region').value : '';
    const issue = $('#comp-filter-issue') ? $('#comp-filter-issue').value : '';
    const text = ($('#comp-filter-text') ? $('#comp-filter-text').value : '').trim().toLowerCase();

    ent.filtered = ent.resources.filter(r => {
        if (status && r.status !== status) return false;
        if (service && r.type !== service) return false;
        if (region && r.region !== region) return false;
        if (issue === 'EXEMPT' && r.exemption_state !== 'EXEMPT') return false;
        if (issue === 'UNKNOWN_TAG' && !(r.warnings || []).some(w => w.type === 'UNKNOWN_TAG')) return false;
        if (issue === 'NEVER_TAGGED' && !r.never_tagged) return false;
        if (issue && !['EXEMPT', 'UNKNOWN_TAG', 'NEVER_TAGGED'].includes(issue) && !(r.violations || []).some(v => v.type === issue)) return false;
        if (text) {
            const hay = [r.id, r.account, r.region, ...Object.entries(r.tags || {}).map(([k, v]) => `${k}=${v}`)].join(' ').toLowerCase();
            if (!hay.includes(text)) return false;
        }
        return true;
    });
    const maxPage = Math.max(0, Math.ceil(ent.filtered.length / COMPLIANCE_PAGE_SIZE) - 1);
    renderCompliancePage(keepPage ? Math.min(ent.page, maxPage) : 0);
}

function renderIssuesCell(violations, warnings) {
    const parts = (violations || []).map(v =>
        `<div title="${escHtml(v.expected || '')}"><span class="status-badge err">${escHtml(VIOLATION_LABEL[v.type] || v.type)}</span> ${escHtml(v.tag)}${v.actual && v.type !== 'MISSING_REQUIRED' ? ` = ${escHtml(v.actual)}` : ''}</div>`);
    (warnings || []).forEach(w =>
        parts.push(`<div title="${escHtml(w.expected || '')}"><span class="status-badge warn">${escHtml(VIOLATION_LABEL[w.type] || w.type)}</span> ${escHtml(w.tag)}</div>`));
    return parts.length ? parts.join('') : '-';
}

function renderTagsCell(tags) {
    const entries = Object.entries(tags || {});
    if (!entries.length) return '<span style="color:var(--muted)">No tags</span>';
    return entries.map(([k, v]) => `<span class="tag-chip"><b>${escHtml(k)}</b> ${escHtml(v)}</span>`).join('');
}

function renderCompliancePage(page) {
    const tbody = $('#compliance-table-body');
    if (!tbody) return;
    const rows = ent.filtered;
    const countEl = $('#comp-result-count');

    if (!ent.resources.length) {
        showUnavailable('#compliance-table-body', 9, 'No resources found. Run a compliance refresh, or check AWS credentials and the scanned regions.');
        if (countEl) countEl.textContent = '';
        renderPagination(0, 0);
        return;
    }
    if (!rows.length) {
        showUnavailable('#compliance-table-body', 9, 'No resources match these filters.');
        if (countEl) countEl.textContent = `0 of ${ent.resources.length} resources`;
        renderPagination(0, 0);
        return;
    }

    const totalPages = Math.ceil(rows.length / COMPLIANCE_PAGE_SIZE);
    ent.page = Math.max(0, Math.min(page, totalPages - 1));
    const start = ent.page * COMPLIANCE_PAGE_SIZE;
    const slice = rows.slice(start, start + COMPLIANCE_PAGE_SIZE);
    if (countEl) {
        countEl.textContent = `Showing ${start + 1}–${start + slice.length} of ${rows.length}` +
            (rows.length !== ent.resources.length ? ` (filtered from ${ent.resources.length})` : '') + ' resources';
    }

    tbody.innerHTML = slice.map((r, k) => {
        const idx = start + k;
        const fixable = (r.violations || []).some(v => FIXABLE.has(v.type));
        const exempt = r.exemption_state === 'EXEMPT';
        const actions = [
            fixable ? `<button class="btn-ghost" data-perm="modify_tags" data-action="fix" data-idx="${idx}">Fix Tags</button>` : '',
            r.status !== 'COMPLIANT' && !exempt ? `<button class="btn-ghost" data-perm="manage_exemptions" data-action="exempt" data-idx="${idx}">Exempt</button>` : '',
        ].join('') || '<span style="color:var(--muted)">-</span>';
        const exemptCell = exempt
            ? `<span class="status-badge info">Exempt</span>${r.exemption_expires_at ? `<div class="chart-sub">until ${escHtml(new Date(r.exemption_expires_at).toLocaleDateString())}</div>` : ''}`
            : (r.exemption_state === 'Unknown' ? '<span class="chart-sub" title="The exemption store (DynamoDB) could not be read">unavailable</span>' : '-');
        const checked = ent.selected.has(r.id) ? 'checked' : '';
        return `<tr>
            <td><input type="checkbox" class="comp-check" data-arn="${escHtml(r.id)}" ${checked}></td>
            <td class="arn-cell">${escHtml(r.id)}<div class="chart-sub">${escHtml(r.region)}${r.never_tagged ? ' · <span class="status-badge badge-never" title="Found by Resource Explorer; never had a tag">never tagged</span>' : ''}</div></td>
            <td>${escHtml(r.account)}</td>
            <td><span class="status-badge info">${escHtml(r.type)}</span></td>
            <td><span class="status-badge ${r.status === 'COMPLIANT' ? 'ok' : 'err'}">${escHtml(r.status)}</span></td>
            <td>${renderTagsCell(r.tags)}</td>
            <td>${renderIssuesCell(r.violations, r.warnings)}</td>
            <td>${exemptCell}</td>
            <td style="white-space:nowrap">${actions}</td>
        </tr>`;
    }).join('');
    renderPagination(ent.page, totalPages);
    const pageBox = $('#comp-select-page');
    if (pageBox) pageBox.checked = slice.length > 0 && slice.every(r => ent.selected.has(r.id));
    updateBulkCount();
    applyPermissions();
}

// ── Bulk selection ───────────────────────────────────────────────────

function updateBulkCount() {
    const n = ent.selected.size;
    setText('#bulk-selected-count', `${n.toLocaleString()} selected`);
    const btn = $('#btn-bulk-fix');
    if (btn) btn.dataset.empty = n ? '' : '1';
    applyPermissions();
    if (btn && !n) btn.disabled = true;
}

function selectPage(on) {
    $$('#compliance-table-body .comp-check').forEach(cb => {
        cb.checked = on;
        on ? ent.selected.add(cb.dataset.arn) : ent.selected.delete(cb.dataset.arn);
    });
    updateBulkCount();
}

function selectAllFiltered(on) {
    if (on) ent.filtered.forEach(r => ent.selected.add(r.id));
    else ent.selected.clear();
    renderCompliancePage(ent.page);
}

document.addEventListener('change', (e) => {
    if (e.target.classList && e.target.classList.contains('comp-check')) {
        e.target.checked ? ent.selected.add(e.target.dataset.arn) : ent.selected.delete(e.target.dataset.arn);
        updateBulkCount();
    }
    if (e.target.classList && e.target.classList.contains('prop-check')) {
        e.target.checked ? ent.prop.selected.add(e.target.dataset.id) : ent.prop.selected.delete(e.target.dataset.id);
        setText('#prop-selected', `${ent.prop.selected.size} selected`);
    }
});

function renderPagination(page, totalPages) {
    const tbody = $('#compliance-table-body');
    let el = $('#compliance-pagination');
    if (!el && tbody) {
        el = document.createElement('div');
        el.id = 'compliance-pagination';
        el.style.cssText = 'display:flex;gap:8px;align-items:center;justify-content:flex-end;margin-top:12px;font-size:0.85rem;';
        tbody.closest('.table-container').after(el);
    }
    if (!el) return;
    if (totalPages <= 1) { el.innerHTML = ''; return; }
    el.innerHTML = `
        <button class="btn-ghost" ${page === 0 ? 'disabled style="opacity:0.3"' : ''} onclick="renderCompliancePage(${page - 1})">← Prev</button>
        <span style="color:var(--muted)">Page ${page + 1} of ${totalPages}</span>
        <button class="btn-ghost" ${page >= totalPages - 1 ? 'disabled style="opacity:0.3"' : ''} onclick="renderCompliancePage(${page + 1})">Next →</button>`;
}

// Delegated row actions (no data is interpolated into inline JS)
document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn || btn.disabled) return;
    const action = btn.dataset.action;
    if (action === 'fix') openFixTags(ent.filtered[Number(btn.dataset.idx)]);
    else if (action === 'exempt') openExempt(ent.filtered[Number(btn.dataset.idx)]);
    else if (action === 'revoke-exemption') revokeExemption(btn.dataset.id);
});

// ── Refresh orchestration ────────────────────────────────────────────

function setRefreshButton(busy) {
    const btn = $('#btn-refresh-compliance');
    if (!btn) return;
    btn.disabled = busy;
    btn.innerHTML = `<svg class="ico icon"><use href="#i-refresh"/></svg> ${busy ? 'Refreshing…' : 'Refresh'}`;
    const bar = $('#compliance-status-bar');
    if (bar) bar.classList.toggle('is-refreshing', busy);
}

async function checkRefreshStatus() {
    const s = await safeFetch('/api/compliance/status');
    if (s._error) return;
    if (s.is_refreshing) {
        setText('#meta-status-text', 'Refreshing from AWS...');
        setRefreshButton(true);
        startPolling();
    } else if (s.last_refresh_error) {
        setText('#meta-status-text', `Last refresh failed: ${s.last_refresh_error}`);
    } else if (s.is_stale && !ent.autoRefreshTried) {
        // Auto-refresh once per page load; a failing refresh must not loop forever
        ent.autoRefreshTried = true;
        triggerBackgroundRefresh();
    }
}

async function triggerBackgroundRefresh() {
    ent.autoRefreshTried = true;
    setRefreshButton(true);
    setText('#meta-status-text', 'Refreshing from AWS...');
    const res = await safeFetch('/api/compliance/refresh', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    if (res._error) {
        setText('#meta-status-text', `Refresh failed: ${res.message || 'error'}`);
        setRefreshButton(false);
        return;
    }
    startPolling();
}

function startPolling() {
    if (ent.polling) return;  // one poller at a time
    ent.polling = setInterval(async () => {
        const s = await safeFetch('/api/compliance/status');
        if (s._error || !s.is_refreshing) {
            clearInterval(ent.polling);
            ent.polling = null;
            setRefreshButton(false);
            await loadEnterpriseData();
            setText('#meta-status-text', s.last_refresh_error
                ? `Refresh failed: ${s.last_refresh_error}`
                : 'Updated just now.');
            if (s.last_refresh_error) toast(`Compliance refresh: ${s.last_refresh_error}`, 'error');
        }
    }, 2000);
}

// ── Fix Tags modal ───────────────────────────────────────────────────

function fieldId(tag) { return 'fix-tag-val-' + tag.replace(/[^a-zA-Z0-9]/g, '-'); }

function openFixTags(r) {
    const modal = $('#fix-tags-modal');
    if (!modal || !r) return;
    const issues = (r.violations || []).filter(v => FIXABLE.has(v.type));
    const tags = [...new Set(issues.map(v => v.tag))];
    modal._arn = r.id;
    modal._tags = tags;
    $('#fix-tags-arn').textContent = r.id;
    $('#fix-tags-region').textContent = r.region;

    $('#fix-tags-fields').innerHTML = tags.map(tag => {
        const rule = ent.schemaByTag[tag] || {};
        const v = issues.find(i => i.tag === tag);
        const current = (r.tags || {})[tag] || '';
        const hint = v.type === 'MISSING_REQUIRED' ? 'missing' : `invalid: ${current}`;
        const input = (rule.allowed_values || []).length
            ? `<select id="${fieldId(tag)}" style="width:100%">
                 <option value="">Select…</option>
                 ${rule.allowed_values.map(a => `<option value="${escHtml(a)}">${escHtml(a)}</option>`).join('')}
               </select>`
            : `<input id="${fieldId(tag)}" type="text" value="${escHtml(v.type === 'MISSING_REQUIRED' ? '' : current)}"
                 placeholder="${escHtml(rule.regex ? 'must match ' + rule.regex : 'Value for ' + tag)}"
                 ${rule.regex ? `data-pattern="${escHtml(rule.regex)}"` : ''} style="width:100%;font-size:0.9rem;">`;
        return `<div>
            <label style="font-size:0.8rem;color:var(--muted);display:block;margin-bottom:4px;">
              ${escHtml(tag)} <span style="color:var(--danger)">*</span> <span class="chart-sub">(${escHtml(hint)})</span></label>
            ${input}
            ${rule.description ? `<div class="chart-sub">${escHtml(rule.description)}</div>` : ''}
        </div>`;
    }).join('');

    const statusEl = $('#fix-tags-status');
    statusEl.style.display = 'none';
    const submit = $('#fix-tags-submit');
    submit.disabled = false;
    submit.textContent = 'Apply Tags';
    submit.onclick = fixTagsConfirm;
    modal.style.display = 'flex';
    const first = $('#fix-tags-fields').querySelector('input,select');
    if (first) setTimeout(() => first.focus(), 50);
    modal.onclick = (e) => { if (e.target === modal) closeFixTagsModal(); };
}

function closeFixTagsModal() {
    const modal = $('#fix-tags-modal');
    if (modal) modal.style.display = 'none';
}

async function fixTagsConfirm() {
    const modal = $('#fix-tags-modal');
    const tags = {};
    let valid = true;
    (modal._tags || []).forEach(tag => {
        const el = document.getElementById(fieldId(tag));
        const val = el ? el.value.trim() : '';
        let ok = !!val;
        if (ok && el.dataset.pattern) {
            try { ok = new RegExp(el.dataset.pattern).test(val); } catch (e) { /* server validates */ }
        }
        el.style.border = ok ? '' : '1px solid var(--danger)';
        if (ok) tags[tag] = val; else valid = false;
    });
    if (!valid) return showModalStatus('#fix-tags-status', 'Fill in every field with a valid value.', 'error');

    const submit = $('#fix-tags-submit');
    submit.disabled = true;
    submit.innerHTML = '<div class="loading-spinner" style="width:14px;height:14px;border-width:2px;"></div> Applying…';
    const { ok, data } = await apiPost('/api/write', { arn: modal._arn, tags });
    if (ok) {
        showModalStatus('#fix-tags-status', '✅ Tags applied. Re-scanning so the table reflects the change…', 'success');
        submit.textContent = 'Done';
        submit.disabled = false;
        submit.onclick = closeFixTagsModal;
        toast('Tags applied', 'success');
        setTimeout(triggerBackgroundRefresh, 800);
    } else {
        const details = (data.details || []).map(v => `${v.tag}: ${v.expected || v.type}`).join('; ');
        showModalStatus('#fix-tags-status', `❌ ${data.message || 'Failed'}${details ? ' — ' + details : ''}`, 'error');
        submit.disabled = false;
        submit.textContent = 'Retry';
    }
}

function showModalStatus(sel, msg, type) {
    const el = $(sel);
    if (!el) return;
    el.style.display = 'block';
    el.textContent = msg;
    el.className = `modal-status ${type === 'success' ? 'ok' : 'err'}`;
}

// ── Exemption modal ──────────────────────────────────────────────────

function openExempt(r) {
    const modal = $('#exempt-modal');
    if (!modal || !r) return;
    modal._arn = r.id;
    $('#exempt-arn').textContent = r.id;
    $('#exempt-reason').value = '';
    const iso = d => d.toISOString().slice(0, 10);
    const tomorrow = new Date(Date.now() + 86400000);
    const expiry = $('#exempt-expiry');
    expiry.min = iso(tomorrow);
    expiry.value = iso(new Date(Date.now() + 30 * 86400000));
    $('#exempt-status').style.display = 'none';
    const submit = $('#exempt-submit');
    submit.disabled = false;
    submit.textContent = 'Create Exemption';
    modal.style.display = 'flex';
    modal.onclick = (e) => { if (e.target === modal) closeExemptModal(); };
    setTimeout(() => $('#exempt-reason').focus(), 50);
}

function closeExemptModal() {
    const modal = $('#exempt-modal');
    if (modal) modal.style.display = 'none';
}

async function exemptConfirm() {
    const modal = $('#exempt-modal');
    const reason = $('#exempt-reason').value.trim();
    const expiry = $('#exempt-expiry').value;
    if (!reason) return showModalStatus('#exempt-status', 'A reason is required.', 'error');
    if (!expiry) return showModalStatus('#exempt-status', 'Pick an expiry date — exemptions should not be permanent.', 'error');
    const submit = $('#exempt-submit');
    submit.disabled = true;
    const { ok, data } = await apiPost('/api/exemptions', {
        resource_id: modal._arn, reason, expires_at: `${expiry}T23:59:59Z`,
    });
    if (!ok) {
        submit.disabled = false;
        return showModalStatus('#exempt-status', `❌ ${data.message || 'Failed to create exemption'}`, 'error');
    }
    toast('Exemption created', 'success');
    closeExemptModal();
    loadEnterpriseData();
}

// ── Inventory coverage (#1, #6) ──────────────────────────────────────

async function loadCoverage() {
    const c = await safeFetch('/api/inventory/coverage');
    const body = $('#dash-coverage-body');
    if (c._error) {
        if (body) body.innerHTML = `<div class="chart-empty">${escHtml(c.message || 'Coverage unavailable')}</div>`;
        return;
    }
    const source = c.inventory_source === 'resource_explorer' ? 'Resource Explorer + tagging API' : 'Tagging API only';
    setText('#dash-coverage-sub', `Source: ${source}`);
    if (body) {
        const cell = (value, label, tip) => `<div title="${escHtml(tip || '')}"><div class="cov-value">${escHtml(value)}</div><div class="cov-label">${escHtml(label)}</div></div>`;
        body.innerHTML =
            cell(c.inventory_source === 'resource_explorer' ? c.never_tagged.toLocaleString() : 'not visible',
                 'never-tagged resources', c.inventory_source === 'resource_explorer'
                     ? 'Found by Resource Explorer, never had a tag (counted as non-compliant)'
                     : 'The tagging API cannot see resources that were never tagged. Set INVENTORY_SOURCE=resource_explorer.') +
            cell(c.unmapped_resources.toLocaleString(), 'resources not evaluated', 'Discovered types outside RESOURCE_TYPE_MAP') +
            cell(c.unmapped_types.length.toLocaleString(), 'unmapped resource types', c.unmapped_types.slice(0, 8).map(t => t.resource_type).join(', ')) +
            cell(c.mapped_types.length.toLocaleString(), 'resource types evaluated', '');
        if (c.warnings && c.warnings.length) {
            body.innerHTML += `<div class="banner banner-warn" style="grid-column:1/-1;margin:0">${c.warnings.map(escHtml).join('<br>')}</div>`;
        }
    }
    const hint = $('#coverage-hint');
    if (hint) hint.textContent = c.hint || '';
    const tbody = $('#coverage-table-body');
    if (tbody) {
        tbody.innerHTML = c.unmapped_types.length
            ? c.unmapped_types.map(t => `<tr><td><code>${escHtml(t.resource_type)}</code></td><td>${escHtml(t.count)}</td></tr>`).join('')
            : `<tr><td colspan="2" style="text-align:center;color:var(--muted);padding:1.5rem">Every discovered resource type is evaluated.</td></tr>`;
    }
}

// ── My resources (#4) ────────────────────────────────────────────────

async function loadMine() {
    const owner = ($('#mine-owner') || {}).value || '';
    const url = '/api/me/resources' + (owner.trim() ? `?owner=${encodeURIComponent(owner.trim())}` : '');
    const m = await safeFetch(url);
    if ($('#mine-admin')) $('#mine-admin').style.display = can('manage_exemptions') ? 'flex' : 'none';
    if (m._error) {
        showUnavailable('#mine-table-body', 5, m.message || 'Could not load your resources.');
        return;
    }
    ent.mine = m;
    setText('#mine-identity', `Resources whose ${'Owner'} tag matches ${m.identifiers.join(' or ') || 'you'}` +
        (m.owner_values.length ? ` (values: ${m.owner_values.slice(0, 3).join(', ')}${m.owner_values.length > 3 ? '…' : ''})` : ''));
    setText('#mine-total', m.summary.total.toLocaleString());
    setText('#mine-pct', `${m.summary.compliance_pct}% compliant`);
    setText('#mine-noncomp', m.summary.non_compliant.toLocaleString());
    setText('#mine-expiring', m.exemptions_error ? 'N/A' : m.summary.exemptions_expiring.toLocaleString());
    const note = $('#mine-cost-note');
    if (m.cost) {
        setText('#mine-unalloc', `${m.cost.unallocated_share_pct}%`);
        setText('#mine-cost-sub', `${fmtMoney(m.cost.unallocated_spend)} of ${fmtMoney(m.cost.owned_spend)} has no ${m.cost.cost_allocation_tag}`);
        if (note) note.style.display = 'none';
    } else {
        setText('#mine-unalloc', 'N/A');
        if (note) {
            note.style.display = m.cost_error ? 'block' : 'none';
            note.textContent = m.cost_error || '';
        }
    }
    const max = Math.max(1, ...m.violations.map(v => v.count));
    renderHBars('#mine-violations', m.violations.slice(0, 6).map(v => ({
        name: `${v.tag} ${v.type}`,
        label: `${escHtml(v.tag)} <small>${escHtml(VIOLATION_LABEL[v.type] || v.type)}</small>`,
        pct: v.count / max * 100, value: v.count.toLocaleString(),
        tip: `<b>${escHtml(v.tag)}</b> · ${escHtml(VIOLATION_LABEL[v.type] || v.type)}<br>${escHtml(v.count)} of your resources`,
    })), 'None of your resources have violations. 🎉');
    const ex = $('#mine-exemptions');
    if (ex) {
        ex.innerHTML = m.exemptions_error ? `<div class="chart-empty">${escHtml(m.exemptions_error)}</div>`
            : m.expiring_exemptions.length ? m.expiring_exemptions.map(e => `<div class="chip" style="margin:4px 0;display:flex">
                <b>${escHtml(e.days_left)}d</b> ${escHtml(e.reason || 'no reason')} · ${escHtml(e.resources.length)} resource(s)</div>`).join('')
            : '<div class="chart-empty">Nothing expiring in the next weeks.</div>';
    }
    setText('#mine-count', `${m.resources.length} resources`);
    const tbody = $('#mine-table-body');
    if (tbody) {
        tbody.innerHTML = m.resources.length ? m.resources.map(r => `<tr>
            <td class="arn-cell">${escHtml(r.id)}<div class="chart-sub">${escHtml(r.region)}</div></td>
            <td><span class="status-badge info">${escHtml(r.type)}</span></td>
            <td><span class="status-badge ${r.status === 'COMPLIANT' ? 'ok' : 'err'}">${escHtml(r.status)}</span></td>
            <td>${renderTagsCell(r.tags)}</td>
            <td>${renderIssuesCell(r.violations, r.warnings)}</td></tr>`).join('')
            : `<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:2rem">No resources carry your identity in ${escHtml('Owner')}. Ask an admin to tag them, or check DEV_AUTH_EMAIL / the ALB email claim.</td></tr>`;
    }
    applyPermissions();
}

function mineNonCompliant() {
    return ent.mine ? ent.mine.resources.filter(r => r.status !== 'COMPLIANT').map(r => r.id) : [];
}

// ── Leaderboard (#3, #5) ─────────────────────────────────────────────

async function loadLeaderboard() {
    const lb = await safeFetch(`/api/leaderboard?dimension=${encodeURIComponent(ent.lbDim)}`);
    $$('#lb-dimension button').forEach(b => b.classList.toggle('active', b.dataset.dim === ent.lbDim));
    const tbody = $('#lb-table-body');
    if (lb._error) { showUnavailable('#lb-table-body', 6, lb.message || 'Leaderboard unavailable'); return; }
    setText('#lb-compared', lb.compared_to ? `Change vs scan of ${fmtDate(lb.compared_to)}` : 'Week-over-week appears once a scan 7+ days old exists');
    const improved = $('#lb-improved');
    if (improved) improved.innerHTML = (lb.most_improved || []).filter(r => r.delta > 0)
        .map(r => `<span class="chip">📈 <b>${escHtml(r.name)}</b> +${escHtml(r.delta)} pts</span>`).join('');
    if (!lb.rows.length) { showUnavailable('#lb-table-body', 6, 'No scan yet.'); return; }
    const delta = d => d === null || d === undefined ? '<span class="delta-flat">—</span>'
        : d > 0 ? `<span class="delta-up">▲ ${d}</span>` : d < 0 ? `<span class="delta-down">▼ ${Math.abs(d)}</span>` : '<span class="delta-flat">0</span>';
    tbody.innerHTML = lb.rows.map(r => `<tr>
        <td>${r.rank <= 3 ? ['🥇', '🥈', '🥉'][r.rank - 1] : escHtml(r.rank)}</td>
        <td class="name-cell">${escHtml(r.name)}${r.name !== r.key ? `<div class="chart-sub">${escHtml(r.key)}${r.ou ? ' · ' + escHtml(r.ou) : ''}</div>` : ''}</td>
        <td><div class="meter"><div class="meter-track"><div class="meter-fill" style="width:${r.compliance_pct}%"></div></div><span>${escHtml(r.compliance_pct)}%</span></div></td>
        <td>${delta(r.delta)}</td>
        <td>${escHtml(r.total.toLocaleString())}</td>
        <td>${escHtml(r.non_compliant.toLocaleString())}</td></tr>`).join('');
}

document.addEventListener('click', (e) => {
    const b = e.target.closest('#lb-dimension button');
    if (b) { ent.lbDim = b.dataset.dim; loadLeaderboard(); }
});

async function loadOrganization() {
    const o = await safeFetch('/api/organization');
    if (o._error) { showUnavailable('#org-table-body', 5, o.message || 'Accounts unavailable'); return; }
    if (!o.accounts.length) { showUnavailable('#org-table-body', 5, 'No scan yet.'); return; }
    $('#org-table-body').innerHTML = o.accounts.map(a => {
        const errs = Object.entries(a.errors || {});
        return `<tr><td class="name-cell">${escHtml(a.name)}${a.name !== a.account_id ? `<div class="chart-sub">${escHtml(a.account_id)}</div>` : ''}</td>
            <td>${escHtml(a.ou || '-')}</td><td>${escHtml(a.compliance_pct)}%</td><td>${escHtml(a.total)}</td>
            <td>${errs.length ? errs.map(([r, m]) => `<div class="chart-sub" title="${escHtml(m)}">⚠ ${escHtml(r)}: ${escHtml(String(m).slice(0, 80))}</div>`).join('') : '-'}</td></tr>`;
    }).join('') + (o.multi_account || o.accounts.length > 1 ? '' : `<tr><td colspan="5" class="chart-sub" style="padding:1rem">Single-account mode. Set COMPLIANCE_ACCOUNTS=all (or a list) and deploy the governance role StackSet to scan the Organization.</td></tr>`);
}

async function refreshOrgDirectory() {
    const r = await safeFetch('/api/organization/refresh', { method: 'POST' });
    if (r._error) return toast(r.message || 'Organizations directory unavailable', 'error');
    toast(`Loaded ${r.accounts} accounts from AWS Organizations`, 'success');
    loadOrganization(); loadLeaderboard();
}

// ── Change sets (#2) ─────────────────────────────────────────────────

const CS_BADGE = { APPLIED: 'ok', NO_CHANGE: 'info', PARTIAL: 'warn', FAILED: 'err', UNDONE: 'info', UNDO_PARTIAL: 'warn' };

async function loadChanges() {
    const c = await safeFetch('/api/changesets?limit=100');
    if (c._error) { showUnavailable('#changes-table-body', 7, c.message || 'Change sets unavailable'); return; }
    if (!c.change_sets.length) { showUnavailable('#changes-table-body', 7, 'No changes yet. Bulk fixes and propagation fixes appear here.'); return; }
    $('#changes-table-body').innerHTML = c.change_sets.map(cs => {
        const sm = cs.summary || {};
        const result = ['SUCCESS', 'FAILED', 'INVALID', 'CONFLICT', 'NO_CHANGE'].filter(k => sm[k])
            .map(k => `<span class="tag-chip"><b>${escHtml(k.toLowerCase().replace('_', ' '))}</b> ${escHtml(sm[k])}</span>`).join('');
        const undoable = !cs.undo_of && !cs.undone_by && ['APPLIED', 'PARTIAL'].includes(cs.status);
        return `<tr>
            <td style="font-size:0.8rem">${escHtml(fmtDate(cs.created_at))}</td>
            <td class="name-cell">${escHtml(cs.actor || '-')}</td>
            <td><span class="status-badge info">${escHtml(cs.kind)}</span></td>
            <td style="font-size:0.85rem">${escHtml(cs.description || '')}<div class="chart-sub">${escHtml(cs.id)}${cs.undo_of ? ' · undoes ' + escHtml(cs.undo_of) : ''}${cs.undone_by ? ' · undone by ' + escHtml(cs.undone_by) : ''}</div></td>
            <td>${result || '-'}</td>
            <td><span class="status-badge ${CS_BADGE[cs.status] || 'info'}">${escHtml(cs.status)}</span></td>
            <td style="white-space:nowrap"><button class="btn-ghost" onclick="viewChangeSet('${escHtml(cs.id)}')">View</button>
                ${undoable ? `<button class="btn-ghost" data-perm="modify_tags" onclick="undoChangeSet('${escHtml(cs.id)}')">Undo</button>` : ''}</td></tr>`;
    }).join('');
    applyPermissions();
}

async function undoChangeSet(id) {
    if (!confirm(`Undo change set ${id}? Keys changed by someone else since will be left alone.`)) return;
    const r = await safeFetch(`/api/changesets/${encodeURIComponent(id)}/undo`, { method: 'POST' });
    if (r._error) return toast(r.message || 'Undo failed', 'error');
    const conflicts = (r.items || []).filter(i => i.conflicts && Object.keys(i.conflicts).length).length;
    toast(conflicts ? `Undone with ${conflicts} conflict(s) left as-is` : 'Change set undone', conflicts ? 'error' : 'success');
    loadChanges();
    setTimeout(triggerBackgroundRefresh, 500);
}

async function viewChangeSet(id) {
    const cs = await safeFetch(`/api/changesets/${encodeURIComponent(id)}`);
    if (cs._error) return toast(cs.message || 'Not found', 'error');
    openChangeModal({ title: `Change set ${cs.id}`, formHtml: `<div class="chart-sub">${escHtml(cs.description || '')} · ${escHtml(cs.actor)} · ${escHtml(fmtDate(cs.created_at))} · ${escHtml(cs.status)}</div>` });
    $('#change-preview').innerHTML = renderChangeItems(cs.items, true);
    $('#change-preview-btn').style.display = 'none';
    $('#change-apply-btn').style.display = 'none';
}

// ── Shared change modal: preview → apply → undo ──────────────────────

let _changeCtx = null;

function openChangeModal({ title, formHtml, onPreview, onApply }) {
    _changeCtx = { onPreview, onApply, token: null };
    setText('#change-title', title);
    $('#change-form').innerHTML = formHtml || '';
    $('#change-preview').innerHTML = '';
    $('#change-status').style.display = 'none';
    const pv = $('#change-preview-btn'), ap = $('#change-apply-btn');
    pv.style.display = onPreview ? '' : 'none';
    ap.style.display = onApply ? '' : 'none';
    pv.disabled = false; pv.textContent = 'Preview';
    ap.disabled = true; ap.textContent = 'Apply';
    pv.onclick = doChangePreview;
    ap.onclick = doChangeApply;
    const modal = $('#change-modal');
    modal.style.display = 'flex';
    modal.onclick = (e) => { if (e.target === modal) closeChangeModal(); };
}

function closeChangeModal() {
    const m = $('#change-modal');
    if (m) m.style.display = 'none';
    _changeCtx = null;
}

function renderChangeItems(items, applied) {
    if (!items || !items.length) return '<div class="chart-empty">Nothing to change.</div>';
    const rows = items.map(it => {
        if (it.op && it.op !== 'tags') {
            return `<tr><td class="arn-cell">${escHtml(it.target || '')}</td><td colspan="2"><span class="status-badge warn">${escHtml(it.op)}</span>
                ${escHtml(Object.keys(it.tags || {}).join(', ') || it.service || '')}</td><td>${escHtml(it.result || 'planned')}${it.error ? ' · ' + escHtml(it.error) : ''}</td></tr>`;
        }
        const changes = it.changes || Object.fromEntries(Object.keys(it.after || {}).map(k => [k, { before: (it.before || {})[k], after: it.after[k] }]));
        const parts = Object.entries(changes).map(([k, c]) => c.before === null || c.before === undefined
            ? `<span class="tag-chip diff-add">+ <b>${escHtml(k)}</b> ${escHtml(c.after)}</span>`
            : `<span class="tag-chip diff-change"><b>${escHtml(k)}</b> ${escHtml(c.before)} → ${escHtml(c.after)}</span>`);
        (it.removed || []).forEach(k => parts.push(`<span class="tag-chip diff-skip">− <b>${escHtml(k)}</b></span>`));
        Object.entries(it.skipped || {}).forEach(([k, v]) => parts.push(`<span class="tag-chip diff-skip" title="exists; enable overwrite to replace"><b>${escHtml(k)}</b> ${escHtml(v.current)}</span>`));
        Object.entries(it.conflicts || {}).forEach(([k, v]) => parts.push(`<span class="tag-chip diff-change" title="changed by someone else; left alone">⚠ <b>${escHtml(k)}</b> now ${escHtml(v.current)}</span>`));
        let status;
        if (applied) status = `<span class="status-badge ${it.result === 'SUCCESS' ? 'ok' : it.result === 'NO_CHANGE' ? 'info' : 'err'}">${escHtml(it.result)}</span>${it.error ? `<div class="chart-sub">${escHtml(it.error)}</div>` : ''}`;
        else if (it.invalid && it.invalid.length) status = `<span class="status-badge err">invalid</span><div class="chart-sub">${it.invalid.map(v => escHtml(`${v.tag}: ${v.expected || v.type}`)).join('<br>')}</div>`;
        else status = `${it.compliant_before ? '✅' : '❌'} → ${it.compliant_after ? '✅' : '❌'}${!it.compliant_after && it.remaining_violations && it.remaining_violations.length ? `<div class="chart-sub">still: ${it.remaining_violations.map(v => escHtml(v.tag)).join(', ')}</div>` : ''}`;
        return `<tr><td class="arn-cell">${escHtml(it.arn)}</td><td colspan="2">${parts.join('') || '<span class="chart-sub">no change</span>'}</td><td>${status}</td></tr>`;
    }).join('');
    return `<div class="table-container" style="max-height:50vh;overflow:auto"><table class="preview-table">
        <thead><tr><th>Resource</th><th colspan="2">Changes</th><th>${applied ? 'Result' : 'Compliant before → after'}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function doChangePreview() {
    if (!_changeCtx || !_changeCtx.onPreview) return;
    const pv = $('#change-preview-btn'), ap = $('#change-apply-btn');
    pv.disabled = true; pv.textContent = 'Previewing…';
    $('#change-status').style.display = 'none';
    try {
        const res = await _changeCtx.onPreview();
        if (!res.ok) { showModalStatus('#change-status', `❌ ${res.data.message || 'Preview failed'}`, 'error'); return; }
        const d = res.data, sm = d.summary || {};
        _changeCtx.token = d.preview_token;
        _changeCtx.preview = d;
        const configs = d.config_changes || [];
        $('#change-preview').innerHTML = `<div class="chip-row">
            <span class="chip"><b>${escHtml(sm.resources_changing ?? 0)}</b> resources change</span>
            <span class="chip diff-add"><b>${escHtml(sm.keys_added ?? 0)}</b> tags added</span>
            <span class="chip diff-change"><b>${escHtml(sm.keys_changed ?? 0)}</b> changed</span>
            <span class="chip"><b>${escHtml(sm.keys_skipped ?? 0)}</b> skipped (exist)</span>
            ${sm.invalid_resources ? `<span class="chip" style="color:var(--danger)"><b>${escHtml(sm.invalid_resources)}</b> invalid</span>` : ''}
            <span class="chip">compliant <b>${escHtml(sm.compliant_before ?? 0)}</b> → <b>${escHtml(sm.compliant_after ?? 0)}</b></span>
            ${configs.length ? `<span class="chip"><b>${configs.length}</b> config fixes</span>` : ''}
        </div>` + renderChangeItems((d.items || []).concat(configs.map(c => ({ ...c, result: 'planned' }))), false);
        ap.disabled = !((sm.resources_changing || 0) + configs.length);
    } finally { pv.disabled = false; pv.textContent = 'Preview again'; }
}

async function doChangeApply() {
    if (!_changeCtx || !_changeCtx.onApply) return;
    const ap = $('#change-apply-btn');
    ap.disabled = true; ap.textContent = 'Applying…';
    const res = await _changeCtx.onApply(_changeCtx.token);
    if (!res.ok) {
        ap.textContent = 'Apply';
        showModalStatus('#change-status', `❌ ${res.data.message || 'Apply failed'}${res.data.error_code === 'PLAN_CHANGED' ? ' — click Preview again.' : ''}`, 'error');
        return;
    }
    const cs = res.data, sm = cs.summary || {};
    showModalStatus('#change-status', `✅ Change set ${cs.id}: ${sm.SUCCESS || 0} succeeded, ${sm.FAILED || 0} failed, ${sm.INVALID || 0} invalid. You can undo it from the Changes tab.`, sm.FAILED ? 'error' : 'success');
    $('#change-preview').innerHTML = renderChangeItems(cs.items, true) +
        `<div style="margin-top:10px"><button class="btn-ghost" data-perm="modify_tags" onclick="undoChangeSet('${escHtml(cs.id)}')">↩ Undo this change set</button></div>`;
    ap.textContent = 'Applied';
    $('#change-preview-btn').style.display = 'none';
    ent.selected.clear();
    loadChanges();
    setTimeout(triggerBackgroundRefresh, 800);
}

// ── Bulk fix (#2) ────────────────────────────────────────────────────

function bulkTagRow(key = '', value = '') {
    const keys = Object.keys(ent.schemaByTag);
    return `<div class="tag-edit-row">
        <input class="bulk-key" list="bulk-key-list" placeholder="Tag key" value="${escHtml(key)}">
        <input class="bulk-val" placeholder="Value" value="${escHtml(value)}">
        <button class="btn-ghost" title="Remove" onclick="this.closest('.tag-edit-row').remove()">✕</button>
        <div class="sugg"></div>
    </div>`;
}

function openBulkFix(arnsOverride) {
    const arns = Array.isArray(arnsOverride) ? arnsOverride : [...ent.selected];
    if (!arns.length) return toast('Select at least one resource.', 'error');
    // Pre-fill keys that are missing/invalid on the selected resources
    const byId = new Map(ent.resources.map(r => [r.id, r]));
    const counts = {};
    arns.forEach(a => ((byId.get(a) || {}).violations || []).forEach(v => {
        if (FIXABLE.has(v.type)) counts[v.tag] = (counts[v.tag] || 0) + 1;
    }));
    const keys = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k]) => k);
    const rows = (keys.length ? keys : ['']).map(k => bulkTagRow(k)).join('');
    const options = Object.keys(ent.schemaByTag).map(k => `<option value="${escHtml(k)}">`).join('');
    openChangeModal({
        title: `Bulk fix · ${arns.length.toLocaleString()} resources`,
        formHtml: `<datalist id="bulk-key-list">${options}</datalist>
            <div class="chart-sub" style="margin-bottom:8px">Only these keys are written; other tags stay as they are.
              ${keys.length ? `Pre-filled with the keys missing or invalid on the selection.` : ''}</div>
            <div id="bulk-rows">${rows}</div>
            <div class="bulk-bar" style="margin-top:6px">
              <button class="btn-ghost" onclick="document.getElementById('bulk-rows').insertAdjacentHTML('beforeend', bulkTagRow())">+ Add tag</button>
              <label class="chart-sub" style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" id="bulk-overwrite"> Overwrite existing values</label>
            </div>`,
        onPreview: () => apiPost('/api/bulk/preview', bulkPayload(arns)),
        onApply: (token) => apiPost('/api/bulk/apply', { ...bulkPayload(arns), preview_token: token }),
    });
    loadBulkSuggestions(arns, keys);
}

function bulkPayload(arns) {
    const tags = {};
    $$('#bulk-rows .tag-edit-row').forEach(row => {
        const k = row.querySelector('.bulk-key').value.trim();
        const v = row.querySelector('.bulk-val').value.trim();
        if (k && v) tags[k] = v;
    });
    return { arns, tags, overwrite: !!($('#bulk-overwrite') || {}).checked };
}

async function loadBulkSuggestions(arns, keys) {
    if (!keys.length) return;
    const { ok, data } = await apiPost('/api/suggestions', { arns: arns.slice(0, 500), keys });
    if (!ok) return;
    $$('#bulk-rows .tag-edit-row').forEach(row => {
        const key = row.querySelector('.bulk-key').value.trim();
        const list = (data.aggregate || {})[key] || [];
        const allowed = (ent.schemaByTag[key] || {}).allowed_values || [];
        const chips = list.map(sg => `<span class="chip clickable" data-val="${escHtml(sg.value)}" title="avg confidence ${Math.round(sg.avg_confidence * 100)}%">💡 <b>${escHtml(sg.value)}</b> ${escHtml(sg.resources)}/${arns.length}</span>`);
        if (!chips.length && allowed.length) allowed.forEach(a => chips.push(`<span class="chip clickable" data-val="${escHtml(a)}">${escHtml(a)}</span>`));
        row.querySelector('.sugg').innerHTML = chips.join('');
        row.querySelectorAll('.chip.clickable').forEach(c => c.onclick = () => { row.querySelector('.bulk-val').value = c.dataset.val; });
    });
}

// ── Propagation (#7, #8) ─────────────────────────────────────────────

async function loadPropagation() {
    const p = await safeFetch('/api/propagation');
    if (p._error) return;
    const rulesEl = $('#prop-rules');
    if (rulesEl && !rulesEl.dataset.ready) {
        rulesEl.innerHTML = p.rules.map(r => `<label><input type="checkbox" class="prop-rule" value="${escHtml(r.name)}" checked> ${escHtml(r.label)}</label>`).join('');
        rulesEl.dataset.ready = '1';
        const sel = $('#prop-filter-rule');
        if (sel) sel.innerHTML = '<option value="">All rules</option>' + p.rules.map(r => `<option value="${escHtml(r.name)}">${escHtml(r.label)}</option>`).join('');
    }
    ent.prop.run = p.run;
    const btn = $('#btn-prop-run');
    if (p.is_running) {
        setText('#prop-status', 'Checking…');
        if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
        if (!ent.prop.polling) ent.prop.polling = setInterval(async () => {
            const s = await safeFetch('/api/propagation');
            if (!s._error && !s.is_running) {
                clearInterval(ent.prop.polling); ent.prop.polling = null;
                if (btn) { btn.disabled = false; btn.textContent = 'Run check'; }
                if (s.last_error) toast(`Propagation check failed: ${s.last_error}`, 'error');
                loadPropagation();
            }
        }, 2000);
    } else if (p.run) {
        const sm = p.run.summary;
        setText('#prop-status', `Last check ${fmtDate(sm.timestamp)} · ${sm.regions.join(', ')} · ${sm.findings} findings` +
            (sm.errors.length ? ` · ${sm.errors.length} rule error(s)` : ''));
    } else if (p.last_error) {
        setText('#prop-status', `Last check failed: ${p.last_error}`);
    }
    renderPropagation();
}

function renderPropagation() {
    const run = ent.prop.run;
    const tbody = $('#prop-table-body');
    if (!tbody) return;
    if (!run) { showUnavailable('#prop-table-body', 6, 'Run a check to see findings.'); return; }
    const sm = run.summary;
    const summaryEl = $('#prop-summary');
    if (summaryEl) {
        summaryEl.innerHTML = Object.entries(sm.parents_checked).map(([rule, n]) => {
            const b = (sm.by_rule || {})[rule] || { tags: 0, config: 0 };
            return `<span class="chip"><b>${escHtml(rule)}</b> ${escHtml(n)} parents · ${escHtml(b.tags)} children off · ${escHtml(b.config)} config</span>`;
        }).join('') + sm.errors.map(e => `<span class="chip" style="color:var(--danger)" title="${escHtml(e.error)}">⚠ ${escHtml(e.rule)} ${escHtml(e.region)}</span>`).join('');
    }
    const rule = ($('#prop-filter-rule') || {}).value || '';
    const kind = ($('#prop-filter-kind') || {}).value || '';
    const rows = run.findings.filter(f => (!rule || f.rule === rule) && (!kind || f.kind === kind));
    ent.prop.shown = rows;
    if (!rows.length) { showUnavailable('#prop-table-body', 6, run.findings.length ? 'No findings match the filters.' : 'All children carry their parents\' tags. 🎉'); return; }
    tbody.innerHTML = rows.slice(0, 1000).map(f => `<tr>
        <td><input type="checkbox" class="prop-check" data-id="${escHtml(f.id)}" ${ent.prop.selected.has(f.id) ? 'checked' : ''}></td>
        <td><span class="status-badge info">${escHtml(f.rule)}</span></td>
        <td class="arn-cell">${escHtml(f.parent_name)}<div class="chart-sub">${escHtml(f.parent_arn)}</div></td>
        <td class="arn-cell">${f.kind === 'config' ? `<span class="status-badge warn">${escHtml(f.issue)}</span><div class="chart-sub">${escHtml(f.detail)}</div>` : `${escHtml(f.child_arn)}<div class="chart-sub">${escHtml(f.child_type)}</div>`}</td>
        <td>${Object.entries(f.missing || {}).map(([k, v]) => `<span class="tag-chip diff-add"><b>${escHtml(k)}</b> ${escHtml(v)}</span>`).join('') || '-'}</td>
        <td>${Object.entries(f.mismatched || {}).map(([k, v]) => `<span class="tag-chip diff-change"><b>${escHtml(k)}</b> ${escHtml(v.actual)} → ${escHtml(v.expected)}</span>`).join('') || '-'}</td>
    </tr>`).join('') + (rows.length > 1000 ? `<tr><td colspan="6" class="chart-sub" style="padding:1rem">Showing 1,000 of ${rows.length}. Use the filters to narrow down.</td></tr>` : '');
    setText('#prop-selected', `${ent.prop.selected.size} selected`);
}

function propSelectAll(on) {
    (ent.prop.shown || []).forEach(f => on ? ent.prop.selected.add(f.id) : ent.prop.selected.delete(f.id));
    renderPropagation();
}

async function runPropagation() {
    const rules = [...$$('.prop-rule:checked')].map(c => c.value);
    if (!rules.length) return toast('Pick at least one rule.', 'error');
    const { ok, data } = await apiPost('/api/propagation/run', { rules });
    if (!ok) return toast(data.message || 'Could not start the check', 'error');
    ent.prop.selected.clear();
    loadPropagation();
}

function previewPropagationFix() {
    const ids = [...ent.prop.selected];
    if (!ids.length) return toast('Select findings first (or "Select all shown").', 'error');
    const overwrite = !!($('#prop-overwrite') || {}).checked;
    openChangeModal({
        title: `Propagation fix · ${ids.length} findings`,
        formHtml: `<div class="chart-sub">Children get the parent's missing tags${overwrite ? ' and mismatched values are replaced' : '; mismatched values are kept (enable "Overwrite" to replace)'}. Config fixes switch on tag propagation for new instances/tasks.</div>`,
        onPreview: () => apiPost('/api/propagation/preview', { finding_ids: ids, overwrite }),
        onApply: (token) => apiPost('/api/propagation/apply', { finding_ids: ids, overwrite, preview_token: token }),
    });
    doChangePreview();
}

// ── Tab hooks ────────────────────────────────────────────────────────

function onEnterpriseTab(tab) {
    if (typeof ent === 'undefined') return;
    if (tab === 'mine') loadMine();
    else if (tab === 'changes') loadChanges();
    else if (tab === 'propagation') loadPropagation();
    else if (tab === 'organization') { loadLeaderboard(); loadOrganization(); }
}

// ── Init ─────────────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeFixTagsModal(); closeExemptModal(); closeChangeModal(); hideTip(); }
});

let _resizeTimer = null;
window.addEventListener('resize', () => {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => renderTrend(ent.history), 150);
});

document.addEventListener('DOMContentLoaded', () => {
    const fromHash = (location.hash || '').slice(1);
    const startBtn = document.querySelector(`.tab-btn[data-tab="${CSS.escape(fromHash)}"]`)
        || document.getElementById('ent-tab-dashboard');
    if (startBtn) startBtn.click();
    loadEnterpriseData();
});

// ── Shell: theme toggle & mobile navigation ──────────────────────────

(function initShell() {
    const root = document.documentElement;
    const themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) themeBtn.addEventListener('click', () => {
        const current = root.dataset.theme
            || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
        const next = current === 'dark' ? 'light' : 'dark';
        root.dataset.theme = next;
        try { localStorage.setItem('atu-theme', next); } catch (e) { /* storage unavailable */ }
    });
    const menuBtn = document.getElementById('menu-btn');
    if (menuBtn) menuBtn.addEventListener('click', () => document.body.classList.add('nav-open'));
    // Colour the status dot from whatever status text the loaders write.
    const statusText = document.getElementById('meta-status-text');
    const statusBar = document.getElementById('compliance-status-bar');
    if (statusText && statusBar) {
        const sync = () => statusBar.classList.toggle('is-error', /fail|unavailable|error/i.test(statusText.textContent));
        new MutationObserver(sync).observe(statusText, { childList: true, characterData: true, subtree: true });
        sync();
    }
    const scrim = document.getElementById('scrim');
    if (scrim) scrim.addEventListener('click', () => document.body.classList.remove('nav-open'));
})();
