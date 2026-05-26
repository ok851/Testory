(function () {
  if (window.__uatExtContentLoaded) return;
  window.__uatExtContentLoaded = true;

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'arm_picker' && window.__uatWebCaptureArm) {
      window.__uatWebCaptureArm();
    }
  });

  if (!window.__uatWebCaptureHighlightLoaded) {
    const s = document.createElement('script');
    s.src = chrome.runtime.getURL ? '' : '';
    /* 扩展模式：由平台在首次连接时通过 scripting.executeScript 注入 highlight；
       此处仅监听 postMessage 拾取 */
    window.addEventListener('message', (ev) => {
      if (!ev.data || ev.data.__uatWebCapturePick !== true) return;
      chrome.runtime.sendMessage({ type: 'pick', payload: ev.data.payload });
    });
  }
})();
