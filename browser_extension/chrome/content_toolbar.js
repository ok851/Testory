/** 每个 http(s) 页面加载时通知后台注入捕获悬浮窗（跨导航/新标签保持显示） */
(function () {
  if (window.top !== window.self) return;
  try {
    chrome.runtime.sendMessage({ type: 'uat_wc_page_ready' }, function () {
      if (chrome.runtime.lastError) { /* ignore */ }
    });
  } catch (e) { /* ignore */ }
})();
