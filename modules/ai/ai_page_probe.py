"""
无头抓取目标页可交互元素摘要，供本地 LLM 结合真实 DOM 生成/优化定位符。
依赖已安装的 Playwright 浏览器（与平台执行用例相同）。

增强：主文档 + iframe、Shadow DOM 浅层遍历、可配置等待与 settle；
     返回控件注册表用于 probe_index 映射与生成后选择器校验。
     与 INTERACTIVE_PAGE_SNAPSHOT_EVAL_JS（主会话 / 远程网关 inspect）共用同一套组件选择器与排序策略。
"""
from __future__ import annotations

import json
import os
import re
import copy
from typing import Any, Dict, List, Optional, Tuple

from modules.web.locator_tier_utils import clamp01

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)

# 在单帧内收集可见可交互元素（含 open Shadow DOM 内节点）
_COLLECT_INTERACTIVE_JS = """
(maxNodes) => {
  const sel = [
    'a[href]','a[role="button"]','button','input:not([type=hidden])','textarea','select','summary',
    '[role=button]','[role=link]','[role=menuitem]','[role=tab]',
    '[role=textbox]','[role=searchbox]','[role=combobox]','[role=switch]','[role=checkbox]','[role=radio]','[role=option]','[role=gridcell]',
    '[contenteditable=true]',
    '.el-menu-item','.el-submenu__title','.el-link',
    '.ant-btn','.ant-menu-item','.ant-menu-submenu-title','.ant-tabs-tab','.ant-tabs-tab-btn',
    '.arco-btn','.arco-menu-item','.arco-menu-inline-header','.arco-tabs-header-title','.arco-link',
    '.n-button','.n-menu-item-content','.n-tabs-tab',
    '.MuiButton-root','.MuiTab-root','.MuiIconButton-root','.MuiLink-root',
    '.v-btn','.v-tab',
    '.t-button','.t-menu__item','.t-tabs__nav-item',
    '.layui-btn','.layui-nav-item','.layui-tab-title > li',
    '.ivu-btn','.ivu-menu-item','.ivu-tabs-tab',
    '.semi-button','.semi-tabs-tab','.semi-navigation-item',
    '.chakra-button','.q-btn',
    '.p-button','.p-menuitem-link','.p-tabview-nav-link','.p-menuitem',
    '.cds--btn','.cds--tabs__nav-link','.bx--btn','.bx--tabs__nav-link',
    '.mantine-Button-root','.mantine-Tabs-tab','.mantine-NavLink-root',
    '.btn.btn-primary','.btn.btn-secondary','.btn.btn-success','.btn.btn-info','.btn.btn-warning','.btn.btn-danger','.btn.btn-default','.btn.btn-outline-primary','.btn.btn-light','.btn.btn-dark',
    '.fui-Button'
  ].join(',');
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') return false;
    return true;
  }
  function inDataTableRow(el) {
    try {
      if (!el.closest) return false;
      const row = el.closest('tr, [role=row], .n-data-table-tr');
      if (!row) return false;
      const hosts = [
        '.el-table__body tbody', '.el-table__body-wrapper tbody',
        '.ant-table-tbody', '.ant-table-body tbody',
        '.arco-table-body tbody', '.arco-table-content tbody',
        '.n-data-table-tbody', '.n-data-table-base-table-body',
        '.layui-table-body tbody', '.layui-table-view tbody',
        '.ivu-table-tbody',
        '.semi-table-body',
        '.p-datatable-tbody', '.p-treetable-tbody',
        '.cds--data-table-content tbody', '.bx--data-table tbody',
        '.mantine-Table tbody',
        'table tbody'
      ];
      return hosts.some((s) => el.closest(s));
    } catch (e) { return false; }
  }
  function scoreEl(el) {
    const tx = ((el.innerText || '') + '').trim().replace(/\\s+/g, ' ').slice(0, 72);
    let s = 0;
    if (/^(导出|下载|导入|查询|搜索|重置|刷新|新增|添加|编辑|删除|提交|确定|取消|保存|上传|预览|打印|筛选|批量)/.test(tx)) s += 38;
    if (tx === '导出' || tx === '下载' || tx === '查询' || tx === '搜索' || tx === '重置') s += 28;
    if (tx.length >= 2 && tx.length <= 24) s += 2;
    if (inDataTableRow(el)) s -= 22;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'button' || el.getAttribute('role') === 'button') s += 5;
    try {
      if (el.classList) {
        if (el.classList.contains('el-menu-item') || el.classList.contains('ant-menu-item')
            || el.classList.contains('arco-menu-item') || el.classList.contains('n-menu-item-content')
            || el.classList.contains('t-menu__item') || el.classList.contains('layui-nav-item')
            || el.classList.contains('ivu-menu-item') || el.classList.contains('semi-navigation-item')
            || el.classList.contains('p-menuitem') || el.classList.contains('p-menuitem-link')
            || el.classList.contains('mantine-NavLink-root')) s += 8;
        if (el.classList.contains('ant-tabs-tab') || el.classList.contains('arco-tabs-header-title')
            || el.classList.contains('n-tabs-tab') || el.classList.contains('MuiTab-root')
            || el.classList.contains('v-tab') || el.classList.contains('t-tabs__nav-item')
            || el.classList.contains('ant-tabs-tab-btn') || el.classList.contains('ivu-tabs-tab')
            || el.classList.contains('semi-tabs-tab')
            || el.classList.contains('p-tabview-nav-link')
            || el.classList.contains('cds--tabs__nav-link') || el.classList.contains('bx--tabs__nav-link')
            || el.classList.contains('mantine-Tabs-tab')) s += 6;
        try {
          const p = el.parentElement;
          const tn = (el.tagName || '').toUpperCase();
          if (tn === 'LI' && p && p.classList && p.classList.contains('layui-tab-title')) s += 6;
        } catch (e3) {}
        const cn = (el.className && typeof el.className === 'string') ? el.className : '';
        if (/\\b(?:ant-btn|arco-btn|MuiButton-root|t-button|n-button|layui-btn|ivu-btn|semi-button|chakra-button|q-btn|p-button|cds--btn|bx--btn|mantine-Button-root|fui-Button)\\b/.test(cn)) s += 5;
        if (/\\b(?:v-btn|MuiIconButton-root)\\b/.test(cn)) s += 4;
        if (el.classList.contains('btn') && (el.classList.contains('btn-primary') || el.classList.contains('btn-secondary')
            || el.classList.contains('btn-success') || el.classList.contains('btn-info')
            || el.classList.contains('btn-warning') || el.classList.contains('btn-danger')
            || el.classList.contains('btn-default') || el.classList.contains('btn-outline-primary')
            || el.classList.contains('btn-light') || el.classList.contains('btn-dark'))) s += 4;
      }
    } catch (e2) {}
    return s;
  }
  function rowFor(el) {
    const tag = el.tagName.toLowerCase();
    const id = el.id || '';
    const name = el.getAttribute('name') || '';
    const typ = (el.getAttribute('type') || '').toLowerCase();
    const ph = el.getAttribute('placeholder') || '';
    const al = el.getAttribute('aria-label') || '';
    const rid = el.getAttribute('role') || '';
    const txt = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 72);
    const href = (el.getAttribute('href') || '').slice(0, 120);
    let css = '';
    if (id && /^[\\w-]+$/.test(id)) css = '#' + id;
    const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || '';
    return { tag, id, name, typ, ph, al, rid, txt, href, css, testid };
  }
  const rows = [];
  function addFrom(root) {
    if (rows.length >= maxNodes) return;
    let nodes = Array.from(root.querySelectorAll(sel)).filter(visible);
    const staged = [];
    for (const el of nodes) {
      if (staged.length >= 5000) break;
      staged.push({ el, sc: scoreEl(el), y: el.getBoundingClientRect().top });
    }
    staged.sort((a, b) => (b.sc - a.sc) || (a.y - b.y));
    for (const { el } of staged) {
      if (rows.length >= maxNodes) return;
      rows.push(rowFor(el));
    }
    const hosts = root.querySelectorAll('*');
    for (const h of hosts) {
      if (rows.length >= maxNodes) return;
      if (h.shadowRoot) addFrom(h.shadowRoot);
    }
  }
  addFrom(document);
  return rows;
}
"""

