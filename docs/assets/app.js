(() => {
  'use strict';
  const root = document.documentElement;
  const themeButton = document.querySelector('[data-theme-toggle]');
  const stored = (() => { try { return localStorage.getItem('bci-theme'); } catch { return null; } })();
  if (stored === 'dark' || stored === 'light') root.dataset.theme = stored;
  function updateThemeLabel() { themeButton?.setAttribute('aria-label', root.dataset.theme === 'dark' ? '切换浅色模式' : '切换深色模式'); }
  updateThemeLabel();
  themeButton?.addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('bci-theme', root.dataset.theme); } catch {}
    updateThemeLabel();
  });
  const menu = document.querySelector('.main-nav');
  const menuButton = document.querySelector('.menu-toggle');
  menuButton?.addEventListener('click', () => { menu.classList.toggle('open'); menuButton.setAttribute('aria-expanded', String(menu.classList.contains('open'))); });
  const sideButton = document.querySelector('.mobile-doc-button');
  const sidebar = document.querySelector('.sidebar');
  sideButton?.addEventListener('click', () => { sidebar.classList.toggle('open'); sideButton.setAttribute('aria-expanded', String(sidebar.classList.contains('open'))); });
  document.addEventListener('click', e => {
    if (sidebar?.classList.contains('open') && !sidebar.contains(e.target) && !sideButton.contains(e.target)) { sidebar.classList.remove('open'); sideButton.setAttribute('aria-expanded', 'false'); }
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { sidebar?.classList.remove('open'); sideButton?.setAttribute('aria-expanded', 'false'); menu.classList.remove('open'); menuButton?.setAttribute('aria-expanded', 'false'); } });
  document.querySelectorAll('pre').forEach(pre => {
    const b = document.createElement('button'); b.className = 'copy-code'; b.type = 'button'; b.textContent = '复制'; b.setAttribute('aria-label', '复制代码');
    b.addEventListener('click', async () => {
      const value = pre.querySelector('code')?.textContent || Array.from(pre.childNodes).filter(n => n !== b).map(n => n.textContent).join('');
      try { await navigator.clipboard.writeText(value); b.textContent = '已复制'; } catch { b.textContent = '请选中复制'; }
      setTimeout(() => { b.textContent = '复制'; }, 1800);
    }); pre.append(b);
  });
  const dialog = document.querySelector('.search-dialog');
  const input = dialog.querySelector('input');
  const results = dialog.querySelector('.search-results');
  const entries = window.DOCS_SEARCH || [];
  function renderSearch() {
    const q = input.value.trim().toLocaleLowerCase(); const words = q.split(/\s+/).filter(Boolean);
    const found = entries.map(e => ({ e, score: words.reduce((n, w) => n + (e.title.toLocaleLowerCase().includes(w) ? 12 : 0) + (e.text.toLocaleLowerCase().includes(w) ? 1 : 0), 0) })).filter(x => !q || words.every(w => (x.e.title + ' ' + x.e.text).toLocaleLowerCase().includes(w))).sort((a,b) => b.score-a.score).slice(0,q ? 16 : 7);
    results.replaceChildren();
    if (!found.length) { const p = document.createElement('p'); p.textContent = '未找到相关文档，试试“BLE”“采样率”或“CSV”。'; results.append(p); return; }
    for (const {e} of found) {
      const a = document.createElement('a'); a.href = e.url; a.textContent = e.title;
      const s = document.createElement('small');
      const pos = q ? Math.max(0, e.text.toLocaleLowerCase().indexOf(words[0]) - 24) : 0;
      s.textContent = (pos ? '…' : '') + e.text.slice(pos, pos+100) + (e.text.length > pos+100 ? '…' : '');
      a.append(s); a.addEventListener('click', () => dialog.close()); results.append(a);
    }
  }
  function openSearch() { dialog.showModal(); renderSearch(); input.focus(); }
  document.querySelectorAll('[data-search]').forEach(b => b.addEventListener('click', openSearch));
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); } });
  input.addEventListener('input', renderSearch);
  input.addEventListener('keydown', e => { if (e.key === 'ArrowDown') { e.preventDefault(); results.querySelector('a')?.focus(); } if (e.key === 'Enter') results.querySelector('a')?.click(); });
  results.addEventListener('keydown', e => { const links = [...results.querySelectorAll('a')], i = links.indexOf(document.activeElement); if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); links[(i + (e.key === 'ArrowDown' ? 1 : -1) + links.length)%links.length]?.focus(); } });
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); if (!dialog.open) openSearch(); } });
  const top = document.querySelector('.to-top');
  window.addEventListener('scroll', () => top.classList.toggle('visible', window.scrollY > 500), {passive:true});
  top.addEventListener('click', () => window.scrollTo({top:0, behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'}));
  if ('IntersectionObserver' in window) { const observer = new IntersectionObserver(items => { for (const item of items) if (item.isIntersecting) { document.querySelectorAll('.toc a').forEach(a => a.classList.toggle('active', a.hash === '#' + item.target.id)); } }, {rootMargin:'-90px 0px -65% 0px'}); document.querySelectorAll('.article section[id]').forEach(s => observer.observe(s)); }
})();
