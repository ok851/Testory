(function () {
  'use strict';

  var lastPreview = null;

  /* ── 思维链数据 ── */
  var HEAL_LOCATOR_CHAIN = [
    { id: 'scan', text: '正在扫描用例步骤…', icon: '🔍' },
    { id: 'probe', text: '正在探测当前页面元素…', icon: '🌐' },
    { id: 'compare', text: '正在对比定位器有效性…', icon: '⚖️' },
    { id: 'repair', text: '正在生成修复方案…', icon: '🔧' },
    { id: 'verify', text: '正在验证修复效果…', icon: '✅' }
  ];
  var HEAL_DIAG_CHAIN = [
    { id: 'parse', text: '正在解析失败日志…', icon: '📝' },
    { id: 'analyze', text: '正在分析根因…', icon: '🧠' },
    { id: 'suggest', text: '正在生成修复建议…', icon: '💡' },
    { id: 'verify', text: '正在验证方案可行性…', icon: '🔒' }
  ];

  function showThinking(panelId, innerId, chain) {
    var panel = document.getElementById(panelId);
    var inner = document.getElementById(innerId);
    if (!panel || !inner) return;
    inner.innerHTML = '';
    chain.forEach(function(s) {
      var row = document.createElement('div');
      row.className = 'ai-thinking-chain__step';
      row.id = panelId + '_step_' + s.id;
      row.innerHTML = '<span class="ai-thinking-chain__dot ai-thinking-chain__dot--wait"></span><span>' + s.icon + ' ' + s.text + '</span>';
      inner.appendChild(row);
    });
    panel.style.display = '';
  }

  function updateThinking(panelId, stepId, state) {
    var row = document.getElementById(panelId + '_step_' + stepId);
    if (!row) return;
    var dot = row.querySelector('.ai-thinking-chain__dot');
    if (state === 'active') {
      row.className = 'ai-thinking-chain__step ai-thinking-chain__step--active';
      if (dot) dot.className = 'ai-thinking-chain__dot ai-thinking-chain__dot--active';
    } else if (state === 'done') {
      row.className = 'ai-thinking-chain__step ai-thinking-chain__step--done';
      if (dot) { dot.className = 'ai-thinking-chain__dot ai-thinking-chain__dot--done'; dot.innerHTML = '✓'; }
    }
  }

  function hideThinking(panelId) {
    var panel = document.getElementById(panelId);
    if (panel) panel.style.display = 'none';
  }

  /* ── 自然语言结果渲染（去代码化） ── */
  function renderNaturalResult(data, container) {
    container.innerHTML = '';
    var steps = (data && data.resolved_steps) || (data && data.steps) || [];
    if (!steps.length) {
      container.innerHTML = '<div style="color:#94a3b8;font-size:13px;">无步骤数据</div>';
      return;
    }
    var stats = { changed: 0, unchanged: 0, suggestion: 0 };
    var card = document.createElement('div');
    card.className = 'ai-heal-result-card';
    steps.forEach(function(step, i) {
      var oldSel = step.original_selector_value || step.original_selector || '';
      var newSel = step.resolved_selector_value || step.selector_value || '';
      var changed = oldSel && newSel && oldSel !== newSel;
      var item = document.createElement('div');
      item.className = 'ai-heal-result-item';
      item.style.animationDelay = (i * 40) + 'ms';

      var badge = document.createElement('span');
      badge.className = 'ai-heal-result-badge';

      var desc = document.createElement('span');
      desc.style.flex = '1';
      desc.style.minWidth = '0';

      if (changed) {
        stats.changed++;
        badge.className += ' ai-heal-result-badge--ok';
        badge.textContent = '🟢';
        var oldType = oldSel.startsWith('//') || oldSel.startsWith('(/') ? 'XPath' : 'CSS 选择器';
        var newType = newSel.startsWith('//') || newSel.startsWith('(/') ? 'XPath' : 'CSS 选择器';
        desc.textContent = '「' + (step.description || step.action || ('步骤 ' + (i + 1))) + '」定位方式已优化（从 ' + oldType + ' 改为 ' + newType + '）';
      } else if (newSel) {
        stats.unchanged++;
        badge.className += ' ai-heal-result-badge--info';
        badge.textContent = '⚪';
        desc.textContent = '「' + (step.description || step.action || ('步骤 ' + (i + 1))) + '」定位方式保持不变';
      } else {
        stats.suggestion++;
        badge.className += ' ai-heal-result-badge--warn';
        badge.textContent = '🟡';
        desc.textContent = '「' + (step.description || step.action || ('步骤 ' + (i + 1))) + '」建议增加等待条件或检查元素可见性';
      }

      item.appendChild(badge);
      item.appendChild(desc);
      card.appendChild(item);
    });
    container.appendChild(card);
    return stats;
  }

  function renderLocatorSummary(stats) {
    var container = document.getElementById('aiHealLocatorSummary');
    if (!container || !stats) return;
    var total = stats.changed + stats.unchanged + stats.suggestion;
    var pct = total ? Math.round((stats.changed + stats.unchanged) * 100 / total) : 0;
    var html = '<div class="ai-task-summary__header">' +
      '<div><div class="ai-task-summary__title">修复完成</div>' +
      '<div class="ai-task-summary__meta">' + stats.changed + ' 项已优化 · ' + stats.unchanged + ' 项保持 · ' + stats.suggestion + ' 项建议</div></div>' +
      '<div class="ai-task-summary__ring" style="--pct:' + pct + '%;"><span>' + pct + '%</span></div>' +
      '</div>' +
      '<div class="ai-task-summary__actions">' +
      '<button type="button" class="ai-glow-btn" id="aiHealSaveBtn2">应用修复</button>' +
      '<button type="button" class="ai-btn ai-btn--secondary" onclick="document.getElementById(\'aiHealLocatorSummary\').style.display=\'none\'">关闭</button>' +
      '</div>';
    container.innerHTML = html;
    container.style.display = '';
    var saveBtn2 = document.getElementById('aiHealSaveBtn2');
    if (saveBtn2) saveBtn2.addEventListener('click', function() { void saveLocator(); });
  }

  function renderDiagSummary(diag) {
    var container = document.getElementById('aiHealDiagSummary');
    if (!container) return;
    var title = '诊断完成';
    var summary = (typeof diag === 'string') ? diag.slice(0, 80) : (diag.summary || '已分析失败原因并生成修复建议');
    var html = '<div class="ai-task-summary__header">' +
      '<div><div class="ai-task-summary__title">' + title + '</div>' +
      '<div class="ai-task-summary__meta">' + escapeHtml(summary) + '</div></div>' +
      '</div>';
    if (diag.root_cause) {
      html += '<div style="margin-top:10px;font-size:13px;color:#334155;"><strong>根因：</strong>' + escapeHtml(diag.root_cause) + '</div>';
    }
    if (diag.suggestion) {
      html += '<div style="margin-top:6px;font-size:13px;color:#334155;"><strong>建议：</strong>' + escapeHtml(diag.suggestion) + '</div>';
    }
    if (diag.fix_steps && diag.fix_steps.length) {
      html += '<div style="margin-top:10px;font-size:13px;color:#334155;"><strong>修复步骤：</strong></div>';
      diag.fix_steps.forEach(function(s, i) {
        html += '<div style="margin-top:4px;font-size:12px;color:#64748b;padding-left:12px;">' + (i + 1) + '. ' + escapeHtml(s) + '</div>';
      });
    }
    html += '<div class="ai-task-summary__actions" style="margin-top:14px;">' +
      '<button type="button" class="ai-btn ai-btn--secondary" onclick="document.getElementById(\'aiHealDiagSummary\').style.display=\'none\'">关闭</button>' +
      '</div>';
    container.innerHTML = html;
    container.style.display = '';
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* ── Animated log renderer ── */
  function renderAnimatedLog(text, container) {
    container.innerHTML = '';
    var lines = String(text).split('\n');
    lines.forEach(function (line, i) {
      var el = document.createElement('div');
      el.className = 'heal-log-line';
      if (/\b(error|fail|失败)\b/i.test(line)) el.classList.add('heal-log-line--error');
      else if (/\b(warn|警告)\b/i.test(line)) el.classList.add('heal-log-line--warn');
      else if (/\b(success|pass|通过)\b/i.test(line)) el.classList.add('heal-log-line--success');
      else if (/^\s*(\[AI\]|🤖)/.test(line)) el.classList.add('heal-log-line--ai');
      else if (/\[INFO\]/.test(line)) el.classList.add('heal-log-line--info');
      el.textContent = line;
      el.style.animationDelay = (i * 60) + 'ms';
      container.appendChild(el);
    });
  }

  function loadProjects() {
    var sel = document.getElementById('aiHealProject');
    if (!sel) return;
    return fetch('/api/projects', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var list = (d && d.projects) || [];
        sel.innerHTML = '<option value="">请选择项目</option>';
        list.forEach(function (p) {
          var o = document.createElement('option');
          o.value = String(p.id || p.project_id || '');
          o.textContent = p.name || ('项目 ' + o.value);
          sel.appendChild(o);
        });
      });
  }

  function loadCases(projectId) {
    var sel = document.getElementById('aiHealCase');
    if (!sel) return;
    if (!projectId) {
      sel.innerHTML = '<option value="">先选项目</option>';
      return;
    }
    return fetch('/api/projects/' + encodeURIComponent(projectId) + '/cases?case_type=ui', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var list = (d && d.cases) || (d && d.test_cases) || [];
        sel.innerHTML = '<option value="">请选择用例</option>';
        list.forEach(function (c) {
          var o = document.createElement('option');
          o.value = String(c.id);
          o.textContent = c.name || ('用例 ' + o.value);
          if (c.url) o.dataset.url = c.url;
          sel.appendChild(o);
        });
      });
  }

  function fetchAllSteps(caseId) {
    var all = [];
    var page = 1;
    function next() {
      return fetch('/api/cases/' + caseId + '/steps?page=' + page + '&page_size=100', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var steps = data.steps || [];
          var total = data.total != null ? data.total : steps.length;
          all = all.concat(steps);
          if (all.length < total && steps.length > 0) {
            page += 1;
            return next();
          }
          return all;
        });
    }
    return next();
  }

  function planStepsFromDb(rows) {
    return (rows || []).map(function (s) {
      return {
        action: s.action || '',
        selector_type: s.selector_type || 'css',
        selector_value: s.selector_value != null ? String(s.selector_value) : '',
        input_value: s.input_value != null ? String(s.input_value) : '',
        description: s.description != null ? String(s.description) : ''
      };
    });
  }

  async function previewLocator() {
    var caseId = (document.getElementById('aiHealCase') || {}).value;
    var url = (document.getElementById('aiHealUrl') || {}).value || '';
    var out = document.getElementById('aiHealLocatorOut');
    var saveBtn = document.getElementById('aiHealSaveBtn');
    var summaryEl = document.getElementById('aiHealLocatorSummary');
    if (!caseId) { alert('请选择用例'); return; }
    if (!url.trim()) {
      var sel = document.getElementById('aiHealCase');
      var opt = sel && sel.options[sel.selectedIndex];
      url = (opt && opt.dataset && opt.dataset.url) || '';
    }
    if (!url.trim()) { alert('请填写目标 URL'); return; }
    showThinking('aiHealLocatorThinking', 'aiHealLocatorThinkingInner', HEAL_LOCATOR_CHAIN);
    updateThinking('aiHealLocatorThinking', 'scan', 'active');
    if (summaryEl) summaryEl.style.display = 'none';
    try {
      var steps = await fetchAllSteps(caseId);
      updateThinking('aiHealLocatorThinking', 'scan', 'done');
      updateThinking('aiHealLocatorThinking', 'probe', 'active');
      var resp = await fetch('/api/ai/locator/resolve-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ url: url.trim(), steps: planStepsFromDb(steps) })
      });
      var data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.error || '失败');
      lastPreview = data;
      updateThinking('aiHealLocatorThinking', 'probe', 'done');
      updateThinking('aiHealLocatorThinking', 'compare', 'done');
      updateThinking('aiHealLocatorThinking', 'repair', 'done');
      updateThinking('aiHealLocatorThinking', 'verify', 'done');
      if (out) {
        var stats = renderNaturalResult(data, out);
        renderLocatorSummary(stats);
      }
      if (saveBtn) saveBtn.disabled = false;
      setTimeout(function() { hideThinking('aiHealLocatorThinking'); }, 800);
    } catch (e) {
      lastPreview = null;
      hideThinking('aiHealLocatorThinking');
      if (out) {
        out.innerHTML = '<div class="heal-log-line heal-log-line--error" style="animation-delay:0ms;">' +
          escapeHtml(String(e && e.message ? e.message : e)) + '</div>';
      }
      if (saveBtn) saveBtn.disabled = true;
    }
  }

  async function saveLocator() {
    var caseId = (document.getElementById('aiHealCase') || {}).value;
    if (!caseId || !lastPreview) return;
    try {
      var resp = await fetch('/api/cases/' + encodeURIComponent(caseId) + '/ai/locator-resolve-save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({})
      });
      var data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.error || '保存失败');
      var msg = '已更新 ' + (data.patched || 0) + ' 条步骤';
      if (data.memory_synced) msg += '；AI 已记住此页面控件，下次优先复用';
      if (typeof window.aiShowToast === 'function') window.aiShowToast(msg, 'success');
      else alert(msg);
      var summaryEl = document.getElementById('aiHealLocatorSummary');
      if (summaryEl) summaryEl.style.display = 'none';
    } catch (e) {
      alert('保存失败: ' + (e && e.message ? e.message : String(e)));
    }
  }

  async function runDiag() {
    var text = (document.getElementById('aiHealDiagText') || {}).value || '';
    var out = document.getElementById('aiHealDiagOut');
    var summaryEl = document.getElementById('aiHealDiagSummary');
    if (!text.trim()) { alert('请粘贴失败信息'); return; }
    showThinking('aiHealDiagThinking', 'aiHealDiagThinkingInner', HEAL_DIAG_CHAIN);
    updateThinking('aiHealDiagThinking', 'parse', 'active');
    if (summaryEl) summaryEl.style.display = 'none';
    if (out) out.style.display = 'none';
    try {
      var resp = await fetch('/api/ai/hub/heal/diagnose-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          error_message: text.slice(0, 8000),
          step_summary: text.slice(0, 2000),
          url: (document.getElementById('aiHealUrl') || {}).value || ''
        })
      });
      var data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.error || '失败');
      var diag = data.diagnosis || data;
      updateThinking('aiHealDiagThinking', 'parse', 'done');
      updateThinking('aiHealDiagThinking', 'analyze', 'done');
      updateThinking('aiHealDiagThinking', 'suggest', 'done');
      updateThinking('aiHealDiagThinking', 'verify', 'done');
      renderDiagSummary(diag);
      setTimeout(function() { hideThinking('aiHealDiagThinking'); }, 800);
    } catch (e) {
      hideThinking('aiHealDiagThinking');
      if (out) {
        out.style.display = '';
        renderAnimatedLog('[ERROR] 诊断失败：' + String(e && e.message ? e.message : e), out);
      }
    }
  }

  /* ── Tab 切换 ── */
  function switchHealTab(tab) {
    document.querySelectorAll('.ai-tab').forEach(function(t) {
      t.classList.toggle('ai-tab--active', t.getAttribute('data-tab') === tab);
    });
    document.querySelectorAll('.ai-heal-tab-body').forEach(function(b) {
      b.classList.toggle('ai-heal-tab-body--active', b.id === 'aiHealBody' + (tab.charAt(0).toUpperCase() + tab.slice(1)));
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    loadProjects();
    document.getElementById('aiHealProject').addEventListener('change', function () {
      loadCases(this.value);
    });
    document.getElementById('aiHealCase').addEventListener('change', function () {
      var opt = this.options[this.selectedIndex];
      var urlEl = document.getElementById('aiHealUrl');
      if (urlEl && opt && opt.dataset && opt.dataset.url) urlEl.value = opt.dataset.url;
    });
    document.getElementById('aiHealPreviewBtn').addEventListener('click', function () { void previewLocator(); });
    document.getElementById('aiHealSaveBtn').addEventListener('click', function () { void saveLocator(); });
    document.getElementById('aiHealDiagBtn').addEventListener('click', function () { void runDiag(); });
    document.getElementById('aiHealTabLocator').addEventListener('click', function () { switchHealTab('locator'); });
    document.getElementById('aiHealTabDiag').addEventListener('click', function () { switchHealTab('diag'); });
  });
})();