# 仅主文档、不穿透 Shadow（略快；复杂页可改用带 Shadow 版本）
_COLLECT_INTERACTIVE_JS_FLAT = """
(maxNodes) => {
  const sel = [
    'a[href]','a[role="button"]','button','input:not([type=hidden])','textarea','select','summary',
    '[role=button]','[role=link]','[role=menuitem]','[role=tab]',
    '[role=textbox]','[role=searchbox]','[role=combobox]','[role=switch]','[role=checkbox]','[role=radio]','[role=option]','[role=gridcell]',
    '[contenteditable=true]',
    '.el-menu-item','.el-submenu__title','.el-link',
    '.ant-btn','.ant-menu-item','.ant-menu-submenu-title','.ant-tabs-tab','.ant-tabs-tab-btn',
    '.arco-btn','.arco-menu-item','.arco-menu-inline-header','.arco-tabs-header-title','.arco-link',
    '.n-button','.n-menu-item-content','.n-tabs-tab',
    '.MuiButton-root','.MuiTab-root','.MuiIconButton-root','.MuiLink-root',
    '.v-btn','.v-tab',
    '.t-button','.t-menu__item','.t-tabs__nav-item',
    '.layui-btn','.layui-nav-item','.layui-tab-title > li',
    '.ivu-btn','.ivu-menu-item','.ivu-tabs-tab',
    '.semi-button','.semi-tabs-tab','.semi-navigation-item',
    '.chakra-button','.q-btn',
    '.p-button','.p-menuitem-link','.p-tabview-nav-link','.p-menuitem',
    '.cds--btn','.cds--tabs__nav-link','.bx--btn','.bx--tabs__nav-link',
    '.mantine-Button-root','.mantine-Tabs-tab','.mantine-NavLink-root',
    '.btn.btn-primary','.btn.btn-secondary','.btn.btn-success','.btn.btn-info','.btn.btn-warning','.btn.btn-danger','.btn.btn-default','.btn.btn-outline-primary','.btn.btn-light','.btn.btn-dark',
    '.fui-Button'
  ].join(',');
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') return false;
    return true;
  }
  function inDataTableRow(el) {
    try {
      if (!el.closest) return false;
      const row = el.closest('tr, [role=row], .n-data-table-tr');
      if (!row) return false;
      const hosts = [
        '.el-table__body tbody', '.el-table__body-wrapper tbody',
        '.ant-table-tbody', '.ant-table-body tbody',
        '.arco-table-body tbody', '.arco-table-content tbody',
        '.n-data-table-tbody', '.n-data-table-base-table-body',
        '.layui-table-body tbody', '.layui-table-view tbody',
        '.ivu-table-tbody',
        '.semi-table-body',
        '.p-datatable-tbody', '.p-treetable-tbody',
        '.cds--data-table-content tbody', '.bx--data-table tbody',
        '.mantine-Table tbody',
        'table tbody'
      ];
      return hosts.some((s) => el.closest(s));
    } catch (e) { return false; }
  }
  function scoreEl(el) {
    const tx = ((el.innerText || '') + '').trim().replace(/\\s+/g, ' ').slice(0, 72);
    let s = 0;
    if (/^(导出|下载|导入|查询|搜索|重置|刷新|新增|添加|编辑|删除|提交|确定|取消|保存|上传|预览|打印|筛选|批量)/.test(tx)) s += 38;
    if (tx === '导出' || tx === '下载' || tx === '查询' || tx === '搜索' || tx === '重置') s += 28;
    if (tx.length >= 2 && tx.length <= 24) s += 2;
    if (inDataTableRow(el)) s -= 22;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'button' || el.getAttribute('role') === 'button') s += 5;
    try {
      if (el.classList) {
        if (el.classList.contains('el-menu-item') || el.classList.contains('ant-menu-item')
            || el.classList.contains('arco-menu-item') || el.classList.contains('n-menu-item-content')
            || el.classList.contains('t-menu__item') || el.classList.contains('layui-nav-item')
            || el.classList.contains('ivu-menu-item') || el.classList.contains('semi-navigation-item')
            || el.classList.contains('p-menuitem') || el.classList.contains('p-menuitem-link')
            || el.classList.contains('mantine-NavLink-root')) s += 8;
        if (el.classList.contains('ant-tabs-tab') || el.classList.contains('arco-tabs-header-title')
            || el.classList.contains('n-tabs-tab') || el.classList.contains('MuiTab-root')
            || el.classList.contains('v-tab') || el.classList.contains('t-tabs__nav-item')
            || el.classList.contains('ant-tabs-tab-btn') || el.classList.contains('ivu-tabs-tab')
            || el.classList.contains('semi-tabs-tab')
            || el.classList.contains('p-tabview-nav-link')
            || el.classList.contains('cds--tabs__nav-link') || el.classList.contains('bx--tabs__nav-link')
            || el.classList.contains('mantine-Tabs-tab')) s += 6;
        try {
          const p = el.parentElement;
          const tn = (el.tagName || '').toUpperCase();
          if (tn === 'LI' && p && p.classList && p.classList.contains('layui-tab-title')) s += 6;
        } catch (e3) {}
        const cn = (el.className && typeof el.className === 'string') ? el.className : '';
        if (/\\b(?:ant-btn|arco-btn|MuiButton-root|t-button|n-button|layui-btn|ivu-btn|semi-button|chakra-button|q-btn|p-button|cds--btn|bx--btn|mantine-Button-root|fui-Button)\\b/.test(cn)) s += 5;
        if (/\\b(?:v-btn|MuiIconButton-root)\\b/.test(cn)) s += 4;
        if (el.classList.contains('btn') && (el.classList.contains('btn-primary') || el.classList.contains('btn-secondary')
            || el.classList.contains('btn-success') || el.classList.contains('btn-info')
            || el.classList.contains('btn-warning') || el.classList.contains('btn-danger')
            || el.classList.contains('btn-default') || el.classList.contains('btn-outline-primary')
            || el.classList.contains('btn-light') || el.classList.contains('btn-dark'))) s += 4;
      }
    } catch (e2) {}
    return s;
  }
  function rowFor(el) {
    const tag = el.tagName.toLowerCase();
    const id = el.id || '';
    const name = el.getAttribute('name') || '';
    const typ = (el.getAttribute('type') || '').toLowerCase();
    const ph = el.getAttribute('placeholder') || '';
    const al = el.getAttribute('aria-label') || '';
    const rid = el.getAttribute('role') || '';
    const txt = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 72);
    const href = (el.getAttribute('href') || '').slice(0, 120);
    let css = '';
    if (id && /^[\\w-]+$/.test(id)) css = '#' + id;
    const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || '';
    return { tag, id, name, typ, ph, al, rid, txt, href, css, testid };
  }
  const rows = [];
  const nodes = Array.from(document.querySelectorAll(sel)).filter(visible);
  const staged = [];
  for (const el of nodes) {
    if (staged.length >= 5000) break;
    staged.push({ el, sc: scoreEl(el), y: el.getBoundingClientRect().top });
  }
  staged.sort((a, b) => (b.sc - a.sc) || (a.y - b.y));
  for (const { el } of staged) {
    if (rows.length >= maxNodes) break;
    rows.push(rowFor(el));
  }
  return rows;
}
"""

# 主会话 / 内置浏览器 / embedded_browser_gateway inspect 共用的页面可交互快照脚本（evaluate 单参 n）。
# 保持与 collect_page_controls 相近的组件覆盖与 Toolbar 优先策略。
INTERACTIVE_PAGE_SNAPSHOT_EVAL_JS = r"""(n) => {
  const v = { width: window.innerWidth, height: window.innerHeight };
  const set = [
    'a[href]','a[role="button"]','button','input','textarea','select','summary',
    '[role=button]','[role=link]','[role=tab]','[role=menuitem]','[role=searchbox]',
    '[role=switch]','[role=checkbox]','[role=radio]','[role=option]','[role=gridcell]',
    '.el-menu-item','.el-submenu__title','.el-link',
    '.ant-btn','.ant-menu-item','.ant-menu-submenu-title','.ant-tabs-tab','.ant-tabs-tab-btn',
    '.arco-btn','.arco-menu-item','.arco-menu-inline-header','.arco-tabs-header-title','.arco-link',
    '.n-button','.n-menu-item-content','.n-tabs-tab',
    '.MuiButton-root','.MuiTab-root','.MuiIconButton-root','.MuiLink-root',
    '.v-btn','.v-tab',
    '.t-button','.t-menu__item','.t-tabs__nav-item',
    '.layui-btn','.layui-nav-item','.layui-tab-title > li',
    '.ivu-btn','.ivu-menu-item','.ivu-tabs-tab',
    '.semi-button','.semi-tabs-tab','.semi-navigation-item',
    '.chakra-button','.q-btn',
    '.p-button','.p-menuitem-link','.p-tabview-nav-link','.p-menuitem',
    '.cds--btn','.cds--tabs__nav-link','.bx--btn','.bx--tabs__nav-link',
    '.mantine-Button-root','.mantine-Tabs-tab','.mantine-NavLink-root',
    '.btn.btn-primary','.btn.btn-secondary','.btn.btn-success','.btn.btn-info','.btn.btn-warning','.btn.btn-danger','.btn.btn-default','.btn.btn-outline-primary','.btn.btn-light','.btn.btn-dark',
    '.fui-Button'
  ].join(',');
  const nodes = Array.from(document.querySelectorAll(set));
  function inDataTableRow(el) {
    try {
      if (!el.closest) return false;
      const row = el.closest('tr, [role=row], .n-data-table-tr');
      if (!row) return false;
      const hosts = [
        '.el-table__body tbody', '.el-table__body-wrapper tbody',
        '.ant-table-tbody', '.ant-table-body tbody',
        '.arco-table-body tbody', '.arco-table-content tbody',
        '.n-data-table-tbody', '.n-data-table-base-table-body',
        '.layui-table-body tbody', '.layui-table-view tbody',
        '.ivu-table-tbody',
        '.semi-table-body',
        '.p-datatable-tbody', '.p-treetable-tbody',
        '.cds--data-table-content tbody', '.bx--data-table tbody',
        '.mantine-Table tbody',
        'table tbody'
      ];
      return hosts.some((s) => el.closest(s));
    } catch (e) { return false; }
  }
  function componentMenuBoost(el) {
    try {
      if (!el.classList) return 0;
      let b = 0;
      if (el.classList.contains('el-menu-item') || el.classList.contains('ant-menu-item')
          || el.classList.contains('arco-menu-item') || el.classList.contains('n-menu-item-content')
          || el.classList.contains('t-menu__item') || el.classList.contains('layui-nav-item')
          || el.classList.contains('ivu-menu-item') || el.classList.contains('semi-navigation-item')
          || el.classList.contains('p-menuitem') || el.classList.contains('p-menuitem-link')
          || el.classList.contains('mantine-NavLink-root')) b += 8;
      if (el.classList.contains('ant-tabs-tab') || el.classList.contains('arco-tabs-header-title')
          || el.classList.contains('n-tabs-tab') || el.classList.contains('MuiTab-root')
          || el.classList.contains('v-tab') || el.classList.contains('t-tabs__nav-item')
          || el.classList.contains('ant-tabs-tab-btn') || el.classList.contains('ivu-tabs-tab')
          || el.classList.contains('semi-tabs-tab')
          || el.classList.contains('p-tabview-nav-link')
          || el.classList.contains('cds--tabs__nav-link') || el.classList.contains('bx--tabs__nav-link')
          || el.classList.contains('mantine-Tabs-tab')) b += 6;
      try {
        const p = el.parentElement;
        const tn = (el.tagName || '').toUpperCase();
        if (tn === 'LI' && p && p.classList && p.classList.contains('layui-tab-title')) b += 6;
      } catch (e3) {}
      const cn = (el.className && typeof el.className === 'string') ? el.className : '';
      if (/\b(?:ant-btn|arco-btn|MuiButton-root|t-button|n-button|layui-btn|ivu-btn|semi-button|chakra-button|q-btn|p-button|cds--btn|bx--btn|mantine-Button-root|fui-Button)\b/.test(cn)) b += 5;
      if (/\b(?:v-btn|MuiIconButton-root)\b/.test(cn)) b += 4;
      if (el.classList.contains('btn') && (el.classList.contains('btn-primary') || el.classList.contains('btn-secondary')
          || el.classList.contains('btn-success') || el.classList.contains('btn-info')
          || el.classList.contains('btn-warning') || el.classList.contains('btn-danger')
          || el.classList.contains('btn-default') || el.classList.contains('btn-outline-primary')
          || el.classList.contains('btn-light') || el.classList.contains('btn-dark'))) b += 4;
      return b;
    } catch (e2) { return 0; }
  }
  function scoreEl(el, tx) {
    let s = 0;
    const t = (tx || '').replace(/\s+/g, ' ').trim();
    if (/^(导出|下载|导入|查询|搜索|重置|刷新|新增|添加|编辑|删除|提交|确定|取消|保存|上传|预览|打印|筛选|批量)/.test(t)) s += 38;
    if (t === '导出' || t === '下载' || t === '查询' || t === '搜索' || t === '重置') s += 28;
    if (t.length >= 2 && t.length <= 24) s += 2;
    if (inDataTableRow(el)) s -= 22;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'button' || el.getAttribute('role') === 'button') s += 5;
    s += componentMenuBoost(el);
    const r = el.getBoundingClientRect();
    if (r.bottom >= 0 && r.top <= v.height && r.right >= 0 && r.left <= v.width) s += 10;
    else if (r.top < v.height + 520 && r.bottom > -300) s += 4;
    return s;
  }
  const raw = [];
  for (const el of nodes) {
    if (raw.length >= 5000) break;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) continue;
    if (r.bottom < -8000 || r.top > v.height + 8000) continue;
    const tag = (el.tagName || '').toLowerCase();
    const idv = (el.id || '').toString();
    const cn = (el.className && typeof el.className === 'string') ? el.className : '';
    const cls = cn.split(/\s+/).filter((c) => c && c.length < 50).slice(0, 2);
    const dt = (el.getAttribute('data-testid') || el.getAttribute('data-test') || '');
    const nm = (el.getAttribute('name') || '');
    const tx = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    const ph = (el.getAttribute('placeholder') || '') || '';
    const al = (el.getAttribute('aria-label') || '') || '';
    let suggest = '';
    if (idv) suggest = tag + '#' + idv;
    else if (dt) suggest = tag + '[data-testid="' + String(dt).replace(/"/g, '\\"') + '"]';
    else if (nm) suggest = tag + '[name="' + String(nm).replace(/"/g, '\\"') + '"]';
    else if (cls.length) suggest = tag + '.' + cls.join('.');
    else if (al) suggest = tag + '[aria-label="' + al.slice(0, 40).replace(/"/g, '\\"') + '"]';
    else if (ph) suggest = tag + '[placeholder="' + ph.slice(0, 32).replace(/"/g, '\\"') + '"]';
    else suggest = tag;
    const sc = scoreEl(el, tx);
    raw.push({
      _sc: sc,
      _y: Math.round(r.top + r.height / 2),
      tag,
      idv, cls, dt, nm, tx, ph, al, r, suggest,
      typ: (el.getAttribute('type') || '') || '',
      href: (el.getAttribute('href') || '') || '',
      role: (el.getAttribute('role') || '') || ''
    });
  }
  raw.sort((a, b) => (b._sc - a._sc) || (a._y - b._y));
  const out = [];
  for (let i = 0; i < Math.min(n, raw.length); i++) {
    const row = raw[i];
    const r = row.r;
    out.push({
      n: i + 1,
      tag: row.tag,
      id: row.idv || null,
      class: row.cls.join(' ') || null,
      name: row.nm || null,
      type: row.typ || null,
      href: row.href || null,
      role: row.role || null,
      text: row.tx || null,
      placeholder: row.ph || null,
      ariaLabel: row.al || null,
      dataTestid: row.dt || null,
      box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      suggestedSelector: row.suggest
    });
  }
  return {
    url: window.location.href,
    title: (document.title || '') || '',
    viewport: v,
    items: out
  };
}"""


