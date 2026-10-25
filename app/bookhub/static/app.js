/* Family Hub web interface. Plain JavaScript, no build step. */
'use strict';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const COVER_COLORS = ['#6E2B2B', '#2F5D62', '#3F3A6B', '#6A4A1F', '#22506B', '#5E2F4A', '#3D4B2A', '#7A3B2E', '#40405A', '#2B5A45', '#5A3A6B', '#1F4F5E'];
const SCOPES = [['community', 'Community tags'], ['family', 'Family tags'], ['epub', 'Book file tags'], ['mine', 'My tags']];
const SRC_LABEL = { epub: 'Book file', community: 'Community', family: 'Family', mine: 'Mine', genre: 'Genre', author: 'Author' };
const KIND_LABEL = { 'Fiction': 'Fiction', 'Non-fiction': 'Non-fiction', 'Graphic novel': 'Graphic novels', 'Tabletop': 'Tabletop', 'Unsorted': 'Unsorted' };
let S = null; // last /api/state

// ---------------------------------------------------------------- helpers

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'style') node.style.cssText = v;
    else if (k === 'value') node.value = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

// Each move to another screen gets a new number. A page still loading for an earlier screen must not
// draw over the new one, so a GET that finishes after the move throws Stale and that page stops.
let routeGen = 0;
class Stale extends Error {}
async function api(method, url, body) {
  const gen = routeGen;
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  // Moved to another page meanwhile: a save still happened, but nothing may redraw the old page over the new one.
  if (gen !== routeGen) throw new Stale('moved on');
  if (!res.ok) throw new Error((data && data.error) || 'Something went wrong (' + res.status + ').');
  return data;
}

// replaceChildren would print a missing (null) part as the word "null"; this leaves it out.
const fill = (node, ...kids) => node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));

let toastTimer = null;
function toast(message, isError, ms) {
  const t = document.getElementById('toast');
  t.textContent = message;
  t.className = 'show' + (isError ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ''; }, ms || (isError ? 6000 : 3000));
}
const oops = (e) => { if (!(e instanceof Stale)) toast(e.message, true); };
const qs = (o) => Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined)
  .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const plural = (n, word, many) => n + ' ' + (n === 1 ? word : (many || word + 's'));
const niceDate = (iso) => { if (!iso) return ''; const d = new Date(iso.length === 10 ? iso + 'T00:00:00' : iso); return isNaN(d) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); };
function ago(iso) {
  if (!iso) return 'Never';
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 2) return 'Just now';
  if (mins < 60) return mins + ' minutes ago';
  if (mins < 1440) return plural(Math.round(mins / 60), 'hour') + ' ago';
  return plural(Math.round(mins / 1440), 'day') + ' ago';
}
// Long titles get smaller type on a cover, so every cover keeps the same shape.
function titleSize(title) { const n = (title || '').length; return n > 70 ? ' t-xlong' : n > 38 ? ' t-long' : ''; }
function coverColor(title) { let n = 0; for (const c of title) n = (n * 31 + c.charCodeAt(0)) >>> 0; return COVER_COLORS[n % COVER_COLORS.length]; }
function field(labelText, input, cls) {
  const id = 'f' + Math.random().toString(36).slice(2, 9);
  input.id = id;
  return el('div', { class: 'field ' + (cls || '') }, el('label', { class: 'lab', for: id, text: labelText }), input);
}
// A panel heading: the title, the design's hatch, then anything that sits on the right.
function head(title, ...right) {
  return el('div', { class: 'ph' }, el('h2', { text: title }), el('span', { class: 'hatch' }), ...right);
}
// A progress bar in the design's style. pct is 0 to 100.
function bar(pct, label) {
  return el('div', { class: 'bar', role: 'img', 'aria-label': label }, el('i', { style: 'width:' + Math.max(0, Math.min(100, pct)) + '%' }));
}
const back = (href, text) => el('a', { class: 'back', href }, icon('M15 5l-7 7 7 7'), text);
function icon(path, size) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('width', size || 20); svg.setAttribute('height', size || 20);
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round'); svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', path); svg.append(p);
  return svg;
}
const isGuest = () => S.me && S.me.guest;
const isAdmin = () => S.me && S.me.role === 'admin';
const canSort = () => S.me && !S.me.guest && (S.me.role === 'admin' || S.me.can_tag);
// Which kinds of book the Library shows is remembered on this device, per profile.
const kindsKey = () => 'bookhub-kinds-' + (S.me && S.me.id ? S.me.id : 'guest');
function savedKinds() {
  try { const v = JSON.parse(localStorage.getItem(kindsKey())); if (Array.isArray(v) && v.length) return v.filter((k) => S.kinds.includes(k)); } catch (e) { /* nothing saved */ }
  return S.kinds.slice();
}
// Add a book to someone's "My books" or take it off, then say what happened. profile is for admins acting for someone else.
async function setShelf(bookId, on, profile) {
  const r = await api('POST', '/api/books/' + bookId + '/shelf', { on, profile_id: profile ? profile.id : undefined });
  const whose = profile ? profile.name + "'s books" : 'your books';
  if (on) toast('Added to ' + whose + '.' + (r.sending.length ? ' Sending to ' + r.sending.join(' and ') + '.' : ''));
  else if (r.still_on.length) toast('Removed from ' + whose + '. Reading history is kept. The copy on ' + r.still_on.join(' and ') + ' stays there until it is deleted on the device itself.', false, 10000);
  else toast('Removed from ' + whose + '. Reading history is kept.');
  return r;
}
const canRequest = () => S.me && !S.me.guest && (S.me.role === 'admin' || S.me.can_request);

/// ----------------------------------------------------------------- shell

const main = () => document.getElementById('main');
function setMain(...kids) { const m = main(); m.replaceChildren(...kids.flat().filter(Boolean)); }

// The sections in the sidebar, with the design's line icons.
const SECTIONS = [
  ['home', 'Home', '#/home', 'M3 11l9-7 9 7v9h-6v-6H9v6H3z'],
  ['movies', 'Movies and TV Shows', '#/movies', 'M3 5h18v14H3zM7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4'],
  ['books', 'Books', '#/library', 'M4 4h7a2 2 0 0 1 1 1 2 2 0 0 1 1-1h7v15h-7a2 2 0 0 0-1 1 2 2 0 0 0-1-1H4zM12 5v15'],
  ['downloads', 'Downloads', '#/downloads', 'M12 4v11M7 10l5 5 5-5M5 20h14'],
  ['photos', 'Photos', '#/photos', 'M3 5h18v14H3zM3 16l5-5 4 4 3-3 6 6M16 7.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 1 0 0-3'],
  ['files', 'Files', '#/files', 'M3 6h7l2 2h9v11H3z'],
  ['admin', 'Admin', '#/admin', 'M4 7h10M18 7h2M4 12h3M11 12h9M4 17h12M14 5h4v4h-4zM7 10h4v4H7zM16 15h4v4h-4z'],
];
function sectionIcon(d) {
  const svg = icon(d, 18); svg.setAttribute('stroke-width', '1.5'); svg.setAttribute('stroke-linecap', 'square'); svg.setAttribute('stroke-linejoin', 'miter');
  return svg;
}
function starMark() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', '#star'); svg.append(use);
  return svg;
}
// Which section each page belongs to, and the tabs along the top of that section.
const PAGE_SECTION = { home: 'home', movies: 'movies', downloads: 'downloads', photos: 'photos', files: 'files', admin: 'admin', sort: 'admin', tags: 'admin', copies: 'admin' };
function sectionTabs(section) {
  if (section === 'books') {
    const t = [['library', 'Library', '#/library']];
    if (!isGuest()) t.push(['dashboard', 'Dashboard', '#/dashboard'], ['discover', 'Discover', '#/discover'], ['downloads', 'Downloads', '#/downloads']);
    return t;
  }
  if (section === 'admin') return [['hub', 'Hub', '#/admin'], ['books', 'Books', '#/admin/books']];
  if (section === 'movies') return mediaTabs();
  return [];
}
const SECTION_LOGO = { home: 'Home', movies: 'Movies and TV', downloads: 'Downloads', books: 'Books', photos: 'Photos', files: 'Files', admin: 'Admin', profiles: 'Family Hub' };

// Put the household's chosen colour scheme and accent in place, if the page is not showing them already.
function applyLook(look) {
  const root = document.documentElement;
  if (root.dataset.design !== look.design) {
    document.getElementById('ds-fonts').href = look.fonts;
    document.getElementById('ds-tokens').href = '/static/looks/' + look.design + '/tokens.css';
    document.getElementById('ds-bundle').href = '/static/looks/' + look.design + '/bundle.css';
    root.dataset.design = look.design;
  }
  root.dataset.theme = look.theme;
  setAccent(look.accent);
}

// The visuals at the top of Home run in sandboxed frames (visuals.py), which cannot read this page's
// colours. Each is sent them, with the home town, when it is ready and whenever the look changes.
const visualFrames = new Set();
const VISUAL_TOKENS = ['--bg', '--panel', '--grid', '--line', '--line2', '--ink', '--dim', '--accent', '--font-label'];
function sendLook(frame) {
  if (!frame.contentWindow) return;
  const cs = getComputedStyle(document.documentElement);
  const tokens = Object.fromEntries(VISUAL_TOKENS.map((k) => [k, cs.getPropertyValue(k).trim()]));
  frame.contentWindow.postMessage({ type: 'hub-look', tokens, place: (S.visuals && S.visuals.place) || null }, '*');
}
function sendLookToVisuals() {
  for (const f of visualFrames) { if (f.isConnected) sendLook(f); else visualFrames.delete(f); }
}
addEventListener('message', (e) => {
  const frame = [...visualFrames].find((f) => f.contentWindow === e.source);
  if (!frame || !e.data || typeof e.data !== 'object') return;
  if (e.data.type === 'hub-visual-ready') sendLook(frame);
  else if (e.data.type === 'hub-visual' && frame.onVisual) frame.onVisual(e.data);
});

// One visual in its frame: pin is a visual's id, or '' for today's. onVisual hears its name, modes and the list.
function visualFrame(pin, onVisual) {
  const ver = (S.visuals && S.visuals.version) || 'builtin';
  const frame = el('iframe', { class: 'visual-frame', title: "Today's visual", loading: 'lazy', referrerpolicy: 'no-referrer',
    sandbox: 'allow-scripts', src: '/visuals/' + encodeURIComponent(ver) + '/frame.html' + (pin ? '?pin=' + encodeURIComponent(pin) : '') });
  frame.onVisual = onVisual;
  frame.addEventListener('load', () => sendLook(frame));
  visualFrames.add(frame);
  return frame;
}

// The accent hue on the page: none means the design's own (purple, or blue in Blueprint).
function setAccent(hue) {
  const root = document.documentElement;
  if (hue === null || hue === undefined) { delete root.dataset.accent; root.style.removeProperty('--accent-h'); }
  else { root.dataset.accent = 'custom'; root.style.setProperty('--accent-h', hue); }
  sendLookToVisuals();
}

let pageTitle = 'Family Hub';  // the tab title for the current screen, put back after a "ready" notice

function shell(section, tab) {
  applyLook(S.look);
  const signedIn = !!S.me;
  document.getElementById('shell').classList.toggle('signed-out', !signedIn);
  const sections = SECTIONS.filter(([k]) => (k !== 'admin' || isAdmin()) && ((k !== 'movies' && k !== 'downloads') || !isGuest()));
  fill(document.getElementById('side'),
    el('div', { class: 'hub glow' }, starMark(), 'Family Hub'),
    el('nav', { 'aria-label': 'Family Hub sections' }, sections.map(([k, label, href, d]) =>
      el('a', { href, 'aria-current': section === k ? 'page' : null }, sectionIcon(d), el('span', { text: label })))),
    signedIn ? el('div', { class: 'sidefoot lab' }, isGuest() ? 'Guest · nothing is tracked' : S.me.name + ' · ' + (isAdmin() ? 'Admin' : 'Member'),
      S.me.via_admin ? el('br') : null, S.me.via_admin ? 'Opened by an admin' : null) : null);
  const tabs = sectionTabs(section);
  fill(document.getElementById('top'),
    el('div', { class: 'logo glow' }, section === 'profiles' ? starMark() : null, SECTION_LOGO[section] || 'Family Hub'),
    el('nav', { class: 'nav', 'aria-label': SECTION_LOGO[section] || 'Family Hub' }, tabs.map(([k, label, href]) =>
      el('a', { href, 'aria-current': tab === k ? 'page' : null, text: label }))),
    signedIn ? el('a', { class: 'who', href: '#/profiles' },
      el('span', { class: 'arch', 'aria-hidden': 'true', text: S.me.name[0].toUpperCase() }),
      el('span', { text: S.me.name + ' · switch' })) : null);
  const where = section === 'profiles' ? 'Profiles' : (SECTIONS.find((s) => s[0] === section) || [0, 'Home'])[1];
  document.getElementById('strip').replaceChildren(el('span', { text: 'Family Hub' + S.look.sep + where }));
  // The browser tab and screen readers name the section too.
  document.title = pageTitle = where + ' · Family Hub';
  // On a phone the sidebar is one sideways row; keep the current section in view rather than off the edge.
  const side = document.getElementById('side');
  const current = side.querySelector('nav a[aria-current]');
  side.scrollLeft = current && side.scrollWidth > side.clientWidth ? Math.max(0, current.offsetLeft - 16) : 0;
}

let lastHash = null;
async function route() {
  routeGen++;
  try { S = await api('GET', '/api/state'); } catch (e) { if (e instanceof Stale) return; setMain(el('p', { class: 'bad', text: e.message })); return; }
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  let page = parts[0] || (S.me ? 'home' : 'profiles');
  if (S.setup_needed) { shell('profiles'); return pageSetup(); }
  if (!S.me) page = 'profiles';
  if (isGuest() && ['dashboard', 'mybooks', 'admin', 'sort', 'tags', 'copies', 'discover', 'devices', 'series', 'read', 'movies', 'downloads'].includes(page)) page = 'library';
  if (!S.series && (page === 'series' || page === 'read')) page = 'library';
  if (['admin', 'sort', 'tags', 'copies'].includes(page) && !isAdmin()) page = 'home';
  const section = page === 'profiles' ? 'profiles' : (PAGE_SECTION[page] || 'books');
  let tab = { library: 'library', book: 'library', shelf: 'library', series: 'library', read: 'library', dashboard: 'dashboard', mybooks: 'dashboard', devices: 'dashboard', discover: 'discover' }[page];
  if (section === 'admin') tab = page === 'admin' && parts[1] !== 'books' ? 'hub' : 'books';
  if (section === 'movies') tab = parts[1] === 'search' ? 'search' : 'watch';
  shell(section, tab);
  Tracker.kick();
  // The design's screen-change effect, when moving to a different screen. Its stylesheet keeps it still for reduced motion.
  const moved = lastHash !== null && lastHash !== location.hash;
  if (S.look.transition && moved) SetPieces.transition();
  lastHash = location.hash;
  // A new screen starts at the top, not wherever the last one was scrolled to.
  if (moved) window.scrollTo(0, 0);
  setMain(el('p', { class: 'small dim', role: 'status', text: 'Loading…' }));
  const pages = { home: pageHome, movies: pageMovies, downloads: pageDownloads, shelf: pageShelf, photos: pageSoon, files: pageSoon, profiles: pageProfiles, library: pageLibrary, book: pageBook,
    dashboard: pageDashboard, admin: parts[1] === 'books' ? pageBooksAdmin : pageHubAdmin, sort: pageSort, tags: pageTags, copies: pageCopies, details: pageDetails,
    discover: pageDiscover, devices: pageDevices, series: pageSeries, read: pageRead, mybooks: pageMyBooks };
  const gen = routeGen;
  try { await (pages[page] || pageLibrary)(page === 'admin' ? parts[2] : parts[1], parts[2]); } catch (e) { if (!(e instanceof Stale)) setMain(el('p', { class: 'bad', text: e.message })); }
  // The link that was used has been redrawn, so keyboard and screen reader users would be left at the top of
  // the document. Start them at the new page instead, unless the page put focus somewhere itself (a search box, say).
  if (moved && gen === routeGen && (!document.activeElement || document.activeElement === document.body)) main().focus({ preventScroll: true });
}
window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);
// After signing in: changing the address draws Home by itself; only an address already on Home needs a redraw.
const goHome = () => { if (location.hash === '#/home') route(); else location.hash = '#/home'; };
// The skip link moves focus to the page without changing the address (which would change page).
document.addEventListener('click', (e) => {
  if (!e.target.closest || !e.target.closest('a.skip')) return;
  e.preventDefault();
  main().focus();
});

// --------------------------------------------------------- films and TV

const canRequestMedia = () => S.me && !S.me.guest && S.me.can_request_media;
const KIND_WORD = { film: 'Film', show: 'TV show' };
function gb(n) { return n >= 1e9 ? (n / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(n / 1e6)) + ' MB'; }

// A poster card in the shelf. Posters keep their own colours, like book covers.
function posterCard(item, extra) {
  const href = item.id ? '#/movies/' + item.kind + '/' + item.id : null;
  const props = { class: 'cover' + (item.poster ? ' has-img' : '') + titleSize(item.title), style: '--cv:' + coverColor(item.title) + (item.poster ? ';background-image:url(' + item.poster + ')' : '') };
  const inner = [el('span', { class: 'fmt', text: item.kind === 'film' ? 'FILM' : 'TV' }), el('span', { class: 't', text: item.title })];
  return el('div', { class: 'book' }, href ? el('a', Object.assign({ href }, props), inner) : el('div', props, inner),
    el('div', { class: 'small dim', text: [item.year, KIND_WORD[item.kind]].filter(Boolean).join(' · ') }), extra || null);
}
function mediaState(item) {
  // A version found and coming in says so, rather than "being looked for".
  const d = item.downloading;
  if (d && !item.downloaded) {
    const e = item.episodes;
    if (d.stuck) return el('div', { class: 'small warn', text: 'Downloaded · could not be filed yet' });
    if (d.progress >= 100) return el('div', { class: 'small ok', text: 'Downloaded' });
    return el('div', { class: 'small ok', text: 'Downloading' + (item.kind === 'show' && d.episodes > 1 ? ' ' + plural(d.episodes, 'episode') : '') + ' · ' + d.progress + '%'
      + (item.kind === 'show' && e && e.have ? ' · ' + e.have + ' of ' + plural(e.want, 'episode') + ' on the server' : '') });
  }
  if (!item.monitored && !item.downloaded && item.id) return el('div', { class: 'small dim', text: 'Not being looked for' });
  if (item.kind === 'film') return item.downloaded ? el('div', { class: 'small ok', text: 'Downloaded · ready to watch' }) : el('div', { class: 'small warn', text: 'Being looked for' });
  const e = item.episodes || { have: 0, want: 0 };
  if (!e.want) return el('div', { class: 'small warn', text: 'Being looked for' });
  return el('div', { class: 'small ' + (item.downloaded ? 'ok' : 'warn'), text: item.downloaded ? 'Every episode downloaded · ready to watch' : e.have + ' of ' + plural(e.want, 'episode') + ' on the server' });
}

function mediaTabs() {
  return [['watch', 'Watch', '#/movies'], ['search', 'Search', '#/movies/search'], ['downloads', 'Downloads', '#/downloads']];
}

async function pageMovies(sub, id) {
  if (sub === 'film' || sub === 'show') return pageMediaItem(sub, Number(id));
  if (sub === 'downloads') { location.replace('#/downloads'); return; }  // where Downloads used to be
  if (sub === 'search') return pageMediaSearch();
  const f = { show: 'all', q: '' };
  const shelf = el('div', { class: 'shelf' });
  const count = el('p', { class: 'small dim', role: 'status' });
  const chips = el('div', { class: 'row', role: 'group', 'aria-label': 'Filter films and shows', style: 'gap:8px' });
  let items = [], problems = [];
  const sets = await Promise.all(['film', 'show'].filter((k) => (k === 'film' ? S.media.films : S.media.shows))
    .map((k) => api('GET', '/api/media/library?kind=' + k).then((d) => d.items).catch((e) => { if (e instanceof Stale) throw e; problems.push(e.message); return []; })));
  items = sets.flat().sort((a, b) => (b.added || '').localeCompare(a.added || ''));
  function draw() {
    chips.replaceChildren(...[['all', 'Everything'], ['film', 'Films'], ['show', 'TV shows'], ['waiting', 'Not on the server yet']].map(([k, label]) =>
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(f.show === k), text: label, onclick: () => { f.show = k; draw(); } })));
    const needle = f.q.toLowerCase();
    const shown = items.filter((i) => (f.show === 'all' || i.kind === f.show || (f.show === 'waiting' && !i.downloaded)) && (!needle || i.title.toLowerCase().includes(needle)));
    shelf.replaceChildren(...shown.map((i) => posterCard(i, mediaState(i))));
    count.textContent = shown.length ? plural(shown.length, 'title') : (items.length ? 'Nothing matches. Try fewer words.' : 'Nothing here yet.');
  }
  const search = el('input', { type: 'search', placeholder: 'Search', oninput: debounce((e) => { f.q = e.target.value; draw(); }, 200) });
  const latest = el('div');
  if (S.media.jellyfin) {
    api('GET', '/api/media/latest').then((d) => fill(latest, d.items.length ? el('section', { class: 'panel cut', style: '--bd:var(--accent)' },
      head('New on the server', el('span', { class: 'small dim', text: 'From Jellyfin' })),
      el('div', { class: 'reel' }, d.items.map((i) => posterCard(Object.assign({}, i, { id: null }),
        i.watch ? el('a', { class: 'btn sm pri', href: i.watch, target: '_blank', rel: 'noopener', 'aria-label': 'Watch ' + i.title + ' in Jellyfin', text: 'Watch' }) : null)))) : null))
      .catch((e) => fill(latest, el('p', { class: 'small warn', text: 'Jellyfin: ' + e.message })));
  }
  const nothing = !S.media.films && !S.media.shows;
  setMain(
    el('div', { class: 'row between end' }, el('div', { class: 'col', style: 'gap:8px' }, el('h1', { class: 'glow', text: 'Watch' }), el('p', { class: 'dim', text: 'Films and series on the family server.' })),
      nothing ? null : field('Search title', search)),
    nothing ? el('section', { class: 'panel cut' }, head('Not set up yet'),
      el('p', { text: 'Films and TV need Radarr and Sonarr. ' + (isAdmin() ? 'Start them with the media profile (guide 14), then press Connect everything under Admin.' : 'Ask an admin to set them up.') }),
      isAdmin() ? el('a', { class: 'btn pri', href: '#/admin', text: 'Open Admin' }) : null) : null,
    latest,
    problems.length ? el('p', { class: 'warn', role: 'status', text: problems.join(' ') }) : null,
    nothing ? null : chips, nothing ? null : count, shelf);
  if (!nothing) draw();
}

// The live story of one film or show: which indexers were asked, what was picked, the download, the filing.
const STEP_MARK = { done: '✓', active: '…', bad: '✕', wait: '○' };
const STEP_CLASS = { done: 'ok', active: '', bad: 'bad', wait: 'dim' };
function activityPanel(kind, id) {
  const now = el('div', { class: 'stack', style: 'gap:8px', 'aria-live': 'polite' }, el('p', { class: 'small dim', text: 'Asking what is happening…' }));
  const list = el('ol', { class: 'steps' });
  const gen = routeGen;
  async function load() {
    let d;
    try { d = await api('GET', '/api/media/activity/' + kind + '/' + id); } catch (e) {
      if (e instanceof Stale) return;
      // Radarr or Sonarr may only be restarting: say so, and try again a little later.
      fill(now, el('p', { class: 'small bad', text: e.message }), el('p', { class: 'small dim', text: 'Trying again shortly.' }));
      if (gen === routeGen) setTimeout(() => { if (gen === routeGen && document.body.contains(now)) load(); }, 20000);
      return;
    }
    const n = d.now;
    fill(now, bar(n.progress, n.progress + '% of the way'),
      el('div', { class: 'row between', style: 'gap:8px' },
        el('span', { class: 'step-now ' + n.state, text: STEP_MARK[n.state] + ' ' + n.text }),
        el('span', { class: 'small dim', text: n.progress + '%' })),
      n.sites ? el('p', { class: 'small dim', text: n.sites }) : null);
    fill(list, d.steps.map((s) => el('li', { class: 'step ' + s.state },
      el('span', { class: 'mark ' + STEP_CLASS[s.state], 'aria-hidden': 'true', text: STEP_MARK[s.state] }),
      el('span', { class: s.state === 'bad' ? 'bad' : '', text: s.text }),
      el('span', { class: 'small dim when', text: s.at ? ago(s.at) : '' }))));
    // Keep watching while this page is open and the film or show is not on the server yet.
    if (!d.finished && gen === routeGen) setTimeout(() => { if (gen === routeGen && document.body.contains(now)) load(); }, 5000);
  }
  load();
  return el('section', { class: 'panel cut' }, head("What's happening", el('span', { class: 'small dim', text: 'Updates by itself' })), now,
    el('details', { class: 'small' }, el('summary', { text: 'Every step' }), list));
}

// While a search runs: a bar, a timer and, when the search goes through Prowlarr, how many sites have answered
// (Prowlarr logs each one as it replies). Returns a function that stops it. sites=false for quick title look-ups.
function searchProgress(box, query, opts) {
  const o = Object.assign({ sites: true, typical: 30, what: 'Searching' }, opts);
  const started = Date.now(), gen = routeGen;
  const fillBar = el('i', { style: 'width:2%' });
  const line = el('span', { class: 'small', text: o.what + '…' });
  const secs = el('span', { class: 'small dim', text: '0 s' });
  fill(box, el('div', { class: 'stack search-progress', role: 'status', 'aria-live': 'polite', style: 'gap:8px' },
    el('div', { class: 'bar' + (o.sites ? '' : ' busy'), role: 'img', 'aria-label': 'Search in progress' }, fillBar),
    el('div', { class: 'row between', style: 'gap:8px' }, line, secs),
    el('p', { class: 'small dim', text: 'Stay on this page until it finishes: the results appear here, and leaving throws them away.' })));
  let stopped = false, timer = null, answered = 0, sites = 0;
  function tick() {
    if (stopped || gen !== routeGen) return;
    const t = Math.round((Date.now() - started) / 1000);
    secs.textContent = t + ' s';
    if (o.sites) {
      // Real progress when sites have answered; otherwise creep along the usual time, never quite reaching the end.
      const byTime = Math.min(90, 100 * t / (o.typical * 1.3));
      const bySites = sites ? 100 * answered / sites : 0;
      fillBar.style.width = Math.max(2, Math.min(97, Math.max(bySites, byTime * 0.6))) + '%';
    }
  }
  async function poll() {
    if (stopped || gen !== routeGen) return;
    try {
      const res = await fetch('/api/search-progress?' + qs({ q: query, ago: (Date.now() - started) / 1000 }), { credentials: 'same-origin' });
      if (res.ok) {
        const d = await res.json();
        if (!stopped && d.sites) {
          answered = d.answered; sites = d.sites;
          line.textContent = 'Asked ' + plural(sites, 'site') + ' · ' + answered + ' answered' + (d.names.length ? ' (latest: ' + d.names[0] + ')' : '') + (answered >= sites ? ' · putting the list together…' : '');
        }
      }
    } catch (e) { /* the bar keeps going on time alone */ }
    if (!stopped) timer = setTimeout(poll, 1500);
  }
  const clock = setInterval(() => { if (stopped || gen !== routeGen) clearInterval(clock); else tick(); }, 500);
  if (o.sites) poll();
  return () => { stopped = true; clearInterval(clock); clearTimeout(timer); };
}

