/**
 * HuFirst AI 助手：与 UX_AI_INTERACTION_ROADMAP 对齐
 * - interaction_context 字段：focus_step_index、focus_step_indices、browser_selection_text、action_kind
 * - sessionStorage 会话草稿、可复用 fetch / payload 工具
 */
(function (global) {
  'use strict';

  var CHAT_HISTORY_KEY_PREFIX = 'hufirst_ai_history_case_';
  var SESSION_KEY = 'hufirst_ai_studio_session_v1';

  function _safeJsonParse(s, fallback) {
    try {
      return JSON.parse(s);
    } catch (e) {
      return fallback;
    }
  }

  function _filterUndefined(obj) {
    if (!obj || typeof obj !== 'object') return {};
    var o = {};
    for (var k in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, k) && obj[k] !== undefined && obj[k] !== null && obj[k] !== '') {
        o[k] = obj[k];
      }
    }
    return o;
  }

  /**
   * 将「交互情境」合并进 /api/ai/task/chat 请求体（仅附加非空字段，向后兼容）
   * ctx: { focus_step_index, focus_step_indices, browser_selection_text, selection_text, action_kind, intent }
   */
  function appendInteractionToPayload(base, ctx) {
    var b = base || {};
    var c = ctx || {};
    var out = {};
    for (var k in b) {
      if (Object.prototype.hasOwnProperty.call(b, k)) out[k] = b[k];
    }
    if (c.focus_step_index != null && c.focus_step_index !== '' && isFinite(Number(c.focus_step_index))) {
      out.focus_step_index = parseInt(c.focus_step_index, 10);
    }
    if (c.focus_step_indices && (Array.isArray(c.focus_step_indices) ? c.focus_step_indices.length : String(c.focus_step_indices).trim())) {
      if (Array.isArray(c.focus_step_indices)) {
        out.focus_step_indices = c.focus_step_indices.map(function (x) { return parseInt(x, 10); }).filter(function (n) { return isFinite(n); });
      } else {
        out.focus_step_indices = String(c.focus_step_indices).split(/[\s,;]+/).map(function (s) { return parseInt(s.trim(), 10); })
          .filter(function (n) { return isFinite(n) && n > 0; });
      }
    }
    var sel = c.browser_selection_text != null && String(c.browser_selection_text).trim()
      ? c.browser_selection_text
      : (c.selection_text != null ? c.selection_text : '');
    if (String(sel).trim()) {
      out.browser_selection_text = String(sel).trim();
      out.selection_text = out.browser_selection_text;
    }
    var ak = (c.action_kind || c.intent || '').trim();
    if (ak) {
      out.action_kind = ak;
      out.intent = ak;
    }
    return out;
  }

  function getInteractionContextFromForm(elPrefix) {
    var p = elPrefix || 'aiIx';
    function g(id) {
      var n = p + id;
      var e = document.getElementById(n);
      return e ? e.value : '';
    }
    var rawIdx = (g('FocusStep') || '').trim();
    var rawMulti = (g('FocusIndices') || '').trim();
    var act = (g('ActionKind') || '').trim();
    var sel = (g('SelectionText') || '').trim();
    var ctx = {};
    if (rawIdx && isFinite(parseInt(rawIdx, 10))) ctx.focus_step_index = parseInt(rawIdx, 10);
    if (rawMulti) ctx.focus_step_indices = rawMulti;
    if (act) ctx.action_kind = act;
    if (sel) ctx.browser_selection_text = sel;
    return ctx;
  }

  function clearInteractionForm(elPrefix) {
    var p = elPrefix || 'aiIx';
    var ids = ['FocusStep', 'FocusIndices', 'ActionKind', 'SelectionText'];
    ids.forEach(function (s) {
      var e = document.getElementById(p + s);
      if (e) {
        if (e.tagName === 'SELECT') e.selectedIndex = 0; else e.value = '';
      }
    });
  }

  function insertSelectionFromDocument(elPrefix) {
    var t = '';
    try {
      t = (global.getSelection() && global.getSelection().toString()) || '';
    } catch (e) {}
    t = (t || '').trim();
    if (!t) {
      if (global.alert) global.alert('请先在页面上划选文字（跨域 iframe 内选区可能无法读取）。');
      return;
    }
    var p = elPrefix || 'aiIx';
    var e = document.getElementById(p + 'SelectionText');
    if (e) e.value = t;
  }

  function dbStepToPlanStep(s) {
    if (!s) return {};
    return {
      action: s.action || '',
      selector_type: s.selector_type || 'css',
      selector_value: s.selector_value != null ? String(s.selector_value) : '',
      input_value: s.input_value != null ? String(s.input_value) : '',
      description: s.description != null ? String(s.description) : '',
      step_order: s.step_order,
      url: s.url != null ? String(s.url) : undefined,
      enter_iframe: !!s.enter_iframe,
      iframe_selector: s.iframe_selector || '',
      locator_candidates: s.locator_candidates || undefined,
      click_repeat_count: s.click_repeat_count
    };
  }

  function fetchAllCaseSteps(caseId) {
    var all = [];
    var page = 1;
    var total = 0;
    return (function next() {
      return fetch('/api/cases/' + caseId + '/steps?page=' + page + '&page_size=100', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var steps = data.steps || [];
          total = data.total != null ? data.total : steps.length;
          for (var i = 0; i < steps.length; i++) all.push(steps[i]);
          if (all.length < total && steps.length > 0) {
            page += 1;
            return next();
          }
          return all;
        });
    })();
  }

  function buildCurrentPlanFromCase(testCase, steps) {
    var list = (steps || []).map(dbStepToPlanStep);
    return {
      case_name: (testCase && testCase.name) || '',
      case_url: (testCase && testCase.url) || '',
      description: (testCase && testCase.description) || '',
      precondition: (testCase && testCase.precondition) || '',
      expected_result: (testCase && testCase.expected_result) || '',
      steps: list
    };
  }

  function getActiveProfileId() {
    return fetch('/api/ai/models', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) return '';
        return (d.active_profile_id != null) ? d.active_profile_id : '';
      })
      .catch(function () { return ''; });
  }

  function getProjectName(projectId) {
    if (!projectId) return Promise.resolve('');
    return fetch('/api/projects/' + projectId, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.project && d.project.name) return d.project.name;
        if (d.name) return d.name;
        return '';
      })
      .catch(function () { return ''; });
  }

  /**
   * 路线图「底角一句状态」：由 current_plan 拼一句（不依赖新 API）
   */
  function formatPlanStatusLine(plan) {
    if (!plan || typeof plan !== 'object') return '';
    var steps = plan.steps;
    var n = Array.isArray(steps) ? steps.length : 0;
    var wList = plan.warnings || (plan.meta && plan.meta.normalization_warnings) || [];
    var wN = Array.isArray(wList) ? wList.length : 0;
    var name = (plan.case_name && String(plan.case_name).trim()) ? String(plan.case_name).slice(0, 40) : '';
    var parts = ['共 ' + n + ' 步'];
    if (wN) parts.push('归一化提示 ' + wN + ' 条');
    if (name) parts.push('《' + name + '》');
    return parts.join(' · ');
  }

  function postJson(url, body, options) {
    var opts = options || {};
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: opts.signal,
      credentials: 'same-origin'
    }).then(function (resp) {
      return resp.text().then(function (raw) {
        var data;
        try { data = JSON.parse(raw); } catch (e) { throw new Error('非 JSON: ' + (raw || '').slice(0, 120)); }
        if (!resp.ok || !data.success) {
          var em = (data && data.error) ? String(data.error) : ('HTTP ' + resp.status);
          if (data && data.hint) em += '\n\n——\n' + String(data.hint);
          throw new Error(em);
        }
        return data;
      });
    });
  }

  function saveStudioSession(st) {
    try {
      var payload = { ts: Date.now(), v: 1, plan: st.plan, history: st.history || [] };
      if (st.projectId != null) payload.project_id = st.projectId;
      if (st.projectName) payload.project_name = st.projectName;
      if (st.targetPageUrl) payload.target_page_url = st.targetPageUrl;
      global.sessionStorage.setItem(SESSION_KEY, JSON.stringify(payload));
    } catch (e) {}
  }

  function loadStudioSession() {
    try {
      var raw = global.sessionStorage.getItem(SESSION_KEY);
      if (!raw) return null;
      return _safeJsonParse(raw, null);
    } catch (e) {
      return null;
    }
  }

  function saveCaseChatHistory(caseId, history) {
    if (!caseId) return;
    try {
      global.sessionStorage.setItem(CHAT_HISTORY_KEY_PREFIX + caseId, JSON.stringify(history || []));
    } catch (e) {}
  }

  function loadCaseChatHistory(caseId) {
    if (!caseId) return [];
    try {
      var raw = global.sessionStorage.getItem(CHAT_HISTORY_KEY_PREFIX + caseId);
      if (!raw) return [];
      var h = _safeJsonParse(raw, []);
      return Array.isArray(h) ? h : [];
    } catch (e) {
      return [];
    }
  }

  // --- 步骤页：浮层 + 可拖拽（最小实现）---
  function mountStepsPageAssistant(options) {
    var opt = options || {};
    if (document.getElementById('hufirst-ai-steps-assistant')) return { destroy: function () {} };

    var style = document.createElement('style');
    style.id = 'hufirst-ai-assistant-style';
    style.textContent = [
      '#hufirst-ai-steps-assistant{position:fixed;z-index:10020;font-family:Segoe UI,system-ui,sans-serif;}',
      '#hufirst-ai-fab{width:56px;height:56px;border-radius:50%;border:none;cursor:pointer;box-shadow:0 4px 20px rgba(79,70,229,.45);',
      'background:linear-gradient(135deg,#4f46e5,#7c3aed);color:#fff;font-size:22px;position:fixed;right:20px;bottom:24px;z-index:10021;transition:transform .2s;}',
      '#hufirst-ai-fab:hover{transform:scale(1.05);}',
      '#hufirst-ai-spanel{position:fixed;right:20px;bottom:88px;width:min(420px,96vw);max-height:78vh;overflow:hidden;display:none;flex-direction:column;border-radius:16px;box-shadow:0 20px 50px rgba(0,0,0,.2);background:#fff;border:1px solid #e2e8f0;z-index:10022;}',
      'html.dark #hufirst-ai-spanel{background:#1e1b4b;border-color:#4338ca;}',
      '#hufirst-ai-spanel.hufirst-ai-open{display:flex;}',
      '#hufirst-ai-shead{flex:0 0 auto;padding:10px 12px;cursor:move;user-select:none;font-weight:700;font-size:14px;',
      'background:linear-gradient(135deg,#4f46e5,#6366f1);color:#fff;border-radius:16px 16px 0 0;display:flex;justify-content:space-between;align-items:center;gap:8px;}',
      '#hufirst-ai-sbody{flex:1;overflow-y:auto;padding:10px 12px;font-size:13px;max-height:48vh;}',
      '#hufirst-ai-sfoot{flex:0 0 auto;padding:10px 12px;border-top:1px solid #e2e8f0;}',
      'html.dark #hufirst-ai-sfoot{border-color:#4338ca;}',
      '#hufirst-ai-slog{min-height:48px;max-height:120px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;background:#f8fafc;padding:8px;border-radius:8px;font-size:12px;margin-bottom:8px;}',
      'html.dark #hufirst-ai-slog{background:#0f172a;}',
      '.hufirst-ai-srow{margin-bottom:8px;}',
      '.hufirst-ai-srow input,.hufirst-ai-srow select,.hufirst-ai-srow textarea{width:100%;box-sizing:border-box;border-radius:8px;border:1px solid #cbd5e1;padding:6px 8px;font-size:12px;}',
      '.hufirst-ai-btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;}',
      '.hufirst-ai-btns button{font-size:11px;padding:4px 8px;border-radius:6px;border:1px solid #c7d2fe;background:#eef2ff;cursor:pointer;}',
      '#hufirst-ai-status{font-size:12px;color:#64748b;line-height:1.45;white-space:pre-wrap;word-break:break-word;max-height:160px;overflow-y:auto;}',
      'html.dark #hufirst-ai-status{color:#94a3b8;}',
      '.hufirst-ai-help{font-size:12px;color:#475569;background:#f1f5f9;padding:8px 10px;border-radius:8px;margin-bottom:10px;line-height:1.5;}',
      '.hufirst-ai-help summary{cursor:pointer;font-weight:600;}',
      '.hufirst-ai-help ol{margin:8px 0 0 18px;padding:0;}',
      '.hufirst-ai-help code{font-size:11px;background:#e2e8f0;padding:1px 4px;border-radius:4px;}',
      '.hufirst-ai-help a{color:#4f46e5;}',
      'html.dark .hufirst-ai-help{color:#cbd5e1;background:#1e293b;}',
      'html.dark .hufirst-ai-help code{background:#334155;}',
      'html.dark .hufirst-ai-help a{color:#a5b4fc;}'
    ].join('');
    document.head.appendChild(style);

    var root = document.createElement('div');
    root.id = 'hufirst-ai-steps-assistant';
    root.innerHTML = [
      '<button type="button" id="hufirst-ai-fab" title="步骤 AI 助手" aria-label="打开步骤 AI 助手">💬</button>',
      '<div id="hufirst-ai-spanel" aria-label="步骤 AI 助手">',
      '  <div id="hufirst-ai-shead">',
      '    <span>步骤 AI 助手</span>',
      '    <span><button type="button" id="hufirst-ai-sclose" style="background:rgba(255,255,255,.2);border:none;color:#fff;border-radius:6px;cursor:pointer;padding:2px 8px">✕</button></span>',
      '  </div>',
      '  <div id="hufirst-ai-sbody">',
      '    <details class="hufirst-ai-help"><summary>怎么用？（必读）</summary>',
      '      <ol>',
      '        <li>助手会把<strong>当前用例的全部步骤</strong>发给<strong>本机 Ollama</strong>（网址默认 <code>http://127.0.0.1:11434</code>）。这和「能不能列出模型」不是一回事：列表很快，真正改写可能要<strong>几分钟</strong>。</li>',
      '        <li>打开面板后会自动<strong>同步步骤</strong>；你在网页里改过步骤后，请再点<strong>刷新同步</strong>。</li>',
      '        <li>流程：写清楚需求 → 点<strong>发送</strong> → 等状态变为「已更新」→ 需要时再点<strong>追加到用例</strong>（会把 AI 给出的步骤<strong>接到现有步骤后面</strong>，不会自动替换原步骤）。</li>',
      '        <li>模型、超时请在 <a href="/ai-test" target="_blank" rel="noopener">AI测试</a> 配置。若总是卡住或超时，优先换成<strong>纯文字对话模型</strong>（名称里带 <code>-vl</code> 的多半是视觉模型，在本页往往很慢）。</li>',
      '      </ol>',
      '    </details>',
      '    <div class="hufirst-ai-srow" id="hufirst-ai-status">正在同步步骤…</div>',
      '    <div class="hufirst-ai-srow"><label>任务类型（可选）</label><select id="hufirst-s-ctx-kind"><option value="">自动理解你的描述</option>',
      '    <option value="optimize_step">优化某一步</option>',
      '    <option value="merge_steps">把多步合并成一步</option>',
      '    <option value="assert_from_selection">根据划词做断言</option></select></div>',
      '    <div class="hufirst-ai-srow"><label>针对第几步（从 1 开始，可选）</label><input type="number" id="hufirst-s-focus" min="1" placeholder="例如 2" /></div>',
      '    <div class="hufirst-ai-srow"><label>涉及哪些步（合并时填，如 2,3）</label><input type="text" id="hufirst-s-idx" placeholder="2,3" /></div>',
      '    <div class="hufirst-ai-srow"><label>页面划选的文案（可选）</label><textarea id="hufirst-s-selection" rows="2" placeholder="在页面上选中文字后，点下方「填入划词」"></textarea></div>',
      '    <div class="hufirst-ai-btns">',
      '      <button type="button" id="hufirst-s-sync">刷新同步步骤</button>',
      '      <button type="button" id="hufirst-s-btn-pick">填入划词</button>',
      '      <button type="button" id="hufirst-tpl-opt">一键：优化这一步</button>',
      '      <button type="button" id="hufirst-tpl-merge">一键：合并多步</button>',
      '      <button type="button" id="hufirst-tpl-assert">一键：断言划词</button>',
      '      <button type="button" id="hufirst-s-clear-hist" title="只清空本浏览器里记录的对话摘要，不会删用例步骤">清空对话记录</button>',
      '    </div>',
      '    <div id="hufirst-ai-slog" style="margin-top:8px;"></div>',
      '  </div>',
      '  <div id="hufirst-ai-sfoot">',
      '    <textarea id="hufirst-s-msg" rows="3" style="width:100%;box-sizing:border-box;border-radius:8px;border:1px solid #cbd5e1;padding:8px" placeholder="用一句话说明你想怎么改，例如：把第 2 步的选择器改稳一点并加 3 秒等待"></textarea>',
      '    <div style="display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap;">',
      '      <button type="button" class="btn btn-primary" id="hufirst-s-send" style="padding:6px 14px;cursor:pointer;">发送给 AI</button>',
      '      <button type="button" id="hufirst-s-apply" style="padding:6px 10px;cursor:pointer;" disabled>追加到用例末尾</button>',
      '    </div>',
      '  </div>',
      '</div>'
    ].join('');
    document.body.appendChild(root);

    var state = { current_plan: { steps: [] }, history: [], lastPlan: null, projectName: '', model: '' };

    function setStatus(s) {
      var el = document.getElementById('hufirst-ai-status');
      if (el) el.textContent = s;
    }
    function logLine(role, line) {
      var log = document.getElementById('hufirst-ai-slog');
      if (!log) return;
      var prefix = role === 'user' ? '你: ' : 'AI: ';
      log.textContent += (log.textContent ? '\n' : '') + prefix + line;
    }

    function getCaseSync() {
      return Promise.resolve()
        .then(function () { return getActiveProfileId(); })
        .then(function (mid) {
          state.model = (mid != null && String(mid).trim() !== '') ? String(mid).trim() : '';
          return getProjectName(opt.getProjectId && opt.getProjectId());
        })
        .then(function (pn) { state.projectName = pn; return fetchAllCaseSteps(opt.getCaseId()); })
        .then(function (stepRows) {
          return fetch('/api/cases/' + opt.getCaseId(), { credentials: 'same-origin' }).then(function (r) { return r.json(); })
            .then(function (cdata) {
              var tc = (cdata && cdata.test_case) || {};
              state.current_plan = buildCurrentPlanFromCase(tc, stepRows);
              setStatus('已同步 ' + (stepRows.length || 0) + ' 条步骤，可以输入需求并点「发送给 AI」。');
            });
        })
        .catch(function (e) { setStatus('同步失败: ' + (e.message || e)); });
    }

    function send() {
      var msg = (document.getElementById('hufirst-s-msg') && document.getElementById('hufirst-s-msg').value || '').trim();
      if (!msg) { if (global.alert) global.alert('请先在下框里写清楚想怎么改，再发送。'); return; }
      var ctx = {
        focus_step_index: (document.getElementById('hufirst-s-focus') || {}).value,
        focus_step_indices: (document.getElementById('hufirst-s-idx') || {}).value,
        browser_selection_text: (document.getElementById('hufirst-s-selection') || {}).value
      };
      var k = (document.getElementById('hufirst-s-ctx-kind') || {}).value;
      if (k) ctx.action_kind = k;
      setStatus('已向本机模型发送请求，若步骤多或模型较慢，可能需要等待较长时间（请勿重复狂点发送）…');
      logLine('user', msg);
      var body = { message: msg, project_name: state.projectName || '', current_plan: state.current_plan, history: state.history, target_page_url: (opt.getTargetUrl && opt.getTargetUrl()) || '' };
      if (state.model) body.model = state.model;
      body = appendInteractionToPayload(body, ctx);
      return postJson('/api/ai/task/chat', body)
        .then(function (data) {
          state.lastPlan = data.plan;
          if (data.plan) state.current_plan = data.plan;
          state.history = state.history || [];
          state.history.push({ role: 'user', content: msg });
          state.history.push({ role: 'assistant', content: '已更新方案（' + ((data.plan && data.plan.steps) ? data.plan.steps.length : 0) + ' 步）' });
          if (state.history.length > 40) state.history = state.history.slice(-40);
          saveCaseChatHistory(opt.getCaseId(), state.history);
          setStatus('模型已返回方案。若满意，可点「追加到用例末尾」把新步骤接到当前用例后面。');
          logLine('assistant', '已返回 ' + (data.plan && data.plan.steps ? data.plan.steps.length : 0) + ' 步。');
          var ap = document.getElementById('hufirst-s-apply');
          if (ap) ap.disabled = !(data.plan && data.plan.steps && data.plan.steps.length);
        })
        .catch(function (e) { setStatus('出错：\n' + (e.message || e)); logLine('assistant', '失败: ' + (e.message || e)); });
    }

    function applyAppend() {
      if (!state.lastPlan || !state.lastPlan.steps || !state.lastPlan.steps.length) return;
      return postJson('/api/ai/cases/append-steps', { case_id: opt.getCaseId(), steps: state.lastPlan.steps })
        .then(function (r) {
          if (opt.onApplied) opt.onApplied(r);
        })
        .catch(function (e) { if (global.alert) global.alert('追加失败: ' + (e.message || e)); });
    }

    document.getElementById('hufirst-ai-fab').addEventListener('click', function () {
      var p = document.getElementById('hufirst-ai-spanel');
      if (p) p.classList.toggle('hufirst-ai-open');
    });
    (function () {
      var c = document.getElementById('hufirst-ai-sclose');
      if (c) c.addEventListener('click', function () { var p = document.getElementById('hufirst-ai-spanel'); if (p) p.classList.remove('hufirst-ai-open'); });
    })();
    document.getElementById('hufirst-s-sync').addEventListener('click', function () { getCaseSync(); });
    document.getElementById('hufirst-s-btn-pick').addEventListener('click', function () {
      var t = ''; try { t = (global.getSelection() && global.getSelection().toString()) || ''; } catch (e) {}
      t = t.trim();
      if (t) { var e = document.getElementById('hufirst-s-selection'); if (e) e.value = t; }
      else { if (global.alert) global.alert('请先在页面上用鼠标划选一段文字，再点「填入划词」。'); }
    });
    document.getElementById('hufirst-tpl-opt').addEventListener('click', function () {
      var n = (document.getElementById('hufirst-s-focus') || {}).value;
      (document.getElementById('hufirst-s-ctx-kind') || {}).value = 'optimize_step';
      (document.getElementById('hufirst-s-msg') || {}).value = '请优化第' + (n || 'N') + '步：在必要处增加等待或更稳的选择器。';
    });
    document.getElementById('hufirst-tpl-merge').addEventListener('click', function () {
      (document.getElementById('hufirst-s-ctx-kind') || {}).value = 'merge_steps';
      (document.getElementById('hufirst-s-msg') || {}).value = '将下列步骤合并为一步，并保持语义不变。';
    });
    document.getElementById('hufirst-tpl-assert').addEventListener('click', function () {
      (document.getElementById('hufirst-s-ctx-kind') || {}).value = 'assert_from_selection';
      (document.getElementById('hufirst-s-msg') || {}).value = '为当前划词/子串增加可见性断言或 verify 步骤。';
    });
    document.getElementById('hufirst-s-send').addEventListener('click', function () { send(); });
    document.getElementById('hufirst-s-apply').addEventListener('click', function () { applyAppend(); });
    document.getElementById('hufirst-s-clear-hist').addEventListener('click', function () {
      state.history = [];
      saveCaseChatHistory(opt.getCaseId(), state.history);
      var log = document.getElementById('hufirst-ai-slog');
      if (log) log.textContent = '';
      setStatus('已清空对话摘要（不会删除用例里的步骤）。');
    });
    // drag
    (function () {
      var head = document.getElementById('hufirst-ai-shead');
      var panel = document.getElementById('hufirst-ai-spanel');
      if (!head || !panel) return;
      var dx, dy, ox, oy, drag = false;
      head.addEventListener('mousedown', function (e) { drag = true; dx = e.clientX; dy = e.clientY; var r = panel.getBoundingClientRect(); ox = r.left; oy = r.top; e.preventDefault(); });
      global.addEventListener('mousemove', function (e) {
        if (!drag) return;
        var nx = ox + (e.clientX - dx);
        var ny = oy + (e.clientY - dy);
        panel.style.left = nx + 'px';
        panel.style.top = ny + 'px';
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
        panel.style.position = 'fixed';
      });
      global.addEventListener('mouseup', function () { drag = false; });
    })();

    state.history = loadCaseChatHistory(opt.getCaseId());
    (function hydrateLog() {
      var log = document.getElementById('hufirst-ai-slog');
      if (!log || !state.history || !state.history.length) return;
      var lines = state.history.slice(-12);
      log.textContent = lines.map(function (h) { return (h.role === 'user' ? '你: ' : 'AI: ') + (h.content || '').replace(/\n/g, '↵'); }).join('\n');
    })();
    getCaseSync();
    if (opt.onReady) setTimeout(function () { try { opt.onReady(state); } catch (e) {} }, 0);

    var api = {
      open: function () { var p = document.getElementById('hufirst-ai-spanel'); if (p) p.classList.add('hufirst-ai-open'); },
      clearLocalHistory: function () {
        state.history = [];
        saveCaseChatHistory(opt.getCaseId(), []);
        var log = document.getElementById('hufirst-ai-slog');
        if (log) log.textContent = '';
      },
      openOptimize: function (oneBasedStep) {
        this.open();
        if (oneBasedStep) { var f = document.getElementById('hufirst-s-focus'); if (f) f.value = String(oneBasedStep); }
        (document.getElementById('hufirst-s-ctx-kind') || {}).value = 'optimize_step';
        (document.getElementById('hufirst-s-msg') || {}).value = '请优化第' + (oneBasedStep || '') + '步：在必要处增加等待或更稳的选择器。';
      },
      openMerge: function (a, b) {
        this.open();
        (document.getElementById('hufirst-s-ctx-kind') || {}).value = 'merge_steps';
        if (a && b) (document.getElementById('hufirst-s-idx') || {}).value = a + ',' + b;
        (document.getElementById('hufirst-s-msg') || {}).value = '将第 ' + a + ' 与第 ' + b + ' 步合并为一步。';
      }
    };
    global.HuFirstAiStepsPanel = api;
    return { destroy: function () { var r = document.getElementById('hufirst-ai-steps-assistant'); if (r) r.remove(); var s = document.getElementById('hufirst-ai-assistant-style'); if (s) s.remove(); } };
  }

  var HuFirstAiAssistant = {
    formatPlanStatusLine: formatPlanStatusLine,
    appendInteractionToPayload: appendInteractionToPayload,
    getInteractionContextFromForm: getInteractionContextFromForm,
    clearInteractionForm: clearInteractionForm,
    insertSelectionFromDocument: insertSelectionFromDocument,
    dbStepToPlanStep: dbStepToPlanStep,
    fetchAllCaseSteps: fetchAllCaseSteps,
    buildCurrentPlanFromCase: buildCurrentPlanFromCase,
    getActiveProfileId: getActiveProfileId,
    getProjectName: getProjectName,
    postJson: postJson,
    saveStudioSession: saveStudioSession,
    loadStudioSession: loadStudioSession,
    saveCaseChatHistory: saveCaseChatHistory,
    loadCaseChatHistory: loadCaseChatHistory,
    mountStepsPageAssistant: mountStepsPageAssistant
  };

  global.HuFirstAiAssistant = HuFirstAiAssistant;
})(typeof window !== 'undefined' ? window : this);
