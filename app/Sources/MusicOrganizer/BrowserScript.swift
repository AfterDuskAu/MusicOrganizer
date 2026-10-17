import Foundation

/// What Bear Media's browser window runs on each page it shows (2026-10-18): a search for
/// the video the page plays, in as many places as there are (the owner: "even if the
/// player is hiding the stream, it searches as many places as possible for it"), and the
/// way the window fetches a piece of it, as the page's own player would.
///
/// The search only looks: at the page's players and their `data-` attributes, at what the
/// page asks for (fetch, XMLHttpRequest, everything it loaded) and what those answer, at
/// its details (og:video, JSON-LD), its links, its code and its player's settings, and at
/// streams it builds itself (MediaSource). Protected playback (a request for DRM keys, or
/// encrypted media) is noticed and said, and nothing of it is ever saved.
enum BrowserScript {
    /// The name the page's findings are sent under.
    static let handler = "bearMedia"

    /// Run at the start of every page and frame, in the page's own world (so it sees the
    /// page's requests as they're made).
    static let finder = #"""
    (() => {
      if (window.__bearMediaFinder) return;
      Object.defineProperty(window, '__bearMediaFinder', {value: true});
      const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bearMedia;
      if (!handler) return;
      const MEDIA = /\.(m3u8|mpd|mp4|m4v|mov|webm|mkv|flv|ogv)(?:[?#]|$)/i;
      const TYPES = /(^video\/|mpegurl|dash\+xml)/i;
      const TEXTS = /(json|javascript|text\/plain|text\/html|xml)/i;
      const LINKS = /https?(?::|%3[aA])(?:\/|\\\/|\\u002[fF]|%2[fF]){2}(?:[^\s"'<>()\\]|\\\/|\\u002[fF])*?\.(?:m3u8|mpd|mp4|m4v|mov|webm|mkv|flv|ogv)(?![A-Za-z0-9])(?:\?(?:[^\s"'<>()\\]|\\\/|\\u0026)*)?/gi;
      const seen = new Set();
      let waiting = [];
      let timer = null;
      const flush = () => {
        timer = null;
        if (!waiting.length) return;
        const items = waiting;
        waiting = [];
        try { handler.postMessage({kind: 'found', frame: location.href, items}); } catch (e) {}
      };
      const report = (url, how, type) => {
        if (!url || typeof url !== 'string') return;
        let full;
        try { full = new URL(url, location.href).href; } catch (e) { return; }
        if (!/^https?:/i.test(full)) return;
        const key = how + '|' + full;
        if (seen.has(key) || seen.size > 5000) return;
        seen.add(key);
        waiting.push({url: full, how, type: type || null});
        if (!timer) timer = setTimeout(flush, 400);
      };
      const note = (kind, detail) => {
        try { handler.postMessage({kind, frame: location.href, detail: String(detail || '')}); } catch (e) {}
      };
      const unescape = (text) => text.replace(/\\u002[fF]/g, '/').replace(/\\\//g, '/')
        .replace(/\\u0026/g, '&').replace(/&amp;/g, '&');
      const scanText = (text, how) => {
        if (!text || typeof text !== 'string') return;
        const found = text.slice(0, 4000000).match(LINKS);
        if (!found) return;
        for (const one of found.slice(0, 300)) {
          let url = unescape(one);
          if (/^https?%3a/i.test(url)) { try { url = decodeURIComponent(url); } catch (e) {} }
          report(url, how);
        }
      };
      const look = (url, type, how) => {
        if (typeof url === 'string' && (MEDIA.test(url) || (type && TYPES.test(type)))) report(url, how, type);
      };

      // What the page asks for, and what it's answered.
      const realFetch = window.fetch;
      if (realFetch) {
        Object.defineProperty(window, '__bearMediaFetch', {value: realFetch.bind(window)});
        window.fetch = function (input, init) {
          const asked = typeof input === 'string' ? input : (input && input.url) || String(input);
          const promise = realFetch.apply(this, arguments);
          promise.then(answer => {
            try {
              const type = answer.headers.get('content-type') || '';
              look(answer.url || asked, type, "the page's requests");
              const length = Number(answer.headers.get('content-length') || 0);
              if (TEXTS.test(type) && !TYPES.test(type) && length < 3000000) {
                answer.clone().text().then(text => scanText(text, "the page's requests")).catch(() => {});
              }
            } catch (e) {}
          }).catch(() => {});
          return promise;
        };
      }
      const open = XMLHttpRequest.prototype.open;
      const send = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function (method, url) {
        try { this.__bearMediaUrl = String(url); } catch (e) {}
        return open.apply(this, arguments);
      };
      XMLHttpRequest.prototype.send = function () {
        try {
          this.addEventListener('load', () => {
            let type = '';
            try { type = this.getResponseHeader('content-type') || ''; } catch (e) {}
            look(this.responseURL || this.__bearMediaUrl, type, "the page's requests");
            try {
              if ((this.responseType === '' || this.responseType === 'text') && TEXTS.test(type) && !TYPES.test(type)) {
                scanText(this.responseText, "the page's requests");
              } else if (this.responseType === 'json' && this.response) {
                scanText(JSON.stringify(this.response), "the page's requests");
              }
            } catch (e) {}
          });
        } catch (e) {}
        return send.apply(this, arguments);
      };

      // Streams the page builds itself, and protected playback.
      for (const name of ['MediaSource', 'ManagedMediaSource', 'WebKitMediaSource']) {
        try {
          const kind = window[name];
          if (!kind || !kind.prototype || !kind.prototype.addSourceBuffer) continue;
          const add = kind.prototype.addSourceBuffer;
          kind.prototype.addSourceBuffer = function (mime) { note('stream', mime); return add.apply(this, arguments); };
        } catch (e) {}
      }
      try {
        if (navigator.requestMediaKeySystemAccess) {
          const ask = navigator.requestMediaKeySystemAccess;
          navigator.requestMediaKeySystemAccess = function () {
            note('protected', arguments[0]);
            return ask.apply(navigator, arguments);
          };
        }
        const media = window.HTMLMediaElement && HTMLMediaElement.prototype;
        if (media && media.webkitSetMediaKeys) {
          const set = media.webkitSetMediaKeys;
          media.webkitSetMediaKeys = function () { note('protected', 'webkit'); return set.apply(this, arguments); };
        }
      } catch (e) {}
      document.addEventListener('encrypted', () => note('protected', 'encrypted'), true);
      document.addEventListener('webkitneedkey', () => note('protected', 'webkitneedkey'), true);

      // The players, the page's details, its links, its code, its player's settings, and
      // everything it has loaded.
      const scanPage = (deep) => {
        try {
          document.querySelectorAll('video, audio, source').forEach(el => {
            for (const src of [el.currentSrc, el.src, el.getAttribute('src')]) {
              if (src && !/^(blob|data|mediasource):/i.test(src)) report(src, 'the player', el.getAttribute('type'));
            }
          });
          document.querySelectorAll('meta[property^="og:video"], meta[name^="og:video"], meta[name^="twitter:player:stream"], meta[property^="twitter:player:stream"], meta[itemprop="contentUrl"], meta[itemprop="embedURL"], link[rel="video_src"]').forEach(m => {
            look(m.getAttribute('content') || m.getAttribute('href'), null, "the page's details");
          });
          document.querySelectorAll('script[type="application/ld+json"]').forEach(s => scanText(s.textContent, "the page's details"));
          document.querySelectorAll('a[href]').forEach(a => look(a.href, null, 'a link'));
          performance.getEntriesByType('resource').forEach(e => look(e.name, null, "the page's requests"));
          if (deep) {
            document.querySelectorAll('*').forEach(el => {
              if (!el.attributes) return;
              for (const a of el.attributes) {
                if (/^data-/i.test(a.name) && a.value && a.value.length < 20000) {
                  look(a.value, null, 'the player');
                  if (/https?(:|%3a)/i.test(a.value)) scanText(a.value, 'the player');
                }
              }
            });
            document.querySelectorAll('script:not([src])').forEach(s => scanText(s.textContent, "the page's code"));
            if (document.documentElement) scanText(document.documentElement.innerHTML, "the page's code");
            let looked = 0;
            for (const name of Object.keys(window)) {
              if (++looked > 3000) break;
              if (/^(webkit|on|__bearMedia)/.test(name)) continue;
              let value;
              try { value = window[name]; } catch (e) { continue; }
              if (!value || typeof value !== 'object' || value === window || value === document) continue;
              if (typeof Node !== 'undefined' && value instanceof Node) continue;
              try {
                const text = JSON.stringify(value);
                if (text && text.length < 3000000) scanText(text, "the player's settings");
              } catch (e) {}
            }
          }
        } catch (e) {}
      };
      try {
        new PerformanceObserver(list => list.getEntries().forEach(e => look(e.name, null, "the page's requests")))
          .observe({type: 'resource', buffered: true});
      } catch (e) {}
      document.addEventListener('loadedmetadata', ev => {
        const el = ev.target;
        if (el && el.currentSrc && !/^blob:/i.test(el.currentSrc)) report(el.currentSrc, 'the player');
      }, true);
      document.addEventListener('play', () => scanPage(false), true);
      window.addEventListener('DOMContentLoaded', () => scanPage(true));
      window.addEventListener('load', () => scanPage(true));
      let rounds = 0;
      const again = setInterval(() => { scanPage(rounds % 3 === 0); if (++rounds >= 20) clearInterval(again); }, 3000);
      Object.defineProperty(window, '__bearMediaSearch', {value: () => { scanPage(true); flush(); }});
    })();
    """#

    /// One piece of a video, fetched by the page as its own player would (with its
    /// cookies, from its frame), given back as base64. Arguments: `url`, `first` and
    /// `last` (a byte range, or null for all of it). An answer bigger than `most` bytes,
    /// to a request for all of it, isn't read: it's said to be too big.
    static let fetchPiece = #"""
    const fetcher = window.__bearMediaFetch || window.fetch;
    const headers = {};
    if (first !== null && first !== undefined) headers['Range'] = 'bytes=' + first + '-' + (last === null || last === undefined ? '' : last);
    let answer;
    try {
      answer = await fetcher(url, {credentials: 'include', headers, cache: 'no-store'});
    } catch (e) {
      return {failed: String((e && e.message) || e)};
    }
    if (!(answer.ok || answer.status === 206)) return {status: answer.status};
    const length = Number(answer.headers.get('content-length') || 0);
    if (answer.status === 200 && length > most) {
      try { answer.body && answer.body.cancel(); } catch (e) {}
      return {status: 200, tooBig: true, length};
    }
    const blob = await answer.blob();
    const data = await new Promise((done, fail) => {
      const reader = new FileReader();
      reader.onload = () => { const text = String(reader.result); done(text.slice(text.indexOf(',') + 1)); };
      reader.onerror = () => fail(reader.error);
      reader.readAsDataURL(blob);
    });
    return {status: answer.status, type: answer.headers.get('content-type'), range: answer.headers.get('content-range'), length: blob.size, data};
    """#

    /// How big a video file is, as its address says when asked (HEAD), from the page.
    static let sizeOf = #"""
    const fetcher = window.__bearMediaFetch || window.fetch;
    try {
      const answer = await fetcher(url, {method: 'HEAD', credentials: 'include', cache: 'no-store'});
      if (!answer.ok) return null;
      const length = Number(answer.headers.get('content-length') || 0);
      return length > 0 ? length : null;
    } catch (e) {
      return null;
    }
    """#

    /// Search the page again, everywhere, at once.
    static let searchAgain = "if (window.__bearMediaSearch) window.__bearMediaSearch();"
}
