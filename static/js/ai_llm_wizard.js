(function () {
  'use strict';

  function showToast(msg, kind) {
    kind = kind || 'info';
    var el = document.getElementById('aiLlmWizardToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'aiLlmWizardToast';
      el.setAttribute('role', 'status');
      el.style.cssText =
        'position:fixed;bottom:24px;right:24px;z-index:9999;max-width:360px;padding:12px 16px;' +
        'border-radius:8px;font-size:13px;box-shadow:0 4px 20px rgba(0,0,0,.15);display:none;';
      document.body.appendChild(el);
    }
    el.style.background = kind === 'success' ? '#ecfdf5' : kind === 'warn' ? '#fffbeb' : '#eff6ff';
    el.style.color = '#1e293b';
    el.style.border = '1px solid ' + (kind === 'success' ? '#6ee7b7' : kind === 'warn' ? '#fcd34d' : '#93c5fd');
    el.textContent = msg;
    el.style.display = 'block';
    clearTimeout(el._hideTimer);
    el._hideTimer = setTimeout(function () {
      el.style.display = 'none';
    }, 6000);
  }

  window.aiShowToast = showToast;

  function renderBanner(readiness) {
    var host = document.getElementById('aiLlmWizardBanner');
    if (!host) return;
    if (!readiness || readiness.ready || readiness.wizard_dismissed) {
      host.hidden = true;
      host.innerHTML = '';
      return;
    }
    host.hidden = false;
    var ollama = readiness.ollama || {};
    var html =
      '<strong>AI 能力未就绪</strong> — ' +
      (readiness.recommendation || '请配置 LLM') +
      '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;">';
    if (!ollama.reachable) {
      html +=
        '<a class="btn btn-secondary btn-sm" href="' +
        (ollama.download_url || 'https://ollama.com/download') +
        '" target="_blank" rel="noopener">下载 Ollama</a>';
    }
    html +=
      '<button type="button" class="btn btn-primary btn-sm" id="aiLlmWizardOpenSettings">配置云端 API</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="aiLlmWizardDismiss">稍后</button>' +
      '</div>';
    host.innerHTML = html;
    var dismissBtn = document.getElementById('aiLlmWizardDismiss');
    if (dismissBtn) {
      dismissBtn.addEventListener('click', function () {
        fetch('/api/ai/llm/wizard-dismiss', { method: 'POST', credentials: 'same-origin' }).catch(function () {});
        host.hidden = true;
      });
    }
    var settingsBtn = document.getElementById('aiLlmWizardOpenSettings');
    if (settingsBtn) {
      settingsBtn.addEventListener('click', function () {
        if (typeof window.toggleAiSettings === 'function') window.toggleAiSettings(true);
      });
    }
  }

  async function refreshLlmReadiness() {
    try {
      var resp = await fetch('/api/ai/llm/readiness', { credentials: 'same-origin' });
      var data = await resp.json();
      if (!data.success) return;
      renderBanner(data);
      window.__aiLlmReadiness = data;
      if (typeof window.aiUpdateHermesCapBanner === 'function') {
        window.aiUpdateHermesCapBanner(data);
      }
    } catch (e) {
      /* ignore */
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    refreshLlmReadiness();
  });

  window.aiRefreshLlmReadiness = refreshLlmReadiness;
})();
