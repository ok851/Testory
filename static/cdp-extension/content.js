/**
 * Testory CDP Bridge - Content Script
 * 在平台页面中接收 CDP WebSocket URL 并同步给平台后端
 */

// 监听来自 background 的 CDP URL
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "testory_cdp_url" && msg.wsUrl) {
    // 通过 postMessage 通知页面
    window.postMessage({
      type: "testory_cdp_bridge",
      wsUrl: msg.wsUrl,
      timestamp: msg.timestamp
    }, "*");

    // 同时通过 API 同步给后端
    fetch("/api/ai/hermes/cdp-sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cdp_ws_url: msg.wsUrl })
    }).catch(() => {});
  }
});
