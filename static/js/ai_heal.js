(function () {
  'use strict';

  var lastPreview = null;

  /* ── Code diff renderer ── */
  function renderDiffView(data, container) {
    container.innerHTML = '';
    var steps = (data && data.resolved_steps) || (data && data.steps) || [];
    if (!steps.length) {
      container.innerHTML = '<div style="color:#94a3b8;font-size:13px;">无步骤数据</div>';
      return;
    }
    var stats = { changed: 0, unchanged: 0, new_sel: 0 };
    steps.forEach(function (step, i) {
      var oldSel = step.original_selector_value || step.original_selector || '';
      var newSel = step.resolved_selector_value || step.selector_value || '';
      var changed = oldSel && newSel && oldSel !== newSel;
      if (changed) stats.changed++;
      else if (newSel) stats.new_sel++;
      else stats.unchanged++;

      var row = document.createElement('div');
      row.className = 'heal-diff-row';
      row.style.animationDelay = (i * 40) + 'ms';

      var num = document.createElement('span');
      num.className = 'heal-diff-num';
      num.textContent = (i + 1);

      var desc = document.createElement('span');
      desc.className = 'heal-diff-desc';
      desc.textContent = step.description || step.action || ('步骤 ' + (i + 1));

      var code = document.createElement('div');
      code.className = 'heal-diff-code';

      if (changed) {
        var oldLine = document.createElement('div');
        oldLine.className = 'heal-diff-line heal-diff-line--old';
        oldLine.innerHTML = '<span class="heal-diff-marker">-</span><code>' + escapeHtml(oldSel) + '</code>';
        var newLine = document.createElement('div');
        newLine.className = 'heal-diff-line heal-diff-line--new';
        newLine.innerHTML = '<span class="heal-diff-marker">+</span><code>' + escapeHtml(newSel) + '</code>';
        code.appendChild(oldLine);
        code.appendChild(newLine);
      } else if (newSel) {
        var keepLine = document.createElement('div');
        keepLine.className = 'heal-diff-line heal-diff-line--keep';
        keepLine.innerHTML = '<span class="heal-diff-marker">&nbsp;</span><code>' + escapeHtml(newSel) + '</code>';
        code.appendChild(keepLine);
      }

      row.appendChild(num);
      var body = document.createElement('div');
      body.className = 'heal-diff-body';
      body.appendChild(desc);
      body.appendChild(code);
      row.appendChild(body);
      container.appendChild(row);
    });

    var summary = document.createElement('div');
    summary.className = 'heal-diff-summary';
    summary.innerHTML = '<span class="heal-diff-summary__changed">' + stats.changed + ' 项变更</span>' +
      '<span class="heal-diff-summary__keep">' + stats.unchanged + ' 项保持</span>';
    container.insertBefore(summary, container.firstChild);
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
    if (!caseId) { alert('请选择用例'); return; }
    if (!url.trim()) {
      var sel = document.getElementById('aiHealCase');
      var opt = sel && sel.options[sel.selectedIndex];
      url = (opt && opt.dataset && opt.dataset.url) || '';
    }
    if (!url.trim()) { alert('请填写目标 URL'); return; }
    try {
      var steps = await fetchAllSteps(caseId);
      var resp = await fetch('/api/ai/locator/resolve-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ url: url.trim(), steps: planStepsFromDb(steps) })
      });
      var data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.error || '失败');
      lastPreview = data;
      if (out) {
        renderDiffView(data, out);
      }
      if (saveBtn) saveBtn.disabled = false;
    } catch (e) {
      lastPreview = null;
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
    } catch (e) {
      alert('保存失败: ' + (e && e.message ? e.message : String(e)));
    }
  }

  async function runDiag() {
    var text = (document.getElementById('aiHealDiagText') || {}).value || '';
    var out = document.getElementById('aiHealDiagOut');
    if (!text.trim()) { alert('请粘贴失败信息'); return; }
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
      if (out) out.textContent = JSON.stringify(data.diagnosis || data, null, 2).slice(0, 12000);
    } catch (e) {
      if (out) out.textContent = String(e && e.message ? e.message : e);
    }
  }

  async function analyzeCase() {
    var caseId = (document.getElementById('aiHealCase') || {}).value;
    var out = document.getElementById('aiHealAnalyzeOut');
    if (!caseId) { alert('请选择用例'); return; }
    try {
      var steps = await fetchAllSteps(caseId);
      var resp = await fetch('/api/ai/hub/heal/analyze-steps', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ steps: planStepsFromDb(steps) })
      });
      var data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.error || '失败');
      if (out) out.textContent = JSON.stringify(data.analysis || data, null, 2);
    } catch (e) {
      if (out) out.textContent = String(e && e.message ? e.message : e);
    }
  }

  window.aiHealUpdateSkill = async function () {
    var skillId = (document.getElementById('aiHealSkillId') || {}).value || '';
    var message = (document.getElementById('aiHealSkillMessage') || {}).value || '';
    var out = document.getElementById('aiHealSkillResult');
    if (!skillId.trim() || !message.trim()) {
      alert('请填写 Skill ID 与说明');
      return;
    }
    if (out) { out.style.display = 'block'; out.textContent = '请求 Hermes…'; }
    try {
      var resp = await fetch('/api/ai/skills/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ skill_id: skillId.trim(), message: message.trim() })
      });
      var data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.error || '失败');
      if (out) out.textContent = data.hermes_response || JSON.stringify(data, null, 2);
    } catch (e) {
      if (out) out.textContent = String(e && e.message ? e.message : e);
    }
  };

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
    document.getElementById('aiHealAnalyzeBtn').addEventListener('click', function () { void analyzeCase(); });
  });
})();
