/**
 * Testory CDP Bridge - Background Service Worker
 * 管理 Chrome DevTools Protocol 连接，将 CDP WebSocket URL 桥接给 Testory 平台
 */

let cdpActive = false;
let cdpTabId = null;
let platformPort = 5000; // Testory 平台默认端口

// 监听扩展图标点击
chrome.action.onClicked.addListener((tab) => {
  if (cdpActive) {
    stopCDP();
  } else {
    startCDP(tab.id);
  }
});

// 启动 CDP 调试会话
function startCDP(tabId) {
  chrome.debugger.attach({ tabId }, "1.3", () => {
    if (chrome.runtime.lastError) {
      console.error("CDP attach failed:", chrome.runtime.lastError.message);
      chrome.runtime.sendMessage({ type: "cdp_status", active: false, error: chrome.runtime.lastError.message });
      return;
    }
    cdpActive = true;
    cdpTabId = tabId;

    // 获取调试目标信息
    chrome.debugger.getTargets((targets) => {
      const target = targets.find(t => t.tabId === tabId);
      if (target) {
        const wsUrl = target.webSocketDebuggerUrl || "";
        // 发送 CDP URL 给平台页面
        sendCDPUrlToPlatform(wsUrl);
        chrome.runtime.sendMessage({ type: "cdp_status", active: true, wsUrl });
      }
    });

    // 更新图标状态
    chrome.action.setBadgeText({ text: "ON" });
    chrome.action.setBadgeBackgroundColor({ color: "#10b981" });
  });
}

// 停止 CDP 调试会话
function stopCDP() {
  if (cdpTabId) {
    chrome.debugger.detach({ tabId: cdpTabId }, () => {
      cdpActive = false;
      cdpTabId = null;
      chrome.action.setBadgeText({ text: "" });
      chrome.runtime.sendMessage({ type: "cdp_status", active: false });
    });
  }
}

// 将 CDP WebSocket URL 发送给 Testory 平台
function sendCDPUrlToPlatform(wsUrl) {
  if (!wsUrl) return;

  // 方式1: 通过 content script 发送给平台页面
  chrome.tabs.query({ url: "http://localhost:*/*" }, (tabs) => {
    tabs.forEach(tab => {
      chrome.tabs.sendMessage(tab.id, {
        type: "testory_cdp_url",
        wsUrl: wsUrl,
        timestamp: Date.now()
      });
    });
  });

  // 方式2: 通过平台 API 同步 CDP URL
  fetch(`http://localhost:${platformPort}/api/ai/hermes/cdp-sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cdp_ws_url: wsUrl })
  }).catch(() => {
    // 平台可能未运行，忽略错误
  });
}

// 监听调试器断开
chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId === cdpTabId) {
    cdpActive = false;
    cdpTabId = null;
    chrome.action.setBadgeText({ text: "" });
    chrome.runtime.sendMessage({ type: "cdp_status", active: false, reason });
  }
});

// 监听来自 popup 的消息
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "get_cdp_status") {
    sendResponse({ active: cdpActive, tabId: cdpTabId });
  } else if (msg.type === "start_cdp") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) startCDP(tabs[0].id);
    });
  } else if (msg.type === "stop_cdp") {
    stopCDP();
  } else if (msg.type === "set_platform_port") {
    platformPort = msg.port || 5000;
  }
});
