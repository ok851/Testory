const WS_URL = 'ws://127.0.0.1:19222';

let socket = null;
let lastArm = null;
let activeTabInfo = { id: 0, url: '', title: '' };

async function refreshActiveTabInfo() {
  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    const tab = tabs && tabs[0];
    if (!tab) return;
    activeTabInfo = {
      id: tab.id || 0,
      url: tab.url || '',
      title: tab.title || '',
    };
    if (socket && socket.readyState === 1) {
      socket.send(JSON.stringify({ type: 'tab_info', tab: activeTabInfo }));
    }
  } catch (e) { /* ignore */ }
}

function connect() {
  try {
    socket = new WebSocket(WS_URL);
    socket.onopen = () => {
      console.log('[UAT] extension bridge connected');
      refreshActiveTabInfo();
      pingLoop();
    };
    socket.onclose = () => setTimeout(connect, 3000);
    socket.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'pong') { /* keepalive */ }
        if (msg.type === 'arm_picker') {
          lastArm = { apiBase: msg.api_base, sessionId: msg.session_id };
          persistArmSession();
          armCaptureTabs(msg.api_base, msg.session_id);
        }
        if (msg.type === 'disarm_picker') {
          lastArm = null;
          persistArmSession();
          disarmAllTabs();
        }
        /* show_toolbar / hide_toolbar 已废弃：捕获控制统一在平台悬浮窗 */
      } catch (e) { /* ignore */ }
    };
  } catch (e) {
    setTimeout(connect, 5000);
  }
}

function pingLoop() {
  if (!socket || socket.readyState !== 1) return;
  socket.send(JSON.stringify({ type: 'ping' }));
  setTimeout(pingLoop, 4000);
}

function isHighlightInjectableUrl(url) {
  if (!url || typeof url !== 'string') return false;
  if (!/^https?:/i.test(url)) return false;
  if (url.includes('/web-capture/toolbar')) return false;
  return true;
}

async function fetchScriptText(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error('fetch failed ' + resp.status);
  return resp.text();
}

async function armCaptureTabs(apiBase, sessionId) {
  if (!apiBase || !sessionId) return;
  const scriptUrl = `${apiBase}/api/web-capture/highlight.js?session=${encodeURIComponent(sessionId)}&page_only=1&_=${Date.now()}`;
  let scriptText = '';
  try {
    scriptText = await fetchScriptText(scriptUrl);
  } catch (e) {
    console.warn('[UAT] highlight.js fetch error', e);
    return;
  }

  const tabs = await chrome.tabs.query({});
  const targets = tabs.filter((t) => isHighlightInjectableUrl(t.url));
  for (const tab of targets) {
    if (!tab.id) continue;
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: true },
        world: 'MAIN',
        func: injectHighlightCode,
        args: [scriptText, true],
      });
    } catch (err) {
      console.warn('[UAT] highlight inject failed tab', tab.id, err);
    }
  }
}

function injectHighlightCode(code, doArm) {
  try {
    if (window.__uatWebCaptureHighlightLoaded) {
      if (doArm && window.__uatWebCaptureArm) window.__uatWebCaptureArm();
      return;
    }
    const s = document.createElement('script');
    s.textContent = code;
    (document.head || document.documentElement).appendChild(s);
    if (doArm && window.__uatWebCaptureArm) window.__uatWebCaptureArm();
  } catch (e) {
    console.warn('[UAT] injectHighlightCode', e);
  }
}

async function disarmAllTabs() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.id || !isHighlightInjectableUrl(tab.url)) continue;
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: true },
        world: 'MAIN',
        func: () => { if (window.__uatWebCaptureDisarm) window.__uatWebCaptureDisarm(); },
      });
    } catch (e) { /* ignore */ }
  }
}

async function restoreArmSession() {
  try {
    const data = await chrome.storage.session.get('uatLastArm');
    if (data && data.uatLastArm) {
      lastArm = data.uatLastArm;
    }
  } catch (e) { /* ignore */ }
}

function persistArmSession() {
  try {
    if (lastArm) {
      chrome.storage.session.set({ uatLastArm: lastArm });
    } else {
      chrome.storage.session.remove('uatLastArm');
    }
  } catch (e) { /* ignore */ }
}

function handleTabNavigation(tabId, tabUrl) {
  if (!lastArm || !tabId || !isHighlightInjectableUrl(tabUrl)) return;
  armCaptureTabs(lastArm.apiBase, lastArm.sessionId);
}

connect();
restoreArmSession();
refreshActiveTabInfo();

chrome.tabs.onActivated.addListener(() => { refreshActiveTabInfo(); });
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.url || changeInfo.title || changeInfo.status === 'complete') {
    refreshActiveTabInfo();
  }
  if (changeInfo.status === 'complete' || changeInfo.url) {
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime.lastError || !tab) return;
      handleTabNavigation(tabId, tab.url || changeInfo.url || '');
    });
  }
});

async function cleanupLegacyBrowserToolbar() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.id) continue;
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: false },
        world: 'MAIN',
        func: () => {
          if (window.__uatWebCaptureHideToolbar) window.__uatWebCaptureHideToolbar();
          const host = document.getElementById('uat-web-capture-toolbar-host');
          if (host && host.parentNode) host.parentNode.removeChild(host);
          window.__uatWebCaptureToolbarLoaded = false;
        },
      });
    } catch (e) { /* ignore */ }
  }
}
cleanupLegacyBrowserToolbar();

chrome.webNavigation.onCompleted.addListener((details) => {
  if (details.frameId !== 0) return;
  chrome.tabs.get(details.tabId, (tab) => {
    if (chrome.runtime.lastError || !tab) return;
    handleTabNavigation(details.tabId, tab.url || '');
  });
});

chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
  if (details.frameId !== 0) return;
  chrome.tabs.get(details.tabId, (tab) => {
    if (chrome.runtime.lastError || !tab) return;
    handleTabNavigation(details.tabId, tab.url || '');
  });
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'pick' && socket && socket.readyState === 1) {
    socket.send(JSON.stringify({ type: 'pick', payload: msg.payload, tabId: sender.tab && sender.tab.id }));
    sendResponse({ ok: true });
  }
  return true;
});