def extract_http_urls(text: str) -> List[str]:
    s = str(text or "").strip()
    if not s:
        return []
    found = _URL_RE.findall(s)
    return list(dict.fromkeys(found))


def pick_probe_url(
    goal: str,
    case_url: str = "",
    plan: Optional[Dict[str, Any]] = None,
    extra_hints: Optional[List[str]] = None,
) -> Optional[str]:
    """从顶栏/显式 URL、用户描述、或 plan（case_url / caseUrl / navigate 步）中选探测地址。"""
    ordered: List[str] = []
    if extra_hints:
        for h in extra_hints:
            if h:
                ordered.append(str(h).strip())
    if case_url:
        ordered.append(str(case_url).strip())
    for h in ordered:
        if h.startswith("http://") or h.startswith("https://"):
            return h.split()[0]

    blob = "\n".join([str(goal or "")] + ([str(h) for h in (extra_hints or []) if h]))
    for candidate in extract_http_urls(blob):
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate.rstrip(").,]}>'\"")

    if plan and isinstance(plan, dict):
        u2 = str(plan.get("case_url") or plan.get("caseUrl") or "").strip()
        if u2.startswith("http://") or u2.startswith("https://"):
            return u2.split()[0]
        for st in plan.get("steps") or []:
            if not isinstance(st, dict):
                continue
            if str(st.get("action") or "").strip().lower() != "navigate":
                continue
            url = str(st.get("input_value") or st.get("selector_value") or "").strip()
            if url.startswith("http://") or url.startswith("https://"):
                return url.split()[0]
    return None


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if raw.isdigit():
        return int(raw)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def build_locator_candidates_from_probe_entry(entry: Dict[str, Any]) -> str:
    """
    根据单次页面探测的一行，生成 locator_candidates JSON（与 playwright_automation._normalize_locator_candidate_list 兼容）。
    主选择器失败后按 score 降级尝试，对齐 Testim 类「多候选 fallback」思路。
    可通过 LOCAL_AI_PROBE_LOCATOR_CANDIDATES=0 关闭。
    """
    if (os.environ.get("LOCAL_AI_PROBE_LOCATOR_CANDIDATES", "1").strip().lower() in ("0", "false", "no")):
        return ""
    if not isinstance(entry, dict):
        return ""

    cands: List[Dict[str, Any]] = []
    seen = set()

    def add(st: str, sv: str, score: int) -> None:
        st = (st or "").strip().lower()
        sv = (sv or "").strip()
        if not sv or not st:
            return
        key = (st, sv)
        if key in seen:
            return
        seen.add(key)
        cands.append({"selector_type": st, "selector_value": sv, "score": score})

    # 推荐主路径（与 recommended_selector 一致）
    rec = _norm_probe_str(entry.get("recommended_selector"))
    rty = _norm_probe_str(entry.get("recommended_selector_type")).lower()
    if rec:
        if rty == "text":
            add("partial_text", rec, 100)
        elif rty in ("css", "xpath", "text"):
            add(rty if rty != "text" else "partial_text", rec, 100)
        else:
            add("css", rec, 100)

    eid = _norm_probe_str(entry.get("id"))
    if eid and re.match(r"^[\w-]+$", eid):
        add("id", eid, 98)
        add("css", f"#{eid}", 97)

    tid = _norm_probe_str(entry.get("testid"))
    if tid:
        safe = tid.replace("\\", "\\\\").replace('"', '\\"')
        add("css", f'[data-testid="{safe}"]', 96)

    name = _norm_probe_str(entry.get("name"))
    tag = (_norm_probe_str(entry.get("tag")) or "input").lower()
    if name and re.match(r"^[\w.\-]+$", name):
        add("css", f'{tag}[name="{name}"]', 93)

    ph = _norm_probe_str(entry.get("ph"))
    if ph and len(ph) <= 80 and "\n" not in ph:
        add("partial_text", ph, 90)
        if "'" not in ph:
            add("xpath", f"//*[@placeholder='{ph}']", 84)

    al = _norm_probe_str(entry.get("al"))
    if al and len(al) <= 80 and "\n" not in al:
        add("partial_text", al, 88)

    txt = _norm_probe_str(entry.get("txt"))
    if txt and 2 <= len(txt) <= 48 and "\n" not in txt and '"' not in txt and "'" not in txt:
        add("partial_text", txt, 82)
        add("xpath", f'//*[contains(normalize-space(.),"{txt[:40]}")]', 74)

    # Tier3：探测项含 box + viewport 时写入视口比例坐标（与 playwright 三层降级一致）
    box = entry.get("box")
    vp = entry.get("viewport") or {}
    if isinstance(box, dict) and isinstance(vp, dict):
        try:
            vw = int(vp.get("width") or 0)
            vh = int(vp.get("height") or 0)
            x = float(box.get("x", 0))
            y = float(box.get("y", 0))
            w = float(box.get("w", 0) or 0)
            h = float(box.get("h", 0) or 0)
            if vw > 0 and vh > 0 and w > 0 and h > 0:
                fx = clamp01((x + w / 2.0) / float(vw))
                fy = clamp01((y + h / 2.0) / float(vh))
                add(
                    "viewport_coord",
                    json.dumps({"fx": round(fx, 6), "fy": round(fy, 6)}, ensure_ascii=False),
                    30,
                )
        except (TypeError, ValueError):
            pass

    if not cands:
        return ""

    cands.sort(key=lambda x: -int(x.get("score") or 0))
    return json.dumps(cands, ensure_ascii=False)


def _norm_probe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _recommended_selector(row: Dict[str, Any]) -> Tuple[str, str]:
    """从探测行生成优先推荐的选择器与类型（供 probe_index 映射）。id 优先于 name。"""
    css = (row.get("css") or row.get("suggestedSelector") or "").strip()
    if css:
        return css, "css"
    tag = (row.get("tag") or "div").strip() or "div"
    tag_low = tag.lower()
    eid = (row.get("id") or "").strip()
    if eid and re.match(r"^[\w.-]+$", eid):
        if tag_low in ("input", "button", "a", "select", "textarea"):
            return f"{tag_low}#{eid}", "css"
        return f"#{eid}", "css"
    testid = (row.get("testid") or "").strip()
    if testid:
        safe = testid.replace("\\", "\\\\").replace('"', '\\"')
        return f'[data-testid="{safe}"]', "css"
    name = (row.get("name") or "").strip()
    if name and re.match(r"^[\w.\-]+$", name):
        return f'{tag_low}[name="{name}"]', "css"
    ph = (row.get("ph") or "").strip()
    if ph:
        return ph, "text"
    al = (row.get("al") or "").strip()
    if al:
        return al, "text"
    return "", ""


def _format_summary_lines(
    title: str,
    url: str,
    registry: List[Dict[str, Any]],
    max_lines: int,
    max_chars: int,
) -> str:
    summary_lines: List[str] = []
    summary_lines.append(f"页面标题: {title or '(无)'}")
    summary_lines.append(f"探测 URL: {url}")
    summary_lines.append(
        "下列为页面内可见可交互元素（含 iframe / Shadow 内）。"
        "每行 [n] 为 probe_index；生成步骤时应优先填 probe_index=n，且 selector_value 必须与该行 recommended=() 内字符串完全一致；"
        "禁止编造未出现在本列表中的 class 或 xpath。"
    )
    for row in registry[:max_lines]:
        parts = [
            f"[{row.get('i')}] frame={row.get('frame')!s} <{row.get('tag')}>",
        ]
        rec = row.get("recommended_selector") or ""
        rty = row.get("recommended_selector_type") or ""
        if rec:
            parts.append(f"recommended=({rty}){rec}")
        if row.get("css"):
            parts.append(f"css={row.get('css')}")
        if row.get("id"):
            parts.append(f"id={row.get('id')}")
        if row.get("name"):
            parts.append(f"name={row.get('name')}")
        if row.get("typ"):
            parts.append(f"type={row.get('typ')}")
        if row.get("ph"):
            parts.append(f"placeholder={row.get('ph')}")
        if row.get("al"):
            parts.append(f"aria-label={row.get('al')}")
        if row.get("rid"):
            parts.append(f"role={row.get('rid')}")
        if row.get("txt"):
            parts.append(f"text={row.get('txt')}")
        if row.get("href"):
            parts.append(f"href={row.get('href')}")
        summary_lines.append(" | ".join(parts))

    text = "\n".join(summary_lines)
    if len(text) > max_chars:
        text = text[: max_chars - 80] + "\n…(摘要已截断，可在描述中指定更具体的页面区域)…"
    return text


def dom_context_pack_enabled() -> bool:
    return (os.environ.get("LOCAL_AI_DOM_PACK", "0").strip().lower() in ("1", "true", "yes", "on"))


def _dom_env_pack_chars() -> int:
    return _env_int("LOCAL_AI_DOM_PACK_MAX_CHARS", 12000)


def _dom_band_gap_px() -> int:
    return _env_int("LOCAL_AI_DOM_PACK_BAND_GAP", 56)


def format_a11y_snapshot_lines(root: Any, max_lines: int = 48) -> str:
    """
    将 Playwright accessibility.snapshot() 根节点压成缩进文本行（非完整树，节流行）。
    root 为 dict 或 None。
    """
    if not isinstance(root, dict):
        return ""
    lines: List[str] = []
    interesting = {
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "listbox",
        "checkbox",
        "radio",
        "heading",
        "navigation",
        "main",
        "form",
        "menubar",
        "menu",
        "menuitem",
        "tab",
        "tablist",
        "dialog",
        "alert",
    }
    skip_roles = {"generic", "none", "invisible", "statictext", "InlineTextBox"}

    def walk(node: Any, depth: int) -> None:
        if len(lines) >= max_lines or not isinstance(node, dict):
            return
        role = (node.get("role") or "").strip()
        name = re.sub(r"\s+", " ", (node.get("name") or "").strip())[:140]
        rl = role.lower()
        if rl in skip_roles and not name:
            for ch in (node.get("children") or [])[:40]:
                if len(lines) >= max_lines:
                    return
                walk(ch, depth)
            return
        if name or rl in interesting or (role and rl not in skip_roles):
            indent = "  " * min(depth, 5)
            line = f"{indent}{role or '?'}: {name}".strip() if name else f"{indent}{role or '?'}"
            if line.strip():
                lines.append(line[:220])
        for ch in (node.get("children") or [])[:35]:
            if len(lines) >= max_lines:
                return
            walk(ch, depth + 1)

    walk(root, 0)
    return "\n".join(lines)


