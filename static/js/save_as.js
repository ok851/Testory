/**
 * 浏览器端「另存为」：优先 File System Access API，否则触发下载。
 * 全局：window.TestorySaveAs
 */
(function (global) {
  'use strict';

  function mimeForName(name) {
    var n = String(name || '').toLowerCase();
    if (n.endsWith('.zip')) return 'application/zip';
    if (n.endsWith('.pdf')) return 'application/pdf';
    if (n.endsWith('.xlsx') || n.endsWith('.xls')) {
      return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    }
    if (n.endsWith('.html') || n.endsWith('.htm')) return 'text/html';
    return 'application/octet-stream';
  }

  function extensionForName(name) {
    var m = String(name || '').match(/\.([a-z0-9]+)$/i);
    return m ? m[1].toLowerCase() : '';
  }

  /**
   * @param {Blob} blob
   * @param {string} suggestedName
   * @returns {Promise<'saved'|'downloaded'|'cancelled'>}
   */
  async function saveBlob(blob, suggestedName) {
    var name = suggestedName || 'download.bin';
    if (global.showSaveFilePicker) {
      try {
        var ext = extensionForName(name);
        var opts = { suggestedName: name };
        if (ext) {
          opts.types = [
            {
              description: ext.toUpperCase(),
              accept: {},
            },
          ];
          opts.types[0].accept[mimeForName(name)] = ['.' + ext];
        }
        var handle = await global.showSaveFilePicker(opts);
        var writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        return 'saved';
      } catch (e) {
        if (e && (e.name === 'AbortError' || e.name === 'NotAllowedError')) {
          return 'cancelled';
        }
        // 降级为下载
      }
    }
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      try {
        URL.revokeObjectURL(a.href);
      } catch (err) { /* ignore */ }
    }, 2000);
    return 'downloaded';
  }

  /**
   * fetch 后保存；JSON 错误返回 {ok:false,error,...}
   */
  async function fetchAndSave(url, options, suggestedName) {
    var resp = await fetch(url, options || {});
    var ct = (resp.headers.get('content-type') || '').toLowerCase();
    if (resp.status === 403 && ct.indexOf('json') >= 0) {
      var gateData = await resp.json();
      if (global.TestoryLicenseGate && global.TestoryLicenseGate.handleResponse(resp, gateData)) {
        return { ok: false, gated: true, error: gateData.error || '授权不足' };
      }
      return { ok: false, error: (gateData && gateData.error) || '无权限' };
    }
    if (!resp.ok) {
      var msg = '导出失败（HTTP ' + resp.status + '）';
      if (ct.indexOf('json') >= 0) {
        try {
          var j = await resp.json();
          msg = (j && (j.error || j.message)) || msg;
        } catch (e) { /* ignore */ }
      }
      return { ok: false, error: msg };
    }
    var cd = resp.headers.get('content-disposition') || '';
    var m = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(cd);
    var fname = suggestedName;
    if (m && m[1]) {
      try {
        fname = decodeURIComponent(m[1].replace(/"/g, '').trim());
      } catch (e2) {
        fname = m[1].replace(/"/g, '').trim();
      }
    }
    var blob = await resp.blob();
    var result = await saveBlob(blob, fname || suggestedName || 'download.bin');
    if (result === 'cancelled') {
      return { ok: false, cancelled: true, error: '已取消保存' };
    }
    return { ok: true, mode: result, filename: fname };
  }

  global.TestorySaveAs = {
    saveBlob: saveBlob,
    fetchAndSave: fetchAndSave,
  };
})(typeof window !== 'undefined' ? window : this);
