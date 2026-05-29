const WS_URL = 'ws://127.0.0.1:19222';
const TOOLBAR_LOADER_ID = 'uat-wc-toolbar-loader';

let socket = null;
let lastArm = null;
let lastToolbar = null;

function connect() {
  try {
    socket = new WebSocket(WS_URL);
    socket.onopen = () => {
      console.log('[UAT] extension bridge connected');
      pingLoop();
    };
    socket.onclose = () => setTimeout(connect, 3000);
    socket.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'pong') { /* keepalive */ }
        if (msg.type === 'show_toolbar') {
          enablePersistentToolbar(msg.api_base, msg.session_id);
        }
        if (msg.type === 'hide_toolbar') {
          disablePersistentToolbar();
        }
        if (msg.type === 'arm_picker') {
          lastArm = { apiBase: msg.api_base, sessionId: msg.session_id };
          armCaptureTabs(msg.api_base, msg.session_id);
        }
        if (msg.type === 'disarm_picker') {
          lastArm = null;
          disarmAllTabs();
        }
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

function isShellUrl(url) {
  return !!(url && typeof url === 'string' && url.includes('/web-capture/shell'));
}

function isToolbarInjectableUrl(url) {
  if (!url || typeof url !== 'string') return false;
  if (isShellUrl(url)) return true;
  if (!/^https?:/i.test(url)) return false;
  if (url.includes('/web-capture/toolbar')) return false;
  return true;
}

function isHighlightInjectableUrl(url) {
  return isToolbarInjectableUrl(url);
}

function shellPageUrl(apiBase, sessionId) {
  return `${apiBase}/web-capture/shell?session=${encodeURIComponent(sessionId)}`;
}

async function fetchScriptText(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error('fetch failed ' + resp.status);
  return resp.text();
}

function injectToolbarCode(code) {
  try {
    if (window.__uatWebCaptureShowToolbar) {
      window.__uatWebCaptureShowToolbar();
      return;
    }
    if (window.__uatWebCaptureToolbarLoaded) return;
    const s = document.createElement('script');
    s.textContent = code;
    (document.head || document.documentElement).appendChild(s);
  } catch (e) {
    console.warn('[UAT] injectToolbarCode', e);
  }
}

async function injectToolbarIntoTab(tabId) {
  if (!lastToolbar || !tabId) return;
  const { apiBase, sessionId } = lastToolbar;
  const scriptUrl = `${apiBase}/api/web-capture/toolbar.js?session=${encodeURIComponent(sessionId)}&_=${Date.now()}`;
  let scriptText = '';
  try {
    scriptText = await fetchScriptText(scriptUrl);
  } catch (e) {
    console.warn('[UAT] toolbar.js fetch error', e);
    return;
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      world: 'MAIN',
      func: injectToolbarCode,
      args: [scriptText],
    });
  } catch (err) {
    console.warn('[UAT] injectToolbarIntoTab failed', tabId, err);
  }
}

async function registerToolbarLoader() {
  try {
    await chrome.scripting.unregisterContentScripts({ ids: [TOOLBAR_LOADER_ID] });
  } catch (e) { /* ignore */ }
  await chrome.scripting.registerContentScripts([{
    id: TOOLBAR_LOADER_ID,
    matches: ['http://*/*', 'https://*/*'],
    js: ['content_toolbar.js'],
    runAt: 'document_idle',
    allFrames: false,
  }]);
}

async function enablePersistentToolbar(apiBase, sessionId) {
  if (!apiBase || !sessionId) return;
  lastToolbar = { apiBase, sessionId };
  try {
    await chrome.storage.session.set({ uatLastToolbar: lastToolbar });
  } catch (e) { /* ignore */ }

  await registerToolbarLoader();

  let pool = await chrome.tabs.query({ lastFocusedWindow: true });
  if (!pool.length) pool = await chrome.tabs.query({});

  let injected = 0;
  for (const tab of pool) {
    if (!tab.id || !isToolbarInjectableUrl(tab.url)) continue;
    await injectToolbarIntoTab(tab.id);
    injected++;
  }

  if (injected === 0) {
    await openShellFallback(shellPageUrl(apiBase, sessionId), pool);
  }
}

async function openShellFallback(shellUrl, pool) {
  const tabs = pool && pool.length ? pool : await chrome.tabs.query({ lastFocusedWindow: true });
  const candidate = (tabs || []).find((t) => {
    const u = t.url || '';
    return !u || u === 'about:blank' || u === 'chrome://newtab/' || u.startsWith('edge://newtab');
  });
  try {
    if (candidate && candidate.id) {
      await chrome.tabs.update(candidate.id, { url: shellUrl, active: true });
    } else {
      await chrome.tabs.create({ url: shellUrl, active: true });
    }
  } catch (e) {
    console.warn('[UAT] openShellFallback', e);
  }
}

async function hideToolbarOnTabs() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.id) continue;
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: false },
        world: 'MAIN',
        func: () => { if (window.__uatWebCaptureHideToolbar) window.__uatWebCaptureHideToolbar(); },
      });
    } catch (e) { /* ignore */ }
  }
}

async function disablePersistentToolbar() {
  lastToolbar = null;
  try {
    await chrome.storage.session.remove('uatLastToolbar');
  } catch (e) { /* ignore */ }
  try {
    await chrome.scripting.unregisterContentScripts({ ids: [TOOLBAR_LOADER_ID] });
  } catch (e) { /* ignore */ }
  await hideToolbarOnTabs();
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

async function restoreToolbarSession() {
  try {
    const data = await chrome.storage.session.get('uatLastToolbar');
    if (data && data.uatLastToolbar) {
      lastToolbar = data.uatLastToolbar;
      await registerToolbarLoader();
    }
  } catch (e) { /* ignore */ }
}

connect();
restoreToolbarSession();

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete') return;
  if (lastToolbar && isToolbarInjectableUrl(tab.url)) {
    injectToolbarIntoTab(tabId);
  }
  if (lastArm && isHighlightInjectableUrl(tab.url)) {
    armCaptureTabs(lastArm.apiBase, lastArm.sessionId);
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'uat_wc_page_ready' && lastToolbar && sender.tab && sender.tab.id) {
    injectToolbarIntoTab(sender.tab.id).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'pick' && socket && socket.readyState === 1) {
    socket.send(JSON.stringify({ type: 'pick', payload: msg.payload, tabId: sender.tab && sender.tab.id }));
    sendResponse({ ok: true });
  }
  return true;
});