// The corner pop-up: films and shows this browser asked for or opened, followed on every page until they are
// ready and dismissed. The list is a per-browser convenience, so it lives in localStorage.
const Tracker = (() => {
  const KEY = 'fh-tracking', MIN = 'fh-tracking-min';  // entries: {kind: film | show | book, id: number, or a book's release name}
  const read = (k, fallback) => { try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch (e) { return fallback; } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private window: just not remembered */ } };
  let box = null, timer = null, shown = [], runs = 0;

  function follow(kind, id) {
    const list = read(KEY, []);
    if (!list.some((x) => x.kind === kind && x.id === id)) { list.push({ kind, id, finished: false }); write(KEY, list.slice(-6)); }
    kick();
  }
  function forget(kind, id) {
    write(KEY, read(KEY, []).filter((x) => !(x.kind === kind && x.id === id)));
    render(shown.filter((x) => !(x.kind === kind && x.id === id)));
  }
  function render(items) {
    shown = items;
    if (!box) {
      box = el('aside', { class: 'tracker', 'aria-label': 'Films, shows and books on their way' });
      document.body.append(box);
    }
    box.hidden = !items.length;
    if (!items.length) return;
    const small = read(MIN, false);
    const waiting = items.filter((x) => !x.finished).length;
    const ready = items.length - waiting;
    fill(box,
      el('div', { class: 'row between tracker-head' },
        el('strong', { class: 'small', text: [waiting ? 'Looking for ' + waiting : '', ready ? ready + ' ready' : ''].filter(Boolean).join(' · ') }),
        el('button', { class: 'btn sm', type: 'button', 'aria-expanded': String(!small), text: small ? 'Show' : 'Hide', onclick: () => { write(MIN, !small); render(shown); } })),
      small ? null : items.map((x) => el('div', { class: 'tracker-item stack' + (x.finished ? ' ready' : '') },
        el('div', { class: 'row between', style: 'gap:8px;flex-wrap:nowrap' },
          el('a', { href: x.kind === 'book' ? (x.finished ? '#/library' : '#/downloads') : '#/movies/' + x.kind + '/' + x.id, class: 'tracker-title', text: x.title, title: x.title }),
          el('button', { class: 'btn sm', type: 'button', 'aria-label': 'Stop showing ' + x.title + ' here', text: '✕', onclick: () => forget(x.kind, x.id) })),
        x.finished ? el('span', { class: 'small ok', text: x.kind === 'book' ? '✓ In the library' : '✓ Ready to watch' }) : [
          bar(x.now.progress, x.now.progress + '% of the way'),
          el('span', { class: 'small ' + (x.now.state === 'bad' ? 'bad' : 'dim'), text: x.now.text })])));
  }
  async function refresh() {
    clearTimeout(timer);
    const list = read(KEY, []);
    if (!list.length || !S || !S.me || isGuest()) { render([]); return; }
    // Not api(): the pop-up outlives page changes, so its answers must not be thrown away as stale.
    const run = ++runs;
    let d = null;
    const media = list.filter((x) => x.kind !== 'book'), books = list.filter((x) => x.kind === 'book');
    const query = 'items=' + media.map((x) => x.kind + ':' + x.id).join(',') + books.map((x) => '&book=' + encodeURIComponent(x.id)).join('');
    try {
      const res = await fetch('/api/media/tracking?' + query, { credentials: 'same-origin' });
      if (res.ok) d = await res.json();
    } catch (e) { /* offline for a moment */ }
    if (run !== runs) return;  // a newer refresh has started
    if (!d) { timer = setTimeout(refresh, 60000); return; }  // the server may be busy; try again later without bothering anyone
    // One Radarr or Sonarr not answering keeps its last title and progress, with the reason as its line.
    const items = d.items.filter((x) => !x.gone).map((x) => {
      if (!x.unreachable) return x;
      const prev = shown.find((y) => y.kind === x.kind && y.id === x.id);
      if (prev && prev.finished) return prev;
      return Object.assign({}, x, { title: (prev && prev.title) || KIND_WORD[x.kind], now: Object.assign({}, x.now, { progress: prev && prev.now ? prev.now.progress : 0 }) });
    });
    // Say so once when something becomes ready, including in the browser tab's title when this tab is in the background.
    for (const x of items) {
      const was = list.find((y) => y.kind === x.kind && y.id === x.id);
      if (x.finished && was && !was.finished) {
        toast(x.title + (x.kind === 'book' ? ' is in the library.' : ' is ready to watch.'), false, 6000);
        if (document.hidden) document.title = '✓ ' + x.title + ' is ready · ' + pageTitle;
      }
    }
    write(KEY, items.map((x) => ({ kind: x.kind, id: x.id, finished: x.finished })));
    render(items);
    if (items.some((x) => !x.finished)) timer = setTimeout(refresh, document.hidden ? 30000 : 15000);
  }
  function kick() { refresh(); }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) document.title = pageTitle; });
  return { follow, kick };
})();

function lookingPanel(kind, id, item, again) {
  const what = kind === 'film' ? 'the film' : 'episodes';
  const press = (path, body) => async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try { const d = await api('POST', path, body); toast(d.message); again(); } catch (e) { btn.disabled = false; oops(e); }
  };
  const looking = item.monitored;
  return el('section', { class: 'panel cut' },
    head(item.downloaded ? 'Look for a better version' : 'Looking for it', item.quality ? el('span', { class: 'small dim', text: 'Now: ' + item.quality }) : null),
    el('p', { class: 'small dim', text: looking
      ? 'Heard there is a new or better release? Look again now instead of waiting for the next regular check. ' + (kind === 'film' ? 'The film' : 'An episode') + ' is only swapped for a higher quality or a fixed copy. Happy with what you have, or tired of waiting? Stop looking.'
      : 'Not looking for ' + what + ' at the moment. Nothing new will be downloaded until you start again.' }),
    el('div', { class: 'row' }, looking ? [
      el('button', { class: 'btn', type: 'button', text: 'Look again now', onclick: press('/api/media/look-again/' + kind + '/' + id) }),
      el('button', { class: 'btn', type: 'button', text: 'Stop looking', onclick: press('/api/media/looking/' + kind + '/' + id, { on: false }) }),
    ] : el('button', { class: 'btn pri', type: 'button', text: 'Start looking again', onclick: press('/api/media/looking/' + kind + '/' + id, { on: true }) })));
}

async function pageMediaItem(kind, id) {
  const item = await api('GET', '/api/media/item/' + kind + '/' + id);
  if (!item.downloaded && item.monitored) Tracker.follow(kind, id);
  const again = () => pageMediaItem(kind, id).catch(oops);
  const releases = el('div', { class: 'stack' });
  const seasonNums = kind === 'show' ? item.seasons.map((s) => s.number).filter((n) => n > 0) : [];
  const seasonSel = seasonNums.length ? el('select', { 'aria-label': 'Season' }, seasonNums.slice().reverse().map((n) => el('option', { value: n, text: 'Season ' + n }))) : null;
  // Versions appear as each site answers. Radarr or Sonarr check every one in the meantime; when they are done
  // their list (with what would not be picked, and why) takes over. Stop searching keeps what has been found.
  let polling = null;
  const sent = () => toast('Sent to qBittorrent. Happy to keep this one? Press Stop looking once it is on the server, or it may be swapped for a better copy later.', false, 7000);
  function checkedTable(rows) {
    return rows.length ? el('div', { class: 'scroll' }, el('table', {},
      el('thead', {}, el('tr', {}, ['Title', 'Quality', 'Size', 'Indexer', 'Seeders', ''].map((t, i) => el('th', { scope: 'col', class: i === 2 || i === 4 ? 'num' : '', text: t })))),
      el('tbody', {}, rows.map((r) => {
        const btn = el('button', { class: 'btn sm' + (r.rejected ? '' : ' pri'), type: 'button', text: r.rejected ? 'Use this anyway' : 'Use this', 'aria-label': 'Download ' + r.title, onclick: async () => {
          btn.disabled = true;
          try { await api('POST', '/api/media/grab', { kind, guid: r.guid, indexer_id: r.indexer_id }); btn.textContent = 'Sent'; sent(); } catch (e) { btn.disabled = false; oops(e); }
        } });
        return el('tr', {}, el('th', { scope: 'row', style: 'font-weight:400' }, r.title, r.rejected ? el('div', { class: 'small warn', text: 'Would not be picked: ' + r.why }) : null),
          el('td', { text: r.quality }), el('td', { class: 'num', text: gb(r.size) }), el('td', { text: r.indexer }),
          el('td', { class: 'num', text: r.seeders === null || r.seeders === undefined ? '–' : r.seeders }), el('td', {}, btn));
      })))) : el('p', { class: 'small dim', text: 'The indexers have nothing for this right now.' });
  }
  function foundTable(job, rows) {
    return rows.length ? el('div', { class: 'scroll' }, el('table', {},
      el('thead', {}, el('tr', {}, ['Title', 'Quality (from its name)', 'Size', 'Site', 'Seeders', ''].map((t, i) => el('th', { scope: 'col', class: i === 2 || i === 4 ? 'num' : '', text: t })))),
      el('tbody', {}, rows.map((r) => {
        const note = el('div', { class: 'small warn' });
        const btn = el('button', { class: 'btn sm pri', type: 'button', text: 'Use this', 'aria-label': 'Download ' + r.title });
        btn.onclick = async () => {
          const force = btn.dataset.force === '1';
          btn.disabled = true;
          try {
            const d = await api('POST', '/api/media/versions/' + job + '/grab', { guid: r.guid, force });
            if (d.ok) { btn.textContent = 'Sent'; note.textContent = d.direct ? 'Sent straight to qBittorrent. If ' + (kind === 'film' ? 'Radarr' : 'Sonarr') + ' cannot tell what it is, file it from its Activity page.' : ''; sent(); return; }
            note.textContent = (kind === 'film' ? 'Radarr' : 'Sonarr') + ' turned it down: ' + d.why;
            btn.textContent = 'Use this anyway'; btn.dataset.force = '1'; btn.classList.remove('pri'); btn.disabled = false;
          } catch (e) { btn.disabled = false; oops(e); }
        };
        return el('tr', {}, el('th', { scope: 'row', style: 'font-weight:400' }, r.title, note),
          el('td', { class: r.quality === 'Cam' || r.quality === 'Remux' ? 'warn' : '', text: r.quality || '?' }), el('td', { class: 'num', text: gb(r.size) }), el('td', { text: r.indexer }),
          el('td', { class: 'num', text: r.seeders === null || r.seeders === undefined ? '–' : r.seeders }), el('td', {}, btn));
      })))) : null;
  }
  async function loadReleases() {
    clearTimeout(polling);
    const gen = routeGen;
    const status = el('div', { class: 'stack', role: 'status', 'aria-live': 'polite', style: 'gap:8px' });
    const list = el('div', { class: 'stack' });
    let stopped = false, job = null;
    const stopBtn = el('button', { class: 'btn sm', type: 'button', text: 'Stop searching', onclick: () => {
      stopped = true; clearTimeout(polling); stopBtn.remove();
      status.lastChild && (status.lastChild.textContent = 'Stopped. These are the versions found so far; pick one, or press Show versions to look again.');
    } });
    fill(releases, status, list);
    fill(status, el('p', { class: 'small dim', text: 'Starting the search…' }));
    try { job = (await api('POST', '/api/media/versions/' + kind + '/' + id + (seasonSel ? '?season=' + seasonSel.value : ''))).job; } catch (e) {
      if (!(e instanceof Stale)) fill(releases, el('p', { class: 'small bad', text: e.message }));
      return;
    }
    let shownCount = -1;
    async function poll() {
      if (stopped || gen !== routeGen || !document.body.contains(list)) return;
      let d;
      try { d = await api('GET', '/api/media/versions/' + job); } catch (e) {
        if (!(e instanceof Stale)) fill(status, el('p', { class: 'small bad', text: e.message }));
        return;
      }
      if (stopped) return;
      if (d.checked !== null && (d.checked.length || d.answered >= d.sites)) {
        const who = kind === 'film' ? 'Radarr' : 'Sonarr';
        if (!d.checked.length && d.found.length) {
          // Radarr or Sonarr failed or came back empty-handed: keep what the sites offered rather than an empty list.
          fill(status, el('p', { class: 'small warn', text: d.checked_error ? who + ' could not check them: ' + d.checked_error : who + ' did not find these itself.' }),
            el('p', { class: 'small dim', text: 'These are the versions the sites offered, best first by the quality in each name. You can still pick one.' }));
          fill(list, foundTable(job, d.found));
          return;
        }
        // Radarr or Sonarr have checked everything: their list is the better one.
        fill(status, d.checked_error ? el('p', { class: 'small warn', text: who + ' could not check them: ' + d.checked_error })
          : el('p', { class: 'small ok', text: who + ' has checked every version (' + d.seconds + ' s). Versions marked "would not be picked" can still be used.' }));
        fill(list, checkedTable(d.checked));
        return;
      }
      const pct = d.sites ? Math.round(100 * d.answered / d.sites) : 0;
      fill(status, bar(Math.max(3, pct), pct + '% of sites have answered'),
        el('div', { class: 'row between', style: 'gap:8px' },
          el('span', { class: 'small', text: (d.sites ? d.answered + ' of ' + plural(d.sites, 'site') + ' answered · ' : '') + plural(d.found.length, 'version') + ' so far' }),
          el('div', { class: 'row', style: 'gap:8px' }, el('span', { class: 'small dim', text: d.seconds + ' s' }), stopBtn)),
        el('p', { class: 'small dim', text: d.answered >= d.sites && d.sites ? 'Every site has answered; ' + (kind === 'film' ? 'Radarr' : 'Sonarr') + ' is still checking them against the household rule. You can pick one now.'
          : 'Best first, by the quality in each name. Pick one now, or wait for the full list.' }));
      // Only redraw the table when something new arrived, and never under someone using its buttons.
      if (d.found.length !== shownCount && !list.contains(document.activeElement)) { shownCount = d.found.length; fill(list, foundTable(job, d.found)); }
      polling = setTimeout(poll, 1500);
    }
    poll();
  }
  const facts = [item.year, KIND_WORD[kind], item.runtime ? item.runtime + ' min' : '', item.network].filter(Boolean).join(' · ');
  setMain(back('#/movies', 'Back to watch'),
    el('div', { class: 'detail' },
      el('div', { class: 'bside' }, el('div', { class: 'cover' + (item.poster ? ' has-img' : '') + titleSize(item.title), style: '--cv:' + coverColor(item.title) + (item.poster ? ';background-image:url(' + item.poster + ')' : '') },
        el('span', { class: 'fmt', text: kind === 'film' ? 'FILM' : 'TV' }), el('span', { class: 't', text: item.poster ? '' : item.title }))),
      el('div', { class: 'bbody' },
        el('div', { class: 'stack' }, el('h1', { class: 'glow', text: item.title }), el('p', { class: 'dim', text: facts }),
          item.genres.length ? el('p', { class: 'lab', text: item.genres.join(' · ') }) : null,
          mediaState(item),
          el('p', { class: 'small dim', text: [item.size ? gb(item.size) + ' on the server' : '', item.requested_by ? 'requested by ' + item.requested_by : '', item.added ? 'added ' + niceDate(item.added) : ''].filter(Boolean).join(' · ') })),
        item.downloaded || kind === 'show' ? null : activityPanel(kind, id),
        kind === 'film' && item.downloaded ? watchPanel(kind, id, item) : null,
        kind === 'show' ? watchPanel(kind, id, item) : null,
        kind === 'show' && !item.downloaded ? activityPanel(kind, id) : null,
        item.overview ? el('section', { class: 'stack' }, head('Story'), el('p', { class: 'synopsis', text: item.overview })) : null)),
    // The working panels use the whole width, so the list of versions fits without scrolling sideways.
    el('div', { class: 'bbody wide-panels' },
        canRequestMedia() ? lookingPanel(kind, id, item, again) : null,
        canRequestMedia() ? el('section', { class: 'panel cut' }, head('Pick a version yourself', seasonSel, el('button', { class: 'btn sm', type: 'button', text: 'Show versions', onclick: loadReleases })),
          el('p', { class: 'small dim', text: (kind === 'film' ? 'Radarr' : 'Sonarr') + ' picks the best version by itself. If it is taking too long, or you are happy with a smaller or lower-quality copy, see every version the indexers have and choose one. Versions marked "would not be picked" can still be used.' }), releases) : null,
        isAdmin() ? el('section', { class: 'panel cut', style: '--bd:var(--warn)' }, head('Remove from the server'),
          el('p', { class: 'small dim', text: 'Deletes ' + (kind === 'film' ? 'the film' : 'every episode') + ' and stops looking for ' + (kind === 'film' ? 'it' : 'new ones') + '. This cannot be undone.' }),
          el('div', { class: 'row' }, el('button', { class: 'btn danger', type: 'button', text: 'Remove ' + item.title, onclick: async () => {
            if (!window.confirm('Remove "' + item.title + '" from the server and delete its files' + (item.size ? ' (' + gb(item.size) + ')' : '') + '? This cannot be undone.')) return;
            try { await api('DELETE', '/api/media/' + kind + '/' + id); toast(item.title + ' was removed.'); location.hash = '#/movies'; } catch (e) { oops(e); }
          } }))) : null));
}

async function pageMediaSearch() {
  const results = el('div', { class: 'stack' });
  const kindSel = el('select', {}, el('option', { value: 'film', text: 'Film' }), el('option', { value: 'show', text: 'TV show' }));
  const q = el('input', { type: 'search' });
  async function search() {
    if (q.value.trim().length < 2) { fill(results, el('p', { class: 'small dim', role: 'status', text: 'Type at least two letters.' })); q.focus(); return; }
    const kind = kindSel.value, run = ++searchRun;
    const stop = searchProgress(results, q.value.trim(), { sites: false, what: 'Looking up titles' });
    try {
      const d = await api('GET', '/api/media/search?' + qs({ kind, q: q.value.trim() })).finally(stop);
      if (run !== searchRun) return;  // a newer search has started; its answer goes here instead
      fill(results, d.results.length ? el('div', { class: 'shelf' }, d.results.map((r) => posterCard(Object.assign({ kind }, r, { id: null }),
        el('div', { class: 'stack', style: 'gap:6px' }, r.overview ? el('div', { class: 'small dim', text: r.overview.length > 120 ? r.overview.slice(0, 120) + '…' : r.overview }) : null,
          r.in_library ? el('div', { class: 'small ok', text: 'Already on the list' }) : requestControls(kind, r))))) : el('p', { class: 'small dim', role: 'status', text: 'Nothing found. Try fewer words.' }));
    } catch (e) { if (!(e instanceof Stale) && run === searchRun) fill(results, el('p', { class: 'bad', role: 'status', text: e.message })); }
  }
  let searchRun = 0;
  setMain(
    el('div', { class: 'col', style: 'gap:8px' }, el('h1', { class: 'glow', text: 'Search' }),
      el('p', { class: 'dim' }, 'Find a film or series and ask for it. Follow it in ', el('a', { href: '#/downloads', text: 'Downloads' }), '.')),
    canRequestMedia() ? el('section', { class: 'panel cut' }, head('Search for a film or show', el('span', { class: 'small dim', text: '2160p first, then 1080p' })),
      el('form', { class: 'row end', role: 'search', onsubmit: (e) => { e.preventDefault(); search(); } }, field('What', kindSel), field('Title', q), el('button', { class: 'btn pri', type: 'submit', text: 'Search' })),
      results) : el('p', { class: 'dim', text: 'This profile can watch but not request new films and shows. Ask an admin.' }));
  q.focus();
}

// Every download in one place: films, TV and books, with pause, reorder and (for admins) delete.
async function pageDownloads() {
  const vpnLine = el('span', { class: 'small dim', text: 'Checking the VPN…' });
  const list = el('div', { class: 'stack' }, el('p', { class: 'small dim', role: 'status', text: 'Asking qBittorrent…' }));
  const added = el('div', { class: 'list' });
  const addedBooks = el('div', { class: 'list' });
  const gen = routeGen;
  let show = 'all', last = null;
  const types = [['all', 'Everything'], ['Film', 'Films'], ['TV', 'TV'], ['Book', 'Books']];
  const chips = el('div', { class: 'row', role: 'group', 'aria-label': 'Show', style: 'gap:8px' });
  function drawChips() {
    fill(chips, types.map(([k, label]) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(show === k), text: label,
      onclick: () => { show = k; drawChips(); draw(); } })));
  }
  function draw() {
    if (!last || list.contains(document.activeElement)) return;  // leave it alone while its buttons are in use
    const shown = last.torrents.filter((t) => show === 'all' || t.type === show);
    const going = shown.filter((t) => !t.done && !t.waiting_seed), finished = shown.filter((t) => t.done);
    const waiting = shown.filter((t) => !t.done && t.waiting_seed);
    fill(list, going.length ? going.map((t) => torrentRow(t, last, loadTorrents))
      : el('p', { class: 'small dim', text: waiting.length ? 'Nothing downloading: everything left is waiting for seed.' : 'Nothing downloading.' }),
      waiting.length ? [el('h3', { class: 'small warn', style: 'margin:8px 0 0', text: 'Waiting for seed (' + waiting.length + ')' }),
        ...waiting.map((t) => torrentRow(t, last, loadTorrents))] : null,
      finished.length ? el('details', {}, el('summary', { class: 'small', text: 'Finished (' + finished.length + ')' }),
        el('div', { class: 'stack', style: 'margin-top:12px' }, finished.map((t) => torrentRow(t, last, loadTorrents)))) : null);
  }
  async function loadTorrents() {
    clearTimeout(list.timer);
    const again = (ms) => { list.timer = setTimeout(() => { if (gen === routeGen && document.body.contains(list)) loadTorrents(); }, ms); };
    try { last = await api('GET', '/api/media/torrents'); } catch (e) {
      if (e instanceof Stale) return;
      // qBittorrent is often only away while the VPN restarts: keep asking, less often.
      last = null;
      fill(list, el('p', { class: 'small bad', role: 'status', text: e.message }), el('p', { class: 'small dim', text: 'Trying again shortly.' }));
      again(20000);
      return;
    }
    draw();
    again(5000);
  }
  const when = (at, by) => el('span', { class: 'small dim', text: [niceDate(at), by ? 'requested by ' + by : ''].filter(Boolean).join(' · ') });
  api('GET', '/api/media/queue').then((d) => {
    vpnLine.textContent = d.vpn.message;
    vpnLine.className = 'small ' + (d.vpn.ok ? 'ok' : 'warn');
    fill(added, d.history.length ? d.history.map((h) => el('div', { class: 'row between' }, el('span', { text: h.title }), when(h.at, h.requested_by)))
      : el('p', { class: 'small dim', text: 'No films or TV added yet.' }));
  }).catch((e) => { if (!(e instanceof Stale)) fill(added, el('p', { class: 'small dim', text: e.message })); });
  api('GET', '/api/discover/queue').then((d) => {
    fill(addedBooks, d.imported.length ? d.imported.slice(0, 8).map((r) => el('div', { class: 'row between' }, el('span', { text: r.name }), when(r.at)))
      : el('p', { class: 'small dim', text: 'No books added from downloads yet.' }));
  }).catch((e) => { if (!(e instanceof Stale)) fill(addedBooks, el('p', { class: 'small dim', text: e.message })); });
  drawChips();
  setMain(
    el('div', { class: 'col', style: 'gap:8px' }, el('h1', { class: 'glow', text: 'Downloads' }),
      el('p', { class: 'dim' }, 'Everything being downloaded for the hub. Ask for films and TV in ', el('a', { href: '#/movies/search', text: 'Movies and TV, Search' }),
        ' and for books in ', el('a', { href: '#/discover', text: 'Books, Discover' }), '.')),
    speedPanel(),
    isAdmin() ? sortPanel() : null,
    el('section', { class: 'panel cut' }, head('Every download', vpnLine),
      el('p', { class: 'small dim', text: 'In the order qBittorrent works through them. Three download at once; the rest wait their turn. '
        + 'One nobody has shared for ten minutes goes to the back, paused, marked waiting for seed.' }), chips, list),
    el('div', { class: 'cols2' },
      el('section', { class: 'panel cut' }, head('Films and TV added'), added),
      el('section', { class: 'panel cut' }, head('Books added'), addedBooks)));
  loadTorrents();
}

const speed = (b) => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB/s' : Math.round(b / 1024) + ' KB/s';
function timeLeft(sec) {
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h >= 48 ? Math.round(h / 24) + ' days' : h ? h + ' h ' + m + ' min' : Math.max(m, 1) + ' min';
}

// One slider for how fast downloads may go, from no limit to a trickle, in small steps, saved when it is let go;
// or type an exact speed. Uploads follow at a quarter of the download limit, since uploading is what slows the
// house's internet most. Under it, the last five minutes of speed, and what every download is doing.
const SPEED_STEPS = [0, 100, 75, 50, 40, 30, 25, 20, 15, 12, 10, 8, 6, 5, 4, 3, 2.5, 2, 1.5, 1, 0.75, 0.5, 0.25, 0.1].map((mb) => Math.round(mb * 1048576));
const SPEED_HISTORY = 60;  // checks kept for the chart, five seconds apart
function speedPanel() {
  const box = el('div', { class: 'stack' }, el('p', { class: 'small dim', role: 'status', text: 'Asking qBittorrent…' }));
  const now = el('span', { class: 'small dim' });
  const counts = el('p', { class: 'small', role: 'status' });
  const chart = el('div', { class: 'speed-chart' });
  const history = [];
  const gen = routeGen;
  let limitNow = 0;
  const words = (b) => b ? 'Up to ' + speed(b) : 'No limit: as fast as it goes';
  function save(b) {
    const up = b ? Math.max(65536, Math.round(b / 4)) : 0;
    api('POST', '/api/media/speed', { down_limit: b, up_limit: up, slow_mode: false })
      .then(() => { limitNow = b; toast(b ? 'Downloads now go up to ' + speed(b) + ', uploads up to ' + speed(up) + '.' : 'Downloads go as fast as they can.'); })
      .catch((err) => { if (!(err instanceof Stale)) { oops(err); drawn = false; } });  // the next check puts the slider back
  }
  function draw(d) {
    const limit = d.slow_mode ? d.slow_down : d.down_limit;
    limitNow = limit;
    // The step closest to the current limit (a limit typed in or set elsewhere shows on the nearest).
    let at = 0;
    SPEED_STEPS.forEach((b, i) => { if (Math.abs(Math.log((b || 1e12) / (limit || 1e12))) < Math.abs(Math.log((SPEED_STEPS[at] || 1e12) / (limit || 1e12)))) at = i; });
    const label = el('strong', { class: 'speed-now', text: words(limit) });
    const canChange = d.can_change !== false;
    const exact = el('input', { type: 'number', min: '0', step: '0.1', inputmode: 'decimal', value: limit ? (limit / 1048576).toFixed(1) : '', placeholder: 'No limit',
      disabled: !canChange, 'aria-label': 'Exact download limit in megabytes per second, empty for no limit' });
    const slider = el('input', { type: 'range', min: '0', max: String(SPEED_STEPS.length - 1), step: '1', value: String(at), disabled: !canChange,
      'aria-label': 'Download speed limit', 'aria-valuetext': words(limit),
      oninput: (e) => {
        const b = SPEED_STEPS[+e.target.value];
        label.textContent = words(b);
        exact.value = b ? (b / 1048576).toFixed(1) : '';
        e.target.setAttribute('aria-valuetext', words(b));
      },
      onchange: (e) => save(SPEED_STEPS[+e.target.value]) });
    const setExact = el('form', { class: 'row', style: 'gap:8px', onsubmit: (e) => {
      e.preventDefault();
      const mb = parseFloat(exact.value);
      const b = exact.value.trim() === '' || !(mb > 0) ? 0 : Math.max(16384, Math.round(mb * 1048576));
      label.textContent = words(b);
      save(b);
    } }, el('label', { class: 'small dim', text: 'Or exactly' }), exact, el('span', { class: 'small dim', text: 'MB/s' }),
      el('button', { class: 'btn sm', type: 'submit', disabled: !canChange, text: 'Set' }));
    fill(box, label,
      el('div', { class: 'speed-slider' }, el('span', { class: 'small dim', text: 'Fastest' }), slider, el('span', { class: 'small dim', text: 'Slowest' })),
      setExact,
      el('p', { class: 'small dim', text: canChange ? 'Slide right when someone needs the internet; downloads keep going, just slower.' : 'This profile can see the speed but not change it.' }),
      counts, chart);
  }
  function tell(d) {
    const c = d.counts || {};
    const bits = [[c.downloading, 'downloading now'], [c.waiting_turn, 'waiting their turn'], [c.waiting_seed, 'waiting for seed'], [c.paused, 'paused'], [c.done, 'done']]
      .filter(([n]) => n).map(([n, w]) => n + ' ' + w);
    counts.textContent = bits.length ? bits.join(' · ') + (d.at_once ? ' · ' + d.at_once + ' download at once' : '') : 'Nothing is downloading.';
    history.push({ down: d.down_now, up: d.up_now, limit: d.slow_mode ? d.slow_down : d.down_limit });
    if (history.length > SPEED_HISTORY) history.shift();
    fill(chart, speedChart(history));
  }
  // The controls are drawn from the first answer, or the first one after qBittorrent comes back or a change fails.
  async function load() {
    try {
      const d = await api('GET', '/api/media/speed');
      now.textContent = 'Now: ' + speed(d.down_now) + ' down, ' + speed(d.up_now) + ' up';
      if (!drawn) { drawn = true; draw(d); }
      tell(d);
    } catch (e) {
      if (e instanceof Stale) return;
      now.textContent = '';
      if (!drawn) fill(box, el('p', { class: 'small bad', text: e.message }));
    }
    setTimeout(() => { if (gen === routeGen && document.body.contains(box)) load(); }, 5000);
  }
  let drawn = false;
  load();
  return el('section', { class: 'panel cut' }, head('Speed', now), box);
}

