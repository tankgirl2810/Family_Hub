/* Family Hub web interface. Plain JavaScript, no build step. */
'use strict';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const COVER_COLORS = ['#6E2B2B', '#2F5D62', '#3F3A6B', '#6A4A1F', '#22506B', '#5E2F4A', '#3D4B2A', '#7A3B2E', '#40405A', '#2B5A45', '#5A3A6B', '#1F4F5E'];
const KIND_LABEL = { 'Fiction': 'Fiction', 'Non-fiction': 'Non-fiction', 'Graphic novel': 'Graphic novels', 'Tabletop': 'Tabletop', 'Unsorted': 'Unsorted' };
let appState = null; // last /api/state

// ---------------------------------------------------------------- helpers

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'style') node.style.cssText = value;
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, value === true ? '' : value);
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
  let res;
  try { res = await fetch(url, opts); } catch (err) {
    if (gen !== routeGen) throw new Stale('moved on');
    throw new Error('Family Hub could not be reached. Check this device is on the home network, then try again.');
  }
  let data = null;
  try { data = await res.json(); } catch (err) { /* no body */ }
  // Moved to another page meanwhile: a save still happened, but nothing may redraw the old page over the new one.
  if (gen !== routeGen) throw new Stale('moved on');
  if (!res.ok && !(data && data.error) && res.status >= 502 && res.status <= 504) throw new Error('Family Hub is not answering (' + res.status + '). It may be restarting; try again in a minute.');
  if (!res.ok) throw new Error((data && data.error) || 'Something went wrong (' + res.status + '). Try again, or reload the page.');
  return data;
}

// replaceChildren would print a missing (null) part as the word "null"; this leaves it out.
const fill = (node, ...kids) => redraw(node, kids.flat().filter((kid) => kid !== null && kid !== undefined && kid !== false));

// Redrawing a page or a panel throws away the button that was pressed, and focus fell back to the top of the
// document, so a keyboard or screen reader user started again from the sidebar after every star or "Mark read".
// Before a redraw note what had focus (its words, and which of the same words it was); afterwards focus the
// new copy. A button whose words change when pressed ("Follow" to "Stop following") is found by its place instead.
let focusWanted = null;
const FOCUSABLE = 'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';
const focusWords = (node) => (node.getAttribute('aria-label') || node.textContent || node.name || '').trim().slice(0, 120);
function rememberFocus(node) {
  const active = document.activeElement;
  if (!active || active === document.body || active === node || !node.contains(active)) return;
  const all = [...main().querySelectorAll(FOCUSABLE)];
  const words = focusWords(active);
  const same = all.filter((candidate) => candidate.tagName === active.tagName && focusWords(candidate) === words);
  focusWanted = { tag: active.tagName, words, nth: same.indexOf(active), place: all.indexOf(active), at: Date.now() };
}
function restoreFocus() {
  const wanted = focusWanted;
  if (!wanted || (document.activeElement && document.activeElement !== document.body)) return;
  if (Date.now() - wanted.at > 15000) { focusWanted = null; return; }
  const all = [...main().querySelectorAll(FOCUSABLE)];
  if (!all.length) return;  // still "Loading…": try again when the page is drawn
  const same = all.filter((candidate) => candidate.tagName === wanted.tag && focusWords(candidate) === wanted.words);
  const pick = same[Math.max(0, Math.min(wanted.nth, same.length - 1))] || all[Math.min(wanted.place, all.length - 1)];
  focusWanted = null;
  if (pick) pick.focus({ preventScroll: true });
}
function redraw(node, kids) {
  rememberFocus(node);
  node.replaceChildren(...kids);
  restoreFocus();
}

// A notice goes by itself after a few seconds (held while the pointer or focus is on it). A problem stays until
// it is closed or the next notice replaces it: an error that vanished before it was read was no help.
let toastTimer = null;
function toast(message, isError, ms) {
  const box = document.getElementById('toast');
  const hide = () => { box.className = ''; box.replaceChildren(); };
  const close = isError ? el('button', { class: 'toast-x', type: 'button', 'aria-label': 'Close this message', text: '×', onclick: hide }) : null;
  box.replaceChildren(el('span', { text: message }), ...(close ? [close] : []));
  box.className = 'show' + (isError ? ' err' : '');
  clearTimeout(toastTimer);
  box.onpointerenter = box.onfocusin = box.onpointerleave = box.onfocusout = null;
  if (isError && !ms) return;
  const later = () => { toastTimer = setTimeout(hide, ms || 3000); };
  box.onpointerenter = box.onfocusin = () => clearTimeout(toastTimer);
  box.onpointerleave = box.onfocusout = later;
  later();
}
const oops = (err) => { if (!(err instanceof Stale)) toast(err.message, true); };
// "1st", "2nd", "3rd", "11th", "22nd"
const ordinal = (n) => n + ([, 'st', 'nd', 'rd'][(n % 100 - 20) % 10] || [, 'st', 'nd', 'rd'][n % 100] || 'th');
// A book just finished: where it comes in the year. The first, and every tenth, get a word more.
function finishedToast(done, title) {
  if (!done) return toast('Marked finished.');
  const whose = done.name ? done.name + "'s" : 'your';
  const extra = done.count === 1 ? ' The first of the year.' : done.count % 10 === 0 ? ' A shelf of ' + done.count + ' now.' : '';
  toast((title ? title + ': ' : 'Finished: ') + whose + ' ' + ordinal(done.count) + ' book of ' + done.year + '.' + extra, false, 5000);
}
const qs = (params) => Object.entries(params).filter(([, value]) => value !== '' && value !== null && value !== undefined)
  .map(([key, value]) => encodeURIComponent(key) + '=' + encodeURIComponent(value)).join('&');
// waits until you STOP typing for ms before calling fn, so the search box doesn't fire on every keystroke
const debounce = (fn, ms) => { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; };
const plural = (count, word, many) => count + ' ' + (count === 1 ? word : (many || word + 's'));
const niceDate = (iso) => { if (!iso) return ''; const date = new Date(iso.length === 10 ? iso + 'T00:00:00' : iso); return isNaN(date) ? iso : date.toLocaleDateString(undefined,
    { day: 'numeric', month: 'short', year: 'numeric' }); };
function ago(iso) {
  if (!iso) return 'Never';
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 2) return 'Just now';
  if (mins < 60) return mins + ' minutes ago';
  if (mins < 1440) return plural(Math.round(mins / 60), 'hour') + ' ago';
  return plural(Math.round(mins / 1440), 'day') + ' ago';
}
// Long titles get smaller type on a cover, so every cover keeps the same shape.
function titleSize(title) { const length = (title || '').length; return length > 70 ? ' t-xlong' : length > 38 ? ' t-long' : ''; }
// same title, same colour, every time: a tiny string hash (>>> 0 keeps it a positive 32-bit number)
function coverColor(title) { let hash = 0; for (const char of title) hash = (hash * 31 + char.charCodeAt(0)) >>> 0; return COVER_COLORS[hash % COVER_COLORS.length]; }
function field(labelText, input, cls) {
  const id = 'f' + Math.random().toString(36).slice(2, 9);
  input.id = id;
  return el('div', { class: 'field ' + (cls || '') }, el('label', { class: 'lab', for: id, text: labelText }), input);
}
// A panel heading: the title, the design's hatch, then anything that sits on the right.
function head(title, ...right) {
  return el('div', { class: 'ph' }, el('h2', { text: title }), el('span', { class: 'hatch' }), ...right);
}
// The top of a page: its title, and the plain words under it (text, or text with links).
function pageHead(title, ...intro) {
  return el('div', { class: 'col gap8' }, el('h1', { class: 'glow', text: title }), intro.length ? el('p', { class: 'dim' }, ...intro) : null);
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
  const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path'); shape.setAttribute('d', path); svg.append(shape);
  return svg;
}
const isGuest = () => appState.me && appState.me.guest;
const isAdmin = () => appState.me && appState.me.role === 'admin';
const canSort = () => appState.me && !appState.me.guest && (appState.me.role === 'admin' || appState.me.can_tag);
// Add a book to someone's "My books" or take it off, then say what happened. profile is for admins acting for someone else.
async function setShelf(bookId, on, profile) {
  const result = await api('POST', '/api/books/' + bookId + '/shelf', { on, profile_id: profile ? profile.id : undefined });
  const whose = profile ? profile.name + "'s books" : 'your books';
  if (on) toast('Added to ' + whose + '.' + (result.sending.length ? ' Sending to ' + result.sending.join(' and ') + '.' : ''));
  else if (result.still_on.length) toast('Removed from ' + whose + '. Reading history is kept. The copy on ' + result.still_on.join(' and ') + ' stays there until it is deleted on the device itself.',
      false, 10000);
  else toast('Removed from ' + whose + '. Reading history is kept.');
  return result;
}
const canRequest = () => appState.me && !appState.me.guest && (appState.me.role === 'admin' || appState.me.can_request);

/// ----------------------------------------------------------------- shell

const main = () => document.getElementById('main');
function setMain(...kids) { redraw(main(), kids.flat().filter(Boolean)); }

// The sections in the sidebar, with the design's line icons.
// [key, name, link, icon, short name for the phone's bottom bar]. The first four sit in the phone's bottom bar;
// the rest open from its More button.
const PHONE_BAR = ['home', 'movies', 'books', 'downloads'];
const SHORT = { movies: 'Films & TV' };
const SECTIONS = [
  ['home', 'Home', '#/home', 'M3 11l9-7 9 7v9h-6v-6H9v6H3z'],
  ['movies', 'Films and TV', '#/movies', 'M3 5h18v14H3zM7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4'],
  ['books', 'Books', '#/library', 'M4 4h7a2 2 0 0 1 1 1 2 2 0 0 1 1-1h7v15h-7a2 2 0 0 0-1 1 2 2 0 0 0-1-1H4zM12 5v15'],
  ['downloads', 'Downloads', '#/downloads', 'M12 4v11M7 10l5 5 5-5M5 20h14'],
  ['watchlist', 'Watchlist', '#/watchlist', 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 1 0 0-6'],
  ['photos', 'Photos', '#/photos', 'M3 5h18v14H3zM3 16l5-5 4 4 3-3 6 6M16 7.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 1 0 0-3'],
  ['files', 'Files', '#/files', 'M3 6h7l2 2h9v11H3z'],
  ['admin', 'Admin', '#/admin', 'M4 7h10M18 7h2M4 12h3M11 12h9M4 17h12M14 5h4v4h-4zM7 10h4v4H7zM16 15h4v4h-4z'],
];
function sectionIcon(pathData) {
  const svg = icon(pathData, 18); svg.setAttribute('stroke-width', '1.5'); svg.setAttribute('stroke-linecap', 'square'); svg.setAttribute('stroke-linejoin', 'miter');
  return svg;
}
function starMark() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', '#star'); svg.append(use);
  return svg;
}
// Which section each page belongs to, and the tabs along the top of that section.
const PAGE_SECTION = { home: 'home', movies: 'movies', downloads: 'downloads', photos: 'photos', files: 'files', admin: 'admin',
    sort: 'admin', tags: 'admin', copies: 'admin', details: 'admin', rules: 'admin', fix: 'admin', watchlist: 'watchlist' };
function sectionTabs(section) {
  if (section === 'books') {
    const tabs = [['library', 'Library', '#/library']];
    if (!isGuest()) tabs.push(['dashboard', 'Dashboard', '#/dashboard'], ['discover', 'Discover', '#/discover'], ['downloads', 'Downloads', '#/downloads']);
    return tabs;
  }
  if (section === 'admin') return [['hub', 'Hub', '#/admin'], ['books', 'Books', '#/admin/books']];
  if (section === 'movies') return mediaTabs();
  return [];
}
const SECTION_LOGO = { home: 'Home', movies: 'Films and TV', downloads: 'Downloads', watchlist: 'Watchlist', books: 'Books',
    photos: 'Photos', files: 'Files', admin: 'Admin', profiles: 'Family Hub' };

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
  const tokens = Object.fromEntries(VISUAL_TOKENS.map((name) => [name, cs.getPropertyValue(name).trim()]));
  frame.contentWindow.postMessage({ type: 'hub-look', tokens, place: (appState.visuals && appState.visuals.place) || null }, '*');
}
function sendLookToVisuals() {
  for (const frame of visualFrames) { if (frame.isConnected) sendLook(frame); else visualFrames.delete(frame); }
}
addEventListener('message', (event) => {
  const frame = [...visualFrames].find((candidate) => candidate.contentWindow === event.source);
  if (!frame || !event.data || typeof event.data !== 'object') return;
  if (event.data.type === 'hub-visual-ready') sendLook(frame);
  else if (event.data.type === 'hub-visual' && frame.onVisual) frame.onVisual(event.data);
});

// One visual in its frame: pin is a visual's id, or '' for today's. onVisual hears its name, modes and the list.
function visualFrame(pin, onVisual) {
  const ver = (appState.visuals && appState.visuals.version) || 'builtin';
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

// The phone's More sheet: the sections that do not fit in the bottom bar.
function toggleMore() {
  const side = document.getElementById('side');
  const open = !side.classList.contains('open');
  side.classList.toggle('open', open);
  side.querySelector('.more-tab').setAttribute('aria-expanded', String(open));
}
function closeMore() {
  const side = document.getElementById('side');
  side.classList.remove('open');
  const more = side.querySelector('.more-tab');
  if (more) more.setAttribute('aria-expanded', 'false');
}
addEventListener('keydown', (event) => { if (event.key === 'Escape') closeMore(); });
addEventListener('click', (event) => { if (!event.target.closest('#side')) closeMore(); });
// A click on the Home visual lands in its frame and never reaches the page, but it takes focus from the page.
addEventListener('blur', closeMore);

let pageTitle = 'Family Hub';  // the tab title for the current screen, put back after a "ready" notice

function shell(section, tab) {
  applyLook(appState.look);
  const signedIn = !!appState.me;
  document.getElementById('shell').classList.toggle('signed-out', !signedIn);
  const sectionLink = ([key, label, href, iconPath]) => el('a', { href, 'aria-current': section === key ? 'page' : null, onclick: closeMore },
    sectionIcon(iconPath), el('span', { class: 'long', text: label }), el('span', { class: 'short', text: SHORT[key] || label }));
  const sections = SECTIONS.filter(([key]) => (key !== 'admin' || isAdmin()) && ((key !== 'movies' && key !== 'downloads' && key !== 'watchlist') || !isGuest()));
  fill(document.getElementById('side'),
    el('div', { class: 'hub glow' }, starMark(), 'Family Hub'),
    el('nav', { 'aria-label': 'Family Hub sections' }, sections.filter(([key]) => PHONE_BAR.includes(key)).map(sectionLink),
      // On a computer these follow the others in the list; on a phone they open from More.
      el('div', { class: 'extras' }, sections.filter(([key]) => !PHONE_BAR.includes(key)).map(sectionLink)),
      sections.some(([key]) => !PHONE_BAR.includes(key)) ? el('button', { class: 'more-tab', type: 'button', 'aria-expanded': 'false',
        'aria-current': sections.some(([key]) => key === section && !PHONE_BAR.includes(key)) ? 'page' : null, onclick: toggleMore },
        sectionIcon('M5 12h.01M12 12h.01M19 12h.01'), el('span', { text: 'More' })) : null),
    signedIn ? el('div', { class: 'sidefoot lab' }, isGuest() ? 'Guest · nothing is tracked' : appState.me.name + ' · ' + (isAdmin() ? 'Admin' : 'Member'),
      appState.me.via_admin ? el('br') : null, appState.me.via_admin ? 'Opened by an admin' : null) : null);
  const tabs = sectionTabs(section);
  fill(document.getElementById('top'),
    el('div', { class: 'logo glow' }, section === 'profiles' ? starMark() : null, SECTION_LOGO[section] || 'Family Hub'),
    el('nav', { class: 'nav', 'aria-label': SECTION_LOGO[section] || 'Family Hub' }, tabs.map(([key, label, href]) =>
      el('a', { href, 'aria-current': tab === key ? 'page' : null, text: label }))),
    signedIn ? el('a', { class: 'who', href: '#/profiles', 'aria-label': 'Switch profile (now ' + appState.me.name + ')' },
      el('span', { class: 'arch', 'aria-hidden': 'true', text: appState.me.name[0].toUpperCase() }),
      el('span', { class: 'who-text', 'aria-hidden': 'true' }, el('span', { text: appState.me.name }), el('span', { class: 'small dim', text: 'Switch profile' }))) : null);
  const where = section === 'profiles' ? 'Profiles' : (SECTIONS.find((entry) => entry[0] === section) || [0, 'Home'])[1];
  document.getElementById('strip').replaceChildren(el('span', { text: 'Family Hub' + appState.look.sep + where }));
  // The browser tab and screen readers name the section too.
  document.title = pageTitle = where + ' · Family Hub';
  closeMore();
  // On a phone the section's tabs are one sideways row; keep the current tab in view rather than off the edge.
  const nav = document.querySelector('#top .nav');
  const current = nav && nav.querySelector('a[aria-current]');
  // Only scrolled when the tab would otherwise be off the edge, so a tab already in view does not cut off the first one.
  if (nav) {
    nav.scrollLeft = 0;
    if (current && nav.scrollWidth > nav.clientWidth) {
      const tabBox = current.getBoundingClientRect(), navBox = nav.getBoundingClientRect();
      if (tabBox.right > navBox.right - 16) nav.scrollLeft = tabBox.left - navBox.left - 16;
    }
  }
}

// A page that could not load says why, with a way to try again and a way home.
function pageFailed(err) {
  setMain(el('section', { class: 'panel cut stack', role: 'alert' }, head('This page could not load'),
    el('p', { class: 'bad', text: err.message }),
    el('div', { class: 'row' }, el('button', { class: 'btn pri', type: 'button', text: 'Try again', onclick: () => route() }),
      location.hash === '#/home' ? null : el('a', { class: 'btn', href: '#/home', text: 'Go to Home' }))));
}

let lastHash = null;
async function route() {
  routeGen++;
  try { appState = await api('GET', '/api/state'); } catch (err) { if (err instanceof Stale) return; pageFailed(err); return; }
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  let page = parts[0] || (appState.me ? 'home' : 'profiles');
  if (appState.setup_needed) { shell('profiles'); return pageSetup(); }
  if (!appState.me) page = 'profiles';
  if (isGuest() && ['dashboard', 'mybooks', 'admin', 'sort', 'tags', 'copies', 'discover', 'devices', 'series', 'read', 'movies', 'downloads', 'watchlist'].includes(page)) page = 'library';
  if (!appState.series && (page === 'series' || page === 'read')) page = 'library';
  if (['admin', 'sort', 'tags', 'copies'].includes(page) && !isAdmin()) page = 'home';
  const section = page === 'profiles' ? 'profiles' : (PAGE_SECTION[page] || 'books');
  document.body.dataset.page = page;  // the stylesheet keeps some decoration for Home only
  let tab = { library: 'library', book: 'library', shelf: 'library', series: 'library', read: 'library', dashboard: 'dashboard',
      mybooks: 'dashboard', devices: 'dashboard', discover: 'discover', tabletop: 'library' }[page];
  if (section === 'admin') tab = page === 'admin' && parts[1] !== 'books' ? 'hub' : 'books';
  if (section === 'movies') tab = parts[1] === 'search' ? 'search' : 'watch';
  shell(section, tab);
  Tracker.kick();
  // The design's screen-change effect, when moving to a different screen. Its stylesheet keeps it still for reduced motion.
  const moved = lastHash !== null && lastHash !== location.hash;
  if (appState.look.transition && moved) SetPieces.transition();
  lastHash = location.hash;
  // A new screen starts at the top, not wherever the last one was scrolled to.
  if (moved) window.scrollTo(0, 0);
  setMain(el('p', { class: 'small dim', role: 'status', text: 'Loading…' }));
  if (moved) focusWanted = null;  // a new screen: the link pressed on the last one is not looked for on this one
  const pages = { home: pageHome, movies: pageMovies, downloads: pageDownloads, shelf: pageShelf, photos: pageSoon, files: pageSoon, profiles: pageProfiles, library: pageLibrary, book: pageBook,
    dashboard: pageDashboard, admin: parts[1] === 'books' ? pageBooksAdmin : pageHubAdmin, sort: pageSort, tags: pageTags, copies: pageCopies, details: pageDetails, rules: pageRules, fix: pageFix, watchlist: pageWatchlist,
    discover: pageDiscover, devices: pageDevices, series: pageSeries, read: pageRead, mybooks: pageMyBooks, tabletop: pageTabletop };
  const gen = routeGen;
  try { await (pages[page] || pageLibrary)(page === 'admin' ? parts[2] : parts[1], parts[2]); } catch (err) { if (!(err instanceof Stale)) pageFailed(err); }
  // The link that was used has been redrawn, so keyboard and screen reader users would be left at the top of
  // the document. Start them at the new page instead, unless the page put focus somewhere itself (a search box, say).
  if (moved && gen === routeGen && (!document.activeElement || document.activeElement === document.body)) main().focus({ preventScroll: true });
}
window.addEventListener('hashchange', route);
window.addEventListener('hashchange', () => checkMaking());
window.addEventListener('DOMContentLoaded', route);
// After signing in: changing the address draws Home by itself; only an address already on Home needs a redraw.
const goHome = () => { if (location.hash === '#/home') route(); else location.hash = '#/home'; };
// The skip link moves focus to the page without changing the address (which would change page).
document.addEventListener('click', (event) => {
  if (!event.target.closest || !event.target.closest('a.skip')) return;
  event.preventDefault();
  main().focus();
});

// --------------------------------------------------------- films and TV

const canRequestMedia = () => appState.me && !appState.me.guest && appState.me.can_request_media;
const KIND_WORD = { film: 'Film', show: 'TV show' };
function gb(bytes) { return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(bytes / 1e6)) + ' MB'; }

// A poster card in the shelf. Posters keep their own colours, like book covers.
function posterCard(item, extra) {
  const href = item.id ? '#/movies/' + item.kind + '/' + item.id : null;
  const props = { class: 'cover' + (item.poster ? ' has-img' : '') + titleSize(item.title),
      style: '--cv:' + coverColor(item.title) + (item.poster ? ';background-image:url(' + item.poster + ')' : '') };
  const inner = [el('span', { class: 'fmt', text: item.kind === 'film' ? 'Film' : 'TV' }), el('span', { class: 't', text: item.title })];
  return el('div', { class: 'book' }, href ? el('a', Object.assign({ href }, props), inner) : el('div', props, inner),
    el('div', { class: 'small dim', text: [item.year, KIND_WORD[item.kind]].filter(Boolean).join(' · ') }), extra || null);
}
function mediaState(item) {
  // A version found and coming in says so, rather than "being looked for".
  const incoming = item.downloading;
  if (incoming && !item.downloaded) {
    const episodeCounts = item.episodes;
    if (incoming.stuck) return el('div', { class: 'small warn', text: 'Downloaded · could not be filed yet' });
    if (incoming.progress >= 100) return el('div', { class: 'small ok', text: 'Downloaded' });
    return el('div', { class: 'small ok', text: 'Downloading' + (item.kind === 'show' && incoming.episodes > 1 ? ' ' + plural(incoming.episodes, 'episode') : '') + ' · ' + incoming.progress + '%'
      + (item.kind === 'show' && episodeCounts && episodeCounts.have ? ' · ' + episodeCounts.have + ' of ' + plural(episodeCounts.want, 'episode') + ' on the server' : '') });
  }
  if (!item.monitored && !item.downloaded && item.id) return el('div', { class: 'small dim', text: 'Not being looked for' });
  if (item.kind === 'film') return item.downloaded ? el('div', { class: 'small ok', text: 'Downloaded · ready to watch' }) : el('div', { class: 'small warn', text: 'Being looked for' });
  const episodes = item.episodes || { have: 0, want: 0 };
  if (!episodes.want) return el('div', { class: 'small warn', text: 'Being looked for' });
  return el('div', { class: 'small ' + (item.downloaded ? 'ok' : 'warn'),
      text: item.downloaded ? 'Every episode downloaded · ready to watch' : episodes.have + ' of ' + plural(episodes.want,
      'episode') + ' on the server' });
}

function mediaTabs() {
  return [['watch', 'Watch', '#/movies'], ['search', 'Search', '#/movies/search'], ['downloads', 'Downloads', '#/downloads']];
}

async function pageMovies(sub, id) {
  if (sub === 'film' || sub === 'show') return pageMediaItem(sub, Number(id));
  if (sub === 'downloads') { location.replace('#/downloads'); return; }  // where Downloads used to be
  if (sub === 'search') return pageMediaSearch();
  const view = { show: 'all', q: '' };
  const shelf = el('div', { class: 'shelf' });
  const count = el('p', { class: 'small dim', role: 'status' });
  const chips = el('div', { class: 'row gap8', role: 'group', 'aria-label': 'Filter films and shows' });
  let items = [], problems = [];
  const sets = await Promise.all(['film', 'show'].filter((kind) => (kind === 'film' ? appState.media.films : appState.media.shows))
    .map((kind) => api('GET', '/api/media/library?kind=' + kind).then((data) => data.items).catch((err) => { if (err instanceof Stale) throw err; problems.push(err.message); return []; })));
  items = sets.flat().sort((left, right) => (right.added || '').localeCompare(left.added || ''));
  function draw() {
    chips.replaceChildren(...[['all', 'Everything'], ['film', 'Films'], ['show', 'TV shows'], ['waiting', 'Not on the server yet']].map(([key, label]) =>
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(view.show === key), text: label, onclick: () => { view.show = key; draw(); } })));
    const needle = view.q.toLowerCase();
    const shown = items.filter((item) => (view.show === 'all' || item.kind === view.show || (view.show === 'waiting' && !item.downloaded)) && (!needle || item.title.toLowerCase().includes(needle)));
    shelf.replaceChildren(...shown.map((item) => posterCard(item, mediaState(item))));
    count.textContent = shown.length ? plural(shown.length, 'title') : (items.length ? 'Nothing matches. Try fewer words.' : 'Nothing here yet.');
  }
  const search = el('input', { type: 'search', placeholder: 'Search', oninput: debounce((event) => { view.q = event.target.value; draw(); }, 200) });
  const latest = el('div');
  if (appState.media.jellyfin) {
    api('GET', '/api/media/latest').then((data) => fill(latest, data.items.length ? el('section', { class: 'panel cut', style: '--bd:var(--accent)' },
      head('New on the server', el('span', { class: 'small dim', text: 'From Jellyfin' })),
      el('div', { class: 'reel' }, data.items.map((item) => posterCard(Object.assign({}, item, { id: null }),
        item.watch ? el('a', { class: 'btn sm pri', href: item.watch, target: '_blank', rel: 'noopener', 'aria-label': 'Watch ' + item.title + ' in Jellyfin', text: 'Watch' }) : null)))) : null))
      .catch((err) => fill(latest, el('p', { class: 'small warn', text: 'Jellyfin: ' + err.message })));
  }
  const nothing = !appState.media.films && !appState.media.shows;
  setMain(
    el('div', { class: 'row between end' }, pageHead('Watch', 'Films and series on the family server.'),
      nothing ? null : field('Search title', search)),
    nothing ? el('section', { class: 'panel cut' }, head('Not set up yet'),
      el('p',
          { text: 'Films and TV need Radarr and Sonarr. ' + (isAdmin() ? 'Start them with the media profile (guide 14), then press Connect everything under Admin.' : 'Ask an admin to set them up.') }),
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
  const now = el('div', { class: 'stack gap8', 'aria-live': 'polite' }, el('p', { class: 'small dim', text: 'Asking what is happening…' }));
  const list = el('ol', { class: 'steps' });
  const gen = routeGen;
  async function load() {
    let data;
    try { data = await api('GET', '/api/media/activity/' + kind + '/' + id); } catch (err) {
      if (err instanceof Stale) return;
      // Radarr or Sonarr may only be restarting: say so, and try again a little later.
      fill(now, el('p', { class: 'small bad', text: err.message }), el('p', { class: 'small dim', text: 'Trying again shortly.' }));
      if (gen === routeGen) setTimeout(() => { if (gen === routeGen && document.body.contains(now)) load(); }, 20000);
      return;
    }
    const current = data.now;
    fill(now, bar(current.progress, current.progress + '% of the way'),
      el('div', { class: 'row between gap8' },
        el('span', { class: 'step-now ' + current.state, text: STEP_MARK[current.state] + ' ' + current.text }),
        el('span', { class: 'small dim', text: current.progress + '%' })),
      current.sites ? el('p', { class: 'small dim', text: current.sites }) : null);
    fill(list, data.steps.map((step) => el('li', { class: 'step ' + step.state },
      el('span', { class: 'mark ' + STEP_CLASS[step.state], 'aria-hidden': 'true', text: STEP_MARK[step.state] }),
      el('span', { class: step.state === 'bad' ? 'bad' : '', text: step.text }),
      el('span', { class: 'small dim when', text: step.at ? ago(step.at) : '' }))));
    // Keep watching while this page is open and the film or show is not on the server yet.
    if (!data.finished && gen === routeGen) setTimeout(() => { if (gen === routeGen && document.body.contains(now)) load(); }, 5000);
  }
  load();
  return el('section', { class: 'panel cut' }, head("What's happening", el('span', { class: 'small dim', text: 'Updates by itself' })), now,
    el('details', { class: 'small' }, el('summary', { text: 'Every step' }), list));
}

// While a search runs: a bar, a timer and, when the search goes through Prowlarr, how many sites have answered
// (Prowlarr logs each one as it replies). Returns a function that stops it. sites=false for quick title look-ups.
function searchProgress(box, query, opts) {
  const options = Object.assign({ sites: true, typical: 30, what: 'Searching' }, opts);
  const started = Date.now(), gen = routeGen;
  const fillBar = el('i', { style: 'width:2%' });
  const line = el('span', { class: 'small', text: options.what + '…' });
  const secs = el('span', { class: 'small dim', text: '0 s' });
  fill(box, el('div', { class: 'stack search-progress gap8', role: 'status', 'aria-live': 'polite' },
    el('div', { class: 'bar' + (options.sites ? '' : ' busy'), role: 'img', 'aria-label': 'Search in progress' }, fillBar),
    el('div', { class: 'row between gap8' }, line, secs),
    el('p', { class: 'small dim', text: 'Stay on this page until it finishes: the results appear here, and leaving throws them away.' })));
  let stopped = false, timer = null, answered = 0, sites = 0;
  function tick() {
    if (stopped || gen !== routeGen) return;
    const seconds = Math.round((Date.now() - started) / 1000);
    secs.textContent = seconds + ' s';
    if (options.sites) {
      // Real progress when sites have answered; otherwise creep along the usual time, never quite reaching the end.
      const byTime = Math.min(90, 100 * seconds / (options.typical * 1.3));
      const bySites = sites ? 100 * answered / sites : 0;
      fillBar.style.width = Math.max(2, Math.min(97, Math.max(bySites, byTime * 0.6))) + '%';
    }
  }
  async function poll() {
    if (stopped || gen !== routeGen) return;
    try {
      const res = await fetch('/api/search-progress?' + qs({ q: query, ago: (Date.now() - started) / 1000 }), { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        if (!stopped && data.sites) {
          answered = data.answered; sites = data.sites;
          line.textContent = 'Asked ' + plural(sites,
              'site') + ' · ' + answered + ' answered' + (data.names.length ? ' (latest: ' + data.names[0] + ')' : '') + (answered >= sites ? ' · putting the list together…' : '');
        }
      }
    } catch (err) { /* the bar keeps going on time alone */ }
    if (!stopped) timer = setTimeout(poll, 1500);
  }
  const clock = setInterval(() => { if (stopped || gen !== routeGen) clearInterval(clock); else tick(); }, 500);
  if (options.sites) poll();
  return () => { stopped = true; clearInterval(clock); clearTimeout(timer); };
}

// The corner pop-up: films and shows this browser asked for or opened, followed on every page until they are
// ready and dismissed. The list is a per-browser convenience, so it lives in localStorage.
const Tracker = (() => {
  const KEY = 'fh-tracking', MIN = 'fh-tracking-min';  // entries: {kind: film | show | book, id: number, or a book's release name}
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (err) { return fallback; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (err) { /* private window: just not remembered */ } };
  let box = null, timer = null, shown = [], runs = 0;

  function follow(kind, id) {
    const list = read(KEY, []);
    if (!list.some((entry) => entry.kind === kind && entry.id === id)) { list.push({ kind, id, finished: false }); write(KEY, list.slice(-6)); }
    kick();
  }
  function forget(kind, id) {
    write(KEY, read(KEY, []).filter((entry) => !(entry.kind === kind && entry.id === id)));
    render(shown.filter((item) => !(item.kind === kind && item.id === id)));
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
    // Downloaded but still being filed counts as done: nothing is coming in any more.
    const waiting = items.filter((item) => !item.finished && !item.downloaded).length;
    const fetched = items.filter((item) => !item.finished && item.downloaded).length;
    const ready = items.length - waiting - fetched;
    fill(box,
      el('div', { class: 'row between tracker-head' },
        el('strong', { class: 'small', text: [waiting ? plural(waiting, 'title') + ' being looked for' : '', fetched ? fetched + ' downloaded' : '', ready ? ready + ' ready' : ''].filter(Boolean).join(' · ') }),
        el('button', { class: 'btn sm', type: 'button', 'aria-expanded': String(!small), text: small ? 'Show list' : 'Hide list', onclick: () => { write(MIN, !small); render(shown); } })),
      small ? null : items.map((item) => el('div', { class: 'tracker-item stack' + (item.finished || item.downloaded ? ' ready' : '') },
        el('div', { class: 'row between', style: 'gap:8px;flex-wrap:nowrap' },
          el('a', { href: item.kind === 'book' ? (item.finished ? '#/library' : '#/downloads') : '#/movies/' + item.kind + '/' + item.id,
              class: 'tracker-title', text: item.title, title: item.title }),
          el('button', { class: 'btn sm', type: 'button', 'aria-label': 'Stop showing ' + item.title + ' here', text: '✕', onclick: () => forget(item.kind, item.id) })),
        item.finished ? el('span', { class: 'small ok', text: item.kind === 'book' ? '✓ In the library' : '✓ Ready to watch' })
          : item.downloaded ? [
            el('span', { class: 'small ok', text: '✓ Downloaded' }),
            item.filing === 'stuck' ? el('span', { class: 'small warn', text: 'It could not be moved into the library by itself. Its page says why.' })
              : el('span', { class: 'small dim', text: item.kind === 'book' ? 'Being added to the library; it appears there in a minute or two.'
                : 'Being moved into the library; it can be watched in a few minutes.' })] : [
          bar(item.now.progress, item.now.progress + '% of the way'),
          el('span', { class: 'small ' + (item.now.state === 'bad' ? 'bad' : 'dim'), text: item.now.text })])));
  }
  async function refresh() {
    clearTimeout(timer);
    const list = read(KEY, []);
    if (!list.length || !appState || !appState.me || isGuest()) { render([]); return; }
    // Not api(): the pop-up outlives page changes, so its answers must not be thrown away as stale.
    const run = ++runs;
    let data = null;
    const media = list.filter((entry) => entry.kind !== 'book'), books = list.filter((entry) => entry.kind === 'book');
    const query = 'items=' + media.map((item) => item.kind + ':' + item.id).join(',') + books.map((book) => '&book=' + encodeURIComponent(book.id)).join('');
    try {
      const res = await fetch('/api/media/tracking?' + query, { credentials: 'same-origin' });
      if (res.ok) data = await res.json();
    } catch (err) { /* offline for a moment */ }
    if (run !== runs) return;  // a newer refresh has started
    if (!data) { timer = setTimeout(refresh, 60000); return; }  // the server may be busy; try again later without bothering anyone
    // One Radarr or Sonarr not answering keeps its last title and progress, with the reason as its line.
    const items = data.items.filter((item) => !item.gone).map((item) => {
      if (!item.unreachable) return item;
      const prev = shown.find((earlier) => earlier.kind === item.kind && earlier.id === item.id);
      if (prev && prev.finished) return prev;
      return Object.assign({}, item, { title: (prev && prev.title) || KIND_WORD[item.kind], now: Object.assign({}, item.now, { progress: prev && prev.now ? prev.now.progress : 0 }) });
    });
    // Say so once when something becomes ready, including in the browser tab's title when this tab is in the background.
    for (const item of items) {
      const was = list.find((entry) => entry.kind === item.kind && entry.id === item.id);
      if (item.finished && was && !was.finished) {
        toast(item.title + (item.kind === 'book' ? ' is in the library.' : ' is ready to watch.'), false, 6000);
        if (document.hidden) document.title = '✓ ' + item.title + ' is ready · ' + pageTitle;
      }
    }
    write(KEY, items.map((item) => ({ kind: item.kind, id: item.id, finished: item.finished })));
    render(items);
    if (items.some((item) => !item.finished)) timer = setTimeout(refresh, document.hidden ? 30000 : 15000);
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
    try { const data = await api('POST', path, body); toast(data.message); again(); } catch (err) { btn.disabled = false; oops(err); }
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
  const seasonNums = kind === 'show' ? item.seasons.map((season) => season.number).filter((number) => number > 0) : [];
  const seasonSel = seasonNums.length ? el('select', { 'aria-label': 'Season' }, seasonNums.slice().reverse().map((number) => el('option', { value: number, text: 'Season ' + number }))) : null;
  // Versions appear as each site answers. Radarr or Sonarr check every one in the meantime; when they are done
  // their list (with what would not be picked, and why) takes over. Stop searching keeps what has been found.
  let polling = null;
  const sent = () => toast('Sent to qBittorrent. Happy to keep this one? Press Stop looking once it is on the server, or it may be swapped for a better copy later.', false, 7000);
  function checkedTable(rows) {
    return rows.length ? el('div', { class: 'scroll' }, el('table', {},
      el('thead', {}, el('tr', {}, ['Title', 'Quality', 'Size', 'Indexer', 'Seeders', ''].map((heading, index) => el('th', { scope: 'col',
          class: index === 2 || index === 4 ? 'num' : '', text: heading })))),
      el('tbody', {}, rows.map((row) => {
        const btn = el('button', { class: 'btn sm' + (row.rejected ? '' : ' pri'), type: 'button',
            text: row.rejected ? 'Use this anyway' : 'Use this', 'aria-label': 'Download ' + row.title, onclick: async () => {
          btn.disabled = true;
          try { await api('POST', '/api/media/grab', { kind, guid: row.guid, indexer_id: row.indexer_id }); btn.textContent = 'Sent'; sent(); } catch (err) { btn.disabled = false; oops(err); }
        } });
        return el('tr', {}, el('th', { scope: 'row', class: 'plain' }, row.title, row.rejected ? el('div', { class: 'small warn', text: 'Would not be picked: ' + row.why }) : null),
          el('td', { text: row.quality }), el('td', { class: 'num', text: gb(row.size) }), el('td', { text: row.indexer }),
          el('td', { class: 'num', text: row.seeders === null || row.seeders === undefined ? '–' : row.seeders }), el('td', {}, btn));
      })))) : el('p', { class: 'small dim', text: 'The indexers have nothing for this right now.' });
  }
  function foundTable(job, rows) {
    return rows.length ? el('div', { class: 'scroll' }, el('table', {},
      el('thead', {}, el('tr', {}, ['Title', 'Quality (from its name)', 'Size', 'Site', 'Seeders', ''].map((heading, index) => el('th',
          { scope: 'col', class: index === 2 || index === 4 ? 'num' : '', text: heading })))),
      el('tbody', {}, rows.map((row) => {
        const note = el('div', { class: 'small warn' });
        const btn = el('button', { class: 'btn sm pri', type: 'button', text: 'Use this', 'aria-label': 'Download ' + row.title });
        btn.onclick = async () => {
          const force = btn.dataset.force === '1';
          btn.disabled = true;
          try {
            const data = await api('POST', '/api/media/versions/' + job + '/grab', { guid: row.guid, force });
            if (data.ok) { btn.textContent = 'Sent'; note.textContent = data.direct ? 'Sent straight to qBittorrent. If ' + (kind === 'film' ? 'Radarr' : 'Sonarr') + ' cannot tell what it is, file it from its Activity page.' : ''; sent(); return; }
            note.textContent = (kind === 'film' ? 'Radarr' : 'Sonarr') + ' turned it down: ' + data.why;
            btn.textContent = 'Use this anyway'; btn.dataset.force = '1'; btn.classList.remove('pri'); btn.disabled = false;
          } catch (err) { btn.disabled = false; oops(err); }
        };
        return el('tr', {}, el('th', { scope: 'row', class: 'plain' }, row.title, note),
          el('td', { class: row.quality === 'Cam' || row.quality === 'Remux' ? 'warn' : '', text: row.quality || '?' }), el('td',
              { class: 'num', text: gb(row.size) }), el('td', { text: row.indexer }),
          el('td', { class: 'num', text: row.seeders === null || row.seeders === undefined ? '–' : row.seeders }), el('td', {}, btn));
      })))) : null;
  }
  async function loadReleases() {
    clearTimeout(polling);
    const gen = routeGen;
    const status = el('div', { class: 'stack gap8', role: 'status', 'aria-live': 'polite' });
    const list = el('div', { class: 'stack' });
    let stopped = false, job = null;
    const stopBtn = el('button', { class: 'btn sm', type: 'button', text: 'Stop searching', onclick: () => {
      stopped = true; clearTimeout(polling); stopBtn.remove();
      status.lastChild && (status.lastChild.textContent = 'Stopped. These are the versions found so far; pick one, or press Show versions to look again.');
    } });
    fill(releases, status, list);
    fill(status, el('p', { class: 'small dim', text: 'Starting the search…' }));
    try { job = (await api('POST', '/api/media/versions/' + kind + '/' + id + (seasonSel ? '?season=' + seasonSel.value : ''))).job; } catch (err) {
      if (!(err instanceof Stale)) fill(releases, el('p', { class: 'small bad', text: err.message }));
      return;
    }
    let shownCount = -1;
    async function poll() {
      if (stopped || gen !== routeGen || !document.body.contains(list)) return;
      let data;
      try { data = await api('GET', '/api/media/versions/' + job); } catch (err) {
        if (!(err instanceof Stale)) fill(status, el('p', { class: 'small bad', text: err.message }));
        return;
      }
      if (stopped) return;
      if (data.checked !== null && (data.checked.length || data.answered >= data.sites)) {
        const who = kind === 'film' ? 'Radarr' : 'Sonarr';
        if (!data.checked.length && data.found.length) {
          // Radarr or Sonarr failed or came back empty-handed: keep what the sites offered rather than an empty list.
          fill(status, el('p', { class: 'small warn', text: data.checked_error ? who + ' could not check them: ' + data.checked_error : who + ' did not find these itself.' }),
            el('p', { class: 'small dim', text: 'These are the versions the sites offered, best first by the quality in each name. You can still pick one.' }));
          fill(list, foundTable(job, data.found));
          return;
        }
        // Radarr or Sonarr have checked everything: their list is the better one.
        fill(status, data.checked_error ? el('p', { class: 'small warn', text: who + ' could not check them: ' + data.checked_error })
          : el('p', { class: 'small ok', text: who + ' has checked every version (' + data.seconds + ' s). Versions marked "would not be picked" can still be used.' }));
        fill(list, checkedTable(data.checked));
        return;
      }
      const pct = data.sites ? Math.round(100 * data.answered / data.sites) : 0;
      fill(status, bar(Math.max(3, pct), pct + '% of sites have answered'),
        el('div', { class: 'row between gap8' },
          el('span', { class: 'small', text: (data.sites ? data.answered + ' of ' + plural(data.sites, 'site') + ' answered · ' : '') + plural(data.found.length, 'version') + ' so far' }),
          el('div', { class: 'row gap8' }, el('span', { class: 'small dim', text: data.seconds + ' s' }), stopBtn)),
        el('p', { class: 'small dim',
            text: data.answered >= data.sites && data.sites ? 'Every site has answered; ' + (kind === 'film' ? 'Radarr' : 'Sonarr') + ' is still checking them against the household rule. You can pick one now.'
          : 'Best first, by the quality in each name. Pick one now, or wait for the full list.' }));
      // Only redraw the table when something new arrived, and never under someone using its buttons.
      if (data.found.length !== shownCount && !list.contains(document.activeElement)) { shownCount = data.found.length; fill(list, foundTable(job, data.found)); }
      polling = setTimeout(poll, 1500);
    }
    poll();
  }
  const facts = [item.year, KIND_WORD[kind], item.runtime ? item.runtime + ' min' : '', item.network].filter(Boolean).join(' · ');
  setMain(back('#/movies', 'Back to watch'),
    el('div', { class: 'detail' },
      el('div', { class: 'bside' }, el('div', { class: 'cover' + (item.poster ? ' has-img' : '') + titleSize(item.title),
          style: '--cv:' + coverColor(item.title) + (item.poster ? ';background-image:url(' + item.poster + ')' : '') },
        el('span', { class: 'fmt', text: kind === 'film' ? 'Film' : 'TV' }), el('span', { class: 't', text: item.poster ? '' : item.title }))),
      el('div', { class: 'bbody' },
        el('div', { class: 'stack' }, el('h1', { class: 'glow', text: item.title }), el('p', { class: 'dim', text: facts }),
          item.genres.length ? el('p', { class: 'lab', text: item.genres.join(' · ') }) : null,
          mediaState(item),
          el('p', { class: 'small dim', text: [item.size ? gb(item.size) + ' on the server' : '',
              item.requested_by ? 'requested by ' + item.requested_by : '',
              item.added ? 'added ' + niceDate(item.added) : ''].filter(Boolean).join(' · ') })),
        item.downloaded || kind === 'show' ? null : activityPanel(kind, id),
        kind === 'film' && item.downloaded ? watchPanel(kind, id, item) : null,
        kind === 'show' ? watchPanel(kind, id, item) : null,
        kind === 'show' && !item.downloaded ? activityPanel(kind, id) : null,
        item.overview ? el('section', { class: 'stack' }, head('Story'), el('p', { class: 'synopsis', text: item.overview })) : null)),
    // The working panels use the whole width, so the list of versions fits without scrolling sideways.
    el('div', { class: 'bbody wide-panels' },
        canRequestMedia() ? lookingPanel(kind, id, item, again) : null,
        canRequestMedia() ? el('section', { class: 'panel cut' }, head('Pick a version yourself', seasonSel, el('button', { class: 'btn sm',
            type: 'button', text: 'Show versions', onclick: loadReleases })),
          el('p', { class: 'small dim',
              text: (kind === 'film' ? 'Radarr' : 'Sonarr') + ' picks the best version by itself. If it is taking too long, or you are happy with a smaller or lower-quality copy, see every version the indexers have and choose one. Versions marked "would not be picked" can still be used.' }),
              releases) : null,
        isAdmin() ? el('section', { class: 'panel cut', style: '--bd:var(--warn)' }, head('Remove from the server'),
          el('p', { class: 'small dim',
              text: 'Deletes ' + (kind === 'film' ? 'the film' : 'every episode') + ' and stops looking for ' + (kind === 'film' ? 'it' : 'new ones') + '. This cannot be undone.' }),
          el('div', { class: 'row' }, el('button', { class: 'btn danger', type: 'button', text: 'Remove ' + item.title, onclick: async () => {
            if (!window.confirm('Remove "' + item.title + '" from the server and delete its files' + (item.size ? ' (' + gb(item.size) + ')' : '') + '? This cannot be undone.')) return;
            try { await api('DELETE', '/api/media/' + kind + '/' + id); toast(item.title + ' was removed.'); location.hash = '#/movies'; } catch (err) { oops(err); }
          } }))) : null));
}

async function pageMediaSearch() {
  const results = el('div', { class: 'stack' });
  const kindSel = el('select', {}, el('option', { value: 'film', text: 'Film' }), el('option', { value: 'show', text: 'TV show' }));
  const searchBox = el('input', { type: 'search' });
  async function search() {
    if (searchBox.value.trim().length < 2) { fill(results, el('p', { class: 'small dim', role: 'status', text: 'Type at least two letters.' })); searchBox.focus(); return; }
    const kind = kindSel.value, run = ++searchRun;
    const stop = searchProgress(results, searchBox.value.trim(), { sites: false, what: 'Looking up titles' });
    try {
      const data = await api('GET', '/api/media/search?' + qs({ kind, q: searchBox.value.trim() })).finally(stop);
      if (run !== searchRun) return;  // a newer search has started; its answer goes here instead
      fill(results, data.results.length ? el('div', { class: 'shelf' }, data.results.map((result) => posterCard(Object.assign({ kind }, result, { id: null }),
        el('div', { class: 'stack gap6' }, result.overview ? el('div', { class: 'small dim', text: result.overview.length > 120 ? result.overview.slice(0, 120) + '…' : result.overview }) : null,
          result.in_library ? el('div', { class: 'small ok', text: 'Already on the list' }) : requestControls(kind, result))))) : el('p',
              { class: 'small dim', role: 'status', text: 'Nothing found. Try fewer words.' }));
    } catch (err) { if (!(err instanceof Stale) && run === searchRun) fill(results, el('p', { class: 'bad', role: 'status', text: err.message })); }
  }
  let searchRun = 0;
  setMain(
    pageHead('Search', 'Find a film or series and ask for it. Follow it in ', el('a', { href: '#/downloads', text: 'Downloads' }), '.'),
    canRequestMedia() ? el('section', { class: 'panel cut' }, head('Search for a film or show', el('span', { class: 'small dim', text: '2160p first, then 1080p' })),
      el('form', { class: 'row end', role: 'search', onsubmit: (event) => { event.preventDefault(); search(); } }, field('What', kindSel),
          field('Title', searchBox), el('button', { class: 'btn pri', type: 'submit', text: 'Search' })),
      results) : el('p', { class: 'dim', text: 'This profile can watch but not request new films and shows. Ask an admin.' }));
  searchBox.focus();
}

// The watchlist: authors, films and TV shows someone is waiting for. Each is checked every few hours; what was
// found shows under it, and anything new since the last visit is marked. Films and shows can be got by
// themselves as soon as a match appears that is not out yet.
const WATCH_KINDS = [['author', 'Author (books)'], ['film', 'Film'], ['show', 'TV show']];
async function pageWatchlist() {
  const data = await api('GET', '/api/watchlist');
  const gen = routeGen;
  const list = el('div', { class: 'stack' });
  const kindSel = el('select', {}, WATCH_KINDS.filter(([key]) => key === 'author' || (key === 'film' ? data.films : data.shows)).map(([key, label]) => el('option', { value: key, text: label })));
  const name = el('input', { type: 'text', placeholder: 'Such as Brandon Sanderson or Dune' });
  const auto = el('input', { type: 'checkbox' });
  const autoRow = el('label', { class: 'check small' }, auto, 'Get it automatically as soon as it is announced (it downloads once it is out)');
  const showAuto = () => { autoRow.hidden = kindSel.value === 'author' || !data.can_request_media; };
  kindSel.addEventListener('change', showAuto);
  showAuto();
  let items = data.items.filter((item) => WATCH_KINDS.some(([key]) => key === item.kind));

  function found(watched, item) {
    const pic = item.poster || item.cover;
    const act = [];
    if (watched.kind === 'author') act.push(el('a', { class: 'btn sm', href: '#/discover/' + encodeURIComponent(item.title + ' ' + watched.value), text: 'Find it' }));
    else if (item.in_library && item.arr_id) act.push(el('a', { class: 'btn sm', href: '#/movies/' + watched.kind + '/' + item.arr_id,
        text: item.requested ? 'Open (got it for you)' : 'Open on the server' }));
    else if (data.can_request_media && item.external_id) act.push(el('button', { class: 'btn sm pri', type: 'button',
        text: item.coming ? 'Get it when it is out' : 'Get it', onclick: async (event) => {
      const button = event.currentTarget; button.disabled = true;
      try {
        const result = await api('POST', '/api/media/request', { kind: watched.kind, id: item.external_id });
        toast(result.title + (item.coming ? ' will download once it is out.' : ' is being looked for.'));
        if (result.id) Tracker.follow(watched.kind, result.id);
        refresh(watched.id);
      } catch (err) { button.disabled = false; oops(err); }
    } }));
    return el('div', { class: 'row', style: 'gap:12px;align-items:center' },
      pic ? el('img', { class: 'thumb', src: pic, alt: '', loading: 'lazy' }) : el('span', { class: 'thumb' }),
      el('div', { class: 'stack', style: 'gap:2px;flex:1;min-width:0' },
        el('div', { class: 'row gap8' }, el('strong', { class: 'book', text: item.title + (item.year && watched.kind !== 'author' ? ' (' + item.year + ')' : '') }),
          item.new ? el('span', { class: 'chip xs', text: 'New' }) : null),
        el('span', { class: 'small ' + (item.coming ? 'warn' : 'dim'), text: item.words }),
        item.request_error ? el('span', { class: 'small bad', text: item.request_error }) : null),
      act);
  }
  function row(watched) {
    const label = (WATCH_KINDS.find(([key]) => key === watched.kind) || [0, watched.kind])[1];
    const btn = (text, fn, cls) => el('button', { class: 'btn sm' + (cls ? ' ' + cls : ''), type: 'button', text, onclick: fn });
    return el('section', { class: 'panel cut' },
      head(watched.value, el('span', { class: 'small dim', text: label + (watched.new ? ' · ' + watched.new + ' new' : '') })),
      watched.found.length ? el('div', { class: 'list' }, watched.found.map((item) => found(watched, item)))
        : el('p', { class: 'small dim',
            text: watched.checked ? (watched.kind === 'author' ? 'Nothing new or coming that the library does not have.' : 'Nothing by that name yet. It is checked again every few hours.') : 'Not checked yet.' }),
      watched.error ? el('p', { class: 'small bad', text: 'The last check failed: ' + watched.error }) : null,
      el('div', { class: 'row', style: 'gap:8px;margin-top:8px' },
        el('span', { class: 'small dim', style: 'flex:1', text: watched.checked ? 'Checked ' + ago(watched.checked).toLowerCase() : '' }),
        watched.kind !== 'author' && data.can_request_media ? el('label', { class: 'check small' }, el('input', { type: 'checkbox', checked: watched.auto, onchange: async (event) => {
          try { replace(await api('POST', '/api/watchlist/' + watched.id + '/auto',
              { on: event.target.checked })); toast(event.target.checked ? 'It will be got as soon as it is announced.' : 'It will only be got when you press Get it.'); } catch (err) { event.target.checked = !event.target.checked; oops(err); }
        } }), 'Get automatically') : null,
        btn('Check now', async (event) => { event.currentTarget.disabled = true; try { replace(await api('POST', '/api/watchlist/' + watched.id + '/check')); } catch (err) { oops(err); } draw(); }),
        btn('Stop watching', async () => { try { await api('DELETE',
            '/api/watch/' + watched.id); items = items.filter((item) => item.id !== watched.id); draw(); } catch (err) { oops(err); } }, 'danger')));
  }
  function replace(updated) { items = items.map((item) => (item.id === updated.id ? updated : item)); draw(); }
  async function refresh(id) { try { replace(await api('POST', '/api/watchlist/' + id + '/check')); } catch (err) { oops(err); } }
  function draw() {
    if (gen !== routeGen) return;
    fill(list, items.length ? items.map(row) : el('p', { class: 'dim', text: 'Nothing on your watchlist yet. Add an author, a film or a show above.' }));
  }
  const add = el('form', { class: 'row end gap8', onsubmit: async (event) => {
    event.preventDefault();
    const button = event.target.querySelector('button[type=submit]');
    button.disabled = true; button.textContent = 'Adding…';
    try {
      const added = await api('POST', '/api/watchlist', { kind: kindSel.value, value: name.value, auto: auto.checked });
      items = items.filter((item) => item.id !== added.id).concat(added);
      name.value = ''; auto.checked = false;
      draw();
      toast('Watching for ' + added.value + '.');
    } catch (err) { oops(err); }
    button.disabled = false; button.textContent = 'Add to watchlist';
  } }, field('What', kindSel), field('Name', name), el('button', { class: 'btn pri', type: 'submit', text: 'Add to watchlist' }));
  draw();
  setMain(
    pageHead('Watchlist', 'Authors, films and shows you are waiting for. Each is checked every few hours; anything new is marked. Tags you follow for book suggestions stay on Books, Discover.'),
    el('section', { class: 'panel cut' }, head('Watch for something'), add, autoRow),
    list);
  if (data.news) api('POST', '/api/watchlist/seen').catch(() => {});  // looked at now: marked as seen for next time
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
  const chips = el('div', { class: 'row gap8', role: 'group', 'aria-label': 'Show' });
  function drawChips() {
    fill(chips, types.map(([key, label]) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(show === key), text: label,
      onclick: () => { show = key; drawChips(); draw(); } })));
  }
  function draw() {
    if (!last || list.contains(document.activeElement)) return;  // leave it alone while its buttons are in use
    const shown = last.torrents.filter((torrent) => show === 'all' || torrent.type === show);
    const missing = shown.filter((item) => item.missing);
    const going = shown.filter((item) => !item.done && !item.waiting_seed && !item.missing), finished = shown.filter((item) => item.done && !item.missing);
    const waiting = shown.filter((item) => !item.done && item.waiting_seed && !item.missing);
    // Downloads whose files were moved by hand: qBittorrent cannot find them. Forgetting them leaves the files alone.
    const forgetAll = el('button', { class: 'btn sm', type: 'button', text: 'Forget all ' + missing.length + ' (files stay)', onclick: async () => {
      if (!window.confirm('Take these ' + missing.length + ' downloads off qBittorrent\'s list? Their files are not touched, wherever they are. A film or show you do not have can be asked for again.')) return;
      forgetAll.disabled = true;
      try { const result = await api('POST', '/api/media/torrents/forget',
          { hashes: missing.map((torrent) => torrent.hash) }); toast(result.message); } catch (err) { if (!(err instanceof Stale)) oops(err); }
      forgetAll.blur();
      loadTorrents();
    } });
    fill(list, missing.length ? el('section', { class: 'panel stack gap8' },
      el('div', { class: 'row between' }, el('h3', { class: 'small bad', style: 'margin:0', text: 'Files are missing (' + missing.length + ')' }), last.can_delete ? forgetAll : null),
      el('p', { class: 'small dim', style: 'margin:0',
          text: 'qBittorrent cannot find these downloads\' files, usually because they were moved by hand (or the downloads folder changed). If you already have them, Forget takes them off the list. Nothing is deleted.' }),
      el('details', {}, el('summary', { class: 'small', text: 'Show them' }), el('div', { class: 'stack', style: 'margin-top:12px' },
          missing.map((torrent) => torrentRow(torrent, last, loadTorrents))))) : null,
      going.length ? going.map((torrent) => torrentRow(torrent, last, loadTorrents))
      : el('p', { class: 'small dim', text: waiting.length ? 'Nothing downloading: everything left is waiting for seed.' : 'Nothing downloading.' }),
      waiting.length ? [el('h3', { class: 'small warn', style: 'margin:8px 0 0', text: 'Waiting for seed (' + waiting.length + ')' }),
        ...waiting.map((torrent) => torrentRow(torrent, last, loadTorrents))] : null,
      finished.length ? el('details', {}, el('summary', { class: 'small', text: 'Finished (' + finished.length + ')' }),
        el('div', { class: 'stack', style: 'margin-top:12px' }, finished.map((torrent) => torrentRow(torrent, last, loadTorrents)))) : null);
  }
  async function loadTorrents() {
    clearTimeout(list.timer);
    const again = (ms) => { list.timer = setTimeout(() => { if (gen === routeGen && document.body.contains(list)) loadTorrents(); }, ms); };
    try { last = await api('GET', '/api/media/torrents'); } catch (err) {
      if (err instanceof Stale) return;
      // qBittorrent is often only away while the VPN restarts: keep asking, less often.
      last = null;
      fill(list, el('p', { class: 'small bad', role: 'status', text: err.message }), el('p', { class: 'small dim', text: 'Trying again shortly.' }));
      again(20000);
      return;
    }
    draw();
    again(5000);
  }
  const when = (at, by) => el('span', { class: 'small dim', text: [niceDate(at), by ? 'requested by ' + by : ''].filter(Boolean).join(' · ') });
  api('GET', '/api/media/queue').then((data) => {
    vpnLine.textContent = data.vpn.message;
    vpnLine.className = 'small ' + (data.vpn.ok ? 'ok' : 'warn');
    fill(added, data.history.length ? data.history.map((entry) => el('div', { class: 'row between' }, el('span', { text: entry.title }), when(entry.at, entry.requested_by)))
      : el('p', { class: 'small dim', text: 'No films or TV added yet.' }));
  }).catch((err) => { if (!(err instanceof Stale)) fill(added, el('p', { class: 'small dim', text: err.message })); });
  api('GET', '/api/discover/queue').then((data) => {
    fill(addedBooks, data.imported.length ? data.imported.slice(0, 8).map((file) => el('div', { class: 'row between' }, el('span', { text: file.name }), when(file.at)))
      : el('p', { class: 'small dim', text: 'No books added from downloads yet.' }));
  }).catch((err) => { if (!(err instanceof Stale)) fill(addedBooks, el('p', { class: 'small dim', text: err.message })); });
  drawChips();
  setMain(
    pageHead('Downloads', 'Everything being downloaded for the hub. Ask for films and TV in ', el('a', { href: '#/movies/search', text: 'Films and TV, Search' }),
      ' and for books in ', el('a', { href: '#/discover', text: 'Books, Discover' }), '.'),
    el('section', { class: 'panel cut' }, head('Every download', vpnLine),
      el('p', { class: 'small dim', text: 'In the order qBittorrent works through them. Three download at once; the rest wait their turn. '
        + 'One nobody has shared for ten minutes goes to the back, paused, marked waiting for seed.' }), chips, list),
    speedPanel(),
    isAdmin() ? sortPanel() : null,
    el('div', { class: 'cols2' },
      el('section', { class: 'panel cut' }, head('Films and TV added'), added),
      el('section', { class: 'panel cut' }, head('Books added'), addedBooks)));
  loadTorrents();
}

const speed = (bytes) => bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB/s' : Math.round(bytes / 1024) + ' KB/s';
function timeLeft(sec) {
  const hours = Math.floor(sec / 3600), minutes = Math.round((sec % 3600) / 60);
  return hours >= 48 ? Math.round(hours / 24) + ' days' : hours ? hours + ' h ' + minutes + ' min' : Math.max(minutes, 1) + ' min';
}

// One slider for how fast downloads may go, from no limit to a trickle, in small steps, saved when it is let go;
// or type an exact speed. Uploads follow at a quarter of the download limit, since uploading is what slows the
// house's internet most. Under it, the last five minutes of speed, and what every download is doing.
const SPEED_STEPS = [0, 100, 75, 50, 40, 30, 25, 20, 15, 12, 10, 8, 6, 5, 4, 3, 2.5, 2, 1.5, 1, 0.75, 0.5, 0.25, 0.1].map((mb) => Math.round(mb * 1048576));
const SPEED_HISTORY = 60;  // checks kept for the chart, five seconds apart
function speedPanel() {
  const box = el('div', { class: 'stack' }, el('p', { class: 'small dim', role: 'status', text: 'Asking qBittorrent…' }));
  const now = el('span', { class: 'small dim' });
  const summary = el('strong', { text: 'Speed' });
  const counts = el('p', { class: 'small', role: 'status' });
  const chart = el('div', { class: 'speed-chart' });
  const history = [];
  const gen = routeGen;
  let limitNow = 0;
  const words = (limit) => limit ? 'Up to ' + speed(limit) : 'No limit: as fast as it goes';
  function save(limit) {
    const up = limit ? Math.max(65536, Math.round(limit / 4)) : 0;
    api('POST', '/api/media/speed', { down_limit: limit, up_limit: up, slow_mode: false })
      .then(() => { limitNow = limit; summary.textContent = 'Speed: ' + (limit ? 'up to ' + speed(limit) : 'no limit'); toast(limit ? 'Downloads now go up to ' + speed(limit) + ', uploads up to ' + speed(up) + '.' : 'Downloads go as fast as they can.'); })
      .catch((err) => { if (!(err instanceof Stale)) { oops(err); drawn = false; } });  // the next check puts the slider back
  }
  function draw(info) {
    const limit = info.slow_mode ? info.slow_down : info.down_limit;
    limitNow = limit;
    summary.textContent = 'Speed: ' + (limit ? 'up to ' + speed(limit) : 'no limit');
    // The step closest to the current limit (a limit typed in or set elsewhere shows on the nearest).
    let at = 0;
    SPEED_STEPS.forEach((step, index) => { if (Math.abs(Math.log((step || 1e12) / (limit || 1e12))) < Math.abs(Math.log((SPEED_STEPS[at] || 1e12) / (limit || 1e12)))) at = index; });
    const label = el('strong', { class: 'speed-now', text: words(limit) });
    const canChange = info.can_change !== false;
    const exact = el('input', { type: 'number', id: 'speed-exact', min: '0', step: '0.1', inputmode: 'decimal', value: limit ? (limit / 1048576).toFixed(1) : '', placeholder: 'No limit',
      disabled: !canChange, 'aria-label': 'Exact download limit in megabytes per second, empty for no limit' });
    const slider = el('input', { type: 'range', min: '0', max: String(SPEED_STEPS.length - 1), step: '1', value: String(at), disabled: !canChange,
      'aria-label': 'Download speed limit', 'aria-valuetext': words(limit),
      oninput: (event) => {
        const picked = SPEED_STEPS[+event.target.value];
        label.textContent = words(picked);
        exact.value = picked ? (picked / 1048576).toFixed(1) : '';
        event.target.setAttribute('aria-valuetext', words(picked));
      },
      onchange: (event) => save(SPEED_STEPS[+event.target.value]) });
    const setExact = el('form', { class: 'row gap8', onsubmit: (event) => {
      event.preventDefault();
      const mb = parseFloat(exact.value);
      const typedLimit = exact.value.trim() === '' || !(mb > 0) ? 0 : Math.max(16384, Math.round(mb * 1048576));
      label.textContent = words(typedLimit);
      save(typedLimit);
    } }, el('label', { class: 'small dim', for: 'speed-exact', text: 'Or exactly' }), exact, el('span', { class: 'small dim', text: 'MB/s' }),
      el('button', { class: 'btn sm', type: 'submit', disabled: !canChange, text: 'Set' }));
    fill(box, label,
      el('div', { class: 'speed-slider' }, el('span', { class: 'small dim', text: 'Fastest' }), slider, el('span', { class: 'small dim', text: 'Slowest' })),
      setExact,
      el('p', { class: 'small dim', text: canChange ? 'Slide right when someone needs the internet; downloads keep going, just slower.' : 'This profile can see the speed but not change it.' }),
      counts, chart);
  }
  function tell(info) {
    const tally = info.counts || {};
    const bits = [[tally.downloading, 'downloading now'], [tally.waiting_turn, 'waiting their turn'], [tally.waiting_seed, 'waiting for seed'], [tally.paused, 'paused'], [tally.done, 'done']]
      .filter(([count]) => count).map(([count, what]) => count + ' ' + what);
    counts.textContent = bits.length ? bits.join(' · ') + (info.at_once ? ' · ' + info.at_once + ' download at once' : '') : 'Nothing is downloading.';
    history.push({ down: info.down_now, up: info.up_now, limit: info.slow_mode ? info.slow_down : info.down_limit });
    if (history.length > SPEED_HISTORY) history.shift();
    fill(chart, speedChart(history));
  }
  // The controls are drawn from the first answer, or the first one after qBittorrent comes back or a change fails.
  async function load() {
    try {
      const data = await api('GET', '/api/media/speed');
      now.textContent = 'Now: ' + speed(data.down_now) + ' down, ' + speed(data.up_now) + ' up';
      if (!drawn) { drawn = true; draw(data); }
      tell(data);
    } catch (err) {
      if (err instanceof Stale) return;
      now.textContent = '';
      if (!drawn) fill(box, el('p', { class: 'small bad', text: err.message }));
    }
    setTimeout(() => { if (gen === routeGen && document.body.contains(box)) load(); }, 5000);
  }
  let drawn = false;
  load();
  // Folded to one line: the limit and how fast things are going now. Opened to change it, with the chart.
  return el('section', { class: 'panel cut' }, el('details', { class: 'speed-fold' },
    el('summary', { class: 'row' }, summary, now, el('span', { class: 'small', style: 'margin-left:auto;color:var(--accent)', text: 'Change' })), box));
}

// Admins: films and TV on the disk that are not filed yet (finished downloads, or copied in from another disk).
// Radarr and Sonarr say what each file is; one they cannot place is searched for by hand. Spare copies of
// something already in the library can be deleted.
function sortPanel() {
  let kind = 'film';
  const out = el('div', { class: 'stack' });
  const where = el('p', { class: 'small dim' });
  const kinds = el('div', { class: 'row gap8', role: 'group', 'aria-label': 'Films or TV' });
  const gen = routeGen;
  const SECTIONS = [['ready', 'Ready to file'], ['better', 'Better than the copy you have'], ['unknown', 'Not recognised: say what it is'],
    ['have', 'Spare copies: already in the library'], ['problem', 'Cannot be filed']];
  function drawKinds() {
    fill(kinds, [['film', 'Films'], ['show', 'TV']].map(([key, label]) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(kind === key), text: label,
      onclick: () => { kind = key; drawKinds(); look(); } })));
  }
  async function send(btn, path, body, after) {
    btn.disabled = true;
    try { const result = await api('POST', path, Object.assign({ kind }, body)); toast(result.message); if (after) after(result); } catch (err) { if (!(err instanceof Stale)) oops(err); }
    btn.disabled = false;
  }
  function identify(item) {
    const searchBox = el('input', { type: 'search', value: item.guess || '', 'aria-label': 'What is ' + item.name + '?', style: 'flex:1 1 220px;min-width:0' });
    const results = el('div', { class: 'stack gap6' });
    const find = async (event) => {
      event.preventDefault();
      fill(results, el('p', { class: 'small dim', role: 'status', text: 'Searching…' }));
      try {
        const data = await api('GET', '/api/media/search?kind=' + kind + '&q=' + encodeURIComponent(searchBox.value.trim()));
        fill(results, data.results.length ? data.results.slice(0, 6).map((result) => el('div', { class: 'row between gap8' },
          el('span', { text: result.title + (result.year ? ' (' + result.year + ')' : '') }),
          el('button', { class: 'btn sm', type: 'button', text: "It's this one", onclick: (ev) => send(ev.currentTarget, '/api/admin/media/sort',
            { path: item.path, external_id: kind === 'film' ? result.tmdb : result.tvdb }, look) })))
          : el('p', { class: 'small dim', role: 'status', text: 'Nothing found. Try fewer words.' }));
      } catch (err) { if (!(err instanceof Stale)) fill(results, el('p', { class: 'small bad', role: 'status', text: err.message })); }
    };
    return el('div', { class: 'stack', style: 'gap:6px;margin-top:6px' },
      el('form', { class: 'row gap8', role: 'search', onsubmit: find }, searchBox, el('button', { class: 'btn sm', type: 'submit', text: 'Search' })), results);
  }
  function row(item) {
    const name = item.title ? item.title + (item.year ? ' (' + item.year + ')' : '') + (item.episodes ? ' · ' + item.episodes : '') : item.name;
    const facts = [item.quality, gb(item.size), item.unsorted ? 'from the unsorted folder' : 'a finished download',
      item.copies_here > 1 ? item.copies_here + ' copies here' + (item.biggest ? ', this is the biggest' : ', a bigger one is here too') : ''];
    return el('div', { class: 'line torrent', style: 'display:block' },
      el('div', { class: 'row between gap8' },
        el('div', { class: 'stack', style: 'gap:2px;min-width:0;flex:1 1 240px' }, el('strong', { class: 'torrent-title', text: name }),
          item.title ? el('span', { class: 'small dim torrent-title', text: item.name, title: item.name }) : null),
        el('div', { class: 'row gap6' },
          item.status === 'ready' || item.status === 'better' ? el('button', { class: 'btn sm pri', type: 'button', text: 'File it',
            onclick: (event) => send(event.currentTarget, '/api/admin/media/sort', { path: item.path }, look) }) : null,
          item.status === 'have' || /short sample/.test(item.reason) ? el('button', { class: 'btn sm danger', type: 'button', text: 'Delete spare',
            onclick: (event) => { if (window.confirm('Delete "' + item.name + '" for good? The copy in the library stays.')) send(event.currentTarget,
                '/api/admin/media/sort/remove', { path: item.path }, look); } }) : null)),
      el('div', { class: 'small dim', text: facts.filter(Boolean).join(' · ') }),
      el('div', { class: 'small ' + (item.status === 'problem' ? 'bad' : item.status === 'have' ? 'warn' : ''), text: item.reason }),
      item.status === 'unknown' ? identify(item) : null);
  }
  async function look() {
    fill(out, el('p', { class: 'small dim', role: 'status', text: 'Asking ' + (kind === 'film' ? 'Radarr' : 'Sonarr') + ' what each file is…' }));
    try {
      const data = await api('GET', '/api/admin/media/sort?kind=' + kind);
      if (gen !== routeGen) return;
      where.textContent = 'Looks in ' + data.folders.downloads + ' and ' + data.folders.unsorted + '. Copy films and TV from another disk into the unsorted folder.';
      const ready = data.items.filter((item) => item.status === 'ready');
      const fileAll = ready.length > 1 ? el('button', { class: 'btn sm', type: 'button', text: 'File all ' + ready.length + ' ready', onclick: async (event) => {
        const btn = event.currentTarget;
        btn.disabled = true;
        for (const item of ready) {
          try { const result = await api('POST', '/api/admin/media/sort', { kind, path: item.path }); toast(result.message); } catch (err) { if (!(err instanceof Stale)) oops(err); }
        }
        look();
      } }) : null;
      fill(out, data.items.length ? SECTIONS.map(([state, label]) => {
        const items = data.items.filter((item) => item.status === state);
        return items.length ? [el('div', { class: 'row between', style: 'gap:8px;margin-top:8px' }, el('h3', { class: 'small',
            text: label + ' (' + items.length + ')' }), state === 'ready' ? fileAll : null),
          ...items.map(row)] : null;
      }).flat() : el('p', { class: 'small dim', text: 'Nothing to sort: every ' + (kind === 'film' ? 'film' : 'episode') + ' here is filed.' }));
    } catch (err) { if (!(err instanceof Stale)) fill(out, el('p', { class: 'small bad', role: 'status', text: err.message })); }
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
  const WIDTH = 600, HEIGHT = 120, PAD = 4;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value)); return node; };
  // the y axis tops out 10% above the busiest moment (never below 256 KB/s, or a quiet chart is all noise)
  const top = Math.max(1048576 / 4, ...points.map((point) => Math.max(point.down, point.up, point.limit || 0))) * 1.1;
  // x: the newest point is always at the right edge; with fewer points than the history holds, they bunch up on the right
  const xAt = (index) => PAD + (WIDTH - 2 * PAD) * (points.length < 2 ? 1 : index / (SPEED_HISTORY - 1)) + (WIDTH - 2 * PAD) * (SPEED_HISTORY - points.length) / (SPEED_HISTORY - 1);
  const yAt = (bytes) => HEIGHT - PAD - (HEIGHT - 2 * PAD) * Math.min(bytes, top) / top;
  const line = (key) => points.map((point, index) => xAt(index).toFixed(1) + ',' + yAt(point[key]).toFixed(1)).join(' ');
  const chart = svg('svg', { viewBox: '0 0 ' + WIDTH + ' ' + HEIGHT, preserveAspectRatio: 'none', class: 'speed-svg', role: 'img',
    'aria-label': 'Speed over the last five minutes. Now ' + speed(points[points.length - 1].down) + ' down, ' + speed(points[points.length - 1].up) + ' up.' });
  chart.append(svg('line', { x1: PAD, x2: WIDTH - PAD, y1: HEIGHT - PAD, y2: HEIGHT - PAD, class: 'sc-axis' }));
  const limit = points[points.length - 1].limit;
  if (limit && limit < top) chart.append(svg('line', { x1: PAD, x2: WIDTH - PAD, y1: yAt(limit), y2: yAt(limit), class: 'sc-limit' }));
  if (points.length > 1) {
    chart.append(svg('polyline', { points: line('up'), class: 'sc-up' }));
    chart.append(svg('polyline', { points: line('down'), class: 'sc-down' }));
  }
  const cross = svg('line', { y1: PAD, y2: HEIGHT - PAD, class: 'sc-cross', visibility: 'hidden' });
  chart.append(cross);
  const readout = el('span', { class: 'small dim', text: 'Top of the chart: ' + speed(top) });
  chart.addEventListener('pointermove', (event) => {
    const box = chart.getBoundingClientRect();
    const fx = (event.clientX - box.left) / box.width * WIDTH;
    let nearest = 0;
    points.forEach((point, index) => { if (Math.abs(xAt(index) - fx) < Math.abs(xAt(nearest) - fx)) nearest = index; });
    cross.setAttribute('x1', xAt(nearest)); cross.setAttribute('x2', xAt(nearest)); cross.setAttribute('visibility', 'visible');
    const ago = (points.length - 1 - nearest) * 5;
    readout.textContent = (ago ? ago + ' s ago: ' : 'Now: ') + speed(points[nearest].down) + ' down, ' + speed(points[nearest].up) + ' up';
  });
  chart.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); readout.textContent = 'Top of the chart: ' + speed(top); });
  return el('figure', { class: 'speed-fig' }, chart,
    el('figcaption', { class: 'row between gap8' },
      el('span', { class: 'row gap12' },
        el('span', { class: 'small key' }, el('i', { class: 'k-down' }), 'Download'),
        el('span', { class: 'small key' }, el('i', { class: 'k-up' }), 'Upload'),
        limit ? el('span', { class: 'small key' }, el('i', { class: 'k-limit' }), 'Limit') : null),
      readout));
}

// One download on the Downloads page, with its pause and move buttons.
// Other shared copies found for a stuck book download, by its hash; kept while the list redraws.
const otherCopies = new Map();
function otherCopiesBox(torrent, reload) {
  const got = otherCopies.get(torrent.hash);
  if (!got) return null;
  if (got.loading) return el('p', { class: 'small dim', role: 'status', text: 'Looking for other copies of this book that someone is sharing…' });
  if (got.error) return el('p', { class: 'small bad', role: 'status', text: got.error });
  const size = (bytes) => (bytes > 1073741824 ? (bytes / 1073741824).toFixed(1) + ' GB' : Math.max(1, Math.round(bytes / 1048576)) + ' MB');
  return el('div', { class: 'stack', style: 'gap:6px;margin-top:8px' },
    el('p', { class: 'small dim', style: 'margin:0', text: got.others.length ? 'Shared copies of "' + got.looked_for + '", the best first. Using one deletes this download and its unfinished files.'
      : 'Nobody is sharing another copy of "' + got.looked_for + '" right now. It is looked for again by itself half an hour after the sharers went.' }),
    got.others.map((other) => el('div', { class: 'row between gap8' },
      el('span', { class: 'small', style: 'min-width:0;flex:1 1 240px' }, el('strong', { text: other.title }),
        el('span', { class: 'dim', text: ' · ' + [other.format || '?', other.size ? size(other.size) : '',
            other.seeders + (other.seeders === 1 ? ' person' : ' people') + ' sharing', other.indexer].filter(Boolean).join(' · ') })),
      el('button', { class: 'btn sm pri', type: 'button', text: 'Use this copy', 'aria-label': 'Use this copy instead: ' + other.title, onclick: async (ev) => {
        ev.currentTarget.disabled = true;
        try {
          await api('POST', '/api/media/torrents/' + torrent.hash + '/swap', other);
          otherCopies.delete(torrent.hash);
          toast('Downloading "' + other.title + '" instead.');
          Tracker.follow('book', other.title);
        } catch (err) { if (!(err instanceof Stale)) oops(err); }
        ev.currentTarget.blur();
        reload();
      } }))),
    el('div', {}, el('button', { class: 'btn sm', type: 'button', text: 'Close', onclick: (ev) => { otherCopies.delete(torrent.hash); ev.currentTarget.blur(); reload(); } })));
}

// Some indexers send names with HTML escapes in them ("Club&#039;s"); shown as the characters they stand for.
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
// &#039; (decimal), &#x27; (hex) or &amp; (named): one regex handles all three
const unescapeName = (text) => String(text || '').replace(/&(#x[0-9a-f]+|#[0-9]+|amp|lt|gt|quot|apos);/gi, (whole, code) =>
  code[0] === '#' ? String.fromCodePoint(code[1] === 'x' || code[1] === 'X' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10)) : ENTITIES[code.toLowerCase()]);

function torrentRow(torrent, listing, reload) {
  torrent = Object.assign({}, torrent, { title: unescapeName(torrent.title), name: unescapeName(torrent.name) });
  const canChange = torrent.type === 'Book' ? listing.can_change_books : listing.can_change;
  const act = (action) => async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try { await api('POST', '/api/media/torrents/' + torrent.hash + '/' + action); } catch (err) { if (!(err instanceof Stale)) oops(err); }
    btn.blur();
    reload();
  };
  const makeButton = (text, action, label) => el('button', { class: 'btn sm', type: 'button', text, 'aria-label': label + ' ' + torrent.title, title: label, onclick: act(action) });
  const mins = (at) => Math.max(0, Math.round((at * 1000 - Date.now()) / 60000));
  const facts = [torrent.progress + '%', gb(torrent.size), !torrent.done && !torrent.paused && torrent.speed ? speed(torrent.speed) : '',
      !torrent.done && !torrent.paused && torrent.up_speed ? speed(torrent.up_speed) + ' up' : '',
    !torrent.done && !torrent.paused && torrent.eta ? timeLeft(torrent.eta) + ' left' : '',
    !torrent.done ? torrent.seeds + (torrent.seeds === 1 ? ' person' : ' people') + ' sharing' : '', torrent.position ? 'number ' + torrent.position + ' in line' : '',
    torrent.waiting_seed && torrent.checked ? 'last checked ' + Math.max(0, -mins(torrent.checked)) + ' min ago' : '',
    torrent.waiting_seed && torrent.next_check ? 'next check in ' + mins(torrent.next_check) + ' min' : '',
    torrent.requested_by ? 'requested by ' + torrent.requested_by : ''];
  return el('div', { class: 'line torrent', style: 'display:block' },
    el('div', { class: 'row between gap8' },
      el('div', { class: 'row', style: 'gap:8px;min-width:0;flex:1 1 240px' }, el('span', { class: 'chip xs', text: torrent.type }),
        torrent.kind && torrent.id ? el('a', { href: '#/movies/' + torrent.kind + '/' + torrent.id, class: 'torrent-title', text: torrent.title, title: torrent.name })
          : el('span', { class: 'torrent-title', text: torrent.title, title: torrent.name })),
      el('span',
          { class: 'small ' + (torrent.trouble ? 'bad' : torrent.paused || torrent.waiting_seed || /Waiting for someone/.test(torrent.state) ? 'warn' : torrent.done ? 'ok' : ''),
          text: torrent.state })),
    torrent.done ? null : el('div', { style: 'margin:8px 0' }, bar(torrent.progress, torrent.progress + ' percent downloaded')),
    el('div', { class: 'small dim', text: facts.filter(Boolean).join(' · ') }),
    torrent.waiting_seed ? el('p', { class: 'small dim', style: 'margin:4px 0 0', text: 'Nobody is sharing this right now, so it is paused at the back of the line and the rest go ahead. '
      + 'It is checked every half hour and carries on by itself when someone shares it. Continue starts it now; Find a new copy looks for another copy someone is sharing.' }) : null,
    torrent.gave_up ? el('p', { class: 'small warn', style: 'margin:4px 0 0',
        text: "Couldn't find another copy someone is sharing (looked after half an hour, an hour and three hours), so it is paused. Press Find a new copy to try again later, or Continue to start it again." }) : null,
    otherCopiesBox(torrent, reload),
    canChange || listing.can_delete ? el('div', { class: 'row', style: 'gap:6px;margin-top:8px' },
      !canChange ? null : torrent.paused ? makeButton('Continue', 'resume', 'Continue') : makeButton('Pause', 'pause', 'Pause'),
      torrent.done || !canChange || torrent.missing ? null : [makeButton('⤒ First', 'top', 'Move to the front of the line:'),
          makeButton('↑', 'up', 'Move up:'), makeButton('↓', 'down', 'Move down:'), makeButton('⤓ Last', 'bottom', 'Move to the back of the line:')],
      // Books and graphic novels: look for another copy of the same book that someone is sharing.
      torrent.type === 'Book' && !torrent.done && !torrent.missing && canChange ? el('button', { class: 'btn sm', type: 'button',
          text: 'Find a new copy', 'aria-label': 'Find a new copy of ' + torrent.title, onclick: async (ev) => {
        ev.currentTarget.blur();
        otherCopies.set(torrent.hash, { loading: true });
        reload();
        try { const result = await api('GET', '/api/media/torrents/' + torrent.hash + '/others'); otherCopies.set(torrent.hash,
            result); } catch (err) { if (!(err instanceof Stale)) otherCopies.set(torrent.hash, { error: err.message }); }
        reload();
      } }) : null,
      torrent.missing && listing.can_delete ? el('button', { class: 'btn sm', type: 'button', text: 'Forget (files stay)',
          'aria-label': 'Forget ' + torrent.title + ', leaving its files', onclick: async (ev) => {
        ev.currentTarget.disabled = true;
        try { const result = await api('POST', '/api/media/torrents/forget', { hashes: [torrent.hash] }); toast(result.message); } catch (err) { if (!(err instanceof Stale)) oops(err); }
        ev.currentTarget.blur();
        reload();
      } }) : null,
      listing.can_delete ? el('button', { class: 'btn sm danger', type: 'button', text: 'Delete', 'aria-label': 'Delete ' + torrent.title + ' everywhere', onclick: async (ev) => {
        const show = torrent.kind === 'show' ? torrent.title.split(' · ')[0] : '';
        const what = torrent.kind === 'film' ? 'Delete "' + torrent.title + '" everywhere: the download, its files, and the film from the server and Jellyfin.'
          : show ? 'Delete the whole show "' + show + '" everywhere: this download, every episode already on the server, and the show from Jellyfin. It will not be looked for again.'
          : torrent.type === 'Book' ? 'Delete the download "' + torrent.title + '" and its files. A book already added to the library stays there; delete it from its page in Books.'
          : 'Delete "' + torrent.title + '" and its downloaded files.';
        if (!window.confirm(what + ' This cannot be undone.')) return;
        const btn = ev.currentTarget;
        btn.disabled = true;
        try { const result = await api('DELETE', '/api/media/torrents/' + torrent.hash); toast(result.message); } catch (err) { if (!(err instanceof Stale)) oops(err); }
        btn.blur();  // the list is left alone while focus is in it
        reload();
      } }) : null) : null);
}

// The Request button, and for a show, which seasons to get.
function requestControls(kind, result) {
  const box = el('div', { class: 'stack gap6' });
  let mode = 'all';
  const picked = new Set();
  const seasons = el('div', { class: 'row gap6', hidden: true }, (result.seasons || []).map((season) =>
    el('button', { class: 'chip xs', type: 'button', 'aria-pressed': 'false', text: 'S' + season.number, 'aria-label': 'Season ' + season.number, onclick: (event) => {
      if (picked.has(season.number)) picked.delete(season.number); else picked.add(season.number);
      event.target.setAttribute('aria-pressed', String(picked.has(season.number)));
    } })));
  const send = el('button', { class: 'btn sm pri', type: 'button', text: 'Request', 'aria-label': 'Request ' + result.title, onclick: async () => {
    send.disabled = true;
    try {
      const added = await api('POST', '/api/media/request', { kind, id: kind === 'film' ? result.tmdb : result.tvdb, seasons: mode, picked: Array.from(picked) });
      if (added && added.id && !(added.already && added.downloaded)) Tracker.follow(kind, added.id);
      fill(box, el('div', { class: 'small ok', role: 'status' }, added && added.already ? 'Already on the list. ' : 'Requested. ',
        added && added.id ? el('a', { href: '#/movies/' + kind + '/' + added.id, text: added.already ? 'See where it is up to' : 'Watch the search happen' }) : null));
    } catch (err) { send.disabled = false; oops(err); }
  } });
  if (kind === 'show') {
    box.append(el('div', { class: 'row gap6', role: 'group', 'aria-label': 'Which seasons' }, [['all', 'Whole series'], ['latest', 'Latest season'], ['pick', 'Pick seasons']].map(([key, label]) =>
      el('button', { class: 'chip xs', type: 'button', 'aria-pressed': String(key === mode), text: label, onclick: (event) => {
        mode = key; event.target.parentNode.querySelectorAll('button').forEach((button) => button.setAttribute('aria-pressed', String(button === event.target))); seasons.hidden = key !== 'pick';
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
    const status = await api('GET', '/api/admin/media/status');
    const line = (item) => el('div', { class: 'row between' }, el('span', { text: item.label }),
      !item.configured ? el('span', { class: 'dim', text: 'Not set up' }) : el('span', { class: item.ok ? 'ok' : 'warn',
          text: item.ok ? 'Connected' + (item.version ? ' · ' + item.version : '') : 'Not reachable · ' + (item.error || '') }));
    fill(body, el('div', { class: 'list' }, line(status.radarr), line(status.sonarr), line(status.bazarr), line(status.jellyfin),
      el('div', { class: 'row between' }, el('span', { text: 'VPN for torrents' }), el('span', { class: status.vpn.ok ? 'ok' : 'warn', text: status.vpn.message }))));
  }
  const go = el('button', { class: 'btn pri', type: 'button', text: 'Connect everything', onclick: async () => {
    go.disabled = true; go.textContent = 'Connecting…';
    try {
      const result = await api('POST', '/api/admin/media/connect');
      fill(results, ...result.steps.map((step) => el('div', { class: 'row between' }, el('span', { text: step.step }), el('span',
          { class: 'small ' + (step.ok ? 'ok' : 'warn'), text: step.message }))));
      draw().catch(oops);
    } catch (err) { oops(err); }
    go.disabled = false; go.textContent = 'Connect everything';
  } });
  draw().catch((err) => fill(body, el('p', { class: 'small bad', text: err.message })));
  return el('section', { class: 'panel cut' }, head('Films and TV', el('span', { class: 'small dim', text: 'Radarr, Sonarr, Bazarr, Jellyfin and the VPN' })),
    body,
    el('p', { class: 'small dim',
        text: 'Connect everything links Radarr and Sonarr to Prowlarr and qBittorrent, gives them their folders and the household quality (2160p first, then 1080p, then 720p, then the best there is, never remux), sets up English subtitles in Bazarr, and adds the libraries to a Jellyfin started from this project. Anything already in place is left alone, so it is safe to press again.' }),
    el('div', { class: 'row' }, go), results);
}

// ------------------------------------------------------------------ home

// The Scope, the design's showpiece on Home: today's visual (or the one an admin pinned), with its name and,
// for a visual that has them, a switch for its modes in the header.
function setPiece(look) {
  const name = el('span', { text: '' });
  const modes = el('span', { class: 'seg mini', role: 'group', hidden: true });
  let frame = null;
  frame = visualFrame(look.scene === 'daily' ? '' : look.scene, (visual) => {
    name.textContent = visual.name || '';
    const list = Array.isArray(visual.modes) ? visual.modes : [];
    modes.hidden = !list.length;
    modes.setAttribute('aria-label', (visual.name || 'Visual') + ' view');
    fill(modes, list.map(([id, label]) => el('button', { type: 'button', 'aria-pressed': String(id === visual.mode), text: label,
      onclick: () => frame.contentWindow.postMessage({ type: 'hub-mode', mode: id }, '*') })));
  });
  return el('div', { class: 'dossier holo' },
    el('div', { class: 'cell dhead' }, el('span', { text: look.piece.title }), el('span', { class: 'dim', text: look.piece.state })),
    el('div', { class: 'cell dhead' }, name, modes),
    el('div', { class: 'cell', style: 'grid-column:1/-1;line-height:0' }, frame));
}

async function pageHome() {
  const home = await api('GET', '/api/home');
  const latestMedia = el('section', { class: 'panel cut' }, head('Latest films and TV', el('a', { class: 'btn sm', href: '#/movies', text: 'Open Films and TV' })),
    el('p', { class: 'small dim', role: 'status',
        text: appState.media.jellyfin ? 'Asking Jellyfin…' : 'Films and series arrive here once Jellyfin is connected' + (isAdmin() ? ' under Admin.' : ' by an admin.') }));
  if (appState.media.jellyfin && !isGuest()) {
    api('GET', '/api/media/latest').then((data) => latestMedia.replaceChildren(latestMedia.firstChild,
      data.items.length ? el('div', { class: 'reel' }, data.items.map((item) => posterCard(Object.assign({}, item, { id: null }),
        el('div', { class: 'small', text: niceDate(item.added) })))) : el('p', { class: 'small dim', text: 'No films or shows on the server yet.' })))
      .catch((err) => latestMedia.replaceChildren(latestMedia.firstChild, el('p', { class: 'small warn', text: 'Jellyfin: ' + err.message })));
  }
  const books = home.books.map((book) => el('div', { class: 'book' },
    el('a', { class: 'cover' + (book.has_cover ? ' has-img' : '') + titleSize(book.title), title: book.title, href: '#/book/' + book.id,
        style: '--cv:' + coverColor(book.title) + (book.has_cover ? ';background-image:url(' + coverUrl(book) + ')' : '') },
      el('span', { class: 'fmt', text: book.formats.includes('EPUB') ? 'EPUB' : (book.formats[0] || '') }), el('span', { class: 't', text: book.title })),
    el('div', { class: 'small dim', text: book.author }),
    el('div', { class: 'small', text: niceDate(book.added) })));
  // News from the watchlist: shown only when something new has turned up since the last visit to it.
  const watchNews = el('section', { class: 'panel cut', hidden: true });
  if (!isGuest()) {
    api('GET', '/api/watchlist').then((data) => {
      const fresh = data.items.flatMap((item) => (item.found || []).filter((hit) => hit.new).map((hit) => ({ x: item, f: hit })));
      if (!fresh.length) return;
      fill(watchNews, head('News from your watchlist', el('a', { class: 'btn sm', href: '#/watchlist', text: 'Open the watchlist' })),
        el('div', { class: 'list' }, fresh.slice(0, 5).map(({ x: watched, f: hit }) => el('div', { class: 'row between gap8' },
          el('span', { text: hit.title + (watched.kind === 'author' ? ' · ' + watched.value : '') }), el('span', { class: 'small ' + (hit.coming ? 'warn' : 'dim'), text: hit.words })))));
      watchNews.hidden = false;
    }).catch(() => {});
  }
  setMain(
    pageHead('Welcome home', 'Everything on the family server, newest first.'),
    setPiece(appState.look),
    watchNews,
    isGuest() ? null : latestMedia,
    el('section', { class: 'panel cut' }, head('Latest books', el('span', { class: 'small dim', text: plural(home.books_month,
        'book') + ' added this month' }), el('a', { class: 'btn sm', href: '#/library', text: 'Open books' })),
      books.length ? el('div', { class: 'reel' }, books) : el('p', { class: 'small dim', text: 'No books in the library yet.' })));
}

// A section that is not built yet. Says so plainly, with no made-up content.
function pageSoon() {
  const page = location.hash.replace(/^#\/?/, '').split('/')[0];
  const what = { photos: ['Photos', 'The family photo albums, once the home server has a photo service.'],
    files: ['Files', 'Shared folders on the home server.'] }[page] || ['Coming soon', ''];
  setMain(pageHead(what[0], what[1]),
    el('section', { class: 'panel cut soon' }, head('Coming soon'),
      el('p', { text: 'This part of Family Hub is not built yet, so there is nothing here to set up or change.' })));
}

// ----------------------------------------------------------------- setup

function pageSetup() {
  const name = el('input', { type: 'text', autocomplete: 'off' });
  const pass = el('input', { type: 'password', autocomplete: 'new-password' });
  setMain(el('form', { class: 'panel cut narrow', onsubmit: async (event) => {
    event.preventDefault();
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
    try { await api('POST', '/api/login', body); goHome(); return true; } catch (err) { oops(err); return false; }
  }
  function ask(profile, door) {
    if (chosen) chosen.setAttribute('aria-pressed', 'false');
    chosen = door; door.setAttribute('aria-pressed', 'true');
    const pass = el('input', { type: 'password', autocomplete: 'off', style: 'font:400 30px/1 var(--font-num);letter-spacing:.5em;min-height:52px' });
    const admins = appState.profiles.filter((admin) => admin.role === 'admin' && admin.id !== profile.id);
    const adminSel = el('select', {}, admins.map((admin) => el('option', { value: admin.id, text: admin.name })));
    const adminPass = el('input', { type: 'password', autocomplete: 'off' });
    const override = el('div', { class: 'stack', hidden: true },
      el('p', { class: 'small dim', text: 'An admin can open this profile with their own passcode.' }),
      el('div', { class: 'row end' }, field('Admin', adminSel), field('Admin passcode', adminPass),
        el('button', { class: 'btn', type: 'button', text: 'Open as admin',
            onclick: async () => { if (!await enter({ profile_id: profile.id, admin_id: Number(adminSel.value),
            admin_passcode: adminPass.value })) adminPass.select(); } })));
    // Cancel (or Escape) closes the box and puts focus back on the door that opened it.
    const cancel = () => { door.setAttribute('aria-pressed', 'false'); chosen = null; fill(panel); door.focus(); };
    fill(panel, el('form', { class: 'panel cut focus', 'aria-label': 'Enter passcode for ' + profile.name,
      onkeydown: (event) => { if (event.key === 'Escape') { event.preventDefault(); cancel(); } },
      // A wrong passcode stays selected, so typing again replaces it.
      onsubmit: async (event) => { event.preventDefault(); if (!await enter({ profile_id: profile.id, passcode: pass.value })) pass.select(); } },
      head('Passcode for ' + profile.name, el('span', { class: 'small dim', text: 'Locked' })),
      field('Passcode', pass, 'wide'),
      el('div', { class: 'row' },
        el('button', { class: 'btn pri', type: 'submit', style: 'flex:1 1 140px', text: 'Unlock' }),
        el('button', { class: 'btn', type: 'button', style: 'flex:1 1 100px', text: 'Cancel', onclick: cancel })),
      admins.length ? el('div', { class: 'line between' }, el('span', { class: 'small dim grow', text: 'Forgotten it? An admin can open this profile or reset the passcode.' }),
        el('button', { type: 'button', class: 'btn line2', text: 'Admin override', onclick: () => { override.hidden = false; adminPass.focus(); } })) : null,
      override));
    pass.focus();
  }
  const doors = appState.profiles.map((profile, index) => {
    const door = el('button', { class: 'door', type: 'button', 'aria-pressed': 'false', style: '--tone:' + TONES[index % TONES.length] },
      el('span', { class: 'glass glow', 'aria-hidden': 'true', text: profile.name[0].toUpperCase() }),
      el('span', { class: 'name', text: profile.name }),
      el('span', { class: 'tag' + (profile.role === 'admin' ? ' on' : ''), text: profile.role === 'admin' ? 'Admin' : 'Member' }),
      el('span', { class: 'small dim', text: profile.has_passcode ? 'Passcode on' : 'No passcode' }),
      // Admins can always find new books, whatever their own switch says, so they are never "Reading only".
      profile.tracker_linked ? el('span', { class: 'small dim',
          text: 'Kindle tracking linked' }) : (profile.can_request || profile.role === 'admin' ? null : el('span', { class: 'small dim',
          text: 'Reading only' })));
    door.addEventListener('click', () => (profile.has_passcode ? ask(profile, door) : enter({ profile_id: profile.id })));
    return door;
  });
  if (appState.guest_enabled) doors.push(el('button', { class: 'door', type: 'button', style: '--tone:var(--dim)', onclick: () => enter({ guest: true }) },
    el('span', { class: 'glass', 'aria-hidden': 'true' }, icon('M8 8a4 4 0 1 0 8 0a4 4 0 1 0 -8 0M4 21c0-4 3.6-7 8-7s8 3 8 7')), el('span',
        { class: 'name', text: 'Guest' }), el('span', { class: 'tag', text: 'No passcode' }),
    el('span', { class: 'small dim', text: 'Browse and download books' }), el('span', { class: 'small dim', text: 'Nothing is tracked' })));
  if (isAdmin()) doors.push(el('a', { class: 'door add', href: '#/admin' }, el('span', { style: 'font:400 64px/1 var(--font-num)', 'aria-hidden': 'true', text: '+' }),
    el('span', { class: 'name', text: 'Add profile' }), el('span', { class: 'small', text: 'Admins only' })));

  setMain(
    el('div', { class: 'col center gap14' }, el('h1', { class: 'glow', text: "Who's here?" }),
      el('div', { class: 'rule', style: 'width:min(100%,420px)' }, starMark()),
      el('p', { class: 'dim', style: 'max-width:60ch', text: 'Everyone shares the family server. Each profile keeps its own reading, ratings, devices and requests.' })),
    el('div', { class: 'doors' }, doors), panel,
    appState.me && !isGuest() ? mySettings() : null,
    appState.me ? el('div', { class: 'row', style: 'justify-content:center' },
      el('button', { class: 'btn', type: 'button', text: 'Sign out of ' + appState.me.name, onclick: async () => { try { await api('POST',
          '/api/logout'); route(); } catch (err) { oops(err); } } })) : null);
}

function mySettings() {
  const save = (body, done) => api('PATCH', '/api/profiles/' + appState.me.id, body).then(() => { toast(done); route(); }).catch(oops);
  const pass = el('input', { type: 'password', autocomplete: 'new-password' });
  const sync = el('input', { type: 'password', autocomplete: 'new-password' });
  const token = el('input', { type: 'password', autocomplete: 'off', placeholder: appState.me.tracker_linked ? 'Saved. Paste a new key to replace it' : '' });
  const syncUrl = location.origin + '/kosync';
  return el('section', { class: 'panel cut' },
    head('Settings for ' + appState.me.name),
    el('div', { class: 'list' },
      el('div', { class: 'stack' }, el('h3', { text: 'Passcode' }),
        el('div', { class: 'row end' }, field('New passcode (leave empty to remove)', pass),
          // An empty box removes the passcode, so say so before doing it rather than after.
          el('button', { class: 'btn', type: 'button', text: 'Save passcode', onclick: () => {
            if (!pass.value && !window.confirm('Remove your passcode? Anyone using this hub could then open your profile.')) return;
            save({ passcode: pass.value }, pass.value ? 'Passcode saved.' : 'Passcode removed.');
          } }))),
      el('div', { class: 'stack' }, el('h3', { text: 'Reading app sync' }),
        el('p', { class: 'small dim',
            text: 'One password signs your reading apps in to your book catalogue and to progress sync. In Readest or KOReader choose a custom KOReader sync server and enter: server ' + syncUrl + ' · user name ' + appState.me.name + ' · the sync password you set here.' }),
        el('p', { class: 'small ' + (appState.me.sync_ready ? 'ok' : 'dim'), text: appState.me.sync_ready ? 'A sync password is set.' : 'No sync password yet.' }),
        el('div', { class: 'row end' }, field('Sync password', sync),
          el('button', { class: 'btn', type: 'button', text: 'Save sync password', onclick: () => save({ sync_password: sync.value }, 'Sync password saved.') }))),
      devicesBox(appState.me.id),
      goodreadsBox(appState.me.id),
      el('div', { class: 'stack' }, el('h3', { text: 'Kindle and other reading (experimental)' }),
        el('p', { class: 'small dim', text: 'Paste a Hardcover API key to pull in books marked as reading or read there, such as Kindle books passed along from Goodreads.' }),
        el('div', { class: 'row end' }, field('Hardcover API key', token),
          el('button', { class: 'btn', type: 'button', text: 'Save key', onclick: () => save({ hardcover_token: token.value }, 'Key saved.') }),
          appState.me.tracker_linked ? el('button', { class: 'btn', type: 'button', text: 'Remove key', onclick: () => save({ hardcover_token: '' }, 'Key removed.') }) : null))));
}

// A profile's Goodreads link. The link itself is only ever shown to its owner.
function goodreadsBox(profileId) {
  const box = el('div', { class: 'stack' }, el('h3', { text: 'Goodreads' }), el('p', { class: 'small dim', text: 'Loading…' }));
  const url = '/api/profiles/' + profileId + '/goodreads';
  let polls = 0;
  function show(info) {
    const input = el('input', { type: 'url', value: info.url, autocomplete: 'off',
        placeholder: info.linked && !info.mine ? 'Saved. Paste a new link to replace it' : 'https://www.goodreads.com/user/show/…' });
    const file = el('input', { type: 'file', accept: '.csv,text/csv' });
    const send = (body, msg) => api('POST', url, body).then((reply) => { if (msg) toast(msg); polls = 0; show(reply); }).catch(oops);
    let state = 'Not linked.';
    if (info.busy) state = 'Reading the shelves from Goodreads… this can take a minute for a long list.';
    else if (info.linked && info.synced) state = 'Linked. Last read ' + ago(info.synced).toLowerCase() + ' · ' + plural(info.books, 'book') + ' taken from Goodreads. Checked again every six hours.';
    else if (info.linked) state = 'Linked, not read yet.';
    else if (info.books) state = 'Not linked. ' + plural(info.books, 'book') + ' came from an export file.';
    fill(box, el('h3', { text: 'Goodreads' }),
      el('p', { class: 'small dim',
          text: 'Paste the address of ' + (info.mine ? 'your' : 'their') + ' Goodreads profile. Family Hub reads the Read and Currently reading shelves into ' + (info.mine ? 'your' : 'their') + ' reading history, with ratings. Books the library does not have are listed for the family as "Available on Kindle", without saying whose they are. Only ' + (info.mine ? 'you see' : 'they see') + ' this link.' }),
      el('p', { class: 'small ' + (info.linked && !info.note ? 'ok' : 'dim'), role: 'status', text: state }),
      info.note && !info.busy ? el('p', { class: 'small warn', text: info.note }) : null,
      el('div', { class: 'row end' }, field('Goodreads profile link', input),
        el('button', { class: 'btn', type: 'button', text: 'Save link', onclick: () => send({ link: input.value }, input.value.trim() ? 'Link saved. Reading the shelves now.' : 'Link removed.') }),
        info.linked ? el('button', { class: 'btn', type: 'button', disabled: info.busy, text: 'Read shelves now', onclick: () => send({ sync: true }, 'Reading the shelves now.') }) : null,
        info.linked ? el('button', { class: 'btn', type: 'button', text: 'Remove link',
            onclick: () => { if (window.confirm('Remove the Goodreads link? Reading history already taken from it stays; the "Available on Kindle" books from it are no longer listed.')) send({ link: '' },
            'Link removed.'); } }) : null),
      el('div', { class: 'row end' }, field('Or import a Goodreads export file (.csv)', file),
        el('button', { class: 'btn', type: 'button', text: 'Import file', onclick: async () => {
          if (!file.files[0]) return toast('Choose the export file first.', true);
          const fd = new FormData(); fd.append('file', file.files[0]);
          try { const result = await api('POST', url + '/import', fd); toast(plural(result.books,
              'book') + ' read from the file, ' + result.changed + ' new or changed.'); draw(); } catch (err) { oops(err); }
        } })),
      el('p', { class: 'small dim',
          text: 'The link only works while the Goodreads profile is public. For a private profile, use the export file: on Goodreads open My Books, Import and export, Export library.' }));
    if (info.busy && polls++ < 40) setTimeout(() => { if (box.isConnected) draw(); }, 3000);
  }
  const draw = () => api('GET', url).then(show).catch((err) => fill(box, el('h3', { text: 'Goodreads' }), el('p', { class: 'small bad', text: err.message })));
  draw();
  return box;
}

// Devices and reading apps for one profile. Shown in your own settings, and to admins for anyone.
function devicesBox(profileId) {
  const box = el('div', { class: 'stack' }, el('h3', { text: 'Devices and reading apps' }), el('p', { class: 'small dim', text: 'Loading…' }));
  const mine = profileId === appState.me.id;
  let editing = null;
  async function draw() {
    const data = await api('GET', '/api/profiles/' + profileId + '/devices');
    const who = data.profile.name;
    const change = (id, body, msg) => api('PATCH', '/api/devices/' + id, body).then(() => { toast(msg); draw(); }).catch(oops);
    const help = (dev) => {
      if (dev.kind === 'kindle') {
        return [el('span', { class: 'small dim', text: 'Books are emailed to ' + dev.address + ', up to ' + data.limit_mb + ' MB each. Comics cannot be sent.' }),
          data.mail_ready ? el('span', { class: 'small dim',
              text: 'On Amazon, add ' + data.sender + ' as an approved sender for this Kindle: Manage Your Content and Devices → Preferences → Personal Document Settings.' })
            : el('span', { class: 'small warn', text: 'Sending by email is not set up yet. An admin can add it under Admin → Hub → Connections.' })];
      }
      if (dev.kind === 'readest' || dev.kind === 'koreader') {
        return [el('span', { class: 'small dim',
            text: 'In the app, add a catalogue (OPDS) with address ' + location.origin + '/opds · user name ' + who + ' · the sync password. "My books" there lists the books chosen for ' + (mine ? 'you' : who) + '.' }),
          el('span', { class: 'small dim', text: 'Reading position: KOReader sync server ' + location.origin + '/kosync, same name and password.' }),
          data.profile.sync_ready ? null : el('span', { class: 'small warn', text: mine ? 'Set a sync password above first.' : who + ' has no sync password yet. It is set in their own settings.' })];
      }
      if (dev.kind === 'kobo') return [el('span', { class: 'small dim',
          text: "Open Family Hub in the Kobo's browser and download books from there. With KOReader installed on the Kobo, add it as a KOReader device instead to get the catalogue and progress sync." })];
      return [];
    };
    const kindName = Object.fromEntries(appState.device_kinds);
    const list = data.devices.length ? el('div', { class: 'list' }, data.devices.map((dev) => el('div', { class: 'stack gap6' },
      el('div', { class: 'row between' },
        el('div', { class: 'row' }, el('strong', { text: dev.name }), el('span', { class: 'tag', text: kindName[dev.kind] || dev.kind })),
        el('div', { class: 'row' },
          dev.kind === 'kindle' ? el('button', { class: 'chip', type: 'button', 'aria-pressed': String(dev.auto_send),
              'aria-label': 'Send new books to ' + dev.name + ' automatically', text: dev.auto_send ? 'Sends automatically' : 'Send automatically: off',
              onclick: () => change(dev.id, { auto_send: !dev.auto_send },
              dev.auto_send ? 'Books will only be sent when you press Send.' : 'Books added to My books will be sent straight away.') }) : null,
          dev.link ? el('a', { class: 'btn', href: dev.link, target: '_blank', rel: 'noopener', 'aria-label': 'Open ' + dev.name + ' in a new tab', text: 'Open' }) : null,
          el('button', { class: 'btn', type: 'button', text: 'Change', 'aria-label': 'Change ' + dev.name, onclick: () => { editing = dev; draw(); } }),
          el('button', { class: 'btn danger', type: 'button', text: 'Remove', 'aria-label': 'Remove ' + dev.name, onclick: async () => {
            if (!window.confirm('Remove ' + dev.name + ' from the list? Books already on it stay on it.')) return;
            try { await api('DELETE', '/api/devices/' + dev.id); toast('Device removed.'); draw(); } catch (err) { oops(err); }
          } }))),
      help(dev)))) : el('p', { class: 'small dim', text: 'No devices yet.' });

    const kind = el('select', {}, appState.device_kinds.map(([key, label]) => el('option', { value: key, text: label, selected: editing ? editing.kind === key : key === 'kindle' })));
    const name = el('input', { type: 'text', maxlength: 60, value: editing ? editing.name : '', placeholder: "Nan's Kindle" });
    const address = el('input', { type: 'email', value: editing ? editing.address : '', placeholder: 'name@kindle.com' });
    const link = el('input', { type: 'url', value: editing ? editing.custom_link : '', placeholder: 'https://' });
    const auto = el('input', { type: 'checkbox', checked: editing ? editing.auto_send : true });
    const addressField = field('Send-to-Kindle email address', address);
    const autoField = el('label', { class: 'check' }, auto, 'Send books automatically when they are added to My books');
    const showKindle = () => { addressField.hidden = autoField.hidden = kind.value !== 'kindle'; };
    kind.addEventListener('change', showKindle);
    showKindle();
    const form = el('form', { class: 'stack', onsubmit: async (event) => {
      event.preventDefault();
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
      el('p', { class: 'small dim',
          text: 'Where ' + (mine ? 'you read' : who + ' reads') + '. Reading apps fetch books from a private catalogue. A Kindle is sent books by email. Only ' + (mine ? 'you' : who) + ' and admins can see this list.' }),
      list, form);
  }
  draw().catch((err) => fill(box, el('p', { class: 'bad', text: err.message })));
  return box;
}

async function pageDevices(id) {
  // a bare #/devices means my own
  const profile = appState.profiles.find((candidate) => candidate.id === Number(id || appState.me.id));
  if (!profile || (!isAdmin() && profile.id !== appState.me.id)) { location.hash = '#/profiles'; return; }
  setMain(back(isAdmin() ? '#/admin' : '#/profiles', isAdmin() ? 'Back to admin' : 'Back to profiles'),
    el('h1', { class: 'glow', text: 'Devices for ' + profile.name }),
    el('section', { class: 'panel cut' }, devicesBox(profile.id)),
    el('section', { class: 'panel cut' }, goodreadsBox(profile.id)));
}

// --------------------------------------------------------------- library

function coverUrl(book) { return '/api/books/' + book.id + '/cover' + (book.cover_v ? '?v=' + encodeURIComponent(book.cover_v) : ''); }

// Choosing a different cover for a book. The picture is kept in Family Hub; the library is not changed.
function coverBox(book, again) {
  const panel = el('div', { class: 'stack gap8', hidden: true });
  const url = '/api/books/' + book.id + '/cover';
  const done = (msg) => { toast(msg); again(); };
  const file = el('input', { type: 'file', accept: 'image/*', 'aria-label': 'Picture to use as the cover' });
  const found = el('div', { class: 'row', role: 'status' });
  const look = el('button', { class: 'btn', type: 'button', text: 'Look for covers', onclick: async () => {
    look.disabled = true; fill(found, el('span', { class: 'small dim', text: 'Asking Open Library…' }));
    try {
      const result = await api('GET', url + '/options');
      fill(found, ...(result.options.length ? result.options.map((option, index) => el('button', { class: 'btn', type: 'button',
          style: 'padding:2px', 'aria-label': 'Use cover ' + (index + 1) + ': ' + option.label, title: option.label,
        onclick: () => api('POST', url, { url: option.url }).then(() => done('Cover changed.')).catch(oops) },
        el('img', { src: '/api/covers/preview?' + qs({ url: option.url.replace('-L.jpg', '-M.jpg') }), alt: '', loading: 'lazy',
            style: 'width:86px;height:129px;object-fit:cover;border-radius:4px;display:block' })))
        : [el('span', { class: 'small dim', text: 'Open Library has no cover for a book with this title and author.' })]));
    } catch (err) { fill(found, el('span', { class: 'small bad', text: err.message })); }
    look.disabled = false;
  } });
  panel.append(
    el('p', { class: 'small dim', text: 'The new cover is kept in Family Hub. The library and the book file are not changed.' }),
    el('div', { class: 'row end' }, file, el('button', { class: 'btn', type: 'button', text: 'Use this picture', onclick: async () => {
      if (!file.files[0]) return toast('Choose a picture first.', true);
      const fd = new FormData(); fd.append('file', file.files[0]);
      try { await api('POST', url, fd); done('Cover changed.'); } catch (err) { oops(err); }
    } })),
    el('div', { class: 'row' }, look,
      el('button', { class: 'btn', type: 'button', text: 'Make a title cover', onclick: () => api('POST', url, { generated: true }).then(() => done('Cover made from the title.')).catch(oops) }),
      book.own_cover ? el('button', { class: 'btn', type: 'button', text: "Back to the library's cover", onclick: () => api('DELETE',
          url).then(() => done("Back to the library's cover.")).catch(oops) }) : null),
    found);
  const toggle = el('button', { class: 'btn', type: 'button', 'aria-expanded': 'false', text: 'Change cover',
      onclick: () => { panel.hidden = !panel.hidden; toggle.setAttribute('aria-expanded', String(!panel.hidden)); } });
  return el('div', { class: 'stack gap8' }, toggle, panel);
}

// What to type into the book search for a book the library lacks.
function findQuery(book) { return (book.title.split(':')[0] + ' ' + book.author.split(/[&,]/)[0]).replace(/\s+/g, ' ').trim(); }
function goodreadsLink(book) {
  return el('a', { class: 'small', href: 'https://www.goodreads.com/book/show/' + encodeURIComponent(book.goodreads_id), target: '_blank',
      rel: 'noopener', 'aria-label': 'View ' + book.title + ' on Goodreads', text: 'View on Goodreads' });
}

// The rating on a card: this person's own, and what readers at large gave it.
function readersText(readers) { return readers.rating.toFixed(1) + ' of 5 on ' + readers.source + (readers.count ? ' (' + plural(readers.count, 'rating') + ')' : ''); }
function ratingLine(book) {
  const mine = !isGuest() && book.rating ? 'My rating: ' + book.rating + ' of 5' : '';
  const theirs = book.readers ? 'Readers: ' + readersText(book.readers) : '';
  if (!mine && !theirs) return null;
  return el('div', { class: 'small' }, mine ? el('div', { text: mine }) : null, theirs ? el('div', { class: 'dim', text: '★ ' + theirs }) : null);
}

function bookCard(book, onChange, opts) {
  // A series followed chapter by chapter has no book page or cover of its own (its id is empty), so it gets its series card.
  if (book.chapter_series) return chapterCard(book, onChange);
  const fmt = !book.in_library ? (book.kindle ? 'KINDLE' : 'ELSEWHERE') : (book.formats.includes('EPUB') ? 'EPUB' : (book.formats[0] || ''));
  const coverProps = { class: 'cover' + (book.has_cover ? ' has-img' : '') + titleSize(book.title), title: book.title,
    style: '--cv:' + coverColor(book.title) + (book.has_cover ? ';background-image:url(' + coverUrl(book) + ')' : '') };
  const inner = [el('span', { class: 'fmt', text: fmt }), el('span', { class: 't', text: book.title }), langBadge(book)];
  const picture = book.in_library ? el('a', Object.assign({ href: '#/book/' + book.id }, coverProps), inner) : el('div', coverProps, inner);
  // A book this person has finished is shown faded, with a note across it.
  const done = book.status === 'finished' && !isGuest() && !(opts && opts.plain);
  const under = book.status === 'reading' && !isGuest() && !(opts && opts.plain);
  const cover = el('div', { class: 'coverwrap' + (done ? ' done' : '') }, picture,
    done ? el('span', { class: 'readnote', text: 'Already read' }) : null,
    under ? el('span', { class: 'readnote going', text: 'Reading' + (book.percent ? ', ' + Math.round(book.percent) + '%' : '') }) : null);
  const allTags = (book.kind === 'Unsorted' ? [] : [book.kind]).concat(Array.from(new Set(book.tags.epub.concat(book.tags.community))));
  const genre = allTags.slice(0, 3).join(' · ');
  const moreTags = allTags.length - 3;
  let status = 'Not started';
  if (book.status === 'reading') status = Math.round(book.percent) + '%' + (book.device ? ' · on ' + book.device : '') + (book.in_library ? '' : ' · reading');
  if (book.status === 'finished') status = 'Finished ' + niceDate(book.finished);
  let action = el('div', { class: 'small dim', text: 'Read outside the library' });
  if (book.kindle) {
    action = el('div', { class: 'stack gap6' }, el('strong', { class: 'small', text: 'On Kindle only' }),
      appState.me.can_request || isAdmin() ? el('a', { class: 'small', href: '#/discover/' + encodeURIComponent(findQuery(book)),
          'aria-label': 'Find the EPUB of ' + book.title, text: 'Find the EPUB' }) : null);
  }
  if (book.in_library) {
    const bestFormat = book.formats.includes('EPUB') ? 'EPUB' : book.formats[0];
    action = bestFormat ? el('a', { class: 'btn sm', href: '/api/books/' + book.id + '/download/' + bestFormat,
        'aria-label': 'Download ' + bestFormat + ': ' + book.title, text: 'Download ' + bestFormat })
      : el('div', { class: 'small dim', text: 'No file in the library' });
  }
  // Fixed lines (see .book in app.css), so the buttons line up along a row of cards.
  return el('div', { class: 'book' }, cover,
    el('div', { class: 'card-title', title: book.title, text: book.title }),
    el('div', { class: 'small dim card-line', title: book.author, text: book.author }),
    el('div', { class: 'card-tags', title: allTags.join(' · ') }, genre ? el('span', { class: 'lab', text: genre }) : null,
      moreTags > 0 ? el('span', { class: 'more', 'aria-label': moreTags + ' more tags', text: '+' + moreTags }) : null),
    book.why && book.why.length ? el('div', { class: 'small card-line', title: book.why.join(', '), text: 'Because: ' + book.why.join(', ') }) : null,
    isGuest() ? null : el('div', { class: 'small card-line', title: status, text: status }),
    el('div', { class: 'card-rating' }, ratingLine(book)),
    el('div', { class: 'card-foot' },
      action,
      book.goodreads_id ? goodreadsLink(book) : null,
      book.in_library && !isGuest() ? (book.on_shelf ? el('div', { class: 'small ok', text: 'In my books ✓' })
        : el('button', { class: 'btn sm', type: 'button', 'aria-label': 'Add to my books: ' + book.title, text: 'Add to my books',
            onclick: () => setShelf(book.id, true).then(onChange).catch(oops) })) : null));
}

// Volumes of one series in the library are shown as a single card, where the first one would have been.
// Comics gather by franchise (every Star Trek run together); other books by their series.
// The Library's order: newest first, by title, or by the author's surname. A series card goes by its series name,
// its first volume's author and its newest volume. Books with no proper author go last.
function ordered(items, order) {
  const title = (item) => (item.volumes ? item.series : item.title || '').replace(/^(the|a|an)\s+/i, '').toLowerCase();
  const added = (item) => (item.volumes ? item.volumes.reduce((total, volume) => ((volume.added_at || '') > total ? volume.added_at : total), '') : item.added_at || item.added || '');
  // "Ursula K. Le Guin" and "Baldwin, James" both file under the surname; stray brackets and marks are ignored.
  const surname = (item) => {
    const author = ((item.volumes ? item.volumes[0].author : item.author) || '').split(/\s*(?:&|;| and )\s*/)[0].trim();
    if (!author || /^unknown$/i.test(author)) return '\uffff';
    const last = author.includes(',') ? author.split(',')[0] : author.split(/\s+/).pop();
    const clean = (text) => text.toLowerCase().replace(/[^\p{L}\p{N} ]+/gu, '').trim();
    return (clean(last) || '\uffff') + ' ' + clean(author);
  };
  const byTitle = (left, right) => title(left).localeCompare(title(right));
  const list = items.slice();
  if (order === 'added') list.sort((left, right) => added(right).localeCompare(added(left)) || byTitle(left, right));
  else if (order === 'author') list.sort((left, right) => surname(left).localeCompare(surname(right)) || byTitle(left, right));
  else list.sort(byTitle);
  return list;
}

function groupSeries(books) {
  const keyOf = (book) => (book.in_library && book.kind !== 'Tabletop' && (book.collection || book.series) ? (book.collection || book.series).toLowerCase() : null);
  const by = {};
  for (const book of books) { const key = keyOf(book); if (key) (by[key] = by[key] || []).push(book); }
  const out = [], done = new Set();
  for (const book of books) {
    const key = keyOf(book);
    if (!key || by[key].length < 2) { out.push(book); continue; }
    if (done.has(key)) continue;
    done.add(key);
    const volumes = by[key].slice().sort((left, index) => (left.series || '').localeCompare(index.series || '') || (left.series_index || 0) - (index.series_index || 0));
    out.push({ series: book.collection || book.series, volumes, runs: new Set(volumes.map((volume) => volume.series || volume.title)).size });
  }
  return out;
}

// The badge on a series card while the series is on my list: where its book for the reading app stands.
const SERIES_BADGE = { ready: ['ok', 'Ready for your reading app'], making: ['', 'Being put together'], waiting: ['dim', 'Waiting to be put together'], failed: ['bad', 'Could not be put together'] };

function seriesStack(item, info, state, onChange) {
  const first = item.volumes[0], href = '#/shelf/' + encodeURIComponent(item.series);
  const word = partWord(item.volumes);
  const read = item.volumes.filter((volume) => volume.status === 'finished').length;
  const going = item.volumes.find((volume) => volume.status === 'reading');
  const rated = item.volumes.filter((volume) => volume.rating).map((volume) => volume.rating);
  const average = rated.length ? (rated.reduce((total, rating) => total + rating, 0) / rated.length).toFixed(1) : '';
  const own = info || { rating: 0, tags: { family: [], mine: [] } };
  const tagList = own.tags.family.concat(own.tags.mine);
  const coverProps = { href, class: 'cover' + (first.has_cover ? ' has-img' : '') + titleSize(item.series), title: item.series, 'aria-label': item.series + ', ' + plural(item.volumes.length, word),
    style: '--cv:' + coverColor(item.series) + (first.has_cover ? ';background-image:url(' + coverUrl(first) + ')' : '') };
  const status = going ? 'Reading ' + (going.part || 'one') : read ? read + ' of ' + item.volumes.length + ' read' : 'Not started';
  const badge = state && SERIES_BADGE[state];
  return el('div', { class: 'book' },
    el('div', { class: 'coverwrap stack-of' }, el('a', coverProps, el('span', { class: 'fmt', text: plural(item.volumes.length, word) }),
      el('span', { class: 't', text: first.has_cover ? '' : item.series }))),
    el('a', { class: 'card-title', href, title: item.series, text: item.series }),
    el('div', { class: 'small dim card-line', text: [plural(item.volumes.length, word), item.runs > 1 ? plural(item.runs, 'run') : first.author].filter(Boolean).join(' · ') }),
    el('div', { class: 'card-tags', title: tagList.join(' · ') }, tagList.length ? el('span', { class: 'lab', text: tagList.slice(0, 3).join(' · ') }) : null,
      tagList.length > 3 ? el('span', { class: 'more', text: '+' + (tagList.length - 3) }) : null),
    isGuest() ? null : el('div', { class: 'small card-line', text: status }),
    el('div', { class: 'card-rating small' },
      !isGuest() && own.rating ? el('div', { text: 'Series rating: ' + own.rating + ' of 5' }) : null,
      average ? el('div', { class: 'dim', text: 'Volumes average ' + average + ' of 5 (' + plural(rated.length, 'rated', 'rated') + ')' }) : null),
    el('div', { class: 'card-foot' },
      badge ? el('span', { class: 'badge ' + badge[0], text: badge[1] }) : null,
      seriesButtons(item, href, onChange)));
}

// Download the whole series (to keep, or to give someone), and put every volume on my list.
function seriesButtons(item, href, onChange) {
  const comics = item.volumes.every((volume) => volume.formats.some((fmt) => fmt === 'CBZ' || fmt === 'CBR'));
  const download = item.runs > 1 ? el('a', { class: 'btn sm', href, text: 'Open the series' })
    : el('a', { class: 'btn sm', href: '/api/books/series-file?' + qs({ series: item.volumes[0].series }), download: '', 'aria-label': 'Download all of ' + item.series,
      title: comics ? 'Every volume joined into one comic file, in order. Putting it together can take a minute.' : 'Every volume in one ZIP file',
      text: comics ? 'Download CBZ' : 'Download all (.zip)' });
  // One volume on my list brings the whole series to the reading app, so any one counts.
  const missing = item.volumes.filter((volume) => volume.in_library && !volume.on_shelf);
  const mine = isGuest() ? null : item.volumes.some((volume) => volume.on_shelf) ? el('div', { class: 'small ok', text: 'In my books ✓' })
    : el('button', { class: 'btn sm', type: 'button', 'aria-label': 'Add every volume of ' + item.series + ' to my books', text: 'Add to my books', onclick: async (ev) => {
      ev.currentTarget.disabled = true;
      try {
        for (const volume of missing) await api('POST', '/api/books/' + volume.id + '/shelf', { on: true });
        toast(item.series + ' is in your books: your reading app gets it as one book.');
      } catch (err) { oops(err); }
      if (onChange) onChange();
    } });
  return [download, mine];
}

// "issue", "chapter" or "volume": what most parts of a series call themselves.
function partWord(volumes) {
  const count = {};
  for (const volume of volumes) { const word = (volume.part || 'Volume').split(' ')[0].toLowerCase(); count[word] = (count[word] || 0) + 1; }
  const top = Object.keys(count).sort((left, right) => count[right] - count[left])[0];
  return ['issue', 'chapter', 'volume'].includes(top) ? top : 'volume';
}

// A series' own rating and tags, kept apart from its volumes' own. Series tags count in searches on every volume;
// a series followed chapter by chapter has them too.
function seriesOwn(name) {
  const own = el('div', { class: 'stack' });
  const drawOwn = async () => {
    let info;
    try { info = await api('GET', '/api/series-info?' + qs({ name })); } catch (err) { fill(own); return; }
    const send = (path, body, method) => api(method || 'POST', '/api/series-info' + path, Object.assign({ name: info.name }, body)).then(drawOwn).catch(oops);
    const canTag = canSort();
    fill(own,
      isGuest() ? null : el('section', { class: 'panel cut' }, el('div', { class: 'cols' },
        el('div', { class: 'stack' }, head('My rating of the whole series'),
          el('div', { class: 'stars', role: 'group', 'aria-label': 'Rate the whole series' }, [1, 2, 3, 4, 5].map((stars) =>
            el('button', { class: 'star' + (stars <= info.rating ? ' on' : ''), type: 'button',
                'aria-label': 'Rate the series ' + stars + ' of 5', 'aria-pressed': String(stars === info.rating), onclick: () => send('/rating',
                { rating: stars === info.rating ? 0 : stars }) }, starMark()))),
          el('p', { class: 'small dim', text: info.rating ? 'You rated the series ' + info.rating + ' of 5' : 'Not rated yet' })),
        info.volumes ? el('div', { class: 'stack' }, head('Your volume ratings'),
          el('p', { class: 'small',
              text: info.volume_average ? 'Average ' + info.volume_average + ' of 5, from ' + plural(info.volumes_rated, 'volume') + ' of ' + info.volumes + ' you rated' : 'No volumes rated yet. Rate each on its own page.' })) : el('div'))),
      tagSection('Family tags for the series', 'Shared with the whole family · found when searching any volume', 'family', info.tags.family,
        canTag ? (tag) => send('/tags', { kind: 'family', tag: tag }, 'DELETE') : null, canTag ? (tag) => send('/tags', { kind: 'family', tag: tag }) : null, 'New family tag'),
      isGuest() ? null : tagSection('My tags for the series', 'Only on your profile · found when searching any volume', 'mine', info.tags.mine,
        (tag) => send('/tags', { kind: 'mine', tag: tag }, 'DELETE'), (tag) => send('/tags', { kind: 'mine', tag: tag }), 'New tag'));
  };
  drawOwn();
  return own;
}

// Every volume of one series, in order.
async function pageShelf(name) {
  const set = decodeURIComponent(name || '');
  // Inside a series a part is called by its own title when it has one, otherwise "Volume 3", "Issue 12" and so on.
  const asPart = (book) => Object.assign({}, book, { title: book.subtitle || book.part || book.title, author: book.subtitle ? book.part : book.part ? '' : book.author });
  const body = el('div', { class: 'stack gap28' });
  const wholeFile = (series, books, word) => {
    const comics = books.every((book) => book.formats.some((fmt) => fmt === 'CBZ' || fmt === 'CBR'));
    return el('a', { class: 'btn sm', href: '/api/books/series-file?' + qs({ series }), download: '',
      title: comics ? 'Every ' + word + ' joined into one comic file, in order' : 'Every ' + word + ' in one ZIP file',
      text: comics ? 'Download the whole run as one comic (.cbz)' : 'Download all (.zip)' });
  };
  const load = async () => {
    // A set made of several runs (Star Trek: Gold Key, Deep Space Nine, ...) or a plain series.
    let found = await api('GET', '/api/books?' + qs({ collection: set }));
    if (!found.total) found = await api('GET', '/api/books?' + qs({ series: set }));
    const runs = [];
    for (const book of found.books) {
      const key = book.series || book.title;
      let run = runs.find((existing) => existing.name === key);
      if (!run) runs.push(run = { name: key, books: [] });
      run.books.push(book);
    }
    runs.sort((left, right) => right.books.length - left.books.length || left.name.localeCompare(right.name));
    fill(body, runs.map((run) => {
      const word = partWord(run.books);
      return el('section', { class: 'stack' },
        runs.length > 1 ? el('div', { class: 'row between gap10' },
          el('h2', { text: run.name }), el('div', { class: 'row gap10' }, el('span', { class: 'small dim', text: plural(run.books.length, word) }),
            run.books.length > 1 && run.books[0].series ? wholeFile(run.books[0].series, run.books, word) : null)) : null,
        el('div', { class: 'shelf' }, run.books.map((book) => bookCard(asPart(book), () => load().catch(oops)))));
    }));
    return { d: found, runs };
  };
  const { d: data, runs } = await load();
  const own = seriesOwn(set);
  const word = runs.length > 1 ? 'issue' : partWord(data.books);
  const author = runs.length > 1 ? '' : (data.books.find((book) => book.author && book.author !== 'Unknown') || {}).author;
  const single = runs.length === 1 && data.total > 1 && data.books[0].series;
  setMain(back('#/library', 'Back to the library'),
    pageHead(set, [plural(data.total, runs.length > 1 ? 'comic' : word), runs.length > 1 ? plural(runs.length, 'run') : '', author].filter(Boolean).join(' · ')),
    data.total ? own : null,
    single ? el('section', { class: 'panel cut' }, head('The whole series in one file'),
      el('p', { class: 'small dim',
          text: 'Every ' + word + ' in one file, in order. Each ' + word + ' can still be downloaded on its own below. Putting it together can take a minute for a long series; the download starts by itself.' }),
      el('div', { class: 'row' }, wholeFile(data.books[0].series, data.books, word))) : null,
    runs.length > 1 ? el('p', { class: 'small dim', text: 'Each run can be downloaded as one comic with the button beside its name.' }) : null,
    data.total ? body : el('p', { class: 'dim', text: 'Nothing in the library belongs to this series any more.' }));
}

// The Library has two halves. Which one was open is remembered on this device, per profile.
const GRAPHIC = 'Graphic novel';
const sectionKey = () => 'bookhub-section-' + (appState.me && appState.me.id ? appState.me.id : 'guest');
function savedSection() { try { return localStorage.getItem(sectionKey()) === 'graphic' ? 'graphic' : 'books'; } catch (err) { return 'books'; } }

// The library search. What is typed becomes terms (see search.py for how they match):
//   plain words      look in the fields chosen with Title · Author · Series · Tag
//   by …  /  in …    an author / a tag, up to the next of these words
//   +word  -word     must match / must not match;  not …  is the same as -
//   a OR b           either will do;  "two words"  together, in order;  mag*  any ending
const TERM_FIELDS = [['title', 'Title'], ['author', 'Author'], ['series', 'Series'], ['tag', 'Tag']];
const TERM_LABEL = { title: 'Title', author: 'Author', series: 'Series', tag: 'Tag', epub: 'Genre', community: 'Community', family: 'Family', mine: 'Mine' };
const SIGN_NEXT = { '+': '-', '-': 'or', or: '+' };
const SIGN_TEXT = { '+': '+', '-': '−', or: '∨ or' };
const SIGN_SAY = { '+': 'must match', '-': 'must not match', or: 'one of the "or" terms will do' };

function parseSearch(text, fields) {
  const tokens = [];
  // replace() used as a loop, not to replace anything: it walks every "quoted phrase" or bare word in order
  String(text || '').replace(/"([^"]*)"|(\S+)/g, (whole, quoted, word) => { tokens.push(quoted !== undefined ? { w: quoted, quoted: true } : { w: word }); return whole; });
  const out = [];
  let mode = null, sign = null, words = [];
  const flush = (phrase) => {
    if (!words.length) return;
    out.push({ fields: mode === 'by' ? ['author'] : mode === 'in' ? ['tag'] : fields.slice(), value: words.join(' '), source: null, sign: sign || '+', phrase: !!phrase });
    words = []; sign = null;
  };
  for (const token of tokens) {
    const low = token.w.toLowerCase();
    if (!token.quoted && token.w === 'OR') { flush(); if (out.length) out[out.length - 1].sign = 'or'; sign = 'or'; continue; }
    if (!token.quoted && (low === 'by' || low === 'in')) { flush(); mode = low; continue; }
    // "not" and +word / -word end a "by" or "in": "by Pratchett -horror" leaves out horror, not an author called Horror.
    if (!token.quoted && low === 'not') { flush(); mode = null; sign = '-'; continue; }
    if (!token.quoted && /^[+-]./.test(token.w)) { flush(); mode = null; sign = token.w[0] === '+' ? '+' : '-'; words.push(token.w.slice(1)); flush(); continue; }
    if (token.quoted) { flush(); if (token.w.trim()) { words.push(token.w.trim()); flush(true); } continue; }
    words.push(token.w);
  }
  flush();
  return out;
}

// The colour dots a term or tag wears, one per place it is looked for or sort of tag that carries it.
// The names go to screen readers and the hover note; the colours are explained by the key under the tags.
function termDots(term) {
  const kinds = term.fields && !(term.fields.length === 1 && term.fields[0] === 'tag') ? term.fields : (term.sources && term.sources.length ? term.sources : [term.source || 'tag']);
  return { dots: el('span', { class: 'dots', 'aria-hidden': 'true' }, kinds.map((kind) => el('span', { class: 'dot', 'data-k': kind }))),
    say: kinds.map((kind) => TERM_LABEL[kind]).join(' or ') };
}

function pageLibrary(section) {
  const pid = appState.me && appState.me.id ? appState.me.id : 'guest';
  const remember = (key, value) => { try { localStorage.setItem('bookhub-' + key + '-' + pid, JSON.stringify(value)); } catch (err) { /* private browsing */ } };
  const recall = (key,
      fallback) => { try { const value = JSON.parse(localStorage.getItem('bookhub-' + key + '-' + pid)); return value === null ? fallback : value; } catch (err) { return fallback; } };
  const view = { section: section === 'graphic' || section === 'books' ? section : savedSection(), chips: [], draft: '' };
  view.fields = recall('search-fields', ['title', 'author', 'series', 'tag']);
  const tabOf = () => recall('tab-' + view.section, 'all');
  const grid = el('div', { class: 'shelf' });
  const count = el('div', { class: 'dim', role: 'status' });
  const orderRow = el('div', { class: 'tabs sm', role: 'radiogroup', 'aria-label': 'Order the books' });
  const sectionRow = el('div', { class: 'tabs', role: 'group', 'aria-label': 'Part of the library' });
  const tabRow = el('div', { class: 'tabs sm', role: 'group', 'aria-label': 'Kind of book' });
  const modes = el('div', { class: 'toggles', role: 'group', 'aria-label': 'Where plain words are looked for' });
  const chipsRow = el('div', { class: 'terms', 'aria-label': 'Search terms', role: 'list' });
  const suggest = el('div', { class: 'terms suggest', role: 'group', 'aria-label': 'Tags that match what you are typing' });
  const moreTags = el('div', { class: 'row' });
  const colourKey = el('p', { class: 'small dim colour-key' }, el('span', { text: 'Colours:' }),
    ['title', 'author', 'series', 'epub', 'community', 'family', 'mine'].map((dotKey) => el('span', {}, el('span', { class: 'dot',
        'data-k': dotKey, 'aria-hidden': 'true' }), ' ' + (dotKey === 'epub' ? 'Book file' : TERM_LABEL[dotKey]))));
  const seriesBox = el('div', { class: 'stack' });
  const gamesLink = el('a', { class: 'btn sm', href: '#/tabletop', hidden: true, text: 'Game books by game' });
  const bookKinds = () => appState.kinds.filter((kind) => kind !== GRAPHIC);
  let states = {};  // series on my list (lower-case name) -> where its book for the reading app stands

  // The query is built first (typed words, picked tags, signs) and only run when Search is pressed.
  let ran = [];
  const terms = () => ran;
  const sent = (term) => ({ fields: term.fields, value: term.value, source: term.source, sign: term.sign, phrase: term.phrase });

  async function loadStates() {
    if (isGuest()) return;
    const order = ['failed', 'making', 'waiting', 'ready'];
    const data = await api('GET', '/api/me/series');
    states = {};
    for (const entry of data.series) states[entry.name.toLowerCase()] = order.find((state) => entry.parts.some((part) => part.state === state));
  }

  const kindsNow = () => {
    const tab = tabOf();
    return view.section === 'graphic' ? [GRAPHIC] : tab !== 'all' && bookKinds().includes(tab) ? [tab] : bookKinds();
  };
  async function loadBooks() {
    const tab = tabOf();
    const kinds = kindsNow();
    const all = terms();
    const data = await api('GET', '/api/books?' + qs({ kinds: kinds.join(','), terms: JSON.stringify(all.map(sent)), feed: 1 }));
    let shown = ordered(groupSeries(groupGameBooks(data.books)), recall('order', 'title'));
    gamesLink.hidden = view.section !== 'books' || !data.books.some((book) => book.kind === 'Tabletop' || book.game);
    if (view.section === 'graphic' && tab === 'series') shown = shown.filter((item) => item.volumes || item.chapter_series);
    if (view.section === 'graphic' && tab === 'oneshots') shown = shown.filter((item) => !item.volumes && !item.chapter_series);
    const info = data.series_info || {};
    fill(grid, ...shown.map((item) => item.chapter_series ? chapterCard(item,
        () => loadBooks().catch(oops)) : item.volumes ? seriesStack(item, info[item.series.toLowerCase()], states[item.series.toLowerCase()],
        () => loadBooks().catch(oops)) : item.game_group ? gameStack(item) : bookCard(item, () => loadBooks().catch(oops))));
    const stacks = shown.filter((item) => item.volumes || item.game_group).length;
    const followed = shown.filter((item) => item.chapter_series).length;
    const books = shown.reduce((total, item) => total + (item.volumes ? item.volumes.length : item.game_group ? item.items.length : item.chapter_series ? 0 : 1), 0);
    count.textContent = !books && !followed ? (all.length ? 'Nothing matches. Take a term away, or switch one from + to "or".' : !appState.library_available ? '' : 'Nothing here yet.')
      : plural(books, 'book') + (stacks ? ' · ' + plural(stacks, 'series',
          'series') + ' shown as one card each' : '') + (followed ? ' · ' + plural(followed, 'series',
          'series') + ' followed chapter by chapter' : '') + (data.hidden ? ' · ' + plural(data.hidden, 'book') + ' you hid (Refine to change)' : '');
  }
  const searchBtn = el('button', { class: 'btn pri', type: 'button', text: 'Search', onclick: () => run() });
  const waiting = el('span', { class: 'small', role: 'status' });
  // Something in the query changed since the last search: say so, and point at the button.
  const reload = () => {
    const same = JSON.stringify(view.chips.map(sent)) === JSON.stringify(ran.map(sent)) && !view.draft.trim();
    waiting.textContent = same ? '' : 'Press Search to see the books for this search.';
    searchBtn.classList.toggle('attn', !same);
  };
  function run() {
    const add = parseSearch(view.draft, view.fields);
    view.draft = ''; box.value = '';
    placeTags(add).then(() => {
      view.chips = view.chips.concat(add);
      ran = view.chips.map((picked) => Object.assign({}, picked));
      drawChips(); reload(); showSuggestions();
      loadBooks().catch(oops);
    });
  }

  function chip(term, index) {
    const { dots, say: where } = termDots(term);
    const say = (term.fields.length === 1 && term.fields[0] === 'tag' ? 'tag' : where) + ' "' + term.value + '"';
    return el('span', { class: 'term' + (term.draft ? ' draft' : '') + (term.sign === '-' ? ' minus' : ''), role: 'listitem',
      title: term.draft ? 'Press Enter to keep this term' : where + ': ' + term.value + ' (' + SIGN_SAY[term.sign] + ')' },
      term.draft ? el('span', { class: 'sign', 'aria-hidden': 'true', text: SIGN_TEXT[term.sign] })
        : el('button', { class: 'sign', type: 'button', text: SIGN_TEXT[term.sign],
            'aria-label': say + ' ' + SIGN_SAY[term.sign] + '. Change it',
            onclick: () => { term.sign = SIGN_NEXT[term.sign]; drawChips(); reload(); showSuggestions(); } }),
      dots,
      el('span', { class: 'val', text: term.phrase ? '"' + term.value + '"' : term.value }),
      term.draft ? el('span', { class: 'small dim', style: 'padding-right:8px', text: 'Enter to keep' })
        : el('button', { class: 'x', type: 'button', text: '×', 'aria-label': 'Take away ' + say, onclick: () => { view.chips.splice(index, 1); drawChips(); reload(); showSuggestions(); } }));
  }
  function drawChips() {
    const drafts = parseSearch(view.draft, view.fields).map((term) => Object.assign(term, { draft: true }));
    chipsRow.hidden = !view.chips.length && !drafts.length;
    fill(chipsRow, ...view.chips.map(chip), ...drafts.map((draft) => chip(draft, -1)),
      view.chips.length ? el('button', { class: 'btn sm', type: 'button', text: 'Clear all', onclick: () => { view.chips = []; drawChips(); reload(); showSuggestions(); } }) : null);
  }

  // A tag typed after "in" gets the colour of where the library has it, when it has it.
  async function placeTags(list) {
    for (const term of list.filter((entry) => entry.fields.length === 1 && entry.fields[0] === 'tag' && !entry.source)) {
      try {
        const data = await api('GET', '/api/tags?' + qs({ q: term.value, merged: 1 }));
        const same = data.tags.find((tag) => tag.tag.toLowerCase() === term.value.toLowerCase());
        if (same) { term.value = same.tag; term.sources = same.sources; }
      } catch (err) { /* stays a plain tag */ }
    }
  }
  async function keep() {
    const add = parseSearch(view.draft, view.fields);
    if (!add.length) return;
    view.draft = ''; box.value = '';
    await placeTags(add);
    view.chips = view.chips.concat(add);
    drawChips(); reload(); showSuggestions();
  }
  // The tags to pick from, always under the box: the most used ones, or those matching what is being typed.
  // A tag already in the search shows pressed; clicking it again takes it away.
  let addSign = '+', showAll = false;
  const TOP_TAGS = 16;
  const signRow = el('div', { class: 'tabs sm', role: 'radiogroup', 'aria-label': 'How a tag you click counts' });
  const drawSigns = () => fill(signRow,
    ...[['+', '+ Must have'], ['-', '− Must not have'], ['or', '∨ Any of these']].map(([key, label]) => el('button', {
      type: 'button', role: 'radio', 'aria-checked': String(addSign === key), text: label,
      onclick: () => { addSign = key; drawSigns(); } })));
  // a tag with others under it is drawn in one box with them: Pathfinder [1e] [2e]
  const nestBox = (group, picks) => (picks.length > 1 ? el('span', { class: 'tagnest', role: 'group', 'aria-label': group[0].tag + ' and the tags under it' }, picks) : picks[0]);
  const showSuggestions = debounce(async () => {
    const last = view.draft.replace(/.*\b(?:by|in|not|OR)\s+/i, '').replace(/^[+-]/, '').trim();
    // Words after "by" are an author, not a tag: the list stays as it is.
    const typing = last.length >= 2 && !/\bby\s+[^]*$/i.test(view.draft.replace(/.*\b(?:in|not|OR)\s+/i, ''));
    let data;
    try { data = await api('GET', '/api/tags?' + qs(Object.assign({ kinds: kindsNow().join(','), merged: 1 }, typing ? { q: last } : {}))); } catch (err) { fill(suggest); return; }
    const chosen = (tag) => view.chips.findIndex((picked) => picked.fields.length === 1 && picked.fields[0] === 'tag' && picked.value.toLowerCase() === tag.tag.toLowerCase());
    const list = data.tags.slice(0, typing || showAll ? 120 : TOP_TAGS);
    suggest.classList.toggle('all', typing || showAll);
    fill(suggest, ...(list.length ? nestTags(list).map((group) => nestBox(group, group.map((entry) => {
      const at = chosen(entry);
      const term = at >= 0 ? view.chips[at] : null;
      const { dots, say } = termDots({ sources: entry.sources });
      return el('button', { class: 'tagpick' + (entry.depth ? ' child' : '') + (term && term.sign === '-' ? ' minus' : ''), type: 'button', 'aria-pressed': String(!!term),
        title: say + ' tag' + (entry.depth ? ': ' + entry.tag : ''),
        'aria-label': (term ? 'Take away' : 'Add, as ' + SIGN_SAY[addSign] + ',') + ' the tag ' + entry.tag + ' (' + plural(entry.count, 'book') + ', ' + say + ')',
        onclick: () => {
          if (term) view.chips.splice(at, 1);
          else {
            if (typing) { view.draft = view.draft.slice(0, view.draft.length - last.length).replace(/\b(?:in|by)\s*$/i, '').trim(); box.value = view.draft; }
            view.chips.push({ fields: ['tag'], value: entry.tag, sources: entry.sources, sign: addSign, phrase: false });
          }
          drawChips(); reload(); showSuggestions();
        } }, term ? el('span', { class: 'sg', text: SIGN_TEXT[term.sign] }) : null,
        dots, el('span', { class: 'val', text: entry.label }), el('span', { class: 'n', text: entry.count }));
    }))) : [el('span', { class: 'small dim', text: typing ? 'No tags match "' + last + '".' : 'No tags yet.' })]));
    fill(moreTags, !typing && data.tags.length > TOP_TAGS ? el('button', { class: 'btn sm', type: 'button',
        text: showAll ? 'Fewer tags' : 'All tags (' + data.tags.length + ')', 'aria-expanded': String(showAll),
        onclick: () => { showAll = !showAll; showSuggestions(); } }) : null);
  }, 200);

  const box = el('input', { type: 'search', placeholder: 'Try: cafe in science fiction · by Le Guin · dragon OR wyvern · -horror',
    'aria-describedby': 'search-help',
    oninput: (event) => { view.draft = event.target.value; drawChips(); reload(); showSuggestions(); },
    onkeydown: (event) => { if (event.key === 'Enter') { event.preventDefault(); keep(); } } });

  function draw() {
    fill(sectionRow, ...[['books', 'Books'], ['graphic', 'Graphic novels']].map(([key, label]) => el('button', { type: 'button',
        'aria-pressed': String(view.section === key), text: label, onclick: () => {
      view.section = key;
      try { localStorage.setItem(sectionKey(), key); } catch (err) { /* private browsing */ }
      draw();
    } })));
    const tabs = view.section === 'graphic' ? [['all', 'All'], ['series', 'Series'], ['oneshots', 'One-shots']]
      : [['all', 'All']].concat(bookKinds().map((bookKind) => [bookKind, KIND_LABEL[bookKind] || bookKind]));
    const tab = tabOf();
    fill(tabRow, ...tabs.map(([key, label]) => el('button', { type: 'button', 'aria-pressed': String(tab === key), text: label, onclick: () => { remember('tab-' + view.section, key); draw(); } })));
    fill(modes, ...TERM_FIELDS.map(([key, label]) => el('button', { type: 'button', 'aria-pressed': String(view.fields.includes(key)), onclick: () => {
      const next = view.fields.includes(key) ? view.fields.filter((other) => other !== key) : view.fields.concat(key);
      if (!next.length) return toast('Keep at least one place to look.', true);
      view.fields = TERM_FIELDS.map((entry) => entry[0]).filter((name) => next.includes(name));
      remember('search-fields', view.fields);
      draw();
    } }, el('span', { class: 'dot', 'data-k': key, 'aria-hidden': 'true' }), label)));
    const order = recall('order', 'title');
    fill(orderRow, ...[['added', 'Last added'], ['title', 'Title A–Z'], ['author', 'Author A–Z']].map(([key, label]) => el('button', {
      type: 'button', role: 'radio', 'aria-checked': String(order === key), text: label,
      onclick: () => { remember('order', key); draw(); } })));
    fill(seriesBox, ...(view.section === 'graphic' ? [seriesShelf()] : []));
    seriesBox.hidden = !seriesBox.childElementCount;  // empty, it would still add a gap above the search
    drawChips();
    drawSigns(); showSuggestions();
    loadBooks().catch(oops);
  }

  // Where to look, how picked tags count, the colour key and typing help: open when wanted, and remembered.
  const refine = el('div', { class: 'stack gap10 refine', id: 'lib-refine', hidden: !recall('refine', false) },
    el('div', { class: 'lib-opt' }, el('span', { class: 'lab', text: 'Look in' }), modes),
    el('div', { class: 'lib-opt' }, el('span', { class: 'lab', text: 'Add tags as' }), signRow),
    isGuest() ? null : hiddenFeedBox(() => loadBooks().catch(oops)),
    colourKey,
    el('details', { class: 'small dim' }, el('summary', { text: 'How to type a search' }),
      el('p', { id: 'search-help',
          text: 'Build your search, then press Search. Enter keeps what you typed as terms. Pick tags, choosing first whether they must be there, must not, or any of them will do. When typing: "by" an author, "in" a tag, + must match, - or "not" must not, OR for either, quotes keep words together, * for any ending. Click a term\'s sign to switch it between +, − and "or".' })));
  const refineToggle = el('button', { class: 'btn sm', type: 'button', 'aria-expanded': String(!refine.hidden), 'aria-controls': 'lib-refine',
    text: refine.hidden ? 'Refine' : 'Hide refine', onclick: () => {
      refine.hidden = !refine.hidden; remember('refine', !refine.hidden);
      refineToggle.setAttribute('aria-expanded', String(!refine.hidden)); refineToggle.textContent = refine.hidden ? 'Refine' : 'Hide refine';
    } });

  setMain(
    pageHead('Library',
        isGuest() ? 'Browse and download. Nothing is tracked for guests.' : 'Every book: the ones on the home server, the ones you have read elsewhere and the ones the family has on Kindle. Add books to My books to have them on your devices.'),
    appState.library_available ? null : el('p', { class: 'bad', text: 'The book library could not be found. Check the CALIBRE_LIBRARY folder in the settings.' }),
    el('div', { class: 'lib-bar' }, sectionRow, tabRow, gamesLink),
    el('section', { class: 'panel cut stack lib-search', 'aria-label': 'Search the library' },
      el('div', { class: 'row end', style: 'flex-wrap:nowrap' }, field('Build your search', box, 'wide'), searchBtn),
      el('div', { class: 'lib-tools' }, el('div', { class: 'lib-opt' }, el('span', { class: 'lab', text: 'Sort' }), orderRow), refineToggle),
      refine, chipsRow, suggest, moreTags, waiting),
    seriesBox, count, grid);
  draw();
  loadStates().then(() => loadBooks()).catch(() => { /* the badges are a nicety */ });
}

// ------------------------------------------------------------- game books
// Tabletop books (and novels or comics carrying a game's tag) by Game › Edition › Product line; the cards that
// keep small game books from flooding the library; nested tags in the tag picker; what each person hides.
// The grouping itself is worked out on the server (tabletop.py); these only draw it.

const ROLE_PLURAL = { Rulebook: 'Rulebooks', Adventure: 'Adventures', 'Player aid': 'Player aids', 'GM aid': 'GM aids' };
const ROLE_WORD = { Rulebook: 'rulebook', Adventure: 'adventure', 'Player aid': 'player aid', 'GM aid': 'GM aid' };
const NO_GAME = 'Game not known';
// the library keeps three-letter codes ("rus"); the badge shows two letters and says the whole name on hover
const LANGUAGES = { rus: ['RU', 'Russian'], ukr: ['UK', 'Ukrainian'], deu: ['DE', 'German'], ger: ['DE', 'German'], fra: ['FR', 'French'],
  fre: ['FR', 'French'], spa: ['ES', 'Spanish'], ita: ['IT', 'Italian'], por: ['PT', 'Portuguese'], pol: ['PL', 'Polish'],
  nld: ['NL', 'Dutch'], dut: ['NL', 'Dutch'], swe: ['SV', 'Swedish'], ces: ['CS', 'Czech'], cze: ['CS', 'Czech'], hun: ['HU', 'Hungarian'],
  tur: ['TR', 'Turkish'], ell: ['EL', 'Greek'], gre: ['EL', 'Greek'], jpn: ['JA', 'Japanese'], kor: ['KO', 'Korean'], zho: ['ZH', 'Chinese'], chi: ['ZH', 'Chinese'] };

function langBadge(book) {
  if (!book.language) return null;
  const [code, name] = LANGUAGES[book.language] || [book.language.slice(0, 3).toUpperCase(), book.language];
  return el('span', { class: 'lang', title: 'In ' + name, 'aria-label': 'In ' + name, text: code });
}

// "Pathfinder 2e" under "Pathfinder" reads "2e" (tabletop.short_name does the same on the server)
function shortTag(tag, parent) {
  if (parent && tag.toLowerCase().startsWith(parent.toLowerCase()) && tag.length > parent.length) {
    const rest = tag.slice(parent.length).replace(/^[\s:\-–·]+/, '');
    if (rest) return rest;
  }
  return tag;
}

const tabletopLink = (focus) => '#/tabletop/' + encodeURIComponent(qs(focus));
const isMaps = (book) => /\bmaps?\b|flip-?mats?|\btiles?\b/i.test(book.title);
const isPlayersGuide = (book) => /players?'?s? ?guide/i.test(book.title);

// Small game books shown together in the library: an adventure series (with its player's guide and maps) is one
// card, and player or GM aids that belong to no series are one card per game, edition and role. Rulebooks and
// lone adventures stay cards of their own. A "group" of one is just its book.
function groupGameBooks(books) {
  const groups = new Map(), out = [];
  for (const book of books) {
    const small = book.kind === 'Tabletop' && book.in_library && book.role && book.role !== 'Rulebook';
    const by = !small ? null : book.game_series ? 'series' : book.role !== 'Adventure' ? 'role' : null;
    if (!by) { out.push(book); continue; }
    const key = [by, book.game, book.edition, by === 'series' ? book.game_series : book.role].join('|').toLowerCase();
    let group = groups.get(key);
    if (!group) {
      group = { game_group: true, by, game: book.game, edition: book.edition, role: book.role, items: [], added_at: '',
        title: by === 'series' ? book.game_series : (book.edition || book.game || 'Game books') + ' · ' + ROLE_PLURAL[book.role], author: '' };
      groups.set(key, group);
      out.push(group);
    }
    group.items.push(book);
    if ((book.added_at || '') > group.added_at) group.added_at = book.added_at;
  }
  return out.map((item) => (item.game_group && item.items.length < 2 ? item.items[0] : item));
}

// "6 parts + Player's Guide + maps", or "34 GM aids"
function gameGroupLine(group) {
  if (group.by === 'role') return plural(group.items.length, ROLE_WORD[group.role]);
  const of = (role) => group.items.filter((book) => book.role === role);
  const adventures = of('Adventure'), players = of('Player aid'), gms = of('GM aid');
  return [adventures.length ? plural(adventures.length, 'part') : '',
    players.length === 1 && isPlayersGuide(players[0]) ? "Player's Guide" : players.length ? plural(players.length, 'player aid') : '',
    gms.length && gms.every(isMaps) ? 'maps' : gms.length ? plural(gms.length, 'GM aid') : ''].filter(Boolean).join(' + ');
}

function gameStack(group) {
  const first = group.items.find((book) => book.has_cover) || group.items[0];
  const focus = group.by === 'role' ? { game: group.game || NO_GAME, edition: group.edition, role: group.role }
    : { game: group.game || NO_GAME, edition: group.edition, key: group.items[0].key };
  const href = tabletopLink(focus);
  const line = gameGroupLine(group);
  const read = group.items.filter((book) => book.status === 'finished').length;
  const coverProps = { href, class: 'cover' + (first.has_cover ? ' has-img' : '') + titleSize(group.title), title: group.title,
    'aria-label': group.title + ', ' + line, style: '--cv:' + coverColor(group.title) + (first.has_cover ? ';background-image:url(' + coverUrl(first) + ')' : '') };
  return el('div', { class: 'book' },
    el('div', { class: 'coverwrap stack-of' }, el('a', coverProps, el('span', { class: 'fmt', text: plural(group.items.length, 'book') }),
      el('span', { class: 't', text: first.has_cover ? '' : group.title }))),
    el('a', { class: 'card-title', href, title: group.title, text: group.title }),
    el('div', { class: 'small dim card-line', title: line, text: line }),
    el('div', { class: 'card-tags' }, el('span', { class: 'lab', text: ['Tabletop', group.by === 'series' ? group.edition || group.game : ''].filter(Boolean).join(' · ') })),
    isGuest() ? null : el('div', { class: 'small card-line', text: read ? read + ' of ' + group.items.length + ' read' : 'Not started' }),
    el('div', { class: 'card-rating' }),
    el('div', { class: 'card-foot' }, el('a', { class: 'btn sm', href, 'aria-label': 'Open ' + group.title, text: 'Open' })));
}

// The tag picker's list as a tree: each tag followed by the tags nested under it, which read short ("2e").
function nestTags(list) {
  const byName = new Map(list.map((entry) => [entry.tag.toLowerCase(), entry]));
  const kids = new Map();
  for (const entry of list) {
    const up = (entry.parent || '').toLowerCase();
    if (up && byName.has(up)) { if (!kids.has(up)) kids.set(up, []); kids.get(up).push(entry); }
  }
  const out = [], seen = new Set();
  const walk = (entry, depth, parent) => {
    if (seen.has(entry.tag.toLowerCase())) return;
    seen.add(entry.tag.toLowerCase());
    out.push(Object.assign({}, entry, { depth, label: depth ? shortTag(entry.tag, parent) : entry.tag }));
    for (const kid of (kids.get(entry.tag.toLowerCase()) || []).sort((left, right) => left.tag.localeCompare(right.tag))) walk(kid, depth + 1, entry.tag);
  };
  for (const entry of list) if (!entry.parent || !byName.has(entry.parent.toLowerCase())) walk(entry, 0, '');
  for (const entry of list) walk(entry, 0, '');  // anything a loop left out
  // back into groups: a tag with others under it wears them in one box
  const groups = [];
  for (const entry of out) {
    if (entry.depth && groups.length) groups[groups.length - 1].push(entry);
    else groups.push([entry]);
  }
  return groups;
}

// The tags and roles this person hides from their own library feed, in the library's Refine panel.
function hiddenFeedBox(onChange) {
  const box = el('div', { class: 'lib-opt' });
  const choices = el('datalist', { id: 'hide-choices' });
  const input = el('input', { type: 'text', list: 'hide-choices', maxlength: 60, placeholder: 'A tag, or a role such as GM aid',
    'aria-label': 'Tag or role to hide from my library', style: 'width:min(280px, 100%)' });
  let names = [];
  const save = (next) => api('PUT', '/api/me/hidden', { hidden: next }).then((reply) => { names = reply.hidden; draw(); onChange(); }).catch(oops);
  const draw = () => fill(box, el('span', { class: 'lab', text: 'Hidden' }),
    el('div', { class: 'terms', role: 'list', 'aria-label': 'Hidden from my library' }, names.map((name) => el('span', { class: 'term', role: 'listitem' },
      el('span', { class: 'val', style: 'padding-left:10px', text: name }),
      el('button', { class: 'x', type: 'button', text: '×', 'aria-label': 'Show ' + name + ' in my library again', onclick: () => save(names.filter((other) => other !== name)) })))),
    el('form', { class: 'row', onsubmit: (event) => {
      event.preventDefault();
      const name = input.value.trim();
      if (name) { input.value = ''; save(names.concat(name)); }
    } }, input, choices, el('button', { class: 'btn sm', type: 'submit', text: 'Hide' })),
    el('span', { class: 'small dim', text: names.length ? 'Hidden books still turn up when you search, and under Game books.' : 'Nothing hidden. Hide a tag or a role to keep those books out of your library list.' }));
  api('GET', '/api/me/hidden').then((reply) => {
    names = reply.hidden;
    draw();
    return api('GET', '/api/tags?' + qs({ merged: 1, scopes: 'family,epub,community' })).then((tags) => fill(choices,
      reply.roles.concat(tags.tags.map((tag) => tag.tag)).map((name) => el('option', { value: name }))));
  }).catch(() => { /* a nicety */ });
  draw();
  return box;
}

// Every game book, by Game › Edition › Product line. Hidden tags still show here.
async function pageTabletop(focusText) {
  const focus = new URLSearchParams(decodeURIComponent(focusText || ''));
  const data = await api('GET', '/api/books?' + qs({ view: 'tabletop' }));
  const books = data.books;
  let hiddenNow = (data.hidden || []).slice();
  const view = { game: focus.get('game') || '', edition: focus.has('edition') ? focus.get('edition') : null,
    roles: new Set(focus.get('role') ? [focus.get('role')] : []), open: focus.get('key') || '' };
  const gameRow = el('div', { class: 'tabs', role: 'group', 'aria-label': 'Game' });
  const editionRow = el('div', { class: 'tabs sm', role: 'group', 'aria-label': 'Edition' });
  const roleRow = el('div', { class: 'terms', role: 'group', 'aria-label': 'Show only these' });
  const hideRow = el('div', { class: 'row gap10' });
  const body = el('div', { class: 'stack gap28' });
  const shown = (book) => !view.roles.size || view.roles.has(book.role);
  const isHidden = (name) => hiddenNow.some((tag) => tag.toLowerCase() === name.toLowerCase());

  // Inside an adventure path a part says its number where the author would be.
  const asPart = (book) => (book.role === 'Adventure' && book.game_index
    ? Object.assign({}, book, { author: 'Part ' + book.game_index + (book.author && book.author !== 'Unknown' ? ' · ' + book.author : '') }) : book);
  // after "Add to my books" and the like: the same game and edition again, with that book's line open
  const refresh = async (key) => {
    const fresh = await api('GET', '/api/books?' + qs({ view: 'tabletop' }));
    books.splice(0, books.length, ...fresh.books);
    data.tree = fresh.tree;
    view.open = key;
    draw();
  };
  const card = (book) => bookCard(asPart(book), () => refresh(book.key).catch(oops));

  function lineSummary(items) {
    const count = {};
    for (const book of items) { const word = ROLE_WORD[book.role] || 'book'; count[word] = (count[word] || 0) + 1; }
    return Object.entries(count).map(([word, many]) => plural(many, word)).join(', ');
  }
  function lineBox(line, openAll) {
    const items = line.items.map((at) => books[at]).filter(shown);
    if (!items.length) return null;
    const holder = el('div', { class: 'shelf' });
    const draw = () => { if (!holder.childElementCount) fill(holder, items.map(card)); };
    const box = el('details', { class: 'tt-line', ontoggle: () => { if (box.open) draw(); } },
      el('summary', {}, el('span', { class: 'tt-name', text: line.name }), el('span', { class: 'small dim', text: lineSummary(items) })), holder);
    if (openAll || (view.open && items.some((book) => book.key === view.open))) { box.open = true; draw(); }
    if (view.open && items.some((book) => book.key === view.open)) requestAnimationFrame(() => box.scrollIntoView({ block: 'start' }));
    return box;
  }
  function editionBox(edition, titled, openAll) {
    const lines = edition.lines.map((line) => lineBox(line, openAll)).filter(Boolean);
    if (!lines.length) return null;
    return el('section', { class: 'stack' }, titled ? el('h2', { class: 'tt-edition', text: edition.edition || edition.label }) : null, lines);
  }

  function hideButton(name) {
    if (isGuest() || !name) return null;
    const hidden = isHidden(name);
    return el('button', { class: 'btn sm', type: 'button', 'aria-pressed': String(hidden),
      text: hidden ? 'Show ' + name + ' in my library again' : 'Hide ' + name + ' from my library', onclick: async () => {
        const next = hidden ? hiddenNow.filter((tag) => tag.toLowerCase() !== name.toLowerCase()) : hiddenNow.concat(name);
        try {
          hiddenNow = (await api('PUT', '/api/me/hidden', { hidden: next })).hidden;
          toast(hidden ? name + ' is back in your library.' : name + ' is hidden from your library. It still shows here and when you search.');
        } catch (err) { oops(err); }
        draw();
      } });
  }

  function draw() {
    const game = data.tree.find((entry) => entry.game === view.game) || data.tree[0];
    if (!game) {
      fill(body, el('p', { class: 'dim', text: 'No game books yet. Books sorted as Tabletop, and novels or comics with a game\'s tag, show here.' }));
      return;
    }
    view.game = game.game;
    fill(gameRow, data.tree.map((entry) => el('button', { type: 'button', 'aria-pressed': String(entry === game),
      title: entry.parent ? entry.parent + ' › ' + entry.game : entry.game,
      onclick: () => { view.game = entry.game; view.edition = null; view.open = ''; draw(); } }, entry.game, el('span', { class: 'tt-n', text: entry.count }))));
    const edition = view.edition === null ? null : game.editions.find((entry) => entry.edition === view.edition) || null;
    editionRow.hidden = game.editions.length < 2;
    fill(editionRow, el('button', { type: 'button', 'aria-pressed': String(!edition), onclick: () => { view.edition = null; draw(); } }, 'All editions'),
      game.editions.map((entry) => el('button', { type: 'button', 'aria-pressed': String(entry === edition), title: entry.edition || entry.label,
        onclick: () => { view.edition = entry.edition; draw(); } }, entry.label, el('span', { class: 'tt-n', text: entry.count }))));
    const scope = edition ? [edition] : game.editions;
    const counts = {};
    for (const one of scope) for (const line of one.lines) for (const at of line.items) if (books[at].role) counts[books[at].role] = (counts[books[at].role] || 0) + 1;
    fill(roleRow, Object.keys(ROLE_PLURAL).filter((role) => counts[role]).map((role) => el('button', { class: 'tagpick', type: 'button',
      'aria-pressed': String(view.roles.has(role)), 'aria-label': (view.roles.has(role) ? 'Stop showing only ' : 'Show only ') + ROLE_PLURAL[role].toLowerCase(),
      onclick: () => { if (view.roles.has(role)) view.roles.delete(role); else view.roles.add(role); draw(); } },
      el('span', { class: 'val', text: ROLE_PLURAL[role] }), el('span', { class: 'n', text: counts[role] }))));
    fill(hideRow, hideButton(edition ? edition.edition : game.game === NO_GAME ? '' : game.game),
      view.roles.size === 1 ? hideButton([...view.roles][0]) : null);
    // a short list opens by itself; so does everything once one role is picked (the feed's "GM aids" card)
    const lineCount = scope.reduce((total, one) => total + one.lines.length, 0);
    const parts = scope.map((one) => editionBox(one, scope.length > 1, lineCount <= 2 || view.roles.size > 0)).filter(Boolean);
    fill(body, parts.length ? parts : [el('p', { class: 'dim', text: 'Nothing here with the roles picked. Pick another, or none to see everything.' })]);
    view.open = '';
  }

  setMain(back('#/library', 'Back to the library'),
    pageHead('Game books', 'Every role-playing game book, by game, edition and product line. Novels and comics set in a game\'s world are here too. Books you hide from your library still show here.'),
    el('div', { class: 'stack gap10' }, gameRow, editionRow, roleRow, hideRow),
    body);
  draw();
}

// Admin → Tidy tags: putting one tag under another ("Pathfinder 2e" under "Pathfinder"). Asking for the
// outer tag then finds the books with the inner ones too, and the tag picker shows them as a tree.
function tagNestPanel() {
  const box = el('section', { class: 'panel cut' }, head('Nested tags'), el('p', { class: 'small dim', text: 'Loading…' }));
  const tag = el('input', { type: 'text', maxlength: 60, list: 'nest-choices' });
  const parent = el('input', { type: 'text', maxlength: 60, list: 'nest-choices' });
  const choices = el('datalist', { id: 'nest-choices' });
  const send = (body, done) => api('POST', '/api/admin/tag-parents', body).then(() => { toast(done); tag.value = ''; parent.value = ''; reload(); }).catch(oops);
  function draw(data) {
    const byParent = {};
    for (const pair of data.parents) (byParent[pair.parent] = byParent[pair.parent] || []).push(pair.tag);
    fill(box, head('Nested tags'),
      el('p', { class: 'small dim', text: 'Put a tag under another, such as Pathfinder 2e under Pathfinder. Picking the outer tag in a search then finds the books with the inner ones too, and the tag list shows them as a tree. The sorter nests game editions by itself.' }),
      Object.keys(byParent).length ? el('div', { class: 'list' }, Object.entries(byParent).map(([up, tags]) => el('div', { class: 'row gap8' },
        el('strong', { text: up }), el('span', { class: 'dim', 'aria-hidden': 'true', text: '›' }),
        tags.map((name) => el('span', { class: 'term' }, el('span', { class: 'val', style: 'padding-left:10px', text: shortTag(name, up), title: name }),
          el('button', { class: 'x', type: 'button', text: '×', 'aria-label': 'Take ' + name + ' out from under ' + up,
            onclick: () => send({ tag: name, parent: '' }, name + ' is no longer under ' + up + '.') })))))) : el('p', { class: 'small dim', text: 'No tags are nested yet.' }),
      el('form', { class: 'row end', onsubmit: (event) => {
        event.preventDefault();
        if (tag.value.trim() && parent.value.trim()) send({ tag: tag.value, parent: parent.value }, tag.value.trim() + ' is now under ' + parent.value.trim() + '.');
      } }, field('Tag', tag), field('Goes under', parent), choices, el('button', { class: 'btn', type: 'submit', text: 'Nest' })));
  }
  const reload = () => api('GET', '/api/admin/tag-parents').then(draw).catch(oops);
  reload();
  api('GET', '/api/tags?' + qs({ merged: 1 })).then((tags) => fill(choices, tags.tags.map((entry) => el('option', { value: entry.tag })))).catch(() => {});
  return box;
}

// ------------------------------------------------- series by chapter

const seriesCover = (followed) => '/api/series/' + followed.id + '/cover';
// A series followed chapter by chapter, in the library beside the books, tagged "Chapter by chapter" and with
// how far publication has got.
const PUB_BADGE = { Ongoing: 'ok', Hiatus: 'dim', Finished: '', Cancelled: 'bad', Licensed: 'dim' };
function chapterCard(item, onChange) {
  const href = '#/series/' + item.series_id;
  const genres = item.tags.community;
  const done = item.status === 'finished' && !isGuest();
  return el('div', { class: 'book' },
    el('div', { class: 'coverwrap' + (done ? ' done' : '') },
      el('a', { href, class: 'cover has-img' + titleSize(item.title), title: item.title,
          style: '--cv:' + coverColor(item.title) + ';background-image:url(' + seriesCover({ id: item.series_id }) + ')' },
        el('span', { class: 'fmt', text: 'Chapters' }), el('span', { class: 't', text: '' })),
      done ? el('span', { class: 'readnote', text: 'All caught up' }) : null),
    el('a', { class: 'card-title', href, title: item.title, text: item.title }),
    el('div', { class: 'small dim card-line', text: [item.author, item.source].filter(Boolean).join(' · ') }),
    el('div', { class: 'card-tags', title: genres.join(' · ') }, genres.length ? el('span', { class: 'lab', text: genres.slice(0, 3).join(' · ') }) : null,
      genres.length > 3 ? el('span', { class: 'more', 'aria-label': (genres.length - 3) + ' more tags', text: '+' + (genres.length - 3) }) : null),
    el('div', { class: 'small card-line', text: !item.chapters ? 'No chapters found yet' : isGuest() ? plural(item.chapters, 'chapter')
      : item.read + ' of ' + plural(item.chapters, 'chapter') + ' read' + (item.unread && item.read ? ' · ' + item.unread + ' new' : '') }),
    el('div', { class: 'card-rating small' }, !isGuest() && item.rating ? el('div', { text: 'Series rating: ' + item.rating + ' of 5' }) : null),
    el('div', { class: 'card-foot' },
      el('div', { class: 'row gap6' }, el('span', { class: 'badge', text: 'Chapter by chapter' }),
        item.publication ? el('span', { class: 'badge ' + (PUB_BADGE[item.publication] || ''), text: item.publication }) : null),
      // The chapters kept so far as one comic file, to keep or give someone; a long series has a file per 50 chapters.
      item.file_parts === 1 ? el('a', { class: 'btn sm', href: '/api/series/' + item.series_id + '/file/1', download: '',
          'aria-label': 'Download ' + item.title + ' as a comic file', text: 'Download CBZ' })
        : item.file_parts > 1 ? el('a', { class: 'btn sm', href,
            'aria-label': item.title + ': ' + item.file_parts + ' comic files to download, on the series page',
            text: 'CBZ · ' + item.file_parts + ' parts' }) : null,
      // As for a book: on my list, it goes to my reading app as one book with a chapter list.
      isGuest() ? el('a', { class: 'btn sm', href, text: 'Open the series' })
        : item.on_shelf ? el('div', { class: 'small ok', text: 'In my books ✓' })
        : el('button', { class: 'btn sm', type: 'button', 'aria-label': 'Add to my books: ' + item.title, text: 'Add to my books',
          onclick: () => api('POST', '/api/series/' + item.series_id + '/mine',
              { on: true }).then(() => { toast('In your books: your reading app gets it as one book, with a chapter list.'); onChange(); }).catch(oops) })));
}

// What a search of the reading sites found, one heading per site. after() runs once a series is followed.
function seriesHits(data, after) {
  if (!data.sources) return [el('p', { class: 'warn', role: 'status',
      text: 'The series server has no sources yet, so there is nowhere to search. An admin adds them in the series server itself; guide 13 shows how.' })];
  const hits = data.groups.filter((group) => group.results.length);
  const quiet = data.groups.filter((group) => group.error).map((group) => group.source);
  return [
    hits.length ? null : el('p', { class: 'dim', role: 'status', text: 'Nothing found. Try fewer words.' }),
    ...hits.map((hit) => el('div', { class: 'stack gap8' }, el('h3', { text: hit.source }),
      el('div', { class: 'list' }, hit.results.map((result) => {
        const btn = el('button', { class: 'btn pri', type: 'button', text: 'Follow', 'aria-label': 'Follow ' + result.title, onclick: async () => {
          btn.disabled = true; btn.textContent = 'Following…';
          try { const added = await api('POST', '/api/series/' + result.id + '/follow',
              { on: true }); toast('Following ' + result.title + '. ' + plural(added.queued,
              'chapter') + ' queued to download.'); btn.textContent = 'Following'; if (after) after(); } catch (err) { btn.disabled = false; btn.textContent = 'Follow'; oops(err); }
        } });
        return el('div', { class: 'row between' },
          el('div', { class: 'row' }, el('img', { class: 'thumb', src: seriesCover(result), alt: '', loading: 'lazy' }), el('a', { href: '#/series/' + result.id, text: result.title })),
          result.following ? el('span', { class: 'ok', text: 'Following' }) : btn);
      })))),
    quiet.length ? el('p', { class: 'small dim', text: 'No answer from: ' + quiet.join(', ') + '.' }) : null];
}

// The top of Library → Graphic novels: the series being followed, and a box to follow another.
function seriesShelf() {
  if (!appState.series) {
    return isAdmin() ? el('p', { class: 'small dim', text: 'Manga and manhwa read chapter by chapter can be kept here too. Guide 13 in the docs folder explains how to switch that on.' }) : null;
  }
  const note = el('p', { class: 'small dim', role: 'status', text: 'Loading…' });
  const found = el('div', { class: 'stack' });
  const searchBox = el('input', { type: 'search', placeholder: 'Title of a manga or manhwa' });
  async function load() {
    const data = await api('GET', '/api/series');
    note.textContent = data.series.length ? plural(data.series.length, 'series',
        'series') + ' followed, shown with the other graphic novels below and tagged "Chapter by chapter"' + (data.queued ? ' · ' + plural(data.queued,
        'chapter') + ' still downloading' : '') + '.'
      : (canRequest() ? 'No series followed yet. Search below to follow one.' : 'No series followed yet.');
  }
  async function search() {
    if (searchBox.value.trim().length < 2) return;
    fill(found, el('p', { class: 'dim', role: 'status', text: 'Searching the reading sites…' }));
    try {
      const data = await api('GET', '/api/series/search?' + qs({ q: searchBox.value.trim() }));
      fill(found, ...seriesHits(data, () => load().catch(oops)));
    } catch (err) { fill(found, el('p', { class: 'bad', role: 'status', text: err.message })); }
  }
  // Once something is followed, the panel is one line; following another opens on request.
  const more = el('div', { class: 'stack', id: 'series-follow', hidden: true },
    el('p', { class: 'small dim',
        text: 'Manga and manhwa followed on reading sites. Every chapter is downloaded and kept on the server. Reading progress here is shared by everyone who can see this shelf.' }),
    canRequest() ? el('form', { class: 'row end', role: 'search', onsubmit: (event) => { event.preventDefault(); search(); } },
        field('Follow a new series', searchBox), el('button', { class: 'btn', type: 'submit', text: 'Search' })) : null,
    found);
  const open = el('button', { class: 'btn sm', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'series-follow', hidden: true,
    text: canRequest() ? 'Follow a new series' : 'About this shelf', onclick: () => {
      more.hidden = !more.hidden; open.setAttribute('aria-expanded', String(!more.hidden));
      if (!more.hidden && canRequest()) searchBox.focus();
    } });
  load().then(() => { const none = note.textContent.startsWith('No series'); more.hidden = none ? false : more.hidden; open.hidden = none; })
    .catch((err) => { note.className = 'small bad'; note.textContent = err.message; more.hidden = false; });
  return el('section', { class: 'panel cut', 'aria-label': 'Series by chapter' },
    head('Series, chapter by chapter', el('button', { class: 'btn', type: 'button', text: 'Look for new chapters',
        onclick: async () => { try { await api('POST',
        '/api/series/check'); toast('Checking every series. New chapters appear here as they are found.'); } catch (err) { oops(err); } } })),
    el('div', { class: 'row between' }, note, open),
    more);
}

// A long description, cut to a few lines on a phone with "Read more" (computers show it all).
function clampText(text) {
  const para = el('p', { class: 'small clamp', style: 'white-space:pre-line', text });
  const more = el('button', { class: 'linkish small', type: 'button', text: 'Read more', onclick: () => {
    const open = para.classList.toggle('open'); more.textContent = open ? 'Show less' : 'Read more'; more.setAttribute('aria-expanded', String(open));
  }, 'aria-expanded': 'false' });
  return el('div', { class: 'clampbox' }, para, text.length > 240 ? more : null);
}

// "every week, on Mondays" and when the next one should come, from the dates the site gave each chapter
function scheduleLine(info) {
  const plan = info.cadence || {};
  if (!plan.last) return null;
  const quiet = Math.round((Date.now() - new Date(plan.last + 'T00:00:00').getTime()) / 86400000);
  const lastText = 'Newest chapter went up ' + (quiet < 1 ? 'today' : quiet === 1 ? 'yesterday' : plural(quiet, 'day') + ' ago') + ' (' + niceDate(plan.last) + ').';
  const done = info.status === 'Finished' || info.status === 'Cancelled';
  const how = plan.weekday ? (plan.every === 14 ? 'every other week' : 'every week') + ', on ' + plan.weekday
    : plan.every ? 'about ' + (plan.every === 1 ? 'every day' : 'every ' + plural(plan.every, 'day')) : '';
  const rhythm = !how ? '' : plan.paused ? ' It used to come ' + how + '; nothing since for far longer than that, so it may be on a break.'
    : ' Usually ' + how + '.';
  let next = '';
  if (plan.next && !done) {
    next = plan.late ? ' The next was due ' + niceDate(plan.next) + ' and has not turned up yet.'
      : ' Next expected around ' + new Date(plan.next + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) + '.';
    if (info.following && !plan.late) next += ' Family Hub looks for it that day.';
  }
  return el('p', { class: 'small ' + (plan.late || plan.paused ? 'warn' : 'dim'), text: lastText + rhythm + next });
}

async function pageSeries(id) {
  const info = await api('GET', '/api/series/' + encodeURIComponent(id));
  const again = () => pageSeries(id).catch(oops);
  const mark = (ids, read) => api('POST', '/api/series/chapters/read', { ids, read }).then(again).catch(oops);
  const list = info.chapters_list.slice().reverse();
  const missing = info.chapters_list.filter((chapter) => !chapter.downloaded).length;
  setMain(
    back('#/library/graphic', 'Back to graphic novels'),
    el('div', { class: 'detail' },
      el('div', { class: 'bside' }, el('div', { class: 'cover has-img', style: '--cv:' + coverColor(info.title) + ';background-image:url(' + seriesCover(info) + ')' })),
      el('div', { class: 'stack' }, el('h1', { class: 'glow' + titleFit(info.title), text: info.title }),
        el('p', { class: 'dim', text: [info.author, info.status, info.source].filter(Boolean).join(' · ') }),
        info.genres.length ? el('div', { class: 'row gap6', 'aria-label': 'Genres on the reading site' }, info.genres.map((genre) => el('span', { class: 'tag', text: genre }))) : null,
        el('p', { text: plural(info.chapters, 'chapter') + ' · ' + info.read + ' read · ' + info.downloaded + ' kept on the server' }),
        scheduleLine(info),
        info.chapters ? null : el('p', { class: 'small warn', text: (info.source || 'This site') + ' lists no chapters for this series. '
          + 'Sites often list the same series twice, one copy empty: go back and try the same title again, or the one from another site.' }),
        info.doubles ? el('p', { class: 'small dim', text: 'The site lists ' + plural(info.doubles,
            'chapter') + ' twice. Only one copy of each is kept: the 18+ or uncensored one when there is one.' }) : null,
        info.status === 'Finished' || info.status === 'Cancelled' ? el('p', { class: 'small dim',
            text: 'Publication is ' + (info.status === 'Finished' ? 'finished' : 'cancelled') + ', so new chapters are not looked for. Every chapter found is kept. "Look for new chapters" below still checks once.' }) : null,
        info.description ? clampText(info.description) : null,
        el('div', { class: 'row' },
          info.next ? el('a', { class: 'btn pri', href: '#/read/' + info.id + '/' + info.next.order, text: (info.read ? 'Continue: ' : 'Start: ') + info.next.name }) : null,
          !canRequest() ? null : el('button', { class: 'btn', type: 'button', text: info.following ? 'Stop following' : 'Follow', onclick: async () => {
            if (info.following && !window.confirm('Stop following ' + info.title + '? New chapters will no longer be fetched. The chapters already kept stay on the server.')) return;
            try { const added = await api('POST', '/api/series/' + info.id + '/follow',
                { on: !info.following }); toast(info.following ? 'No longer following.' : 'Following. ' + plural(added.queued,
                'chapter') + ' queued to download.'); again(); } catch (err) { oops(err); }
          } }),
          // Every kept chapter as one book in the reading app's catalogue, with a contents entry per chapter.
          el('button', { class: 'btn', type: 'button', text: info.mine ? 'In my books ✓' : 'Add to my books', 'aria-pressed': String(!!info.mine),
            title: 'Your reading app (Readest) gets every kept chapter as one book, and a longer copy as new chapters arrive.', onclick: async () => {
              try { await api('POST', '/api/series/' + info.id + '/mine',
                  { on: !info.mine }); toast(info.mine ? 'Taken out of your books.' : 'In your books: your reading app gets it as one book, with a chapter list.'); again(); } catch (err) { oops(err); }
            } }),
          missing && !info.local && canRequest() ? el('button', { class: 'btn', type: 'button', text: 'Keep the ' + plural(missing,
              'missing chapter'), onclick: async () => { try { const added = await api('POST',
              '/api/series/' + info.id + '/download'); toast(plural(added.queued,
              'chapter') + ' queued to download.'); } catch (err) { oops(err); } } }) : null,
          el('button', { class: 'btn', type: 'button', text: 'Look for new chapters', onclick: async () => { try { await api('GET',
              '/api/series/' + info.id + '?refresh=1'); toast('Chapter list refreshed.'); again(); } catch (err) { oops(err); } } }),
          info.unread ? el('button', { class: 'btn', type: 'button', text: 'Mark all as read',
              onclick: () => mark(info.chapters_list.filter((chapter) => !chapter.read).map((chapter) => chapter.id), true) }) : null))),
    info.files.length ? el('section', { class: 'panel cut stack' }, head('As comic files'),
      el('p', { class: 'small dim',
          text: 'Every kept chapter joined into one comic file (CBZ), with a chapter list inside, ' + (info.files.length > 1 ? 'in parts of 50 chapters. ' : '') + 'The server makes it by itself as chapters arrive; only the last part changes when new ones come.' }),
      el('div', { class: 'list' }, info.files.map((file) => el('div', { class: 'row between' },
        el('span', {}, el('strong', { text: info.files.length > 1 ? 'Part ' + file.part : info.title }), el('span', { class: 'small dim',
            text: ' · ' + plural(file.chapters,
            'chapter') + ' (' + file.first + (file.chapters > 1 ? ' – ' + file.last : '') + ')' + (file.size ? ' · ' + (file.size / 1048576).toFixed(0) + ' MB' : '') })),
        // a shorter copy made earlier still downloads while the newest chapters are being added
        file.state === 'ready' || file.older ? el('a', { class: 'btn sm', href: '/api/series/' + info.id + '/file/' + file.part, download: '',
            title: file.state === 'ready' ? null : 'The newest chapters are still being added; this is the copy made before them.',
            'aria-label': 'Download ' + file.title + ' as a comic file', text: 'Download CBZ' })
          : el('span', { class: 'small ' + (file.state === 'failed' ? 'bad' : 'dim'),
              text: file.state === 'making' ? 'Being put together…' : file.state === 'failed' ? 'Could not be made: ' + file.error : 'Waiting to be put together' }))))) : null,
    seriesOwn(info.title),
    el('section', { class: 'panel cut' }, head('Chapters'),
      list.length ? el('div', { class: 'scroll' }, el('table', { style: 'min-width:760px' },
        el('thead', {}, el('tr', {}, ['Chapter', 'Went up', 'Read', 'On the server', ''].map((heading) => el('th', { scope: 'col', text: heading })))),
        el('tbody', {}, list.map((entry) => el('tr', {},
          el('th', { scope: 'row', style: 'font-weight:' + (entry.read ? 400 : 700) }, el('a', { href: '#/read/' + info.id + '/' + entry.order, text: entry.name })),
          // the date the reading site gives; a few sites give every chapter the day the series was added there
          el('td', { class: 'dim', text: entry.uploaded ? niceDate(new Date(entry.uploaded).toISOString()) : '' }),
          el('td', { class: entry.read ? 'dim' : '', text: entry.read ? 'Read' : 'Not yet' }),
          el('td', { class: entry.downloaded || info.local ? 'ok' : 'dim', text: entry.downloaded || info.local ? 'Kept' : 'Not yet' }),
          el('td', {}, el('div', { class: 'row' },
            entry.downloaded ? el('a', { class: 'btn', href: '/api/series/chapters/' + entry.id + '/file', 'aria-label': 'Download ' + entry.name + ' as a file', text: 'Download' }) : null,
            el('button', { class: 'btn', type: 'button', 'aria-label': (entry.read ? 'Mark as not read: ' : 'Mark as read: ') + entry.name,
                text: entry.read ? 'Mark unread' : 'Mark read', onclick: () => mark([entry.id], !entry.read) })))))))) : el('p', { class: 'dim',
                text: 'No chapters found. Try "Look for new chapters".' })));
}

// One chapter, every page under the last: the way manhwa are drawn, and fine for manga on a tablet.
async function pageRead(id, order) {
  const info = await api('GET', '/api/series/' + encodeURIComponent(id));
  const orderNo = Number(order);
  const at = info.chapters_list.findIndex((candidate) => candidate.order === orderNo);
  if (at < 0) { location.hash = '#/series/' + id; return; }
  const chapter = info.chapters_list[at], before = info.chapters_list[at - 1], after = info.chapters_list[at + 1];
  const pages = el('div', { class: 'pages', role: 'status' }, el('p', { class: 'dim', text: 'Fetching the pages…' }));
  let marked = chapter.read;
  const done = async () => { if (marked) return; marked = true; try { await api('POST', '/api/series/chapters/read', { ids: [chapter.id],
      read: true }); } catch (err) { marked = false; oops(err); } };
  const nav = (where) => el('nav', { class: 'row between', 'aria-label': 'Chapters, ' + where },
    before ? el('a', { class: 'btn', href: '#/read/' + id + '/' + before.order, text: '← ' + before.name }) : el('span'),
    el('a', { href: '#/series/' + id, text: info.title }),
    after ? el('a', { class: 'btn', href: '#/read/' + id + '/' + after.order, text: after.name + ' →' }) : el('span'));
  const end = el('div', { class: 'row', style: 'justify-content:center' },
    after ? el('button', { class: 'btn pri', type: 'button', text: 'Next: ' + after.name, onclick: async () => { await done(); location.hash = '#/read/' + id + '/' + after.order; } })
      : el('button', { class: 'btn pri', type: 'button', text: 'Finished. Back to the series', onclick: async () => { await done(); location.hash = '#/series/' + id; } }));
  setMain(nav('top'), el('h1', { class: 'glow', text: chapter.name }), pages, end, nav('bottom'));
  window.scrollTo(0, 0);
  try {
    const data = await api('GET', '/api/series/chapters/' + chapter.id + '/pages');
    pages.removeAttribute('role');
    fill(pages, ...Array.from({ length: data.pages }, (_, index) => el('img',
        { src: '/api/series/' + id + '/chapter/' + chapter.order + '/page/' + index, alt: 'Page ' + (index + 1) + ' of ' + data.pages,
        loading: index < 3 ? 'eager' : 'lazy' })));
    // Reaching the end of the last page counts as having read the chapter.
    if (window.IntersectionObserver) new IntersectionObserver((seen, observer) => { if (seen.some((entry) => entry.isIntersecting)) { observer.disconnect(); done(); } }).observe(end);
  } catch (err) { fill(pages, el('p', { class: 'bad', text: err.message })); }
}

// ------------------------------------------------------------ book detail

// Where one book stands on one Kindle, with the button that fits.
function deviceLine(device, bookId, again) {
  const send = (label) => el('button', { class: 'btn', type: 'button', text: label, onclick: () => api('POST',
      '/api/books/' + bookId + '/send',
      { device_id: device.id }).then(() => { toast('Sending to ' + device.name + '.'); again(); }).catch(oops) });
  if (device.state === 'sent') return el('div', { class: 'row' }, el('span', { class: 'small ok',
      text: 'Sent to ' + device.name + ' · ' + niceDate(device.at) }), device.can_send ? send('Send again') : null);
  if (device.state === 'queued') return el('div', { class: 'row' }, el('span', { class: 'small dim', role: 'status', text: 'Sending to ' + device.name + '…' }));
  if (device.state === 'failed') return el('div', { class: 'row' }, el('span', { class: 'small bad',
      text: 'Not sent to ' + device.name + ': ' + device.reason }), device.can_send ? send('Try again') : null);
  return el('div', { class: 'row' }, device.can_send ? send('Send to ' + device.name) : el('span', { class: 'small dim', text: device.name + ': ' + device.why }));
}
let sendPolls = 0;

function tagSection(title, note, cls, list, onRemove, onAdd, addLabel, sub) {
  const input = el('input', { type: 'text', maxlength: 60 });
  return el('section', { class: 'stack' + (sub ? ' tagsub' : '') },
    sub ? el('div', { class: 'row gap8' }, el('h3', { text: title }), el('span', { class: 'small dim', text: note }))
      : head(title, el('span', { class: 'small dim', text: note })),
    el('div', { class: 'row gap8' }, list.length ? list.map((entry) => el('span', { class: 'chip tg' + (cls === 'mine' ? ' mine' : '') }, entry,
      onRemove ? el('button', { class: 'x', type: 'button', 'aria-label': 'Remove tag ' + entry, onclick: () => onRemove(entry), text: '×' }) : null))
      : el('span', { class: 'small dim', text: 'None yet.' })),
    onAdd ? el('form', { class: 'row end', onsubmit: (event) => { event.preventDefault(); if (input.value.trim()) onAdd(input.value.trim()); } },
      field(addLabel, input), el('button', { class: 'btn', type: 'submit', text: 'Add' })) : null);
}

// A heading's size by its length, so a long manga title does not take four lines of capitals.
function titleFit(title) {
  const length = (title || '').length;
  return length > 70 ? ' fit-xl' : length > 40 ? ' fit-l' : '';
}

// A "More" disclosure for actions used now and then; the dangerous ones go last inside it.
function moreMenu(items) {
  // Escape folds it back up, like the phone's More menu, and leaves focus on its button.
  return el('details', { class: 'more-menu', onkeydown: (event) => { if (event.key === 'Escape' && event.currentTarget.open) {
    event.currentTarget.open = false; event.currentTarget.querySelector('summary').focus(); } } },
  el('summary', { class: 'btn', text: 'More' }), el('div', { class: 'stack gap8' }, items));
}

async function pageBook(id) {
  const [book, sim] = await Promise.all([api('GET', '/api/books/' + id), api('GET', '/api/similar/' + id)]);
  // An old link to a second copy opens the book it is a copy of; its downloads and buttons belong to that book.
  if (String(book.id) !== String(id)) { history.replaceState(null, '', '#/book/' + book.id); lastHash = location.hash; id = book.id; }
  const again = () => pageBook(id).catch(oops);
  const post = (path, body, method) => api(method || 'POST', '/api/books/' + id + path, body).then(again).catch(oops);
  const guest = isGuest();
  const side = el('div', { class: 'bside' },
    el('div', { class: 'cover' + (book.has_cover ? ' has-img' : '') + titleSize(book.title),
        style: '--cv:' + coverColor(book.title) + (book.has_cover ? ';background-image:url(' + coverUrl(book) + ')' : '') },
      el('span', { class: 'fmt', text: book.formats.join(' · ') }), el('span', { class: 't t-hero', text: book.has_cover ? '' : book.title })),
    book.formats.map((fmt, index) => el('a', { class: 'btn' + (index === 0 ? ' pri' : ''), href: '/api/books/' + id + '/download/' + fmt, text: 'Download ' + fmt })),
    book.formats.length || book.converted ? null : el('p', { class: 'small warn',
        text: 'The library has this book\'s details but no file for it, so there is nothing to download. Adding the file again in Calibre-Web fixes it.' }),
    book.converted ? el('a', { class: 'btn pri', href: '/api/books/' + id + '/download/converted', text: 'Download converted EPUB' }) : null);
  if (book.formats.includes('PDF') && !book.formats.includes('EPUB') && !book.converted && !guest) {
    const running = book.convert_status === 'running';
    // append() would print a left-out part as the word "null"; filter them first
    side.append(...[el('button', { class: 'btn', type: 'button', disabled: running || !appState.can_convert, text: running ? 'Converting…' : 'Convert to EPUB',
        onclick: () => post('/convert').then(() => toast('Conversion started. Check back in a minute.')) }),
      book.convert_status === 'failed' ? el('p', { class: 'small bad', text: 'The last conversion failed. Scanned PDFs often cannot be converted.' }) : null,
      appState.can_convert ? null : el('p', { class: 'small dim',
          text: 'The converter is not installed in Family Hub' + (appState.cwa_url ? '; use Convert in Calibre-Web instead.' : '.') })].filter(Boolean));
  }
  // the details editor, with the book open beside it
  if (isAdmin()) side.append(el('a', { class: 'btn', href: '#/fix/' + id, text: 'Edit details' }));
  // Things done now and then (and deleting) wait behind More, away from the download buttons.
  const more = [];
  if (canSort()) more.push(coverBox(book, again));
  if (isAdmin() && appState.cwa_url) more.push(el('a', { class: 'btn', href: appState.cwa_url + '/book/' + book.id, target: '_blank', rel: 'noopener', text: 'Open in Calibre-Web' }));
  if (isAdmin()) {
    const copies = (book.copies || []).length;
    more.push(el('button', { class: 'btn danger', type: 'button', text: 'Delete this book', onclick: async () => {
      const files = (book.formats.join(', ') || 'no file') + (copies ? ' and ' + plural(copies, 'other copy', 'other copies') : '');
      if (!window.confirm('Delete "' + book.title + '" (' + files + ') from the library for good? Its files are removed. A copy of the library\'s database is saved first, and the nightly copy (if switched on) still has the book.')) return;
      try { const result = await api('DELETE', '/api/books/' + book.id); toast('"' + result.removed + '" was deleted.'); location.hash = '#/library'; } catch (err) { oops(err); }
    } }));
  }
  if (more.length) side.append(moreMenu(more));
  if (book.goodreads_id) side.append(goodreadsLink(book));
  if (book.copies && book.copies.length) {
    side.append(el('div', { class: 'stack gap6' },
      el('p', { class: 'small dim', text: 'The library holds ' + plural(book.copies.length, 'more copy', 'more copies') + ' of this book, shown here as one.' }),
      book.copies.flatMap((copy) => copy.files.map((file) => el('a', { class: 'small',
          href: '/api/books/' + copy.id + '/download/' + file.format, text: 'Other copy: ' + file.format + ', ' + fileSize(file.size) }))),
      el('a', { class: 'small', href: '#/copies', text: 'Manage copies' })));
  }

  const body = el('div', { class: 'bbody' });
  // A volume or issue names its own story under the title, and links to the rest of its series.
  const seriesName = book.collection || book.series;
  body.append(el('div', { class: 'stack' }, el('h1', { class: 'glow' + titleFit(book.title), text: book.title }),
    book.subtitle ? el('p', { class: 'fs-lg', text: book.subtitle }) : null,
    el('p', { class: 'dim fs-lg' }, [book.author, book.pages ? 'about ' + book.pages + ' pages' : ''].filter(Boolean).join(' · '),
      seriesName ? [book.author || book.pages ? ' · ' : '', el('a', { href: '#/shelf/' + encodeURIComponent(seriesName),
          text: (book.series_index && !book.part ? 'Book ' + book.series_index + ' of ' : 'Part of ') + book.series })] : null)));

  // Tags and the rating given to the whole series count for this volume too; they are changed on the series' page.
  const fs = book.from_series;
  body.append(el('section', { class: 'stack' }, head('Synopsis'),
    el('p', { class: 'synopsis' + (book.synopsis ? '' : ' dim'), text: book.synopsis || 'No synopsis in this book\'s details yet. Add one in Calibre-Web.' })));
  if (!guest) {
    body.append(el('section', { class: 'stack' },
      el('div', { class: 'row' },
        el('button', { class: 'btn' + (book.shelf.on ? ' done' : ''), type: 'button', 'aria-pressed': String(book.shelf.on),
            text: book.shelf.on ? 'In my books ✓' : 'Add to my books', onclick: () => setShelf(id, !book.shelf.on).then(again).catch(oops) }),
        el('span', { class: 'small dim', text: book.shelf.on ? 'Listed under My books in your reading app.' : 'My books are the ones offered to your devices.' })),
      book.shelf.devices.map((device) => deviceLine(device, id, again))));
    if (book.family_shelves && book.family_shelves.length) {
      body.append(el('section', { class: 'panel cut' }, head("Other people's books"),
        el('p', { class: 'small dim', text: 'Admins can put this book on someone else\'s list, send it to their Kindle, and record how far they have got.' }),
        el('div', { class: 'list' }, book.family_shelves.map((shelf) => el('div', { class: 'row between' },
          el('div', { class: 'stack gap6' },
            el('div', { class: 'row' }, el('strong', { text: shelf.name }),
              el('button', { class: 'btn sm' + (shelf.on ? ' done' : ''), type: 'button', 'aria-pressed': String(shelf.on),
                  text: shelf.on ? 'In ' + shelf.name + "'s books ✓" : 'Add to ' + shelf.name + "'s books", onclick: () => setShelf(id, !shelf.on,
                  { id: shelf.profile_id, name: shelf.name }).then(again).catch(oops) })),
            shelf.devices.map((device) => deviceLine(device, id, again))),
          field('Reading status for ' + shelf.name, el('select', { onchange: (event) => api('POST', '/api/books/' + id + '/reading',
              { profile_id: shelf.profile_id, status: event.target.value }).then((got) => { if (got.finished) finishedToast(got.finished, book.title); again(); }).catch(oops) },
            [['none', 'Not started'], ['reading', 'Reading'], ['finished', 'Finished']].map(([key, label]) => el('option', { value: key, text: label, selected: shelf.status === key })))))))));
    }
    const waiting = book.shelf.devices.concat(...(book.family_shelves || []).map((person) => person.devices)).some((device) => device.state === 'queued');
    if (!waiting) sendPolls = 0;
    else if (sendPolls++ < 12) setTimeout(() => { if (location.hash === '#/book/' + id) again(); }, 5000);
    const pct = el('input', { type: 'number', min: 0, max: 100, value: Math.round(book.percent || 0) });
    body.append(el('section', { class: 'stack' },
      book.status === 'reading' ? el('div', { style: 'max-width:480px' }, bar(book.percent, Math.round(book.percent) + ' percent read')) : null,
      el('p', { class: 'small dim',
          text: book.status === 'finished' ? 'Finished ' + niceDate(book.finished) : (book.status === 'reading' ? Math.round(book.percent) + '% read' + (book.device ? ' · last read on ' + book.device : '') : 'Not started') }),
      el('div', { class: 'row end', role: 'group', 'aria-label': 'Reading status' },
        el('button', { class: 'chip', type: 'button', 'aria-pressed': String(book.status === 'none'), text: 'Not started', onclick: () => post('/reading', { status: 'none' }) }),
        el('button', { class: 'chip', type: 'button', 'aria-pressed': String(book.status === 'reading'), text: 'Reading', onclick: () => post('/reading', { status: 'reading' }) }),
        el('button', { class: 'chip', type: 'button', 'aria-pressed': String(book.status === 'finished'), text: 'Finished',
          onclick: () => api('POST', '/api/books/' + id + '/reading', { status: 'finished' }).then((got) => { if (got.finished) finishedToast(got.finished, book.title); again(); }).catch(oops) }),
        el('div', { class: 'field', style: 'max-width:140px' }, el('label', { class: 'lab', for: 'pct', text: 'My place (%)' }), Object.assign(pct, { id: 'pct' })),
        el('button', { class: 'btn', type: 'button', text: 'Set my place', onclick: () => post('/reading', { percent: Number(pct.value) }) }))));
  }
  if (!guest) {
    body.append(el('section', { class: 'panel cut' }, el('div', { class: 'cols' },
      el('div', { class: 'stack' }, head('My rating'),
        el('div', { class: 'stars', role: 'group', 'aria-label': 'Rate this book' }, [1, 2, 3, 4, 5].map((stars) =>
          el('button', { class: 'star' + (stars <= book.rating ? ' on' : ''), type: 'button', 'aria-label': 'Rate ' + stars + ' of 5',
              'aria-pressed': String(stars === book.rating), onclick: () => post('/rating', { rating: stars === book.rating ? 0 : stars }) },
              starMark()))),
        el('p', { class: 'small dim',
            text: book.rating ? 'You rated this ' + book.rating + ' of 5' : fs && fs.rating ? 'Not rated on its own; the whole series has ' + fs.rating + ' of 5' : 'Not rated yet' }),
        book.readers ? el('p', { class: 'small', text: '★ Readers: ' + readersText(book.readers) }) : null),
      el('div', { class: 'stack' }, head('Family ratings'),
        book.family_ratings.length ? el('div', { class: 'list' }, book.family_ratings.map((rating) => el('div', { class: 'row between' },
            el('span', { text: rating.name }), el('strong', { text: rating.rating + ' of 5' }))))
          : el('p', { class: 'small dim', text: 'Nobody else has rated this yet.' })))));
  }
  const kindSel = el('select', { 'aria-label': 'Kind of book', onchange: (event) => api('POST', '/api/books/kind', { ids: [book.id], kind: event.target.value }).then(again).catch(oops) },
    appState.kinds.map((kind) => el('option', { value: kind, text: kind, selected: kind === book.kind })));
  const tagsPanel = el('section', { class: 'panel cut stack tagpanel' }, head('Tags'),
    el('div', { class: 'row' }, el('span', { class: 'small dim', text: 'Kind of book' }), canSort() ? kindSel : el('strong', { text: book.kind })));

  const canTag = canSort();
  tagsPanel.append(tagSection('Book file tags', "From the file's own details, tidied", '', book.tags.epub, null, null, null, true));
  const community = tagSection('Community tags',
      (book.community_sources.length ? 'From ' + book.community_sources.join(', ') : 'From book catalogues') + ' · read-only', 'community',
      book.tags.community, null, null, null, true);
  if (canTag && book.can_look_up) {
    const look = el('button', { class: 'btn', type: 'button', text: book.tags.community.length ? 'Look up again' : 'Look up tags', onclick: async () => {
      look.disabled = true; look.textContent = 'Asking the catalogues…';
      try {
        const result = await api('POST', '/api/books/' + id + '/lookup');
        if (result.added) toast(plural(result.added, 'tag') + ' from ' + result.sources.join(' and ') + '.');
        else if (result.failed.length) toast(result.failed.join(' and ') + ' could not be reached. Try again later.', true);
        else toast('The catalogues do not know this book under this title and author. Fixing its details in Calibre-Web usually helps.', false, 9000);
        again();
      } catch (err) { oops(err); look.disabled = false; look.textContent = 'Look up tags'; }
    } });
    community.append(el('div', { class: 'row' }, look));
  }
  tagsPanel.append(community);
  tagsPanel.append(tagSection('Family tags', 'Shared with the whole family', 'family', book.tags.family,
    canTag ? (tag) => post('/tags', { kind: 'family', tag: tag }, 'DELETE') : null, canTag ? (tag) => post('/tags', { kind: 'family', tag: tag }) : null, 'New family tag', true));
  if (!guest) {
    tagsPanel.append(tagSection('My tags', 'Only on your profile', 'mine', book.tags.mine,
      (tag) => post('/tags', { kind: 'mine', tag: tag }, 'DELETE'), (tag) => post('/tags', { kind: 'mine', tag: tag }), 'New tag', true));
  }
  body.append(tagsPanel);
  if (fs && (fs.rating || fs.tags.family.length || fs.tags.mine.length)) {
    body.append(el('section', { class: 'panel cut stack' }, head('From the whole series'),
      el('p', { class: 'small dim' }, 'Given to every volume of ', el('a', { href: '#/shelf/' + encodeURIComponent(fs.name), text: fs.name }), ". Change them on the series' page."),
      fs.tags.family.length || fs.tags.mine.length ? el('div', { class: 'row gap6' },
        fs.tags.family.map((tag) => el('span', { class: 'tag', text: tag })), fs.tags.mine.map((tag) => el('span', { class: 'tag mine', text: tag }))) : null,
      !guest && fs.rating ? el('p', { class: 'small', text: 'Series rating: ' + fs.rating + ' of 5' }) : null));
  }
  body.append(similarList('Similar books', sim.books));
  setMain(back('#/library', 'Back to the library'),
    el('div', { class: 'detail' }, side, body));
}

function similarList(title, books) {
  return el('section', { class: 'stack' }, head(title),
    books.length ? el('div', { class: 'tiles' }, books.slice(0, 9).map((book) => el('div', { class: 'tile cut stack' },
      el('strong', { class: 'fs-lg', text: book.title }), el('span', { class: 'small dim', text: book.author }),
      el('span', { class: 'lab', text: 'Shares: ' + book.shared.join(', ') }),
      book.already_read ? el('span', { class: 'small dim', text: 'You have read this' }) : null,
      book.in_library ? el('a', { class: 'btn', href: '#/book/' + book.id, text: 'Open in library' })
        : (canRequest() ? el('a', { class: 'btn pri', href: '#/discover/' + encodeURIComponent(book.title + ' ' + book.author),
            text: 'Find it' }) : el('span', { class: 'small dim', text: 'Not in the library' })))))
      : el('p', { class: 'small dim', text: 'No matches yet. Add a few tags to this book to get suggestions.' }));
}

// ------------------------------------------------------------- dashboard

// The Personal / My books tabs, with Refresh: catch up now (new books, Hardcover, series for the reading app).
function dashTabs(active, reload) {
  const btn = el('button', { class: 'btn sm', type: 'button', text: 'Refresh', title: 'Look again for new books and reading, and get your series ready for your reading app', onclick: async () => {
    btn.disabled = true; btn.textContent = 'Refreshing…';
    try {
      const result = await api('POST', '/api/me/refresh');
      const said = ['Up to date.'];
      if (result.hardcover !== null) said.push('Hardcover: ' + plural(result.hardcover, 'book') + ' updated.');
      if (result.hardcover_error) said.push('Hardcover: ' + result.hardcover_error);
      if (result.series_making) said.push(plural(result.series_making, 'series', 'series') + ' being put together for your reading app; it arrives there by itself in a few minutes.');
      else if (result.series_ready) said.push('Your series are ready for your reading app.');
      toast(said.join(' '));
      await reload();
    } catch (err) { oops(err); } finally { btn.disabled = false; btn.textContent = 'Refresh'; }
  } });
  return el('div', { class: 'row between', style: 'align-items:center' },
    el('nav', { class: 'tabs', 'aria-label': 'Dashboard views' },
      el('a', { href: '#/dashboard', 'aria-current': active === 'me' ? 'page' : null, text: 'Personal' }),
      el('a', { href: '#/mybooks', 'aria-current': active === 'mybooks' ? 'page' : null, text: 'My books' })),
    btn);
}
// Dashboard, My books: what is on my list and my devices, what I am reading, what I have finished.
async function pageMyBooks() {
  const [shelf, mine, reading, read] = await Promise.all([api('GET', '/api/me/shelf'), api('GET', '/api/books?filter=shelf'),
    api('GET', '/api/books?filter=reading'), api('GET', '/api/books?filter=read')]);
  const again = () => pageMyBooks().catch(oops);
  const apps = shelf.devices.filter((device) => device.kind !== 'kindle').map((device) => device.name);
  const finished = read.books.slice().sort((book, index) => String(index.finished || '').localeCompare(String(book.finished || '')));
  const grid = (books) => el('div', { class: 'shelf' }, books.map((book) => bookCard(book, again, { plain: true })));
  const appBox = el('section', { class: 'panel cut', 'aria-label': 'For your reading app' });
  const comicsBox = el('section', { class: 'panel cut', 'aria-label': 'For your graphic novel app', hidden: true });
  setMain(
    pageHead('My books', 'The books on your list, where each one stands on your devices, and what you are reading and have read.'),
    dashTabs('mybooks', () => pageMyBooks()),
    appBox,
    comicsBox,
    el('section', { class: 'panel cut' }, head('On my devices', el('a', { class: 'btn', href: '#/devices/' + appState.me.id, text: 'Add or change devices' })),
      shelf.devices.length ? null : el('p', { class: 'small warn', text: 'You have no devices yet. Add one and the books on your list are offered to it.' }),
      apps.length ? el('p', { class: 'small dim',
          text: apps.join(' and ') + (apps.length === 1 ? ' fetches' : ' fetch') + ' these from the catalogue under My books. "For your reading app" above shows which ones have been downloaded.' }) : null,
      mine.books.length ? el('div', { class: 'list' }, mine.books.map((book) => {
        const onShelf = shelf.books[book.id] || { devices: [] };
        return el('div', { class: 'row between' },
          el('div', { class: 'stack', style: 'gap:4px;min-width:220px;flex:1' }, el('a', { class: 'ink', href: '#/book/' + book.id, text: book.title }),
            el('span', { class: 'small dim', text: book.author + (onShelf.added ? ' · added ' + niceDate(onShelf.added) : '') })),
          el('div', { class: 'stack', style: 'gap:4px;flex:2' }, onShelf.devices.length ? onShelf.devices.map((device) => deviceLine(device, book.id, again)) : null),
          el('button', { class: 'btn', type: 'button', 'aria-label': 'Take off my list: ' + book.title, text: 'Take off my list', onclick: () => setShelf(book.id, false).then(again).catch(oops) }));
      })) : el('p', { class: 'small dim', text: 'Nothing on your list yet. Press "Add to my books" under a book in the Library.' })),
    el('section', { class: 'panel cut' }, head('Reading now'),
      reading.books.length ? grid(reading.books) : el('p', { class: 'small dim', text: 'Nothing in progress.' })),
    el('section', { class: 'panel cut' }, head('Finished'),
      finished.length ? el('p', { class: 'small dim', text: plural(finished.length, 'book') + ', the most recent first.' }) : null,
      finished.length ? grid(finished) : el('p', { class: 'small dim', text: 'Nothing finished yet.' })));
  watchReadingApp(appBox);
  watchComicsApp(comicsBox);
}

// What the graphic novel app (Paperback) can get from Komga, and what's new in it. Paperback is really bad at
// saying what it has and hasn't got yet, so this is the list to check it against.
const KOMGA_STATUS = { ONGOING: 'Ongoing', HIATUS: 'On hiatus', ENDED: 'Ended', ABANDONED: 'No more coming' };
async function watchComicsApp(box) {
  let data;
  try { data = await api('GET', '/api/me/comics-app'); } catch (err) { box.hidden = false; fill(box, el('p', { class: 'small bad', text: err.message })); return; }
  if (!box.isConnected) return;
  box.hidden = !data.used;
  if (!data.used) return;
  if (!data.ready) { fill(box, head('For your graphic novel app'), el('p', { class: 'small warn', text: data.message })); return; }
  // straight to the series on Komga's own page, which also works as a reader in the browser
  const seriesLink = (item) => el('a', { class: 'ink', text: item.title, target: '_blank', rel: 'noopener',
    href: data.port ? location.protocol + '//' + location.hostname + ':' + data.port + '/series/' + item.series_id : null });
  const isNew = (group) => !data.seen || group.newest > data.seen;
  const unit = (kind, count) => plural(count, kind === 'graphic' ? 'volume' : 'chapter');
  const added = (group) => {
    const books = group.books, span = books.length > 1 ? books[0].name + ' – ' + books[books.length - 1].name : books[0].name;
    return el('div', { class: 'stack gap4' },
      el('div', { class: 'row between' },
        el('div', { class: 'row gap10' }, seriesLink(group), isNew(group) ? el('span', { class: 'chip xs', text: 'New' }) : null),
        el('span', { class: 'small dim', text: 'added ' + niceDate(group.newest) })),
      el('span', { class: 'small' + (group.unread ? '' : ' dim'),
        text: unit(group.kind, books.length) + ': ' + span + (group.unread ? ' · ' + group.unread + ' unread' : ' · all read') }));
  };
  const onTheWay = (item) => el('div', { class: 'row between' }, el('span', { text: item.title }),
    el('span', { class: 'small dim', text: item.kind === 'graphic'
      ? unit('graphic', item.count) + ' being copied in · there within 10 minutes'
      : unit('chapters', item.count) + ' still downloading on the series server' + (item.status === 'Hiatus' ? ' · on hiatus' : '') }));
  const shelfRow = (item) => el('div', { class: 'stack gap4' },
    el('div', { class: 'row between' },
      el('div', { class: 'row gap10' }, seriesLink(item), item.status ? el('span', { class: 'chip xs', text: KOMGA_STATUS[item.status] || item.status }) : null),
      el('span', { class: 'small ' + (item.unread ? '' : 'ok'), text: item.unread ? item.unread + ' of ' + item.total + ' unread' : 'All ' + item.total + ' read' })),
    item.kind === 'chapters' && item.summary ? el('span', { class: 'small dim', text: item.summary }) : null);
  const fresh = data.added.filter(isNew);
  const unread = data.shelf.reduce((total, item) => total + item.unread, 0);
  const section = (title, items, row) => items.length ? el('div', { class: 'stack' }, el('h3', { text: title + ' (' + items.length + ')' }), el('div', { class: 'list' }, items.map(row))) : null;
  const seenButton = fresh.length ? el('button', { class: 'btn sm', type: 'button', text: 'Mark all as seen',
    title: 'Clears the New marks, once your graphic novel app has everything',
    onclick: () => api('POST', '/api/me/comics-app/seen').then(() => watchComicsApp(box)).catch(oops) }) : null;
  fill(box, head('For your graphic novel app', seenButton),
    el('div', { class: 'stack gap6' },
      el('div', { class: 'row between' },
        el('strong', { role: 'status', text: fresh.length ? plural(fresh.length, 'series', 'series') + ' with something new' : 'Nothing new since you last looked' }),
        el('span', { class: 'small dim', text: plural(data.shelf.length, 'series', 'series') + ' in Komga · ' + unread + ' unread' }))),
    el('p', { class: 'small dim', text: 'Paperback gets these from Komga, signed in as ' + data.account + ". It doesn't always notice new ones by itself: "
      + 'open a series marked New, pull down to refresh it, then download what you want to read offline.' }),
    section('Added in the last ' + data.days + ' days', data.added, added) || el('p', { class: 'small dim', text: 'Nothing added in the last ' + data.days + ' days.' }),
    data.coming.length ? el('details', {}, el('summary', { text: 'On the way (' + data.coming.length + ')' }), el('div', { class: 'list' }, data.coming.map(onTheWay))) : null,
    data.shelf.length ? el('details', {}, el('summary', { text: 'Everything in Komga (' + data.shelf.length + ')' }), el('div', { class: 'list' }, data.shelf.map(shelfRow))) : null);
}

// Everything on my list as the reading app gets it: how much of it the app has downloaded, what is still to come
// and why. Looks again every few seconds while something is being put together, and now and then otherwise,
// so a download by the app shows up.
const APP_STATE = { on_app: ['ok', 'On your reading app'], newer: ['', 'A new copy is ready · your app fetches it at its next check'],
  ready: ['', 'Ready · your app fetches it at its next check'], making: ['', 'Being put together'],
  waiting: ['dim', 'Waiting its turn'], failed: ['bad', 'Could not be put together'] };
async function watchReadingApp(box) {
  let data;
  try { data = await api('GET', '/api/me/reading-app'); } catch (err) { fill(box, el('p', { class: 'small bad', text: err.message })); return; }
  if (!box.isConnected) return;
  box.hidden = !data.total;
  const todo = data.items.filter((item) => item.state !== 'on_app'), have = data.items.filter((item) => item.state === 'on_app');
  // Still to come, in two groups: what the server is still making, and what is ready for the app to fetch.
  const making = todo.filter((item) => ['making', 'waiting', 'failed'].includes(item.state)), fetch = todo.filter((item) => !making.includes(item));
  const group = (title, items) => items.length ? el('div', { class: 'stack' }, el('h3', { text: title + ' (' + items.length + ')' }), el('div', { class: 'list' }, items.map(row))) : null;
  const row = (item) => {
    const [cls, words] = APP_STATE[item.state] || ['', item.state];
    return el('div', { class: 'stack gap4' },
      el('div', { class: 'row between' }, el('a', { class: 'ink', href: item.link, text: item.title }),
        el('div', { class: 'row gap10' },
          el('span', { class: 'small ' + cls, text: words + (item.state === 'making' ? ' · ' + item.percent + '%' : '') + (item.error ? ': ' + item.error : '') }),
          item.state === 'ready' || item.state === 'newer' ? el('button', { class: 'btn sm', type: 'button', 'aria-label': "It's on my app: " + item.title, text: "It's on my app",
            title: 'For a book your reading app downloaded before Family Hub started noting downloads',
            onclick: () => api('POST', '/api/me/reading-app/have', { item: item.item }).then(() => watchReadingApp(box)).catch(oops) }) : null)),
      item.state === 'making' ? bar(item.percent, item.title + ': ' + item.percent + ' percent put together') : null);
  };
  const share = data.total ? Math.round(100 * data.on_app / data.total) : 0;
  fill(box, head('For your reading app'),
    el('div', { class: 'stack gap6' },
      el('div', { class: 'row between' }, el('strong', { role: 'status', text: data.on_app + ' of ' + plural(data.total, 'item') + ' on your reading app' }),
        el('span', { class: 'small dim', text: todo.length ? todo.length + ' still to come' : 'Everything is there' })),
      bar(share, share + ' percent of your list is on your reading app')),
    el('p', { class: 'small dim', text: data.comics_elsewhere
      ? 'Counted from what your reading app has downloaded from the catalogue. Graphic novels and series by chapter are read in Paperback instead, from Komga, so they are not counted here.'
      : 'Counted from what your reading app has downloaded from the catalogue. A comic series counts once per part; a series by chapter once per 50 chapters.' }),
    group('Being made on the server', making),
    group('Ready, not fetched yet', fetch),
    have.length ? el('details', {}, el('summary', { text: 'On your reading app (' + have.length + ')' }), el('div', { class: 'list' }, have.map(row))) : null);
  const busy = data.making.length > 0;
  if (busy || todo.length) setTimeout(() => watchReadingApp(box), busy ? 5000 : 20000);
}

// On every page of a profile: a note while something on its list is being put together for the reading app.
const makingNote = el('a', { class: 'making-note', href: '#/mybooks', 'aria-live': 'polite', hidden: true });
async function checkMaking() {
  if (!appState || !appState.me || isGuest()) { makingNote.hidden = true; return; }
  try {
    const data = await api('GET', '/api/me/reading-app');
    const now = data.making.find((item) => item.state === 'making') || data.making[0];
    makingNote.hidden = !now;
    if (now) {
      fill(makingNote, el('span', { class: 'lab', text: 'Being added to your reading app' }),
        el('span',
            { text: now.title + (now.state === 'making' ? ' · ' + now.percent + '%' : ' · waiting its turn') + (data.making.length > 1 ? ' (and ' + (data.making.length - 1) + ' more)' : '') }),
        now.state === 'making' ? bar(now.percent, now.percent + ' percent') : null);
      if (!makingNote.isConnected) document.body.append(makingNote);
    }
  } catch (err) { makingNote.hidden = true; }
}
setInterval(() => { if (!document.hidden) checkMaking(); }, 10000);

// A number tile. empty: a sentence shown instead of a big 0.
function tile(label, num, note, empty) {
  if (empty && !num) return el('div', { class: 'tile cut empty' }, el('div', { class: 'lab', text: label }), el('p', { class: 'dim', text: empty }));
  return el('div', { class: 'tile cut' }, el('div', { class: 'lab', text: label }), el('div', { class: 'num glow', text: num }), el('div', { class: 'small dim', text: note || '' }));
}
function barList(title, rows, color) {
  const max = Math.max(1, ...rows.map((row) => row[1]));
  return el('section', { class: 'panel cut' }, el('h3', { text: title }),
    rows.length ? rows.slice(0, 5).map(([name, count]) => el('div', { class: 'stack gap4' },
      el('div', { class: 'row between' }, el('span', { text: name }), el('strong', { text: count })),
      el('div', { class: 'meter', 'aria-hidden': 'true' }, el('i', { style: 'width:' + Math.round(100 * count / max) + '%;background:' + color }))))
      : el('p', { class: 'small dim', text: 'Nothing yet this year.' }));
}

async function pageDashboard() {
  const view = { kind: '', value: '', metric: 'books' };
  const base = await api('GET', '/api/stats/me');
  const trend = el('section', { class: 'panel cut' });
  const chips = [{ kind: '', value: '', label: 'All books', src: '' }];
  base.top.epub.slice(0, 3).forEach(([tag]) => chips.push({ kind: 'genre', value: tag, label: tag, src: 'Genre' }));
  base.top.community.slice(0, 3).forEach(([tag]) => chips.push({ kind: 'community', value: tag, label: tag, src: 'Community' }));
  base.top.family.slice(0, 2).forEach(([tag]) => chips.push({ kind: 'family', value: tag, label: tag, src: 'Family' }));
  base.top.mine.slice(0, 2).forEach(([tag]) => chips.push({ kind: 'mine', value: tag, label: tag, src: 'Mine' }));
  base.authors.slice(0, 3).forEach(([author]) => chips.push({ kind: 'author', value: author, label: author, src: 'Author' }));

  async function drawTrend() {
    const stats = view.kind ? await api('GET', '/api/stats/me?' + qs({ kind: view.kind, value: view.value })) : base;
    const pages = view.metric === 'pages';
    const max = Math.max(1, ...stats.months.map((month) => (pages ? month.pages : month.library + month.outside)));
    const totalPages = stats.months.reduce((total, month) => total + month.pages, 0);
    // The chart starts at the first month anything was read, not with a row of empty months.
    const first = stats.months.findIndex((month) => month.library + month.outside + month.pages > 0);
    const months = first < 0 ? [] : stats.months.slice(first);
    fill(trend, 
      head('Reading trend', el('div', { class: 'row', role: 'group', 'aria-label': 'Measure' }, [['books', 'Books'], ['pages', 'Pages']].map(([key, label]) =>
          el('button', { class: 'chip', type: 'button', 'aria-pressed': String(view.metric === key), text: label, onclick: () => { view.metric = key; drawTrend().catch(oops); } })))),
      el('div', { class: 'row', role: 'group', 'aria-label': 'Filter the trend' }, el('span', { class: 'lab', text: 'Filter by' }),
        chips.map((chip) => el('button', { class: 'chip', type: 'button',
            'aria-pressed': String(view.kind === chip.kind && view.value === chip.value),
            onclick: () => { view.kind = chip.kind; view.value = chip.value; drawTrend().catch(oops); } },
          el('strong', { text: chip.label }), chip.src ? el('span', { class: 'src', text: chip.src }) : null))),
      el('strong', { role: 'status', text: plural(stats.filtered_total,
          'book') + (view.kind ? (view.kind === 'author' ? ' by ' : ' tagged ') + view.value : ' read') + ' in ' + stats.year + ' · ' + totalPages.toLocaleString() + ' pages' }),
      el('div', { class: 'row' }, el('span', { class: 'key' }, el('i', { style: 'background:var(--accent)' }), 'Family library'),
        el('span', { class: 'key' }, el('i',
            { style: 'background:repeating-linear-gradient(135deg,var(--accent2) 0 2px,transparent 2px 5px);border:1px solid var(--accent2)' }),
            'Read elsewhere (Kindle and others)')),
      months.length ? monthChart(months.map((month) => ({ month: month.month, value: pages ? month.pages.toLocaleString() : month.library + month.outside, scale: pages ? month.pages : undefined,
        pages: month.pages, series: [['e', month.library], ['k', month.outside]] })),
            (month) => MONTHS[month.month - 1] + ': ' + plural(month.series[0][1] + month.series[1][1], 'book') + ', ' + month.pages + ' pages')
        : el('p', { class: 'small dim', text: 'Nothing finished yet in ' + stats.year + (view.kind ? ' with this filter.' : '. Finished books appear here month by month.') }));
  }

  const readingNow = el('section', { class: 'panel cut' }, head('Reading now'),
    base.reading_now.length ? el('div', { class: 'list' }, base.reading_now.map((book) => el('div', { class: 'stack gap6' },
      el('div', { class: 'row' }, book.id ? el('a', { href: '#/book/' + book.id, class: 'fs-lg strong',
          text: book.title }) : el('strong', { class: 'fs-lg', text: book.title }),
        el('span', { class: 'tag', text: book.in_library ? 'Library' : 'Elsewhere' })),
      el('span', { class: 'small dim', text: book.author }),
      book.in_library || book.percent ? bar(book.percent, Math.round(book.percent) + ' percent read') : null,
      el('span', { class: 'small dim',
          text: (book.in_library || book.percent ? Math.round(book.percent) + '% · ' : 'Started ' + niceDate(book.started) + ' · place not reported · ') + (book.device ? 'last read on ' + book.device + ' · ' : '') + 'updated ' + ago(book.updated).toLowerCase() }))))
      : el('p', { class: 'small dim', text: 'Nothing in progress.' }));

  const oTitle = el('input', { type: 'text' }), oAuthor = el('input', { type: 'text' }), oPages = el('input', { type: 'number', min: 1 });
  const oStatus = el('select', {}, el('option', { value: 'finished', text: 'Finished' }), el('option', { value: 'reading', text: 'Reading now' }));
  const outside = el('form', { class: 'panel cut', onsubmit: async (event) => {
    event.preventDefault();
    try { await api('POST', '/api/me/outside', { title: oTitle.value, author: oAuthor.value, pages: oPages.value,
        status: oStatus.value }); toast('Added.'); pageDashboard(); } catch (err) { oops(err); }
  } }, head('Add a book read elsewhere'),
    el('p', { class: 'small dim', text: 'For Kindle Unlimited, paper books or anything else outside the family library.' }),
    el('div', { class: 'row end' }, field('Title', oTitle), field('Author', oAuthor), field('Pages (optional)', oPages), field('Status', oStatus),
      el('button', { class: 'btn pri', type: 'submit', text: 'Add book' })),
    appState.me.tracker_linked ? el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', text: 'Update from Hardcover now', onclick: async () => {
      try { const result = await api('POST', '/api/me/tracker-sync'); toast(plural(result.updated, 'book') + ' updated.'); pageDashboard(); } catch (err) { oops(err); }
    } })) : null);

  setMain(
    pageHead('Your reading', 'Family library and everything read elsewhere · ' + base.year + ' so far'),
    dashTabs('me', () => pageDashboard()),
    el('div', { class: 'tiles' },
      tile('Books read this year', base.read_books,
          base.books_library + ' from the family library · ' + (base.read_books - base.books_library) + ' elsewhere',
          'None finished yet this year. Finish one, or add a book read elsewhere below.'),
      tile('Graphic novels read this year', base.read_graphic, plural(base.read_graphic,
          'volume') + ' across ' + plural(base.graphic_series, 'series', 'series'), 'None finished yet this year. Each volume counts once.'),
      tile('Read this month', base.read_month, plural(base.month_books, 'book') + ' · ' + plural(base.month_graphic, 'graphic novel volume'), 'Nothing finished yet this month.'),
      tile('Pages read this year', base.pages_year.toLocaleString(), base.pages_unknown ? plural(base.pages_unknown, 'book') + ' without a page count' : 'From finished books'),
      tile('Authors read this year', base.authors_year, '', 'No named authors yet; books with an unknown author are not counted.')),
    trend,
    el('div', { class: 'cols3' },
      barList('Top book file tags', base.top.epub, 'var(--accent)'), barList('Top community tags', base.top.community, 'var(--accent2)'),
      barList('Top family and personal tags', base.top.family.concat(base.top.mine).sort((left, right) => right[1] - left[1]),
          'var(--accent3)'), barList('Authors read most', base.authors, 'var(--accent3)')),
    el('div', { class: 'cols' }, readingNow,
      el('section', { class: 'panel cut' }, head('Recently finished'),
        base.recent.length ? el('div', { class: 'list' }, base.recent.map((book) => el('div', { class: 'row between' },
          el('span', {}, book.id ? el('a', { href: '#/book/' + book.id, text: book.title }) : book.title,
            el('span', { class: 'small dim', text: (book.volumes ? ' · ' + plural(book.volumes,
                'volume') : '') + (book.graphic && !book.volumes ? ' · graphic novel' : '') + (book.in_library ? '' : ' · elsewhere') })),
          el('span', { class: 'small dim', text: niceDate(book.finished) })))) : el('p', { class: 'small dim', text: 'No finished books yet this year.' }))),
    kindlePanel(),
    outside);
  await drawTrend();
}

// Kindle: the books in this person's Kindle library and a diary of the days each one moved on. Amazon gives
// out no reading history, so a script run on the Kindle library page saves where each book is up to, and every
// file brought in here is one entry in the diary (see kindle.py). Finished is pressed here: Amazon does not say
// how long a book is.
function kindlePanel() {
  const box = el('section', { class: 'panel cut stack', 'aria-label': 'Kindle' }, head('Kindle'), el('p', { class: 'small dim', role: 'status', text: 'Loading…' }));
  const shortDay = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  const picker = el('input', { type: 'file', accept: '.json,application/json', class: 'kd-file', 'aria-label': 'The kindle-reading.json file' });
  picker.addEventListener('change', async () => {
    const file = picker.files[0];
    if (!file) return;
    let payload;
    try { payload = JSON.parse(await file.text()); } catch (err) { toast('That file is not the one the script saves (kindle-reading.json).', true); return; }
    try {
      const got = await api('POST', '/api/me/kindle', payload);
      toast(plural(got.books, 'book') + ' brought in' + (got.new ? ', ' + got.new + ' new' : '') + (got.moved ? ', ' + plural(got.moved, 'book') + ' read further' : '') + '.');
      draw(got);
    } catch (err) { oops(err); }
    picker.value = '';
  });
  const copy = el('button', { class: 'btn sm', type: 'button', text: 'Copy the script', onclick: async () => {
    try {
      const text = await (await fetch('/static/kindle-export.js')).text();
      await navigator.clipboard.writeText(text);
      toast('Copied. Paste it into the console on your Kindle library page.');
    } catch (err) { window.open('/static/kindle-export.js', '_blank', 'noopener'); }
  } });
  const steps = el('ol', { class: 'kd-steps small' },
    el('li', {}, copy, ' (or ', el('a', { href: '/static/kindle-export.js', target: '_blank', rel: 'noopener', text: 'open it' }), ').'),
    el('li', {}, 'On a computer, open ', el('a', { href: 'https://read.amazon.com.au/kindle-library', target: '_blank', rel: 'noopener', text: 'your Kindle library' }),
      ', signed in. Open the console (Ctrl+Shift+J in Chrome), paste the script and press Enter. It saves kindle-reading.json.'),
    el('li', {}, 'Bring the file in here: ', picker));
  function bookLine(book) {
    const status = book.status === 'finished' ? 'Finished' + (book.finished ? ' ' + niceDate(book.finished) : '')
      : book.status === 'reading' ? 'Reading' : 'Not started';
    const days = book.days.length ? 'Read on ' + book.days.map(shortDay).join(', ')
      : book.status === 'reading' ? 'Started before ' + shortDay(book.first_seen) + '; no reading since' : '';
    const mark = (finished) => api('POST', '/api/me/kindle/' + book.asin + '/finished', { finished }).then((got) => {
      if (finished) finishedToast(got.finished, book.title); else toast('Marked not finished.');
      draw(got);
    }).catch(oops);
    return el('div', { class: 'row between gap8' },
      el('div', { class: 'stack gap4 grow' },
        el('strong', { class: 'wrap', text: book.title }),
        el('span', { class: 'small dim', text: [book.author, book.unlimited ? 'Kindle Unlimited' : ''].filter(Boolean).join(' · ') }),
        el('span', { class: 'small' + (book.status === 'finished' ? ' ok' : '') }, status, days ? el('span', { class: 'dim', text: ' · ' + days }) : null)),
      book.status === 'finished'
        ? el('button', { class: 'linkish', type: 'button', text: 'Not finished', 'aria-label': 'Not finished: ' + book.title, onclick: () => mark(false) })
        : book.status === 'reading' ? el('button', { class: 'btn sm', type: 'button', text: 'Finished', 'aria-label': 'Finished: ' + book.title, onclick: () => mark(true) }) : null);
  }
  function draw(data) {
    const groups = [['reading', 'Reading'], ['finished', 'Finished'], ['none', 'Not started']].map(([state, name]) => [name, data.books.filter((book) => book.status === state)]);
    fill(box, head('Kindle', data.last_import ? el('span', { class: 'small dim', text: 'Last brought in ' + niceDate(data.last_import) }) : null),
      el('p', { class: 'small dim', text: 'Amazon only gives out how far you are in each book. Each time you bring a file in, Family Hub notes which books moved on since the last one, so bring one in often (weekly is fine) to see the days you read. Press Finished when you finish one.' }),
      steps,
      data.books.length ? groups.filter(([, books]) => books.length).map(([name, books]) => el('div', { class: 'stack gap8' },
        el('h3', { text: name + ' (' + books.length + ')' }),
        el('div', { class: 'list' }, (name === 'Not started' ? books.slice(0, 8) : books).map(bookLine)),
        name === 'Not started' && books.length > 8 ? el('p', { class: 'small dim', text: 'And ' + (books.length - 8) + ' more. All of them are in the library search, marked Kindle.' }) : null))
        : el('p', { class: 'small dim', text: 'No Kindle books here yet. Follow the steps above.' }));
  }
  api('GET', '/api/me/kindle').then(draw).catch((err) => { fill(box, head('Kindle'), el('p', { class: 'small bad', text: err.message })); });
  return box;
}

// ----------------------------------------------------------------- admin

// A column chart in the design's style: one stacked column per month, the months underneath.
// series: [[class, value], ...] per month, drawn bottom first. Classes are the design's: 'e' solid, 'k' hatched.
function monthChart(months, label) {
  const total = (month) => month.series.reduce((sum, followed) => sum + followed[1], 0);
  const max = Math.max(1, ...months.map((month) => month.scale === undefined ? total(month) : month.scale));
  return el('div', { class: 'scroll' },
    el('div', { class: 'chart' }, months.map((month) => {
      const unit = 140 / max * (month.scale === undefined ? 1 : (total(month) ? month.scale / total(month) : 0));
      return el('div', { class: 'm' }, el('div', { class: 'num fs-lg tight', text: month.value }),
        el('div', { class: 'stack', role: 'img', 'aria-label': label(month) },
          month.series.slice().reverse().map(([cls, value]) => el('div', { class: cls, style: 'height:' + Math.round(value * unit) + 'px' }))));
    })),
    el('div', { class: 'months', 'aria-hidden': 'true' }, months.map((month) => el('span', { text: MONTHS[month.month - 1] }))));
}

function serverCard(sv, reload) {
  const status = (ok, yes, no) => el('span', { class: ok ? 'ok' : 'warn', text: ok ? yes : no });
  return el('section', { class: 'panel cut' }, head('Server'),
    el('div', { class: 'list' },
      el('div', { class: 'row between' }, 'Book library', status(sv.library, 'Found', 'Not found')),
      el('div', { class: 'row between' }, 'Search (Prowlarr)', sv.search.configured ? status(sv.search.ok, 'Connected', 'Not reachable') : el('span', { class: 'dim', text: 'Not set up' })),
      el('div', { class: 'row between' }, 'Series by chapter (Suwayomi)', sv.series.configured ? (sv.series.ok ? el('span',
          { class: sv.series.sources ? 'ok' : 'warn', text: sv.series.sources ? 'Connected · ' + plural(sv.series.sources,
          'source') : 'Connected, but it has no sources yet' }) : status(false, '', 'Not reachable')) : el('span', { class: 'dim',
          text: 'Not set up' })),
      el('div', { class: 'row between' }, 'Finished-downloads folder', status(sv.downloads_folder, 'Found', 'Not set up')),
      el('div', { class: 'row between' }, 'Inbox for new files', sv.inbox_waiting === null ? el('span', { class: 'dim',
          text: 'Not set up' }) : el('span', { class: 'ok', text: sv.inbox_waiting ? plural(sv.inbox_waiting, 'file') + ' waiting' : 'Empty' })),
      el('div', { class: 'row between' }, 'Library import folder', sv.ingest_folder ? (sv.ingest_stuck ? el('span', { class: 'warn',
          text: plural(sv.ingest_stuck,
          'file') + ' waiting longer than usual. Family Hub has asked Calibre-Web to look again; if this stays, restart Calibre-Web.' }) : el('span',
          { class: 'ok', text: sv.ingest_waiting ? plural(sv.ingest_waiting, 'file') + ' being added' : 'Found' })) : el('span', { class: 'warn',
          text: 'Not set up' })),
      el('div', { class: 'row between' }, 'Guest access', el('span', { class: 'dim', text: sv.remote_guest_blocked ? 'Home network only' : 'Anywhere' }))),
    el('div', { class: 'row' }, el('button', { class: 'btn sm', type: 'button', text: 'Rescan library and tag packs',
        onclick: async () => { try { const result = await api('POST', '/api/admin/rescan'); toast(plural(result.books,
        'book') + ' in the library.'); reload(); } catch (err) { oops(err); } } })));
}

// Admin, Hub: the look, the profiles, the connections and the server. Visible to admins only.
async function pageHubAdmin() {
  const [stats, conn, looks] = await Promise.all([api('GET', '/api/stats/admin'), api('GET', '/api/admin/connections'), api('GET', '/api/admin/look')]);
  const reload = () => pageHubAdmin().catch(oops);
  setMain(
    pageHead('Admin', 'Set-up for the whole hub. Visible to admins only.'),
    appearanceCard(looks, reload),
    profilesCard(stats),
    mediaCard(reload),
    servicesCard(),
    connectionsCard(conn.groups, reload, conn.in_file),
    setupFileCard(conn.in_file),
    el('div', { class: 'cols' }, serverCard(stats.services, reload),
      el('section', { class: 'panel cut' }, head('Copies to a second disk'), secondCopyLines(stats.services.second_copy, reload))));
}

// A program's address as this device can reach it: the saved one, or this page's address with the program's port.
// A saved address on "localhost" only works on the server itself, so this page's address is used instead.
function localLink(link, port) {
  let href = link || 'http://' + location.hostname + ':' + port;
  try { const url = new URL(href); if (['localhost',
      '127.0.0.1'].includes(url.hostname)) { url.hostname = location.hostname; href = url.href.replace(/\/$/,
      ''); } } catch (err) { /* keep it as saved */ }
  return href;
}
const jellyfinWatch = (jellyfin, id) => localLink(jellyfin.link, jellyfin.port) + '/web/#/details?id=' + id;

// A show's seasons, then its episodes, each with a Watch button once it is on the server. A film gets one button.
function watchPanel(kind, id, item) {
  const box = el('div', { class: 'stack' }, el('p', { class: 'small dim', role: 'status', text: kind === 'film' ? 'Finding it in Jellyfin…' : 'Asking for the episodes…' }));
  const section = el('section', { class: 'panel cut' }, head(kind === 'film' ? 'Watch' : 'Seasons and episodes'), box);
  api('GET', '/api/media/watch/' + kind + '/' + id).then((data) => {
    if (kind === 'film') {
      if (!item.downloaded) { section.remove(); return; }
      fill(box, data.watch_id ? el('div', { class: 'row' }, el('a', { class: 'btn pri', href: jellyfinWatch(data.jellyfin, data.watch_id),
          target: '_blank', rel: 'noopener', text: 'Watch ' + item.title }))
        : el('p', { class: 'small dim', text: 'On the server, but Jellyfin has not listed it yet. It usually appears within a few minutes.' }));
      return;
    }
    if (!data.seasons.length) { fill(box, el('p', { class: 'small dim', text: 'Sonarr has no episodes listed for this show yet.' })); return; }
    let pick = (data.seasons.find((season) => season.episodes.some((episode) => episode.on_server)) || data.seasons[0]).number;
    const chips = el('div', { class: 'row gap6', role: 'group', 'aria-label': 'Season' });
    const list = el('div', { class: 'list' });
    function draw() {
      fill(chips, data.seasons.map((option) => {
        const have = option.episodes.filter((episode) => episode.on_server).length;
        return el('button', { class: 'chip', type: 'button', 'aria-pressed': String(option.number === pick), onclick: () => { pick = option.number; draw(); } },
          'Season ' + option.number, el('span', { class: 'src', text: have + '/' + option.episodes.length }));
      }));
      const season = data.seasons.find((candidate) => candidate.number === pick);
      fill(list, season.episodes.map((episode) => el('div', { class: 'row between gap10' },
        el('div', { class: 'stack', style: 'gap:2px;min-width:0' },
          el('strong', { text: episode.number + '. ' + (episode.title || 'Episode ' + episode.number) }),
          el('span', { class: 'small ' + (episode.downloading !== null && episode.downloading !== undefined && !episode.on_server ? 'ok' : 'dim'),
            text: [episode.aired ? niceDate(episode.aired) : 'Not aired yet',
                episode.on_server ? '' : episode.downloading !== null && episode.downloading !== undefined ? 'downloading · ' + episode.downloading + '%' : episode.wanted ? 'being looked for' : 'not wanted'].filter(Boolean).join(' · ') })),
        episode.on_server && episode.watch_id ? el('a', { class: 'btn sm pri', href: jellyfinWatch(data.jellyfin, episode.watch_id),
            target: '_blank', rel: 'noopener', 'aria-label': 'Watch episode ' + episode.number + ', ' + episode.title, text: 'Watch' })
          : episode.on_server ? el('span', { class: 'small dim', text: 'On the server' }) : null)));
    }
    draw();
    fill(box, chips, list);
  }).catch((err) => { if (!(err instanceof Stale)) fill(box, el('p', { class: 'small bad', text: err.message })); });
  return section;
}

// Links to each program's own page, built from the address Family Hub was opened with, so they work from
// any device at home. Checking which ones answer takes a moment, so the card fills in by itself.
function servicesCard() {
  const list = el('div', { class: 'list' }, el('p', { class: 'small dim', role: 'status', text: 'Checking which programs are running…' }));
  api('GET', '/api/admin/services').then((data) => fill(list, data.services.map((service) => {
    const href = localLink(service.link, service.port);
    return el('div', { class: 'row between gap8' },
      el('div', { class: 'stack', style: 'gap:2px;min-width:0' }, el('strong', { text: service.name }), el('span', { class: 'small dim', text: service.what })),
      el('div', { class: 'row gap10' },
        el('span', { class: 'small ' + (service.running ? 'ok' : 'dim'), text: service.running ? 'Running' : 'Not running' }),
        service.running ? el('a', { class: 'btn sm', href, target: '_blank', rel: 'noopener', 'aria-label': 'Open ' + service.name + ' in a new tab', text: 'Open' }) : null));
  }))).catch((err) => { if (!(err instanceof Stale)) fill(list, el('p', { class: 'small bad', text: err.message })); });
  return el('section', { class: 'panel cut' }, head('Manage the programs', el('span', { class: 'small dim', text: 'Each opens its own page in a new tab' })),
    el('p', { class: 'small dim',
        text: 'These work from any computer or phone on the home network. From outside the house they need a VPN into home (for example Tailscale); they are not on the internet by themselves.' }),
    list);
}

// The colour scheme and accent everyone sees, the Scope on Home, and the screen-change scan.
const HUE_NAMES = [[15, 'Pink'], [40, 'Red'], [70, 'Orange'], [105, 'Yellow'], [165, 'Green'], [215, 'Teal'], [250, 'Sky'], [280, 'Blue'], [320, 'Purple'], [345, 'Magenta'], [360, 'Pink']];
const hueName = (hue) => (HUE_NAMES.find(([top]) => hue < top) || HUE_NAMES[0])[1];

function appearanceCard(data, reload) {
  const look = data.look;
  const design = data.designs.find((candidate) => candidate.id === look.design) || data.designs[0];
  const save = (body, msg) => api('POST', '/api/admin/look', body).then((reply) => { appState.look = reply.look; applyLook(reply.look); toast(msg); reload(); }).catch(oops);
  const swatch = (colours) => el('span', { class: 'swatch', 'aria-hidden': 'true' }, colours.filter(Boolean).map((colour) => el('i', { style: 'background:' + colour })));
  const hue = look.accent === null ? design.default_hue : look.accent;
  const hueLabel = el('span', { class: 'small dim', text: look.accent === null ? 'Purple · default' : hueName(hue) + ' · ' + hue + '°' });
  // The slider shows the colour as it moves and saves when it is let go.
  const slider = el('input', { type: 'range', min: '0', max: '359', step: '1', value: String(hue), 'aria-label': 'Accent hue', 'aria-valuetext': hueName(hue),
    oninput: () => { const chosenHue = +slider.value; setAccent(chosenHue); slider.setAttribute('aria-valuetext',
        hueName(chosenHue)); hueLabel.textContent = hueName(chosenHue) + ' · ' + chosenHue + '°'; },
    onchange: () => save({ accent: +slider.value }, 'The accent is now ' + hueName(+slider.value).toLowerCase() + ' for everyone.') });
  return el('section', { class: 'panel cut' }, head('Appearance', el('span', { class: 'small dim', text: 'For everyone, on every device' })),
    el('div', { class: 'stack' }, el('span', { class: 'lab', text: 'Colour scheme' }),
      el('div', { class: 'looks', role: 'group', 'aria-label': 'Colour scheme' }, design.schemes.map((sc) =>
        el('button', { class: 'chip scheme', type: 'button', 'aria-pressed': String(sc.id === look.theme),
            onclick: () => save({ theme: sc.id }, sc.name + ' is now the colour scheme for everyone.') },
          swatch(sc.swatch), el('span', { text: sc.name }))))),
    el('div', { class: 'huepick' },
      el('div', { class: 'row', style: 'justify-content:space-between;gap:10px' }, el('span', { class: 'lab', text: 'Accent colour' }), hueLabel),
      el('div', { class: 'track' }, slider),
      el('div', { class: 'presets', role: 'group', 'aria-label': 'Accent presets' }, design.accents.map((accent) =>
        el('button', { type: 'button', 'aria-pressed': String(accent.hue === hue), onclick: () => save({ accent: accent.hue }, 'The accent is now ' + accent.name.toLowerCase() + ' for everyone.') },
          el('i', { style: '--sw:oklch(.68 .16 ' + accent.hue + ')' }), accent.name))),
      el('div', { class: 'row gap10' },
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
  const chips = el('div', { class: 'row gap8', role: 'group', 'aria-label': 'Visual on Home' });
  const status = el('p', { class: 'small dim', role: 'status' });
  const nowShowing = el('span', { class: 'small dim' });
  const preview = visualFrame(look.scene === 'daily' ? '' : look.scene, (visual) => {
    nowShowing.textContent = visual.name ? (look.scene === 'daily' ? 'Today: ' + visual.name : 'Showing ' + visual.name) : '';
    const list = Array.isArray(visual.list) ? visual.list : [];
    fill(chips, [['daily', 'A different one each day']].concat(list.map((entry) => [entry.id, entry.name])).map(([id, name]) =>
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(id === look.scene), text: name,
        onclick: () => save({ scene: id }, id === 'daily' ? 'Home now shows a different visual each day.' : name + ' now shows on Home every day.') })));
  });
  const check = el('button', { class: 'btn sm', type: 'button', text: 'Check for new visuals', onclick: async () => {
    check.disabled = true;
    try { showStatus(await api('POST', '/api/admin/visuals/check')); } catch (err) { oops(err); }
    check.disabled = false;
  } });
  function showStatus(st) {
    const where = st.source === 'github' ? 'From GitHub: ' + st.repo + ' (version ' + st.version + '), ' + plural(st.count, 'visual')
      : (st.repo ? 'Built in for now (' + plural(st.count,
          'visual') + '); ' + st.repo + ' has not been read yet' : 'Built in: ' + plural(st.count,
          'visual') + '. Add your own from a GitHub repository under Connections, Visuals on Home');
    status.className = 'small ' + (st.error ? 'bad' : 'dim');
    status.textContent = where + (st.checked ? ' · checked ' + ago(st.checked).toLowerCase() : '') + (st.error ? ' · ' + st.error : '');
    check.hidden = !st.repo;
    if (appState.visuals && st.version !== appState.visuals.version) { appState.visuals.version = st.version; toast('New visuals arrived.'); route(); }
  }
  api('GET', '/api/admin/visuals').then(showStatus).catch(() => {});
  return el('div', { class: 'stack' }, el('div', { class: 'row between' }, el('span', { class: 'lab', text: 'Visual on Home' }), nowShowing),
    chips,
    el('div', { style: 'border:1px solid var(--line2);line-height:0' }, preview),
    el('div', { class: 'row between gap8' }, status, check));
}

// Every profile, what it may do, and adding another.
function profilesCard(stats) {
  const patch = (id, body, msg) => api('PATCH', '/api/profiles/' + id, body).then(() => { toast(msg || 'Saved.'); route(); }).catch(oops);
  const nName = el('input', { type: 'text' }), nPass = el('input', { type: 'password', autocomplete: 'new-password' });
  const nKind = el('select', {}, el('option', { value: 'member', text: 'Member: everything except admin tools' }),
    el('option', { value: 'restricted', text: 'Restricted: read, rate and tag only' }), el('option', { value: 'admin', text: 'Admin' }));
  // Admins may do all of these whatever the switch says, so their rows say "Always" rather than offer a switch that does nothing.
  const yesNo = (person, profile, key, label) => person.role === 'admin' ? el('td', { class: 'small dim', text: 'Always' }) : el('td', {},
      el('button', { class: 'chip xs', type: 'button', 'aria-pressed': String(!!profile[key]), 'aria-label': label + ': ' + person.name,
      text: profile[key] ? 'Yes' : 'No', onclick: () => patch(person.id, { [key]: !profile[key] }) }));
  return el('section', { class: 'panel cut' }, head('Profiles', el('span', { class: 'small dim', text: 'Who can do what' })),
    el('div', { class: 'scroll' }, el('table', { style: 'min-width:1080px' },
      el('thead', {}, el('tr', {}, ['Profile', 'Role', 'Last activity', 'Can find new books', 'Can request films and TV',
          'Can edit family tags', 'Can see series by chapter', ''].map((heading) => el('th', { scope: 'col', text: heading })))),
      el('tbody', {}, stats.family.map((person) => {
        const profile = appState.profiles.find((candidate) => candidate.id === person.id) || {};
        return el('tr', {}, el('th', { scope: 'row', text: person.name }),
          el('td', {}, el('div', { class: 'field' }, el('select', { 'aria-label': 'Role for ' + person.name, style: 'min-width:120px',
              onchange: (event) => patch(person.id, { role: event.target.value }) },
            el('option', { value: 'member', text: 'Member', selected: person.role === 'member' }), el('option', { value: 'admin', text: 'Admin', selected: person.role === 'admin' })))),
          el('td', { class: 'small dim', text: ago(person.last) }),
          yesNo(person, profile, 'can_request', 'Can find new books'),
          yesNo(person, profile, 'can_request_media', 'Can request films and TV'),
          yesNo(person, profile, 'can_tag', 'Can edit family tags'),
          yesNo(person, profile, 'can_series', 'Can see series by chapter'),
          el('td', {}, el('div', { class: 'row', style: 'gap:8px;flex-wrap:nowrap' },
            el('a', { class: 'btn sm', href: '#/devices/' + person.id, 'aria-label': 'Devices for ' + person.name, text: 'Devices' }),
            el('button', { class: 'btn sm', type: 'button', text: 'Reset passcode',
                onclick: () => { const passcode = window.prompt('New passcode for ' + person.name + ' (leave empty to remove it):',
                ''); if (passcode !== null) patch(person.id, { passcode: passcode },
                passcode ? 'Passcode updated.' : person.name + ' has no passcode now.'); } }),
            person.id === appState.me.id ? null : el('button', { class: 'btn sm danger', type: 'button', text: 'Delete', onclick: async () => {
              if (!window.confirm('Delete ' + person.name + ' and their ratings, tags and reading history? A backup is saved first.')) return;
              try { await api('DELETE', '/api/profiles/' + person.id); toast('Profile deleted.'); route(); } catch (err) { oops(err); }
            } }))));
      })))),
    el('form', { class: 'row end', onsubmit: async (event) => {
      event.preventDefault();
      const restricted = nKind.value === 'restricted';
      try { await api('POST', '/api/profiles', { name: nName.value, passcode: nPass.value,
          role: nKind.value === 'admin' ? 'admin' : 'member', can_request: !restricted,
          can_tag: true }); toast('Profile added.'); route(); } catch (err) { oops(err); }
    } }, field('New profile name', nName), field('Passcode (optional)', nPass), field('Type', nKind), el('button', { class: 'btn pri', type: 'submit', text: 'Add profile' })));
}

// Admin, Books: the library, its tidying, the family's reading, and the book data backup.
async function pageBooksAdmin() {
  const stats = await api('GET', '/api/stats/admin');
  const reload = () => pageBooksAdmin().catch(oops);
  const file = el('input', { type: 'file', accept: 'application/json,.json' });
  setMain(
    pageHead('Library and family', 'Books only · visible to admins · ' + stats.year + ' so far'),
    el('div', { class: 'tiles' },
      tile('Books in the library', stats.total.toLocaleString(), stats.formats.map(([fmt, count]) => count + ' ' + fmt).join(' · ')),
      tile('Added this month', stats.added_month.toLocaleString(), stats.added_year.toLocaleString() + ' added this year'),
      tile('Books read by the family', stats.read_year, stats.read_month + ' so far this month'),
      tile('Never opened', stats.never_opened.toLocaleString(), 'Books no profile has started')),
    sortCard(stats), tagsCard(stats.tags), detailsCard(), copiesCard(stats.copies), elsewhereCard(stats.elsewhere), attentionCard(stats, reload),
    el('section', { class: 'panel cut' }, head('Books added each month'),
      monthChart(stats.added_by_month.map((month) => ({ month: month.month, value: month.count, series: [['e', month.count]] })),
          (month) => MONTHS[month.month - 1] + ': ' + plural(month.value, 'book') + ' added')),
    el('section', { class: 'panel cut' }, head('Family reading'),
      el('div', { class: 'scroll' }, el('table', {},
        el('thead', {}, el('tr', {}, ['Profile', 'Read this year', 'This month', 'Reading now', 'Last activity'].map((heading,
            index) => el('th', { scope: 'col', class: index >= 1 && index <= 3 ? 'num' : '', text: heading })))),
        el('tbody', {}, stats.family.map((person) => el('tr', {}, el('th', { scope: 'row', text: person.name }),
          el('td', { class: 'num', text: person.read_year }), el('td', { class: 'num', text: person.read_month }), el('td', { class: 'num', text: person.reading }),
          el('td', { class: 'small dim', text: ago(person.last) }))))))),
    el('div', { class: 'cols2' },
      el('section', { class: 'panel cut' }, head('Recently added'),
        stats.recent.length ? el('div', { class: 'list' }, stats.recent.map((book) => el('div', { class: 'row between' }, el('a',
            { href: '#/book/' + book.id, text: book.title }), el('span', { class: 'small dim',
            text: book.author + ' · ' + niceDate(book.added) })))) : el('p', { class: 'small dim', text: 'Nothing yet.' })),
      el('section', { class: 'panel cut' }, head('Book data backup'),
        el('p', { class: stats.last_backup ? 'ok' : 'dim',
            text: 'Last backup: ' + (stats.last_backup ? niceDate(stats.last_backup) + ' (' + ago(stats.last_backup).toLowerCase() + ')' : 'none yet') }),
        el('p', { class: 'small dim',
            text: 'Ratings, family tags, personal tags, community tags, book file tags and reading history, saved as one file. A copy is written automatically every day.' }),
        el('div', { class: 'row gap8' }, el('button', { class: 'btn pri sm', type: 'button', text: 'Back up now',
            onclick: async () => { try { const result = await api('POST',
            '/api/admin/backup'); toast('Saved ' + result.file); reload(); } catch (err) { oops(err); } } }),
          el('a', { class: 'btn sm', href: '/api/admin/export', text: 'Download book data' })),
        el('div', { class: 'row end' }, field('Restore from a book data file', file), el('button', { class: 'btn', type: 'button', text: 'Import', onclick: async () => {
          if (!file.files[0]) return toast('Choose a file first.', true);
          const fd = new FormData(); fd.append('file', file.files[0]);
          try { const result = await api('POST', '/api/admin/import', fd); toast('Imported data for ' + plural(result.books, 'book') + '.'); reload(); } catch (err) { oops(err); }
        } })))));
}

// The whole setup in one zip locked with a passphrase, to set up or rebuild the server (setupfile.py).
function setupFileCard(inFile) {
  const phrase = el('input', { type: 'password', autocomplete: 'new-password', minlength: '10' });
  const again = el('input', { type: 'password', autocomplete: 'new-password' });
  const note = el('p', { class: 'small', role: 'status' });
  const make = async (event) => {
    event.preventDefault();
    if (phrase.value.length < 10) { note.className = 'small bad'; note.textContent = 'Use a passphrase of at least 10 characters.'; return; }
    if (phrase.value !== again.value) { note.className = 'small bad'; note.textContent = 'The two passphrases are not the same.'; return; }
    const btn = event.target.querySelector('button[type=submit]');
    btn.disabled = true;
    note.className = 'small dim'; note.textContent = 'Packing the setup… this takes a moment.';
    try {
      const res = await fetch('/api/admin/setup-file', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passphrase: phrase.value }) });
      if (!res.ok) { let msg = 'Something went wrong (' + res.status + ').'; try { msg = (await res.json()).error || msg; } catch (err) { /* not JSON */ } throw new Error(msg); }
      const blob = await res.blob();
      const name = (res.headers.get('Content-Disposition') || '').match(/filename="?([^";]+)/);
      const link = el('a', { href: URL.createObjectURL(blob), download: name ? name[1] : 'family-hub-setup.zip' });
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
      phrase.value = again.value = '';
      note.className = 'small ok'; note.textContent = 'Downloaded (' + fileSize(blob.size) + '). Give the passphrase separately, by voice or another app.';
    } catch (err) { note.className = 'small bad'; note.textContent = err.message; }
    btn.disabled = false;
  };
  return el('section', { class: 'panel cut' }, head('Setup file'),
    el('p', { class: 'small dim', text: 'Everything needed to set Family Hub up again, or on the home server, in one file locked with a passphrase: '
      + 'the .env file with every setting and password, the profiles and reading history, and each program\'s own setup (indexers, qBittorrent, Radarr, Sonarr, subtitles, Jellyfin, Calibre-Web). '
      + 'No books or films. Unpack it into the Family Hub folder with 7-Zip and the passphrase (guide 7 has the steps).' }),
    inFile ? el('form', { class: 'row end gap8', onsubmit: make }, field('Passphrase (10 or more characters)', phrase), field('Again', again),
      el('button', { class: 'btn pri', type: 'submit', text: 'Make the setup file' }))
      : el('p', { class: 'small bad', text: 'Family Hub cannot see the .env file. Start it with docker compose from its folder, which shares the file with it.' }),
    note);
}

function connectionsCard(groups, reload, inFile) {
  return el('section', { class: 'panel cut' }, head('Connections'),
    el('p', { class: 'small dim', text: inFile
      ? 'Addresses and keys for the other services. Saving writes them into the .env file, the one file that holds every setting and password, and takes effect straight away. Keys and passwords are never shown again.'
      : 'Addresses and keys for the other services. A value saved here replaces the one in the .env file and takes effect straight away. Keys and passwords are stored on the server and never shown again.' }),
    el('div', { class: 'list' }, groups.map((group) => {
      const inputs = {};
      const note = el('p', { class: 'small', role: 'status' });
      const typed = () => { const values = {}; group.fields.forEach((setting) => { const value = inputs[setting.name].value.trim(); if (setting.kind !== 'secret' || value) values[setting.name] = value; }); return values; };
      const fields = group.fields.map((setting) => {
        const secret = setting.kind === 'secret';
        const input = setting.kind === 'toggle' ? el('select', {}, el('option', { value: 'on', text: 'On',
            selected: setting.value === 'on' }), el('option', { value: 'off', text: 'Off', selected: setting.value !== 'on' }))
          : el('input', { type: secret ? 'password' : (setting.kind === 'url' ? 'url' : 'text'),
              inputmode: setting.kind === 'number' ? 'numeric' : setting.kind === 'coordinate' ? 'decimal' : null,
              autocomplete: secret ? 'new-password' : 'off', value: setting.value,
            placeholder: secret ? (setting.set ? 'Saved. Type a new one to replace it' : '') : (setting.kind === 'url' ? 'http://' : '') });
        inputs[setting.name] = input;
        const where = setting.source === 'here' ? 'Saved here' : setting.source === 'file' ? 'In the .env file' : (setting.source === 'env' ? 'From the .env file' : (setting.source === 'default' ? 'Usual setting' : 'Not set'));
        return el('div', { class: 'stack', style: 'gap:4px;flex:1;min-width:220px' }, field(setting.label, input),
          el('div', { class: 'row gap8' }, el('span', { class: 'small dim', text: where }),
            setting.source === 'here' || setting.source === 'file' ? el('button', { class: 'btn', type: 'button', text: 'Remove', 'aria-label': 'Remove saved ' + setting.label, onclick: async () => {
              try { await api('POST', '/api/admin/connections', { values: { [setting.name]: '' } }); toast(setting.label + ' removed.'); reload(); } catch (err) { oops(err); }
            } }) : null));
      });
      // Each service folds away; its line says whether anything is filled in yet.
      const used = group.fields.some((setting) => setting.set && setting.source !== 'default');
      return el('details', {}, el('summary', { class: 'row between' }, el('span', { text: group.title }), el('span', { class: 'small ' + (used ? 'ok' : 'dim'), text: used ? 'Set' : 'Not set up' })),
        el('form', { class: 'stack', style: 'padding-top:8px', onsubmit: async (event) => {
        event.preventDefault();
        try { await api('POST', '/api/admin/connections', { values: typed() }); toast(group.title + ' saved.'); reload(); } catch (err) { oops(err); }
      } }, el('p', { class: 'small dim', text: group.hint }),
        el('div', { class: 'row end' }, fields),
        el('div', { class: 'row' },
          group.can_test ? el('button', { class: 'btn', type: 'button', text: 'Test connection', onclick: async () => {
            note.className = 'small dim'; note.textContent = 'Testing…';
            try { const result = await api('POST', '/api/admin/connections/test', { group: group.id,
                values: typed() }); note.className = 'small ' + (result.ok ? 'ok' : 'bad'); note.textContent = result.message; } catch (err) { note.className = 'small bad'; note.textContent = err.message; }
          } }) : null,
          el('button', { class: 'btn pri', type: 'submit', text: 'Save' })),
        note));
    })));
}

function sortCard(stats) {
  const unsorted = (stats.kinds.find((kind) => kind[0] === 'Unsorted') || [0, 0])[1];
  return el('section', { class: 'panel cut' }, head('Kinds of book', el('a', { class: 'btn', href: '#/sort', text: 'Sort the library' })),
    el('p', { class: 'small dim', text: 'Family Hub guesses a kind for each new book. ' + (unsorted ? plural(unsorted, 'book') + ' could not be guessed.' : 'Every book has a kind.') }),
    el('div', { class: 'row' }, stats.kinds.map(([key, count]) => el('span', { class: 'tag', text: (KIND_LABEL[key] || key) + ' · ' + count }))));
}

function lookupLine(lookup) {
  if (!lookup.on) return 'Looking up tags is switched off under Connections.';
  const from = lookup.sources.join(' and ');
  if (lookup.running || lookup.waiting) return 'Asking ' + from + ': ' + lookup.asked + ' of ' + lookup.books + ' books looked up so far, ' + lookup.matched + ' recognised. This carries on by itself.';
  return lookup.matched + ' of ' + lookup.books + ' books were recognised by ' + from + '.' + (lookup.errors ? ' ' + plural(lookup.errors, 'lookup') + ' failed and will be tried again.' : '');
}

function tagsCard(tags) {
  return el('section', { class: 'panel cut' }, head('Tags', el('a', { class: 'btn', href: '#/tags', text: 'Tidy tags' })),
    el('p', { class: 'small dim', text: lookupLine(tags.lookups) }),
    el('div', { class: 'row' }, el('span', { class: 'tag', text: 'Community tags · ' + tags.community }), el('span', { class: 'tag', text: 'Book file tags · ' + tags.file }),
      el('span', { class: 'tag', text: 'Removed as clutter · ' + tags.removed }), tags.hidden ? el('span', { class: 'tag', text: 'Hidden or renamed by you · ' + tags.hidden }) : null));
}

async function pageTags() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const tags = await api('GET', '/api/admin/tags');
  const again = () => pageTags().catch(oops);
  const rule = (body, msg) => api('POST', '/api/admin/tags/rule', body).then(() => { toast(msg); again(); }).catch(oops);
  const shown = tags.tags.filter((tag) => tag.becomes === null || tag.becomes === undefined);
  const merged = new Set((tags.merged || []).map((tag) => tag.toLowerCase()));  // merges have their own list, with Undo
  const changed = tags.tags.filter((tag) => tag.becomes !== null && tag.becomes !== undefined && !merged.has(tag.tag.toLowerCase()));
  const filterBox = el('input', { type: 'search', placeholder: 'Filter', oninput: debounce(() => draw(), 200) });
  const body = el('tbody');
  function draw() {
    const needle = filterBox.value.trim().toLowerCase();
    fill(body, ...shown.filter((item) => !needle || item.tag.toLowerCase().includes(needle)).map((entry) => {
      const name = el('input', { type: 'text', maxlength: 60, value: entry.tag, 'aria-label': 'New name for ' + entry.tag });
      return el('tr', {}, el('th', { scope: 'row', text: entry.tag }), el('td', { class: 'num', text: entry.count }), el('td', { class: 'small dim', text: entry.sources.join(', ') }),
        el('td', {}, el('div', { class: 'row' }, name,
          el('button', { class: 'btn', type: 'button', text: 'Rename', 'aria-label': 'Rename ' + entry.tag, onclick: () => rule({ tag: entry.tag, becomes: name.value }, 'Renamed everywhere.') }),
          el('button', { class: 'btn', type: 'button', text: 'Hide', 'aria-label': 'Hide ' + entry.tag,
              onclick: () => rule({ tag: entry.tag, hide: true }, '"' + entry.tag + '" is hidden everywhere.') }))));
    }));
  }
  const lookNow = (again_) => api('POST', '/api/admin/tags/lookup',
      { again: again_ }).then((reply) => { toast(reply.started ? 'Looking up tags. This page shows progress when you reopen it.' : 'A lookup is already running.'); again(); }).catch(oops);
  setMain(back('#/admin/books', 'Back to books admin'),
    pageHead('Tidy tags',
        'Tags that come from book files and from catalogues. Hide the ones that say nothing, or rename two spellings to the same name to merge them. Merge tags, below, merges family tags as well. Personal tags are not listed: change those on the book itself.'),
    el('section', { class: 'panel cut' }, head('Community tags from catalogues'),
      el('p', { class: 'small', role: 'status', text: lookupLine(tags.lookups) }),
      tags.lookups.on ? el('div', { class: 'row' },
        el('button', { class: 'btn', type: 'button', disabled: tags.lookups.running, text: tags.lookups.running ? 'Looking up…' : 'Look up waiting books now', onclick: () => lookNow(false) }),
        el('button', { class: 'btn', type: 'button', disabled: tags.lookups.running, text: 'Ask again about unrecognised books', onclick: () => lookNow(true) })) : null,
      el('p', { class: 'small dim',
          text: 'A book is only recognised when its title and author match a catalogue entry. Books whose details are a file name or a user name will not be; correct them in Calibre-Web, then ask again.' })),
    tagMergePanel(),
    tagNestPanel(),
    el('section', { class: 'panel cut' }, head(plural(shown.length, 'tag') + ' in use', field('Filter tags', filterBox)),
      shown.length ? el('div', { class: 'scroll' }, el('table', { style: 'min-width:760px' },
        el('thead', {}, el('tr', {}, ['Tag', 'Books', 'From', 'Change'].map((heading, index) => el('th', { scope: 'col', class: index === 1 ? 'num' : '', text: heading })))), body))
        : el('p', { class: 'small dim', text: 'No tags yet.' })),
    changed.length ? el('section', { class: 'panel cut' }, head('Hidden or renamed by you'),
      el('div', { class: 'list' }, changed.map((entry) => el('div', { class: 'row between' },
        el('span', {}, el('strong', { text: entry.tag }), el('span', { class: 'small dim',
            text: entry.becomes ? ' shown as "' + entry.becomes + '"' : ' hidden' }), el('span', { class: 'small dim', text: ' · ' + plural(entry.count,
            'book') })),
        el('button', { class: 'btn', type: 'button', text: 'Put back', 'aria-label': 'Put back ' + entry.tag, onclick: () => rule({ tag: entry.tag, restore: true }, 'Put back.') }))))) : null,
    tags.removed.length ? el('section', { class: 'panel cut' }, head('Removed as clutter'),
      el('p', { class: 'small dim',
          text: 'Found in book files and left out automatically: ISBNs, dates, publishers, adverts and whole sentences. They are still in the files; Family Hub just does not show them.' }),
      el('div', { class: 'row gap8' }, tags.removed.map((entry) => el('span', { class: 'tag small dim', text: entry.tag.length > 60 ? entry.tag.slice(0, 60) + '…' : entry.tag })))) : null);
  draw();
}

// ---- Merging tags, on the Tidy tags page: two spellings become one, across family, book file and community
// tags. Suggestions Family Hub spotted itself, a picker for merging by hand, and the merges made so far.
function mergeSentence(preview) {
  if (!preview.books) return 'No books carry ' + (preview.names.length === 1 ? 'that tag' : 'those tags') + ' yet. Merging still catches it in future.';
  return plural(preview.books, 'book') + ' change' + (preview.books === 1 ? 's' : '')
    + (preview.already ? '; ' + preview.already + ' already ' + (preview.already === 1 ? 'has' : 'have') + ' "' + preview.keep + '"' : '') + '.';
}

function tagMergePanel() {
  const box = el('section', { class: 'panel cut' }, head('Merge tags'), el('p', { class: 'small dim', text: 'Loading…' }));
  const reload = () => api('GET', '/api/admin/tags/merges').then(draw).catch(oops);

  // ask first, saying what will happen, then merge
  async function merge(names, keep) {
    const preview = await api('POST', '/api/admin/tags/merge/preview', { tags: names, keep });
    if (!preview.names.length) { toast('Choose at least one other tag to merge into "' + keep + '".', true); return; }
    const from = preview.names.map((name) => '"' + name + '"').join(', ');
    if (!window.confirm('Merge ' + from + ' into "' + keep + '"? ' + mergeSentence(preview))) return;
    await api('POST', '/api/admin/tags/merge', { tags: preview.names, keep });
    toast('Merged into "' + keep + '".');
    pageTags().catch(oops);  // the tag list below changes too
  }

  // a tag to click: solid when it is the name kept
  const namePick = (entry, kept, onPick) => el('button', { class: 'tagpick', type: 'button', 'aria-pressed': String(kept),
      title: kept ? 'The name kept' : 'Keep this name instead', onclick: onPick },
    kept ? el('span', { class: 'sg', 'aria-hidden': 'true', text: '✓' }) : null, el('span', { class: 'val', text: entry.tag }),
    el('span', { class: 'n', text: entry.books }));

  function suggestion(item) {
    let keep = item.keep;
    const line = el('div', { class: 'col gap8' });
    function drawOne() {
      const others = item.names.map((entry) => entry.tag).filter((tag) => tag !== keep);
      fill(line,
        el('div', { class: 'row gap8' }, item.names.map((entry) => namePick(entry, entry.tag === keep, () => { keep = entry.tag; drawOne(); }))),
        el('div', { class: 'row gap8' },
          el('button', { class: 'btn', type: 'button', text: 'Merge into "' + keep + '"', onclick: () => merge(others, keep).catch(oops) }),
          el('button', { class: 'btn', type: 'button', text: 'Not the same',
              onclick: () => api('POST', '/api/admin/tags/merge/dismiss', { tags: item.names.map((entry) => entry.tag) })
                .then(() => { toast('Will not suggest those again.'); reload(); }).catch(oops) })));
    }
    drawOne();
    return line;
  }

  function byHand(tags) {
    const known = new Map(tags.map((entry) => [entry.tag.toLowerCase(), entry]));
    const listId = 'mergetags' + Math.random().toString(36).slice(2, 7);
    const options = el('datalist', { id: listId }, tags.map((entry) => el('option', { value: entry.tag })));
    const adder = el('input', { type: 'search', list: listId, maxlength: 60, placeholder: 'Start typing a tag', autocomplete: 'off' });
    const keepBox = el('input', { type: 'text', maxlength: 60, placeholder: 'Or type a new name' });
    const picked = [];
    let keep = '';
    const chips = el('div', { class: 'row gap8' });
    const status = el('p', { class: 'small', role: 'status' });
    const go = el('button', { class: 'btn', type: 'button', disabled: true, text: 'Merge', onclick: () => merge(picked, keepName()).catch(oops) });
    const keepName = () => keepBox.value.trim() || keep;
    const preview = debounce(() => {
      const name = keepName();
      const from = picked.filter((tag) => tag !== name);
      if (!name || !from.length) { status.textContent = picked.length ? 'Click the name to keep, or type a new one.' : ''; go.disabled = true; return; }
      api('POST', '/api/admin/tags/merge/preview', { tags: from, keep: name }).then((reply) => {
        status.textContent = mergeSentence(reply) + (reply.new_name ? ' "' + name + '" is a new name.' : '');
        go.textContent = 'Merge into "' + name + '"';
        go.disabled = false;
      }).catch(oops);
    }, 250);
    function drawPicked() {
      fill(chips, picked.map((tag) => {
        const entry = known.get(tag.toLowerCase()) || { tag, books: 0 };
        return el('span', { class: 'row', style: 'gap:2px' },
          namePick(entry, !keepBox.value.trim() && tag === keep, () => { keep = tag; keepBox.value = ''; drawPicked(); }),
          el('button', { class: 'btn', type: 'button', text: '×', 'aria-label': 'Take ' + tag + ' out',
              onclick: () => { picked.splice(picked.indexOf(tag), 1); if (keep === tag) keep = picked[0] || ''; drawPicked(); } }));
      }));
      preview();
    }
    function add() {
      const typed = adder.value.trim();
      const tag = (known.get(typed.toLowerCase()) || { tag: typed }).tag;
      if (!tag || picked.some((other) => other === tag)) { adder.value = ''; return; }
      picked.push(tag);
      // the most used one is kept unless you say otherwise
      if (!keep || (known.get(tag.toLowerCase()) || { books: 0 }).books > (known.get(keep.toLowerCase()) || { books: 0 }).books) keep = tag;
      adder.value = '';
      drawPicked();
    }
    adder.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); add(); } });
    adder.addEventListener('change', () => { if (known.has(adder.value.trim().toLowerCase())) add(); });  // picked from the list
    keepBox.addEventListener('input', () => drawPicked());
    return el('div', { class: 'col gap8' },
      el('h3', { class: 'small', style: 'margin:8px 0 0', text: 'Merge by hand' }),
      el('div', { class: 'row end' }, field('Tags to merge', adder), options,
        el('button', { class: 'btn', type: 'button', text: 'Add', onclick: add }), field('Name to keep', keepBox)),
      chips, status, el('div', { class: 'row' }, go));
  }

  function draw(data) {
    fill(box, head('Merge tags'),
      el('p', { class: 'small dim', text: 'Two spellings of one tag become one. Family tags are changed on every book, and any added later under the old name get the new one. '
        + 'Book file and community tags are shown under the new name; the files are not changed. Personal tags are never touched.' }),
      data.suggestions.length ? [el('h3', { class: 'small', style: 'margin:8px 0 0', text: 'These look like the same tag' }),
        el('p', { class: 'small dim', text: 'Spellings that differ only by capitals, a plural, a hyphen or "&". The number is how many books carry each. The ticked one is kept: click another to keep that instead.' }),
        el('div', { class: 'list' }, data.suggestions.slice(0, 30).map(suggestion))] : null,
      byHand(data.tags),
      data.merges.length ? [el('h3', { class: 'small', style: 'margin:8px 0 0', text: 'Merged so far' }),
        el('p', { class: 'small dim', text: 'Undo puts the old name back on the family tags that had it when you merged. Tags added since then keep the new name, and nesting stays as it is now.' }),
        el('div', { class: 'list' }, data.merges.map((entry) => el('div', { class: 'row between' },
          el('span', {}, el('strong', { text: entry.tag }), el('span', { class: 'small dim', text: ' becomes "' + entry.becomes + '" · merged ' + (entry.created || '').slice(0, 10)
            + (entry.restorable ? '' : ' · undo stops the renaming only') })),
          el('button', { class: 'btn', type: 'button', text: 'Undo', 'aria-label': 'Undo merging ' + entry.tag,
              onclick: () => api('POST', '/api/admin/tags/merge/undo', { tag: entry.tag })
                .then((reply) => { toast(reply.restored ? '"' + entry.tag + '" is back on ' + plural(reply.restored, 'book') + '.' : 'Stopped merging "' + entry.tag + '".'); pageTags().catch(oops); })
                .catch(oops) }))))] : null);
  }
  reload();
  return box;
}

function fileSize(bytes) { return bytes > 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB'; }

// Books whose title is a file name or whose author is a user name, with a link to put them right.
function detailsCard() {
  const box = el('section', { class: 'panel cut', hidden: true });
  api('GET', '/api/admin/book-details').then((data) => {
    if (!data.books.length) return;
    fill(box, head('Books without real details', el('a', { class: 'btn', href: '#/details', text: 'Fix them' })),
      el('p', { class: 'small dim', text: plural(data.books.length, 'book') + ' in the library ' + (data.books.length === 1 ? 'has' : 'have') + ' a file name for a title or no real author, '
        + 'such as "' + data.books[0].title + '" by ' + (data.books[0].author || 'nobody') + '. Family Hub can look for the real details in the files and online catalogues.' }));
    box.hidden = false;
  }).catch(() => {});
  return box;
}

// One book at a time: what its files say, matches from Open Library and Google Books, and a form to type the
// details in. The library manager makes the change, after a copy of the library's database.
async function pageDetails() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const data = await api('GET', '/api/admin/book-details');
  const gen = routeGen;
  const again = () => pageDetails().catch(oops);
  function bookRow(book) {
    const area = el('div', { class: 'stack gap10' });
    const find = el('button', { class: 'btn sm pri', type: 'button', text: 'Find details', onclick: () => open() });
    async function open() {
      find.disabled = true;
      fill(area, el('p', { class: 'small dim', role: 'status', text: 'Reading the file and asking the catalogues…' }));
      let result;
      try { result = await api('GET', '/api/admin/book-details/' + book.id); } catch (err) { if (!(err instanceof Stale)) fill(area, el('p',
          { class: 'small bad', text: err.message })); find.disabled = false; return; }
      if (gen !== routeGen) return;
      const first = result.candidates[0], clue = result.clues.titles[0] || {};
      const title = el('input', { type: 'text', value: first ? first.title : clue.title || '' });
      const authors = el('input', { type: 'text', value: first ? first.authors.join(' & ') : clue.author || '' });
      let picked = first || null;
      const save = async (ev) => {
        ev.preventDefault();
        const btn = ev.target.querySelector('button[type=submit]');
        btn.disabled = true;
        const same = picked && picked.title === title.value && picked.authors.join(' & ') === authors.value;
        try {
          const done = await api('POST', '/api/admin/book-details/' + book.id, { title: title.value, authors: authors.value,
            synopsis: same ? picked.synopsis : '', publisher: same ? picked.publisher : '', cover: same ? picked.cover : '' });
          toast(done.message);
          row.remove();
        } catch (err) { if (!(err instanceof Stale)) oops(err); btn.disabled = false; }
      };
      const clues = [...result.clues.isbns.map((found) => 'ISBN ' + found.isbn + ' ' + found.where),
          ...result.clues.titles.map((found) => '"' + found.title + '"' + (found.author ? ' by ' + found.author : '') + ' from ' + found.where)];
      fill(area,
        el('p', { class: 'small dim', text: clues.length ? 'Clues: ' + clues.join(' · ') : 'The file gave no clues; type the details in below.' }),
        result.errors.length ? el('p', { class: 'small bad', text: 'A catalogue did not answer: ' + result.errors[0] }) : null,
        result.candidates.length ? el('div', { class: 'list' }, result.candidates.map((candidate) => el('div', { class: 'row', style: 'gap:12px;align-items:flex-start' },
          candidate.cover ? el('img', { class: 'thumb', src: candidate.cover, alt: '', loading: 'lazy' }) : el('span', { class: 'thumb' }),
          el('div', { class: 'stack', style: 'gap:2px;flex:1;min-width:0' }, el('strong', { class: 'book', text: candidate.title }),
            el('span', { class: 'small', text: [candidate.authors.join(' & '), candidate.year, candidate.publisher].filter(Boolean).join(' · ') }),
            el('span', { class: 'small dim', text: candidate.why })),
          el('button', { class: 'btn sm', type: 'button', text: 'Use this', 'aria-label': 'Use this: ' + candidate.title,
            onclick: () => { picked = candidate; title.value = candidate.title; authors.value = candidate.authors.join(' & '); title.focus(); } }))))
          : el('p', { class: 'small dim', text: 'No catalogue knew it. Type the details in, or leave it as it is.' }),
        el('form', { class: 'row end gap8', onsubmit: save }, field('Title', title), field('Authors (separate with &)', authors),
          el('button', { class: 'btn pri', type: 'submit', text: 'Save to the library' })));
    }
    const row = el('div', { class: 'stack gap8' },
      el('div', { class: 'row between gap8' },
        el('div', { class: 'stack', style: 'gap:2px;min-width:0;flex:1 1 240px' }, el('strong', { class: 'book', text: book.title }),
          el('span', { class: 'small dim book', text: (book.author || 'no author') + ' · ' + (book.formats.join(', ') || 'no file') + ' · ' + book.problems.join(', ').toLowerCase() })),
        el('div', { class: 'row gap6' }, find,
          el('a', { class: 'btn sm', href: '#/book/' + book.id, 'aria-label': 'Open ' + book.title, text: 'Open' }),
          el('button', { class: 'btn sm', type: 'button', text: 'It is right', 'aria-label': 'It is right: ' + book.title,
            onclick: () => api('POST', '/api/admin/book-details/' + book.id, { leave: true }).then(() => {
              toast('Marked as right. It is left as it is.');
              // the row goes, so focus moves on to the next book rather than falling back to the top of the page
              const next = row.nextElementSibling && row.nextElementSibling.querySelector('button, a[href]');
              row.remove();
              if (next) next.focus({ preventScroll: true });
            }).catch(oops) }))),
      area);
    return row;
  }
  const PAGE = 40;
  let shown = PAGE;
  const list = el('div', { class: 'list' });
  const more = el('button', { class: 'btn', type: 'button', text: 'Show more', onclick: () => { shown += PAGE; draw(); } });
  function draw() {
    fill(list, data.books.slice(0, shown).map(bookRow));
    more.hidden = shown >= data.books.length;
  }
  draw();
  setMain(back('#/admin/books', 'Back to books admin'),
    pageHead('Books without real details', 'Titles that are file names and authors that are user names. Find details reads the file for an ISBN and its own title, then asks Open Library '
      + '(and Google Books, when its key is saved). Saving changes the title and author in the library through Calibre-Web, after a copy of the library database. '
      + 'Reading progress, tags and ratings stay with the book. Afterwards, Copies of the same book may find more spare copies.'),
    data.blocked ? el('p', { class: 'bad' }, data.blocked + ' ', el('a', { href: '#/admin', text: 'Open Admin, Hub' })) : null,
    reviewPanel(),
    el('section', { class: 'panel cut' }, head(plural(data.books.length, 'book') + ' to look at', el('button', { class: 'btn sm', type: 'button', text: 'Look again', onclick: again })),
      data.books.length ? list : el('p', { class: 'small dim', text: 'Every book has a real title and author.' }), more));
}

// ---------------------------------------------------------------- the review queue
// The sorter's suggestions (review.py), in tabs by media. Each book shows what confirming would change in the
// library; Edit opens the details as boxes. Confirming writes through the library manager and saves a learned
// rule, so the next books like it sort themselves. Skip only hides a book until the page is opened again.

const REVIEW_FIELDS = { title: 'Title', authors: 'Author', series: 'Series', series_index: 'Number', publisher: 'Publisher', tags: 'Tags to add', language: 'Language' };
let reviewTab = null;
const reviewSkipped = new Set();

function reviewValue(value) {
  if (Array.isArray(value)) return value.join(' & ');
  return value === null || value === undefined || value === '' ? '' : String(value);
}

// A look inside one library entry, its own file not its copy's (peek.py): a PDF's first pages in the browser's
// own viewer, an EPUB's opening text or a comic's first pictures, all scrollable, with more pages on request.
// marker (optional): { labels: {}, pick(field, answer) }. A PDF then gets "Mark on the page": its pages as pictures
// to drag a box round the title or the author. The box's text goes in the field, and the box is kept in
// marker.labels so confirming teaches the rule where to read the next book like it.
const MARK_NAMES = { title: 'Title', authors: 'Author' };

function markPages(base, total, marker, back) {
  let shown = Math.min(3, total);
  const strip = el('div', { class: 'pk-page pk-pictures pk-mark', tabindex: '0' });
  const line = el('span', { class: 'small dim' });
  const more = el('button', { class: 'btn sm', type: 'button', text: 'Show 5 more pages', onclick: () => { shown = Math.min(shown + 5, total, 30); draw(); } });
  const pages = [];
  const pct = (n) => (n * 100).toFixed(2) + '%';
  const place = (node, box) => Object.assign(node.style, { left: pct(box[0]), top: pct(box[1]), width: pct(box[2] - box[0]), height: pct(box[3] - box[1]) });

  // The kept boxes, each with its name, on whichever page it is on.
  function drawMarks() {
    pages.forEach((wrap, n) => {
      wrap.querySelectorAll('.pk-box.kept').forEach((node) => node.remove());
      Object.entries(marker.labels).forEach(([field, mark]) => {
        if (mark.page !== n) return;
        const node = el('div', { class: 'pk-box kept ' + field }, el('span', { text: MARK_NAMES[field] }));
        place(node, mark.box);
        wrap.append(node);
      });
    });
  }

  function addPage(n) {
    const wrap = el('div', { class: 'pk-sheet' }, el('img', { src: base + '/page/' + n + '.png', alt: 'Page ' + (n + 1), draggable: 'false' }));
    let start = null;
    let ghost = null;
    const at = (event) => {
      const rect = wrap.getBoundingClientRect();
      return [Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height))];
    };
    wrap.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || event.target.closest('.pk-ask')) return;
      event.preventDefault();
      wrap.setPointerCapture(event.pointerId);
      strip.querySelectorAll('.pk-box.new, .pk-ask').forEach((node) => node.remove());
      start = at(event);
      ghost = el('div', { class: 'pk-box new' });
      place(ghost, [start[0], start[1], start[0], start[1]]);
      wrap.append(ghost);
    });
    wrap.addEventListener('pointermove', (event) => {
      if (!start) return;
      const now = at(event);
      place(ghost, [Math.min(start[0], now[0]), Math.min(start[1], now[1]), Math.max(start[0], now[0]), Math.max(start[1], now[1])]);
    });
    wrap.addEventListener('pointerup', (event) => {
      if (!start) return;
      const now = at(event);
      const box = [Math.min(start[0], now[0]), Math.min(start[1], now[1]), Math.max(start[0], now[0]), Math.max(start[1], now[1])];
      start = null;
      if (box[2] - box[0] < 0.01 || box[3] - box[1] < 0.005) { ghost.remove(); return; }
      // What is this box? Asked right under it.
      const ask = el('div', { class: 'pk-ask', role: 'group', 'aria-label': 'What did you mark?' },
        ...Object.entries(MARK_NAMES).map(([field, name]) => el('button', { class: 'btn sm', type: 'button', text: 'This is the ' + name.toLowerCase(),
          onclick: () => use(field) })),
        el('button', { class: 'btn sm', type: 'button', text: 'Cancel', onclick: () => { ghost.remove(); ask.remove(); } }));
      Object.assign(ask.style, { left: pct(Math.min(box[0], 0.55)), top: pct(Math.min(box[3], 0.94)) });
      wrap.append(ask);
      ask.querySelector('button').focus();
      const use = async (field) => {
        ask.querySelectorAll('button').forEach((btn) => { btn.disabled = true; });
        try {
          const answer = await api('POST', base + '/box', { page: n, box });
          if (!answer.text) { toast('There is no text in that box. A scanned page is only a picture, so type it in instead.', true); return; }
          marker.labels[field] = { page: n, box: answer.box };
          marker.pick(field, answer);
          toast(MARK_NAMES[field] + ': ' + (field === 'title' ? answer.title : answer.authors.join(' & ')));
        } catch (err) { if (!(err instanceof Stale)) oops(err); } finally { ghost.remove(); ask.remove(); drawMarks(); }
      };
    });
    pages.push(wrap);
    strip.append(wrap);
  }
  function draw() {
    while (pages.length < shown) addPage(pages.length);
    line.textContent = 'Pages 1–' + shown + ' of ' + total.toLocaleString() + '. Drag a box round the title or the author.';
    more.hidden = shown >= Math.min(total, 30);
    drawMarks();
  }
  draw();
  return [strip, el('div', { class: 'row between gap8' }, line, el('div', { class: 'row gap6' }, more,
    el('button', { class: 'btn sm', type: 'button', text: 'Back to reading', onclick: back })))];
}

function peekPane(bookId, marker) {
  const pane = el('div', { class: 'pk', role: 'region', 'aria-label': 'Inside the book' }, el('p', { class: 'small dim', text: 'Opening the book…' }));
  const base = '/api/admin/books/' + bookId + '/peek';
  const moreBtn = (label, onclick) => el('button', { class: 'btn sm', type: 'button', text: label, onclick });
  api('GET', base).then((info) => {
    if (info.kind === 'pdf') {
      let pages = Math.min(3, info.pages);
      const frame = el('iframe', { class: 'pk-page', title: 'The first pages of the book' });
      const line = el('span', { class: 'small dim' });
      const more = moreBtn('', () => { pages = Math.min(pages + 10, info.pages, 30); draw(); });
      const draw = () => {
        frame.src = base + '.pdf?pages=' + pages + '#navpanes=0&view=FitH';
        line.textContent = pages >= info.pages ? 'All ' + plural(info.pages, 'page') : 'Pages 1–' + pages + ' of ' + info.pages.toLocaleString();
        more.hidden = pages >= Math.min(info.pages, 30);
        more.textContent = 'Show ' + (Math.min(pages + 10, info.pages, 30) - pages) + ' more pages';
      };
      draw();
      // Reading uses the browser's own PDF viewer; marking needs the pages as pictures to draw on.
      const reading = [frame, el('div', { class: 'row between gap8' }, line, el('div', { class: 'row gap6' }, more,
        marker ? el('button', { class: 'btn sm pri', type: 'button', text: 'Mark title or author on the page',
          onclick: () => fill(pane, ...markPages(base, info.pages, marker, () => fill(pane, ...reading))) }) : null))];
      fill(pane, ...reading);
    } else if (info.kind === 'text') {
      let shown = 40;
      const text = el('div', { class: 'pk-page pk-text', tabindex: '0' });
      const more = moreBtn('Show more of the text', () => { shown += 60; draw(); });
      const draw = () => { fill(text, info.paragraphs.slice(0, shown).map((words) => el('p', { text: words }))); more.hidden = shown >= info.paragraphs.length; };
      draw();
      fill(pane, text, el('div', { class: 'row between gap8' }, el('span', { class: 'small dim', text: 'The opening of the ' + info.format }), more));
    } else if (info.kind === 'pictures') {
      let shown = Math.min(3, info.pages);
      const strip = el('div', { class: 'pk-page pk-pictures', tabindex: '0' });
      const line = el('span', { class: 'small dim' });
      const more = moreBtn('Show 10 more pages', () => { shown = Math.min(shown + 10, info.pages); draw(); });
      const draw = () => {
        while (strip.children.length < shown) {
          const n = strip.children.length;
          strip.append(el('img', { src: base + '/picture/' + n, alt: 'Page ' + (n + 1), loading: 'lazy' }));
        }
        line.textContent = 'Pages 1–' + shown + ' of ' + info.pages.toLocaleString();
        more.hidden = shown >= info.pages;
      };
      draw();
      fill(pane, strip, el('div', { class: 'row between gap8' }, line, more));
    } else {
      fill(pane, el('div', { class: 'pk-page pk-none' }, el('p', { class: 'small dim', text: info.reason })));
    }
  }).catch((err) => { if (!(err instanceof Stale)) fill(pane, el('p', { class: 'small bad', text: err.message })); });
  return pane;
}

// "Edit details" for one book from anywhere: the same editor as the queue, with the book open beside it.
async function pageFix(bookId) {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  setMain(back('#/book/' + bookId, 'Back to the book'),
    pageHead('Edit details', 'Look inside the book and put its details right. Saving changes the library entry through Calibre-Web, after a copy of the '
      + 'library database, and Family Hub remembers how books like it are sorted.'),
    reviewPanel(Number(bookId)));
}

function reviewPanel(only) {
  const box = el('section', { class: 'panel cut' }, head('Sorting suggestions'), el('p', { class: 'small dim', role: 'status', text: 'Reading the sorter’s suggestions…' }));
  const gen = routeGen;
  const PAGE = 20;
  let data = null;
  let shown = PAGE;
  const jobArea = el('div', { class: 'stack', style: 'gap:6px', 'aria-live': 'polite' });
  const list = el('div', { class: 'list' });
  const tabRow = el('div', { class: 'tabs', role: 'group', 'aria-label': 'Kind of book' });
  const readyArea = el('div', { class: 'stack' });
  const more = el('button', { class: 'btn', type: 'button', text: 'Show more', onclick: () => { shown += PAGE; load(); } });
  // One tab at a time: the queue can be a thousand books long.
  // only: one book's id, for Edit details. Otherwise one tab at a time: the queue can be a thousand books long.
  const load = () => (only ? api('GET', '/api/admin/review/book/' + only).then((answer) => Object.assign(answer, { items: [answer.item], ready: [], more: 0 }))
    : api('GET', '/api/admin/review?' + qs({ tab: reviewTab || '', limit: shown }))).then((answer) => { data = answer; if (!only) reviewTab = answer.tab; draw(); watch(); })
    .catch((err) => { if (!(err instanceof Stale)) fill(box, head('Sorting suggestions'), el('p', { class: 'small bad', text: err.message })); });

  // A batch or the re-sort is running: show how far it has got, and read the queue again when it ends.
  function watch() {
    drawJob();
    if (!data.job.running) return;
    const timer = setInterval(async () => {
      if (gen !== routeGen) { clearInterval(timer); return; }
      let job;
      try { job = await api('GET', '/api/admin/review/job'); } catch (err) { clearInterval(timer); return; }
      data.job = job;
      drawJob();
      if (job.running) return;
      clearInterval(timer);
      if (job.problems.length) toast(job.problems[0], true);
      else toast(job.what === 'resort' ? 'The whole library is sorted again.' : plural(job.confirmed || 0, 'book') + ' written to the library.');
      load();
    }, 2000);
  }
  function drawJob() {
    const job = data.job;
    if (!job.running) { fill(jobArea); return; }
    const words = job.what === 'resort' ? (job.total ? 'Sorting the library again: ' + job.done.toLocaleString() + ' of ' + job.total.toLocaleString() + ' books'
      : 'Getting ready to sort the library again…') : 'Writing ' + job.done + ' of ' + plural(job.total, 'book') + ' to the library…';
    fill(jobArea, el('p', { class: 'small', role: 'status', text: words }), bar(job.total ? 100 * job.done / job.total : 2, words));
  }
  function resort(event) {
    if (!window.confirm('Sort every book in the library again? Books whose kind someone chose are left as they are. Reading the first pages of every PDF takes a while; '
      + 'the suggestions fill in when it is done.')) return;
    event.currentTarget.disabled = true;
    api('POST', '/api/admin/review/resort').then((answer) => { data.job = answer.job; watch(); }).catch(oops);
  }

  // Take answered books off the page at once, then read the tab again (a new rule may have sorted others).
  function gone(ids) {
    if (only) { location.hash = '#/book/' + only; return; }
    data.items = data.items.filter((item) => !ids.includes(item.book_id));
    drawList();
    load();
  }
  function confirmAll(item, fields) {
    if (!window.confirm('Confirm all ' + item.batch_size + ' books like this? Each keeps its own title and number; the game, edition, role, kind, publisher, '
      + 'language and tags you see here go on all of them. The library database is copied first.')) return;
    api('POST', '/api/admin/review/' + item.book_id, { action: 'confirm_all', batch: item.batch, fields, new_game: item.new_game })
      .then((answer) => { data.job = answer.job; watch(); }).catch(oops);
  }

  function itemCard(item, editing) {
    const card = el('article', { class: 'rv stack', 'aria-label': item.title });
    const blocked = Boolean(data.blocked);
    const batchWords = item.batch_size > 1 ? (item.batch_folder ? 'arrived in ' + item.batch_folder : 'are named like “' + item.batch_pattern.replace(/^\^|\$$/g, '')
      .replace(/\\d\+/g, '#').replace(/\\(.)/g, '$1') + '”') : '';
    const confirmOne = (fields, btn) => {
      btn.disabled = true;
      api('POST', '/api/admin/review/' + item.book_id, { action: 'confirm', fields, new_game: item.new_game }).then((answer) => {
        toast(answer.message + (answer.problems.length ? ' ' + answer.problems[0] : ''));
        gone([item.book_id]);
      }).catch((err) => { btn.disabled = false; oops(err); });
    };
    const reject = (btn) => {
      btn.disabled = true;
      api('POST', '/api/admin/review/' + item.book_id, { action: 'reject' }).then((answer) => { toast(answer.message); gone([item.book_id]); })
        .catch((err) => { btn.disabled = false; oops(err); });
    };
    const skip = () => { reviewSkipped.add(item.book_id); gone([item.book_id]); };
    // A greyed-out Confirm says why, right where it is pressed (the panel's own notice can be off screen).
    const whyOff = () => (blocked ? el('p', { class: 'small bad' }, 'Confirm is off: ' + data.blocked + ' ', el('a', { href: '#/admin', text: 'Open Admin' })) : null);

    // What confirming changes: the new value large, the old one under it.
    function summary() {
      const sorted = [item.suggest.kind, item.suggest.role, [item.suggest.game, item.suggest.edition].filter(Boolean).join(' › ')].filter(Boolean).join(', ');
      fill(card,
        el('div', { class: 'row between gap8' },
          el('div', { class: 'stack', style: 'gap:2px;min-width:0;flex:1 1 260px' }, el('strong', { class: 'book', text: item.title }),
            el('span', { class: 'small dim book', text: (item.author || 'no author') + ' · ' + (item.formats.join(', ') || 'no file') + (item.origin ? ' · from ' + item.origin : '') })),
          el('a', { class: 'btn sm', href: '#/book/' + item.book_id, 'aria-label': 'Open ' + item.title, text: 'Open' })),
        item.shares > 1 ? el('p', { class: 'small rv-note', text: 'Shares its title and author with ' + plural(item.shares - 1, 'other entry', 'other entries')
          + ', so they share one set of tags. Giving it its own title tells them apart.' }) : null,
        item.why.length ? el('ul', { class: 'small rv-why' }, item.why.map((line) => el('li', { text: line }))) : null,
        item.changes.length ? el('dl', { class: 'rv-changes' }, item.changes.map((change) => el('div', {},
          el('dt', { class: 'lab', text: REVIEW_FIELDS[change.field] || change.field }),
          el('dd', {}, el('span', { class: 'rv-to', text: reviewValue(change.to) }),
            change.field === 'tags' ? null : el('span', { class: 'small dim', text: change.now === null || change.now === '' ? 'now empty' : 'now ' + reviewValue(change.now) })))))
          : el('p', { class: 'small dim', text: 'Nothing in the library entry changes; confirming settles how Family Hub sorts it.' }),
        sorted ? el('p', { class: 'small dim', text: 'Sorted in Family Hub as ' + sorted + '.' }) : null,
        // Confirm and Edit lead; "Not right" (for good) is set apart from Skip (just for now), and the batch, the
        // widest action, sits on its own line under what it covers.
        el('div', { class: 'row gap6 rv-acts' },
          el('button', { class: 'btn sm pri', type: 'button', text: 'Confirm', 'aria-label': 'Confirm: ' + item.title, disabled: blocked,
            onclick: (event) => confirmOne(item.suggest, event.currentTarget) }),
          el('button', { class: 'btn sm', type: 'button', text: 'Edit', 'aria-label': 'Edit: ' + item.title, onclick: edit }),
          el('button', { class: 'linkish', type: 'button', text: 'Skip for now', 'aria-label': 'Skip for now: ' + item.title, onclick: skip }),
          el('button', { class: 'btn sm quiet-danger', type: 'button', text: 'Not right', 'aria-label': 'Not right: ' + item.title,
            title: 'Leave the book as it is and never suggest this for it again', onclick: (event) => reject(event.currentTarget) })),
        whyOff(),
        item.batch_size > 1 ? el('div', { class: 'row between gap8 rv-batch' },
          el('p', { class: 'small dim', text: plural(item.batch_size, 'book') + ' in this batch ' + batchWords + '. Confirming them all gives each the same sorting and keeps its own title.' }),
          el('button', { class: 'btn sm', type: 'button', disabled: blocked || data.job.running, text: 'Confirm all ' + item.batch_size,
            onclick: () => confirmAll(item, item.suggest) })) : null);
    }

    // The details as boxes, started from the suggestion.
    function edit() {
      const s = item.suggest;
      const box = (value) => el('input', { type: 'text', value: reviewValue(value) });
      const inputs = { title: box(s.title), authors: box(s.authors), series: box(s.series), series_index: el('input', { type: 'text', inputmode: 'decimal', value: reviewValue(s.series_index) }),
        publisher: box(s.publisher), game: box(s.game), edition: box(s.edition), tags: box(s.tags.join(', ')) };
      const kind = el('select', {}, data.kinds.map((name) => el('option', { value: name, text: name, selected: name === s.kind })));
      const role = el('select', {}, el('option', { value: '', text: 'None' }), data.roles.map((name) => el('option', { value: name, text: name, selected: name === s.role })));
      const language = el('select', {}, el('option', { value: '', text: 'Leave as it is' }),
        data.languages.map((lang) => el('option', { value: lang.code, text: lang.name, selected: lang.code === s.language })));
      const roleField = field('Role', role);
      const showRole = () => { roleField.hidden = kind.value !== 'Tabletop'; };
      kind.addEventListener('change', showRole);
      showRole();
      const values = () => ({ title: inputs.title.value, authors: inputs.authors.value, series: inputs.series.value, series_index: inputs.series_index.value,
        publisher: inputs.publisher.value, game: inputs.game.value, edition: inputs.edition.value, tags: inputs.tags.value,
        kind: kind.value, role: kind.value === 'Tabletop' ? role.value : '', language: language.value, labels: marker.labels });
      // Boxes drawn on the pages fill the boxes here, and confirming teaches the rule where to look.
      const marker = { labels: {}, pick: (field, answer) => {
        if (field === 'title') inputs.title.value = answer.title;
        else inputs.authors.value = answer.authors.join(' & ');
        markedNote.textContent = 'Marked on the page: ' + Object.keys(marker.labels).map((name) => MARK_NAMES[name].toLowerCase()).join(' and ')
          + '. Confirming teaches Family Hub to read the same places in books like this one.';
        markedNote.hidden = false;
      } };
      const markedNote = el('p', { class: 'small rv-note', hidden: true });
      const findWriters = el('button', { class: 'btn sm', type: 'button', text: 'Find the writers', onclick: async (event) => {
        const btn = event.currentTarget;
        btn.disabled = true;
        btn.textContent = 'Reading the credits…';
        try {
          const found = await api('GET', '/api/admin/review/' + item.book_id + '/credits');
          if (found.authors.length) { inputs.authors.value = found.authors.join(' & '); toast('Found ' + plural(found.authors.length, 'writer') + ' on the credits page.'); }
          else toast('The first pages name no writers. The publisher is a fine author for a game book.');
        } catch (err) { oops(err); }
        btn.textContent = 'Find the writers';
        btn.disabled = false;
      } });
      fill(card,
        el('strong', { class: 'book', text: item.title }),
        el('span', { class: 'small dim book', text: (item.formats.join(', ') || 'no file') + (item.origin ? ' · from ' + item.origin : '') }),
        el('div', { class: 'pk-split' }, peekPane(item.book_id, marker), el('form', { class: 'stack gap10', onsubmit: (event) => { event.preventDefault(); confirmOne(values(), event.submitter || event.target.querySelector('[type=submit]')); } },
          el('div', { class: 'rv-grid' },
            field('Title', inputs.title, 'wide'),
            field('Author (separate with &)', inputs.authors),
            item.formats.includes('PDF') ? el('div', { class: 'field', style: 'justify-content:flex-end' }, findWriters) : null,
            field('Series', inputs.series), field('Number in series', inputs.series_index), field('Publisher', inputs.publisher),
            field('Game', inputs.game), field('Edition', inputs.edition),
            field('Kind', kind), roleField, field('Language', language),
            field('Other tags (separate with commas)', inputs.tags, 'wide')),
          el('p', { class: 'small dim', text: 'The game, edition and role go on the library entry as tags, and become nested family tags here.' }),
          markedNote,
          el('div', { class: 'row gap6' },
            el('button', { class: 'btn sm pri', type: 'submit', text: 'Confirm', disabled: blocked }),
            only ? null : el('button', { class: 'linkish', type: 'button', text: item.media === 'unsorted' ? 'Skip for now' : 'Cancel', onclick: item.media === 'unsorted' ? skip : summary }),
            item.batch_size > 1 ? el('button', { class: 'btn sm rv-all', type: 'button', disabled: blocked || data.job.running, text: 'Confirm all ' + item.batch_size + ' like this',
              onclick: () => confirmAll(item, values()) }) : null),
          whyOff())));
      inputs.title.focus();
    }
    if (editing) edit(); else summary();
    return card;
  }

  // Books a learned rule already sorted: one card per rule, written to the library with one press.
  function readyCard(group) {
    const blocked = Boolean(data.blocked) || data.job.running;
    const books = el('div', { class: 'list' }, group.items.map((item) => {
      const row = el('div', { class: 'row between gap8' },
        el('div', { class: 'stack', style: 'gap:2px;min-width:0;flex:1 1 260px' }, el('strong', { class: 'book', text: item.title }),
          el('span', { class: 'small dim', text: item.changes.map((change) => (REVIEW_FIELDS[change.field] || change.field) + ': ' + reviewValue(change.to)).join('; ') })),
        el('button', { class: 'btn sm', type: 'button', text: 'Edit', 'aria-label': 'Edit ' + item.title, onclick: () => row.replaceWith(itemCard(item, true)) }));
      return row;
    }));
    const write = (event) => {
      if (!window.confirm('Write ' + plural(group.count, 'book') + ' to the library? The library database is copied first.')) return;
      event.currentTarget.disabled = true;
      api('POST', '/api/admin/review/ready/' + group.rule).then((answer) => { data.job = answer.job; watch(); }).catch(oops);
    };
    return el('div', { class: 'rv stack rv-ready' },
      el('div', { class: 'row between gap8' },
        el('div', { class: 'stack', style: 'gap:2px;min-width:0;flex:1 1 320px' }, el('strong', { text: 'Ready to write: ' + plural(group.count, 'book') }),
          el('span', { class: 'small dim', text: group.words })),
        el('button', { class: 'btn sm pri', type: 'button', disabled: blocked, text: 'Write ' + plural(group.count, 'book') + ' to the library', onclick: write })),
      el('details', {}, el('summary', { class: 'small', text: 'See the books and what changes' }), books));
  }

  function drawList() {
    const items = data.items.filter((item) => !reviewSkipped.has(item.book_id));
    fill(readyArea, data.ready.map(readyCard));
    // Unsorted books have no suggestion worth reading first: the top one opens straight in the editor, and
    // saving or skipping it opens the next
    fill(list, items.map((item, i) => itemCard(item, only || (reviewTab === 'unsorted' && i === 0))));
    more.hidden = !data.more;
    list.hidden = !items.length;
    empty.hidden = Boolean(items.length || data.ready.length);
  }
  const empty = el('p', { class: 'small dim', text: 'Nothing waiting here. New books are sorted as they arrive; Sort the whole library again looks at every book once more.' });
  function draw() {
    if (only) {
      fill(box, head('Details'), data.blocked ? el('p', { class: 'small bad', text: data.blocked }) : null, list);
      drawList();
      return;
    }
    if (!data.tabs.some((tab) => tab.id === reviewTab)) reviewTab = (data.tabs.find((tab) => tab.count) || data.tabs[0]).id;
    fill(tabRow, data.tabs.map((tab) => el('button', { type: 'button', 'aria-pressed': String(tab.id === reviewTab), onclick: () => { reviewTab = tab.id; shown = PAGE; load(); } },
      tab.label, el('span', { class: 'tt-n', text: tab.count.toLocaleString() }))));
    fill(box,
      head('Sorting suggestions',
        el('a', { class: 'btn sm', href: '#/rules', text: data.merges ? 'Learned rules (' + plural(data.merges, 'merge') + ' to look at)' : 'Learned rules' }),
        el('button', { class: 'btn sm', type: 'button', text: 'Sort the whole library again', disabled: data.job.running, onclick: resort })),
      el('p', { class: 'small dim', text: 'What the sorter would like to put right, from the folder a book arrived in, its name and its first pages. Books that share a name come first, '
        + 'then the biggest batches. Confirming changes the library entry through Calibre-Web, after a copy of the library database, and teaches Family Hub '
        + 'the books like it: they wait under Ready to write until you write them too. Not right leaves the book as it is.' }),
      data.blocked ? el('p', { class: 'small bad', text: data.blocked }) : null,
      jobArea, tabRow, readyArea, list, empty, more);
    drawJob();
    drawList();
  }
  load();
  return box;
}

// Learned rules: what confirming and rejecting taught the sorter, in plain words. Rules that say nearly the
// same thing are offered as one; nothing is combined until an admin says so.
async function pageRules() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const data = await api('GET', '/api/admin/review/rules');
  const again = () => pageRules().catch(oops);
  const examples = (titles, count) => titles.length ? el('details', { class: 'small' }, el('summary', { text: 'Which books (' + count.toLocaleString() + ')' }),
    el('p', { class: 'small dim', text: titles.join('; ') + (count > titles.length ? '; and ' + (count - titles.length) + ' more' : '') })) : el('p', { class: 'small dim', text: 'No book in the library matches it now.' });
  const merges = data.merges.map((offer) => el('div', { class: 'stack gap8' },
    el('strong', { text: offer.words }),
    el('p', { class: 'small dim', text: 'Would replace ' + plural(offer.replaces.length, 'rule') + ':' }),
    el('ul', { class: 'small rv-why' }, offer.replaces.map((words) => el('li', { text: words }))),
    offer.left_out.length ? el('p', { class: 'small', text: 'Each rule set its own ' + offer.left_out.join(' and ').replace(/_/g, ' ')
      + '; the combined rule leaves that to the sorter, which reads it from each book’s folder and name.' }) : null,
    offer.newly_count ? el('p', { class: 'small rv-note', text: 'It would also cover ' + plural(offer.newly_count, 'book') + ' no rule covers now: ' + offer.newly.join('; ')
      + (offer.newly_count > offer.newly.length ? '; and more' : '') + '.' }) : el('p', { class: 'small dim', text: 'It covers the same books as the rules it replaces.' }),
    examples(offer.examples, offer.matches),
    el('div', { class: 'row gap6' }, el('button', { class: 'btn sm pri', type: 'button', text: 'Combine them', onclick: (event) => {
      event.currentTarget.disabled = true;
      api('POST', '/api/admin/review/rules/merge', { ids: offer.ids }).then(() => { toast('Combined into one rule.'); again(); }).catch(oops);
    } }))));
  const rules = data.rules.map((rule) => el('div', { class: 'stack gap8' },
    el('div', { class: 'row between gap8' }, el('strong', { style: 'flex:1 1 320px;min-width:0', text: rule.words }),
      el('button', { class: 'btn sm danger', type: 'button', text: 'Delete', 'aria-label': 'Delete the rule: ' + rule.words, onclick: (event) => {
        if (!window.confirm('Delete this rule? Books it sorted keep their sort; new ones like them will wait in the queue again.')) return;
        event.currentTarget.disabled = true;
        api('DELETE', '/api/admin/review/rules/' + rule.id).then(() => { toast('Rule deleted.'); again(); }).catch(oops);
      } })),
    el('p', { class: 'small dim', text: 'From ' + plural(rule.confirmations, 'confirmation') + (rule.note ? ' (' + rule.note + ')' : '') + ', ' + niceDate(rule.created) + '.' }),
    examples(rule.examples, rule.matches)));
  setMain(back('#/details', 'Back to books without real details'),
    pageHead('Learned rules', 'Each time a suggestion is confirmed or turned down, Family Hub remembers it here, and books arriving the same way are sorted by themselves. '
      + 'Delete a rule to have books like it wait in the queue again.'),
    merges.length ? el('section', { class: 'panel cut focus' }, head(plural(merges.length, 'set') + ' of rules that could be one'), el('div', { class: 'list' }, merges)) : null,
    el('section', { class: 'panel cut' }, head(plural(rules.length, 'rule')),
      rules.length ? el('div', { class: 'list' }, rules) : el('p', { class: 'small dim', text: 'No rules yet. Confirm or turn down a suggestion on Books without real details and it appears here.' })));
}

function copiesCard(copies) {
  if (!copies.sets && !copies.apart) return null;
  return el('section', { class: 'panel cut' }, head('Copies of the same book', el('a', { class: 'btn', href: '#/copies', text: 'See the copies' })),
    el('p', { class: 'small dim',
        text: copies.hidden ? (copies.hidden === 1 ? '1 library entry is a second copy of a book and is' : copies.hidden + ' library entries are second copies of a book and are') + ' shown together with it, as one book. Nothing was removed from the library.' : 'Some library entries share a title and author but are not shown as one book.' }),
    el('div', { class: 'row' }, el('span', { class: 'tag', text: 'Books with copies · ' + copies.sets }),
      copies.spare ? el('span', { class: 'tag', text: 'Space the copies take · ' + fileSize(copies.spare) }) : null,
      copies.apart ? el('span', { class: 'tag', text: 'Same name, kept apart · ' + copies.apart }) : null));
}

// Books on people's Goodreads shelves that the library lacks. The one place that says whose they are.
function elsewhereCard(count) {
  if (!count) return null;
  const list = el('div', { class: 'scroll', hidden: true });
  const show = el('button', { class: 'btn', type: 'button', text: 'Show them', onclick: async () => {
    try {
      const data = await api('GET', '/api/admin/elsewhere');
      fill(list, el('table', { style: 'min-width:640px' },
        el('thead', {}, el('tr', {}, ['Book', 'Author', 'On whose Goodreads', ''].map((heading) => el('th', { scope: 'col', text: heading })))),
        el('tbody', {}, data.books.map((book) => el('tr', {}, el('th', { scope: 'row', class: 'plain', text: book.title }), el('td',
            { text: book.author }), el('td', { text: book.people.join(', ') }),
          el('td', {}, el('a', { class: 'btn', href: '#/discover/' + encodeURIComponent(findQuery(book)), 'aria-label': 'Find the EPUB of ' + book.title, text: 'Find the EPUB' })))))));
      list.hidden = false; show.hidden = true;
    } catch (err) { oops(err); }
  } });
  return el('section', { class: 'panel cut' }, head('On Kindle, not in the library', show),
    el('p', { class: 'small dim', text: plural(count,
        'book') + ' on the family\'s Goodreads shelves ' + (count === 1 ? 'is' : 'are') + ' not in the library. Everyone signed in sees them as "Available on Kindle"; only this list says whose they are. Guests do not see them.' }),
    list);
}

// Removing spare copies from the library for good. The one job where Family Hub asks the library manager to change the library.
const SPARE_SORTS = [
  ['identical', 'The very same file twice', 'The spare entry is removed. Nothing is set aside, because the kept file is identical byte for byte.', 'Remove'],
  ['duplicate', 'The same book, the same details',
      'File sizes are within 10% and neither entry says more than the other. The original is kept over a converted copy; the spare is set aside, then removed.',
      'Remove'],
  ['merge', 'The same book, one entry says more',
      'File sizes are within 10%. The entry with the better file is kept, any description, tags, series or publisher it lacks is copied onto it, and the spare is set aside, then removed.',
      'Merge'],
];
function spareEntry(book, extra) {
  const files = book.files.length ? book.files.map((file) => file.format + ' ' + fileSize(file.size)).join(' · ') : 'no book file';
  return el('div', { class: 'row between' },
    el('span', { class: 'wrap' }, book.kept ? el('span', { class: 'tag on', text: 'Keep' }) : el('span', { class: 'tag', text: 'Spare' }), ' ',
      book.files.length ? el('a', { href: '/api/books/' + book.id + '/download/' + book.files[0].format, text: files }) : el('span', { class: 'dim', text: files }),
      el('span', { class: 'small dim',
          text: (book.converted ? ' · converted copy' : '') + ' · added ' + niceDate(book.added) + ' · ' + (book.details.length ? book.details.join(', ') : 'no details') })),
    el('span', { class: 'row gap8' }, appState.cwa_url ? el('a', { class: 'small', href: appState.cwa_url + '/book/' + book.id,
        target: '_blank', rel: 'noopener', text: 'Open in Calibre-Web' }) : null, extra || null));
}
function spareCopiesCard(data, again) {
  const run = (body, sure) => { if (!window.confirm(sure)) return; api('POST', '/api/admin/duplicates', body).then(() => { toast('Started. This page follows it.'); again(); }).catch(oops); };
  const busy = data.job.running;
  if (busy) setTimeout(() => { if (location.hash.startsWith('#/copies')) again(); }, 3000);
  const of = (cls) => data.sets.filter((set) => set.cls === cls);
  const review = of('review');
  const setBlock = (group, button) => el('div', { class: 'stack gap6' },
    el('div', { class: 'row between' }, el('span', { class: 'wrap' }, el('strong', { text: group.title }), el('span', { class: 'small dim',
        text: ' · ' + (group.author || 'no author') + (group.copy.length ? ' · copies across: ' + group.copy.map((entry) => entry.label).join(', ') : '') })),
        button),
    group.why ? el('p', { class: 'small warn', text: group.why }) : null,
    el('div', { class: 'list' }, group.books.map((book) => spareEntry(book, group.cls === 'review' && !data.blocked && !busy ? el('button',
        { class: 'btn', type: 'button',
        'aria-label': 'Keep this entry of ' + group.title + ' (' + (book.files[0] ? book.files[0].format + ' ' + fileSize(book.files[0].size) : 'no file') + ')',
        text: 'Keep this one',
      onclick: () => run({ key: group.key, keep: book.id },
          'Keep this entry of "' + group.title + '" and remove the other ' + plural(group.books.length - 1, 'entry',
          'entries') + '? Their files are set aside in ' + data.set_aside + ' first.') }) : null))));
  return el('section', { class: 'panel cut' }, head('Remove spare copies from the library'),
    el('p', { class: 'small dim',
        text: 'This is the one place where Family Hub changes the library, and it does it by asking Calibre-Web. The library\'s database is copied first, every time. A spare is only deleted outright when the kept file is identical; otherwise its files are moved to ' + data.set_aside + ' for you to empty when you are satisfied.' }),
    data.blocked ? el('p', { class: 'warn', role: 'status' }, data.blocked + ' ', el('a', { href: '#/admin', text: 'Open Admin, Hub' })) : null,
    busy ? el('p', { class: 'ok', role: 'status', text: 'Working: ' + data.job.done + ' of ' + data.job.total + ' done…' }) : null,
    !busy && data.job.finished ? el('p', { class: 'small ' + (data.job.problems.length ? 'bad' : 'ok'), role: 'status',
        text: 'Last run: ' + data.job.done + ' of ' + data.job.total + ' done' + (data.job.problems.length ? ', ' + plural(data.job.problems.length,
        'problem') + '.' : ', no problems.') + (data.job.backup ? ' Database copy: ' + data.job.backup : '') }) : null,
    data.job.problems.length ? el('ul', { class: 'small bad' }, data.job.problems.map((problem) => el('li', { text: problem }))) : null,
    SPARE_SORTS.map(([cls, title, what, verb]) => {
      const sets = of(cls);
      return el('div', { class: 'stack gap8' },
        el('div', { class: 'row between' }, el('h3', { text: title + ' · ' + sets.length }),
          sets.length ? el('button', { class: 'btn pri', type: 'button', disabled: data.blocked || busy ? '' : null, text: verb + ' all ' + sets.length,
            onclick: () => run({ cls }, verb + ' all ' + plural(sets.length, 'set') + ' of this sort? ' + what) }) : null),
        el('p', { class: 'small dim', text: what }),
        sets.length ? el('details', {}, el('summary', { text: 'See the ' + plural(sets.length, 'set') + ', or do them one at a time' }),
          el('div', { class: 'list', style: 'margin-top:12px' }, sets.map((set) => setBlock(set, el('button', { class: 'btn',
              type: 'button', disabled: data.blocked || busy ? '' : null, 'aria-label': verb + ': ' + set.title, text: verb + ' this one',
            onclick: () => run({ key: set.key }, verb + ' the spare of "' + set.title + '"?') }))))) : null);
    }),
    el('div', { class: 'stack gap8' }, el('h3', { text: 'For you to decide · ' + review.length }),
      el('p', { class: 'small dim',
          text: 'The files differ in size by more than 10%, an entry has no file, the names are doubtful, or a detail would be lost. Open each file if you need to, then keep one. If they are different books, press "They are different books" and they leave this list.' }),
      review.length ? el('div', { class: 'list' }, review.map((group) => setBlock(group, el('button', { class: 'btn', type: 'button',
          'aria-label': 'They are different books: ' + group.title, text: 'They are different books',
        onclick: () => api('POST', '/api/admin/copies', { key: group.key,
            choice: 'apart' }).then(() => { toast('Kept as separate books.'); again(); }).catch(oops) })))) : el('p', { class: 'small dim',
            text: 'Nothing waiting.' })),
    data.log.length ? el('details', {}, el('summary', { text: 'What has been done (' + data.log.length + (data.log.length === 30 ? ' most recent' : '') + ')' }),
      el('div', { class: 'list', style: 'margin-top:12px' }, data.log.map((entry) => el('div', { class: 'row between' }, el('span', { text: entry.title }),
        el('span', { class: 'small dim', text: ({ identical: 'identical spare removed', duplicate: 'spare set aside and removed',
            merge: 'merged',
            review: 'your choice', apart: 'not duplicates' }[entry.action] || entry.action)
          + (entry.note ? (entry.action === 'apart' ? ' (now ' + entry.note + ')' : ' (' + entry.note + ' copied)') : '') + ' · ' + niceDate(entry.at) }))))) : null);
}

// What the copies say about themselves, as the carousel lists it under each page.
const DUP_ROWS = [
  ['Title', (copy) => copy.title],
  ['Author', (copy) => copy.authors.join(' & ')],
  ['Series', (copy) => copy.series ? copy.series + (copy.series_index !== null ? ' · ' + copy.series_index : '') : ''],
  ['Format', (copy) => copy.files.map((file) => file.format).join(', ') + (copy.converted ? ' (converted)' : '')],
  ['File size', (copy) => copy.files.map((file) => fileSize(file.size)).join(', ')],
  ['Pages', (copy) => copy.pages ? 'about ' + copy.pages : ''],
  ['Added', (copy) => niceDate(copy.added)],
  ['Tags', (copy) => copy.tags.join(', ')],
  ['Publisher', (copy) => copy.publisher],
  ['Language', (copy) => (copy.language || '').toUpperCase()],
  ['ISBN', (copy) => copy.isbn],
  ['Description', (copy) => copy.synopsis ? plural(copy.synopsis, 'word') : ''],
];

// The duplicates queue as a carousel the admin drives: a strip of every set along the top (Previous, Next, the
// arrow keys, or a cover in the strip), and the chosen set's copies laid open like facing pages, front pages
// left and right, each copy's own details under its page ready to put right. Keep one, or say they are
// different books, and the carousel moves on to the next set.
let dupInside = false;  // front pages, or the first few pages of each copy

function dupQueueCard(data, again) {
  const card = el('section', { class: 'panel cut focus dq cs', 'aria-label': 'Copies to sort out' });
  const set = data.set;
  const sets = data.sets;
  const blocked = Boolean(data.blocked) || data.busy;
  if (!set) {
    return fill(card, head('Copies to sort out'), el('p', { class: 'small dim', text: 'Nothing to sort out. Every book is in the library once, or you have said which copies are different books.' }));
  }
  const at = Math.max(0, sets.findIndex((entry) => entry.key === set.key));
  const go = (index) => { const next = sets[(index + sets.length) % sets.length]; if (next && next.key !== set.key) dupGo(next.key, again); };
  // once this set is settled it leaves the list, so the carousel goes on to whichever came after it
  const after = (sets[at + 1] || sets[at - 1] || {}).key;
  const name = (copy) => 'Copy ' + (set.copies.indexOf(copy) + 1);
  let keep = set.copies.find((copy) => copy.id === set.keep) || set.copies[0];

  // the strip: every set's cover, the one open now marked
  const strip = el('div', { class: 'cs-strip', role: 'group', 'aria-label': 'Every set of copies' }, sets.map((entry, i) => el('button', {
    type: 'button', class: 'cs-thumb', 'aria-current': i === at ? 'true' : null, 'aria-label': entry.title + ', ' + plural(entry.copies, 'copy', 'copies'),
    title: entry.title, onclick: () => go(i) },
  entry.cover ? el('img', { src: '/api/admin/duplicates/cover/' + entry.cover, alt: '', loading: 'lazy',
    onerror: (event) => event.currentTarget.replaceWith(el('span', { class: 'cs-thumb-t', text: entry.title })) }) : el('span', { class: 'cs-thumb-t', text: entry.title }),
  el('span', { class: 'cs-thumb-n', 'aria-hidden': 'true', text: String(entry.copies) }))));
  requestAnimationFrame(() => { const now = strip.children[at]; if (now) strip.scrollLeft = now.offsetLeft - strip.clientWidth / 2 + now.offsetWidth / 2; });

  // what differs between the copies, field by field, before anyone types
  const own = (copy) => ({ title: copy.title, authors: copy.authors.join(' & '), series: copy.series || '', series_index: reviewValue(copy.series_index),
    tags: copy.tags.join(', ') });
  const differs = {};
  for (const fieldName of ['title', 'authors', 'series', 'series_index', 'tags']) {
    differs[fieldName] = new Set(set.copies.map((copy) => own(copy)[fieldName])).size > 1;
  }

  const pages = set.copies.map((copy) => {
    const start = own(copy);
    const inputs = {
      title: el('input', { type: 'text', value: start.title }), authors: el('input', { type: 'text', value: start.authors }),
      series: el('input', { type: 'text', value: start.series }), series_index: el('input', { type: 'text', inputmode: 'decimal', value: start.series_index }),
      tags: el('input', { type: 'text', value: start.tags }) };
    const from = el('span', { class: 'small dq-from' });
    const boxed = (label, fieldName, cls) => {
      const box = field(label, inputs[fieldName], cls);
      if (differs[fieldName]) box.classList.add('cs-diff');
      return box;
    };
    const front = el('div', { class: 'cs-front' });
    const keepBtn = el('button', { class: 'btn sm cs-keep', type: 'button', 'aria-label': 'Keep ' + name(copy), onclick: () => { keep = copy; mark(); } });
    const fate = el('p', { class: 'small cs-fate', 'aria-live': 'polite' });
    // the facts nobody edits here: format, size, pages, date added and the rest, the differing ones marked
    const facts = el('dl', { class: 'cs-facts' }, DUP_ROWS.filter(([label]) => !['Title', 'Author', 'Series', 'Tags'].includes(label)).map(([label, show]) => {
      const values = set.copies.map(show);
      if (values.every((value) => !value)) return null;
      return el('div', { class: values.every((value) => value === values[0]) ? '' : 'cs-diff' }, el('dt', { text: label }), el('dd', { text: show(copy) || 'none' }));
    }));
    const page = el('article', { class: 'cs-page', 'aria-label': name(copy) },
      el('div', { class: 'row between gap8' }, el('strong', { text: name(copy) }),
        el('span', { class: 'row gap8' },
          copy.files.length ? el('a', { class: 'small', href: '/api/books/' + copy.id + '/download/' + copy.files[0].format, text: 'Download the ' + copy.files[0].format }) : null,
          el('a', { class: 'small', href: '#/fix/' + copy.id, text: 'Open in the editor' }))),
      front,
      el('div', { class: 'rv-grid' }, boxed('Title', 'title', 'wide'), boxed('Author (separate with &)', 'authors', 'wide'),
        boxed('Series', 'series'), boxed('Number in series', 'series_index'), boxed('Tags (separate with commas)', 'tags', 'wide')),
      from, facts,
      el('div', { class: 'stack gap6' }, keepBtn, fate));
    const values = () => ({ title: inputs.title.value, authors: inputs.authors.value, series: inputs.series.value,
      series_index: inputs.series_index.value, tags: inputs.tags.value });
    return { copy, page, front, inputs, keepBtn, fate, from, values };
  });

  // The copy picked to start with takes what it's missing from whichever copy says more, as a saved keep does.
  const startWith = pages.find((one) => one.copy === keep);
  const fullest = set.start;
  const filled = [];
  for (const [fieldName, value] of Object.entries({ title: fullest.title, authors: fullest.authors.join(' & '), series: fullest.series || '',
    series_index: reviewValue(fullest.series_index), tags: fullest.tags.join(', ') })) {
    if (!startWith.inputs[fieldName].value && value) { startWith.inputs[fieldName].value = value; filled.push(fieldName); }
  }
  if (filled.length) startWith.from.textContent = 'Filled in from the other copy: ' + filled.map((fieldName) => ({ title: 'title', authors: 'author', series: 'series', series_index: 'number', tags: 'tags' })[fieldName]).join(', ') + '.';

  // the front pages, or the first few pages, of every copy side by side
  function drawFronts() {
    for (const one of pages) {
      if (dupInside || !one.copy.has_cover) { fill(one.front, peekPane(one.copy.id)); continue; }
      fill(one.front, el('img', { class: 'cs-cover', src: '/api/admin/duplicates/cover/' + one.copy.id, alt: 'Front page of ' + name(one.copy),
        onerror: (event) => fill(one.front, peekPane(one.copy.id)) }));
    }
    insideBtn.textContent = dupInside ? 'Show the front pages' : 'Show the first pages';
    insideBtn.setAttribute('aria-pressed', String(dupInside));
  }
  const insideBtn = el('button', { class: 'btn sm', type: 'button', onclick: () => { dupInside = !dupInside; drawFronts(); } });

  const keepSave = el('button', { class: 'btn pri dq-go', type: 'button', disabled: blocked });
  const apartSave = el('button', { class: 'btn', type: 'button', disabled: blocked, text: 'They are different books · save each one',
    title: 'Every copy stays in the library with the details written under it' });
  const settle = (btn, body, words) => {
    btn.disabled = true;
    api('POST', '/api/admin/duplicates/queue', Object.assign({ key: set.key }, body)).then((answer) => {
      toast(answer.problem ? answer.problem : (answer.message || words), Boolean(answer.problem), answer.problem ? 8000 : undefined);
      if (after) dupGo(after, again); else again();
    }).catch((err) => { btn.disabled = false; oops(err); });
  };
  keepSave.addEventListener('click', () => {
    const chosen = pages.find((one) => one.copy === keep);
    settle(keepSave, { action: 'keep', keep: keep.id, fields: chosen.values() }, 'Kept ' + name(keep).toLowerCase() + '.');
  });
  apartSave.addEventListener('click', () => {
    const names = {};
    pages.forEach((one) => { names[one.copy.id] = one.values(); });
    settle(apartSave, { action: 'apart', names }, 'Saved as separate books.');
  });

  // picking a copy: its page takes the accent, the others say what becomes of them, the button says it in words
  function mark() {
    const outcome = keep.outcome;
    for (const one of pages) {
      const kept = one.copy === keep;
      one.page.classList.toggle('keep', kept);
      one.keepBtn.setAttribute('aria-pressed', String(kept));
      one.keepBtn.textContent = kept ? 'Keeping this one' : 'Keep this one';
      one.fate.textContent = kept ? '' : (outcome.identical ? 'Will be deleted (identical file)' : 'Will be set aside in needs-attention');
    }
    keepSave.textContent = 'Keep ' + name(keep).toLowerCase() + ' · ' + outcome.words;
  }

  fill(card,
    head('Copies to sort out', el('span', { class: 'small dim', text: (at + 1) + ' of ' + sets.length })),
    el('div', { class: 'cs-nav' },
      el('button', { class: 'btn sm', type: 'button', 'data-go': 'prev', disabled: sets.length < 2, text: 'Previous', onclick: () => go(at - 1) }),
      strip,
      el('button', { class: 'btn sm', type: 'button', 'data-go': 'next', disabled: sets.length < 2, text: 'Next', onclick: () => go(at + 1) })),
    el('div', { class: 'row between gap8' },
      el('div', { class: 'stack', style: 'gap:2px;min-width:0;flex:1 1 320px' }, el('h3', { class: 'book', text: set.title }),
        el('span', { class: 'small dim', text: plural(set.copies.length, 'copy', 'copies') + (set.author ? ' by ' + set.author : '') + '. Move with the arrow keys when you are not typing.' })),
      insideBtn),
    data.blocked ? el('p', { class: 'warn', role: 'status' }, data.blocked + ' ', el('a', { href: '#/admin', text: 'Open Admin, Hub' })) : null,
    data.busy ? el('p', { class: 'small', role: 'status', text: 'A batch is running below. This waits until it is done.' }) : null,
    set.why ? el('p', { class: 'small rv-note', text: set.why }) : null,
    el('div', { class: 'cs-spread' }, pages.map((one) => one.page)),
    el('p', { class: 'small dim', text: 'Marked boxes and facts are where the copies differ. Keeping one writes its details to the library and deals with the rest; '
      + 'different books keeps every copy, each with the details under it.' }),
    el('div', { class: 'row gap6' }, keepSave, apartSave));
  drawFronts();
  mark();
  return card;
}

// ← and → move the carousel, except while typing in a box
document.addEventListener('keydown', (event) => {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  if (event.target.closest && event.target.closest('input, textarea, select, [contenteditable], iframe')) return;
  const btn = document.querySelector('.cs [data-go="' + (event.key === 'ArrowLeft' ? 'prev' : 'next') + '"]');
  if (btn && !btn.disabled) { event.preventDefault(); btn.click(); }
});

// Show another set in the queue without reloading the rest of the page.
function dupGo(key, again) {
  api('GET', '/api/admin/duplicates/queue' + (key ? '?' + qs({ key }) : '')).then((data) => {
    const old = document.querySelector('.dq');
    if (old) old.replaceWith(dupQueueCard(data, again));
  }).catch((err) => { if (!(err instanceof Stale)) oops(err); });
}

async function pageCopies() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const [copies, spares, queue] = await Promise.all([api('GET', '/api/admin/copies'), api('GET', '/api/admin/duplicates'), api('GET', '/api/admin/duplicates/queue')]);
  const again = () => pageCopies().catch(oops);
  const choose = (key, choice, msg) => api('POST', '/api/admin/copies', { key, choice }).then(() => { toast(msg); again(); }).catch(oops);
  const one = copies.sets.filter((set) => set.together), apart = copies.sets.filter((set) => !set.together);
  const files = (book) => (book.files.length ? book.files.map((file) => file.format + ' ' + fileSize(file.size)).join(' · ') : 'no book file');
  const entry = (book) => el('div', { class: 'row between' },
    el('span', { class: 'wrap' }, book.files.length ? el('a', { href: '/api/books/' + book.id + '/download/' + book.files[0].format, text: files(book) }) : el('span', { class: 'dim', text: files(book) }),
      el('span', { class: 'small dim', text: ' · added ' + niceDate(book.added) + (book.has_cover ? '' : ' · no cover') })),
    el('span', { class: 'row gap8' }, book.shown ? el('span', { class: 'tag', text: 'Shown' }) : null,
      appState.cwa_url ? el('a', { class: 'small', href: appState.cwa_url + '/book/' + book.id, target: '_blank', rel: 'noopener', text: 'Open in Calibre-Web' }) : null));
  const block = (group, button) => el('div', { class: 'stack gap6' },
    el('div', { class: 'row between' }, el('span', { class: 'wrap' }, el('strong', { text: group.title }), el('span', { class: 'small dim',
        text: ' · ' + (group.author || 'no author') + (group.choice ? ' · your choice' : '') })), button),
    el('div', { class: 'list' }, group.books.map(entry)));
  setMain(back('#/admin/books', 'Back to books admin'),
    pageHead('Copies of the same book', 'The library holds ' + copies.entries + ' entries for ' + plural(copies.books,
        'book') + '. Entries with the same title and author are shown as one book. Family Hub changes nothing in the library: every copy stays where it is, and can be downloaded here.'),
    dupQueueCard(queue, again),
    // the bulk buttons for the clear-cut sets, folded away: the queue above is the usual way through
    el('details', { class: 'dq-bulk', open: spares.job.running ? true : null }, el('summary', { text: 'Clear-cut sets, all at once' }), spareCopiesCard(spares, again)),
    head('How Family Hub shows them meanwhile'),
    el('section', { class: 'panel cut' }, head(plural(one.length, 'book') + ' shown as one'),
      el('p', { class: 'small dim',
          text: 'The copy marked Shown is the one people download: the one with a cover, an EPUB and the larger file. A format that only another copy has is offered too. To remove spare copies for good, use the card above, or delete one by hand in Calibre-Web.' }),
      one.length ? el('div', { class: 'list' }, one.map((group) => block(group, el('button', { class: 'btn', type: 'button',
          text: 'Keep apart', 'aria-label': 'Keep apart: ' + group.title, onclick: () => choose(group.key, 'apart', 'Shown as separate books.') }))))
        : el('p', { class: 'small dim', text: 'No book is in the library twice.' })),
    apart.length ? el('section', { class: 'panel cut' }, head('Same name, kept apart'),
      el('p', { class: 'small dim',
          text: 'These share a title and author, but the title looks like a file name or the author like a user name, and the files differ. They may be different documents, so each is listed by itself. Entries holding exactly the same file are still shown as one.' }),
      el('div', { class: 'list' }, apart.map((group) => block(group, el('div', { class: 'row' },
        el('button', { class: 'btn', type: 'button', text: 'Show as one', 'aria-label': 'Show as one: ' + group.title, onclick: () => choose(group.key, 'together', 'Shown as one book.') }),
        group.choice ? el('button', { class: 'btn', type: 'button', text: 'Let Family Hub decide',
            'aria-label': 'Let Family Hub decide: ' + group.title, onclick: () => choose(group.key, null,
            'Back to the usual rule.') }) : null))))) : null);
}

// The nightly copy to a second disk: when it last ran, what it did, and a button to run it now.
function secondCopyLines(copy, reload) {
  const title = el('h3', { text: 'Copy to a second disk' });
  if (!copy.configured) return el('div', { class: 'stack gap6' }, title, el('p', { class: 'small dim',
      text: 'Not set up. Every night Family Hub can copy its data, the settings of the other services, the book library and the chapters to another disk. Guide 6 has the steps.' }));
  const last = copy.last;
  let line = el('p', { class: 'small dim', text: 'No copy has been made yet. The first runs tonight.' });
  if (last && last.at) line = el('p', { class: 'small ' + (last.ok ? '' : 'bad'),
      text: 'Last copy: ' + niceDate(last.at) + ' (' + ago(last.at).toLowerCase() + '). ' + (last.error ? last.error : plural(last.copied,
      'file') + ' copied (' + fileSize(last.bytes) + '), ' + last.unchanged + ' unchanged, ' + last.set_aside + ' older ' + (last.set_aside === 1 ? 'version' : 'versions') + ' kept aside.') });
  return el('div', { class: 'stack gap6' }, title, line,
    el('p', { class: 'small dim',
        text: (copy.on ? 'Runs every night.' : 'Switched off under Connections.') + (copy.free ? ' ' + (copy.free / 1e9).toFixed(1) + ' GB free on the second disk.' : '') }),
    el('div', { class: 'row' }, el('button', { class: 'btn', type: 'button', disabled: copy.running ? '' : null, text: copy.running ? 'Copying now…' : 'Copy now', onclick: async (event) => {
      try { await api('POST',
          '/api/admin/second-copy'); event.target.disabled = true; event.target.textContent = 'Copying now…'; toast('Copying. A large library takes a while the first time; reload this page to see the result.',
          false, 8000); } catch (err) { oops(err); }
    } })));
}

function attentionCard(stats, reload) {
  const attention = stats.attention;
  if (!attention.total && !attention.processed) return null;
  return el('section', { class: 'panel cut' }, head('Needs attention', attention.total ? el('button', { class: 'btn', type: 'button', text: 'Try these again', onclick: async () => {
      try { const result = await api('POST', '/api/admin/attention/retry'); toast(plural(result.delivered,
          'file') + ' sent to the library, ' + result.attention + ' still set aside.'); reload(); } catch (err) { oops(err); }
    } }) : null),
    el('p', { class: 'small dim', text: attention.total ? plural(attention.total,
        'file') + ' could not be added and ' + (attention.total === 1 ? 'was' : 'were') + ' moved to the needs-attention folder. Nothing was deleted.' : 'Nothing is waiting.' }),
    attention.items.length ? el('div', { class: 'scroll' }, el('table', {},
      el('thead', {}, el('tr', {}, ['File', 'Why', 'Size'].map((heading, index) => el('th', { scope: 'col', class: index === 2 ? 'num' : '', text: heading })))),
      el('tbody', {}, attention.items.map((item) => el('tr', {}, el('th', { scope: 'row', class: 'plain', text: item.path }), el('td',
          { text: item.reason }), el('td', { class: 'num', text: fileSize(item.size) })))))) : null,
    attention.total > attention.items.length ? el('p', { class: 'small dim', text: 'Showing the newest ' + attention.items.length + '.' }) : null,
    attention.processed ? el('p', { class: 'small dim', text: plural(attention.processed,
        'original file') + ' (unpacked archives, covers, loose comic pages) kept in needs-attention/_processed. They are already in the library and safe to remove.' }) : null);
}

// ------------------------------------------------------- sort the library

async function pageSort() {
  if (!isAdmin()) { location.hash = '#/dashboard'; return; }
  const view = { kind: 'Unsorted', q: '' };
  const picked = new Set();
  const table = el('div', { class: 'scroll' });
  const count = el('div', { class: 'dim', role: 'status' });
  const kindRow = el('div', { class: 'row', role: 'group', 'aria-label': 'Kind to show' });
  const toKind = el('select', {}, el('option', { value: '', text: 'Leave as it is' }), appState.kinds.map((kind) => el('option', { value: kind, text: kind })));
  const tag = el('input', { type: 'text', maxlength: 60 });
  let shown = [];

  async function load() {
    const data = await api('GET', '/api/books?' + qs({ filter: 'library', kinds: view.kind, q: view.q }));
    shown = data.books;
    picked.forEach((id) => { if (!shown.some((item) => item.id === id)) picked.delete(id); });
    drawTable();
  }
  function drawTable() {
    count.textContent = plural(shown.length, 'book') + ' · ' + picked.size + ' selected';
    const all = el('input', { type: 'checkbox', 'aria-label': 'Select all', checked: shown.length > 0 && picked.size === shown.length,
        onchange: (event) => { shown.forEach((item) => (event.target.checked ? picked.add(item.id) : picked.delete(item.id))); drawTable(); } });
    fill(table, shown.length ? el('table', { style: 'min-width:720px' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, all), ['Title', 'Author', 'Formats', 'Family tags'].map((heading) => el('th', { scope: 'col', text: heading })))),
      el('tbody', {}, shown.map((item) => el('tr', {},
        el('td', {}, el('input', { type: 'checkbox', 'aria-label': 'Select ' + item.title, checked: picked.has(item.id),
            onchange: (event) => { if (event.target.checked) picked.add(item.id); else picked.delete(item.id); drawTable(); } })),
        el('th', { scope: 'row', class: 'plain' }, el('a', { href: '#/book/' + item.id, text: item.title })),
        el('td', { text: item.author }), el('td', { text: item.formats.join(' · ') }), el('td', { class: 'small', text: item.tags.family.join(', ') })))))
      : el('p', { class: 'dim', text: 'No books of this kind.' }));
  }
  function draw() {
    fill(kindRow, ...appState.kinds.map((kind) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(view.kind === kind),
        text: KIND_LABEL[kind] || kind, onclick: () => { view.kind = kind; picked.clear(); draw(); } })));
    load().catch(oops);
  }
  const search = el('input', { type: 'search', oninput: debounce((event) => { view.q = event.target.value; load().catch(oops); }, 250) });
  setMain(
    el('div', { class: 'row between end' },
      pageHead('Sort the library', 'Tick the books, then choose their kind or add a family tag such as a series or game edition.'),
      field('Search title, author or tag', search)),
    back('#/admin/books', 'Back to books admin'), kindRow,
    el('form', { class: 'panel cut row end', onsubmit: async (event) => {
      event.preventDefault();
      if (!picked.size) return toast('Tick some books first.', true);
      try {
        const result = await api('POST', '/api/books/kind', { ids: Array.from(picked), kind: toKind.value, tag: tag.value });
        toast(plural(result.updated, 'book') + ' updated.'); picked.clear(); tag.value = ''; load().catch(oops);
      } catch (err) { oops(err); }
    } }, field('Set kind to', toKind), field('Add family tag (optional)', tag), el('button', { class: 'btn pri', type: 'submit', text: 'Apply to selected' })),
    count, table);
  draw();
}

// -------------------------------------------------------------- discover

// The last Discover search, kept so going back to Discover (after opening a result, say) shows it again,
// where it was scrolled to, without asking the sites again. Kept for the browser tab, for two hours.
const DISCOVER_KEEP_MS = 2 * 60 * 60 * 1000;
let discoverKept = null;
try { discoverKept = JSON.parse(sessionStorage.getItem('bookhub-discover')) || null; } catch (err) { /* private browsing */ }
function keepDiscover(change) {
  discoverKept = Object.assign(discoverKept || { sent: [] }, change);
  try { sessionStorage.setItem('bookhub-discover', JSON.stringify(discoverKept)); } catch (err) { /* too big, or private browsing */ }
}
addEventListener('scroll', debounce(() => { if (discoverKept && location.hash.startsWith('#/discover')) keepDiscover({ scroll: scrollY }); }, 200), { passive: true });

async function pageDiscover(prefill) {
  // Read before anything else: arriving here scrolls to the top, which would soon be noted as where it was.
  const backTo = discoverKept ? discoverKept.scroll || 0 : 0;
  const results = el('div', { class: 'stack' });
  // Manga and webcomics followed chapter by chapter (Webtoon and the like) are found here too.
  const sites = el('section', { class: 'stack', hidden: true, 'aria-label': 'Series on reading sites' });
  const withSites = appState.series && canRequest();
  // Whether the reading sites are searched too; remembered on this device, per profile.
  const scopeKey = 'bookhub-discover-sites-' + (appState.me && appState.me.id ? appState.me.id : 'guest');
  let useSites = true;
  try { useSites = localStorage.getItem(scopeKey) !== 'off'; } catch (err) { /* private browsing */ }
  const scope = el('div', { class: 'tabs sm', role: 'radiogroup', 'aria-label': 'Where to search' });
  const drawScope = () => fill(scope, ...[[false, 'Books and novels'], [true, 'Also manga and webcomic sites']].map(([on, label]) =>
    el('button', { type: 'button', role: 'radio', 'aria-checked': String(useSites === on), text: label, onclick: () => {
      useSites = on;
      try { localStorage.setItem(scopeKey, on ? 'on' : 'off'); } catch (err) { /* private browsing */ }
      if (!on) { sites.hidden = true; fill(sites); }
      drawScope();
    } })));
  drawScope();
  const searchBox = el('input', { type: 'search', value: prefill ? decodeURIComponent(prefill) : '' });
  const drawSites = (data) => fill(sites, el('h2', { text: 'Series on reading sites' }),
    el('p', { class: 'small dim', text: 'Follow one to keep every chapter on the server; new chapters arrive by themselves.' }), ...seriesHits(data));
  async function search() {
    if (!searchBox.value.trim()) return;
    keepDiscover({ q: searchBox.value.trim(), data: null, sites: null, sent: [], scroll: 0, at: Date.now() });
    if (withSites && useSites && searchBox.value.trim().length >= 2) {
      sites.hidden = false;
      fill(sites, el('h2', { text: 'Series on reading sites' }), el('p', { class: 'dim', role: 'status', text: 'Searching the reading sites…' }));
      api('GET', '/api/series/search?' + qs({ q: searchBox.value.trim() }))
        .then((data) => { drawSites(data); keepDiscover({ sites: data }); })
        .catch((err) => fill(sites, el('h2', { text: 'Series on reading sites' }), el('p', { class: 'bad', role: 'status', text: err.message })));
    }
    const stop = searchProgress(results, searchBox.value.trim(), { typical: 20, what: 'Asking the torrent sites' });
    try {
      const data = await api('GET', '/api/discover/search?' + qs({ q: searchBox.value })).finally(stop);
      keepDiscover({ data });
      drawResults(data);
    } catch (err) { fill(results, el('p', { class: 'bad', role: 'status', text: err.message })); }
  }
  function drawResults(data) {
    if (!data.results.length) { fill(results, el('p', { class: 'dim', role: 'status', text: 'No results. Try fewer words, or check your indexers in Prowlarr.' })); return; }
    fill(results, el('p', { class: 'small dim', role: 'status', text: plural(data.results.length, 'result') }),
      el('div', { class: 'scroll' }, el('table', { style: 'min-width:720px' },
        el('thead', {}, el('tr', {}, ['Title', 'Format', 'Size', 'Indexer', 'Seeders', 'Action'].map((heading, index) => el('th',
            { scope: 'col', class: index === 2 || index === 4 ? 'num' : '', text: heading })))),
        el('tbody', {}, data.results.map((result) => {
          const already = (discoverKept && discoverKept.sent || []).includes(result.guid);
          const btn = el('button', { class: 'btn pri', type: 'button', disabled: already, text: already ? 'Sent' : result.format === 'PDF' ? 'Get (PDF)' : 'Download', onclick: async () => {
            btn.disabled = true;
            try {
              await api('POST', '/api/discover/grab', { guid: result.guid, indexer_id: result.indexer_id, title: result.title, indexer: result.indexer });
              btn.textContent = 'Sent';
              keepDiscover({ sent: (discoverKept.sent || []).concat(result.guid) });
              Tracker.follow('book', result.title);
              toast('Sent to qBittorrent. Follow it in the corner, or in Downloads.');
              loadQueue();
            } catch (err) { btn.disabled = false; oops(err); }
          } });
          return el('tr', {}, el('th', { scope: 'row', class: 'plain', text: result.title }), el('td', { text: result.format || '?' }), el('td', { class: 'num', text: fileSize(result.size) }),
            el('td', { text: result.indexer }), el('td', { class: 'num', text: result.seeders === null || result.seeders === undefined ? '–' : result.seeders }),
            el('td', {}, result.library_id ? el('a', { class: 'ok', href: '#/book/' + result.library_id, text: 'In library' }) : btn));
        })))));
  }

  const queue = el('section', { class: 'panel cut' });
  async function loadQueue() {
    const data = await api('GET', '/api/discover/queue');
    fill(queue, head('Downloads', el('a', { class: 'btn sm', href: '#/downloads', text: 'See every download' })),
      el('p', { class: 'small dim', text: 'Progress, pausing, the order things download in and deleting are all on the Downloads page.' }),
      el('h3', { text: 'Recent requests' }),
      data.requests.length ? el('div', { class: 'list' }, data.requests.slice(0, 6).map((request) => el('div', { class: 'row between' },
          el('span', { text: request.title }), el('span', { class: 'small dim',
          text: (request.swapped ? 'swapped in for a copy nobody was sharing · ' : '') + 'by ' + request.by + ' · ' + ago(request.created).toLowerCase() })))) : el('p',
          { class: 'small dim', text: 'No requests yet.' }),
      el('h3', { text: 'Added to the library' }),
      data.imported.length ? el('div', { class: 'list' }, data.imported.slice(0, 6).map((file) => el('div', { class: 'row between' },
          el('span', { text: file.name }), el('span', { class: 'small ok', text: ago(file.at) })))) : el('p', { class: 'small dim',
          text: 'Nothing imported yet.' }));
  }

  // Unread books already in the library, closest to what this person has enjoyed first.
  const likeBox = el('section', { class: 'panel cut' });
  function loadLike() {
    return api('GET', '/api/books?filter=foryou').then((data) => {
      let body;
      if (!data.taste.ready) body = el('p', { class: 'small dim', text: 'Finish or rate ' + plural(data.taste.need - data.taste.have,
          'more book') + ' and this fills with unread books like the ones you enjoyed. You have ' + data.taste.have + ' so far.' });
      else if (!data.books.length) body = el('p', { class: 'small dim', text: 'Nothing unread in the library resembles what you have enjoyed yet. Tags help: see Admin, Books, Tidy tags.' });
      else body = el('div', { class: 'shelf' }, data.books.slice(0, 12).map((book) => bookCard(book, () => loadLike().catch(oops))));
      fill(likeBox, head("In the library: similar to what I've read"),
        el('p', { class: 'small dim', text: 'Books you have not started, closest to the ones you finished or rated well first.' }), body);
    });
  }
  loadLike().catch(oops);

  // Authors this person likes (suggested more, and looked for first) or does not (never suggested). Only theirs.
  const authorBox = el('section', { class: 'panel cut stack' });
  const authorIn = el('input', { type: 'text', list: 'known-authors', placeholder: 'An author' });
  const known = el('datalist', { id: 'known-authors' });
  async function setAuthor(author, how) {
    await api('POST', '/api/me/authors', { author, how });
    drawAuthors().catch(oops);
    loadLike().catch(oops);
    api('GET', '/api/recommendations').then(drawRecs).catch(() => {});
  }
  async function drawAuthors() {
    const data = await api('GET', '/api/me/authors');
    fill(known, data.known.map((author) => el('option', { value: author })));
    const chips = (list, how, word) => list.length ? el('div', { class: 'terms' }, list.map((entry) => el('span', { class: 'term', role: 'listitem' },
      el('span', { class: 'sign', 'aria-hidden': 'true', text: how === 'like' ? '+' : '−' }), el('span', { class: 'val', text: entry }),
      el('button', { class: 'x', type: 'button', text: '×', 'aria-label': 'Take ' + entry + ' off the authors you ' + word, onclick: () => setAuthor(entry, 'none').catch(oops) }))))
      : el('p', { class: 'small dim', style: 'margin:0', text: 'None yet.' });
    const add = (how) => (event) => { event.preventDefault(); const author = authorIn.value.trim(); if (!author) return; authorIn.value = ''; setAuthor(author, how).catch(oops); };
    fill(authorBox, head('Authors for my suggestions'),
      el('p', { class: 'small dim',
          text: 'Authors you like are suggested more and looked for first when suggesting books the library lacks. Authors you do not like are never suggested. Only you see these lists.' }),
      el('form', { class: 'row end', onsubmit: add('like') }, field('Author', authorIn, 'wide'), known,
        el('button', { class: 'btn', type: 'submit', text: 'I like them' }),
        el('button', { class: 'btn', type: 'button', text: "I don't like them", onclick: add('dislike') })),
      el('div', { class: 'cols' },
        el('div', { class: 'stack gap6' }, el('h3', { text: 'I like' }), chips(data.like, 'like', 'like')),
        el('div', { class: 'stack gap6' }, el('h3', { text: "I don't like" }), chips(data.dislike, 'dislike', 'do not like'))));
  }
  drawAuthors().catch(oops);

  // Manga and manhwa to try, from AniList readers' recommendations for the series this person enjoyed.
  const mangaBox = el('section', { class: 'panel cut' });
  function drawManga(data) {
    const again = el('button', { class: 'btn', type: 'button', text: 'Look again', onclick: () => { again.disabled = true; again.textContent = 'Asking AniList…'; loadManga(true); } });
    let body;
    if (!data.on) body = el('p', { class: 'small dim', text: 'These come from AniList, and looking things up outside is switched off under Connections.' });
    else if (!data.liked) body = el('p', { class: 'small dim', text: 'Rate a manga or manhwa (or read a good way into one you follow) and readers\' recommendations from AniList appear here.' });
    else if (!data.books.length) body = el('p', { class: 'small dim',
        text: data.waiting ? 'Asking AniList about the series you enjoyed; press Look again in a minute.' : 'Nothing new to suggest from what you have enjoyed so far.' });
    else body = el('div', { class: 'shelf' }, data.books.map((book) => el('div', { class: 'book' },
      el('div', { class: 'cover' + (book.cover ? ' has-img' : '') + titleSize(book.title),
          style: '--cv:' + coverColor(book.title) + (book.cover ? ';background-image:url(/api/covers/preview?' + qs({ url: book.cover }) + ')' : '') },
        el('span', { class: 'fmt', text: book.kind }), el('span', { class: 't', style: 'text-decoration:none', text: book.cover ? '' : book.title })),
      el('div', { class: 'card-title', title: book.title, text: book.title }),
      el('div', { class: 'small dim card-line', text: [book.status, book.score ? book.score + '% liked' : ''].filter(Boolean).join(' · ') }),
      el('div', { class: 'card-tags', title: book.tags.concat(book.genres).join(' · ') }, el('span', { class: 'lab',
          text: (book.matched.length ? book.matched : book.genres).slice(0, 3).join(' · ') })),
      el('div', { class: 'small card-line', title: book.reason, text: book.reason }),
      el('div', { class: 'card-foot' },
        data.series && canRequest() ? el('button', { class: 'btn sm pri', type: 'button', 'aria-label': 'Find ' + book.title + ' on the reading sites', text: 'Find on reading sites',
          onclick: () => { searchBox.value = book.title; if (withSites && !useSites) { useSites = true; drawScope(); } search(); window.scrollTo(0, 0); } }) : null,
        el('div', { class: 'row gap6' },
          book.link ? el('a', { class: 'small', href: book.link, target: '_blank', rel: 'noopener noreferrer', text: 'On AniList' }) : null,
          el('button', { class: 'btn sm', type: 'button', 'aria-label': 'Not for me: ' + book.title, text: 'Not for me',
            onclick: () => api('POST', '/api/recommendations/manga/dismiss', { id: book.id }).then(() => loadManga(false)).catch(oops) }))))));
    fill(mangaBox, head('Manga and manhwa you might like', data.on && data.liked ? again : null),
      el('p', { class: 'small dim', text: 'What AniList readers recommend after the series you rated or have read a good way into, best match first. Only series titles are sent to AniList.' }),
      data.error ? el('p', { class: 'small warn', role: 'status', text: data.error }) : null, body);
  }
  function loadManga(look) {
    return api('GET', '/api/recommendations/manga' + (look ? '?look=1' : '')).then((data) => {
      drawManga(data);
      // A few liked series are asked about at a time; keep going while some are still to ask about.
      if (data.on && data.waiting && data.asked && !data.error && document.body.contains(mangaBox)) setTimeout(() => loadManga(true), 4000);
    }).catch(oops);
  }
  loadManga(true);

  // Books the library does not have, picked once a week from what this person has enjoyed.
  const recBox = el('section', { class: 'panel cut' });
  function drawRecs(data) {
    const top = head('Suggested for you this week', data.ready && data.on ? el('button', { class: 'btn', type: 'button',
        text: 'Suggest again', onclick: (event) => { event.target.disabled = true; event.target.textContent = 'Asking Open Library…'; api('POST',
        '/api/recommendations/refresh').then(drawRecs).catch((err) => { oops(err); drawRecs(data); }); } }) : null);
    let body;
    if (!data.ready) body = el('p', { class: 'small dim', text: 'Finish or rate ' + plural(data.need - data.have,
        'more book') + ' and Family Hub will suggest books the library does not have yet. You have ' + data.have + ' so far.' });
    else if (!data.on) body = el('p', { class: 'small dim', text: 'Suggestions come from Open Library, and looking things up outside is switched off under Connections.' });
    else if (!data.books.length) body = el('p', { class: 'small dim',
        text: data.updated ? 'Nothing new this week. Rating more books gives Family Hub more to go on.' : 'Your first suggestions arrive within the hour, or press Suggest again.' });
    else body = el('div', { class: 'shelf' }, data.books.map((book) => el('div', { class: 'book' },
      el('div', { class: 'cover' + (book.cover_id ? ' has-img' : '') + titleSize(book.title),
          style: '--cv:' + coverColor(book.title) + (book.cover_id ? ';background-image:url(/api/covers/preview?' + qs({ url: 'https://covers.openlibrary.org/b/id/' + book.cover_id + '-M.jpg' }) + ')' : '') },
        el('span', { class: 'fmt', text: 'Not in library' }), el('span', { class: 't', style: 'text-decoration:none', text: book.title })),
      el('div', { class: 'small dim', text: book.author + (book.year ? ' · ' + book.year : '') }),
      el('div', { class: 'small', text: book.reason }),
      canRequest() ? el('button', { class: 'btn pri', type: 'button', 'aria-label': 'Find it: ' + book.title, text: 'Find it',
          onclick: () => { searchBox.value = findQuery(book); search(); window.scrollTo(0, 0); } }) : null,
      el('button', { class: 'btn', type: 'button', 'aria-label': 'Not for me: ' + book.title, text: 'Not for me', onclick: () => api('POST',
          '/api/recommendations/dismiss', { key: book.key }).then(() => api('GET', '/api/recommendations')).then(drawRecs).catch(oops) }),
      book.author ? el('button', { class: 'btn sm', type: 'button', 'aria-label': 'No more books by ' + book.author,
          text: 'Not this author', onclick: () => setAuthor(book.author.split(' & ')[0], 'dislike').catch(oops) }) : null)));
    fill(recBox, top,
      el('p', { class: 'small dim',
          text: 'Books the library does not have, by authors and on subjects you have rated highly. A new list is made each week' + (data.updated ? '; this one is from ' + niceDate(data.updated) + '.' : '.') }),
          body);
  }

  const simBox = el('div', { class: 'stack' });
  const read = (await api('GET', '/api/books?filter=read')).books;
  let pick = read[0];
  async function drawSimilar() {
    if (!pick) { fill(simBox, el('p', { class: 'small dim', text: 'Finish a book and it will appear here.' })); return; }
    const data = await api('GET', '/api/similar-by-key?' + qs({ key: pick.key }));
    fill(simBox, el('div', { class: 'row', role: 'group', 'aria-label': "Books you've read" }, read.slice(0, 12).map((book) =>
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(book.key === pick.key), text: book.title, onclick: () => { pick = book; drawSimilar().catch(oops); } }))),
      similarList('Like ' + pick.title, data.books.filter((book) => !book.already_read)));
  }

  const watchBox = el('div', { class: 'stack' }), newBox = el('div', { class: 'stack' });
  const wKind = el('select', {}, el('option', { value: 'author', text: 'Author' }), el('option', { value: 'tag', text: 'Genre or tag' }));
  const wValue = el('input', { type: 'text' });
  async function loadWatch(refresh) {
    const [watching, news] = await Promise.all([api('GET', '/api/watch'), api('GET', '/api/discover/new' + (refresh ? '?refresh=1' : ''))]);
    fill(watchBox, watching.watch.length ? el('div', { class: 'list' }, watching.watch.map((watched) => el('div', { class: 'row between' },
      el('span', {}, el('strong', { text: watched.value }), el('span', { class: 'small dim', text: watched.kind === 'author' ? ' · author' : ' · tag' })),
      el('button', { class: 'btn', type: 'button', text: 'Stop following', 'aria-label': 'Stop following ' + watched.value,
          onclick: () => api('DELETE', '/api/watch/' + watched.id).then(() => loadWatch(true)).catch(oops) }))))
      : el('p', { class: 'small dim', text: 'Follow an author or a tag to get suggestions here.' }));
    fill(newBox, news.items.length ? el('div', { class: 'list' }, news.items.slice(0, 12).map((item) => el('div', { class: 'row between' },
      el('div', {}, el('strong', { text: item.title }), el('div', { class: 'small dim', text: item.reason + (item.author && !item.release ? ' · ' + item.author : '') })),
      item.in_library ? el('a', { class: 'btn', href: '#/book/' + item.id, text: 'Open in library' })
        : (canRequest() ? el('button', { class: 'btn pri', type: 'button', text: 'Find it',
            onclick: () => { searchBox.value = item.release ? item.title : item.title + ' ' + item.author; search(); window.scrollTo(0,
            0); } }) : null))))
      : el('p', { class: 'small dim', text: 'Nothing new right now.' }));
  }

  setMain(
    pageHead('Discover', 'Find new books, send them to the download client, and follow authors and tags.'),
    canRequest() ? el('section', { class: 'panel cut' }, head('Search for a book'),
      appState.discover_configured ? null : el('p', { class: 'warn' }, 'Book search is not set up yet. ', isAdmin() ? el('a',
          { href: '#/admin',
          text: 'Add the Prowlarr address and API key under Admin, Hub, Connections.' }) : 'Ask an admin to add the Prowlarr address and API key.'),
      withSites ? scope : null,
      el('form', { class: 'row end', onsubmit: (event) => { event.preventDefault(); search(); } }, field('Title or author', searchBox,
          'wide'), el('button', { class: 'btn pri', type: 'submit', text: 'Search' })),
      sites, results) : el('p', { class: 'dim', text: 'This profile can browse suggestions but cannot download new books. Ask an admin to find one for you.' }),
    likeBox, authorBox, recBox, mangaBox,
    el('section', { class: 'panel cut' }, head("Similar to books you've read"), el('p', { class: 'small dim', text: 'Matches share tags or an author with the book you pick.' }), simBox),
    el('div', { class: 'cols' },
      canRequest() ? queue : null,
      el('section', { class: 'panel cut' }, head('New for you', el('button', { class: 'btn', type: 'button', text: 'Check again', onclick: () => loadWatch(true).catch(oops) })), newBox)),
    el('section', { class: 'panel cut' }, head('Your watchlist'), watchBox,
      el('form', { class: 'row end', onsubmit: async (event) => { event.preventDefault(); try { await api('POST', '/api/watch',
          { kind: wKind.value, value: wValue.value }); wValue.value = ''; loadWatch(true); } catch (err) { oops(err); } } },
        field('Follow', wKind), field('Name', wValue), el('button', { class: 'btn', type: 'submit', text: 'Follow' }))));
  drawSimilar().catch(oops);
  api('GET', '/api/recommendations').then(drawRecs).catch(oops);
  loadWatch().catch(oops);
  if (canRequest()) {
    loadQueue().catch(oops);
    if (prefill) search();
    else if (discoverKept && discoverKept.q && Date.now() - (discoverKept.at || 0) < DISCOVER_KEEP_MS) {
      // Back on Discover: the last search as it was, without asking the sites again.
      searchBox.value = discoverKept.q;
      if (discoverKept.data) drawResults(discoverKept.data);
      if (discoverKept.sites && withSites && useSites) { sites.hidden = false; drawSites(discoverKept.sites); }
      requestAnimationFrame(() => { scrollTo(0, backTo); keepDiscover({ scroll: backTo }); });
    }
  }
}
