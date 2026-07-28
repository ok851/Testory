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
  /** 过滤仅面向开发者的画布/网关内部提示，避免步骤页助手打扰用户。 */
  function filterUserFacingWarnings(warnings) {
    if (!warnings || !warnings.length) return [];
    return warnings.filter(function (w) {
      var s = String(w == null ? '' : w);
      if (!s.trim()) return false;
      if (s.indexOf('未连接内置画布') >= 0) return false;
      if (s.indexOf('内置画布网关') >= 0 && s.indexOf('跳过') >= 0) return false;
      if (s.indexOf('embedded_session_id') >= 0) return false;
      return true;
    });
  }

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

  function pollAiJobUntilDone(jobId) {
    var polls = 0;
    function tick() {
      return fetch('/api/ai/task/job/' + encodeURIComponent(jobId), { credentials: 'same-origin' })
        .then(function (resp) {
          return resp.json().then(function (j) { return { resp: resp, j: j }; });
        })
        .then(function (o) {
          if (o.resp.status === 404) throw new Error('任务不存在');
          if (!o.j.success) throw new Error(o.j.error || '查询任务失败');
          if (o.j.status === 'running') {
            polls += 1;
            var delay = polls <= 24 ? 400 : 1000;
            return new Promise(function (res) { setTimeout(res, delay); }).then(tick);
          }
          if (o.j.status === 'cancelled') throw new Error('已取消');
          if (o.j.status === 'error') {
            var r = o.j.result || {};
            throw new Error(o.j.error || r.error || '生成失败');
          }
          if (o.j.status === 'done') {
            var result = o.j.result;
            if (!result || !result.success) throw new Error((result && result.error) || '生成失败');
            return result;
          }
          polls += 1;
          return new Promise(function (res) { setTimeout(res, 500); }).then(tick);
        });
    }
    return tick();
  }

  function startChatAsync(body) {
    return fetch('/api/ai/task/chat-async', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body)
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success || !d.job_id) throw new Error(d.error || '启动异步任务失败');
        return d.job_id;
      });
  }

  function formatStepDiffSummary(beforePlan, afterPlan) {
    var beforeN = (beforePlan && beforePlan.steps && beforePlan.steps.length) || 0;
    var afterN = (afterPlan && afterPlan.steps && afterPlan.steps.length) || 0;
    var parts = ['步骤数：' + beforeN + ' → ' + afterN];
    function stepLine(s, i) {
      if (!s) return '';
      return (i + 1) + '. ' + (s.action || '?') + (s.description ? ' — ' + String(s.description).slice(0, 40) : '');
    }
    if (afterN && afterPlan.steps[0]) parts.push('首步：' + stepLine(afterPlan.steps[0], 0));
    if (afterN > 1 && afterPlan.steps[afterN - 1]) parts.push('末步：' + stepLine(afterPlan.steps[afterN - 1], afterN - 1));
    return parts.join('\n');
  }

  function defaultApplyMode(actionKind, message) {
    var k = (actionKind || '').trim();
    if (k === 'optimize_step' || k === 'merge_steps' || k === 'assert_from_selection') return 'replace';
    var m = (message || '').toLowerCase();
    if (/追加|添加|再加|末尾|后面再加/.test(message || '')) return 'append';
    return 'replace';
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
      /* 执行悬浮窗在 fab 上方（base.html globalRunBanner bottom:5.75rem），避免重叠 */      '#hufirst-ai-fab:hover{transform:scale(1.05);}',
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
      '        <li>流程：写清楚需求 → 点<strong>发送</strong> → 查看变更预览 → 点<strong>替换用例步骤</strong>（优化/合并时推荐）或<strong>追加到末尾</strong>。</li>',
      '        <li>模型、超时请在 <a href="/ai-hub" target="_blank" rel="noopener">AI 中心</a> → <a href="/ai-test" target="_blank" rel="noopener">自主测试</a> 配置。</li>',
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
      '    <div id="hufirst-ai-sdiff" class="ai-diff-box" style="display:none;"></div>',
      '    <div id="hufirst-ai-slog" style="margin-top:8px;"></div>',
      '  </div>',
      '  <div id="hufirst-ai-sfoot">',
      '    <textarea id="hufirst-s-msg" rows="3" style="width:100%;box-sizing:border-box;border-radius:8px;border:1px solid #cbd5e1;padding:8px" placeholder="用一句话说明你想怎么改，例如：把第 2 步的选择器改稳一点并加 3 秒等待"></textarea>',
      '    <div style="display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap;">',
      '      <button type="button" class="btn btn-primary" id="hufirst-s-send" style="padding:6px 14px;cursor:pointer;">发送给 AI</button>',
      '      <button type="button" class="btn btn-primary" id="hufirst-s-apply-replace" style="padding:6px 10px;cursor:pointer;" disabled>替换用例步骤</button>',
      '      <button type="button" id="hufirst-s-apply" style="padding:6px 10px;cursor:pointer;" disabled>追加到末尾</button>',
      '    </div>',
      '  </div>',
      '</div>'
    ].join('');
    document.body.appendChild(root);

    var state = {
      current_plan: { steps: [] },
      planBeforeSend: null,
      history: [],
      lastPlan: null,
      projectName: '',
      model: '',
      applyMode: 'replace',
      lastWarnings: []
    };

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

    function showDiff() {
      var box = document.getElementById('hufirst-ai-sdiff');
      if (!box || !state.lastPlan) return;
      var txt = formatStepDiffSummary(state.planBeforeSend || state.current_plan, state.lastPlan);
      if (state.lastWarnings && state.lastWarnings.length) {
        txt += '\n提示：' + state.lastWarnings.slice(0, 5).join('；');
      }
      box.textContent = txt;
      box.style.display = 'block';
    }

    function enableApplyButtons(enabled) {
      var ap = document.getElementById('hufirst-s-apply');
      var rp = document.getElementById('hufirst-s-apply-replace');
      if (ap) ap.disabled = !enabled;
      if (rp) rp.disabled = !enabled;
    }

    function onChatResult(data, msg, actionKind) {
      state.lastPlan = data.plan;
      state.lastWarnings = filterUserFacingWarnings(data.warnings || []);
      if (data.plan) state.current_plan = data.plan;
      state.applyMode = defaultApplyMode(actionKind, msg);
      state.history = state.history || [];
      state.history.push({ role: 'user', content: msg });
      state.history.push({
        role: 'assistant',
        content: '已更新方案（' + ((data.plan && data.plan.steps) ? data.plan.steps.length : 0) + ' 步）',
        warningsList: state.lastWarnings
      });
      if (state.history.length > 40) state.history = state.history.slice(-40);
      saveCaseChatHistory(opt.getCaseId(), state.history);
      var modeHint = state.applyMode === 'append' ? '追加到末尾' : '替换用例步骤';
      setStatus('模型已返回。建议点击「' + modeHint + '」应用变更（见下方预览）。');
      logLine('assistant', '已返回 ' + (data.plan && data.plan.steps ? data.plan.steps.length : 0) + ' 步。');
      if (state.lastWarnings.length) logLine('assistant', '提示: ' + state.lastWarnings.join('；'));
      showDiff();
      enableApplyButtons(!!(data.plan && data.plan.steps && data.plan.steps.length));
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
      state.planBeforeSend = JSON.parse(JSON.stringify(state.current_plan || { steps: [] }));
      var diffBox = document.getElementById('hufirst-ai-sdiff');
      if (diffBox) { diffBox.style.display = 'none'; diffBox.textContent = ''; }
      enableApplyButtons(false);
      setStatus('已提交异步任务，正在等待本机模型（可继续浏览页面，请勿重复发送）…');
      logLine('user', msg);
      var body = {
        message: msg,
        project_name: state.projectName || '',
        current_plan: state.current_plan,
        history: state.history,
        target_page_url: (opt.getTargetUrl && opt.getTargetUrl()) || '',
        case_id: opt.getCaseId(),
        response_mode: defaultApplyMode(k, msg) === 'append' ? 'delta' : 'full'
      };
      if (state.model) body.model = state.model;
      body = appendInteractionToPayload(body, ctx);
      var sendBtn = document.getElementById('hufirst-s-send');
      if (sendBtn) sendBtn.disabled = true;
      return startChatAsync(body)
        .then(function (jobId) { return pollAiJobUntilDone(jobId); })
        .then(function (data) { onChatResult(data, msg, k); })
        .catch(function (e) {
          setStatus('出错：\n' + (e.message || e));
          logLine('assistant', '失败: ' + (e.message || e));
        })
        .finally(function () { if (sendBtn) sendBtn.disabled = false; });
    }

    function applyAppend() {
      if (!state.lastPlan || !state.lastPlan.steps || !state.lastPlan.steps.length) return;
      return postJson('/api/ai/cases/append-steps', { case_id: opt.getCaseId(), steps: state.lastPlan.steps })
        .then(function (r) {
          var w = (r && r.warnings) || state.lastWarnings || [];
          if (w.length && global.alert) global.alert('已追加。提示：\n' + w.join('\n'));
          if (opt.onApplied) opt.onApplied(r);
          return getCaseSync();
        })
        .catch(function (e) { if (global.alert) global.alert('追加失败: ' + (e.message || e)); });
    }

    function applyReplace() {
      if (!state.lastPlan || !state.lastPlan.steps || !state.lastPlan.steps.length) return;
      return postJson('/api/ai/cases/import-ui-plan', {
        case_id: opt.getCaseId(),
        steps: state.lastPlan.steps,
        replace: true,
        goal: (document.getElementById('hufirst-s-msg') && document.getElementById('hufirst-s-msg').value) || ''
      })
        .then(function (r) {
          var w = (r && r.warnings) || state.lastWarnings || [];
          if (w.length && global.alert) global.alert('已替换用例步骤。提示：\n' + w.join('\n'));
          if (opt.onApplied) opt.onApplied(r);
          return getCaseSync();
        })
        .catch(function (e) { if (global.alert) global.alert('替换失败: ' + (e.message || e)); });
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
    var repBtn = document.getElementById('hufirst-s-apply-replace');
    if (repBtn) repBtn.addEventListener('click', function () { applyReplace(); });
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

  // ============================================================
  // 思考气泡：AI 推理过程中实时展示步骤进展
  // ============================================================

  /**
   * 在指定容器（通常是 ai-chat-log）底部创建一个思考气泡。
   * 返回气泡 DOM 元素（内含 .ai-thinking-bubble__inner）。
   * @param {HTMLElement} container - 聊天消息容器
   * @returns {HTMLElement} 气泡根元素
   */
  function showThinkingBubble(container) {
    if (!container) return null;
    // 移除之前残留的气泡（防止重复）
    removeThinkingBubble(container);
    var wrap = document.createElement('div');
    wrap.className = 'ai-thinking-bubble';
    wrap.setAttribute('role', 'status');
    wrap.setAttribute('aria-live', 'polite');
    var inner = document.createElement('div');
    inner.className = 'ai-thinking-bubble__inner';
    wrap.appendChild(inner);
    container.appendChild(wrap);
    container.scrollTop = container.scrollHeight;
    return wrap;
  }

  /**
   * 在思考气泡中追加或更新一个步骤。
   * @param {HTMLElement} bubble - showThinkingBubble 返回的元素
   * @param {string} stepText - 步骤描述文字
   * @param {boolean} [done=false] - 是否已完成（绿色圆点 + 灰色文字）
   * @returns {HTMLElement} 新增的步骤行元素
   */
  function updateThinkingStep(bubble, stepText, done) {
    if (!bubble) return null;
    var inner = bubble.querySelector('.ai-thinking-bubble__inner') || bubble;
    var row = document.createElement('div');
    row.className = 'ai-thinking-step' + (done ? ' ai-thinking-step--done' : '');
    var dot = document.createElement('span');
    dot.className = 'ai-thinking-step__dot';
    var label = document.createElement('span');
    label.textContent = stepText || '';
    row.appendChild(dot);
    row.appendChild(label);
    inner.appendChild(row);
    // 自动滚动到气泡底部
    var log = bubble.closest('.ai-chat-log');
    if (log) log.scrollTop = log.scrollHeight;
    return row;
  }

  /**
   * 将气泡内最后一步标记为已完成。
   * @param {HTMLElement} bubble
   */
  function markLastStepDone(bubble) {
    if (!bubble) return;
    var inner = bubble.querySelector('.ai-thinking-bubble__inner') || bubble;
    var steps = inner.querySelectorAll('.ai-thinking-step');
    if (steps.length) {
      var last = steps[steps.length - 1];
      last.classList.add('ai-thinking-step--done');
    }
  }

  /**
   * 从容器中移除思考气泡。
   * @param {HTMLElement} container
   */
  function removeThinkingBubble(container) {
    if (!container) return;
    var old = container.querySelector('.ai-thinking-bubble');
    if (old) old.remove();
  }

  // ============================================================
  // SSE 解析工具：解析 text/event-stream 格式的 chunk
  // ============================================================

  /**
   * 将 SSE buffer 解析为已完成的事件数组。
   * 返回 { events: [{data: ...}], rest: "未解析完的尾部" }。
   * 每个事件的 data 字段已做 JSON.parse，失败时返回原始字符串。
   * @param {string} buffer - 累积的 SSE 文本
   * @returns {{ events: Array, rest: string }}
   */
  function parseSSEBuffer(buffer) {
    var events = [];
    var parts = buffer.split('\n\n');
    var rest = parts.pop() || '';
    for (var i = 0; i < parts.length; i++) {
      var block = parts[i];
      if (!block.trim()) continue;
      var dataLines = [];
      var lines = block.split('\n');
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j];
        if (line.indexOf('data: ') === 0) {
          dataLines.push(line.slice(6));
        }
        // 忽略 event: / id: / retry: 等字段（当前后端未使用）
      }
      if (dataLines.length) {
        var raw = dataLines.join('\n');
        var parsed;
        try { parsed = JSON.parse(raw); } catch (e) { parsed = raw; }
        events.push(parsed);
      }
    }
    return { events: events, rest: rest };
  }

  /**
   * 从 Response body 读取 SSE 流，对每个事件调用 onEvent(data)。
   * 返回一个 Promise，在流结束或出错时 resolve。
   * 可通过 AbortController 中断。
   *
   * @param {Response} resp - fetch 返回的 Response 对象（body 必须可读）
   * @param {function} onEvent - 回调：onEvent(parsedData)
   * @param {AbortSignal} [signal] - 中断信号
   * @returns {Promise<void>}
   */
  function consumeSSEStream(resp, onEvent, signal) {
    if (!resp.body || !resp.body.getReader) {
      // 浏览器不支持 ReadableStream，回退到 polling
      return Promise.reject(new Error('ReadableStream 不可用'));
    }
    var reader = resp.body.getReader();
    var dec = new TextDecoder();
    var buf = '';
    function pump() {
      return reader.read().then(function (result) {
        if (result.done) return;
        if (signal && signal.aborted) {
          reader.cancel();
          return;
        }
        buf += dec.decode(result.value, { stream: true }).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        var parsed = parseSSEBuffer(buf);
        buf = parsed.rest;
        for (var i = 0; i < parsed.events.length; i++) {
          try { onEvent(parsed.events[i]); } catch (e) { /* 不阻塞流 */ }
        }
        return pump();
      });
    }
    return pump();
  }

  /**
   * 发起 SSE 流式请求。返回 { promise, abort }。
   * 若浏览器不支持流式读取或请求失败，reject 以便调用方回退。
   *
   * @param {string} url - POST 端点
   * @param {object} body - 请求体对象（将 JSON.stringify）
   * @param {function} onEvent - 每收到一个 SSE 事件时回调
   * @param {object} [options] - { signal, timeoutMs }
   * @returns {{ promise: Promise, abort: function }}
   */
  function fetchSSE(url, body, onEvent, options) {
    var opts = options || {};
    var ctl = opts.signal ? null : new AbortController();
    var sig = opts.signal || (ctl && ctl.signal);
    var timer = null;
    var promise = fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'same-origin',
      signal: sig
    }).then(function (resp) {
      if (!resp.ok) {
        return resp.text().then(function (t) {
          throw new Error('SSE 请求失败 HTTP ' + resp.status + ': ' + (t || '').slice(0, 200));
        });
      }
      return consumeSSEStream(resp, onEvent, sig);
    });
    if (opts.timeoutMs && opts.timeoutMs > 0) {
      timer = setTimeout(function () {
        if (ctl) try { ctl.abort(); } catch (e) {}
      }, opts.timeoutMs);
      promise = promise.finally(function () { if (timer) clearTimeout(timer); });
    }
    return {
      promise: promise,
      abort: function () { if (ctl) try { ctl.abort(); } catch (e) {} }
    };
  }

  /**
   * 用户友好的错误消息映射。
   * @param {Error|string} err
   * @returns {string}
   */
  function friendlyErrorMessage(err) {
    var msg = (err && err.message) ? err.message : String(err || '');
    if (/abort|AbortError/i.test(msg)) return '请求已取消。';
    if (/超时|timeout/i.test(msg)) return '请求超时，请检查模型服务是否运行。';
    if (/NetworkError|Failed to fetch|Load failed/i.test(msg)) return '网络连接失败，请检查服务器是否可达。';
    if (/HTTP 401|HTTP 403/.test(msg)) return '认证失败，请重新登录。';
    if (/HTTP 500/.test(msg)) return '服务器内部错误，请查看后台日志。';
    if (/HTTP 502|HTTP 503/.test(msg)) return '服务暂时不可用，请稍后重试。';
    return msg;
  }

  // ============================================================
  // 汇出
  // ============================================================

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
    mountStepsPageAssistant: mountStepsPageAssistant,
    /* 思考气泡 */
    showThinkingBubble: showThinkingBubble,
    updateThinkingStep: updateThinkingStep,
    markLastStepDone: markLastStepDone,
    removeThinkingBubble: removeThinkingBubble,
    /* SSE 解析 */
    parseSSEBuffer: parseSSEBuffer,
    consumeSSEStream: consumeSSEStream,
    fetchSSE: fetchSSE,
    friendlyErrorMessage: friendlyErrorMessage
  };

  global.HuFirstAiAssistant = HuFirstAiAssistant;
})(typeof window !== 'undefined' ? window : this);