def dom_context_pack(
    snap: Dict[str, Any],
    a11y_outline: str = "",
) -> str:
    """
    第二路上下文：视口内控件按垂直区域分组 + 可选无障碍大纲；不替代 probe 行表，只辅助主模型分块理解页面。
    """
    if not dom_context_pack_enabled() or not isinstance(snap, dict):
        return ""
    maxc = _dom_env_pack_chars()
    title = _norm_probe_str(snap.get("title"))
    url = _norm_probe_str(snap.get("url"))
    vp = snap.get("viewport") or {}
    vpt = ""
    if isinstance(vp, dict) and (vp.get("width") or vp.get("height")):
        vpt = f"{vp.get('width', '')}x{vp.get('height', '')}"
    items_raw = [x for x in (snap.get("items") or []) if isinstance(x, dict)]
    # 带 box 的项用于分带；无 box 则退化为单列列表
    scored: List[Tuple[int, int, int, Dict[str, Any]]] = []
    for it in items_raw:
        box = it.get("box")
        y = 10**9
        x = 0
        nprobe = 0
        if isinstance(box, dict):
            try:
                y = int(box.get("y", 0))
            except (TypeError, ValueError):
                y = 0
            try:
                x = int(box.get("x", 0))
            except (TypeError, ValueError):
                x = 0
        try:
            nprobe = int(it.get("n") or 0)
        except (TypeError, ValueError):
            nprobe = 0
        scored.append((y, x, nprobe, it))
    scored.sort(key=lambda t: (t[0], t[1]))
    gap = _dom_band_gap_px()
    bands: List[List[Dict[str, Any]]] = []
    for _y, _x, _n, it in scored:
        if not bands:
            bands.append([it])
            continue
        last = bands[-1]
        prev_box = last[-1].get("box") if last else None
        cur_box = it.get("box")
        y_prev = 0
        y_cur = 0
        if isinstance(prev_box, dict) and isinstance(cur_box, dict):
            try:
                y_prev = int(prev_box.get("y", 0))
                y_cur = int(cur_box.get("y", 0))
            except (TypeError, ValueError):
                y_prev, y_cur = 0, 0
        if y_cur - y_prev > gap and last:
            bands.append([it])
        else:
            last.append(it)

    lines: List[str] = [
        "--- DOM context pack (grouped; probe_index in [n] matches the LIVE list above) ---",
        f"Title: {title or '(无)'} | URL: {url}" + (f" | viewport {vpt}" if vpt else ""),
    ]
    if (a11y_outline or "").strip() and (os.environ.get("LOCAL_AI_DOM_A11Y", "1").strip().lower() not in ("0", "false", "no")):
        lines.append("Accessibility outline (trimmed):")
        for ln in a11y_outline.strip().splitlines()[:60]:
            if ln.strip():
                lines.append("  " + ln.strip()[:200])
    lines.append("Controls by vertical region (y-order):")
    for bi, group in enumerate(bands[:32], start=1):
        if not group:
            continue
        y0 = None
        b0 = group[0].get("box")
        if isinstance(b0, dict):
            try:
                y0 = int(b0.get("y", 0))
            except (TypeError, ValueError):
                y0 = None
        y_label = f"y≈{y0}" if y0 is not None else f"band{bi}"
        lines.append(f"  [Region {bi} | {y_label}]")
        for it in group[:24]:
            try:
                idx = int(it.get("n") or 0)
            except (TypeError, ValueError):
                idx = 0
            tag = (it.get("tag") or "?").lower()
            tx = re.sub(r"\s+", " ", (it.get("text") or ""))[:64]
            sug = re.sub(r"\s+", " ", (it.get("suggestedSelector") or ""))[:100]
            bit = f"    [n={idx}] <{tag}>"
            if tx:
                bit += f" text={tx!r}"
            if sug:
                bit += f" try={sug!r}"
            lines.append(bit[:300])
    out = "\n".join(lines)
    if len(out) > maxc:
        out = out[: maxc - 80] + "\n…(DOM pack truncated)…"
    return out


def probe_registry_from_interactive_snapshot(snap: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], str]:
    """
    将 get_interactive_page_snapshot / 网关 inspect 返回的 data 转为
    （page_snapshot 文本, probe 注册表, 页面 URL）。

    注册表字段与 collect_page_controls / _probe_pick_selector / build_locator_candidates_from_probe_entry 兼容。
    """
    title = _norm_probe_str(snap.get("title"))
    url = _norm_probe_str(snap.get("url"))
    items = snap.get("items") or []
    max_lines = _env_int("LOCAL_AI_PROBE_MAX_LINES", 90)
    max_chars = _env_int("LOCAL_AI_PROBE_MAX_CHARS", 18000)
    registry: List[Dict[str, Any]] = []
    for it in items[:max_lines]:
        if not isinstance(it, dict):
            continue
        try:
            ii = int(it.get("n") or len(registry) + 1)
        except (TypeError, ValueError):
            ii = len(registry) + 1
        tag = _norm_probe_str(it.get("tag")) or "div"
        sel = _norm_probe_str(it.get("suggestedSelector"))
        rty = "css"
        if sel.startswith("//") or sel.lower().startswith("xpath:"):
            rty = "xpath"
            if sel.lower().startswith("xpath:"):
                sel = sel[6:].strip()
        row: Dict[str, Any] = {
            "i": ii,
            "frame": "main",
            "tag": tag,
            "typ": _norm_probe_str(it.get("type")).lower(),
            "txt": _norm_probe_str(it.get("text")),
            "al": _norm_probe_str(it.get("ariaLabel")),
            "ph": _norm_probe_str(it.get("placeholder")),
            "rid": _norm_probe_str(it.get("role")),
            "href": _norm_probe_str(it.get("href")),
            "id": _norm_probe_str(it.get("id")),
            "name": _norm_probe_str(it.get("name")),
            "testid": _norm_probe_str(it.get("dataTestid")),
            "recommended_selector": sel,
            "recommended_selector_type": rty,
        }
        vp = snap.get("viewport") if isinstance(snap.get("viewport"), dict) else {}
        if vp:
            try:
                row["viewport"] = {
                    "width": int(vp.get("width") or 0),
                    "height": int(vp.get("height") or 0),
                }
            except (TypeError, ValueError):
                pass
        bx = it.get("box")
        if isinstance(bx, dict):
            row["box"] = bx
        eid = row["id"]
        if eid and re.match(r"^[\w-]+$", eid):
            row["css"] = f"#{eid}"
        registry.append(row)
    text = _format_summary_lines(title, url, registry, max_lines, max_chars)
    return text, registry, url


def collect_page_controls(url: str) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
    """
    打开 url（无头），抽取可见可交互控件，返回 (摘要文本, 错误信息, 注册表)。
    注册表每项含全局 i（probe_index）、frame、推荐选择器等。
    """
    timeout_ms = _env_int("LOCAL_AI_PROBE_TIMEOUT_MS", 35000)
    settle_ms = _env_int("LOCAL_AI_PROBE_SETTLE_MS", 800)
    max_nodes_total = _env_int("LOCAL_AI_PROBE_MAX_NODES", 320)
    max_lines = _env_int("LOCAL_AI_PROBE_MAX_LINES", 90)
    max_chars = _env_int("LOCAL_AI_PROBE_MAX_CHARS", 18000)
    main_cap = _env_int("LOCAL_AI_PROBE_MAIN_CAP", 200)
    frame_cap = _env_int("LOCAL_AI_PROBE_FRAME_CAP", 40)
    scan_iframes = (os.environ.get("LOCAL_AI_PROBE_IFRAMES", "1").strip().lower() not in ("0", "false", "no"))
    scan_shadow = (os.environ.get("LOCAL_AI_PROBE_SHADOW", "1").strip().lower() not in ("0", "false", "no"))

    goto_wait = (os.environ.get("LOCAL_AI_PROBE_GOTO_WAIT", "load") or "load").strip().lower()
    if goto_wait not in ("commit", "domcontentloaded", "load", "networkidle"):
        goto_wait = "load"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "", "未安装 playwright Python 包，无法探测页面", []

    registry: List[Dict[str, Any]] = []
    err: Optional[str] = None
    title = ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1365, "height": 900},
                )
                page = ctx.new_page()
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until=goto_wait, timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(12000, timeout_ms))
                except Exception:
                    pass
                if settle_ms > 0:
                    page.wait_for_timeout(settle_ms)

                title = page.title() or ""

                frames = list(page.frames)
                global_i = 0

                for fi, frame in enumerate(frames):
                    if global_i >= max_nodes_total:
                        break
                    try:
                        if frame.is_detached():
                            continue
                    except Exception:
                        continue

                    cap = main_cap if fi == 0 else frame_cap
                    cap = min(cap, max_nodes_total - global_i)
                    if cap <= 0:
                        break

                    fu = ""
                    try:
                        fu = (frame.url or "")[:96]
                    except Exception:
                        fu = ""

                    if fi == 0:
                        frame_label = "main"
                    else:
                        frame_label = f"iframe[{fi}]"
                        if fu:
                            frame_label = f"{frame_label} url≈{fu}"

                    js = _COLLECT_INTERACTIVE_JS if scan_shadow else _COLLECT_INTERACTIVE_JS_FLAT

                    if not scan_iframes and fi > 0:
                        break

                    try:
                        rows = frame.evaluate(js, cap)
                    except Exception:
                        continue

                    if not isinstance(rows, list):
                        continue

                    for raw in rows:
                        if global_i >= max_nodes_total:
                            break
                        if not isinstance(raw, dict):
                            continue
                        raw_norm = dict(raw)
                        if not raw_norm.get("css"):
                            sug = (raw_norm.get("suggestedSelector") or "").strip()
                            if sug:
                                raw_norm["css"] = sug
                            elif raw_norm.get("id"):
                                tid = str(raw_norm.get("id") or "").strip()
                                ttag = (raw_norm.get("tag") or "input").strip().lower()
                                if tid and re.match(r"^[\w.-]+$", tid):
                                    if ttag in ("input", "button", "a", "select", "textarea"):
                                        raw_norm["css"] = f"{ttag}#{tid}"
                                    else:
                                        raw_norm["css"] = f"#{tid}"
                        rec, rty = _recommended_selector(raw_norm)
                        entry = {
                            "i": global_i,
                            "frame": frame_label,
                            "frame_index": fi,
                            "tag": raw.get("tag") or "",
                            "id": raw.get("id") or "",
                            "name": raw.get("name") or "",
                            "typ": raw.get("typ") or "",
                            "ph": raw.get("ph") or "",
                            "al": raw.get("al") or "",
                            "rid": raw.get("rid") or "",
                            "txt": raw.get("txt") or "",
                            "href": raw.get("href") or "",
                            "css": raw_norm.get("css") or "",
                            "testid": raw.get("testid") or "",
                            "recommended_selector": rec,
                            "recommended_selector_type": rty,
                        }
                        registry.append(entry)
                        global_i += 1

            finally:
                browser.close()
    except Exception as e:
        return "", f"页面探测失败：{e}", []

    text = _format_summary_lines(title, url, registry, max_lines, max_chars)
    return text, err, registry


