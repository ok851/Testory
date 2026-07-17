/**
 * Testory CDP Bridge - Popup Script
 */

let cdpActive = false;

function updateUI(active, wsUrl) {
  cdpActive = active;
  const bar = document.getElementById("statusBar");
  const dot = document.getElementById("statusDot");
  const text = document.getElementById("statusText");
  const btn = document.getElementById("toggleBtn");
  const wsBox = document.getElementById("wsUrlBox");

  if (active) {
    bar.className = "status on";
    dot.className = "dot on";
    text.textContent = "CDP 已连接";
    btn.className = "btn-stop";
    btn.textContent = "断开 CDP 连接";
    if (wsUrl) {
      wsBox.style.display = "block";
      wsBox.textContent = wsUrl;
    }
  } else {
    bar.className = "status off";
    dot.className = "dot off";
    text.textContent = "未连接";
    btn.className = "btn-start";
    btn.textContent = "启动 CDP 连接";
    wsBox.style.display = "none";
  }
}

function toggleCDP() {
  const btn = document.getElementById("toggleBtn");
  btn.disabled = true;
  btn.textContent = "处理中…";

  if (cdpActive) {
    chrome.runtime.sendMessage({ type: "stop_cdp" }, () => {
      updateUI(false);
      btn.disabled = false;
    });
  } else {
    chrome.runtime.sendMessage({ type: "start_cdp" }, () => {
      setTimeout(() => {
        chrome.runtime.sendMessage({ type: "get_cdp_status" }, (resp) => {
          if (resp) updateUI(resp.active);
          btn.disabled = false;
        });
      }, 1000);
    });
  }
}

function updatePort() {
  const port = parseInt(document.getElementById("portInput").value) || 5000;
  chrome.runtime.sendMessage({ type: "set_platform_port", port });
}

// 初始化状态
chrome.runtime.sendMessage({ type: "get_cdp_status" }, (resp) => {
  if (resp) updateUI(resp.active);
});

// 监听状态变化
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "cdp_status") {
    updateUI(msg.active, msg.wsUrl);
  }
});