// Admins: films and TV on the disk that are not filed yet (finished downloads, or copied in from another disk).
// Radarr and Sonarr say what each file is; one they cannot place is searched for by hand. Spare copies of
// something already in the library can be deleted.
function sortPanel() {
  let kind = 'film';
  const out = el('div', { class: 'stack' });
  const where = el('p', { class: 'small dim' });
  const kinds = el('div', { class: 'row', role: 'group', 'aria-label': 'Films or TV', style: 'gap:8px' });
  const gen = routeGen;
  const SECTIONS = [['ready', 'Ready to file'], ['better', 'Better than the copy you have'], ['unknown', 'Not recognised: say what it is'],
    ['have', 'Spare copies: already in the library'], ['problem', 'Cannot be filed']];
  function drawKinds() {
    fill(kinds, [['film', 'Films'], ['show', 'TV']].map(([k, label]) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(kind === k), text: label,
      onclick: () => { kind = k; drawKinds(); look(); } })));
  }
  async function send(btn, path, body, after) {
    btn.disabled = true;
    try { const r = await api('POST', path, Object.assign({ kind }, body)); toast(r.message); if (after) after(r); } catch (e) { if (!(e instanceof Stale)) oops(e); }
    btn.disabled = false;
  }
  function identify(item) {
    const q = el('input', { type: 'search', value: item.guess || '', 'aria-label': 'What is ' + item.name + '?', style: 'flex:1 1 220px;min-width:0' });
    const results = el('div', { class: 'stack', style: 'gap:6px' });
    const find = async (e) => {
      e.preventDefault();
      fill(results, el('p', { class: 'small dim', role: 'status', text: 'Searching…' }));
      try {
        const d = await api('GET', '/api/media/search?kind=' + kind + '&q=' + encodeURIComponent(q.value.trim()));
        fill(results, d.results.length ? d.results.slice(0, 6).map((r) => el('div', { class: 'row between', style: 'gap:8px' },
          el('span', { text: r.title + (r.year ? ' (' + r.year + ')' : '') }),
          el('button', { class: 'btn sm', type: 'button', text: "It's this one", onclick: (ev) => send(ev.currentTarget, '/api/admin/media/sort',
            { path: item.path, external_id: kind === 'film' ? r.tmdb : r.tvdb }, look) })))
          : el('p', { class: 'small dim', role: 'status', text: 'Nothing found. Try fewer words.' }));
      } catch (err) { if (!(err instanceof Stale)) fill(results, el('p', { class: 'small bad', role: 'status', text: err.message })); }
    };
    return el('div', { class: 'stack', style: 'gap:6px;margin-top:6px' },
      el('form', { class: 'row', style: 'gap:8px', role: 'search', onsubmit: find }, q, el('button', { class: 'btn sm', type: 'submit', text: 'Search' })), results);
  }
  function row(item) {
    const name = item.title ? item.title + (item.year ? ' (' + item.year + ')' : '') + (item.episodes ? ' · ' + item.episodes : '') : item.name;
    const facts = [item.quality, gb(item.size), item.unsorted ? 'from the unsorted folder' : 'a finished download',
      item.copies_here > 1 ? item.copies_here + ' copies here' + (item.biggest ? ', this is the biggest' : ', a bigger one is here too') : ''];
    return el('div', { class: 'line torrent', style: 'display:block' },
      el('div', { class: 'row between', style: 'gap:8px' },
        el('div', { class: 'stack', style: 'gap:2px;min-width:0;flex:1 1 240px' }, el('strong', { class: 'torrent-title', text: name }),
          item.title ? el('span', { class: 'small dim torrent-title', text: item.name, title: item.name }) : null),
        el('div', { class: 'row', style: 'gap:6px' },
          item.status === 'ready' || item.status === 'better' ? el('button', { class: 'btn sm pri', type: 'button', text: 'File it',
            onclick: (e) => send(e.currentTarget, '/api/admin/media/sort', { path: item.path }, look) }) : null,
          item.status === 'have' || /short sample/.test(item.reason) ? el('button', { class: 'btn sm danger', type: 'button', text: 'Delete spare',
            onclick: (e) => { if (window.confirm('Delete "' + item.name + '" for good? The copy in the library stays.')) send(e.currentTarget, '/api/admin/media/sort/remove', { path: item.path }, look); } }) : null)),
      el('div', { class: 'small dim', text: facts.filter(Boolean).join(' · ') }),
      el('div', { class: 'small ' + (item.status === 'problem' ? 'bad' : item.status === 'have' ? 'warn' : ''), text: item.reason }),
      item.status === 'unknown' ? identify(item) : null);
  }
  async function look() {
    fill(out, el('p', { class: 'small dim', role: 'status', text: 'Asking ' + (kind === 'film' ? 'Radarr' : 'Sonarr') + ' what each file is…' }));
    try {
      const d = await api('GET', '/api/admin/media/sort?kind=' + kind);
      if (gen !== routeGen) return;
      where.textContent = 'Looks in ' + d.folders.downloads + ' and ' + d.folders.unsorted + '. Copy films and TV from another disk into the unsorted folder.';
      const ready = d.items.filter((i) => i.status === 'ready');
      const fileAll = ready.length > 1 ? el('button', { class: 'btn sm', type: 'button', text: 'File all ' + ready.length + ' ready', onclick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        for (const i of ready) {
          try { const r = await api('POST', '/api/admin/media/sort', { kind, path: i.path }); toast(r.message); } catch (err) { if (!(err instanceof Stale)) oops(err); }
        }
        look();
      } }) : null;
      fill(out, d.items.length ? SECTIONS.map(([s, label]) => {
        const items = d.items.filter((i) => i.status === s);
        return items.length ? [el('div', { class: 'row between', style: 'gap:8px;margin-top:8px' }, el('h3', { class: 'small', text: label + ' (' + items.length + ')' }), s === 'ready' ? fileAll : null),
          ...items.map(row)] : null;
      }).flat() : el('p', { class: 'small dim', text: 'Nothing to sort: every ' + (kind === 'film' ? 'film' : 'episode') + ' here is filed.' }));
    } catch (e) { if (!(e instanceof Stale)) fill(out, el('p', { class: 'small bad', role: 'status', text: e.message })); }
  }
  drawKinds();
  look();
  return el('section', { class: 'panel cut' }, head('Films and TV to sort', el('button', { class: 'btn sm', type: 'button', text: 'Look again', onclick: look })),
    el('p', { class: 'small dim', text: 'Videos on the disk that are not on the server yet. Filing one moves it into the media folder, named so Jellyfin finds it.' }),
    kinds, where, out);
}

// The last five minutes of download and upload speed, with the limit as a dashed line. Hovering (or tapping)
// shows the speeds at that moment.
function speedChart(points) {
  const W = 600, H = 120, P = 4;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs) => { const n = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); return n; };
  const top = Math.max(1048576 / 4, ...points.map((p) => Math.max(p.down, p.up, p.limit || 0))) * 1.1;
  const x = (i) => P + (W - 2 * P) * (points.length < 2 ? 1 : i / (SPEED_HISTORY - 1)) + (W - 2 * P) * (SPEED_HISTORY - points.length) / (SPEED_HISTORY - 1);
  const y = (b) => H - P - (H - 2 * P) * Math.min(b, top) / top;
  const line = (key) => points.map((p, i) => x(i).toFixed(1) + ',' + y(p[key]).toFixed(1)).join(' ');
  const s = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'none', class: 'speed-svg', role: 'img',
    'aria-label': 'Speed over the last five minutes. Now ' + speed(points[points.length - 1].down) + ' down, ' + speed(points[points.length - 1].up) + ' up.' });
  s.append(svg('line', { x1: P, x2: W - P, y1: H - P, y2: H - P, class: 'sc-axis' }));
  const limit = points[points.length - 1].limit;
  if (limit && limit < top) s.append(svg('line', { x1: P, x2: W - P, y1: y(limit), y2: y(limit), class: 'sc-limit' }));
  if (points.length > 1) {
    s.append(svg('polyline', { points: line('up'), class: 'sc-up' }));
    s.append(svg('polyline', { points: line('down'), class: 'sc-down' }));
  }
  const cross = svg('line', { y1: P, y2: H - P, class: 'sc-cross', visibility: 'hidden' });
  s.append(cross);
  const readout = el('span', { class: 'small dim', text: 'Top of the chart: ' + speed(top) });
  s.addEventListener('pointermove', (e) => {
    const r = s.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width * W;
    let i = 0;
    points.forEach((p, j) => { if (Math.abs(x(j) - fx) < Math.abs(x(i) - fx)) i = j; });
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    const ago = (points.length - 1 - i) * 5;
    readout.textContent = (ago ? ago + ' s ago: ' : 'Now: ') + speed(points[i].down) + ' down, ' + speed(points[i].up) + ' up';
  });
  s.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); readout.textContent = 'Top of the chart: ' + speed(top); });
  return el('figure', { class: 'speed-fig' }, s,
    el('figcaption', { class: 'row between', style: 'gap:8px' },
      el('span', { class: 'row', style: 'gap:12px' },
        el('span', { class: 'small key' }, el('i', { class: 'k-down' }), 'Download'),
        el('span', { class: 'small key' }, el('i', { class: 'k-up' }), 'Upload'),
        limit ? el('span', { class: 'small key' }, el('i', { class: 'k-limit' }), 'Limit') : null),
      readout));
}

// One download on the Downloads page, with its pause and move buttons.
function torrentRow(t, d, reload) {
  const canChange = t.type === 'Book' ? d.can_change_books : d.can_change;
  const act = (action) => async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try { await api('POST', '/api/media/torrents/' + t.hash + '/' + action); } catch (e) { if (!(e instanceof Stale)) oops(e); }
    btn.blur();
    reload();
  };
  const b = (text, action, label) => el('button', { class: 'btn sm', type: 'button', text, 'aria-label': label + ' ' + t.title, title: label, onclick: act(action) });
  const mins = (at) => Math.max(0, Math.round((at * 1000 - Date.now()) / 60000));
  const facts = [t.progress + '%', gb(t.size), !t.done && !t.paused && t.speed ? speed(t.speed) : '', !t.done && !t.paused && t.up_speed ? speed(t.up_speed) + ' up' : '',
    !t.done && !t.paused && t.eta ? timeLeft(t.eta) + ' left' : '',
    !t.done ? t.seeds + (t.seeds === 1 ? ' person' : ' people') + ' sharing' : '', t.position ? 'number ' + t.position + ' in line' : '',
    t.waiting_seed && t.checked ? 'last checked ' + Math.max(0, -mins(t.checked)) + ' min ago' : '',
    t.waiting_seed && t.next_check ? 'next check in ' + mins(t.next_check) + ' min' : '',
    t.requested_by ? 'requested by ' + t.requested_by : ''];
  return el('div', { class: 'line torrent', style: 'display:block' },
    el('div', { class: 'row between', style: 'gap:8px' },
      el('div', { class: 'row', style: 'gap:8px;min-width:0;flex:1 1 240px' }, el('span', { class: 'chip xs', text: t.type }),
        t.kind && t.id ? el('a', { href: '#/movies/' + t.kind + '/' + t.id, class: 'torrent-title', text: t.title, title: t.name })
          : el('span', { class: 'torrent-title', text: t.title, title: t.name })),
      el('span', { class: 'small ' + (t.trouble ? 'bad' : t.paused || t.waiting_seed || /Waiting for someone/.test(t.state) ? 'warn' : t.done ? 'ok' : ''), text: t.state })),
    t.done ? null : el('div', { style: 'margin:8px 0' }, bar(t.progress, t.progress + ' percent downloaded')),
    el('div', { class: 'small dim', text: facts.filter(Boolean).join(' · ') }),
    t.waiting_seed ? el('p', { class: 'small dim', style: 'margin:4px 0 0', text: 'Nobody is sharing this right now, so it is paused at the back of the line and the rest go ahead. '
      + 'It is checked every half hour and carries on by itself when someone shares it. Continue starts it now.' }) : null,
    canChange || d.can_delete ? el('div', { class: 'row', style: 'gap:6px;margin-top:8px' },
      !canChange ? null : t.paused ? b('Continue', 'resume', 'Continue') : b('Pause', 'pause', 'Pause'),
      t.done || !canChange ? null : [b('⤒ First', 'top', 'Move to the front of the line:'), b('↑', 'up', 'Move up:'), b('↓', 'down', 'Move down:'), b('⤓ Last', 'bottom', 'Move to the back of the line:')],
      d.can_delete ? el('button', { class: 'btn sm danger', type: 'button', text: 'Delete', 'aria-label': 'Delete ' + t.title + ' everywhere', onclick: async (ev) => {
        const show = t.kind === 'show' ? t.title.split(' · ')[0] : '';
        const what = t.kind === 'film' ? 'Delete "' + t.title + '" everywhere: the download, its files, and the film from the server and Jellyfin.'
          : show ? 'Delete the whole show "' + show + '" everywhere: this download, every episode already on the server, and the show from Jellyfin. It will not be looked for again.'
          : t.type === 'Book' ? 'Delete the download "' + t.title + '" and its files. A book already added to the library stays there; delete it from its page in Books.'
          : 'Delete "' + t.title + '" and its downloaded files.';
        if (!window.confirm(what + ' This cannot be undone.')) return;
        const btn = ev.currentTarget;
        btn.disabled = true;
        try { const r = await api('DELETE', '/api/media/torrents/' + t.hash); toast(r.message); } catch (e) { if (!(e instanceof Stale)) oops(e); }
        btn.blur();  // the list is left alone while focus is in it
        reload();
      } }) : null) : null);
}

// The Request button, and for a show, which seasons to get.
function requestControls(kind, r) {
  const box = el('div', { class: 'stack', style: 'gap:6px' });
  let mode = 'all';
  const picked = new Set();
  const seasons = el('div', { class: 'row', style: 'gap:6px', hidden: true }, (r.seasons || []).map((s) =>
    el('button', { class: 'chip xs', type: 'button', 'aria-pressed': 'false', text: 'S' + s.number, 'aria-label': 'Season ' + s.number, onclick: (e) => {
      if (picked.has(s.number)) picked.delete(s.number); else picked.add(s.number);
      e.target.setAttribute('aria-pressed', String(picked.has(s.number)));
    } })));
  const send = el('button', { class: 'btn sm pri', type: 'button', text: 'Request', 'aria-label': 'Request ' + r.title, onclick: async () => {
    send.disabled = true;
    try {
      const added = await api('POST', '/api/media/request', { kind, id: kind === 'film' ? r.tmdb : r.tvdb, seasons: mode, picked: Array.from(picked) });
      if (added && added.id && !(added.already && added.downloaded)) Tracker.follow(kind, added.id);
      fill(box, el('div', { class: 'small ok', role: 'status' }, added && added.already ? 'Already on the list. ' : 'Requested. ',
        added && added.id ? el('a', { href: '#/movies/' + kind + '/' + added.id, text: added.already ? 'See where it is up to' : 'Watch the search happen' }) : null));
    } catch (e) { send.disabled = false; oops(e); }
  } });
  if (kind === 'show') {
    box.append(el('div', { class: 'row', role: 'group', 'aria-label': 'Which seasons', style: 'gap:6px' }, [['all', 'Whole series'], ['latest', 'Latest season'], ['pick', 'Pick seasons']].map(([k, label]) =>
      el('button', { class: 'chip xs', type: 'button', 'aria-pressed': String(k === mode), text: label, onclick: (e) => {
        mode = k; e.target.parentNode.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === e.target))); seasons.hidden = k !== 'pick';
      } }))), seasons);
  }
  box.append(send);
  return box;
}

// Admin, Hub: whether the film and TV services answer, and the button that connects them.
function mediaCard(reload) {
  const body = el('div', { class: 'stack' }, el('p', { class: 'small dim', role: 'status', text: 'Checking the services…' }));
  const results = el('div', { class: 'list' });
  async function draw() {
    const s = await api('GET', '/api/admin/media/status');
    const line = (item) => el('div', { class: 'row between' }, el('span', { text: item.label }),
      !item.configured ? el('span', { class: 'dim', text: 'Not set up' }) : el('span', { class: item.ok ? 'ok' : 'warn', text: item.ok ? 'Connected' + (item.version ? ' · ' + item.version : '') : 'Not reachable · ' + (item.error || '') }));
    fill(body, el('div', { class: 'list' }, line(s.radarr), line(s.sonarr), line(s.bazarr), line(s.jellyfin),
      el('div', { class: 'row between' }, el('span', { text: 'VPN for torrents' }), el('span', { class: s.vpn.ok ? 'ok' : 'warn', text: s.vpn.message }))));
  }
  const go = el('button', { class: 'btn pri', type: 'button', text: 'Connect everything', onclick: async () => {
    go.disabled = true; go.textContent = 'Connecting…';
    try {
      const r = await api('POST', '/api/admin/media/connect');
      fill(results, ...r.steps.map((x) => el('div', { class: 'row between' }, el('span', { text: x.step }), el('span', { class: 'small ' + (x.ok ? 'ok' : 'warn'), text: x.message }))));
      draw().catch(oops);
    } catch (e) { oops(e); }
    go.disabled = false; go.textContent = 'Connect everything';
  } });
  draw().catch((e) => fill(body, el('p', { class: 'small bad', text: e.message })));
  return el('section', { class: 'panel cut' }, head('Films and TV', el('span', { class: 'small dim', text: 'Radarr, Sonarr, Bazarr, Jellyfin and the VPN' })),
    body,
    el('p', { class: 'small dim', text: 'Connect everything links Radarr and Sonarr to Prowlarr and qBittorrent, gives them their folders and the household quality (2160p first, then 1080p, then 720p, then the best there is, never remux), sets up English subtitles in Bazarr, and adds the libraries to a Jellyfin started from this project. Anything already in place is left alone, so it is safe to press again.' }),
    el('div', { class: 'row' }, go), results);
}

// ------------------------------------------------------------------ home

// The Scope, the design's showpiece on Home: today's visual (or the one an admin pinned), with its name and,
// for a visual that has them, a switch for its modes in the header.
function setPiece(look) {
  const name = el('span', { text: '' });
  const modes = el('span', { class: 'seg', role: 'group', hidden: true });
  let frame = null;
  frame = visualFrame(look.scene === 'daily' ? '' : look.scene, (v) => {
    name.textContent = v.name || '';
    const list = Array.isArray(v.modes) ? v.modes : [];
    modes.hidden = !list.length;
    modes.setAttribute('aria-label', (v.name || 'Visual') + ' view');
    fill(modes, list.map(([id, label]) => el('button', { type: 'button', 'aria-pressed': String(id === v.mode), text: label,
      onclick: () => frame.contentWindow.postMessage({ type: 'hub-mode', mode: id }, '*') })));
  });
  return el('div', { class: 'dossier holo' },
    el('div', { class: 'cell dhead' }, el('span', { text: look.piece.title }), el('span', { class: 'dim', text: look.piece.state })),
    el('div', { class: 'cell dhead' }, name, modes),
    el('div', { class: 'cell', style: 'grid-column:1/-1;line-height:0' }, frame));
}

async function pageHome() {
  const home = await api('GET', '/api/home');
  const latestMedia = el('section', { class: 'panel cut' }, head('Latest movies and shows', el('a', { class: 'btn sm', href: '#/movies', text: 'Open movies and TV' })),
    el('p', { class: 'small dim', role: 'status', text: S.media.jellyfin ? 'Asking Jellyfin…' : 'Films and series arrive here once Jellyfin is connected' + (isAdmin() ? ' under Admin.' : ' by an admin.') }));
  if (S.media.jellyfin && !isGuest()) {
    api('GET', '/api/media/latest').then((d) => latestMedia.replaceChildren(latestMedia.firstChild,
      d.items.length ? el('div', { class: 'reel' }, d.items.map((i) => posterCard(Object.assign({}, i, { id: null }),
        el('div', { class: 'small', text: niceDate(i.added) })))) : el('p', { class: 'small dim', text: 'No films or shows on the server yet.' })))
      .catch((e) => latestMedia.replaceChildren(latestMedia.firstChild, el('p', { class: 'small warn', text: 'Jellyfin: ' + e.message })));
  }
  const books = home.books.map((b) => el('div', { class: 'book' },
    el('a', { class: 'cover' + (b.has_cover ? ' has-img' : '') + titleSize(b.title), title: b.title, href: '#/book/' + b.id, style: '--cv:' + coverColor(b.title) + (b.has_cover ? ';background-image:url(' + coverUrl(b) + ')' : '') },
      el('span', { class: 'fmt', text: b.formats.includes('EPUB') ? 'EPUB' : (b.formats[0] || '') }), el('span', { class: 't', text: b.title })),
    el('div', { class: 'small dim', text: b.author }),
    el('div', { class: 'small', text: niceDate(b.added) })));
  setMain(
    el('div', { class: 'col', style: 'gap:8px' }, el('h1', { class: 'glow', text: 'Welcome home' }),
      el('p', { class: 'dim', text: 'Everything on the family server, newest first.' })),
    setPiece(S.look),
    isGuest() ? null : latestMedia,
    el('section', { class: 'panel cut' }, head('Latest books', el('span', { class: 'small dim', text: plural(home.books_month, 'book') + ' added this month' }), el('a', { class: 'btn sm', href: '#/library', text: 'Open books' })),
      books.length ? el('div', { class: 'reel' }, books) : el('p', { class: 'small dim', text: 'No books in the library yet.' })));
}

