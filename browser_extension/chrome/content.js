(function () {
  if (window.__uatExtContentLoaded) return;
  window.__uatExtContentLoaded = true;

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'arm_picker' && window.__uatWebCaptureArm) {
      window.__uatWebCaptureArm();
    }
    if (msg.type === 'disarm_picker' && window.__uatWebCaptureDisarm) {
      window.__uatWebCaptureDisarm();
    }
  });

  window.addEventListener('message', (ev) => {
    if (!ev.data || ev.data.__uatWebCapturePick !== true) return;
    try {
      chrome.runtime.sendMessage({ type: 'pick', payload: ev.data.payload });
    } catch (e) { /* ignore */ }
  });
})();
