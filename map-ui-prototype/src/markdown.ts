// Minimal, safe-ish markdown -> HTML for agent answers. Handles paragraphs, bold,
// italics, inline code, links, and inline images. Image/link URLs are resolved to
// absolute via the provided resolver (host-relative /agent/files/<id>/download).
export function renderMarkdown(src: string, resolveUrl: (u: string) => string): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const stash: string[] = [];
  const hold = (html: string) => { stash.push(html); return `${stash.length - 1}`; };

  const inline = (s: string) => {
    let t = s
      // images first: ![alt](url)
      .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, alt, u) => {
        const href = resolveUrl(u);
        const safe = /^(https?:|data:image\/)/i.test(href) ? href : '#';
        return hold(`<img src="${safe}" alt="${esc(alt)}" loading="lazy" class="md-img"/>`);
      })
      // links: [text](url)
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, txt, u) => {
        const href = resolveUrl(u);
        const safe = /^(https?:|mailto:)/i.test(href) ? href : '#';
        return hold(`<a href="${safe}" target="_blank" rel="noopener noreferrer">${esc(txt)}</a>`);
      })
      .replace(/`([^`]+)`/g, (_m, c) => hold(`<code>${esc(c)}</code>`))
      .replace(/\*\*([^*]+)\*\*/g, (_m, c) => `<strong>${esc(c)}</strong>`)
      .replace(/(^|[^*])\*([^*]+)\*/g, (_m, pre, c) => `${esc(pre)}<em>${esc(c)}</em>`);
    // restore held html
    for (let k = stash.length - 1; k >= 0; k--) t = t.split(`${k}`).join(stash[k]);
    return t;
  };

  const lines = String(src || '').replace(/\r\n?/g, '\n').split('\n');
  let html = '';
  let para: string[] = [];
  let inUl = false;
  const flush = () => { if (para.length) { html += `<p>${inline(esc(para.join(' ')))}</p>`; para = []; } };
  const closeUl = () => { if (inUl) { html += '</ul>'; inUl = false; } };

  for (const line of lines) {
    if (!line.trim()) { flush(); closeUl(); continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { flush(); closeUl(); const n = h[1].length; html += `<h${n} class="md-h">${inline(esc(h[2]))}</h${n}>`; continue; }
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) { flush(); if (!inUl) { html += '<ul>'; inUl = true; } html += `<li>${inline(esc(li[1]))}</li>`; continue; }
    para.push(line);
  }
  flush(); closeUl();
  return html;
}
