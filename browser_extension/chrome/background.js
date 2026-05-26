const WS_URL = 'ws://127.0.0.1:19222';
let socket = null;

function connect() {
  try {
    socket = new WebSocket(WS_URL);
    socket.onopen = () => console.log('[UAT] extension bridge connected');
    socket.onclose = () => setTimeout(connect, 3000);
    socket.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'ping') {
          socket.send(JSON.stringify({ type: 'pong' }));
        }
        if (msg.type === 'arm_picker') {
          chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
            const tab = tabs[0];
            if (tab && tab.id) {
              chrome.tabs.sendMessage(tab.id, { type: 'arm_picker' });
            }
          });
        }
      } catch (e) { /* ignore */ }
    };
  } catch (e) {
    setTimeout(connect, 5000);
  }
}

connect();

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'pick' && socket && socket.readyState === 1) {
    socket.send(JSON.stringify({ type: 'pick', payload: msg.payload, tabId: sender.tab && sender.tab.id }));
    sendResponse({ ok: true });
  }
  return true;
});