// A section that is not built yet. Says so plainly, with no made-up content.
function pageSoon() {
  const page = location.hash.replace(/^#\/?/, '').split('/')[0];
  const what = { movies: ['Movies and TV', 'Films and series on the family server, and requests for new ones.'],
    photos: ['Photos', 'The family photo albums, once the home server has a photo service.'],
    files: ['Files', 'Shared folders on the home server.'] }[page] || ['Coming soon', ''];
  setMain(el('div', { class: 'col', style: 'gap:8px' }, el('h1', { class: 'glow', text: what[0] }), el('p', { class: 'dim', text: what[1] })),
    el('section', { class: 'panel cut soon' }, head('Coming soon'),
      el('p', { text: 'This part of Family Hub is not built yet, so there is nothing here to set up or change.' })));
}

// ----------------------------------------------------------------- setup

function pageSetup() {
  const name = el('input', { type: 'text', autocomplete: 'off' });
  const pass = el('input', { type: 'password', autocomplete: 'new-password' });
  setMain(el('form', { class: 'panel cut narrow', onsubmit: async (e) => {
    e.preventDefault();
    try { await api('POST', '/api/setup', { name: name.value, passcode: pass.value }); goHome(); } catch (err) { oops(err); }
  } },
    el('h1', { class: 'glow', text: 'Welcome to Family Hub' }),
    el('p', { class: 'dim', text: 'Create the first profile. It will be an admin, so it can add the rest of the family afterwards.' }),
    field('Your name', name, 'wide'), field('Passcode (4 or more characters)', pass, 'wide'),
    el('button', { class: 'btn pri', type: 'submit', text: 'Create admin profile' })));
}

// -------------------------------------------------------------- profiles

const TONES = ['var(--accent)', 'var(--accent3)', 'var(--accent2)'];
function pageProfiles() {
  const panel = el('div', { class: 'narrow' });
  let chosen = null;
  async function enter(body) {
    try { await api('POST', '/api/login', body); goHome(); return true; } catch (e) { oops(e); return false; }
  }
  function ask(p, door) {
    if (chosen) chosen.setAttribute('aria-pressed', 'false');
    chosen = door; door.setAttribute('aria-pressed', 'true');
    const pass = el('input', { type: 'password', autocomplete: 'off', style: 'font:400 30px/1 var(--font-num);letter-spacing:.5em;min-height:52px' });
    const admins = S.profiles.filter((a) => a.role === 'admin' && a.id !== p.id);
    const adminSel = el('select', {}, admins.map((a) => el('option', { value: a.id, text: a.name })));
    const adminPass = el('input', { type: 'password', autocomplete: 'off' });
    const override = el('div', { class: 'stack', hidden: true },
      el('p', { class: 'small dim', text: 'An admin can open this profile with their own passcode.' }),
      el('div', { class: 'row end' }, field('Admin', adminSel), field('Admin passcode', adminPass),
        el('button', { class: 'btn', type: 'button', text: 'Open as admin', onclick: async () => { if (!await enter({ profile_id: p.id, admin_id: Number(adminSel.value), admin_passcode: adminPass.value })) adminPass.select(); } })));
    // Cancel (or Escape) closes the box and puts focus back on the door that opened it.
    const cancel = () => { door.setAttribute('aria-pressed', 'false'); chosen = null; fill(panel); door.focus(); };
    fill(panel, el('form', { class: 'panel cut focus', 'aria-label': 'Enter passcode for ' + p.name,
      onkeydown: (e) => { if (e.key === 'Escape') { e.preventDefault(); cancel(); } },
      // A wrong passcode stays selected, so typing again replaces it.
      onsubmit: async (e) => { e.preventDefault(); if (!await enter({ profile_id: p.id, passcode: pass.value })) pass.select(); } },
      head('Passcode for ' + p.name, el('span', { class: 'small dim', text: 'Locked' })),
      field('Passcode', pass, 'wide'),
      el('div', { class: 'row' },
        el('button', { class: 'btn pri', type: 'submit', style: 'flex:1 1 140px', text: 'Unlock' }),
        el('button', { class: 'btn', type: 'button', style: 'flex:1 1 100px', text: 'Cancel', onclick: cancel })),
      admins.length ? el('div', { class: 'line between' }, el('span', { class: 'small dim grow', text: 'Forgotten it? An admin can open this profile or reset the passcode.' }),
        el('button', { type: 'button', class: 'btn line2', text: 'Admin override', onclick: () => { override.hidden = false; adminPass.focus(); } })) : null,
      override));
    pass.focus();
  }
  const doors = S.profiles.map((p, i) => {
    const door = el('button', { class: 'door', type: 'button', 'aria-pressed': 'false', style: '--tone:' + TONES[i % TONES.length] },
      el('span', { class: 'glass glow', 'aria-hidden': 'true', text: p.name[0].toUpperCase() }),
      el('span', { class: 'name', text: p.name }),
      el('span', { class: 'tag' + (p.role === 'admin' ? ' on' : ''), text: p.role === 'admin' ? 'Admin' : 'Member' }),
      el('span', { class: 'small dim', text: p.has_passcode ? 'Passcode on' : 'No passcode' }),
      // Admins can always find new books, whatever their own switch says, so they are never "Reading only".
      p.tracker_linked ? el('span', { class: 'small dim', text: 'Kindle tracking linked' }) : (p.can_request || p.role === 'admin' ? null : el('span', { class: 'small dim', text: 'Reading only' })));
    door.addEventListener('click', () => (p.has_passcode ? ask(p, door) : enter({ profile_id: p.id })));
    return door;
  });
  if (S.guest_enabled) doors.push(el('button', { class: 'door', type: 'button', style: '--tone:var(--dim)', onclick: () => enter({ guest: true }) },
    el('span', { class: 'glass', 'aria-hidden': 'true' }, icon('M8 8a4 4 0 1 0 8 0a4 4 0 1 0 -8 0M4 21c0-4 3.6-7 8-7s8 3 8 7')), el('span', { class: 'name', text: 'Guest' }), el('span', { class: 'tag', text: 'No passcode' }),
    el('span', { class: 'small dim', text: 'Browse and download books' }), el('span', { class: 'small dim', text: 'Nothing is tracked' })));
  if (isAdmin()) doors.push(el('a', { class: 'door add', href: '#/admin' }, el('span', { style: 'font:400 64px/1 var(--font-num)', 'aria-hidden': 'true', text: '+' }),
    el('span', { class: 'name', text: 'Add profile' }), el('span', { class: 'small', text: 'Admins only' })));

  setMain(
    el('div', { class: 'col center', style: 'gap:14px' }, el('h1', { class: 'glow', text: "Who's here?" }),
      el('div', { class: 'rule', style: 'width:min(100%,420px)' }, starMark()),
      el('p', { class: 'dim', style: 'max-width:60ch', text: 'Everyone shares the family server. Each profile keeps its own reading, ratings, devices and requests.' })),
    el('div', { class: 'doors' }, doors), panel,
    S.me && !isGuest() ? mySettings() : null,
    S.me ? el('div', { class: 'row', style: 'justify-content:center' },
      el('button', { class: 'btn', type: 'button', text: 'Sign out of ' + S.me.name, onclick: async () => { try { await api('POST', '/api/logout'); route(); } catch (e) { oops(e); } } })) : null);
}

function mySettings() {
  const save = (body, done) => api('PATCH', '/api/profiles/' + S.me.id, body).then(() => { toast(done); route(); }).catch(oops);
  const pass = el('input', { type: 'password', autocomplete: 'new-password' });
  const sync = el('input', { type: 'password', autocomplete: 'new-password' });
  const token = el('input', { type: 'password', autocomplete: 'off', placeholder: S.me.tracker_linked ? 'Saved. Paste a new key to replace it' : '' });
  const syncUrl = location.origin + '/kosync';
  return el('section', { class: 'panel cut' },
    head('Settings for ' + S.me.name),
    el('div', { class: 'list' },
      el('div', { class: 'stack' }, el('h3', { text: 'Passcode' }),
        el('div', { class: 'row end' }, field('New passcode (leave empty to remove)', pass),
          // An empty box removes the passcode, so say so before doing it rather than after.
          el('button', { class: 'btn', type: 'button', text: 'Save passcode', onclick: () => {
            if (!pass.value && !window.confirm('Remove your passcode? Anyone using this hub could then open your profile.')) return;
            save({ passcode: pass.value }, pass.value ? 'Passcode saved.' : 'Passcode removed.');
          } }))),
      el('div', { class: 'stack' }, el('h3', { text: 'Reading app sync' }),
        el('p', { class: 'small dim', text: 'One password signs your reading apps in to your book catalogue and to progress sync. In Readest or KOReader choose a custom KOReader sync server and enter: server ' + syncUrl + ' · user name ' + S.me.name + ' · the sync password you set here.' }),
        el('p', { class: 'small ' + (S.me.sync_ready ? 'ok' : 'dim'), text: S.me.sync_ready ? 'A sync password is set.' : 'No sync password yet.' }),
        el('div', { class: 'row end' }, field('Sync password', sync),
          el('button', { class: 'btn', type: 'button', text: 'Save sync password', onclick: () => save({ sync_password: sync.value }, 'Sync password saved.') }))),
      devicesBox(S.me.id),
      goodreadsBox(S.me.id),
      el('div', { class: 'stack' }, el('h3', { text: 'Kindle and other reading (experimental)' }),
        el('p', { class: 'small dim', text: 'Paste a Hardcover API key to pull in books marked as reading or read there, such as Kindle books passed along from Goodreads.' }),
        el('div', { class: 'row end' }, field('Hardcover API key', token),
          el('button', { class: 'btn', type: 'button', text: 'Save key', onclick: () => save({ hardcover_token: token.value }, 'Key saved.') }),
          S.me.tracker_linked ? el('button', { class: 'btn', type: 'button', text: 'Remove key', onclick: () => save({ hardcover_token: '' }, 'Key removed.') }) : null))));
}

// A profile's Goodreads link. The link itself is only ever shown to its owner.
function goodreadsBox(profileId) {
  const box = el('div', { class: 'stack' }, el('h3', { text: 'Goodreads' }), el('p', { class: 'small dim', text: 'Loading…' }));
  const url = '/api/profiles/' + profileId + '/goodreads';
  let polls = 0;
  function show(g) {
    const input = el('input', { type: 'url', value: g.url, autocomplete: 'off', placeholder: g.linked && !g.mine ? 'Saved. Paste a new link to replace it' : 'https://www.goodreads.com/user/show/…' });
    const file = el('input', { type: 'file', accept: '.csv,text/csv' });
    const send = (body, msg) => api('POST', url, body).then((r) => { if (msg) toast(msg); polls = 0; show(r); }).catch(oops);
    let state = 'Not linked.';
    if (g.busy) state = 'Reading the shelves from Goodreads… this can take a minute for a long list.';
    else if (g.linked && g.synced) state = 'Linked. Last read ' + ago(g.synced).toLowerCase() + ' · ' + plural(g.books, 'book') + ' taken from Goodreads. Checked again every six hours.';
    else if (g.linked) state = 'Linked, not read yet.';
    else if (g.books) state = 'Not linked. ' + plural(g.books, 'book') + ' came from an export file.';
    fill(box, el('h3', { text: 'Goodreads' }),
      el('p', { class: 'small dim', text: 'Paste the address of ' + (g.mine ? 'your' : 'their') + ' Goodreads profile. Family Hub reads the Read and Currently reading shelves into ' + (g.mine ? 'your' : 'their') + ' reading history, with ratings. Books the library does not have are listed for the family as "Available on Kindle", without saying whose they are. Only ' + (g.mine ? 'you see' : 'they see') + ' this link.' }),
      el('p', { class: 'small ' + (g.linked && !g.note ? 'ok' : 'dim'), role: 'status', text: state }),
      g.note && !g.busy ? el('p', { class: 'small warn', text: g.note }) : null,
      el('div', { class: 'row end' }, field('Goodreads profile link', input),
        el('button', { class: 'btn', type: 'button', text: 'Save link', onclick: () => send({ link: input.value }, input.value.trim() ? 'Link saved. Reading the shelves now.' : 'Link removed.') }),
        g.linked ? el('button', { class: 'btn', type: 'button', disabled: g.busy, text: 'Read shelves now', onclick: () => send({ sync: true }, 'Reading the shelves now.') }) : null,
        g.linked ? el('button', { class: 'btn', type: 'button', text: 'Remove link', onclick: () => { if (window.confirm('Remove the Goodreads link? Reading history already taken from it stays; the "Available on Kindle" books from it are no longer listed.')) send({ link: '' }, 'Link removed.'); } }) : null),
      el('div', { class: 'row end' }, field('Or import a Goodreads export file (.csv)', file),
        el('button', { class: 'btn', type: 'button', text: 'Import file', onclick: async () => {
          if (!file.files[0]) return toast('Choose the export file first.', true);
          const fd = new FormData(); fd.append('file', file.files[0]);
          try { const r = await api('POST', url + '/import', fd); toast(plural(r.books, 'book') + ' read from the file, ' + r.changed + ' new or changed.'); draw(); } catch (e) { oops(e); }
        } })),
      el('p', { class: 'small dim', text: 'The link only works while the Goodreads profile is public. For a private profile, use the export file: on Goodreads open My Books, Import and export, Export library.' }));
    if (g.busy && polls++ < 40) setTimeout(() => { if (box.isConnected) draw(); }, 3000);
  }
  const draw = () => api('GET', url).then(show).catch((e) => fill(box, el('h3', { text: 'Goodreads' }), el('p', { class: 'small bad', text: e.message })));
  draw();
  return box;
}

// Devices and reading apps for one profile. Shown in your own settings, and to admins for anyone.
function devicesBox(profileId) {
  const box = el('div', { class: 'stack' }, el('h3', { text: 'Devices and reading apps' }), el('p', { class: 'small dim', text: 'Loading…' }));
  const mine = profileId === S.me.id;
  let editing = null;
  async function draw() {
    const d = await api('GET', '/api/profiles/' + profileId + '/devices');
    const who = d.profile.name;
    const change = (id, body, msg) => api('PATCH', '/api/devices/' + id, body).then(() => { toast(msg); draw(); }).catch(oops);
    const help = (dev) => {
      if (dev.kind === 'kindle') {
        return [el('span', { class: 'small dim', text: 'Books are emailed to ' + dev.address + ', up to ' + d.limit_mb + ' MB each. Comics cannot be sent.' }),
          d.mail_ready ? el('span', { class: 'small dim', text: 'On Amazon, add ' + d.sender + ' as an approved sender for this Kindle: Manage Your Content and Devices → Preferences → Personal Document Settings.' })
            : el('span', { class: 'small warn', text: 'Sending by email is not set up yet. An admin can add it under Admin → Hub → Connections.' })];
      }
      if (dev.kind === 'readest' || dev.kind === 'koreader') {
        return [el('span', { class: 'small dim', text: 'In the app, add a catalogue (OPDS) with address ' + location.origin + '/opds · user name ' + who + ' · the sync password. "My books" there lists the books chosen for ' + (mine ? 'you' : who) + '.' }),
          el('span', { class: 'small dim', text: 'Reading position: KOReader sync server ' + location.origin + '/kosync, same name and password.' }),
          d.profile.sync_ready ? null : el('span', { class: 'small warn', text: mine ? 'Set a sync password above first.' : who + ' has no sync password yet. It is set in their own settings.' })];
      }
      if (dev.kind === 'kobo') return [el('span', { class: 'small dim', text: "Open Family Hub in the Kobo's browser and download books from there. With KOReader installed on the Kobo, add it as a KOReader device instead to get the catalogue and progress sync." })];
      return [];
    };
    const kindName = Object.fromEntries(S.device_kinds);
    const list = d.devices.length ? el('div', { class: 'list' }, d.devices.map((dev) => el('div', { class: 'stack', style: 'gap:6px' },
      el('div', { class: 'row between' },
        el('div', { class: 'row' }, el('strong', { text: dev.name }), el('span', { class: 'tag', text: kindName[dev.kind] || dev.kind })),
        el('div', { class: 'row' },
          dev.kind === 'kindle' ? el('button', { class: 'chip', type: 'button', 'aria-pressed': String(dev.auto_send), 'aria-label': 'Send new books to ' + dev.name + ' automatically', text: dev.auto_send ? 'Sends automatically' : 'Send automatically: off', onclick: () => change(dev.id, { auto_send: !dev.auto_send }, dev.auto_send ? 'Books will only be sent when you press Send.' : 'Books added to My books will be sent straight away.') }) : null,
          dev.link ? el('a', { class: 'btn', href: dev.link, target: '_blank', rel: 'noopener', text: 'Open' }) : null,
          el('button', { class: 'btn', type: 'button', text: 'Change', 'aria-label': 'Change ' + dev.name, onclick: () => { editing = dev; draw(); } }),
          el('button', { class: 'btn danger', type: 'button', text: 'Remove', 'aria-label': 'Remove ' + dev.name, onclick: async () => {
            if (!window.confirm('Remove ' + dev.name + ' from the list? Books already on it stay on it.')) return;
            try { await api('DELETE', '/api/devices/' + dev.id); toast('Device removed.'); draw(); } catch (e) { oops(e); }
          } }))),
      help(dev)))) : el('p', { class: 'small dim', text: 'No devices yet.' });

    const kind = el('select', {}, S.device_kinds.map(([k, label]) => el('option', { value: k, text: label, selected: editing ? editing.kind === k : k === 'kindle' })));
    const name = el('input', { type: 'text', maxlength: 60, value: editing ? editing.name : '', placeholder: "Nan's Kindle" });
    const address = el('input', { type: 'email', value: editing ? editing.address : '', placeholder: 'name@kindle.com' });
    const link = el('input', { type: 'url', value: editing ? editing.custom_link : '', placeholder: 'https://' });
    const auto = el('input', { type: 'checkbox', checked: editing ? editing.auto_send : true });
    const addressField = field('Send-to-Kindle email address', address);
    const autoField = el('label', { class: 'check' }, auto, 'Send books automatically when they are added to My books');
    const showKindle = () => { addressField.hidden = autoField.hidden = kind.value !== 'kindle'; };
    kind.addEventListener('change', showKindle);
    showKindle();
    const form = el('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      const body = { kind: kind.value, name: name.value, address: address.value, link: link.value, auto_send: auto.checked };
      try {
        if (editing) await api('PATCH', '/api/devices/' + editing.id, body); else await api('POST', '/api/profiles/' + profileId + '/devices', body);
        toast(editing ? 'Device saved.' : 'Device added.'); editing = null; draw();
      } catch (err) { oops(err); }
    } },
      el('strong', { text: editing ? 'Change ' + editing.name : 'Add a device or app' }),
      el('div', { class: 'row end' }, field('Kind', kind), field('Name', name), addressField, field('Link to open it (optional)', link)),
      autoField,
      el('div', { class: 'row' }, el('button', { class: 'btn pri', type: 'submit', text: editing ? 'Save changes' : 'Add device' }),
        editing ? el('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: () => { editing = null; draw(); } }) : null));
    fill(box, el('h3', { text: 'Devices and reading apps' }),
      el('p', { class: 'small dim', text: 'Where ' + (mine ? 'you read' : who + ' reads') + '. Reading apps fetch books from a private catalogue. A Kindle is sent books by email. Only ' + (mine ? 'you' : who) + ' and admins can see this list.' }),
      list, form);
  }
  draw().catch((e) => fill(box, el('p', { class: 'bad', text: e.message })));
  return box;
}

async function pageDevices(id) {
  const profile = S.profiles.find((p) => p.id === Number(id));
  if (!profile || (!isAdmin() && profile.id !== S.me.id)) { location.hash = '#/profiles'; return; }
  setMain(back(isAdmin() ? '#/admin' : '#/profiles', isAdmin() ? 'Back to admin' : 'Back to profiles'),
    el('h1', { class: 'glow', text: 'Devices for ' + profile.name }),
    el('section', { class: 'panel cut' }, devicesBox(profile.id)),
    el('section', { class: 'panel cut' }, goodreadsBox(profile.id)));
}

// --------------------------------------------------------------- library

function coverUrl(b) { return '/api/books/' + b.id + '/cover' + (b.cover_v ? '?v=' + encodeURIComponent(b.cover_v) : ''); }

// Choosing a different cover for a book. The picture is kept in Family Hub; the library is not changed.
function coverBox(b, again) {
  const panel = el('div', { class: 'stack', style: 'gap:8px', hidden: true });
  const url = '/api/books/' + b.id + '/cover';
  const done = (msg) => { toast(msg); again(); };
  const file = el('input', { type: 'file', accept: 'image/*', 'aria-label': 'Picture to use as the cover' });
  const found = el('div', { class: 'row', role: 'status' });
  const look = el('button', { class: 'btn', type: 'button', text: 'Look for covers', onclick: async () => {
    look.disabled = true; fill(found, el('span', { class: 'small dim', text: 'Asking Open Library…' }));
    try {
      const r = await api('GET', url + '/options');
      fill(found, ...(r.options.length ? r.options.map((o, i) => el('button', { class: 'btn', type: 'button', style: 'padding:2px', 'aria-label': 'Use cover ' + (i + 1) + ': ' + o.label, title: o.label,
        onclick: () => api('POST', url, { url: o.url }).then(() => done('Cover changed.')).catch(oops) },
        el('img', { src: '/api/covers/preview?' + qs({ url: o.url.replace('-L.jpg', '-M.jpg') }), alt: '', loading: 'lazy', style: 'width:86px;height:129px;object-fit:cover;border-radius:4px;display:block' })))
        : [el('span', { class: 'small dim', text: 'Open Library has no cover for a book with this title and author.' })]));
    } catch (e) { fill(found, el('span', { class: 'small bad', text: e.message })); }
    look.disabled = false;
  } });
  panel.append(
    el('p', { class: 'small dim', text: 'The new cover is kept in Family Hub. The library and the book file are not changed.' }),
    el('div', { class: 'row end' }, file, el('button', { class: 'btn', type: 'button', text: 'Use this picture', onclick: async () => {
      if (!file.files[0]) return toast('Choose a picture first.', true);
      const fd = new FormData(); fd.append('file', file.files[0]);
      try { await api('POST', url, fd); done('Cover changed.'); } catch (e) { oops(e); }
    } })),
    el('div', { class: 'row' }, look,
      el('button', { class: 'btn', type: 'button', text: 'Make a title cover', onclick: () => api('POST', url, { generated: true }).then(() => done('Cover made from the title.')).catch(oops) }),
      b.own_cover ? el('button', { class: 'btn', type: 'button', text: "Back to the library's cover", onclick: () => api('DELETE', url).then(() => done("Back to the library's cover.")).catch(oops) }) : null),
    found);
  const toggle = el('button', { class: 'btn', type: 'button', 'aria-expanded': 'false', text: 'Change cover', onclick: () => { panel.hidden = !panel.hidden; toggle.setAttribute('aria-expanded', String(!panel.hidden)); } });
  return el('div', { class: 'stack', style: 'gap:8px' }, toggle, panel);
}

// What to type into the book search for a book the library lacks.
function findQuery(b) { return (b.title.split(':')[0] + ' ' + b.author.split(/[&,]/)[0]).replace(/\s+/g, ' ').trim(); }
function goodreadsLink(b) {
  return el('a', { class: 'small', href: 'https://www.goodreads.com/book/show/' + encodeURIComponent(b.goodreads_id), target: '_blank', rel: 'noopener', 'aria-label': 'View ' + b.title + ' on Goodreads', text: 'View on Goodreads' });
}

// The rating on a card: this person's own, and what readers at large gave it.
function readersText(r) { return r.rating.toFixed(1) + ' of 5 on ' + r.source + (r.count ? ' (' + plural(r.count, 'rating') + ')' : ''); }
function ratingLine(b) {
  const mine = !isGuest() && b.rating ? 'My rating: ' + b.rating + ' of 5' : '';
  const theirs = b.readers ? 'Readers: ' + readersText(b.readers) : '';
  if (!mine && !theirs) return null;
  return el('div', { class: 'small' }, mine ? el('div', { text: mine }) : null, theirs ? el('div', { class: 'dim', text: '★ ' + theirs }) : null);
}

function bookCard(b, onChange, opts) {
  const fmt = !b.in_library ? (b.kindle ? 'KINDLE' : 'ELSEWHERE') : (b.formats.includes('EPUB') ? 'EPUB' : (b.formats[0] || ''));
  const coverProps = { class: 'cover' + (b.has_cover ? ' has-img' : '') + titleSize(b.title), title: b.title,
    style: '--cv:' + coverColor(b.title) + (b.has_cover ? ';background-image:url(' + coverUrl(b) + ')' : '') };
  const inner = [el('span', { class: 'fmt', text: fmt }), el('span', { class: 't', text: b.title })];
  const picture = b.in_library ? el('a', Object.assign({ href: '#/book/' + b.id }, coverProps), inner) : el('div', coverProps, inner);
  // A book this person has finished is shown faded, with a note across it.
  const done = b.status === 'finished' && !isGuest() && !(opts && opts.plain);
  const under = b.status === 'reading' && !isGuest() && !(opts && opts.plain);
  const cover = el('div', { class: 'coverwrap' + (done ? ' done' : '') }, picture,
    done ? el('span', { class: 'readnote', text: 'Already read' }) : null,
    under ? el('span', { class: 'readnote going', text: 'Reading' + (b.percent ? ', ' + Math.round(b.percent) + '%' : '') }) : null);
  const genre = (b.kind === 'Unsorted' ? [] : [b.kind]).concat(Array.from(new Set(b.tags.epub.concat(b.tags.community))).slice(0, 2)).join(' · ');
  let status = 'Not started';
  if (b.status === 'reading') status = Math.round(b.percent) + '%' + (b.device ? ' · on ' + b.device : '') + (b.in_library ? '' : ' · reading');
  if (b.status === 'finished') status = 'Finished ' + niceDate(b.finished);
  let action = el('div', { class: 'small dim', text: 'Read outside the library' });
  if (b.kindle) {
    action = el('div', { class: 'stack', style: 'gap:6px' }, el('strong', { class: 'small', text: 'On Kindle only' }),
      S.me.can_request || isAdmin() ? el('a', { class: 'small', href: '#/discover/' + encodeURIComponent(findQuery(b)), 'aria-label': 'Find the EPUB of ' + b.title, text: 'Find the EPUB' }) : null);
  }
  if (b.in_library) {
    const f = b.formats.includes('EPUB') ? 'EPUB' : b.formats[0];
    action = f ? el('a', { class: 'btn sm', href: '/api/books/' + b.id + '/download/' + f, 'aria-label': 'Download ' + f + ': ' + b.title, text: 'Download ' + f })
      : el('div', { class: 'small dim', text: 'No file in the library' });
  }
  return el('div', { class: 'book' }, cover,
    el('div', { class: 'small dim', text: b.author }),
    genre ? el('div', { class: 'lab', text: genre }) : null,
    b.why && b.why.length ? el('div', { class: 'small', text: 'Because: ' + b.why.join(', ') }) : null,
    isGuest() ? null : el('div', { class: 'small', text: status }),
    ratingLine(b),
    action,
    b.goodreads_id ? goodreadsLink(b) : null,
    b.in_library && !isGuest() ? (b.on_shelf ? el('div', { class: 'small ok', text: 'Already in my library' })
      : el('button', { class: 'chip xs', type: 'button', 'aria-label': 'Add to my books: ' + b.title, text: 'Add to my books', onclick: () => setShelf(b.id, true).then(onChange).catch(oops) })) : null);
}

// Volumes of one series in the library are shown as a single card, where the first one would have been.
// Comics gather by franchise (every Star Trek run together); other books by their series.
function groupSeries(books) {
  const keyOf = (b) => (b.in_library && (b.collection || b.series) ? (b.collection || b.series).toLowerCase() : null);
  const by = {};
  for (const b of books) { const k = keyOf(b); if (k) (by[k] = by[k] || []).push(b); }
  const out = [], done = new Set();
  for (const b of books) {
    const k = keyOf(b);
    if (!k || by[k].length < 2) { out.push(b); continue; }
    if (done.has(k)) continue;
    done.add(k);
    const volumes = by[k].slice().sort((a, c) => (a.series || '').localeCompare(c.series || '') || (a.series_index || 0) - (c.series_index || 0));
    out.push({ series: b.collection || b.series, volumes, runs: new Set(volumes.map((v) => v.series || v.title)).size });
  }
  return out;
}

function seriesStack(x) {
  const first = x.volumes[0], href = '#/shelf/' + encodeURIComponent(x.series);
  const word = partWord(x.volumes);
  const read = x.volumes.filter((v) => v.status === 'finished').length;
  const going = x.volumes.find((v) => v.status === 'reading');
  const coverProps = { href, class: 'cover' + (first.has_cover ? ' has-img' : '') + titleSize(x.series), title: x.series, 'aria-label': x.series + ', ' + plural(x.volumes.length, word),
    style: '--cv:' + coverColor(x.series) + (first.has_cover ? ';background-image:url(' + coverUrl(first) + ')' : '') };
  return el('div', { class: 'book' },
    el('div', { class: 'coverwrap stack-of' }, el('a', coverProps, el('span', { class: 'fmt', text: plural(x.volumes.length, word) }),
      el('span', { class: 't', text: first.has_cover ? '' : x.series }))),
    el('a', { class: 'small', href, text: x.series }),
    el('div', { class: 'small dim', text: [plural(x.volumes.length, word), x.runs > 1 ? plural(x.runs, 'run') : first.author].filter(Boolean).join(' · ') }),
    isGuest() ? null : el('div', { class: 'small', text: going ? 'Reading ' + (going.part || 'one') : read ? read + ' of ' + x.volumes.length + ' read' : 'Not started' }));
}

// "issue", "chapter" or "volume": what most parts of a series call themselves.
function partWord(volumes) {
  const count = {};
  for (const v of volumes) { const w = (v.part || 'Volume').split(' ')[0].toLowerCase(); count[w] = (count[w] || 0) + 1; }
  const top = Object.keys(count).sort((a, b) => count[b] - count[a])[0];
  return ['issue', 'chapter', 'volume'].includes(top) ? top : 'volume';
}

// Every volume of one series, in order.
async function pageShelf(name) {
  const set = decodeURIComponent(name || '');
  // Inside a series a part is called by its own title when it has one, otherwise "Volume 3", "Issue 12" and so on.
  const asPart = (b) => Object.assign({}, b, { title: b.subtitle || b.part || b.title, author: b.subtitle ? b.part : b.part ? '' : b.author });
  const body = el('div', { class: 'stack', style: 'gap:28px' });
  const wholeFile = (series, books, word) => {
    const comics = books.every((b) => b.formats.some((f) => f === 'CBZ' || f === 'CBR'));
    return el('a', { class: 'btn sm', href: '/api/books/series-file?' + qs({ series }), download: '',
      title: comics ? 'Every ' + word + ' joined into one comic file, in order' : 'Every ' + word + ' in one ZIP file',
      text: comics ? 'Download the whole run as one comic (.cbz)' : 'Download all (.zip)' });
  };
  const load = async () => {
    // A set made of several runs (Star Trek: Gold Key, Deep Space Nine, ...) or a plain series.
    let d = await api('GET', '/api/books?' + qs({ collection: set }));
    if (!d.total) d = await api('GET', '/api/books?' + qs({ series: set }));
    const runs = [];
    for (const b of d.books) {
      const k = b.series || b.title;
      let run = runs.find((r) => r.name === k);
      if (!run) runs.push(run = { name: k, books: [] });
      run.books.push(b);
    }
    runs.sort((a, b) => b.books.length - a.books.length || a.name.localeCompare(b.name));
    fill(body, runs.map((run) => {
      const word = partWord(run.books);
      return el('section', { class: 'stack' },
        runs.length > 1 ? el('div', { class: 'row between', style: 'gap:10px' },
          el('h2', { text: run.name }), el('div', { class: 'row', style: 'gap:10px' }, el('span', { class: 'small dim', text: plural(run.books.length, word) }),
            run.books.length > 1 && run.books[0].series ? wholeFile(run.books[0].series, run.books, word) : null)) : null,
        el('div', { class: 'shelf' }, run.books.map((b) => bookCard(asPart(b), () => load().catch(oops)))));
    }));
    return { d, runs };
  };
  const { d, runs } = await load();
  const word = runs.length > 1 ? 'issue' : partWord(d.books);
  const author = runs.length > 1 ? '' : (d.books.find((b) => b.author && b.author !== 'Unknown') || {}).author;
  const single = runs.length === 1 && d.total > 1 && d.books[0].series;
  setMain(back('#/library', 'Back to the library'),
    el('div', { class: 'col', style: 'gap:8px' }, el('h1', { class: 'glow', text: set }),
      el('p', { class: 'dim', text: [plural(d.total, runs.length > 1 ? 'comic' : word), runs.length > 1 ? plural(runs.length, 'run') : '', author].filter(Boolean).join(' · ') })),
    single ? el('section', { class: 'panel cut' }, head('The whole series in one file'),
      el('p', { class: 'small dim', text: 'Every ' + word + ' in one file, in order. Each ' + word + ' can still be downloaded on its own below. Putting it together can take a minute for a long series; the download starts by itself.' }),
      el('div', { class: 'row' }, wholeFile(d.books[0].series, d.books, word))) : null,
    runs.length > 1 ? el('p', { class: 'small dim', text: 'Each run can be downloaded as one comic with the button beside its name.' }) : null,
    d.total ? body : el('p', { class: 'dim', text: 'Nothing in the library belongs to this series any more.' }));
}

// The Library has two halves. Which one was open is remembered on this device, per profile.
const GRAPHIC = 'Graphic novel';
const sectionKey = () => 'bookhub-section-' + (S.me && S.me.id ? S.me.id : 'guest');
function savedSection() { try { return localStorage.getItem(sectionKey()) === 'graphic' ? 'graphic' : 'books'; } catch (e) { return 'books'; } }

function pageLibrary(section) {
  const f = { section: section === 'graphic' || section === 'books' ? section : savedSection(), q: '', tagq: '', scopes: SCOPES.map((s) => s[0]).filter((s) => s !== 'mine' || !isGuest()), tag: '', kinds: savedKinds() };
  const grid = el('div', { class: 'shelf' });
  const count = el('div', { class: 'dim', role: 'status' });
  const tagBox = el('div', { class: 'row', style: 'gap:8px' });
  const tagNote = el('div', { class: 'row', role: 'status' });
  const scopeRow = el('div', { class: 'row', role: 'group', 'aria-label': 'Tag sources to search' });
  const kindRow = el('div', { class: 'row', role: 'group', 'aria-label': 'Kinds of book to show' });
  const sectionRow = el('nav', { class: 'seg row', role: 'group', 'aria-label': 'Part of the library', style: 'gap:6px;border:1px solid var(--line);padding:4px;align-self:flex-start' });
  const seriesBox = el('div', { class: 'stack' });
  const bookKinds = () => S.kinds.filter((k) => k !== GRAPHIC);
  const shownKinds = () => { if (f.section === 'graphic') return [GRAPHIC]; const k = f.kinds.filter((x) => x !== GRAPHIC); return k.length ? k : bookKinds(); };

  async function loadBooks() {
    const data = await api('GET', '/api/books?' + qs({ q: f.q, tag: f.tag, scopes: f.scopes.join(','), kinds: shownKinds().join(',') }));
    const shown = groupSeries(data.books);
    fill(grid, ...shown.map((x) => x.volumes ? seriesStack(x) : bookCard(x, () => loadBooks().catch(oops))));
    const stacks = shown.filter((x) => x.volumes).length;
    const empty = f.q || f.tag ? 'No books match. Try fewer words, or another tag.'
      : !S.library_available ? '' : f.section === 'graphic' ? 'No comics or graphic novels here yet.'
        : shownKinds().length < bookKinds().length ? 'No books of these kinds yet. Turn on more kinds above.' : 'No books in the library yet.';
    count.textContent = data.total === 0 ? empty
      : plural(data.total, 'book') + (stacks ? ' · ' + plural(stacks, 'series', 'series') + ' shown as one card each' : '');
    fill(tagNote, ...(f.tag ? [el('strong', { text: plural(data.total, 'book') + ' tagged "' + f.tag + '"' }),
      el('button', { class: 'btn', type: 'button', text: 'Show all books', onclick: () => { f.tag = ''; draw(); } })] : []));
  }
  async function loadTags() {
    const data = await api('GET', '/api/tags?' + qs({ q: f.tagq, scopes: f.scopes.join(',') }));
    fill(tagBox, ...(data.tags.length ? data.tags.slice(0, 40).map((t) => el('button', {
      class: 'chip', type: 'button', 'aria-pressed': String(f.tag.toLowerCase() === t.tag.toLowerCase()),
      onclick: () => { f.tag = f.tag.toLowerCase() === t.tag.toLowerCase() ? '' : t.tag; draw(); },
    }, el('strong', { text: t.tag }), el('span', { class: 'src', text: SRC_LABEL[t.source] + ' · ' + t.count })))
      : [el('span', { class: 'small dim', text: 'No tags match. Check the spelling or turn on more tag sources.' })]));
  }
  function draw() {
    fill(scopeRow, el('span', { class: 'lab', text: 'Search in' }), ...SCOPES.filter((s) => s[0] !== 'mine' || !isGuest()).map(([k, label]) =>
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(f.scopes.includes(k)), text: label, onclick: () => { f.scopes = f.scopes.includes(k) ? f.scopes.filter((x) => x !== k) : f.scopes.concat(k); draw(); } })));
    fill(sectionRow, ...[['books', 'Books'], ['graphic', 'Graphic novels']].map(([k, label]) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(f.section === k), text: label, onclick: () => {
      f.section = k; f.tag = '';
      try { localStorage.setItem(sectionKey(), k); } catch (e) { /* private browsing */ }
      draw();
    } })));
    kindRow.hidden = f.section === 'graphic';
    fill(seriesBox, ...(f.section === 'graphic' ? [seriesShelf()] : []));
    fill(kindRow, el('span', { class: 'lab', text: 'Show' }), ...bookKinds().map((k) =>
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(shownKinds().includes(k)), text: KIND_LABEL[k] || k, onclick: () => {
        const now = shownKinds();
        const next = now.includes(k) ? now.filter((x) => x !== k) : now.concat(k);
        if (!next.length) return toast('Keep at least one kind of book showing.', true);
        f.kinds = next;
        try { localStorage.setItem(kindsKey(), JSON.stringify(next)); } catch (e) { /* private browsing */ }
        draw();
      } })));
    const vt = document.getElementById('volumes-title'); if (vt) vt.hidden = f.section !== 'graphic' || !S.series;
    loadBooks().catch(oops); loadTags().catch(oops);
  }
  const search = el('input', { type: 'search', placeholder: 'Search', oninput: debounce((e) => { f.q = e.target.value; loadBooks().catch(oops); }, 250) });
  const tagSearch = el('input', { type: 'search', placeholder: 'Try: slow, gothic, read', oninput: debounce((e) => { f.tagq = e.target.value; loadTags().catch(oops); }, 250) });
  setMain(
    el('div', { class: 'row between end' },
      el('div', {}, el('h1', { class: 'glow', text: 'Library' }), el('p', { class: 'dim', text: isGuest() ? 'Browse and download. Nothing is tracked for guests.' : 'Every book: the ones on the home server, the ones you have read elsewhere and the ones the family has on Kindle. Add books to My books to have them on your devices.' })),
      field('Search title, author, series or tag', search)),
    S.library_available ? null : el('p', { class: 'bad', text: 'The book library could not be found. Check the CALIBRE_LIBRARY folder in the settings.' }),
    sectionRow, seriesBox,
    el('div', { class: 'ph', hidden: true, id: 'volumes-title' }, el('h2', { text: 'Comics, graphic novels and whole volumes' }), el('span', { class: 'hatch' })),
    kindRow,
    el('section', { class: 'panel cut', 'aria-label': 'Search tags' }, el('div', { class: 'row end' }, field('Search tags', tagSearch), scopeRow), tagBox, tagNote),
    count, grid);
  draw();
}

