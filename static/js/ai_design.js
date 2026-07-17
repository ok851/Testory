(function () {
  'use strict';

  var state = {
    projectId: '',
    projectName: '',
    platform: 'web',
    busy: false,
    drafts: []
  };

  var ROLE_LABELS = {
    login_feature: '登录功能',
    business: '业务场景',
    auth_fixture: '会话前置'
  };

  function qs(name) {
    try {
      return new URLSearchParams(window.location.search).get(name) || '';
    } catch (e) {
      return '';
    }
  }

  function setStatus(msg, kind) {
    var el = document.getElementById('aiDesignStatus');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'ai-design-status is-visible ai-design-status--' + (kind || 'info');
    if (!msg) el.classList.remove('is-visible');
  }

  function profileOptionLabel(p) {
    if (!p) return '';
    var mid = p.model_id || p.label || p.id || '';
    var prov = p.provider || '';
    return (p.label || mid) + (prov ? ' · ' + prov : '');
  }

  function getSelectedModel() {
    var sel = document.getElementById('aiDesignModelSelect');
    if (sel && sel.value) return Promise.resolve(String(sel.value));
    return fetch('/api/ai/models', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.active_profile_id != null) return String(d.active_profile_id);
        return '';
      })
      .catch(function () { return ''; });
  }

  function loadAiDesignModels() {
    var sel = document.getElementById('aiDesignModelSelect');
    if (!sel) return Promise.resolve();
    return fetch('/api/ai/models', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var profiles = (d && d.profiles) || [];
        var active = (d && d.active_profile_id) ? String(d.active_profile_id) : '';
        if (!profiles.length) {
          sel.innerHTML = '<option value="">未配置模型，请点击「管理模型」</option>';
          return;
        }
        sel.innerHTML = profiles.map(function (p) {
          var id = String(p.id || '');
          var lab = profileOptionLabel(p);
          var selected = id === active ? ' selected' : '';
          return '<option value="' + id.replace(/"/g, '&quot;') + '"' + selected + '>' + lab.replace(/</g, '&lt;') + '</option>';
        }).join('');
      })
      .catch(function () {
        sel.innerHTML = '<option value="">加载模型列表失败</option>';
      });
  }

  function saveAiDesignActiveModel(profileId) {
    if (!profileId) return Promise.resolve();
    return fetch('/api/ai/models/active', {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId })
    }).catch(function () {});
  }

  function hasRequirementsInput() {
    var text = (document.getElementById('aiDesignReqText') && document.getElementById('aiDesignReqText').value || '').trim();
    var f = document.getElementById('aiDesignReqFile');
    return !!(text || (f && f.files && f.files[0]));
  }

  function buildFormData(extra) {
    var fd = new FormData();
    var ext = extra || {};
    if (state.projectId) fd.append('project_id', state.projectId);
    if (state.projectName) fd.append('project_name', state.projectName);
    fd.append('platform_type', state.platform === 'android' ? 'android' : state.platform);
    var baseUrl = (document.getElementById('aiDesignBaseUrl') && document.getElementById('aiDesignBaseUrl').value || '').trim();
    if (baseUrl) fd.append('base_url', baseUrl);
    if (ext.model) fd.append('model', ext.model);
    var text = (document.getElementById('aiDesignReqText') && document.getElementById('aiDesignReqText').value || '').trim();
    if (text) fd.append('requirements_text', text);
    var f = document.getElementById('aiDesignReqFile');
    if (f && f.files && f.files[0]) fd.append('file', f.files[0]);
    return fd;
  }

  function buildJsonBody(extra) {
    var text = (document.getElementById('aiDesignReqText') && document.getElementById('aiDesignReqText').value || '').trim();
    return {
      project_id: state.projectId,
      project_name: state.projectName,
      platform_type: state.platform === 'android' ? 'android' : state.platform,
      requirements_text: text,
      base_url: (document.getElementById('aiDesignBaseUrl') && document.getElementById('aiDesignBaseUrl').value || '').trim(),
      model: (extra && extra.model) || ''
    };
  }

  async function postDesignPreview(extra) {
    var f = document.getElementById('aiDesignReqFile');
    var hasFile = f && f.files && f.files[0];
    var url = '/api/ai/hub/design/preview';
    if (hasFile) {
      var fd = buildFormData(extra);
      var resp = await fetch(url, { method: 'POST', body: fd, credentials: 'same-origin' });
      var data = await resp.json().catch(function () { return {}; });
      return { resp: resp, data: data };
    }
    var body = buildJsonBody(extra);
    if (!body.requirements_text) {
      throw new Error('请上传需求文件或填写需求文本');
    }
    var r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body)
    });
    var d = await r.json().catch(function () { return {}; });
    return { resp: r, data: d };
  }

  function escapeHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  /* ── Typing animation for status messages ── */
  function typeText(el, text, speed) {
    speed = speed || 18;
    el.textContent = '';
    var i = 0;
    var cursor = document.createElement('span');
    cursor.className = 'ai-type-cursor';
    el.appendChild(cursor);
    function tick() {
      if (i < text.length) {
        el.insertBefore(document.createTextNode(text.charAt(i)), cursor);
        i++;
        setTimeout(tick, speed);
      } else {
        setTimeout(function () { if (cursor.parentNode) cursor.remove(); }, 800);
      }
    }
    tick();
  }

  function renderDraftList() {
    var wrap = document.getElementById('aiDesignDraftList');
    var saveBtn = document.getElementById('aiDesignSaveBtn');
    if (!wrap) return;
    if (!state.drafts.length) {
      wrap.style.display = 'none';
      wrap.innerHTML = '';
      if (saveBtn) saveBtn.style.display = 'none';
      return;
    }
    wrap.style.display = 'block';
    if (saveBtn) saveBtn.style.display = 'inline-flex';
    var html = '<div class="ai-design-draft-toolbar">'
      + '<label><input type="checkbox" id="aiDesignSelectAll" checked> 全选</label>'
      + '<span class="ai-design-draft-count">共 ' + state.drafts.length + ' 条草案</span>'
      + '</div>';
    state.drafts.forEach(function (d, i) {
      var steps = (d.steps && d.steps.length) || 0;
      var role = ROLE_LABELS[d.case_role] || d.case_role || '业务';
      html += '<label class="ai-design-draft-card ai-design-draft-card-v2" style="animation-delay:' + (i * 60) + 'ms">'
        + '<input type="checkbox" class="ai-design-draft-cb" data-idx="' + i + '" checked>'
        + '<div class="ai-design-draft-card__body">'
        + '<strong>' + escapeHtml(d.case_name || ('用例 ' + (i + 1))) + '</strong>'
        + '<span class="ai-design-draft-meta">' + escapeHtml(role)
        + (d.design_method ? ' · ' + escapeHtml(d.design_method) : '')
        + ' · ' + steps + ' 步</span>'
        + (d.case_url ? '<span class="ai-design-draft-url">' + escapeHtml(d.case_url) + '</span>' : '')
        + '<p class="ai-design-draft-desc">' + escapeHtml((d.description || '').slice(0, 200)) + '</p>'
        + '</div></label>';
    });
    wrap.innerHTML = html;
    var all = document.getElementById('aiDesignSelectAll');
    if (all) {
      all.addEventListener('change', function () {
        wrap.querySelectorAll('.ai-design-draft-cb').forEach(function (cb) {
          cb.checked = all.checked;
        });
      });
    }
  }

  function getSelectedDrafts() {
    var wrap = document.getElementById('aiDesignDraftList');
    if (!wrap) return [];
    var selected = [];
    wrap.querySelectorAll('.ai-design-draft-cb').forEach(function (cb) {
      if (cb.checked) {
        var idx = parseInt(cb.getAttribute('data-idx'), 10);
        if (!isNaN(idx) && state.drafts[idx]) selected.push(state.drafts[idx]);
      }
    });
    return selected;
  }

  function loadProjects() {
    var sel = document.getElementById('aiDesignProject');
    if (!sel) return Promise.resolve();
    return fetch('/api/projects', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var list = (d && d.projects) || [];
        sel.innerHTML = '<option value="">请选择项目</option>';
        list.forEach(function (p) {
          var o = document.createElement('option');
          o.value = String(p.id || p.project_id || '');
          o.textContent = p.name || ('项目 ' + o.value);
          o.dataset.name = p.name || '';
          sel.appendChild(o);
        });
        var pid = qs('project_id');
        if (pid) {
          sel.value = pid;
          onProjectChange();
        }
      })
      .catch(function () {
        sel.innerHTML = '<option value="">加载项目失败</option>';
      });
  }

  function onProjectChange() {
    var sel = document.getElementById('aiDesignProject');
    var link = document.getElementById('aiDesignCasesLink');
    if (!sel) return;
    state.projectId = sel.value || '';
    var opt = sel.options[sel.selectedIndex];
    state.projectName = (opt && opt.dataset && opt.dataset.name) || (opt ? opt.textContent : '') || '';
    if (link && state.projectId) {
      link.href = '/list_cases_v2/' + encodeURIComponent(state.projectId);
      link.style.display = 'inline-flex';
    } else if (link) {
      link.style.display = 'none';
    }
  }

  function setPlatform(p) {
    state.platform = p || 'web';
    document.querySelectorAll('.ai-design-platform-btn').forEach(function (btn) {
      btn.classList.toggle('ai-design-platform-btn--active', btn.getAttribute('data-platform') === state.platform);
    });
  }

  function setBusy(busy) {
    state.busy = busy;
    var gen = document.getElementById('aiDesignGenerateBtn');
    var save = document.getElementById('aiDesignSaveBtn');
    if (gen) gen.disabled = busy;
    if (save) save.disabled = busy;
  }

  var DESIGN_THINKING_CHAIN = [
    { id: 'parse', text: '正在解析测试需求…', icon: '📝' },
    { id: 'probe', text: '正在探测目标页面元素…', icon: '🔍' },
    { id: 'analyze', text: '正在分析业务场景…', icon: '🧩' },
    { id: 'generate', text: '正在生成测试用例…', icon: '✨' },
    { id: 'validate', text: '正在验证步骤可行性…', icon: '🔒' }
  ];

  function showDesignThinking() {
    var panel = document.getElementById('aiDesignThinking');
    var inner = document.getElementById('aiDesignThinkingInner');
    if (!panel || !inner) return;
    inner.innerHTML = '';
    DESIGN_THINKING_CHAIN.forEach(function(s) {
      var row = document.createElement('div');
      row.className = 'ai-thinking-chain__step';
      row.id = 'aiDesignThink_' + s.id;
      row.innerHTML = '<span class="ai-thinking-chain__dot ai-thinking-chain__dot--wait"></span><span>' + s.icon + ' ' + s.text + '</span>';
      inner.appendChild(row);
    });
    panel.style.display = '';
  }

  function updateDesignThinkingStep(stepId, state) {
    var row = document.getElementById('aiDesignThink_' + stepId);
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

  function hideDesignThinking() {
    var panel = document.getElementById('aiDesignThinking');
    if (panel) panel.style.display = 'none';
  }

  function renderDesignSummary(count, warnings) {
    var container = document.getElementById('aiDesignSummary');
    if (!container) return;
    var html = '<div class="ai-task-summary__header">' +
      '<div><div class="ai-task-summary__title">生成完成</div>' +
      '<div class="ai-task-summary__meta">共生成 ' + count + ' 条用例草案</div></div>' +
      '<div class="ai-task-summary__ring" style="--pct:100%;"><span>100%</span></div>' +
      '</div>';
    if (warnings && warnings.length) {
      html += '<div style="margin-top:10px;font-size:12px;color:#64748b;">提示：' + warnings.join('；') + '</div>';
    }
    container.innerHTML = html;
    container.style.display = '';
  }

  function hideDesignSummary() {
    var el = document.getElementById('aiDesignSummary');
    if (el) el.style.display = 'none';
  }

  async function generateDrafts() {
    if (state.busy) return;
    if (!state.projectId) {
      alert('请先选择目标项目');
      return;
    }
    if (!hasRequirementsInput()) {
      alert('请上传需求文件或填写需求文本');
      return;
    }
    var out = document.getElementById('aiDesignBatchOut');
    setBusy(true);
    state.drafts = [];
    renderDraftList();
    hideDesignSummary();
    setStatus('正在根据需求生成用例草案（约 6–10 条），请稍候…', 'busy');
    showDesignThinking();
    updateDesignThinkingStep('parse', 'active');
    try {
      var model = await getSelectedModel();
      updateDesignThinkingStep('parse', 'done');
      updateDesignThinkingStep('probe', 'active');
      var result = await postDesignPreview({ model: model });
      var resp = result.resp;
      var data = result.data;
      if (!resp.ok || !data.success) {
        var err = (data && data.error) || ('请求失败 HTTP ' + resp.status);
        if (data && data.hint) err += '\n' + data.hint;
        throw new Error(err);
      }
      state.drafts = data.drafts || [];
      updateDesignThinkingStep('probe', 'done');
      updateDesignThinkingStep('analyze', 'done');
      updateDesignThinkingStep('generate', 'done');
      updateDesignThinkingStep('validate', 'done');
      renderDraftList();
      var msg = '已生成 ' + (data.draft_count || state.drafts.length) + ' 条草案（尚未写入项目）。请勾选后点击「保存到当前项目」。';
      if (data.warnings && data.warnings.length) {
        msg += '\n\n提示：\n' + data.warnings.join('\n');
      }
      setStatus('草案生成完成。', 'ok');
      if (out) typeText(out, msg);
      renderDesignSummary(data.draft_count || state.drafts.length, data.warnings);
      setTimeout(hideDesignThinking, 600);
    } catch (e) {
      setStatus('生成失败', 'err');
      hideDesignThinking();
      if (out) out.textContent = String(e && e.message ? e.message : e);
    } finally {
      setBusy(false);
    }
  }

  async function saveDrafts() {
    if (state.busy) return;
    if (!state.projectId) {
      alert('请先选择目标项目');
      return;
    }
    var selected = getSelectedDrafts();
    if (!selected.length) {
      alert('请至少勾选一条草案');
      return;
    }
    var out = document.getElementById('aiDesignBatchOut');
    setBusy(true);
    setStatus('正在保存到项目…', 'busy');
    try {
      var r = await fetch('/api/ai/hub/design/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          project_id: state.projectId,
          platform_type: state.platform === 'android' ? 'android' : state.platform,
          drafts: selected
        })
      });
      var data = await r.json().catch(function () { return {}; });
      if (!r.ok || !data.success) {
        throw new Error((data && data.error) || ('保存失败 HTTP ' + r.status));
      }
      var msg = '已保存 ' + (data.count || 0) + ' 个用例';
      if (data.created_case_ids && data.created_case_ids.length) {
        msg += '，ID: ' + data.created_case_ids.join(', ');
      }
      if (data.batch_id) msg += '\n批次: ' + data.batch_id;
      if (data.warnings && data.warnings.length) {
        msg += '\n\n提示：\n' + data.warnings.join('\n');
      }
      setStatus('保存完成。', 'ok');
      if (out) out.textContent = msg;
      state.drafts = [];
      renderDraftList();
      hideDesignSummary();
    } catch (e) {
      setStatus('保存失败', 'err');
      if (out) out.textContent = String(e && e.message ? e.message : e);
    } finally {
      setBusy(false);
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    loadProjects();
    loadAiDesignModels();
    var sel = document.getElementById('aiDesignProject');
    if (sel) sel.addEventListener('change', onProjectChange);
    var msel = document.getElementById('aiDesignModelSelect');
    if (msel) {
      msel.addEventListener('change', function () {
        void saveAiDesignActiveModel(msel.value);
      });
    }
    var genBtn = document.getElementById('aiDesignGenerateBtn');
    var saveBtn = document.getElementById('aiDesignSaveBtn');
    if (genBtn) genBtn.addEventListener('click', function () { void generateDrafts(); });
    if (saveBtn) saveBtn.addEventListener('click', function () { void saveDrafts(); });
    document.querySelectorAll('.ai-design-platform-btn').forEach(function (b) {
      b.addEventListener('click', function () { setPlatform(b.getAttribute('data-platform')); });
    });
    document.querySelectorAll('.ai-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var text = chip.getAttribute('data-example') || '';
        var ta = document.getElementById('aiDesignReqText');
        if (ta) { ta.value = text; ta.focus(); }
      });
    });
    setPlatform('web');
  });
})();