def fetch_page_controls_bundle(url: str) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
    """返回 (摘要文本, 错误, 控件注册表)。无错误时第二项为 None。"""
    text, err, registry = collect_page_controls(url)
    return text, err, registry


def fetch_page_controls_summary(url: str) -> Tuple[str, Optional[str]]:
    """兼容旧接口：仅返回摘要与错误。"""
    text, err, _ = fetch_page_controls_bundle(url)
    return text, err


def registry_step_selector_warnings(
    registry: Optional[List[Dict[str, Any]]],
    steps: List[Dict[str, Any]],
) -> List[str]:
    """
    对照 probe 注册表（与 LIVE inspect 同源）检查步骤里 CSS 常见属性是否与快照一致。
    典型问题：模型臆造 input[name='password']，而真实表单为 name=pwd（Element UI 等）。
    """
    if not registry or not isinstance(registry, list) or not steps:
        return []
    if (os.environ.get("LOCAL_AI_SNAPSHOT_SELECTOR_CHECK", "1").strip().lower() in ("0", "false", "no")):
        return []
    names: set = set()
    ids: set = set()
    rec_lines: List[str] = []
    types_by_name: Dict[str, str] = {}
    for row in registry:
        if not isinstance(row, dict):
            continue
        n = (row.get("name") or "").strip()
        if n:
            names.add(n)
            t = (row.get("typ") or "").strip().lower()
            if t and n not in types_by_name:
                types_by_name[n] = t
        i = (row.get("id") or "").strip()
        if i:
            ids.add(i)
        rs = (row.get("recommended_selector") or "").strip()
        if rs:
            rec_lines.append(rs)
    blob = "\n".join(rec_lines)
    warns: List[str] = []
    seen: set = set()
    name_pat = re.compile(r"""\[name\s*=\s*['\"]([^'\"]+)['\"]\]""", re.I)
    name_pat2 = re.compile(r"""\[name\s*=\s*([^\]'\"\s]+)\]""", re.I)
    id_pat = re.compile(r"""#([\w-]{1,80})\b""")
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip().lower()
        if action in ("navigate", "wait", ""):
            continue
        ct = str(step.get("compare_type") or "").strip().lower()
        if action == "assert" and ct in ("url_equals", "url_contains", "page_text_contains", "page_text_equals", "page_text_regex"):
            continue
        st = str(step.get("selector_type") or "css").strip().lower()
        sv = str(step.get("selector_value") or "").strip()
        if not sv or st != "css":
            continue
        found_names = set(name_pat.findall(sv)) | set(name_pat2.findall(sv))
        for nm in found_names:
            nm = (nm or "").strip()
            if not nm:
                continue
            key = (idx, "name", nm.lower())
            if key in seen:
                continue
            if nm in names or nm in blob:
                seen.add(key)
                continue
            seen.add(key)
            hint = ""
            low = nm.lower()
            pwd_names = [x for x in names if types_by_name.get(x, "").lower() == "password" or "pwd" in x.lower() or "pass" in x.lower()]
            if low in ("password", "passwd", "pwd") and pwd_names:
                hint = f" 提示：快照中密码类输入框 name 为 {pwd_names[0]!r}，请勿使用未在快照出现的 name={nm!r}。"
            elif low == "username" and names:
                unames = [x for x in names if x and x != nm and ("user" in x.lower() or "login" in x.lower() or "account" in x.lower())]
                if unames:
                    hint = f" 提示：快照中的 name 示例含 {unames[0]!r}，请核对是否与 {nm!r} 一致。"
            sample = ", ".join(sorted(names)[:8])
            warns.append(
                f"第{idx}步({action})选择器含 [name={nm!r}]，但当前 LIVE 控件注册表中未登记该 name。"
                + (f" 已登记 name 示例: {sample}" if sample else "（注册表无 name 字段）")
                + hint
            )
        if "#" in sv:
            for mid in id_pat.findall(sv):
                key2 = (idx, "id", mid.lower())
                if key2 in seen:
                    continue
                if mid in ids or mid in blob:
                    seen.add(key2)
                    continue
                seen.add(key2)
                warns.append(
                    f"第{idx}步({action})选择器含 #{mid}，但当前 LIVE 控件注册表中未登记该 id。"
                )
    return warns


_CSS_NAME_IN_SELECTOR_RE = re.compile(r"""\[name\s*=\s*['\"]([^'\"]+)['\"]\]""", re.I)
_CSS_NAME_IN_SELECTOR_RE2 = re.compile(r"""\[name\s*=\s*([^\]'\"\s]+)\]""", re.I)


def _css_extract_name_attributes(selector_value: str) -> List[str]:
    s = selector_value or ""
    found = list(_CSS_NAME_IN_SELECTOR_RE.findall(s)) + list(_CSS_NAME_IN_SELECTOR_RE2.findall(s))
    return [(x or "").strip() for x in found if (x or "").strip()]