// ------------------------------------------------- series by chapter

const seriesCover = (s) => '/api/series/' + s.id + '/cover';
function seriesCard(s) {
  const done = s.chapters ? s.read + ' of ' + plural(s.chapters, 'chapter') + ' read' : 'No chapters found yet';
  return el('div', { class: 'book' },
    el('a', { class: 'cover has-img', href: '#/series/' + s.id, style: '--cv:' + coverColor(s.title) + ';background-image:url(' + seriesCover(s) + ')' },
      el('span', { class: 'fmt', text: 'SERIES' }), el('span', { class: 't', text: s.title })),
    el('div', { class: 'small dim', text: [s.author, s.source].filter(Boolean).join(' · ') }),
    el('div', { class: 'small', text: done }),
    s.unread && s.read ? el('span', { class: 'tag', text: s.unread + ' new' }) : null,
    el('div', { class: 'small dim', text: s.downloaded >= s.chapters ? 'Every chapter kept' : s.downloaded + ' of ' + s.chapters + ' kept so far' }),
    s.next ? el('a', { class: 'btn pri', href: '#/read/' + s.id + '/' + s.next.order, 'aria-label': 'Read ' + s.title + ', ' + s.next.name, text: s.read ? 'Continue' : 'Start reading' })
      : el('div', { class: 'small ok', text: 'All caught up' }));
}

// The top of Library → Graphic novels: the series being followed, and a box to follow another.
function seriesShelf() {
  if (!S.series) {
    return isAdmin() ? el('p', { class: 'small dim', text: 'Manga and manhwa read chapter by chapter can be kept here too. Guide 13 in the docs folder explains how to switch that on.' }) : null;
  }
  const grid = el('div', { class: 'shelf' });
  const note = el('p', { class: 'small dim', role: 'status', text: 'Loading…' });
  const found = el('div', { class: 'stack' });
  const q = el('input', { type: 'search', placeholder: 'Title of a manga or manhwa' });
  async function load() {
    const d = await api('GET', '/api/series');
    fill(grid, ...d.series.map(seriesCard));
    note.textContent = d.series.length ? plural(d.series.length, 'series', 'series') + (d.queued ? ' · ' + plural(d.queued, 'chapter') + ' still downloading' : '') : (canRequest() ? 'No series followed yet. Search below to follow one.' : 'No series followed yet.');
  }
  async function search() {
    if (q.value.trim().length < 2) return;
    fill(found, el('p', { class: 'dim', role: 'status', text: 'Searching the reading sites…' }));
    try {
      const d = await api('GET', '/api/series/search?' + qs({ q: q.value.trim() }));
      if (!d.sources) { fill(found, el('p', { class: 'warn', role: 'status', text: 'The series server has no sources yet, so there is nowhere to search. An admin adds them in the series server itself; guide 13 shows how.' })); return; }
      const hits = d.groups.filter((g) => g.results.length);
      const quiet = d.groups.filter((g) => g.error).map((g) => g.source);
      fill(found, 
        hits.length ? null : el('p', { class: 'dim', role: 'status', text: 'Nothing found. Try fewer words.' }),
        ...hits.map((g) => el('div', { class: 'stack', style: 'gap:8px' }, el('h3', { text: g.source }),
          el('div', { class: 'list' }, g.results.map((r) => {
            const btn = el('button', { class: 'btn pri', type: 'button', text: 'Follow', 'aria-label': 'Follow ' + r.title, onclick: async () => {
              btn.disabled = true; btn.textContent = 'Following…';
              try { const x = await api('POST', '/api/series/' + r.id + '/follow', { on: true }); toast('Following ' + r.title + '. ' + plural(x.queued, 'chapter') + ' queued to download.'); btn.textContent = 'Following'; load().catch(oops); } catch (e) { btn.disabled = false; btn.textContent = 'Follow'; oops(e); }
            } });
            return el('div', { class: 'row between' },
              el('div', { class: 'row' }, el('img', { class: 'thumb', src: seriesCover(r), alt: '', loading: 'lazy' }), el('a', { href: '#/series/' + r.id, text: r.title })),
              r.following ? el('span', { class: 'ok', text: 'Following' }) : btn);
          })))),
        quiet.length ? el('p', { class: 'small dim', text: 'No answer from: ' + quiet.join(', ') + '.' }) : null);
    } catch (e) { fill(found, el('p', { class: 'bad', role: 'status', text: e.message })); }
  }
  load().catch((e) => { note.className = 'small bad'; note.textContent = e.message; });
  return el('section', { class: 'panel cut', 'aria-label': 'Series by chapter' },
    head('Series, chapter by chapter', el('button', { class: 'btn', type: 'button', text: 'Look for new chapters', onclick: async () => { try { await api('POST', '/api/series/check'); toast('Checking every series. New chapters appear here as they are found.'); } catch (e) { oops(e); } } })),
    el('p', { class: 'small dim', text: 'Manga and manhwa followed on reading sites. Every chapter is downloaded and kept on the server. Reading progress here is shared by everyone who can see this shelf.' }),
    note, grid,
    canRequest() ? el('form', { class: 'row end', role: 'search', onsubmit: (e) => { e.preventDefault(); search(); } }, field('Follow a new series', q), el('button', { class: 'btn', type: 'submit', text: 'Search' })) : null,
    found);
}

async function pageSeries(id) {
  const s = await api('GET', '/api/series/' + encodeURIComponent(id));
  const again = () => pageSeries(id).catch(oops);
  const mark = (ids, read) => api('POST', '/api/series/chapters/read', { ids, read }).then(again).catch(oops);
  const list = s.chapters_list.slice().reverse();
  const missing = s.chapters_list.filter((c) => !c.downloaded).length;
  setMain(
    el('a', { href: '#/library/graphic', text: '← Graphic novels' }),
    el('div', { class: 'cols' },
      el('div', { style: 'flex:0 0 220px' }, el('div', { class: 'cover has-img', style: '--cv:' + coverColor(s.title) + ';background-image:url(' + seriesCover(s) + ')' })),
      el('div', { class: 'stack' }, el('h1', { class: 'glow', text: s.title }),
        el('p', { class: 'dim', text: [s.author, s.status, s.source].filter(Boolean).join(' · ') }),
        s.genres.length ? el('p', { class: 'lab', text: s.genres.join(' · ') }) : null,
        el('p', { text: plural(s.chapters, 'chapter') + ' · ' + s.read + ' read · ' + s.downloaded + ' kept on the server' }),
        s.description ? el('p', { class: 'small', style: 'white-space:pre-line', text: s.description }) : null,
        el('div', { class: 'row' },
          s.next ? el('a', { class: 'btn pri', href: '#/read/' + s.id + '/' + s.next.order, text: (s.read ? 'Continue: ' : 'Start: ') + s.next.name }) : null,
          !canRequest() ? null : el('button', { class: 'btn', type: 'button', text: s.following ? 'Stop following' : 'Follow', onclick: async () => {
            if (s.following && !window.confirm('Stop following ' + s.title + '? New chapters will no longer be fetched. The chapters already kept stay on the server.')) return;
            try { const x = await api('POST', '/api/series/' + s.id + '/follow', { on: !s.following }); toast(s.following ? 'No longer following.' : 'Following. ' + plural(x.queued, 'chapter') + ' queued to download.'); again(); } catch (e) { oops(e); }
          } }),
          missing && !s.local && canRequest() ? el('button', { class: 'btn', type: 'button', text: 'Keep the ' + plural(missing, 'missing chapter'), onclick: async () => { try { const x = await api('POST', '/api/series/' + s.id + '/download'); toast(plural(x.queued, 'chapter') + ' queued to download.'); } catch (e) { oops(e); } } }) : null,
          el('button', { class: 'btn', type: 'button', text: 'Look for new chapters', onclick: async () => { try { await api('GET', '/api/series/' + s.id + '?refresh=1'); toast('Chapter list refreshed.'); again(); } catch (e) { oops(e); } } }),
          s.unread ? el('button', { class: 'btn', type: 'button', text: 'Mark all as read', onclick: () => mark(s.chapters_list.filter((c) => !c.read).map((c) => c.id), true) }) : null))),
    el('section', { class: 'panel cut' }, head('Chapters'),
      list.length ? el('div', { class: 'scroll' }, el('table', { style: 'min-width:640px' },
        el('thead', {}, el('tr', {}, ['Chapter', 'Read', 'On the server', ''].map((t) => el('th', { scope: 'col', text: t })))),
        el('tbody', {}, list.map((c) => el('tr', {},
          el('th', { scope: 'row', style: 'font-weight:' + (c.read ? 400 : 700) }, el('a', { href: '#/read/' + s.id + '/' + c.order, text: c.name })),
          el('td', { class: c.read ? 'dim' : '', text: c.read ? 'Read' : 'Not yet' }),
          el('td', { class: c.downloaded || s.local ? 'ok' : 'dim', text: c.downloaded || s.local ? 'Kept' : 'Not yet' }),
          el('td', {}, el('div', { class: 'row' },
            c.downloaded ? el('a', { class: 'btn', href: '/api/series/chapters/' + c.id + '/file', 'aria-label': 'Download ' + c.name + ' as a file', text: 'File' }) : null,
            el('button', { class: 'btn', type: 'button', 'aria-label': (c.read ? 'Mark as not read: ' : 'Mark as read: ') + c.name, text: c.read ? 'Mark unread' : 'Mark read', onclick: () => mark([c.id], !c.read) })))))))) : el('p', { class: 'dim', text: 'No chapters found. Try "Look for new chapters".' })));
}

// One chapter, every page under the last: the way manhwa are drawn, and fine for manga on a tablet.
async function pageRead(id, order) {
  const s = await api('GET', '/api/series/' + encodeURIComponent(id));
  const n = Number(order);
  const at = s.chapters_list.findIndex((c) => c.order === n);
  if (at < 0) { location.hash = '#/series/' + id; return; }
  const c = s.chapters_list[at], before = s.chapters_list[at - 1], after = s.chapters_list[at + 1];
  const pages = el('div', { class: 'pages', role: 'status' }, el('p', { class: 'dim', text: 'Fetching the pages…' }));
  let marked = c.read;
  const done = async () => { if (marked) return; marked = true; try { await api('POST', '/api/series/chapters/read', { ids: [c.id], read: true }); } catch (e) { marked = false; oops(e); } };
  const nav = (where) => el('nav', { class: 'row between', 'aria-label': 'Chapters, ' + where },
    before ? el('a', { class: 'btn', href: '#/read/' + id + '/' + before.order, text: '← ' + before.name }) : el('span'),
    el('a', { href: '#/series/' + id, text: s.title }),
    after ? el('a', { class: 'btn', href: '#/read/' + id + '/' + after.order, text: after.name + ' →' }) : el('span'));
  const end = el('div', { class: 'row', style: 'justify-content:center' },
    after ? el('button', { class: 'btn pri', type: 'button', text: 'Next: ' + after.name, onclick: async () => { await done(); location.hash = '#/read/' + id + '/' + after.order; } })
      : el('button', { class: 'btn pri', type: 'button', text: 'Finished. Back to the series', onclick: async () => { await done(); location.hash = '#/series/' + id; } }));
  setMain(nav('top'), el('h1', { class: 'glow', text: c.name }), pages, end, nav('bottom'));
  window.scrollTo(0, 0);
  try {
    const d = await api('GET', '/api/series/chapters/' + c.id + '/pages');
    pages.removeAttribute('role');
    fill(pages, ...Array.from({ length: d.pages }, (_, i) => el('img', { src: '/api/series/' + id + '/chapter/' + c.order + '/page/' + i, alt: 'Page ' + (i + 1) + ' of ' + d.pages, loading: i < 3 ? 'eager' : 'lazy' })));
    // Reaching the end of the last page counts as having read the chapter.
    if (window.IntersectionObserver) new IntersectionObserver((seen, o) => { if (seen.some((x) => x.isIntersecting)) { o.disconnect(); done(); } }).observe(end);
  } catch (e) { fill(pages, el('p', { class: 'bad', text: e.message })); }
}

// ------------------------------------------------------------ book detail

// Where one book stands on one Kindle, with the button that fits.
function deviceLine(d, bookId, again) {
  const send = (label) => el('button', { class: 'btn', type: 'button', text: label, onclick: () => api('POST', '/api/books/' + bookId + '/send', { device_id: d.id }).then(() => { toast('Sending to ' + d.name + '.'); again(); }).catch(oops) });
  if (d.state === 'sent') return el('div', { class: 'row' }, el('span', { class: 'small ok', text: 'Sent to ' + d.name + ' · ' + niceDate(d.at) }), d.can_send ? send('Send again') : null);
  if (d.state === 'queued') return el('div', { class: 'row' }, el('span', { class: 'small dim', role: 'status', text: 'Sending to ' + d.name + '…' }));
  if (d.state === 'failed') return el('div', { class: 'row' }, el('span', { class: 'small bad', text: 'Not sent to ' + d.name + ': ' + d.reason }), d.can_send ? send('Try again') : null);
  return el('div', { class: 'row' }, d.can_send ? send('Send to ' + d.name) : el('span', { class: 'small dim', text: d.name + ': ' + d.why }));
}
let sendPolls = 0;

function tagSection(title, note, cls, list, onRemove, onAdd, addLabel) {
  const input = el('input', { type: 'text', maxlength: 60 });
  return el('section', { class: 'stack' },
    head(title, el('span', { class: 'small dim', text: note })),
    el('div', { class: 'row', style: 'gap:8px' }, list.length ? list.map((t) => el('span', { class: 'chip tg' + (cls === 'mine' ? ' mine' : '') }, t,
      onRemove ? el('button', { class: 'x', type: 'button', 'aria-label': 'Remove tag ' + t, onclick: () => onRemove(t), text: '×' }) : null))
      : el('span', { class: 'small dim', text: 'None yet.' })),
    onAdd ? el('form', { class: 'row end', onsubmit: (e) => { e.preventDefault(); if (input.value.trim()) onAdd(input.value.trim()); } },
      field(addLabel, input), el('button', { class: 'btn', type: 'submit', text: 'Add' })) : null);
}

async function pageBook(id) {
  const [b, sim] = await Promise.all([api('GET', '/api/books/' + id), api('GET', '/api/similar/' + id)]);
  // An old link to a second copy opens the book it is a copy of; its downloads and buttons belong to that book.
  if (String(b.id) !== String(id)) { history.replaceState(null, '', '#/book/' + b.id); lastHash = location.hash; id = b.id; }
  const again = () => pageBook(id).catch(oops);
  const post = (path, body, method) => api(method || 'POST', '/api/books/' + id + path, body).then(again).catch(oops);
  const guest = isGuest();
  const side = el('div', { class: 'bside' },
    el('div', { class: 'cover' + (b.has_cover ? ' has-img' : '') + titleSize(b.title), style: '--cv:' + coverColor(b.title) + (b.has_cover ? ';background-image:url(' + coverUrl(b) + ')' : '') },
      el('span', { class: 'fmt', text: b.formats.join(' · ') }), el('span', { class: 't', style: 'font-size:26px;text-decoration:none', text: b.title })),
    b.formats.map((fmt, i) => el('a', { class: 'btn' + (i === 0 ? ' pri' : ''), href: '/api/books/' + id + '/download/' + fmt, text: 'Download ' + fmt })),
    b.formats.length || b.converted ? null : el('p', { class: 'small warn', text: 'The library has this book\'s details but no file for it, so there is nothing to download. Adding the file again in the library manager fixes it.' }),
    b.converted ? el('a', { class: 'btn pri', href: '/api/books/' + id + '/download/converted', text: 'Download converted EPUB' }) : null);
  if (b.formats.includes('PDF') && !b.formats.includes('EPUB') && !b.converted && !guest) {
    const running = b.convert_status === 'running';
    side.append(el('button', { class: 'btn', type: 'button', disabled: running, text: running ? 'Converting…' : 'Convert to EPUB', onclick: () => post('/convert').then(() => toast('Conversion started. Check back in a minute.')) }),
      b.convert_status === 'failed' ? el('p', { class: 'small bad', text: 'The last conversion failed. Scanned PDFs often cannot be converted.' }) : null,
      S.can_convert ? null : el('p', { class: 'small dim', text: 'The converter is not installed in Family Hub' + (S.cwa_url ? '; use Convert in the library manager instead.' : '.') }));
  }
  if (isAdmin() && S.cwa_url) side.append(el('a', { class: 'btn', href: S.cwa_url + '/book/' + b.id, target: '_blank', rel: 'noopener', text: 'Open in library manager' }));
  if (isAdmin()) {
    const copies = (b.copies || []).length;
    side.append(el('button', { class: 'btn danger', type: 'button', text: 'Delete this book', onclick: async () => {
      const files = (b.formats.join(', ') || 'no file') + (copies ? ' and ' + plural(copies, 'other copy', 'other copies') : '');
      if (!window.confirm('Delete "' + b.title + '" (' + files + ') from the library for good? Its files are removed. A copy of the library\'s database is saved first, and the nightly copy (if switched on) still has the book.')) return;
      try { const r = await api('DELETE', '/api/books/' + b.id); toast('"' + r.removed + '" was deleted.'); location.hash = '#/library'; } catch (e) { oops(e); }
    } }));
  }
  if (b.goodreads_id) side.append(goodreadsLink(b));
  if (canSort()) side.append(coverBox(b, again));
  if (b.copies && b.copies.length) {
    side.append(el('div', { class: 'stack', style: 'gap:6px' },
      el('p', { class: 'small dim', text: 'The library holds ' + plural(b.copies.length, 'more copy', 'more copies') + ' of this book, shown here as one.' }),
      b.copies.flatMap((c) => c.files.map((f) => el('a', { class: 'small', href: '/api/books/' + c.id + '/download/' + f.format, text: 'Other copy: ' + f.format + ', ' + fileSize(f.size) }))),
      el('a', { class: 'small', href: '#/copies', text: 'Manage copies' })));
  }

  const body = el('div', { class: 'bbody' });
  // A volume or issue names its own story under the title, and links to the rest of its series.
  const seriesName = b.collection || b.series;
  body.append(el('div', { class: 'stack' }, el('h1', { class: 'glow', text: b.title }),
    b.subtitle ? el('p', { style: 'font-size:20px', text: b.subtitle }) : null,
    el('p', { class: 'dim', style: 'font-size:18px' }, [b.author, b.pages ? 'about ' + b.pages + ' pages' : ''].filter(Boolean).join(' · '),
      seriesName ? [b.author || b.pages ? ' · ' : '', el('a', { href: '#/shelf/' + encodeURIComponent(seriesName), text: (b.series_index && !b.part ? 'Book ' + b.series_index + ' of ' : 'Part of ') + b.series })] : null)));

  const kindSel = el('select', { 'aria-label': 'Kind of book', onchange: (e) => api('POST', '/api/books/kind', { ids: [b.id], kind: e.target.value }).then(again).catch(oops) },
    S.kinds.map((k) => el('option', { value: k, text: k, selected: k === b.kind })));
  body.append(el('div', { class: 'row' }, el('span', { class: 'small dim', text: 'Kind of book' }), canSort() ? kindSel : el('strong', { text: b.kind })));

  if (!guest) {
    body.append(el('section', { class: 'stack' },
      el('div', { class: 'row' },
        el('button', { class: 'chip', type: 'button', 'aria-pressed': String(b.shelf.on), text: b.shelf.on ? 'In my books' : 'Add to my books', onclick: () => setShelf(id, !b.shelf.on).then(again).catch(oops) }),
        el('span', { class: 'small dim', text: b.shelf.on ? 'Listed under My books in your reading app.' : 'My books are the ones offered to your devices.' })),
      b.shelf.devices.map((d) => deviceLine(d, id, again))));
    if (b.family_shelves && b.family_shelves.length) {
      body.append(el('section', { class: 'panel cut' }, head("Other people's books"),
        el('p', { class: 'small dim', text: 'Admins can put this book on someone else\'s list, send it to their Kindle, and record how far they have got.' }),
        el('div', { class: 'list' }, b.family_shelves.map((p) => el('div', { class: 'row between' },
          el('div', { class: 'stack', style: 'gap:6px' },
            el('div', { class: 'row' }, el('strong', { text: p.name }),
              el('button', { class: 'chip', type: 'button', 'aria-pressed': String(p.on), text: p.on ? 'In ' + p.name + "'s books" : 'Add to ' + p.name + "'s books", onclick: () => setShelf(id, !p.on, { id: p.profile_id, name: p.name }).then(again).catch(oops) })),
            p.devices.map((d) => deviceLine(d, id, again))),
          field('Reading status for ' + p.name, el('select', { onchange: (e) => post('/reading', { profile_id: p.profile_id, status: e.target.value }) },
            [['none', 'Not started'], ['reading', 'Reading'], ['finished', 'Finished']].map(([k, label]) => el('option', { value: k, text: label, selected: p.status === k })))))))));
    }
    const waiting = b.shelf.devices.concat(...(b.family_shelves || []).map((p) => p.devices)).some((d) => d.state === 'queued');
    if (!waiting) sendPolls = 0;
    else if (sendPolls++ < 12) setTimeout(() => { if (location.hash === '#/book/' + id) again(); }, 5000);
    const pct = el('input', { type: 'number', min: 0, max: 100, value: Math.round(b.percent || 0) });
    body.append(el('section', { class: 'stack' },
      b.status === 'reading' ? el('div', { style: 'max-width:480px' }, bar(b.percent, Math.round(b.percent) + ' percent read')) : null,
      el('p', { class: 'small dim', text: b.status === 'finished' ? 'Finished ' + niceDate(b.finished) : (b.status === 'reading' ? Math.round(b.percent) + '% read' + (b.device ? ' · last read on ' + b.device : '') : 'Not started') }),
      el('div', { class: 'row end', role: 'group', 'aria-label': 'Reading status' },
        el('button', { class: 'chip', type: 'button', 'aria-pressed': String(b.status === 'none'), text: 'Not started', onclick: () => post('/reading', { status: 'none' }) }),
        el('button', { class: 'chip', type: 'button', 'aria-pressed': String(b.status === 'reading'), text: 'Reading', onclick: () => post('/reading', { status: 'reading' }) }),
        el('button', { class: 'chip', type: 'button', 'aria-pressed': String(b.status === 'finished'), text: 'Finished', onclick: () => post('/reading', { status: 'finished' }) }),
        el('div', { class: 'field', style: 'max-width:140px' }, el('label', { class: 'lab', for: 'pct', text: 'My place (%)' }), Object.assign(pct, { id: 'pct' })),
        el('button', { class: 'btn', type: 'button', text: 'Set my place', onclick: () => post('/reading', { percent: Number(pct.value) }) }))));
  }
  body.append(el('section', { class: 'stack' }, head('Synopsis'),
    el('p', { class: 'synopsis' + (b.synopsis ? '' : ' dim'), text: b.synopsis || 'No synopsis in this book\'s details yet. Add one in the library manager.' })));
  const canTag = !guest && (S.me.role === 'admin' || S.me.can_tag);
  body.append(tagSection('Book file tags', "From the file's own details, tidied", '', b.tags.epub));
  const community = tagSection('Community tags', (b.community_sources.length ? 'From ' + b.community_sources.join(', ') : 'From book catalogues') + ' · read-only', 'community', b.tags.community);
  if (canTag && b.can_look_up) {
    const look = el('button', { class: 'btn', type: 'button', text: b.tags.community.length ? 'Look up again' : 'Look up tags', onclick: async () => {
      look.disabled = true; look.textContent = 'Asking the catalogues…';
      try {
        const r = await api('POST', '/api/books/' + id + '/lookup');
        if (r.added) toast(plural(r.added, 'tag') + ' from ' + r.sources.join(' and ') + '.');
        else if (r.failed.length) toast(r.failed.join(' and ') + ' could not be reached. Try again later.', true);
        else toast('The catalogues do not know this book under this title and author. Fixing its details in the library manager usually helps.', false, 9000);
        again();
      } catch (e) { oops(e); look.disabled = false; look.textContent = 'Look up tags'; }
    } });
    community.append(el('div', { class: 'row' }, look));
  }
  body.append(community);
  body.append(tagSection('Family tags', 'Shared with the whole family', 'family', b.tags.family,
    canTag ? (t) => post('/tags', { kind: 'family', tag: t }, 'DELETE') : null, canTag ? (t) => post('/tags', { kind: 'family', tag: t }) : null, 'New family tag'));
  if (!guest) {
    body.append(tagSection('My tags', 'Only on your profile', 'mine', b.tags.mine,
      (t) => post('/tags', { kind: 'mine', tag: t }, 'DELETE'), (t) => post('/tags', { kind: 'mine', tag: t }), 'New tag'));
    body.append(el('section', { class: 'panel cut' }, el('div', { class: 'cols' },
      el('div', { class: 'stack' }, head('My rating'),
        el('div', { class: 'stars', role: 'group', 'aria-label': 'Rate this book' }, [1, 2, 3, 4, 5].map((n) =>
          el('button', { class: 'star' + (n <= b.rating ? ' on' : ''), type: 'button', 'aria-label': 'Rate ' + n + ' of 5', 'aria-pressed': String(n === b.rating), onclick: () => post('/rating', { rating: n === b.rating ? 0 : n }) }, starMark()))),
        el('p', { class: 'small dim', text: b.rating ? 'You rated this ' + b.rating + ' of 5' : 'Not rated yet' }),
        b.readers ? el('p', { class: 'small', text: '★ Readers: ' + readersText(b.readers) }) : null),
      el('div', { class: 'stack' }, head('Family ratings'),
        b.family_ratings.length ? el('div', { class: 'list' }, b.family_ratings.map((r) => el('div', { class: 'row between' }, el('span', { text: r.name }), el('strong', { text: r.rating + ' of 5' }))))
          : el('p', { class: 'small dim', text: 'Nobody else has rated this yet.' })))));
  }
  body.append(similarList('Similar books', sim.books));
  setMain(back('#/library', 'Back to library'),
    el('div', { class: 'detail' }, side, body));
}

