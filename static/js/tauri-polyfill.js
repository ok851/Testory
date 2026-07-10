/**
 * Tauri desktop shell helpers (Flask SSR pages only).
 */
import { getCurrentWindow } from '../vendor/tauri/window.js';
import { VirtualScrollList } from './virtual-scroll.js';

function initDesktopGuards() {
  document.addEventListener('contextmenu', (e) => {
    e.preventDefault();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'F5' || (e.ctrlKey && e.key.toLowerCase() === 'r')) {
      e.preventDefault();
    }
  });
  document.addEventListener('selectstart', (e) => {
    const t = e.target;
    if (t && (t.closest('input, textarea, [contenteditable="true"], .allow-text-select, .testory-vscroll-viewport'))) {
      return;
    }
    e.preventDefault();
  });
}

async function updateMaxIcon(win) {
  const btn = bar && bar.querySelector('[data-win="maximize"]');
  if (!btn) return;
  const maximized = await win.isMaximized();
  btn.innerHTML = maximized
    ? '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><rect x="0.5" y="2.5" width="7" height="7" rx="1" stroke="currentColor"/><path d="M2.5 2.5V1H9.5V7.5H8" stroke="currentColor" fill="none"/></svg>'
    : '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><rect x="0.5" y="0.5" width="9" height="9" rx="1" stroke="currentColor"/></svg>';
  btn.title = maximized ? '向下还原' : '最大化';
}

let bar;

function bindDesktopChrome() {
  bar = document.getElementById('testoryDesktopTitlebar');
  if (!bar) return;
  const win = getCurrentWindow();
  bar.querySelectorAll('[data-win]').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const action = btn.getAttribute('data-win');
      try {
        if (action === 'minimize') await win.minimize();
        else if (action === 'maximize') {
          const maximized = await win.isMaximized();
          if (maximized) await win.unmaximize();
          else await win.maximize();
          updateMaxIcon(win);
        } else if (action === 'close') await win.close();
      } catch (err) {
        console.warn('window action failed', err);
      }
    });
  });
  /* 双击标题栏拖拽区域切换最大化 */
  const drag = bar.querySelector('.testory-desktop-chrome__drag');
  if (drag) {
    drag.addEventListener('dblclick', async () => {
      try {
        const maximized = await win.isMaximized();
        if (maximized) await win.unmaximize();
        else await win.maximize();
        updateMaxIcon(win);
      } catch (err) {
        console.warn('toggle maximize failed', err);
      }
    });
  }
  /* 监听窗口大小变化更新图标 */
  win.onResized(() => updateMaxIcon(win));
  updateMaxIcon(win);
}

export async function testoryInvoke(cmd, args) {
  const { invoke } = await import('../vendor/tauri/core.js');
  return invoke(cmd, args || {});
}

export async function testoryFlaskFetch(path, options = {}) {
  const result = await testoryInvoke('flask_fetch', {
    path,
    method: options.method || 'GET',
    body:
      options.body == null
        ? null
        : typeof options.body === 'string'
          ? options.body
          : JSON.stringify(options.body),
    cookie: typeof document !== 'undefined' ? document.cookie || null : null,
  });
  const status = result?.status ?? 0;
  const body = result?.body ?? '';
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => (body ? JSON.parse(body) : {}),
    text: async () => body,
  };
}

function parseAcceptFilters(accept) {
  const raw = (accept || '').trim();
  if (!raw) return [{ label: 'All files', extensions: ['*'] }];
  const parts = raw.split(',').map((p) => p.trim()).filter(Boolean);
  const extensions = [];
  for (const part of parts) {
    if (part.startsWith('.')) extensions.push(part.slice(1).toLowerCase());
    else if (part.includes('/')) {
      const sub = part.split('/')[1];
      if (sub && sub !== '*') extensions.push(sub.toLowerCase());
    }
  }
  if (!extensions.length) return [{ label: 'All files', extensions: ['*'] }];
  return [{ label: 'Files', extensions }];
}

function base64ToUint8Array(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export async function testoryPickFile(options = {}) {
  const filters = options.filters || parseAcceptFilters(options.accept);
  const paths = await testoryInvoke('pick_native_files', {
    title: options.title || '选择文件',
    filters: filters.map((f) => [f.label, f.extensions]),
    multiple: !!options.multiple,
  });
  if (!paths || !paths.length) return options.multiple ? [] : null;

  const files = [];
  for (const path of paths) {
    const payload = await testoryInvoke('read_native_file_base64', { path });
    const bytes = base64ToUint8Array(payload.base64);
    files.push(new File([bytes], payload.name || 'file'));
  }
  return options.multiple ? files : files[0] || null;
}

function assignInputFiles(input, fileOrList) {
  const list = Array.isArray(fileOrList) ? fileOrList : [fileOrList];
  const dt = new DataTransfer();
  list.filter(Boolean).forEach((f) => dt.items.add(f));
  input.files = dt.files;
}

function enhanceNativeFileInputs() {
  document.querySelectorAll('input[type="file"]:not([data-testory-skip-native])').forEach((input) => {
    if (input.dataset.testoryEnhanced === '1') return;
    input.dataset.testoryEnhanced = '1';
    input.addEventListener(
      'click',
      async (e) => {
        e.preventDefault();
        e.stopPropagation();
        try {
          const picked = await testoryPickFile({
            accept: input.getAttribute('accept') || '',
            multiple: input.multiple,
          });
          if (!picked || (Array.isArray(picked) && !picked.length)) return;
          assignInputFiles(input, picked);
          input.dispatchEvent(new Event('change', { bubbles: true }));
        } catch (err) {
          console.warn('native file picker failed', err);
        }
      },
      true,
    );
  });
}

function patchHighFrequencyFetch() {
  if (typeof window.uatFetchCurrentRunsPack !== 'function') return;
  const legacy = window.uatFetchCurrentRunsPack;
  window.uatFetchCurrentRunsPack = async function uatFetchCurrentRunsPackTauri() {
    try {
      const resp = await testoryFlaskFetch('/api/ui/current-runs/status');
      if (resp.status === 404) {
        window._uatMergedRunStatusUnavailable = true;
        return legacy();
      }
      const pack = await resp.json();
      return { ok: !!(resp.ok && pack && pack.success), pack: pack || {} };
    } catch (err) {
      console.warn('invoke proxy failed, fallback to fetch', err);
      return legacy();
    }
  };
}

function boot() {
  if (window.location.protocol !== 'http:' && window.location.protocol !== 'https:') {
    return;
  }
  initDesktopGuards();
  const onReady = () => {
    bindDesktopChrome();
    enhanceNativeFileInputs();
    patchHighFrequencyFetch();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onReady);
  } else {
    onReady();
  }
}

boot();

window.testoryInvoke = testoryInvoke;
window.testoryFlaskFetch = testoryFlaskFetch;
window.testoryPickFile = testoryPickFile;
window.TestoryVirtualScrollList = VirtualScrollList;
