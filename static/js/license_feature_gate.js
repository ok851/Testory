/**
 * License 企业能力门禁：解析 LICENSE_FEATURE_REQUIRED，引导升级页。
 * 全局：window.TestoryLicenseGate
 */
(function (global) {
  'use strict';

  function isFeatureGatePayload(data) {
    if (!data || typeof data !== 'object') return false;
    return data.error_code === 'LICENSE_FEATURE_REQUIRED';
  }

  function messageFrom(data) {
    if (!data) return '此功能当前不可用，请检查授权或联系管理员。';
    if (data.gate && data.gate.user_message) return String(data.gate.user_message);
    if (data.error) return String(data.error);
    var gate = data.gate || {};
    var title = gate.title || data.feature || '该功能';
    var display = gate.product_display_name || gate.license_type || '当前版本';
    var minTier = gate.min_tier || 'enterprise';
    var needMap = { free: '免费版', professional: '团队版', enterprise: '企业版' };
    var need = needMap[minTier] || minTier;
    return '「' + title + '」需要' + need + '及以上授权。当前为' + display + '。';
  }

  function upgradeUrl(data) {
    if (data && data.upgrade_url) return String(data.upgrade_url);
    var feat = (data && data.feature) ? String(data.feature) : '';
    return '/license?gate=' + encodeURIComponent(feat) + '&denied=1';
  }

  /**
   * @param {Response} response
   * @param {object|null} data 已解析 JSON（可选）
   * @param {object} [opts]
   * @param {boolean} [opts.notify=true] 是否弹窗
   * @returns {boolean} 若已识别门禁返回 true
   */
  function handleResponse(response, data, opts) {
    opts = opts || {};
    if (!response || response.status !== 403) return false;
    var payload = data;
    if (!payload) return false;
    if (!isFeatureGatePayload(payload) && !payload.gate) return false;
    if (opts.notify !== false) notify(payload);
    return true;
  }

  function notify(data) {
    var msg = messageFrom(data);
    var url = upgradeUrl(data);
    try {
      if (typeof global.Swal !== 'undefined' && global.Swal.fire) {
        global.Swal.fire({
          icon: 'info',
          title: '功能暂不可用',
          text: msg,
          confirmButtonText: '打开 License',
          showCancelButton: true,
          cancelButtonText: '关闭',
        }).then(function (r) {
          if (r && r.isConfirmed) global.location.href = url;
        });
        return;
      }
    } catch (e) { /* ignore */ }
    if (global.confirm(msg + '\n\n是否打开 License 管理页？')) {
      global.location.href = url;
    }
  }

  /** 在页面顶部插入/更新琥珀色横幅 */
  function showPageBanner(data, containerSelector) {
    var root =
      (containerSelector && document.querySelector(containerSelector)) ||
      document.querySelector('main') ||
      document.querySelector('.max-w-7xl') ||
      document.body;
    if (!root) return null;
    var id = 'testory-license-gate-banner';
    var el = document.getElementById(id);
    if (!el) {
      el = document.createElement('div');
      el.id = id;
      el.className =
        'mb-4 rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/40 px-4 py-3 text-sm text-amber-900 dark:text-amber-100';
      if (root.firstChild) root.insertBefore(el, root.firstChild);
      else root.appendChild(el);
    }
    var url = upgradeUrl(data);
    el.innerHTML =
      '<div class="flex flex-wrap items-center justify-between gap-2">' +
      '<span>' +
      escapeHtml(messageFrom(data)) +
      '</span>' +
      '<a class="underline font-medium text-blue-700 dark:text-blue-300" href="' +
      escapeAttr(url) +
      '">打开 License</a></div>';
    return el;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, '&#39;');
  }

  /**
   * 表格空态行（colspan）
   * @returns {string} HTML
   */
  function tableGateRowHtml(data, colspan) {
    var cols = colspan || 5;
    var url = upgradeUrl(data);
    return (
      '<tr><td colspan="' +
      cols +
      '" class="py-10 text-center text-amber-800 dark:text-amber-200">' +
      '<p class="font-medium mb-2">' +
      escapeHtml(messageFrom(data)) +
      '</p>' +
      '<a class="underline text-blue-600 dark:text-blue-400" href="' +
      escapeAttr(url) +
      '">去 License 管理</a></td></tr>'
    );
  }

  /**
   * fetch 包装：403 门禁时弹窗并返回 {gated:true, response, data}
   */
  async function fetchJson(url, options) {
    var resp = await fetch(url, options || {});
    var data = null;
    var ct = (resp.headers.get('content-type') || '').toLowerCase();
    if (ct.indexOf('application/json') >= 0) {
      try {
        data = await resp.json();
      } catch (e) {
        data = null;
      }
    } else if (resp.status === 403) {
      try {
        data = await resp.json();
      } catch (e) {
        data = null;
      }
    }
    if (handleResponse(resp, data)) {
      return { gated: true, response: resp, data: data };
    }
    return { gated: false, response: resp, data: data };
  }

  global.TestoryLicenseGate = {
    isFeatureGatePayload: isFeatureGatePayload,
    messageFrom: messageFrom,
    upgradeUrl: upgradeUrl,
    handleResponse: handleResponse,
    notify: notify,
    showPageBanner: showPageBanner,
    tableGateRowHtml: tableGateRowHtml,
    fetchJson: fetchJson,
  };
})(typeof window !== 'undefined' ? window : this);