function similarList(title, books) {
  return el('section', { class: 'stack' }, head(title),
    books.length ? el('div', { class: 'tiles' }, books.slice(0, 9).map((s) => el('div', { class: 'tile cut stack' },
      el('strong', { style: 'font-size:18px', text: s.title }), el('span', { class: 'small dim', text: s.author }),
      el('span', { class: 'lab', text: 'Shares: ' + s.shared.join(', ') }),
      s.already_read ? el('span', { class: 'small dim', text: 'You have read this' }) : null,
      s.in_library ? el('a', { class: 'btn', href: '#/book/' + s.id, text: 'In library · open' })
        : (canRequest() ? el('a', { class: 'btn pri', href: '#/discover/' + encodeURIComponent(s.title + ' ' + s.author), text: 'Find it' }) : el('span', { class: 'small dim', text: 'Not in the library' })))))
      : el('p', { class: 'small dim', text: 'No matches yet. Add a few tags to this book to get suggestions.' }));
}

// ------------------------------------------------------------- dashboard

function dashTabs(active) {
  return el('nav', { class: 'seg row', 'aria-label': 'Dashboard views', style: 'gap:6px;border:1px solid var(--line);padding:4px;align-self:flex-start' },
    el('a', { href: '#/dashboard', 'aria-current': active === 'me' ? 'page' : null, text: 'Personal' }),
    el('a', { href: '#/mybooks', 'aria-current': active === 'mybooks' ? 'page' : null, text: 'My books' }));
}
// Dashboard, My books: what is on my list and my devices, what I am reading, what I have finished.
async function pageMyBooks() {
  const [shelf, mine, reading, read] = await Promise.all([api('GET', '/api/me/shelf'), api('GET', '/api/books?filter=shelf'),
    api('GET', '/api/books?filter=reading'), api('GET', '/api/books?filter=read')]);
  const again = () => pageMyBooks().catch(oops);
  const apps = shelf.devices.filter((d) => d.kind !== 'kindle').map((d) => d.name);
  const finished = read.books.slice().sort((x, y) => String(y.finished || '').localeCompare(String(x.finished || '')));
  const grid = (books) => el('div', { class: 'shelf' }, books.map((b) => bookCard(b, again, { plain: true })));
  setMain(
    el('div', {}, el('h1', { class: 'glow', text: 'My books' }), el('p', { class: 'dim', text: 'The books on your list, where each one stands on your devices, and what you are reading and have read.' })),
    dashTabs('mybooks'),
    el('section', { class: 'panel cut' }, head('On my devices', el('a', { class: 'btn', href: '#/devices/' + S.me.id, text: 'Add or change devices' })),
      shelf.devices.length ? null : el('p', { class: 'small warn', text: 'You have no devices yet. Add one and the books on your list are offered to it.' }),
      apps.length ? el('p', { class: 'small dim', text: apps.join(' and ') + (apps.length === 1 ? ' fetches' : ' fetch') + ' these from the catalogue under My books; Family Hub cannot see which ones have been downloaded there.' }) : null,
      mine.books.length ? el('div', { class: 'list' }, mine.books.map((b) => {
        const s = shelf.books[b.id] || { devices: [] };
        return el('div', { class: 'row between' },
          el('div', { class: 'stack', style: 'gap:4px;min-width:220px;flex:1' }, el('a', { href: '#/book/' + b.id, text: b.title }),
            el('span', { class: 'small dim', text: b.author + (s.added ? ' · added ' + niceDate(s.added) : '') })),
          el('div', { class: 'stack', style: 'gap:4px;flex:2' }, s.devices.length ? s.devices.map((d) => deviceLine(d, b.id, again)) : el('span', { class: 'small dim', text: shelf.devices.length ? 'Offered through the catalogue.' : '' })),
          el('button', { class: 'btn', type: 'button', 'aria-label': 'Take off my list: ' + b.title, text: 'Take off my list', onclick: () => setShelf(b.id, false).then(again).catch(oops) }));
      })) : el('p', { class: 'small dim', text: 'Nothing on your list yet. Press "Add to my books" under a book in the Library.' })),
    el('section', { class: 'panel cut' }, head('Reading now'),
      reading.books.length ? grid(reading.books) : el('p', { class: 'small dim', text: 'Nothing in progress.' })),
    el('section', { class: 'panel cut' }, head('Finished'),
      finished.length ? el('p', { class: 'small dim', text: plural(finished.length, 'book') + ', the most recent first.' }) : null,
      finished.length ? grid(finished) : el('p', { class: 'small dim', text: 'Nothing finished yet.' })));
}

function tile(label, num, note) {
  return el('div', { class: 'tile cut' }, el('div', { class: 'lab', text: label }), el('div', { class: 'num glow', text: num }), el('div', { class: 'small dim', text: note || '' }));
}
function barList(title, rows, color) {
  const max = Math.max(1, ...rows.map((r) => r[1]));
  return el('section', { class: 'panel cut' }, el('h3', { text: title }),
    rows.length ? rows.slice(0, 5).map(([name, n]) => el('div', { class: 'stack', style: 'gap:4px' },
      el('div', { class: 'row between' }, el('span', { text: name }), el('strong', { text: n })),
      el('div', { class: 'meter', 'aria-hidden': 'true' }, el('i', { style: 'width:' + Math.round(100 * n / max) + '%;background:' + color }))))
      : el('p', { class: 'small dim', text: 'Nothing yet this year.' }));
}

async function pageDashboard() {
  const view = { kind: '', value: '', metric: 'books' };
  const base = await api('GET', '/api/stats/me');
  const trend = el('section', { class: 'panel cut' });
  const chips = [{ kind: '', value: '', label: 'All books', src: '' }];
  base.top.epub.slice(0, 3).forEach(([t]) => chips.push({ kind: 'genre', value: t, label: t, src: 'Genre' }));
  base.top.community.slice(0, 3).forEach(([t]) => chips.push({ kind: 'community', value: t, label: t, src: 'Community' }));
  base.top.family.slice(0, 2).forEach(([t]) => chips.push({ kind: 'family', value: t, label: t, src: 'Family' }));
  base.top.mine.slice(0, 2).forEach(([t]) => chips.push({ kind: 'mine', value: t, label: t, src: 'Mine' }));
  base.authors.slice(0, 3).forEach(([a]) => chips.push({ kind: 'author', value: a, label: a, src: 'Author' }));

  async function drawTrend() {
    const s = view.kind ? await api('GET', '/api/stats/me?' + qs({ kind: view.kind, value: view.value })) : base;
    const pages = view.metric === 'pages';
    const max = Math.max(1, ...s.months.map((m) => (pages ? m.pages : m.library + m.outside)));
    const totalPages = s.months.reduce((a, m) => a + m.pages, 0);
    fill(trend, 
      head('Reading trend', el('div', { class: 'row', role: 'group', 'aria-label': 'Measure' }, [['books', 'Books'], ['pages', 'Pages']].map(([k, label]) =>
          el('button', { class: 'chip', type: 'button', 'aria-pressed': String(view.metric === k), text: label, onclick: () => { view.metric = k; drawTrend().catch(oops); } })))),
      el('div', { class: 'row', role: 'group', 'aria-label': 'Filter the trend' }, el('span', { class: 'lab', text: 'Filter by' }),
        chips.map((c) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(view.kind === c.kind && view.value === c.value), onclick: () => { view.kind = c.kind; view.value = c.value; drawTrend().catch(oops); } },
          el('strong', { text: c.label }), c.src ? el('span', { class: 'src', text: c.src }) : null))),
      el('strong', { role: 'status', text: plural(s.filtered_total, 'book') + (view.kind ? (view.kind === 'author' ? ' by ' : ' tagged ') + view.value : ' read') + ' in ' + s.year + ' · ' + totalPages.toLocaleString() + ' pages' }),
      el('div', { class: 'row' }, el('span', { class: 'key' }, el('i', { style: 'background:var(--accent)' }), 'Family library'),
        el('span', { class: 'key' }, el('i', { style: 'background:repeating-linear-gradient(135deg,var(--accent2) 0 2px,transparent 2px 5px);border:1px solid var(--accent2)' }), 'Read elsewhere (Kindle and others)')),
      monthChart(s.months.map((m) => ({ month: m.month, value: pages ? m.pages.toLocaleString() : m.library + m.outside, scale: pages ? m.pages : undefined,
        pages: m.pages, series: [['e', m.library], ['k', m.outside]] })), (m) => MONTHS[m.month - 1] + ': ' + plural(m.series[0][1] + m.series[1][1], 'book') + ', ' + m.pages + ' pages'));
  }

  const readingNow = el('section', { class: 'panel cut' }, head('Reading now'),
    base.reading_now.length ? el('div', { class: 'list' }, base.reading_now.map((r) => el('div', { class: 'stack', style: 'gap:6px' },
      el('div', { class: 'row' }, r.id ? el('a', { href: '#/book/' + r.id, style: 'font-weight:700;font-size:18px', text: r.title }) : el('strong', { style: 'font-size:18px', text: r.title }),
        el('span', { class: 'tag', text: r.in_library ? 'Library' : 'Elsewhere' })),
      el('span', { class: 'small dim', text: r.author }),
      r.in_library || r.percent ? bar(r.percent, Math.round(r.percent) + ' percent read') : null,
      el('span', { class: 'small dim', text: (r.in_library || r.percent ? Math.round(r.percent) + '% · ' : 'Started ' + niceDate(r.started) + ' · place not reported · ') + (r.device ? 'last read on ' + r.device + ' · ' : '') + 'updated ' + ago(r.updated).toLowerCase() }))))
      : el('p', { class: 'small dim', text: 'Nothing in progress.' }));

  const oTitle = el('input', { type: 'text' }), oAuthor = el('input', { type: 'text' }), oPages = el('input', { type: 'number', min: 1 });
  const oStatus = el('select', {}, el('option', { value: 'finished', text: 'Finished' }), el('option', { value: 'reading', text: 'Reading now' }));
  const outside = el('form', { class: 'panel cut', onsubmit: async (e) => {
    e.preventDefault();
    try { await api('POST', '/api/me/outside', { title: oTitle.value, author: oAuthor.value, pages: oPages.value, status: oStatus.value }); toast('Added.'); pageDashboard(); } catch (err) { oops(err); }
  } }, head('Add a book read elsewhere'),
    el('p', { class: 'small dim', text: 'For Kindle Unlimited, paper books or anything else outside the family library.' }),
    el('div', { class: 'row end' }, field('Title', oTitle), field('Author', oAuthor), field('Pages (optional)', oPages), field('Status', oStatus),
      el('button', { class: 'btn pri', type: 'submit', text: 'Add book' })),
    S.me.tracker_linked ? el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', text: 'Update from Hardcover now', onclick: async () => {
      try { const r = await api('POST', '/api/me/tracker-sync'); toast(plural(r.updated, 'book') + ' updated.'); pageDashboard(); } catch (err) { oops(err); }
    } })) : null);

  setMain(
    el('div', {}, el('h1', { class: 'glow', text: 'Your reading' }), el('p', { class: 'dim', text: 'Family library and everything read elsewhere · ' + base.year + ' so far' })),
    dashTabs('me'),
    el('div', { class: 'tiles' },
      tile('Books read this year', base.read_year, base.read_library + ' family library · ' + (base.read_year - base.read_library) + ' elsewhere'),
      tile('Books read this month', base.read_month, ''),
      tile('Pages read this year', base.pages_year.toLocaleString(), base.pages_unknown ? plural(base.pages_unknown, 'book') + ' without a page count' : 'From finished books'),
      tile('Authors read this year', base.authors_year, '')),
    trend,
    el('div', { class: 'cols3' },
      barList('Top book file tags', base.top.epub, 'var(--accent)'), barList('Top community tags', base.top.community, 'var(--accent2)'),
      barList('Top family and personal tags', base.top.family.concat(base.top.mine).sort((a, b) => b[1] - a[1]), 'var(--accent3)'), barList('Authors read most', base.authors, 'var(--accent3)')),
    el('div', { class: 'cols' }, readingNow,
      el('section', { class: 'panel cut' }, head('Recently finished'),
        base.recent.length ? el('div', { class: 'list' }, base.recent.map((r) => el('div', { class: 'row between' },
          el('span', {}, r.id ? el('a', { href: '#/book/' + r.id, text: r.title }) : r.title, el('span', { class: 'small dim', text: r.in_library ? '' : ' · elsewhere' })),
          el('span', { class: 'small dim', text: niceDate(r.finished) })))) : el('p', { class: 'small dim', text: 'No finished books yet this year.' }))),
    outside);
  await drawTrend();
}

// ----------------------------------------------------------------- admin

// A column chart in the design's style: one stacked column per month, the months underneath.
// series: [[class, value], ...] per month, drawn bottom first. Classes are the design's: 'e' solid, 'k' hatched.
function monthChart(months, label) {
  const total = (m) => m.series.reduce((a, s) => a + s[1], 0);
  const max = Math.max(1, ...months.map((m) => m.scale === undefined ? total(m) : m.scale));
  return el('div', { class: 'scroll' },
    el('div', { class: 'chart' }, months.map((m) => {
      const unit = 140 / max * (m.scale === undefined ? 1 : (total(m) ? m.scale / total(m) : 0));
      return el('div', { class: 'm' }, el('div', { class: 'num', style: 'font-size:20px;line-height:1', text: m.value }),
        el('div', { class: 'stack', role: 'img', 'aria-label': label(m) },
          m.series.slice().reverse().map(([cls, v]) => el('div', { class: cls, style: 'height:' + Math.round(v * unit) + 'px' }))));
    })),
    el('div', { class: 'months', 'aria-hidden': 'true' }, months.map((m) => el('span', { text: MONTHS[m.month - 1] }))));
}

function serverCard(sv, reload) {
  const status = (ok, yes, no) => el('span', { class: ok ? 'ok' : 'warn', text: ok ? yes : no });
  return el('section', { class: 'panel cut' }, head('Server'),
    el('div', { class: 'list' },
      el('div', { class: 'row between' }, 'Book library', status(sv.library, 'Found', 'Not found')),
      el('div', { class: 'row between' }, 'Search (Prowlarr)', sv.search.configured ? status(sv.search.ok, 'Connected', 'Not reachable') : el('span', { class: 'dim', text: 'Not set up' })),
      el('div', { class: 'row between' }, 'Series by chapter (Suwayomi)', sv.series.configured ? (sv.series.ok ? el('span', { class: sv.series.sources ? 'ok' : 'warn', text: sv.series.sources ? 'Connected · ' + plural(sv.series.sources, 'source') : 'Connected, but it has no sources yet' }) : status(false, '', 'Not reachable')) : el('span', { class: 'dim', text: 'Not set up' })),
      el('div', { class: 'row between' }, 'Finished-downloads folder', status(sv.downloads_folder, 'Found', 'Not set up')),
      el('div', { class: 'row between' }, 'Inbox for new files', sv.inbox_waiting === null ? el('span', { class: 'dim', text: 'Not set up' }) : el('span', { class: 'ok', text: sv.inbox_waiting ? plural(sv.inbox_waiting, 'file') + ' waiting' : 'Empty' })),
      el('div', { class: 'row between' }, 'Library import folder', sv.ingest_folder ? (sv.ingest_stuck ? el('span', { class: 'warn', text: plural(sv.ingest_stuck, 'file') + ' waiting longer than usual. Family Hub has asked the library manager to look again; if this stays, restart the library manager.' }) : el('span', { class: 'ok', text: sv.ingest_waiting ? plural(sv.ingest_waiting, 'file') + ' being added' : 'Found' })) : el('span', { class: 'warn', text: 'Not set up' })),
      el('div', { class: 'row between' }, 'Guest access', el('span', { class: 'dim', text: sv.remote_guest_blocked ? 'Home network only' : 'Anywhere' }))),
    el('div', { class: 'row' }, el('button', { class: 'btn sm', type: 'button', text: 'Rescan library and tag packs', onclick: async () => { try { const r = await api('POST', '/api/admin/rescan'); toast(plural(r.books, 'book') + ' in the library.'); reload(); } catch (e) { oops(e); } } })));
}

// Admin, Hub: the look, the profiles, the connections and the server. Visible to admins only.
async function pageHubAdmin() {
  const [a, conn, looks] = await Promise.all([api('GET', '/api/stats/admin'), api('GET', '/api/admin/connections'), api('GET', '/api/admin/look')]);
  const reload = () => pageHubAdmin().catch(oops);
  setMain(
    el('div', { class: 'col', style: 'gap:8px' }, el('h1', { class: 'glow', text: 'Admin' }), el('p', { class: 'dim', text: 'Set-up for the whole hub. Visible to admins only.' })),
    appearanceCard(looks, reload),
    profilesCard(a),
    mediaCard(reload),
    servicesCard(),
    connectionsCard(conn.groups, reload, conn.in_file),
    setupFileCard(conn.in_file),
    el('div', { class: 'cols' }, serverCard(a.services, reload),
      el('section', { class: 'panel cut' }, head('Copies to a second disk'), secondCopyLines(a.services.second_copy, reload))));
}

// A program's address as this device can reach it: the saved one, or this page's address with the program's port.
// A saved address on "localhost" only works on the server itself, so this page's address is used instead.
function localLink(link, port) {
  let href = link || 'http://' + location.hostname + ':' + port;
  try { const u = new URL(href); if (['localhost', '127.0.0.1'].includes(u.hostname)) { u.hostname = location.hostname; href = u.href.replace(/\/$/, ''); } } catch (e) { /* keep it as saved */ }
  return href;
}
const jellyfinWatch = (j, id) => localLink(j.link, j.port) + '/web/#/details?id=' + id;

// A show's seasons, then its episodes, each with a Watch button once it is on the server. A film gets one button.
function watchPanel(kind, id, item) {
  const box = el('div', { class: 'stack' }, el('p', { class: 'small dim', role: 'status', text: kind === 'film' ? 'Finding it in Jellyfin…' : 'Asking for the episodes…' }));
  const section = el('section', { class: 'panel cut' }, head(kind === 'film' ? 'Watch' : 'Seasons and episodes'), box);
  api('GET', '/api/media/watch/' + kind + '/' + id).then((d) => {
    if (kind === 'film') {
      if (!item.downloaded) { section.remove(); return; }
      fill(box, d.watch_id ? el('div', { class: 'row' }, el('a', { class: 'btn pri', href: jellyfinWatch(d.jellyfin, d.watch_id), target: '_blank', rel: 'noopener', text: 'Watch ' + item.title }))
        : el('p', { class: 'small dim', text: 'On the server, but Jellyfin has not listed it yet. It usually appears within a few minutes.' }));
      return;
    }
    if (!d.seasons.length) { fill(box, el('p', { class: 'small dim', text: 'Sonarr has no episodes listed for this show yet.' })); return; }
    let pick = (d.seasons.find((x) => x.episodes.some((e) => e.on_server)) || d.seasons[0]).number;
    const chips = el('div', { class: 'row', role: 'group', 'aria-label': 'Season', style: 'gap:6px' });
    const list = el('div', { class: 'list' });
    function draw() {
      fill(chips, d.seasons.map((x) => {
        const have = x.episodes.filter((e) => e.on_server).length;
        return el('button', { class: 'chip', type: 'button', 'aria-pressed': String(x.number === pick), onclick: () => { pick = x.number; draw(); } },
          'Season ' + x.number, el('span', { class: 'src', text: have + '/' + x.episodes.length }));
      }));
      const season = d.seasons.find((x) => x.number === pick);
      fill(list, season.episodes.map((e) => el('div', { class: 'row between', style: 'gap:10px' },
        el('div', { class: 'stack', style: 'gap:2px;min-width:0' },
          el('strong', { text: e.number + '. ' + (e.title || 'Episode ' + e.number) }),
          el('span', { class: 'small ' + (e.downloading !== null && e.downloading !== undefined && !e.on_server ? 'ok' : 'dim'),
            text: [e.aired ? niceDate(e.aired) : 'Not aired yet', e.on_server ? '' : e.downloading !== null && e.downloading !== undefined ? 'downloading · ' + e.downloading + '%' : e.wanted ? 'being looked for' : 'not wanted'].filter(Boolean).join(' · ') })),
        e.on_server && e.watch_id ? el('a', { class: 'btn sm pri', href: jellyfinWatch(d.jellyfin, e.watch_id), target: '_blank', rel: 'noopener', 'aria-label': 'Watch episode ' + e.number + ', ' + e.title, text: 'Watch' })
          : e.on_server ? el('span', { class: 'small dim', text: 'On the server' }) : null)));
    }
    draw();
    fill(box, chips, list);
  }).catch((e) => { if (!(e instanceof Stale)) fill(box, el('p', { class: 'small bad', text: e.message })); });
  return section;
}

// Links to each program's own page, built from the address Family Hub was opened with, so they work from
// any device at home. Checking which ones answer takes a moment, so the card fills in by itself.
function servicesCard() {
  const list = el('div', { class: 'list' }, el('p', { class: 'small dim', role: 'status', text: 'Checking which programs are running…' }));
  api('GET', '/api/admin/services').then((d) => fill(list, d.services.map((x) => {
    const href = localLink(x.link, x.port);
    return el('div', { class: 'row between', style: 'gap:8px' },
      el('div', { class: 'stack', style: 'gap:2px;min-width:0' }, el('strong', { text: x.name }), el('span', { class: 'small dim', text: x.what })),
      el('div', { class: 'row', style: 'gap:10px' },
        el('span', { class: 'small ' + (x.running ? 'ok' : 'dim'), text: x.running ? 'Running' : 'Not running' }),
        x.running ? el('a', { class: 'btn sm', href, target: '_blank', rel: 'noopener', 'aria-label': 'Open ' + x.name + ' in a new tab', text: 'Open' }) : null));
  }))).catch((e) => { if (!(e instanceof Stale)) fill(list, el('p', { class: 'small bad', text: e.message })); });
  return el('section', { class: 'panel cut' }, head('Manage the programs', el('span', { class: 'small dim', text: 'Each opens its own page in a new tab' })),
    el('p', { class: 'small dim', text: 'These work from any computer or phone on the home network. From outside the house they need a VPN into home (for example Tailscale); they are not on the internet by themselves.' }),
    list);
}

// The colour scheme and accent everyone sees, the Scope on Home, and the screen-change scan.
const HUE_NAMES = [[15, 'Pink'], [40, 'Red'], [70, 'Orange'], [105, 'Yellow'], [165, 'Green'], [215, 'Teal'], [250, 'Sky'], [280, 'Blue'], [320, 'Purple'], [345, 'Magenta'], [360, 'Pink']];
const hueName = (h) => (HUE_NAMES.find(([top]) => h < top) || HUE_NAMES[0])[1];

function appearanceCard(data, reload) {
  const look = data.look;
  const design = data.designs.find((d) => d.id === look.design) || data.designs[0];
  const save = (body, msg) => api('POST', '/api/admin/look', body).then((r) => { S.look = r.look; applyLook(r.look); toast(msg); reload(); }).catch(oops);
  const swatch = (colours) => el('span', { class: 'swatch', 'aria-hidden': 'true' }, colours.filter(Boolean).map((c) => el('i', { style: 'background:' + c })));
  const hue = look.accent === null ? design.default_hue : look.accent;
  const hueLabel = el('span', { class: 'small dim', text: look.accent === null ? 'Purple · default' : hueName(hue) + ' · ' + hue + '°' });
  // The slider shows the colour as it moves and saves when it is let go.
  const slider = el('input', { type: 'range', min: '0', max: '359', step: '1', value: String(hue), 'aria-label': 'Accent hue', 'aria-valuetext': hueName(hue),
    oninput: () => { const h = +slider.value; setAccent(h); slider.setAttribute('aria-valuetext', hueName(h)); hueLabel.textContent = hueName(h) + ' · ' + h + '°'; },
    onchange: () => save({ accent: +slider.value }, 'The accent is now ' + hueName(+slider.value).toLowerCase() + ' for everyone.') });
  return el('section', { class: 'panel cut' }, head('Appearance', el('span', { class: 'small dim', text: 'For everyone, on every device' })),
    el('div', { class: 'stack' }, el('span', { class: 'lab', text: 'Colour scheme' }),
      el('div', { class: 'looks', role: 'group', 'aria-label': 'Colour scheme' }, design.schemes.map((sc) =>
        el('button', { class: 'chip scheme', type: 'button', 'aria-pressed': String(sc.id === look.theme), onclick: () => save({ theme: sc.id }, sc.name + ' is now the colour scheme for everyone.') },
          swatch(sc.swatch), el('span', { text: sc.name }))))),
    el('div', { class: 'huepick' },
      el('div', { class: 'row', style: 'justify-content:space-between;gap:10px' }, el('span', { class: 'lab', text: 'Accent colour' }), hueLabel),
      el('div', { class: 'track' }, slider),
      el('div', { class: 'presets', role: 'group', 'aria-label': 'Accent presets' }, design.accents.map((a) =>
        el('button', { type: 'button', 'aria-pressed': String(a.hue === hue), onclick: () => save({ accent: a.hue }, 'The accent is now ' + a.name.toLowerCase() + ' for everyone.') },
          el('i', { style: '--sw:oklch(.68 .16 ' + a.hue + ')' }), a.name))),
      el('div', { class: 'row', style: 'gap:10px' },
        look.accent === null ? null : el('button', { class: 'btn sm', type: 'button', text: 'Reset to purple', onclick: () => save({ accent: null }, 'The accent is back to purple.') }),
        el('span', { class: 'small dim', text: 'Dark and Light use it as the accent; Blueprint prints everything in it. Paper is printed in black only and keeps no accent.' }))),
    visualsChooser(look, save),
    el('div', { class: 'line' }, el('div', { class: 'grow' }, el('div', { style: 'font-weight:600', text: look.piece.transition }),
      el('div', { class: 'small dim', text: 'A sheet of grid paper passes down the page when anyone moves between screens. It stays off on devices set to reduce motion.' })),
    // Named in full, as the design has it: a bare "On" or "Off" says nothing about what it switches.
    el('button', { class: 'chip', type: 'button', 'aria-pressed': String(look.transition), text: look.transition ? 'Scan: on' : 'Scan: off',
      onclick: () => save({ transition: !look.transition }, look.transition ? 'The screen-change scan is off.' : 'The screen-change scan is on.') })));
}

// Which visual shows at the top of Home: a different one each day, or one pinned. The list comes from the
// preview frame itself, so visuals added on GitHub appear here as soon as they arrive.
function visualsChooser(look, save) {
  const chips = el('div', { class: 'row', role: 'group', 'aria-label': 'Visual on Home', style: 'gap:8px' });
  const status = el('p', { class: 'small dim', role: 'status' });
  const nowShowing = el('span', { class: 'small dim' });
  const preview = visualFrame(look.scene === 'daily' ? '' : look.scene, (v) => {
    nowShowing.textContent = v.name ? (look.scene === 'daily' ? 'Today: ' + v.name : 'Showing ' + v.name) : '';
    const list = Array.isArray(v.list) ? v.list : [];
    fill(chips, [['daily', 'A different one each day']].concat(list.map((x) => [x.id, x.name])).map(([id, name]) =>
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(id === look.scene), text: name,
        onclick: () => save({ scene: id }, id === 'daily' ? 'Home now shows a different visual each day.' : name + ' now shows on Home every day.') })));
  });
  const check = el('button', { class: 'btn sm', type: 'button', text: 'Check for new visuals', onclick: async () => {
    check.disabled = true;
    try { showStatus(await api('POST', '/api/admin/visuals/check')); } catch (e) { oops(e); }
    check.disabled = false;
  } });
  function showStatus(st) {
    const where = st.source === 'github' ? 'From GitHub: ' + st.repo + ' (version ' + st.version + '), ' + plural(st.count, 'visual')
      : (st.repo ? 'Built in for now (' + plural(st.count, 'visual') + '); ' + st.repo + ' has not been read yet' : 'Built in: ' + plural(st.count, 'visual') + '. Add your own from a GitHub repository under Connections, Visuals on Home');
    status.className = 'small ' + (st.error ? 'bad' : 'dim');
    status.textContent = where + (st.checked ? ' · checked ' + ago(st.checked).toLowerCase() : '') + (st.error ? ' · ' + st.error : '');
    check.hidden = !st.repo;
    if (S.visuals && st.version !== S.visuals.version) { S.visuals.version = st.version; toast('New visuals arrived.'); route(); }
  }
  api('GET', '/api/admin/visuals').then(showStatus).catch(() => {});
  return el('div', { class: 'stack' }, el('div', { class: 'row between' }, el('span', { class: 'lab', text: 'Visual on Home' }), nowShowing),
    chips,
    el('div', { style: 'border:1px solid var(--line2);line-height:0' }, preview),
    el('div', { class: 'row between', style: 'gap:8px' }, status, check));
}