def _heuristic_selector_repair_enabled() -> bool:
    return os.environ.get("LOCAL_AI_HEURISTIC_SELECTOR_REPAIR", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _registry_password_rows(registry: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in registry:
        if not isinstance(r, dict):
            continue
        if (r.get("typ") or "").lower() == "password":
            rows.append(r)
    if rows:
        return rows
    # 部分站点未暴露 type=password；唯一「占位符含 密码」的 input 视为密码框
    ph_matches: List[Dict[str, Any]] = []
    for r in registry:
        if not isinstance(r, dict):
            continue
        if (r.get("tag") or "").lower() != "input":
            continue
        if "密码" in (r.get("ph") or ""):
            ph_matches.append(r)
    if len(ph_matches) == 1:
        return ph_matches
    return []


def _pick_password_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    for r in rows:
        blob = f'{r.get("ph") or ""}{r.get("al") or ""}{r.get("txt") or ""}'
        if "密码" in blob:
            return r
    return rows[0]


def _selector_for_registry_row(r: Dict[str, Any]) -> Tuple[str, str]:
    tag = (r.get("tag") or "input").strip().lower() or "input"
    eid = (r.get("id") or "").strip()
    if eid and re.match(r"^[\w.-]+$", eid):
        if tag in ("input", "button", "a", "select", "textarea"):
            return f"{tag}#{eid}", "css"
        return f"#{eid}", "css"
    rec = (r.get("recommended_selector") or "").strip()
    stype = (r.get("recommended_selector_type") or "css").strip().lower()
    if stype not in ("css", "xpath", "text"):
        stype = "css"
    if rec:
        if stype == "xpath":
            return rec, "xpath"
        if stype == "text":
            return rec, "text"
        return rec, "css"
    name = (r.get("name") or "").strip()
    if name:
        return f'input[name="{name}"]', "css"
    return "", "css"


def _is_password_input_step(step: Dict[str, Any]) -> bool:
    if (step.get("action") or "").strip().lower() != "input":
        return False
    desc = step.get("description") or ""
    if "密码框" in desc:
        return True
    if "账号框" in desc:
        return False
    if "密码" in desc and "账号" not in desc.replace("密码", ""):
        return True
    sv = (step.get("selector_value") or "").lower()
    if "password" in sv and "name" in sv:
        return True
    if "[type='password']" in sv or '[type="password"]' in sv:
        return True
    return False


def _is_account_input_step(step: Dict[str, Any]) -> bool:
    if (step.get("action") or "").strip().lower() != "input":
        return False
    desc = step.get("description") or ""
    if "密码框" in desc:
        return False
    if "账号框" in desc or "用户名" in desc:
        return True
    dlow = desc.lower()
    if "password" in dlow and "account" not in dlow and "username" not in dlow:
        return False
    if "密码" in desc and "账号" not in desc:
        return False
    if any(k in desc for k in ("账号", "用户名")) or any(k in dlow for k in ("account", "username")):
        return True
    sv = (step.get("selector_value") or "").lower()
    if ("username" in sv or "account" in sv) and "password" not in sv:
        return True
    return False


def _registry_account_rows(registry: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in registry:
        if not isinstance(r, dict):
            continue
        tag = (r.get("tag") or "").lower()
        if tag not in ("input", "textarea"):
            continue
        typ = (r.get("typ") or "").lower()
        if typ == "password":
            continue
        ph = (r.get("ph") or "")
        eid = (r.get("id") or "").lower()
        name = (r.get("name") or "").lower()
        if any(k in ph for k in ("账号", "用户", "account", "Account")):
            out.append(r)
        elif any(k in eid for k in ("user", "account", "login", "email")):
            out.append(r)
        elif any(k in name for k in ("user", "account", "login", "email")):
            out.append(r)
    return out


def _pick_account_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    for r in rows:
        eid = (r.get("id") or "").lower()
        if eid in ("username", "user", "account", "login", "email"):
            return r
    for r in rows:
        ph = r.get("ph") or ""
        if "账号" in ph or "用户" in ph:
            return r
    return rows[0]


def _registry_login_button_rows(registry: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in registry:
        if not isinstance(r, dict):
            continue
        raw_txt = (r.get("txt") or "").strip()
        compact = re.sub(r"\s+", "", raw_txt)
        if "登录" not in compact:
            continue
        tag = (r.get("tag") or "").lower()
        typ = (r.get("typ") or "").lower()
        rid = (r.get("rid") or "").lower()
        if tag in ("button", "a") or typ == "submit" or rid == "button":
            out.append(r)
        elif tag == "span" and "登录" in compact:
            out.append(r)
    return out


def _pick_login_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    buttons = [r for r in rows if (r.get("tag") or "").lower() == "button"]
    pool = buttons if buttons else rows
    if len(pool) == 1:
        return pool[0]
    for r in pool:
        if (r.get("typ") or "").lower() == "submit":
            return r
    return pool[0]


def _is_generic_login_submit_selector_value(sv: str) -> bool:
    sl = (sv or "").lower()
    if "submit-btn" in sl:
        return False
    return any(tok in sl for tok in ("submit", "login-btn", "login_btn", "btn-login"))


def _selector_for_login_click_row(r: Dict[str, Any]) -> Tuple[str, str]:
    """登录按钮：有稳定 id（如 submit-btn）时优先 css；span 内文案时用 text=登录。"""
    tag = (r.get("tag") or "").lower()
    eid = (r.get("id") or "").strip()
    if eid and re.match(r"^[\w.-]+$", eid):
        if tag == "button":
            return f"button#{eid}", "css"
        return f"#{eid}", "css"
    if tag == "span":
        return "登录", "text"
    ideal, st = _selector_for_registry_row(r)
    if ideal and st == "css" and (len(ideal) < 4 or ideal in ("span", "button")):
        return "登录", "text"
    return ideal, st


def _is_login_click_step(step: Dict[str, Any]) -> bool:
    if (step.get("action") or "").strip().lower() != "click":
        return False
    desc = step.get("description") or ""
    sv = step.get("selector_value") or ""
    return "登录" in desc or "登录" in sv


def heuristic_repair_plan_selectors_from_registry(
    steps: List[Dict[str, Any]],
    registry: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    不依赖 LLM：对照 probe 注册表修正常见错误（如 Element UI 密码框 name=pwd 被写成 password；
    XPath //button[contains(text(),'登录')] 因文本在子节点而 0 匹配 → 改为 text 登录）。
    """
    if not _heuristic_selector_repair_enabled() or not steps or not registry:
        return steps, []
    out = copy.deepcopy(steps)
    hints: List[str] = []

    pw_rows = _registry_password_rows(registry)
    pw_row = _pick_password_row(pw_rows)
    ac_rows = _registry_account_rows(registry)
    ac_row = _pick_account_row(ac_rows)

    for i, step in enumerate(out):
        if not isinstance(step, dict):
            continue
        if _is_account_input_step(step) and ac_row:
            ideal, st = _selector_for_registry_row(ac_row)
            if not ideal:
                continue
            cur = (step.get("selector_value") or "").strip()
            cur_typ = (step.get("selector_type") or "css").strip().lower()
            if cur == ideal and cur_typ == st:
                continue
            hints.append(
                f"第{i+1}步(input)：已按 LIVE 快照将账号框定位改为 {ideal!r}（原: {cur[:120]!r}）。"
            )
            step["selector_value"] = ideal
            step["selector_type"] = st
            continue
        if not (_is_password_input_step(step) and pw_row):
            continue
        ideal, st = _selector_for_registry_row(pw_row)
        if not ideal:
            continue
        cur = (step.get("selector_value") or "").strip()
        reg_name = (pw_row.get("name") or "").strip()
        cur_typ = (step.get("selector_type") or "css").strip().lower()
        if cur == ideal and cur_typ == st:
            continue
        hints.append(
            f"第{i+1}步(input)：已按 LIVE 快照将密码框定位改为 {ideal!r}（原: {cur[:120]!r}）；"
            f"常见误判为 name=password，实际为 name={reg_name!r}。"
        )
        step["selector_value"] = ideal
        step["selector_type"] = st

    login_rows_all = _registry_login_button_rows(registry)
    login_row = _pick_login_row(login_rows_all) if login_rows_all else None

    for i, step in enumerate(out):
        if not isinstance(step, dict):
            continue
        if not _is_login_click_step(step):
            continue
        cur_st = (step.get("selector_type") or "css").strip().lower()
        cur_sv = (step.get("selector_value") or "").strip()

        if login_row is not None:
            ideal, st = _selector_for_login_click_row(login_row)
            xpath_login = cur_st == "xpath" and "登录" in cur_sv and (
                "text()" in cur_sv or "contains" in cur_sv.lower()
            )
            if ideal and (
                xpath_login
                or _is_generic_login_submit_selector_value(cur_sv)
                or cur_sv != ideal
                or cur_st != st
            ):
                hints.append(
                    f"第{i+1}步(click)：已按 LIVE 快照将「登录」改为 {st}={ideal!r}（原: {cur_st}={cur_sv[:100]!r}）。"
                )
                step["selector_type"] = st
                step["selector_value"] = ideal
                continue

        if _is_generic_login_submit_selector_value(cur_sv):
            ideal, st = ("button#submit-btn", "css")
            if login_row is not None:
                reg_ideal, reg_st = _selector_for_login_click_row(login_row)
                if reg_ideal:
                    ideal, st = reg_ideal, reg_st
            if cur_sv != ideal or cur_st != st:
                hints.append(
                    f"第{i+1}步(click)：泛化 submit/login-btn 选择器易误点，已改为 {st}={ideal!r}（原: {cur_st}={cur_sv[:100]!r}）。"
                )
                step["selector_type"] = st
                step["selector_value"] = ideal
            continue

        if cur_st == "xpath" and "登录" in cur_sv and (
            "text()" in cur_sv or "contains" in cur_sv.lower()
        ):
            hints.append(
                f"第{i+1}步(click)：XPath 对「登录」按钮常因文本在子节点而匹配失败，已改为 text 定位「登录」。"
            )
            step["selector_type"] = "text"
            step["selector_value"] = "登录"

    for i, step in enumerate(out):
        if not isinstance(step, dict):
            continue
        if (step.get("action") or "").strip().lower() != "assert":
            continue
        ct = str(step.get("compare_type") or "").strip().lower()
        if ct in ("url_equals", "url_contains", "page_text_contains", "page_text_equals", "page_text_regex"):
            continue
        cur_st = (step.get("selector_type") or "css").strip().lower()
        cur_sv = (step.get("selector_value") or "").strip()
        desc = step.get("description") or ""
        if not _assert_selector_needs_repair(cur_sv, cur_st, desc):
            continue
        target_row = None
        if _assert_targets_password_field(step):
            target_row = pw_row
        elif _assert_targets_account_field(step):
            target_row = ac_row
        if target_row is not None:
            ideal, st = _selector_for_registry_row(target_row)
            if ideal:
                hints.append(
                    f"第{i+1}步(assert)：泛化/臆造选择器已按 LIVE 快照改为 {st}={ideal!r}（原: {cur_st}={cur_sv[:100]!r})。"
                )
                step["selector_type"] = st
                step["selector_value"] = ideal
                if not str(step.get("input_value") or "").strip() and _assert_expects_empty_value(desc):
                    step["compare_type"] = "text_equals"
                    step["input_value"] = ""
                continue
        msg_fix = repair_message_toast_assert_step_inplace(step)
        if msg_fix:
            hints.append(f"第{i+1}步(assert)：{msg_fix}")

    return out, hints


def _assert_expects_empty_value(desc: str) -> bool:
    return any(k in (desc or "") for k in ("为空", "留空", "空值", "未填", "空白", "empty", "blank"))


def _assert_targets_account_field(step: Dict[str, Any]) -> bool:
    desc = step.get("description") or ""
    if "密码" in desc and "账号" not in desc.replace("密码", ""):
        return False
    return any(k in desc for k in ("账号", "用户名", "account", "username"))


def _assert_targets_password_field(step: Dict[str, Any]) -> bool:
    desc = step.get("description") or ""
    if "密码框" in desc or ("密码" in desc and "账号" not in desc.replace("密码", "")):
        return True
    sv = (step.get("selector_value") or "").lower()
    return "password" in sv


def _assert_selector_needs_repair(sv: str, st: str, desc: str) -> bool:
    if not sv:
        return bool(desc.strip())
    try:
        from modules.ai.ai_step_normalization import is_weak_generic_css_selector

        if st == "css" and is_weak_generic_css_selector(sv):
            return True
    except Exception:
        pass
    if st == "xpath" and ("contains" in sv.lower() or "text()" in sv):
        return True
    if st == "css" and sv.lower() in ("input", "button", "div", "span"):
        return True
    return False


def resolve_steps_probe_url(steps: List[Dict[str, Any]], case_url: str = "") -> str:
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("action") or "").strip().lower() == "navigate":
            u = str(step.get("input_value") or step.get("url") or step.get("selector_value") or "").strip()
            if u.startswith(("http://", "https://")):
                return u
    u = (case_url or "").strip()
    if u.startswith(("http://", "https://")):
        return u
    return ""


def runtime_repair_steps_with_live_probe(
    steps: List[Dict[str, Any]],
    case_url: str = "",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    执行前对已有步骤做 LIVE 页面探测 + 启发式修复（账号/密码/登录/断言选择器）。
    解决「AI 生成时探测过、但入库步骤未修复」或「未填 URL 导致未探测」的问题。
    """
    if os.environ.get("UAT_RUNTIME_PROBE_REPAIR", "1").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    ):
        return list(steps or []), []
    if not steps:
        return [], []
    url = resolve_steps_probe_url(steps, case_url)
    if not url:
        return list(steps), ["运行时 LIVE 修复跳过：用例无 navigate URL 且 case_url 为空"]
    _, err, registry = fetch_page_controls_bundle(url)
    if err or not registry:
        return list(steps), [
            f"运行时 LIVE 探测未成功（步骤仍将按库内选择器执行）：{err or '无控件注册表'}"
        ]
    repaired, hints = heuristic_repair_plan_selectors_from_registry(
        copy.deepcopy(steps), registry
    )
    return repaired, hints


def _frame_locator(frame: Any, selector_type: str, selector_value: str) -> Optional[Any]:
    from playwright.sync_api import Frame

    if not isinstance(frame, Frame):
        return None
    st = (selector_type or "css").strip().lower()
    sv = (selector_value or "").strip()
    if not sv:
        return None
    try:
        if st == "xpath":
            xs = sv
            if not xs.lower().startswith("xpath="):
                xs = f"xpath={sv}"
            return frame.locator(xs)
        if st == "text":
            return frame.get_by_text(sv, exact=False)
        return frame.locator(sv)
    except Exception:
        return None


def assert_grounding_enabled() -> bool:
    return os.environ.get("LOCAL_AI_ASSERT_GROUND", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


_XPATH_CONTAINS_TEXT_RE = re.compile(
    r"contains\s*\(\s*(?:text\s*\(\s*\)|\.)\s*,\s*['\"]([^'\"]+)['\"]",
    re.I,
)


def split_assert_expected_alternatives(raw: str) -> List[str]:
    """将 AI 写的「错误|不正确|失败」拆成候选片段；非 pipe 则返回单元素列表。"""
    s = (raw or "").strip()
    if not s:
        return []
    if "|" in s and not s.startswith("("):
        parts = [p.strip() for p in s.split("|") if p.strip()]
        return parts if parts else [s]
    return [s]


def page_text_matches_assert_expected(actual: str, expected: str, compare_type: str = "") -> bool:
    """整页文本是否满足断言预期（支持 pipe 备选与 regex）。"""
    from modules.auth.auth_batch_helpers import page_text_assert_matches

    return page_text_assert_matches(actual, expected, compare_type)


def is_message_toast_assert_selector(selector_value: str, selector_type: str = "css") -> bool:
    st = (selector_type or "css").strip().lower()
    sv = (selector_value or "").strip().lower()
    if not sv:
        return False
    keys = (
        "toast", "message", "notice", "el-message", "ant-message",
        "arco-message", "van-toast", "error-msg", "err-msg",
    )
    if st == "xpath":
        return any(k in sv for k in keys) or ("contains(@class" in sv and "message" in sv)
    if st == "css":
        return any(k in sv for k in keys)
    return False


def repair_message_toast_assert_step_inplace(step: Dict[str, Any]) -> Optional[str]:
    """将 toast/message 泛化选择器改为 page_text_regex/contains，避免元素等待超时。"""
    if not isinstance(step, dict) or (step.get("action") or "").strip().lower() != "assert":
        return None
    sv = str(step.get("selector_value") or "").strip()
    st = str(step.get("selector_type") or "css").strip().lower()
    if not is_message_toast_assert_selector(sv, st) and not (
        st == "xpath" and "contains" in sv.lower() and any(k in sv.lower() for k in ("toast", "message", "notice"))
    ):
        return None
    iv = str(step.get("input_value") or "").strip()
    desc = str(step.get("description") or "")
    if not iv and any(k in desc for k in ("错误", "失败", "提示", "toast", "消息")):
        iv = "错误|不正确|失败"
        step["input_value"] = iv
    if "|" in iv or split_assert_expected_alternatives(iv) and len(split_assert_expected_alternatives(iv)) > 1:
        step["compare_type"] = "page_text_regex"
    else:
        step["compare_type"] = "page_text_contains"
    step["selector_type"] = ""
    step["selector_value"] = ""
    step.pop("locator_candidates", None)
    step.pop("probe_index", None)
    return f"toast/message 断言已改为 {step['compare_type']}（预期 {iv[:80]!r}），不再依赖泛化 XPath/CSS"


def extract_assert_expected_fragments(step: Dict[str, Any]) -> List[str]:
    """从 assert 步骤的 input_value / XPath 中提取候选预期文案。"""
    if not isinstance(step, dict):
        return []
    out: List[str] = []
    seen: set = set()

    def _add(val: str) -> None:
        v = (val or "").strip()
        if not v or v.lower() in ("true", "false") or v in seen:
            return
        seen.add(v)
        out.append(v)

    raw_iv = str(step.get("input_value") or "")
    for part in split_assert_expected_alternatives(raw_iv):
        _add(part)
    if not out:
        _add(raw_iv)
    sv = str(step.get("selector_value") or "")
    for m in _XPATH_CONTAINS_TEXT_RE.finditer(sv):
        _add(m.group(1))
    return out


def _resolve_plan_ground_url(steps: List[Dict[str, Any]], fallback_url: str = "") -> str:
    u = (fallback_url or "").strip()
    if u.startswith(("http://", "https://")):
        return u
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("action") or "").strip().lower() == "navigate":
            nu = str(step.get("input_value") or step.get("selector_value") or "").strip()
            if nu.startswith(("http://", "https://")):
                return nu
    return ""


def _pick_present_fragment(page_text: str, fragments: List[str]) -> Optional[str]:
    blob = (page_text or "").replace("\u00a0", " ")
    for frag in fragments:
        f = (frag or "").strip()
        if f and f in blob:
            return f
    return None


def _hint_fragments_for_assert_step(
    step: Dict[str, Any],
    fragments: List[str],
    expected: str = "",
) -> List[str]:
    """从描述/预期中提取主题词，用于在页面上模糊匹配真实提示语。"""
    hints: List[str] = []
    seen: set = set()

    def _add(val: str) -> None:
        v = (val or "").strip()
        if not v or v in seen:
            return
        seen.add(v)
        hints.append(v)

    for f in fragments or []:
        _add(f)
    desc = str(step.get("description") or "")
    blob = f"{desc} {expected or ''}"
    for theme in (
        "密码", "账号", "用户名", "手机号", "验证码", "登录",
        "错误", "失败", "提示", "不能为空", "请输入", "不正确",
    ):
        if theme in blob:
            _add(theme)
    return hints


def _collect_page_assert_hint_snippets(page: Any, page_text: str) -> List[str]:
    """汇总 toast、表单校验与整页短行文本，供断言文案自动修正。"""
    out: List[str] = []
    seen: set = set()

    def _add(val: str) -> None:
        v = (val or "").strip()
        if not v or len(v) > 200 or v in seen:
            return
        seen.add(v)
        out.append(v)

    for snip in _scan_message_like_texts(page) if page is not None else []:
        _add(snip)
    for line in re.split(r"[\r\n]+", page_text or ""):
        _add(line)
    return out


def _best_snippet_for_fragments(snippets: List[str], fragments: List[str], desc: str = "") -> Optional[str]:
    if not snippets:
        return None
    desc = desc or ""
    best: Optional[str] = None
    best_score = -1
    theme_blob = desc + " " + " ".join(fragments or [])
    for snip in snippets:
        s = (snip or "").strip()
        if not s or len(s) > 240:
            continue
        score = 0
        for frag in fragments:
            f = (frag or "").strip()
            if not f:
                continue
            if f in s:
                score += 10 + len(f)
            elif any(k in s for k in (f[:4], f[-4:]) if len(f) >= 4):
                score += 2
        for kw in ("错误", "失败", "不能为空", "请输入", "欢迎", "成功"):
            if kw in desc and kw in s:
                score += 3
        for theme in ("密码", "账号", "用户名", "手机号", "验证码", "登录"):
            if theme in theme_blob and theme in s:
                score += 6
        if any(k in s for k in ("请输入", "不能为空", "错误", "失败", "不正确", "无效", "提示")):
            score += 5
        if len(s) <= 4 and not any(k in s for k in ("错误", "失败", "请", "不能")):
            score -= 8
        if len(s) >= 6 and any(k in s for k in ("请", "不能", "错误", "失败")):
            score += 2
        if score > best_score:
            best_score = score
            best = s
    if best_score > 0:
        return best
    return None


def _auto_fix_assert_from_page_snippets(
    step: Dict[str, Any],
    *,
    page: Any,
    page_text: str,
    fragments: List[str],
    expected: str,
    step_idx: int,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """预期文案未命中时，从回放页面上的真实提示语中选取最匹配片段并改写 assert。"""
    snippets = _collect_page_assert_hint_snippets(page, page_text)
    if not snippets:
        return None, []
    hints = _hint_fragments_for_assert_step(step, fragments, expected)
    best = _best_snippet_for_fragments(snippets, hints, step.get("description") or "")
    if not best:
        return None, []
    new_step = copy.deepcopy(step)
    new_step["compare_type"] = "page_text_contains"
    new_step["selector_type"] = ""
    new_step["selector_value"] = ""
    new_step["input_value"] = best
    new_step.pop("probe_index", None)
    new_step.pop("locator_candidates", None)
    warn = (
        f"第{step_idx}步 page_text 断言已按页面实测修正为 {best!r}"
        f"（原预期 {expected!r} 未出现在页面上）"
    )
    return new_step, [warn]


_MESSAGE_LIKE_SCAN_JS = """
() => {
  const sels = [
    '.el-message', '.el-message__content', '.el-message-box__message',
    '.el-form-item__error', '.el-form-item__validatemessage',
    '.ant-message', '.ant-message-notice-content', '.ant-notification-notice-message',
    '.ant-form-item-explain-error', '.ant-form-item-explain',
    '.arco-message-content', '.arco-form-item-message',
    '.van-toast', '.van-notify', '.van-field__error-message', '.uni-toast',
    '[role="alert"]', '[class*="toast"]', '[class*="Toast"]',
    '[class*="error"]', '[class*="Error"]', '[class*="warn"]', '[class*="tip"]',
    '[class*="validate"]', '[class*="Validate"]'
  ];
  const out = [];
  const seen = new Set();
  for (const sel of sels) {
    try {
      document.querySelectorAll(sel).forEach(el => {
        const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        if (t && t.length <= 200 && !seen.has(t)) {
          seen.add(t);
          out.push(t);
        }
      });
    } catch (e) {}
  }
  return out.slice(0, 40);
}
"""


def _scan_message_like_texts(page: Any) -> List[str]:
    try:
        rows = page.evaluate(_MESSAGE_LIKE_SCAN_JS)
        if isinstance(rows, list):
            return [str(x).strip() for x in rows if str(x).strip()]
    except Exception:
        pass
    return []


def _page_visible_text(page: Any) -> str:
    try:
        handle = page.query_selector("body")
        if handle:
            try:
                return (handle.inner_text() or "").strip()
            finally:
                handle.dispose()
    except Exception:
        pass
    return ""


def _locator_match_count(page: Any, selector_type: str, selector_value: str) -> int:
    total = 0
    for frame in page.frames:
        try:
            if frame.is_detached():
                continue
        except Exception:
            continue
        loc = _frame_locator(frame, selector_type, selector_value)
        if loc is None:
            continue
        try:
            total += loc.count()
        except Exception:
            continue
    return total


def _extract_text_via_selector(page: Any, selector_type: str, selector_value: str) -> Tuple[int, str]:
    total = 0
    chunks: List[str] = []
    for frame in page.frames:
        try:
            if frame.is_detached():
                continue
        except Exception:
            continue
        loc = _frame_locator(frame, selector_type, selector_value)
        if loc is None:
            continue
        try:
            c = loc.count()
        except Exception:
            c = 0
        if c <= 0:
            continue
        total += c
        try:
            t = (loc.first.inner_text(timeout=2000) or "").strip()
            if t:
                chunks.append(t)
        except Exception:
            pass
    return total, " ".join(chunks)


def _click_in_any_frame(page: Any, selector_type: str, selector_value: str, timeout_ms: int) -> bool:
    for frame in page.frames:
        loc = _frame_locator(frame, selector_type, selector_value)
        if loc is None:
            continue
        try:
            if loc.count() > 0:
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _fill_in_any_frame(page: Any, selector_type: str, selector_value: str, text: str, timeout_ms: int) -> bool:
    for frame in page.frames:
        loc = _frame_locator(frame, selector_type, selector_value)
        if loc is None:
            continue
        try:
            if loc.count() > 0:
                loc.first.fill(text or "", timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _wait_ms_from_step_input(raw: Any) -> int:
    try:
        v = int(float(str(raw or "1").strip()))
    except (TypeError, ValueError):
        return 1000
    if v <= 0:
        return 1000
    if v <= 120:
        return min(v * 1000, 120_000)
    return min(v, 30_000)


def _replay_plan_step(page: Any, step: Dict[str, Any], action_timeout_ms: int) -> Optional[str]:
    """回放单步（不含 assert），失败时返回错误说明。"""
    action = str(step.get("action") or "").strip().lower()
    if action in ("assert", "extract_text", "verify"):
        return None
    if action == "navigate":
        url = str(step.get("input_value") or step.get("selector_value") or "").strip()
        if not url:
            return "navigate 缺少 URL"
        page.goto(url, wait_until="load", timeout=action_timeout_ms)
        return None
    if action == "wait":
        page.wait_for_timeout(_wait_ms_from_step_input(step.get("input_value")))
        return None
    if action == "click":
        sv = str(step.get("selector_value") or "").strip()
        if not sv:
            return "click 缺少 selector_value"
        st = str(step.get("selector_type") or "css").strip().lower()
        if not _click_in_any_frame(page, st, sv, action_timeout_ms):
            return f"click 选择器无匹配: {st}={sv[:80]}"
        return None
    if action == "input":
        sv = str(step.get("selector_value") or "").strip()
        if not sv:
            return "input 缺少 selector_value"
        st = str(step.get("selector_type") or "css").strip().lower()
        text = step.get("input_value")
        if text is None:
            text = ""
        else:
            text = str(text)
        if not _fill_in_any_frame(page, st, sv, text, action_timeout_ms):
            return f"input 选择器无匹配: {st}={sv[:80]}"
        return None
    return None


def _ground_single_assert_step(
    page: Any,
    step: Dict[str, Any],
    step_idx: int,
    *,
    page_text: str,
    page_url: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """对照回放后的真实页面修正 assert 步骤。"""
    warnings: List[str] = []
    if not isinstance(step, dict):
        return step, warnings
    expected = str(step.get("input_value") or "").strip()
    sv = str(step.get("selector_value") or "").strip()
    st = str(step.get("selector_type") or "css").strip().lower()
    from modules.auth.auth_batch_helpers import normalize_assert_compare_type

    ct = normalize_assert_compare_type(
        step.get("compare_type") or "text_contains",
        selector_value=sv,
        input_value=expected,
    )
    fragments = extract_assert_expected_fragments(step)
    desc = str(step.get("description") or "")
    if st == "text" and sv and any(
        k in desc for k in ("包含", "标题", "页面", "出现", "显示", "展示", "可见")
    ):
        expect_text = expected or sv
        new_step = copy.deepcopy(step)
        new_step["input_value"] = expect_text
        new_step["selector_type"] = ""
        new_step["selector_value"] = ""
        new_step.pop("probe_index", None)
        new_step.pop("locator_candidates", None)
        if ct in ("text_equals", "page_text_equals"):
            new_step["compare_type"] = "page_text_equals"
        elif ct in ("text_regex", "page_text_regex"):
            new_step["compare_type"] = "page_text_regex"
        else:
            new_step["compare_type"] = "page_text_contains"
        step = new_step
        expected = expect_text
        sv = ""
        st = ""
        ct = new_step["compare_type"]
        fragments = extract_assert_expected_fragments(step)
        warnings.append(
            f"第{step_idx}步 assert：text 定位已改为 {ct}（预期 {expect_text!r}）"
        )
    toast_fix = repair_message_toast_assert_step_inplace(step)
    if toast_fix:
        warnings.append(f"第{step_idx}步 assert：{toast_fix}")
        return step, warnings

    if ct in ("url_equals", "url_contains"):
        if ct == "url_contains" and expected and expected not in page_url:
            warnings.append(
                f"第{step_idx}步 URL 断言：回放后地址 {page_url[:120]!r} 不包含预期 {expected!r}"
            )
        return step, warnings

    if ct in ("page_text_contains", "page_text_equals", "page_text_regex"):
        if page_text_matches_assert_expected(page_text, expected, ct):
            matched = _pick_present_fragment(page_text, fragments or ([expected] if expected else []))
            if not matched and expected:
                for part in split_assert_expected_alternatives(expected):
                    if part in page_text:
                        matched = part
                        break
        else:
            matched = None
        if page_text_matches_assert_expected(page_text, expected, ct):
            if matched and matched != expected:
                step = copy.deepcopy(step)
                step["input_value"] = matched
                warnings.append(
                    f"第{step_idx}步 page_text 断言已按页面探测修正文案为 {matched!r}"
                )
            return step, warnings
        fixed, fix_warns = _auto_fix_assert_from_page_snippets(
            step,
            page=page,
            page_text=page_text,
            fragments=fragments or ([expected] if expected else []),
            expected=expected,
            step_idx=step_idx,
        )
        if fixed:
            warnings.extend(fix_warns)
            return fixed, warnings
        hint_snips = _collect_page_assert_hint_snippets(page, page_text)[:5]
        extra = f"；页面可见提示语示例 {hint_snips!r}" if hint_snips else ""
        warnings.append(
            f"第{step_idx}步 page_text 断言：回放后页面未找到预期文案 {fragments or [expected]!r}{extra}"
        )
        return step, warnings

    matched = _pick_present_fragment(page_text, fragments)
    if matched:
        new_step = copy.deepcopy(step)
        new_step["compare_type"] = "page_text_contains"
        new_step["selector_type"] = ""
        new_step["selector_value"] = ""
        new_step["input_value"] = matched
        new_step.pop("probe_index", None)
        new_step.pop("locator_candidates", None)
        warnings.append(
            f"第{step_idx}步 assert 已改为 page_text_contains（页面实测含 {matched!r}；"
            f"原元素选择器未命中或文案不符）"
        )
        return new_step, warnings

    selector_hits = 0
    actual_text = ""
    if sv and page is not None:
        selector_hits, actual_text = _extract_text_via_selector(page, st, sv)

    if selector_hits > 0 and fragments:
        for frag in fragments:
            if frag in actual_text:
                if frag != expected or ct != "text_contains":
                    step = copy.deepcopy(step)
                    step["compare_type"] = "text_contains"
                    step["input_value"] = frag
                return step, warnings
        if expected and expected in actual_text:
            return step, warnings

    snippets = _scan_message_like_texts(page) if page is not None else []
    best = _best_snippet_for_fragments(snippets, fragments, step.get("description") or "")
    if best:
        use_text = best
        if len(best) > 80:
            for frag in fragments:
                if frag and frag in best:
                    use_text = frag
                    break
        new_step = copy.deepcopy(step)
        new_step["compare_type"] = "page_text_contains"
        new_step["selector_type"] = ""
        new_step["selector_value"] = ""
        new_step["input_value"] = use_text
        new_step.pop("probe_index", None)
        new_step.pop("locator_candidates", None)
        warnings.append(
            f"第{step_idx}步 assert 已按页面提示语探测修正为 page_text_contains: {use_text!r}"
        )
        return new_step, warnings

    if selector_hits == 0 and sv:
        warnings.append(
            f"第{step_idx}步 assert 选择器回放后无匹配: {st}={sv[:100]!r}；"
            f"预期文案 {fragments or [expected]!r} 亦未出现在页面可见文本中"
        )
    elif fragments or expected:
        warnings.append(
            f"第{step_idx}步 assert：回放后页面未找到预期 {fragments or [expected]!r}，请人工核对"
        )
    return step, warnings


def ground_plan_assertions_with_replay(
    url: str,
    steps: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str], Optional[str]]:
    """
    回放用例前置步骤，在 assert 前的真实页面上探测并修正断言（selector / 文案 / compare_type）。
    返回 (修正后的 steps, 警告列表, 致命错误)。
    """
    if not assert_grounding_enabled():
        return list(steps or []), [], None
    if not steps or not isinstance(steps, list):
        return list(steps or []), [], None
    ground_url = _resolve_plan_ground_url(steps, url)
    if not ground_url.startswith(("http://", "https://")):
        return list(steps), [], None

    has_assert = any(
        isinstance(s, dict) and str(s.get("action") or "").strip().lower() == "assert"
        for s in steps
    )
    if not has_assert:
        return list(steps), [], None

    timeout_ms = _env_int("LOCAL_AI_PROBE_TIMEOUT_MS", 35000)
    action_timeout_ms = _env_int("LOCAL_AI_ASSERT_GROUND_ACTION_MS", min(timeout_ms, 20000))
    settle_ms = _env_int("LOCAL_AI_ASSERT_GROUND_SETTLE_MS", 1200)
    goto_wait = (os.environ.get("LOCAL_AI_PROBE_GOTO_WAIT", "load") or "load").strip().lower()
    if goto_wait not in ("commit", "domcontentloaded", "load", "networkidle"):
        goto_wait = "load"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return list(steps), [], "未安装 playwright，无法进行断言页面回放探测"

    out_steps = copy.deepcopy(steps)
    warnings: List[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(locale="zh-CN", viewport={"width": 1365, "height": 900})
                page = ctx.new_page()
                page.set_default_timeout(action_timeout_ms)
                navigated = False
                for idx, step in enumerate(out_steps):
                    if not isinstance(step, dict):
                        continue
                    action = str(step.get("action") or "").strip().lower()
                    if action == "assert":
                        if settle_ms > 0:
                            page.wait_for_timeout(settle_ms)
                        page_text = _page_visible_text(page)
                        page_url = page.url or ""
                        grounded, gw = _ground_single_assert_step(
                            page,
                            step,
                            idx + 1,
                            page_text=page_text,
                            page_url=page_url,
                        )
                        out_steps[idx] = grounded
                        warnings.extend(gw)
                        continue
                    err = _replay_plan_step(page, step, action_timeout_ms)
                    if err:
                        warnings.append(f"第{idx + 1}步回放跳过（{action}）: {err}")
                        if action == "navigate" and not navigated:
                            try:
                                page.goto(ground_url, wait_until=goto_wait, timeout=timeout_ms)
                                navigated = True
                            except Exception:
                                pass
                        continue
                    if action == "navigate":
                        navigated = True
                    if action in ("click", "input", "submit"):
                        page.wait_for_timeout(min(400, settle_ms))
            finally:
                browser.close()
    except Exception as e:
        return list(steps), warnings, f"断言回放探测异常：{e}"

    return out_steps, warnings, None


def apply_ai_assert_grounding_to_plan(
    plan: Dict[str, Any],
    extra_warnings: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """在 plan 落库/返回前统一做 assert 回放探测（避免重复执行）。"""
    warns = list(extra_warnings or [])
    if not isinstance(plan, dict):
        return plan, warns
    meta = plan.setdefault("meta", {})
    if meta.get("assert_grounding_applied"):
        return plan, warns
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return plan, warns
    url = _resolve_plan_ground_url(steps, str(plan.get("case_url") or ""))
    repaired, gw, gerr = ground_plan_assertions_with_replay(url, steps)
    plan["steps"] = repaired
    meta["assert_grounding_applied"] = True
    if gw:
        meta["assert_grounding"] = list(meta.get("assert_grounding") or []) + gw
        warns.extend(gw)
    if gerr:
        meta["assert_grounding_error"] = gerr
    return plan, warns


def validate_plan_locators(url: str, steps: List[Dict[str, Any]]) -> Tuple[List[str], Optional[str]]:
    """
    在无头会话中校验步骤中的选择器在各 frame 中的匹配数。
    返回 (警告列表, 致命错误)。iframe 内控件在主 frame 可能 0 匹配属正常，会提示若多 frame 命中则不稳定。
    """
    timeout_ms = _env_int("LOCAL_AI_PROBE_TIMEOUT_MS", 35000)
    settle_ms = _env_int("LOCAL_AI_PROBE_SETTLE_MS", 800)
    goto_wait = (os.environ.get("LOCAL_AI_PROBE_GOTO_WAIT", "load") or "load").strip().lower()
    if goto_wait not in ("commit", "domcontentloaded", "load", "networkidle"):
        goto_wait = "load"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], "未安装 playwright，无法校验选择器"

    warnings: List[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(locale="zh-CN", viewport={"width": 1365, "height": 900})
                page = ctx.new_page()
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until=goto_wait, timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(12000, timeout_ms))
                except Exception:
                    pass
                if settle_ms > 0:
                    page.wait_for_timeout(settle_ms)

                for idx, step in enumerate(steps, start=1):
                    if not isinstance(step, dict):
                        continue
                    action = str(step.get("action") or "").strip().lower()
                    if action in ("navigate", "wait", ""):
                        continue
                    ct_assert = str(step.get("compare_type") or "").strip().lower()
                    if action == "assert" and ct_assert in ("url_equals", "url_contains"):
                        continue
                    stype = str(step.get("selector_type") or "css").strip().lower()
                    sval = str(step.get("selector_value") or "").strip()
                    if not sval:
                        warnings.append(f"第{idx}步({action})缺少 selector_value，无法校验")
                        continue

                    total = 0
                    frame_hits: List[str] = []
                    for fi, frame in enumerate(page.frames):
                        try:
                            if frame.is_detached():
                                continue
                        except Exception:
                            continue
                        loc = _frame_locator(frame, stype, sval)
                        if loc is None:
                            continue
                        try:
                            c = loc.count()
                        except Exception:
                            c = 0
                        if c > 0:
                            total += c
                            fu = ""
                            try:
                                fu = (frame.url or "")[:64]
                            except Exception:
                                fu = ""
                            frame_hits.append(f"frame[{fi}]×{c}({fu})")

                    if total == 0:
                        warnings.append(
                            f"第{idx}步({action})选择器无匹配: {stype}={sval[:100]}"
                            "（若在 iframe 内且主文档无匹配，可忽略；否则请改 selector）"
                        )
                    elif total > 1:
                        warnings.append(
                            f"第{idx}步({action})选择器共匹配 {total} 处，可能不稳定: {stype}={sval[:80]} "
                            f"详情: {', '.join(frame_hits[:4])}"
                        )
            finally:
                browser.close()
    except Exception as e:
        return [], f"校验过程异常：{e}"

    return warnings, None
