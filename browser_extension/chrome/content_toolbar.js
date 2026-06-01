/** 每个 http(s) 页面加载时通知后台注入捕获悬浮窗（跨导航/新标签保持显示） */
(function () {
  if (window.top !== window.self) return;

  function notifyBackground(attempt) {
    try {
      chrome.runtime.sendMessage({ type: 'uat_wc_page_ready' }, function () {
        if (chrome.runtime.lastError && attempt < 4) {
          setTimeout(function () { notifyBackground(attempt + 1); }, 300 * attempt);
        }
      });
    } catch (e) {
      if (attempt < 4) {
        setTimeout(function () { notifyBackground(attempt + 1); }, 300 * attempt);
      }
    }
  }

  notifyBackground(1);

  window.addEventListener('pageshow', function () {
    notifyBackground(1);
  });
})();