// Every profile, what it may do, and adding another.
function profilesCard(a) {
  const patch = (id, body, msg) => api('PATCH', '/api/profiles/' + id, body).then(() => { toast(msg || 'Saved.'); route(); }).catch(oops);
  const nName = el('input', { type: 'text' }), nPass = el('input', { type: 'password', autocomplete: 'new-password' });
  const nKind = el('select', {}, el('option', { value: 'member', text: 'Member: everything except admin tools' }),
    el('option', { value: 'restricted', text: 'Restricted: read, rate and tag only' }), el('option', { value: 'admin', text: 'Admin' }));
  // Admins may do all of these whatever the switch says, so their rows say "Always" rather than offer a switch that does nothing.
  const yesNo = (f, p, key, label) => f.role === 'admin' ? el('td', { class: 'small dim', text: 'Always' }) : el('td', {}, el('button', { class: 'chip xs', type: 'button', 'aria-pressed': String(!!p[key]), 'aria-label': label + ': ' + f.name, text: p[key] ? 'Yes' : 'No', onclick: () => patch(f.id, { [key]: !p[key] }) }));
  return el('section', { class: 'panel cut' }, head('Profiles', el('span', { class: 'small dim', text: 'Who can do what' })),
    el('div', { class: 'scroll' }, el('table', { style: 'min-width:1080px' },
      el('thead', {}, el('tr', {}, ['Profile', 'Role', 'Last activity', 'Can find new books', 'Can request films and TV', 'Can edit family tags', 'Can see series by chapter', ''].map((t) => el('th', { scope: 'col', text: t })))),
      el('tbody', {}, a.family.map((f) => {
        const p = S.profiles.find((x) => x.id === f.id) || {};
        return el('tr', {}, el('th', { scope: 'row', text: f.name }),
          el('td', {}, el('div', { class: 'field' }, el('select', { 'aria-label': 'Role for ' + f.name, style: 'min-width:120px', onchange: (e) => patch(f.id, { role: e.target.value }) },
            el('option', { value: 'member', text: 'Member', selected: f.role === 'member' }), el('option', { value: 'admin', text: 'Admin', selected: f.role === 'admin' })))),
          el('td', { class: 'small dim', text: ago(f.last) }),
          yesNo(f, p, 'can_request', 'Can find new books'),
          yesNo(f, p, 'can_request_media', 'Can request films and TV'),
          yesNo(f, p, 'can_tag', 'Can edit family tags'),
          yesNo(f, p, 'can_series', 'Can see series by chapter'),
          el('td', {}, el('div', { class: 'row', style: 'gap:8px;flex-wrap:nowrap' },
            el('a', { class: 'btn sm', href: '#/devices/' + f.id, 'aria-label': 'Devices for ' + f.name, text: 'Devices' }),
            el('button', { class: 'btn sm', type: 'button', text: 'Reset passcode', onclick: () => { const v = window.prompt('New passcode for ' + f.name + ' (leave empty to remove it):', ''); if (v !== null) patch(f.id, { passcode: v }, v ? 'Passcode updated.' : f.name + ' has no passcode now.'); } }),
            f.id === S.me.id ? null : el('button', { class: 'btn sm danger', type: 'button', text: 'Delete', onclick: async () => {
              if (!window.confirm('Delete ' + f.name + ' and their ratings, tags and reading history? A backup is saved first.')) return;
              try { await api('DELETE', '/api/profiles/' + f.id); toast('Profile deleted.'); route(); } catch (e) { oops(e); }
            } }))));
      })))),
    el('form', { class: 'row end', onsubmit: async (e) => {
      e.preventDefault();
      const restricted = nKind.value === 'restricted';
      try { await api('POST', '/api/profiles', { name: nName.value, passcode: nPass.value, role: nKind.value === 'admin' ? 'admin' : 'member', can_request: !restricted, can_tag: true }); toast('Profile added.'); route(); } catch (err) { oops(err); }
    } }, field('New profile name', nName), field('Passcode (optional)', nPass), field('Type', nKind), el('button', { class: 'btn pri', type: 'submit', text: 'Add profile' })));
}

// Admin, Books: the library, its tidying, the family's reading, and the book data backup.
async function pageBooksAdmin() {
  const a = await api('GET', '/api/stats/admin');
  const reload = () => pageBooksAdmin().catch(oops);
  const file = el('input', { type: 'file', accept: 'application/json,.json' });
  setMain(
    el('div', { class: 'col', style: 'gap:8px' }, el('h1', { class: 'glow', text: 'Library and family' }), el('p', { class: 'dim', text: 'Books only · visible to admins · ' + a.year + ' so far' })),
    el('div', { class: 'tiles' },
      tile('Books in the library', a.total.toLocaleString(), a.formats.map(([f, n]) => n + ' ' + f).join(' · ')),
      tile('Added this month', a.added_month, a.added_year + ' added this year'),
      tile('Books read by the family', a.read_year, a.read_month + ' so far this month'),
      tile('Never opened', a.never_opened.toLocaleString(), 'Books no profile has started')),
    sortCard(a), tagsCard(a.tags), detailsCard(), copiesCard(a.copies), elsewhereCard(a.elsewhere), attentionCard(a, reload),
    el('section', { class: 'panel cut' }, head('Books added each month'),
      monthChart(a.added_by_month.map((m) => ({ month: m.month, value: m.count, series: [['e', m.count]] })), (m) => MONTHS[m.month - 1] + ': ' + plural(m.value, 'book') + ' added')),
    el('section', { class: 'panel cut' }, head('Family reading'),
      el('div', { class: 'scroll' }, el('table', {},
        el('thead', {}, el('tr', {}, ['Profile', 'Read this year', 'This month', 'Reading now', 'Last activity'].map((t, i) => el('th', { scope: 'col', class: i >= 1 && i <= 3 ? 'num' : '', text: t })))),
        el('tbody', {}, a.family.map((f) => el('tr', {}, el('th', { scope: 'row', text: f.name }),
          el('td', { class: 'num', text: f.read_year }), el('td', { class: 'num', text: f.read_month }), el('td', { class: 'num', text: f.reading }),
          el('td', { class: 'small dim', text: ago(f.last) }))))))),
    el('div', { class: 'cols2' },
      el('section', { class: 'panel cut' }, head('Recently added'),
        a.recent.length ? el('div', { class: 'list' }, a.recent.map((r) => el('div', { class: 'row between' }, el('a', { href: '#/book/' + r.id, text: r.title }), el('span', { class: 'small dim', text: r.author + ' · ' + niceDate(r.added) })))) : el('p', { class: 'small dim', text: 'Nothing yet.' })),
      el('section', { class: 'panel cut' }, head('Book data backup'),
        el('p', { class: a.last_backup ? 'ok' : 'dim', text: 'Last backup: ' + (a.last_backup ? niceDate(a.last_backup) + ' (' + ago(a.last_backup).toLowerCase() + ')' : 'none yet') }),
        el('p', { class: 'small dim', text: 'Ratings, family tags, personal tags, community tags, book file tags and reading history, saved as one file. A copy is written automatically every day.' }),
        el('div', { class: 'row', style: 'gap:8px' }, el('button', { class: 'btn pri sm', type: 'button', text: 'Back up now', onclick: async () => { try { const r = await api('POST', '/api/admin/backup'); toast('Saved ' + r.file); reload(); } catch (e) { oops(e); } } }),
          el('a', { class: 'btn sm', href: '/api/admin/export', text: 'Download book data' })),
        el('div', { class: 'row end' }, field('Restore from a book data file', file), el('button', { class: 'btn', type: 'button', text: 'Import', onclick: async () => {
          if (!file.files[0]) return toast('Choose a file first.', true);
          const fd = new FormData(); fd.append('file', file.files[0]);
          try { const r = await api('POST', '/api/admin/import', fd); toast('Imported data for ' + plural(r.books, 'book') + '.'); reload(); } catch (e) { oops(e); }
        } })))));
}

// The whole setup in one zip locked with a passphrase, to set up or rebuild the server (setupfile.py).
function setupFileCard(inFile) {
  const phrase = el('input', { type: 'password', autocomplete: 'new-password', minlength: '10' });
  const again = el('input', { type: 'password', autocomplete: 'new-password' });
  const note = el('p', { class: 'small', role: 'status' });
  const make = async (e) => {
    e.preventDefault();
    if (phrase.value.length < 10) { note.className = 'small bad'; note.textContent = 'Use a passphrase of at least 10 characters.'; return; }
    if (phrase.value !== again.value) { note.className = 'small bad'; note.textContent = 'The two passphrases are not the same.'; return; }
    const btn = e.target.querySelector('button[type=submit]');
    btn.disabled = true;
    note.className = 'small dim'; note.textContent = 'Packing the setup… this takes a moment.';
    try {
      const res = await fetch('/api/admin/setup-file', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passphrase: phrase.value }) });
      if (!res.ok) { let msg = 'Something went wrong (' + res.status + ').'; try { msg = (await res.json()).error || msg; } catch (x) { /* not JSON */ } throw new Error(msg); }
      const blob = await res.blob();
      const name = (res.headers.get('Content-Disposition') || '').match(/filename="?([^";]+)/);
      const a = el('a', { href: URL.createObjectURL(blob), download: name ? name[1] : 'family-hub-setup.zip' });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      phrase.value = again.value = '';
      note.className = 'small ok'; note.textContent = 'Downloaded (' + fileSize(blob.size) + '). Give the passphrase separately, by voice or another app.';
    } catch (err) { note.className = 'small bad'; note.textContent = err.message; }
    btn.disabled = false;
  };
  return el('section', { class: 'panel cut' }, head('Setup file'),
    el('p', { class: 'small dim', text: 'Everything needed to set Family Hub up again, or on the home server, in one file locked with a passphrase: '
      + 'the .env file with every setting and password, the profiles and reading history, and each program\'s own setup (indexers, qBittorrent, Radarr, Sonarr, subtitles, Jellyfin, the library manager). '
      + 'No books or films. Unpack it into the Family Hub folder with 7-Zip and the passphrase (guide 7 has the steps).' }),
    inFile ? el('form', { class: 'row end', style: 'gap:8px', onsubmit: make }, field('Passphrase (10 or more characters)', phrase), field('Again', again),
      el('button', { class: 'btn pri', type: 'submit', text: 'Make the setup file' }))
      : el('p', { class: 'small bad', text: 'Family Hub cannot see the .env file. Start it with docker compose from its folder, which shares the file with it.' }),
    note);
}

function connectionsCard(groups, reload, inFile) {
  return el('section', { class: 'panel cut' }, head('Connections'),
    el('p', { class: 'small dim', text: inFile
      ? 'Addresses and keys for the other services. Saving writes them into the .env file, the one file that holds every setting and password, and takes effect straight away. Keys and passwords are never shown again.'
      : 'Addresses and keys for the other services. A value saved here replaces the one in the .env file and takes effect straight away. Keys and passwords are stored on the server and never shown again.' }),
    el('div', { class: 'list' }, groups.map((g) => {
      const inputs = {};
      const note = el('p', { class: 'small', role: 'status' });
      const typed = () => { const v = {}; g.fields.forEach((f) => { const x = inputs[f.name].value.trim(); if (f.kind !== 'secret' || x) v[f.name] = x; }); return v; };
      const fields = g.fields.map((f) => {
        const secret = f.kind === 'secret';
        const input = f.kind === 'toggle' ? el('select', {}, el('option', { value: 'on', text: 'On', selected: f.value === 'on' }), el('option', { value: 'off', text: 'Off', selected: f.value !== 'on' }))
          : el('input', { type: secret ? 'password' : (f.kind === 'url' ? 'url' : 'text'), inputmode: f.kind === 'number' ? 'numeric' : f.kind === 'coordinate' ? 'decimal' : null, autocomplete: secret ? 'new-password' : 'off', value: f.value,
            placeholder: secret ? (f.set ? 'Saved. Type a new one to replace it' : '') : (f.kind === 'url' ? 'http://' : '') });
        inputs[f.name] = input;
        const where = f.source === 'here' ? 'Saved here' : f.source === 'file' ? 'In the .env file' : (f.source === 'env' ? 'From the .env file' : (f.source === 'default' ? 'Usual setting' : 'Not set'));
        return el('div', { class: 'stack', style: 'gap:4px;flex:1;min-width:220px' }, field(f.label, input),
          el('div', { class: 'row', style: 'gap:8px' }, el('span', { class: 'small dim', text: where }),
            f.source === 'here' || f.source === 'file' ? el('button', { class: 'btn', type: 'button', text: 'Remove', 'aria-label': 'Remove saved ' + f.label, onclick: async () => {
              try { await api('POST', '/api/admin/connections', { values: { [f.name]: '' } }); toast(f.label + ' removed.'); reload(); } catch (e) { oops(e); }
            } }) : null));
      });
      // Each service folds away; its line says whether anything is filled in yet.
      const used = g.fields.some((f) => f.set && f.source !== 'default');
      return el('details', {}, el('summary', { class: 'row between' }, el('span', { text: g.title }), el('span', { class: 'small ' + (used ? 'ok' : 'dim'), text: used ? 'Set' : 'Not set up' })),
        el('form', { class: 'stack', style: 'padding-top:8px', onsubmit: async (e) => {
        e.preventDefault();
        try { await api('POST', '/api/admin/connections', { values: typed() }); toast(g.title + ' saved.'); reload(); } catch (err) { oops(err); }
      } }, el('p', { class: 'small dim', text: g.hint }),
        el('div', { class: 'row end' }, fields),
        el('div', { class: 'row' },
          g.can_test ? el('button', { class: 'btn', type: 'button', text: 'Test connection', onclick: async () => {
            note.className = 'small dim'; note.textContent = 'Testing…';
            try { const r = await api('POST', '/api/admin/connections/test', { group: g.id, values: typed() }); note.className = 'small ' + (r.ok ? 'ok' : 'bad'); note.textContent = r.message; } catch (err) { note.className = 'small bad'; note.textContent = err.message; }
          } }) : null,
          el('button', { class: 'btn pri', type: 'submit', text: 'Save' })),
        note));
    })));
}

function sortCard(a) {
  const unsorted = (a.kinds.find((k) => k[0] === 'Unsorted') || [0, 0])[1];
  return el('section', { class: 'panel cut' }, head('Kinds of book', el('a', { class: 'btn', href: '#/sort', text: 'Sort the library' })),
    el('p', { class: 'small dim', text: 'Family Hub guesses a kind for each new book. ' + (unsorted ? plural(unsorted, 'book') + ' could not be guessed.' : 'Every book has a kind.') }),
    el('div', { class: 'row' }, a.kinds.map(([k, n]) => el('span', { class: 'tag', text: (KIND_LABEL[k] || k) + ' · ' + n }))));
}

function lookupLine(l) {
  if (!l.on) return 'Looking up tags is switched off under Connections.';
  const from = l.sources.join(' and ');
  if (l.running || l.waiting) return 'Asking ' + from + ': ' + l.asked + ' of ' + l.books + ' books looked up so far, ' + l.matched + ' recognised. This carries on by itself.';
  return l.matched + ' of ' + l.books + ' books were recognised by ' + from + '.' + (l.errors ? ' ' + plural(l.errors, 'lookup') + ' failed and will be tried again.' : '');
}

function tagsCard(t) {
  return el('section', { class: 'panel cut' }, head('Tags', el('a', { class: 'btn', href: '#/tags', text: 'Tidy tags' })),
    el('p', { class: 'small dim', text: lookupLine(t.lookups) }),
    el('div', { class: 'row' }, el('span', { class: 'tag', text: 'Community tags · ' + t.community }), el('span', { class: 'tag', text: 'Book file tags · ' + t.file }),
      el('span', { class: 'tag', text: 'Removed as clutter · ' + t.removed }), t.hidden ? el('span', { class: 'tag', text: 'Hidden or renamed by you · ' + t.hidden }) : null));
}

async function pageTags() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const t = await api('GET', '/api/admin/tags');
  const again = () => pageTags().catch(oops);
  const rule = (body, msg) => api('POST', '/api/admin/tags/rule', body).then(() => { toast(msg); again(); }).catch(oops);
  const shown = t.tags.filter((x) => x.becomes === null || x.becomes === undefined);
  const changed = t.tags.filter((x) => x.becomes !== null && x.becomes !== undefined);
  const q = el('input', { type: 'search', placeholder: 'Filter', oninput: debounce(() => draw(), 200) });
  const body = el('tbody');
  function draw() {
    const needle = q.value.trim().toLowerCase();
    fill(body, ...shown.filter((x) => !needle || x.tag.toLowerCase().includes(needle)).map((x) => {
      const name = el('input', { type: 'text', maxlength: 60, value: x.tag, 'aria-label': 'New name for ' + x.tag });
      return el('tr', {}, el('th', { scope: 'row', text: x.tag }), el('td', { class: 'num', text: x.count }), el('td', { class: 'small dim', text: x.sources.join(', ') }),
        el('td', {}, el('div', { class: 'row' }, name,
          el('button', { class: 'btn', type: 'button', text: 'Rename', 'aria-label': 'Rename ' + x.tag, onclick: () => rule({ tag: x.tag, becomes: name.value }, 'Renamed everywhere.') }),
          el('button', { class: 'btn', type: 'button', text: 'Hide', 'aria-label': 'Hide ' + x.tag, onclick: () => rule({ tag: x.tag, hide: true }, '"' + x.tag + '" is hidden everywhere.') }))));
    }));
  }
  const lookNow = (again_) => api('POST', '/api/admin/tags/lookup', { again: again_ }).then((r) => { toast(r.started ? 'Looking up tags. This page shows progress when you reopen it.' : 'A lookup is already running.'); again(); }).catch(oops);
  setMain(back('#/admin/books', 'Back to books admin'),
    el('div', {}, el('h1', { class: 'glow', text: 'Tidy tags' }), el('p', { class: 'dim', text: 'Tags that come from book files and from catalogues. Hide the ones that say nothing, or rename two spellings to the same name to merge them. Family tags and personal tags are not listed: change those on the book itself.' })),
    el('section', { class: 'panel cut' }, head('Community tags from catalogues'),
      el('p', { class: 'small', role: 'status', text: lookupLine(t.lookups) }),
      t.lookups.on ? el('div', { class: 'row' },
        el('button', { class: 'btn', type: 'button', disabled: t.lookups.running, text: t.lookups.running ? 'Looking up…' : 'Look up waiting books now', onclick: () => lookNow(false) }),
        el('button', { class: 'btn', type: 'button', disabled: t.lookups.running, text: 'Ask again about unrecognised books', onclick: () => lookNow(true) })) : null,
      el('p', { class: 'small dim', text: 'A book is only recognised when its title and author match a catalogue entry. Books whose details are a file name or a user name will not be; correct them in the library manager, then ask again.' })),
    el('section', { class: 'panel cut' }, head(plural(shown.length, 'tag') + ' in use', field('Filter tags', q)),
      shown.length ? el('div', { class: 'scroll' }, el('table', { style: 'min-width:760px' },
        el('thead', {}, el('tr', {}, ['Tag', 'Books', 'From', 'Change'].map((h, i) => el('th', { scope: 'col', class: i === 1 ? 'num' : '', text: h })))), body))
        : el('p', { class: 'small dim', text: 'No tags yet.' })),
    changed.length ? el('section', { class: 'panel cut' }, head('Hidden or renamed by you'),
      el('div', { class: 'list' }, changed.map((x) => el('div', { class: 'row between' },
        el('span', {}, el('strong', { text: x.tag }), el('span', { class: 'small dim', text: x.becomes ? ' shown as "' + x.becomes + '"' : ' hidden' }), el('span', { class: 'small dim', text: ' · ' + plural(x.count, 'book') })),
        el('button', { class: 'btn', type: 'button', text: 'Put back', 'aria-label': 'Put back ' + x.tag, onclick: () => rule({ tag: x.tag, restore: true }, 'Put back.') }))))) : null,
    t.removed.length ? el('section', { class: 'panel cut' }, head('Removed as clutter'),
      el('p', { class: 'small dim', text: 'Found in book files and left out automatically: ISBNs, dates, publishers, adverts and whole sentences. They are still in the files; Family Hub just does not show them.' }),
      el('div', { class: 'row', style: 'gap:8px' }, t.removed.map((x) => el('span', { class: 'tag small dim', text: x.tag.length > 60 ? x.tag.slice(0, 60) + '…' : x.tag })))) : null);
  draw();
}

function fileSize(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; }

// Books whose title is a file name or whose author is a user name, with a link to put them right.
function detailsCard() {
  const box = el('section', { class: 'panel cut', hidden: true });
  api('GET', '/api/admin/book-details').then((d) => {
    if (!d.books.length) return;
    fill(box, head('Books without real details', el('a', { class: 'btn', href: '#/details', text: 'Fix them' })),
      el('p', { class: 'small dim', text: plural(d.books.length, 'book') + ' in the library ' + (d.books.length === 1 ? 'has' : 'have') + ' a file name for a title or no real author, '
        + 'such as "' + d.books[0].title + '" by ' + (d.books[0].author || 'nobody') + '. Family Hub can look for the real details in the files and online catalogues.' }));
    box.hidden = false;
  }).catch(() => {});
  return box;
}

// One book at a time: what its files say, matches from Open Library and Google Books, and a form to type the
// details in. The library manager makes the change, after a copy of the library's database.
async function pageDetails() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const d = await api('GET', '/api/admin/book-details');
  const gen = routeGen;
  const again = () => pageDetails().catch(oops);
  function bookRow(b) {
    const area = el('div', { class: 'stack', style: 'gap:10px' });
    const find = el('button', { class: 'btn sm pri', type: 'button', text: 'Find details', onclick: () => open() });
    async function open() {
      find.disabled = true;
      fill(area, el('p', { class: 'small dim', role: 'status', text: 'Reading the file and asking the catalogues…' }));
      let r;
      try { r = await api('GET', '/api/admin/book-details/' + b.id); } catch (e) { if (!(e instanceof Stale)) fill(area, el('p', { class: 'small bad', text: e.message })); find.disabled = false; return; }
      if (gen !== routeGen) return;
      const first = r.candidates[0], clue = r.clues.titles[0] || {};
      const title = el('input', { type: 'text', value: first ? first.title : clue.title || '' });
      const authors = el('input', { type: 'text', value: first ? first.authors.join(' & ') : clue.author || '' });
      let picked = first || null;
      const save = async (ev) => {
        ev.preventDefault();
        const btn = ev.target.querySelector('button[type=submit]');
        btn.disabled = true;
        const same = picked && picked.title === title.value && picked.authors.join(' & ') === authors.value;
        try {
          const done = await api('POST', '/api/admin/book-details/' + b.id, { title: title.value, authors: authors.value,
            synopsis: same ? picked.synopsis : '', publisher: same ? picked.publisher : '', cover: same ? picked.cover : '' });
          toast(done.message);
          row.remove();
        } catch (e) { if (!(e instanceof Stale)) oops(e); btn.disabled = false; }
      };
      const clues = [...r.clues.isbns.map((c) => 'ISBN ' + c.isbn + ' ' + c.where), ...r.clues.titles.map((c) => '"' + c.title + '"' + (c.author ? ' by ' + c.author : '') + ' from ' + c.where)];
      fill(area,
        el('p', { class: 'small dim', text: clues.length ? 'Clues: ' + clues.join(' · ') : 'The file gave no clues; type the details in below.' }),
        r.errors.length ? el('p', { class: 'small bad', text: 'A catalogue did not answer: ' + r.errors[0] }) : null,
        r.candidates.length ? el('div', { class: 'list' }, r.candidates.map((c) => el('div', { class: 'row', style: 'gap:12px;align-items:flex-start' },
          c.cover ? el('img', { class: 'thumb', src: c.cover, alt: '', loading: 'lazy' }) : el('span', { class: 'thumb' }),
          el('div', { class: 'stack', style: 'gap:2px;flex:1;min-width:0' }, el('strong', { class: 'book', text: c.title }),
            el('span', { class: 'small', text: [c.authors.join(' & '), c.year, c.publisher].filter(Boolean).join(' · ') }),
            el('span', { class: 'small dim', text: c.why })),
          el('button', { class: 'btn sm', type: 'button', text: 'Use this', 'aria-label': 'Use this: ' + c.title,
            onclick: () => { picked = c; title.value = c.title; authors.value = c.authors.join(' & '); title.focus(); } }))))
          : el('p', { class: 'small dim', text: 'No catalogue knew it. Type the details in, or leave it as it is.' }),
        el('form', { class: 'row end', style: 'gap:8px', onsubmit: save }, field('Title', title), field('Authors (separate with &)', authors),
          el('button', { class: 'btn pri', type: 'submit', text: 'Save to the library' })));
    }
    const row = el('div', { class: 'stack', style: 'gap:8px' },
      el('div', { class: 'row between', style: 'gap:8px' },
        el('div', { class: 'stack', style: 'gap:2px;min-width:0;flex:1 1 240px' }, el('strong', { class: 'book', text: b.title }),
          el('span', { class: 'small dim book', text: (b.author || 'no author') + ' · ' + (b.formats.join(', ') || 'no file') + ' · ' + b.problems.join(', ').toLowerCase() })),
        el('div', { class: 'row', style: 'gap:6px' }, find,
          el('a', { class: 'btn sm', href: '#/book/' + b.id, text: 'Open' }),
          el('button', { class: 'btn sm', type: 'button', text: 'It is right', 'aria-label': 'Leave as it is: ' + b.title,
            onclick: () => api('POST', '/api/admin/book-details/' + b.id, { leave: true }).then(() => { toast('Left as it is.'); row.remove(); }).catch(oops) }))),
      area);
    return row;
  }
  const PAGE = 40;
  let shown = PAGE;
  const list = el('div', { class: 'list' });
  const more = el('button', { class: 'btn', type: 'button', text: 'Show more', onclick: () => { shown += PAGE; draw(); } });
  function draw() {
    fill(list, d.books.slice(0, shown).map(bookRow));
    more.hidden = shown >= d.books.length;
  }
  draw();
  setMain(back('#/admin/books', 'Back to books admin'),
    el('div', {}, el('h1', { class: 'glow', text: 'Books without real details' }),
      el('p', { class: 'dim', text: 'Titles that are file names and authors that are user names. Find details reads the file for an ISBN and its own title, then asks Open Library '
        + '(and Google Books, when its key is saved). Saving changes the title and author in the library through the library manager, after a copy of the library database. '
        + 'Reading progress, tags and ratings stay with the book. Afterwards, Copies of the same book may find more spare copies.' })),
    d.blocked ? el('p', { class: 'bad', text: d.blocked }) : null,
    el('section', { class: 'panel cut' }, head(plural(d.books.length, 'book') + ' to look at', el('button', { class: 'btn sm', type: 'button', text: 'Look again', onclick: again })),
      d.books.length ? list : el('p', { class: 'small dim', text: 'Every book has a real title and author.' }), more));
}

function copiesCard(c) {
  if (!c.sets && !c.apart) return null;
  return el('section', { class: 'panel cut' }, head('Copies of the same book', el('a', { class: 'btn', href: '#/copies', text: 'See the copies' })),
    el('p', { class: 'small dim', text: c.hidden ? (c.hidden === 1 ? '1 library entry is a second copy of a book and is' : c.hidden + ' library entries are second copies of a book and are') + ' shown together with it, as one book. Nothing was removed from the library.' : 'Some library entries share a title and author but are not shown as one book.' }),
    el('div', { class: 'row' }, el('span', { class: 'tag', text: 'Books with copies · ' + c.sets }),
      c.spare ? el('span', { class: 'tag', text: 'Space the copies take · ' + fileSize(c.spare) }) : null,
      c.apart ? el('span', { class: 'tag', text: 'Same name, kept apart · ' + c.apart }) : null));
}

// Books on people's Goodreads shelves that the library lacks. The one place that says whose they are.
function elsewhereCard(count) {
  if (!count) return null;
  const list = el('div', { class: 'scroll', hidden: true });
  const show = el('button', { class: 'btn', type: 'button', text: 'Show them', onclick: async () => {
    try {
      const d = await api('GET', '/api/admin/elsewhere');
      fill(list, el('table', { style: 'min-width:640px' },
        el('thead', {}, el('tr', {}, ['Book', 'Author', 'On whose Goodreads', ''].map((h) => el('th', { scope: 'col', text: h })))),
        el('tbody', {}, d.books.map((b) => el('tr', {}, el('th', { scope: 'row', style: 'font-weight:400', text: b.title }), el('td', { text: b.author }), el('td', { text: b.people.join(', ') }),
          el('td', {}, el('a', { class: 'btn', href: '#/discover/' + encodeURIComponent(findQuery(b)), 'aria-label': 'Find the EPUB of ' + b.title, text: 'Find the EPUB' })))))));
      list.hidden = false; show.hidden = true;
    } catch (e) { oops(e); }
  } });
  return el('section', { class: 'panel cut' }, head('On Kindle, not in the library', show),
    el('p', { class: 'small dim', text: plural(count, 'book') + ' on the family\'s Goodreads shelves ' + (count === 1 ? 'is' : 'are') + ' not in the library. Everyone signed in sees them as "Available on Kindle"; only this list says whose they are. Guests do not see them.' }),
    list);
}

