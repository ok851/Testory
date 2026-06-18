/**
 * Fixed-row-height virtual list for large log buffers.
 */
export class VirtualScrollList {
  constructor(container, options = {}) {
    if (!(container instanceof HTMLElement)) {
      throw new Error('VirtualScrollList requires a container element');
    }
    this.container = container;
    this.rowHeight = options.rowHeight || 22;
    this.maxLines = options.maxLines || 5000;
    this.overscan = options.overscan || 12;
    this.lines = [];
    this._seq = 0;
    this._renderScheduled = false;

    this.viewport = document.createElement('div');
    this.viewport.className = 'testory-vscroll-viewport';
    this.viewport.style.overflow = 'auto';
    this.viewport.style.height = options.height || '100%';
    this.viewport.style.position = 'relative';

    this.spacer = document.createElement('div');
    this.spacer.className = 'testory-vscroll-spacer';
    this.spacer.style.height = '0px';
    this.spacer.style.position = 'relative';

    this.inner = document.createElement('div');
    this.inner.className = 'testory-vscroll-inner';
    this.inner.style.position = 'absolute';
    this.inner.style.left = '0';
    this.inner.style.right = '0';
    this.inner.style.top = '0';

    this.spacer.appendChild(this.inner);
    this.viewport.appendChild(this.spacer);
    this.container.appendChild(this.viewport);

    this.viewport.addEventListener('scroll', () => this.scheduleRender(), { passive: true });
    this.scheduleRender();
  }

  appendLine(text) {
    const line = {
      id: ++this._seq,
      text: String(text ?? ''),
    };
    this.lines.push(line);
    if (this.lines.length > this.maxLines) {
      this.lines.splice(0, this.lines.length - this.maxLines);
    }
    this.scheduleRender(true);
    return line;
  }

  clear() {
    this.lines = [];
    this._seq = 0;
    this.scheduleRender();
  }

  scrollToBottom(force = false) {
    const nearBottom =
      this.viewport.scrollTop + this.viewport.clientHeight >= this.spacer.offsetHeight - this.rowHeight * 2;
    if (force || nearBottom) {
      this.viewport.scrollTop = this.spacer.offsetHeight;
    }
  }

  scheduleRender(scrollBottom = false) {
    if (this._renderScheduled) return;
    this._renderScheduled = true;
    requestAnimationFrame(() => {
      this._renderScheduled = false;
      this.render(scrollBottom);
    });
  }

  render(scrollBottom = false) {
    const total = this.lines.length;
    const totalHeight = Math.max(total * this.rowHeight, this.viewport.clientHeight);
    this.spacer.style.height = `${totalHeight}px`;

    const scrollTop = this.viewport.scrollTop;
    const viewHeight = this.viewport.clientHeight || 320;
    const start = Math.max(0, Math.floor(scrollTop / this.rowHeight) - this.overscan);
    const visibleCount = Math.ceil(viewHeight / this.rowHeight) + this.overscan * 2;
    const end = Math.min(total, start + visibleCount);

    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i += 1) {
      const row = document.createElement('div');
      row.className = 'testory-vscroll-row';
      row.style.position = 'absolute';
      row.style.left = '0';
      row.style.right = '0';
      row.style.top = `${i * this.rowHeight}px`;
      row.style.height = `${this.rowHeight}px`;
      row.style.lineHeight = `${this.rowHeight}px`;
      row.style.whiteSpace = 'pre';
      row.style.overflow = 'hidden';
      row.style.textOverflow = 'ellipsis';
      row.style.fontFamily = 'Consolas, "Cascadia Mono", monospace';
      row.style.fontSize = '12px';
      row.textContent = this.lines[i].text;
      frag.appendChild(row);
    }
    this.inner.replaceChildren(frag);
    if (scrollBottom) this.scrollToBottom(true);
  }
}