// Removing spare copies from the library for good. The one job where Family Hub asks the library manager to change the library.
const SPARE_SORTS = [
  ['identical', 'The very same file twice', 'The spare entry is removed. Nothing is set aside, because the kept file is identical byte for byte.', 'Remove'],
  ['duplicate', 'The same book, the same details', 'File sizes are within 10% and neither entry says more than the other. The original is kept over a converted copy; the spare is set aside, then removed.', 'Remove'],
  ['merge', 'The same book, one entry says more', 'File sizes are within 10%. The entry with the better file is kept, any description, tags, series or publisher it lacks is copied onto it, and the spare is set aside, then removed.', 'Merge'],
];
function spareEntry(b, extra) {
  const files = b.files.length ? b.files.map((f) => f.format + ' ' + fileSize(f.size)).join(' · ') : 'no book file';
  return el('div', { class: 'row between' },
    el('span', {}, b.kept ? el('span', { class: 'tag on', text: 'Keep' }) : el('span', { class: 'tag', text: 'Spare' }), ' ',
      b.files.length ? el('a', { href: '/api/books/' + b.id + '/download/' + b.files[0].format, text: files }) : el('span', { class: 'dim', text: files }),
      el('span', { class: 'small dim', text: (b.converted ? ' · converted copy' : '') + ' · added ' + niceDate(b.added) + ' · ' + (b.details.length ? b.details.join(', ') : 'no details') })),
    el('span', { class: 'row', style: 'gap:8px' }, S.cwa_url ? el('a', { class: 'small', href: S.cwa_url + '/book/' + b.id, target: '_blank', rel: 'noopener', text: 'Open in library manager' }) : null, extra || null));
}
function spareCopiesCard(d, again) {
  const run = (body, sure) => { if (!window.confirm(sure)) return; api('POST', '/api/admin/duplicates', body).then(() => { toast('Started. This page follows it.'); again(); }).catch(oops); };
  const busy = d.job.running;
  if (busy) setTimeout(() => { if (location.hash.startsWith('#/copies')) again(); }, 3000);
  const of = (cls) => d.sets.filter((s) => s.cls === cls);
  const review = of('review');
  const setBlock = (s, button) => el('div', { class: 'stack', style: 'gap:6px' },
    el('div', { class: 'row between' }, el('span', {}, el('strong', { text: s.title }), el('span', { class: 'small dim', text: ' · ' + (s.author || 'no author') + (s.copy.length ? ' · copies across: ' + s.copy.map((x) => x.label).join(', ') : '') })), button),
    s.why ? el('p', { class: 'small warn', text: s.why }) : null,
    el('div', { class: 'list' }, s.books.map((b) => spareEntry(b, s.cls === 'review' && !d.blocked && !busy ? el('button', { class: 'btn', type: 'button', 'aria-label': 'Keep this entry of ' + s.title + ' (' + (b.files[0] ? b.files[0].format + ' ' + fileSize(b.files[0].size) : 'no file') + ')', text: 'Keep this one',
      onclick: () => run({ key: s.key, keep: b.id }, 'Keep this entry of "' + s.title + '" and remove the other ' + plural(s.books.length - 1, 'entry', 'entries') + '? Their files are set aside in ' + d.set_aside + ' first.') }) : null))));
  return el('section', { class: 'panel cut' }, head('Remove spare copies from the library'),
    el('p', { class: 'small dim', text: 'This is the one place where Family Hub changes the library, and it does it by asking the library manager. The library\'s database is copied first, every time. A spare is only deleted outright when the kept file is identical; otherwise its files are moved to ' + d.set_aside + ' for you to empty when you are satisfied.' }),
    d.blocked ? el('p', { class: 'warn', role: 'status', text: d.blocked }) : null,
    busy ? el('p', { class: 'ok', role: 'status', text: 'Working: ' + d.job.done + ' of ' + d.job.total + ' done…' }) : null,
    !busy && d.job.finished ? el('p', { class: 'small ' + (d.job.problems.length ? 'bad' : 'ok'), role: 'status', text: 'Last run: ' + d.job.done + ' of ' + d.job.total + ' done' + (d.job.problems.length ? ', ' + plural(d.job.problems.length, 'problem') + '.' : ', no problems.') + (d.job.backup ? ' Database copy: ' + d.job.backup : '') }) : null,
    d.job.problems.length ? el('ul', { class: 'small bad' }, d.job.problems.map((p) => el('li', { text: p }))) : null,
    SPARE_SORTS.map(([cls, title, what, verb]) => {
      const sets = of(cls);
      return el('div', { class: 'stack', style: 'gap:8px' },
        el('div', { class: 'row between' }, el('h3', { text: title + ' · ' + sets.length }),
          sets.length ? el('button', { class: 'btn pri', type: 'button', disabled: d.blocked || busy ? '' : null, text: verb + ' all ' + sets.length,
            onclick: () => run({ cls }, verb + ' all ' + plural(sets.length, 'set') + ' of this sort? ' + what) }) : null),
        el('p', { class: 'small dim', text: what }),
        sets.length ? el('details', {}, el('summary', { text: 'See the ' + plural(sets.length, 'set') + ', or do them one at a time' }),
          el('div', { class: 'list', style: 'margin-top:12px' }, sets.map((s) => setBlock(s, el('button', { class: 'btn', type: 'button', disabled: d.blocked || busy ? '' : null, 'aria-label': verb + ': ' + s.title, text: verb + ' this one',
            onclick: () => run({ key: s.key }, verb + ' the spare of "' + s.title + '"?') }))))) : null);
    }),
    el('div', { class: 'stack', style: 'gap:8px' }, el('h3', { text: 'For you to decide · ' + review.length }),
      el('p', { class: 'small dim', text: 'The files differ in size by more than 10%, an entry has no file, the names are doubtful, or a detail would be lost. Open each file if you need to, then keep one. If they are different books, press "They are different books" and they leave this list.' }),
      review.length ? el('div', { class: 'list' }, review.map((s) => setBlock(s, el('button', { class: 'btn', type: 'button', 'aria-label': 'They are different books: ' + s.title, text: 'They are different books',
        onclick: () => api('POST', '/api/admin/copies', { key: s.key, choice: 'apart' }).then(() => { toast('Kept as separate books.'); again(); }).catch(oops) })))) : el('p', { class: 'small dim', text: 'Nothing waiting.' })),
    d.log.length ? el('details', {}, el('summary', { text: 'What has been done (' + d.log.length + (d.log.length === 30 ? ' most recent' : '') + ')' }),
      el('div', { class: 'list', style: 'margin-top:12px' }, d.log.map((l) => el('div', { class: 'row between' }, el('span', { text: l.title }),
        el('span', { class: 'small dim', text: ({ identical: 'identical spare removed', duplicate: 'spare set aside and removed', merge: 'merged', review: 'your choice' }[l.action] || l.action) + (l.note ? ' (' + l.note + ' copied)' : '') + ' · ' + niceDate(l.at) }))))) : null);
}

async function pageCopies() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const [c, d] = await Promise.all([api('GET', '/api/admin/copies'), api('GET', '/api/admin/duplicates')]);
  const again = () => pageCopies().catch(oops);
  const choose = (key, choice, msg) => api('POST', '/api/admin/copies', { key, choice }).then(() => { toast(msg); again(); }).catch(oops);
  const one = c.sets.filter((s) => s.together), apart = c.sets.filter((s) => !s.together);
  const files = (b) => (b.files.length ? b.files.map((f) => f.format + ' ' + fileSize(f.size)).join(' · ') : 'no book file');
  const entry = (b) => el('div', { class: 'row between' },
    el('span', {}, b.files.length ? el('a', { href: '/api/books/' + b.id + '/download/' + b.files[0].format, text: files(b) }) : el('span', { class: 'dim', text: files(b) }),
      el('span', { class: 'small dim', text: ' · added ' + niceDate(b.added) + (b.has_cover ? '' : ' · no cover') })),
    el('span', { class: 'row', style: 'gap:8px' }, b.shown ? el('span', { class: 'tag', text: 'Shown' }) : null,
      S.cwa_url ? el('a', { class: 'small', href: S.cwa_url + '/book/' + b.id, target: '_blank', rel: 'noopener', text: 'Open in library manager' }) : null));
  const block = (s, button) => el('div', { class: 'stack', style: 'gap:6px' },
    el('div', { class: 'row between' }, el('span', {}, el('strong', { text: s.title }), el('span', { class: 'small dim', text: ' · ' + (s.author || 'no author') + (s.choice ? ' · your choice' : '') })), button),
    el('div', { class: 'list' }, s.books.map(entry)));
  setMain(back('#/admin/books', 'Back to books admin'),
    el('div', {}, el('h1', { class: 'glow', text: 'Copies of the same book' }), el('p', { class: 'dim', text: 'The library holds ' + c.entries + ' entries for ' + plural(c.books, 'book') + '. Entries with the same title and author are shown as one book. Family Hub changes nothing in the library: every copy stays where it is, and can be downloaded here.' })),
    spareCopiesCard(d, again),
    head('How Family Hub shows them meanwhile'),
    el('section', { class: 'panel cut' }, head(plural(one.length, 'book') + ' shown as one'),
      el('p', { class: 'small dim', text: 'The copy marked Shown is the one people download: the one with a cover, an EPUB and the larger file. A format that only another copy has is offered too. To remove spare copies for good, use the card above, or delete one by hand in the library manager.' }),
      one.length ? el('div', { class: 'list' }, one.map((s) => block(s, el('button', { class: 'btn', type: 'button', text: 'Keep apart', 'aria-label': 'Keep apart: ' + s.title, onclick: () => choose(s.key, 'apart', 'Shown as separate books.') }))))
        : el('p', { class: 'small dim', text: 'No book is in the library twice.' })),
    apart.length ? el('section', { class: 'panel cut' }, head('Same name, kept apart'),
      el('p', { class: 'small dim', text: 'These share a title and author, but the title looks like a file name or the author like a user name, and the files differ. They may be different documents, so each is listed by itself. Entries holding exactly the same file are still shown as one.' }),
      el('div', { class: 'list' }, apart.map((s) => block(s, el('div', { class: 'row' },
        el('button', { class: 'btn', type: 'button', text: 'Show as one', 'aria-label': 'Show as one: ' + s.title, onclick: () => choose(s.key, 'together', 'Shown as one book.') }),
        s.choice ? el('button', { class: 'btn', type: 'button', text: 'Let Family Hub decide', 'aria-label': 'Let Family Hub decide: ' + s.title, onclick: () => choose(s.key, null, 'Back to the usual rule.') }) : null))))) : null);
}

// The nightly copy to a second disk: when it last ran, what it did, and a button to run it now.
function secondCopyLines(c, reload) {
  const title = el('h3', { text: 'Copy to a second disk' });
  if (!c.configured) return el('div', { class: 'stack', style: 'gap:6px' }, title, el('p', { class: 'small dim', text: 'Not set up. Every night Family Hub can copy its data, the settings of the other services, the book library and the chapters to another disk. Guide 6 has the steps.' }));
  const l = c.last;
  let line = el('p', { class: 'small dim', text: 'No copy has been made yet. The first runs tonight.' });
  if (l && l.at) line = el('p', { class: 'small ' + (l.ok ? '' : 'bad'), text: 'Last copy: ' + niceDate(l.at) + ' (' + ago(l.at).toLowerCase() + '). ' + (l.error ? l.error : plural(l.copied, 'file') + ' copied (' + fileSize(l.bytes) + '), ' + l.unchanged + ' unchanged, ' + l.set_aside + ' older ' + (l.set_aside === 1 ? 'version' : 'versions') + ' kept aside.') });
  return el('div', { class: 'stack', style: 'gap:6px' }, title, line,
    el('p', { class: 'small dim', text: (c.on ? 'Runs every night.' : 'Switched off under Connections.') + (c.free ? ' ' + (c.free / 1e9).toFixed(1) + ' GB free on the second disk.' : '') }),
    el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', disabled: c.running ? '' : null, text: c.running ? 'Copying now…' : 'Copy now', onclick: async (e) => {
      try { await api('POST', '/api/admin/second-copy'); e.target.disabled = true; e.target.textContent = 'Copying now…'; toast('Copying. A large library takes a while the first time; reload this page to see the result.', false, 8000); } catch (err) { oops(err); }
    } })));
}

function attentionCard(a, reload) {
  const t = a.attention;
  if (!t.total && !t.processed) return null;
  const size = fileSize;
  return el('section', { class: 'panel cut' }, head('Needs attention', t.total ? el('button', { class: 'btn', type: 'button', text: 'Try these again', onclick: async () => {
      try { const r = await api('POST', '/api/admin/attention/retry'); toast(plural(r.delivered, 'file') + ' sent to the library, ' + r.attention + ' still set aside.'); reload(); } catch (e) { oops(e); }
    } }) : null),
    el('p', { class: 'small dim', text: t.total ? plural(t.total, 'file') + ' could not be added and ' + (t.total === 1 ? 'was' : 'were') + ' moved to the needs-attention folder. Nothing was deleted.' : 'Nothing is waiting.' }),
    t.items.length ? el('div', { class: 'scroll' }, el('table', {},
      el('thead', {}, el('tr', {}, ['File', 'Why', 'Size'].map((h, i) => el('th', { scope: 'col', class: i === 2 ? 'num' : '', text: h })))),
      el('tbody', {}, t.items.map((i) => el('tr', {}, el('th', { scope: 'row', style: 'font-weight:400', text: i.path }), el('td', { text: i.reason }), el('td', { class: 'num', text: size(i.size) })))))) : null,
    t.total > t.items.length ? el('p', { class: 'small dim', text: 'Showing the newest ' + t.items.length + '.' }) : null,
    t.processed ? el('p', { class: 'small dim', text: plural(t.processed, 'original file') + ' (unpacked archives, covers, loose comic pages) kept in needs-attention/_processed. They are already in the library and safe to remove.' }) : null);
}

// ------------------------------------------------------- sort the library

async function pageSort() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const f = { kind: 'Unsorted', q: '' };
  const picked = new Set();
  const table = el('div', { class: 'scroll' });
  const count = el('div', { class: 'dim', role: 'status' });
  const kindRow = el('div', { class: 'row', role: 'group', 'aria-label': 'Kind to show' });
  const toKind = el('select', {}, el('option', { value: '', text: 'Leave as it is' }), S.kinds.map((k) => el('option', { value: k, text: k })));
  const tag = el('input', { type: 'text', maxlength: 60 });
  let shown = [];

  async function load() {
    const data = await api('GET', '/api/books?' + qs({ filter: 'library', kinds: f.kind, q: f.q }));
    shown = data.books;
    picked.forEach((id) => { if (!shown.some((b) => b.id === id)) picked.delete(id); });
    drawTable();
  }
  function drawTable() {
    count.textContent = plural(shown.length, 'book') + ' · ' + picked.size + ' selected';
    const all = el('input', { type: 'checkbox', 'aria-label': 'Select all', checked: shown.length > 0 && picked.size === shown.length, onchange: (e) => { shown.forEach((b) => (e.target.checked ? picked.add(b.id) : picked.delete(b.id))); drawTable(); } });
    fill(table, shown.length ? el('table', { style: 'min-width:720px' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, all), ['Title', 'Author', 'Formats', 'Family tags'].map((t) => el('th', { scope: 'col', text: t })))),
      el('tbody', {}, shown.map((b) => el('tr', {},
        el('td', {}, el('input', { type: 'checkbox', 'aria-label': 'Select ' + b.title, checked: picked.has(b.id), onchange: (e) => { if (e.target.checked) picked.add(b.id); else picked.delete(b.id); drawTable(); } })),
        el('th', { scope: 'row', style: 'font-weight:400' }, el('a', { href: '#/book/' + b.id, text: b.title })),
        el('td', { text: b.author }), el('td', { text: b.formats.join(' · ') }), el('td', { class: 'small', text: b.tags.family.join(', ') })))))
      : el('p', { class: 'dim', text: 'No books of this kind.' }));
  }
  function draw() {
    fill(kindRow, ...S.kinds.map((k) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(f.kind === k), text: KIND_LABEL[k] || k, onclick: () => { f.kind = k; picked.clear(); draw(); } })));
    load().catch(oops);
  }
  const search = el('input', { type: 'search', oninput: debounce((e) => { f.q = e.target.value; load().catch(oops); }, 250) });
  setMain(
    el('div', { class: 'row between end' },
      el('div', {}, el('h1', { class: 'glow', text: 'Sort the library' }), el('p', { class: 'dim', text: 'Tick the books, then choose their kind or add a family tag such as a series or game edition.' })),
      field('Search title, author or tag', search)),
    back('#/admin/books', 'Back to books admin'), kindRow,
    el('form', { class: 'panel cut row end', onsubmit: async (e) => {
      e.preventDefault();
      if (!picked.size) return toast('Tick some books first.', true);
      try {
        const r = await api('POST', '/api/books/kind', { ids: Array.from(picked), kind: toKind.value, tag: tag.value });
        toast(plural(r.updated, 'book') + ' updated.'); picked.clear(); tag.value = ''; load().catch(oops);
      } catch (err) { oops(err); }
    } }, field('Set kind to', toKind), field('Add family tag (optional)', tag), el('button', { class: 'btn pri', type: 'submit', text: 'Apply to selected' })),
    count, table);
  draw();
}

// -------------------------------------------------------------- discover

async function pageDiscover(prefill) {
  const results = el('div', { class: 'stack' });
  const q = el('input', { type: 'search', value: prefill ? decodeURIComponent(prefill) : '' });
  const size = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');

  async function search() {
    if (!q.value.trim()) return;
    const stop = searchProgress(results, q.value.trim(), { typical: 20, what: 'Asking the torrent sites' });
    try {
      const data = await api('GET', '/api/discover/search?' + qs({ q: q.value })).finally(stop);
      if (!data.results.length) { fill(results, el('p', { class: 'dim', role: 'status', text: 'No results. Try fewer words, or check your indexers in Prowlarr.' })); return; }
      fill(results, el('p', { class: 'small dim', role: 'status', text: plural(data.results.length, 'result') }),
        el('div', { class: 'scroll' }, el('table', { style: 'min-width:720px' },
          el('thead', {}, el('tr', {}, ['Title', 'Format', 'Size', 'Indexer', 'Seeders', 'Action'].map((t, i) => el('th', { scope: 'col', class: i === 2 || i === 4 ? 'num' : '', text: t })))),
          el('tbody', {}, data.results.map((r) => {
            const btn = el('button', { class: 'btn pri', type: 'button', text: r.format === 'PDF' ? 'Get (PDF)' : 'Download', onclick: async () => {
              btn.disabled = true;
              try {
                await api('POST', '/api/discover/grab', { guid: r.guid, indexer_id: r.indexer_id, title: r.title, indexer: r.indexer });
                btn.textContent = 'Sent';
                Tracker.follow('book', r.title);
                toast('Sent to qBittorrent. Follow it in the corner, or in Downloads.');
                loadQueue();
              } catch (e) { btn.disabled = false; oops(e); }
            } });
            return el('tr', {}, el('th', { scope: 'row', style: 'font-weight:400', text: r.title }), el('td', { text: r.format || '?' }), el('td', { class: 'num', text: size(r.size) }),
              el('td', { text: r.indexer }), el('td', { class: 'num', text: r.seeders === null || r.seeders === undefined ? '–' : r.seeders }),
              el('td', {}, r.library_id ? el('a', { class: 'ok', href: '#/book/' + r.library_id, text: 'In library' }) : btn));
          })))));
    } catch (e) { fill(results, el('p', { class: 'bad', role: 'status', text: e.message })); }
  }

  const queue = el('section', { class: 'panel cut' });
  async function loadQueue() {
    const d = await api('GET', '/api/discover/queue');
    fill(queue, head('Downloads', el('a', { class: 'btn sm', href: '#/downloads', text: 'See every download' })),
      el('p', { class: 'small dim', text: 'Progress, pausing, the order things download in and deleting are all on the Downloads page.' }),
      el('h3', { text: 'Recent requests' }),
      d.requests.length ? el('div', { class: 'list' }, d.requests.slice(0, 6).map((r) => el('div', { class: 'row between' }, el('span', { text: r.title }), el('span', { class: 'small dim', text: 'by ' + r.by + ' · ' + ago(r.created).toLowerCase() })))) : el('p', { class: 'small dim', text: 'No requests yet.' }),
      el('h3', { text: 'Added to the library' }),
      d.imported.length ? el('div', { class: 'list' }, d.imported.slice(0, 6).map((r) => el('div', { class: 'row between' }, el('span', { text: r.name }), el('span', { class: 'small ok', text: ago(r.at) })))) : el('p', { class: 'small dim', text: 'Nothing imported yet.' }));
  }

  // Unread books already in the library, closest to what this person has enjoyed first.
  const likeBox = el('section', { class: 'panel cut' });
  function loadLike() {
    return api('GET', '/api/books?filter=foryou').then((d) => {
      let body;
      if (!d.taste.ready) body = el('p', { class: 'small dim', text: 'Finish or rate ' + plural(d.taste.need - d.taste.have, 'more book') + ' and this fills with unread books like the ones you enjoyed. You have ' + d.taste.have + ' so far.' });
      else if (!d.books.length) body = el('p', { class: 'small dim', text: 'Nothing unread in the library resembles what you have enjoyed yet. Tags help: see Admin, Books, Tidy tags.' });
      else body = el('div', { class: 'shelf' }, d.books.slice(0, 12).map((b) => bookCard(b, () => loadLike().catch(oops))));
      fill(likeBox, head("In the library: similar to what I've read"),
        el('p', { class: 'small dim', text: 'Books you have not started, closest to the ones you finished or rated well first.' }), body);
    });
  }
  loadLike().catch(oops);

  // Books the library does not have, picked once a week from what this person has enjoyed.
  const recBox = el('section', { class: 'panel cut' });
  function drawRecs(d) {
    const top = head('Suggested for you this week', d.ready && d.on ? el('button', { class: 'btn', type: 'button', text: 'Suggest again', onclick: (e) => { e.target.disabled = true; e.target.textContent = 'Asking Open Library…'; api('POST', '/api/recommendations/refresh').then(drawRecs).catch((err) => { oops(err); drawRecs(d); }); } }) : null);
    let body;
    if (!d.ready) body = el('p', { class: 'small dim', text: 'Finish or rate ' + plural(d.need - d.have, 'more book') + ' and Family Hub will suggest books the library does not have yet. You have ' + d.have + ' so far.' });
    else if (!d.on) body = el('p', { class: 'small dim', text: 'Suggestions come from Open Library, and looking things up outside is switched off under Connections.' });
    else if (!d.books.length) body = el('p', { class: 'small dim', text: d.updated ? 'Nothing new this week. Rating more books gives Family Hub more to go on.' : 'Your first suggestions arrive within the hour, or press Suggest again.' });
    else body = el('div', { class: 'shelf' }, d.books.map((b) => el('div', { class: 'book' },
      el('div', { class: 'cover' + (b.cover_id ? ' has-img' : '') + titleSize(b.title), style: '--cv:' + coverColor(b.title) + (b.cover_id ? ';background-image:url(/api/covers/preview?' + qs({ url: 'https://covers.openlibrary.org/b/id/' + b.cover_id + '-M.jpg' }) + ')' : '') },
        el('span', { class: 'fmt', text: 'NOT IN LIBRARY' }), el('span', { class: 't', style: 'text-decoration:none', text: b.title })),
      el('div', { class: 'small dim', text: b.author + (b.year ? ' · ' + b.year : '') }),
      el('div', { class: 'small', text: b.reason }),
      canRequest() ? el('button', { class: 'btn pri', type: 'button', 'aria-label': 'Find it: ' + b.title, text: 'Find it', onclick: () => { q.value = findQuery(b); search(); window.scrollTo(0, 0); } }) : null,
      el('button', { class: 'btn', type: 'button', 'aria-label': 'Not for me: ' + b.title, text: 'Not for me', onclick: () => api('POST', '/api/recommendations/dismiss', { key: b.key }).then(() => api('GET', '/api/recommendations')).then(drawRecs).catch(oops) }))));
    fill(recBox, top,
      el('p', { class: 'small dim', text: 'Books the library does not have, by authors and on subjects you have rated highly. A new list is made each week' + (d.updated ? '; this one is from ' + niceDate(d.updated) + '.' : '.') }), body);
  }

  const simBox = el('div', {});
  const read = (await api('GET', '/api/books?filter=read')).books;
  let pick = read[0];
  async function drawSimilar() {
    if (!pick) { fill(simBox, el('p', { class: 'small dim', text: 'Finish a book and it will appear here.' })); return; }
    const d = await api('GET', '/api/similar-by-key?' + qs({ key: pick.key }));
    fill(simBox, el('div', { class: 'row', role: 'group', 'aria-label': "Books you've read" }, read.slice(0, 12).map((b) =>
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(b.key === pick.key), text: b.title, onclick: () => { pick = b; drawSimilar().catch(oops); } }))),
      similarList('Like ' + pick.title, d.books.filter((b) => !b.already_read)));
  }

  const watchBox = el('div', { class: 'stack' }), newBox = el('div', { class: 'stack' });
  const wKind = el('select', {}, el('option', { value: 'author', text: 'Author' }), el('option', { value: 'tag', text: 'Genre or tag' }));
  const wValue = el('input', { type: 'text' });
  async function loadWatch(refresh) {
    const [w, n] = await Promise.all([api('GET', '/api/watch'), api('GET', '/api/discover/new' + (refresh ? '?refresh=1' : ''))]);
    fill(watchBox, w.watch.length ? el('div', { class: 'list' }, w.watch.map((x) => el('div', { class: 'row between' },
      el('span', {}, el('strong', { text: x.value }), el('span', { class: 'small dim', text: x.kind === 'author' ? ' · author' : ' · tag' })),
      el('button', { class: 'btn', type: 'button', text: 'Stop following', 'aria-label': 'Stop following ' + x.value, onclick: () => api('DELETE', '/api/watch/' + x.id).then(() => loadWatch(true)).catch(oops) }))))
      : el('p', { class: 'small dim', text: 'Follow an author or a tag to get suggestions here.' }));
    fill(newBox, n.items.length ? el('div', { class: 'list' }, n.items.slice(0, 12).map((i) => el('div', { class: 'row between' },
      el('div', {}, el('strong', { text: i.title }), el('div', { class: 'small dim', text: i.reason + (i.author && !i.release ? ' · ' + i.author : '') })),
      i.in_library ? el('a', { class: 'btn', href: '#/book/' + i.id, text: 'In library · open' })
        : (canRequest() ? el('button', { class: 'btn pri', type: 'button', text: 'Find it', onclick: () => { q.value = i.release ? i.title : i.title + ' ' + i.author; search(); window.scrollTo(0, 0); } }) : null))))
      : el('p', { class: 'small dim', text: 'Nothing new right now.' }));
  }

  setMain(
    el('div', {}, el('h1', { class: 'glow', text: 'Discover' }), el('p', { class: 'dim', text: 'Find new books, send them to the download client, and follow authors and tags.' })),
    canRequest() ? el('section', { class: 'panel cut' }, head('Search for a book'),
      S.discover_configured ? null : el('p', { class: 'warn' }, 'Book search is not set up yet. ', isAdmin() ? el('a', { href: '#/admin', text: 'Add the Prowlarr address and API key under Admin, Hub, Connections.' }) : 'Ask an admin to add the Prowlarr address and API key.'),
      el('form', { class: 'row end', onsubmit: (e) => { e.preventDefault(); search(); } }, field('Title or author', q, 'wide'), el('button', { class: 'btn pri', type: 'submit', text: 'Search' })),
      results) : el('p', { class: 'dim', text: 'This profile can browse suggestions but cannot download new books. Ask an admin to find one for you.' }),
    likeBox, recBox,
    el('section', { class: 'panel cut' }, head("Similar to books you've read"), el('p', { class: 'small dim', text: 'Matches share tags or an author with the book you pick.' }), simBox),
    el('div', { class: 'cols' },
      canRequest() ? queue : null,
      el('section', { class: 'panel cut' }, head('New for you', el('button', { class: 'btn', type: 'button', text: 'Check again', onclick: () => loadWatch(true).catch(oops) })), newBox)),
    el('section', { class: 'panel cut' }, head('Your watchlist'), watchBox,
      el('form', { class: 'row end', onsubmit: async (e) => { e.preventDefault(); try { await api('POST', '/api/watch', { kind: wKind.value, value: wValue.value }); wValue.value = ''; loadWatch(true); } catch (err) { oops(err); } } },
        field('Follow', wKind), field('Name', wValue), el('button', { class: 'btn', type: 'submit', text: 'Follow' }))));
  drawSimilar().catch(oops);
  api('GET', '/api/recommendations').then(drawRecs).catch(oops);
  loadWatch().catch(oops);
  if (canRequest()) { loadQueue().catch(oops); if (prefill) search(); }
}
