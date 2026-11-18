/* Watch mode: Films and TV for the big screen, and the player. Plain JavaScript, no build step.
   Uses app.js's helpers (el, api, fill, setMain, head, bar, icon, posterCard and the rest); loaded after it.

   #/watch                         rows of posters: carry on, new, films, TV shows, on the way
   #/watch/film/7  #/watch/show/7  one title: Play, or its seasons and episodes
   #/watch/search                  find a film or show and ask for it
   #/watch/play/film/7             the player           (add /start to begin again from the beginning)
   #/watch/play/show/7/2/5         season 2, episode 5

   The video itself comes through Family Hub (player.py); the browser never talks to Jellyfin. */
'use strict';

const Watch = (() => {
  const SKIP_BACK = 10, SKIP_ON = 30;     // seconds
  const QUIET_AFTER = 3000;               // the controls go away after this long without a move
  const KEEP_EVERY = 15000;               // how often the place is kept while playing
  const UP_NEXT = 10;                     // seconds before the next episode starts by itself
  const STALLED_AFTER = 60000;            // waiting this long for the next part means it is not coming
  const WORTH_RESUMING = 60;              // matches player.py
  const ICON = {
    play: 'M7 5l12 7-12 7z', pause: 'M8 5v14M16 5v14', back: 'M4 12a8 8 0 1 0 3-6.2M4 4v5h5', on: 'M20 12a8 8 0 1 1-3-6.2M20 4v5h-5',
    next: 'M6 5l9 7-9 7zM18 5v14', subs: 'M3 6h18v12H3zM7 12h4M13 12h4M7 15h7', sound: 'M4 10v4h4l5 4V6L8 10zM16 9a4 4 0 0 1 0 6',
    muted: 'M4 10v4h4l5 4V6L8 10zM17 9l4 6M21 9l-4 6', full: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
    small: 'M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5', search: 'M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14M16 16l4 4', leave: 'M15 5l-7 7 7 7',
  };
  const calm = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const same = (left, right) => String(left || '').toLowerCase() === String(right || '').toLowerCase();

  let leave = null;  // puts the player away (keeps the place, stops the conversion); set while a film is on screen

  // ---------------------------------------------------------------- small things

  // 4980 -> "1:23:00"; 185 -> "3:05"
  function clock(seconds) {
    const whole = Math.max(0, Math.floor(seconds || 0));
    const hours = Math.floor(whole / 3600), mins = Math.floor((whole % 3600) / 60), secs = whole % 60;
    return (hours ? hours + ':' + String(mins).padStart(2, '0') : String(mins)) + ':' + String(secs).padStart(2, '0');
  }
  // for a screen reader: "1 hour 23 minutes"
  function spoken(seconds) {
    const mins = Math.round((seconds || 0) / 60), hours = Math.floor(mins / 60);
    return [hours ? plural(hours, 'hour') : '', mins % 60 || !hours ? plural(mins % 60, 'minute') : ''].filter(Boolean).join(' ');
  }
  const left = (place) => Math.max(1, Math.round((place.length - place.at) / 60)) + ' min left';
  const episodeName = (season, episode) => 'S' + season + ' E' + episode;

  function playLink(kind, id, season, episode, fromStart) {
    return '#/watch/play/' + kind + '/' + id + (kind === 'show' ? '/' + season + '/' + episode : '') + (fromStart ? '/start' : '');
  }

  // A fetch that carries on after the page has moved on: keeping the place and stopping must not be thrown away as stale.
  function tell(url, body, closing) {
    const text = JSON.stringify(body);
    if (closing && navigator.sendBeacon && navigator.sendBeacon(url, new Blob([text], { type: 'application/json' }))) return;
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: text, keepalive: true }).catch(() => {});
  }
  // A page's extras (the places, where to watch) may fail without taking the page with them.
  const soft = (promise, instead) => promise.catch((err) => { if (err instanceof Stale) throw err; return instead; });

  // ---------------------------------------------------------------- full screen

  const fullNow = () => document.fullscreenElement || document.webkitFullscreenElement || null;
  const canFull = () => !!(document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen);
  function toggleFull(video) {
    if (fullNow()) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
    const page = document.documentElement, ask = page.requestFullscreen || page.webkitRequestFullscreen;
    if (ask) { const asked = ask.call(page); if (asked && asked.catch) asked.catch(() => toast('This browser would not go full screen.', true, 6000)); }
    else if (video && video.webkitEnterFullscreen) video.webkitEnterFullscreen();  // an iPhone: only the video itself can
    else toast('This browser cannot go full screen. Turning the phone sideways gives the film the whole screen.', false, 8000);
  }
  function paintFull(button) {
    const on = !!fullNow(), words = on ? 'Leave full screen' : 'Full screen';
    button.setAttribute('aria-pressed', String(on));
    if (button.dataset.full === 'icon') { button.setAttribute('aria-label', words); button.title = words + ' (F)'; button.replaceChildren(icon(on ? ICON.small : ICON.full)); }
    else button.replaceChildren(icon(on ? ICON.small : ICON.full, 16), words);
    return button;
  }
  const showFull = () => document.querySelectorAll('[data-full]').forEach(paintFull);
  document.addEventListener('fullscreenchange', showFull);
  document.addEventListener('webkitfullscreenchange', showFull);
  function fullButton() {
    if (!canFull()) return null;
    return paintFull(el('button', { class: 'btn', type: 'button', 'data-full': 'words', onclick: () => toggleFull() }));
  }

  // ---------------------------------------------------------------- moving about with arrow keys

  // A TV remote, or a keyboard on the couch: the arrows go to the nearest thing that can be pressed in that direction.
  const PRESSABLE = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), summary';
  function step(direction, within) {
    const scope = within || main();
    const all = [...scope.querySelectorAll(PRESSABLE)].filter((node) => node.offsetParent !== null && !node.closest('[hidden]'));
    const from = scope.contains(document.activeElement) ? document.activeElement : null;
    if (!from) { if (all[0]) go(all[0]); return true; }
    const here = from.getBoundingClientRect();
    let best = null, bestScore = Infinity;
    for (const node of all) {
      if (node === from) continue;
      const there = node.getBoundingClientRect();
      const sideways = direction === 'left' || direction === 'right';
      // how far ahead it is, and how far off to the side (nothing if the two overlap on that axis)
      const ahead = direction === 'left' ? here.left - there.right : direction === 'right' ? there.left - here.right
        : direction === 'up' ? here.top - there.bottom : there.top - here.bottom;
      if (ahead < -2) continue;
      const aside = sideways ? Math.max(0, there.top - here.bottom, here.top - there.bottom)
        : Math.abs((there.left + there.width / 2) - (here.left + here.width / 2));
      const score = ahead + aside * (sideways ? 4 : 0.35);
      if (score < bestScore) { best = node; bestScore = score; }
    }
    if (best) go(best, direction);
    return !!best;
  }
  function go(node, direction) {
    node.focus({ preventScroll: true });
    node.scrollIntoView({ behavior: calm() ? 'auto' : 'smooth', inline: 'nearest', block: direction === 'up' || direction === 'down' ? 'center' : 'nearest' });
  }
  const ARROWS = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' };
  document.addEventListener('keydown', (event) => {
    if (document.body.dataset.page !== 'watch' || leave || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (document.querySelector('dialog[open]')) return;
    const typing = event.target.closest && event.target.closest('input, textarea, select, [contenteditable]');
    const direction = ARROWS[event.key];
    if (direction) {
      if (typing && (typing.tagName === 'SELECT' || direction === 'left' || direction === 'right')) return;  // moving within the words
      if (step(direction)) event.preventDefault();
    } else if (!typing && (event.key === 'Backspace' || event.key === 'BrowserBack')) { event.preventDefault(); history.back(); }
    else if (!typing && (event.key === 'f' || event.key === 'F')) { event.preventDefault(); toggleFull(); }
  });

  // ---------------------------------------------------------------- the frame around every Watch screen

  function topBar(start) {
    return el('header', { class: 'wbar' },
      start ? el('h1', { class: 'glow', text: 'Watch' }) : el('a', { class: 'wbar-home', href: '#/watch', 'aria-label': 'Watch mode, start' }, starMark(), 'Watch'),
      el('nav', { class: 'row gap8', 'aria-label': 'Watch mode' },
        isAdmin() || canRequestMedia() ? el('a', { class: 'btn', href: '#/watch/search' }, icon(ICON.search, 16), 'Search and ask') : null,
        fullButton(),
        el('a', { class: 'btn', href: '#/movies' }, icon(ICON.leave, 16), 'Leave Watch mode')));
  }
  // What an admin (or anyone else) is told when a part of films and TV is not connected yet.
  function notSetUp(what, need) {
    return el('section', { class: 'panel cut' }, head(what),
      isAdmin() ? el('p', {}, need + ' The steps are in ', guide('14-films-tv-and-the-vpn.md', 'guide 14'), '.') : el('p', { text: need + ' Ask an admin.' }),
      isAdmin() ? el('div', { class: 'row' }, el('a', { class: 'btn pri', href: '#/admin', text: 'Open Admin' })) : null);
  }
  const noPlayer = () => notSetUp('Watching is not connected yet',
    'You can look through what is on the server and ask for more, but playing here needs Jellyfin connected under Admin, Hub, Connections.');

  // A poster to pick. extra: { href, note, place } (place draws the ribbon as far down as the film has got).
  function card(item, extra) {
    const more = extra || {};
    const pct = more.place ? Math.max(4, Math.min(100, Math.round(100 * more.place.at / more.place.length))) : 0;
    return el('a', { class: 'wcard', href: more.href || '#/watch/' + item.kind + '/' + item.id },
      el('div', { class: 'cover' + (item.poster ? ' has-img' : '') + titleSize(item.title), 'aria-hidden': 'true',
          style: '--cv:' + coverColor(item.title) + (item.poster ? ';background-image:url(' + item.poster + ')' : '') },
        el('span', { class: 'fmt', text: item.kind === 'film' ? 'Film' : 'TV' }),
        more.place ? el('span', { class: 'ribbon', style: '--at:' + Math.max(16, pct) + '%' }, el('b', { text: pct + '%' })) : null,
        el('span', { class: 't', text: item.title })),
      el('span', { class: 'wcard-title', text: item.title }),
      el('span', { class: 'small dim', text: more.note || [item.year, KIND_WORD[item.kind]].filter(Boolean).join(' · ') }));
  }
  function row(title, note, cards) {
    if (!cards.length) return null;
    return el('section', { class: 'panel cut wrow' }, head(title, note ? el('span', { class: 'small dim', text: note }) : null),
      el('div', { class: 'wreel' }, cards));
  }

  // ---------------------------------------------------------------- the rows

  async function library() {
    const problems = [];
    const sets = await Promise.all(['film', 'show'].filter((kind) => (kind === 'film' ? appState.media.films : appState.media.shows))
      .map((kind) => api('GET', '/api/media/library?kind=' + kind).then((data) => data.items)
        .catch((err) => { if (err instanceof Stale) throw err; problems.push(err.message); return []; })));
    return { items: sets.flat(), problems };
  }
  const onServer = (item) => (item.kind === 'film' ? item.downloaded : !!(item.episodes && item.episodes.have));

  async function pageHome() {
    nameThePage('Watch mode');
    if (!appState.media.films && !appState.media.shows) {
      setMain(topBar(true), notSetUp('Films and TV are not set up yet', 'Films and TV need Radarr and Sonarr connected.'));
      return;
    }
    const [{ items, problems }, kept] = await Promise.all([library(), soft(api('GET', '/api/media/places'), { places: [] })]);
    const find = (kind, id) => items.find((item) => item.kind === kind && item.id === id);
    // Carry on watching: the latest unfinished place for each film or show.
    const seen = new Set(), carryOn = [];
    for (const place of kept.places) {
      const item = find(place.kind, place.arr_id), key = place.kind + place.arr_id;
      if (!item || seen.has(key)) continue;
      seen.add(key);
      if (place.finished || place.at < WORTH_RESUMING) continue;
      carryOn.push(card(item, { place, href: playLink(place.kind, place.arr_id, place.season, place.episode),
        note: (place.kind === 'show' && place.season ? episodeName(place.season, place.episode) + ' · ' : '') + left(place) }));
    }
    const ready = items.filter(onServer);
    const filed = (item) => item.title.replace(/^(the|a|an)\s+/i, '');  // "The Kid" sits under K, as on a shelf
    const byTitle = (first, second) => filed(first).localeCompare(filed(second), undefined, { sensitivity: 'base' });
    const newest = [...ready].sort((first, second) => (second.added || '').localeCompare(first.added || '')).slice(0, 12);
    const coming = items.filter((item) => !onServer(item) && (item.monitored || item.downloading));
    const waitingNote = (item) => { const state = mediaState(item); return state ? state.textContent : ''; };
    setMain(topBar(true),
      appState.media.jellyfin ? null : noPlayer(),
      problems.length ? el('p', { class: 'warn', role: 'status', text: problems.join(' ') }) : null,
      row('Carry on watching', 'Where the household left off', carryOn),
      row('New on the server', '', newest.map((item) => card(item))),
      row('Films', plural(ready.filter((item) => item.kind === 'film').length, 'film'), ready.filter((item) => item.kind === 'film').sort(byTitle).map((item) => card(item))),
      row('TV shows', plural(ready.filter((item) => item.kind === 'show').length, 'show'), ready.filter((item) => item.kind === 'show').sort(byTitle).map((item) => card(item))),
      row('On the way', 'Asked for, not on the server yet', coming.map((item) => card(item, { note: waitingNote(item) }))),
      items.length ? null : el('section', { class: 'panel cut' }, head('Nothing here yet'),
        el('p', { text: isAdmin() || canRequestMedia() ? 'Nothing has been asked for yet. Search for a film or show to start.' : 'Nothing is on the server yet. Ask someone who can request films and TV.' }),
        isAdmin() || canRequestMedia() ? el('div', { class: 'row' }, el('a', { class: 'btn pri', href: '#/watch/search', text: 'Search and ask' })) : null));
    const first = main().querySelector('.wcard, .btn.pri');
    if (first) first.focus({ preventScroll: true });
  }

  // ---------------------------------------------------------------- one film or show

  // Every episode of a show in order, with its season: [{ season, number, title, on_server, watch_id, ... }]
  const episodesOf = (where) => (where.seasons || []).flatMap((season) => season.episodes.map((episode) => Object.assign({ season: season.number }, episode)));
  // What to play when someone presses Play on a show: the episode they are part-way through, else the one after
  // the last they finished, else the first that is on the server.
  function nextUp(episodes, places) {
    const playable = episodes.filter((episode) => episode.on_server && episode.watch_id);
    const placeOf = (episode) => places.find((place) => same(place.item, episode.watch_id));
    const latest = playable.map((episode) => [episode, placeOf(episode)]).filter(([, place]) => place)
      .sort(([, first], [, second]) => (second.updated || '').localeCompare(first.updated || ''))[0];
    if (!latest) return playable[0] ? { episode: playable[0], place: null } : null;
    const [episode, place] = latest;
    if (!place.finished) return { episode, place: place.at >= WORTH_RESUMING ? place : null };
    const after = playable[playable.indexOf(episode) + 1];
    return after ? { episode: after, place: null } : { episode: playable[0], place: null, again: true };
  }

  async function pageTitle(kind, id) {
    const item = await api('GET', '/api/media/item/' + kind + '/' + id);
    const [where, kept] = await Promise.all([soft(api('GET', '/api/media/watch/' + kind + '/' + id), {}), soft(api('GET', '/api/media/places'), { places: [] })]);
    nameThePage(item.title);
    const facts = [item.year, KIND_WORD[kind], item.runtime ? item.runtime + ' min' : '', item.network].filter(Boolean).join(' · ');
    const actions = el('div', { class: 'stack' });
    const waitWords = appState.media.jellyfin ? 'It is on the server, but not ready to play yet. It usually is within a few minutes.'
      : 'It is on the server. Playing it here needs Jellyfin connected' + (isAdmin() ? ' under Admin, Hub, Connections.' : '; ask an admin.');
    let seasons = null;
    if (kind === 'film') {
      const place = where.watch_id ? kept.places.find((entry) => same(entry.item, where.watch_id)) : null;
      if (!item.downloaded) fill(actions, mediaState(item), el('p', { class: 'small dim', text: 'It can be watched here as soon as it has been downloaded.' }));
      else if (!where.watch_id) fill(actions, el('p', { class: 'dim', role: 'status', text: waitWords }));
      else if (place && !place.finished && place.at >= WORTH_RESUMING) fill(actions,
        bar(100 * place.at / place.length, 'Watched ' + spoken(place.at) + ' of ' + spoken(place.length)),
        el('div', { class: 'row' }, el('a', { class: 'btn pri wplay', href: playLink('film', id) }, icon(ICON.play, 16), 'Carry on from ' + clock(place.at)),
          el('a', { class: 'btn', href: playLink('film', id, 0, 0, true), text: 'Start from the beginning' })));
      else fill(actions, el('div', { class: 'row' }, el('a', { class: 'btn pri wplay', href: playLink('film', id, 0, 0, !!(place && place.finished)) }, icon(ICON.play, 16),
        place && place.finished ? 'Watch again' : 'Play'), place && place.finished ? el('span', { class: 'small ok', text: 'Watched' }) : null));
    } else {
      const episodes = episodesOf(where), up = nextUp(episodes, kept.places);
      if (up) fill(actions, el('div', { class: 'row' }, el('a', { class: 'btn pri wplay', href: playLink('show', id, up.episode.season, up.episode.number, !!up.again) }, icon(ICON.play, 16),
        (up.place ? 'Carry on with ' : up.again ? 'Watch again from ' : 'Play ') + episodeName(up.episode.season, up.episode.number)),
        el('span', { class: 'small dim', text: up.episode.title + (up.place ? ' · ' + left(up.place) : '') })));
      else fill(actions, mediaState(item), episodes.some((episode) => episode.on_server) ? el('p', { class: 'dim', role: 'status', text: waitWords })
        : el('p', { class: 'small dim', text: 'Episodes can be watched here as each one is downloaded.' }));
      seasons = seasonList(id, where, kept.places, up);
    }
    setMain(topBar(), back('#/watch', 'Back to Watch'),
      el('div', { class: 'wtitle', style: '--backdrop:url(/api/media/backdrop/' + kind + '/' + id + ')' },
        el('div', { class: 'cover' + (item.poster ? ' has-img' : '') + titleSize(item.title),
            style: '--cv:' + coverColor(item.title) + (item.poster ? ';background-image:url(' + item.poster + ')' : '') },
          el('span', { class: 'fmt', text: kind === 'film' ? 'Film' : 'TV' }), el('span', { class: 't', text: item.title })),
        el('div', { class: 'stack wtitle-words' }, el('h1', { class: 'glow', text: item.title }), el('p', { class: 'dim', text: facts }),
          item.genres.length ? el('p', { class: 'lab', text: item.genres.join(' · ') }) : null,
          actions,
          item.overview ? el('p', { class: 'synopsis', text: item.overview }) : null,
          el('p', { class: 'small' }, el('a', { href: '#/movies/' + kind + '/' + id, text: 'Downloads, versions and more' }), ' (leaves Watch mode)'))),
      seasons);
    const first = main().querySelector('.wplay') || main().querySelector('.back');
    if (first) first.focus({ preventScroll: true });
  }

  function seasonList(id, where, places, up) {
    if (!(where.seasons || []).length) return el('section', { class: 'panel cut' }, head('Seasons and episodes'),
      el('p', { class: 'small dim', text: 'No episodes are listed for this show yet.' }));
    let pick = up ? up.episode.season : where.seasons[0].number;
    const chips = el('div', { class: 'row gap6', role: 'group', 'aria-label': 'Season' });
    const list = el('div', { class: 'list wepisodes' });
    function draw() {
      fill(chips, where.seasons.map((option) => {
        const have = option.episodes.filter((episode) => episode.on_server).length;
        return el('button', { class: 'chip', type: 'button', 'aria-pressed': String(option.number === pick), onclick: () => { pick = option.number; draw(); } },
          'Season ' + option.number, el('span', { class: 'src', text: have + '/' + option.episodes.length }));
      }));
      fill(list, where.seasons.find((season) => season.number === pick).episodes.map((episode) => {
        const place = episode.watch_id ? places.find((entry) => same(entry.item, episode.watch_id)) : null;
        const partWay = place && !place.finished && place.at >= WORTH_RESUMING;
        const coming = episode.downloading !== null && episode.downloading !== undefined;
        const state = episode.on_server ? (episode.watch_id ? (place && place.finished ? 'Watched' : partWay ? left(place) : '') : 'On the server, ready to play in a few minutes')
          : coming ? 'Downloading · ' + episode.downloading + '%' : episode.wanted ? 'Being looked for' : 'Not asked for';
        const words = [el('strong', { text: episode.number + '. ' + (episode.title || 'Episode ' + episode.number) }),
          el('span', { class: 'small dim', text: [episode.aired ? niceDate(episode.aired) : 'Not aired yet', state].filter(Boolean).join(' · ') }),
          partWay ? bar(100 * place.at / place.length, 'Watched ' + spoken(place.at) + ' of ' + spoken(place.length)) : null];
        return episode.on_server && episode.watch_id
          ? el('a', { class: 'wep', href: playLink('show', id, pick, episode.number, !!(place && place.finished)), 'aria-label': 'Play episode ' + episode.number + ', ' + (episode.title || '') + (state ? ', ' + state : '') },
              el('span', { class: 'wep-play', 'aria-hidden': 'true' }, icon(ICON.play, 16)), el('span', { class: 'stack wep-words' }, words))
          : el('div', { class: 'wep off' }, el('span', { class: 'wep-play', 'aria-hidden': 'true' }), el('span', { class: 'stack wep-words' }, words));
      }));
    }
    draw();
    return el('section', { class: 'panel cut' }, head('Seasons and episodes'), chips, list);
  }

  // ---------------------------------------------------------------- search and ask

  async function pageSearch() {
    nameThePage('Search and ask');
    if (!isAdmin() && !canRequestMedia()) {
      setMain(topBar(), back('#/watch', 'Back to Watch'), el('p', { class: 'dim', text: 'This profile can watch but not ask for new films and shows. Ask an admin.' }));
      return;
    }
    const results = el('div', { class: 'stack' });
    const kinds = [['film', 'Films'], ['show', 'TV shows']].filter(([kind]) => (kind === 'film' ? appState.media.films : appState.media.shows));
    if (!kinds.length) { setMain(topBar(), back('#/watch', 'Back to Watch'), notSetUp('Films and TV are not set up yet', 'Asking for films and TV needs Radarr and Sonarr connected.')); return; }
    let kind = kinds[0][0], run = 0;
    const box = el('input', { type: 'search', placeholder: 'Such as Paddington or Bluey' });
    const chips = el('div', { class: 'row gap6', role: 'group', 'aria-label': 'Look for' });
    const drawChips = () => fill(chips, kinds.map(([key, label]) => el('button', { class: 'chip', type: 'button', 'aria-pressed': String(kind === key), text: label,
      onclick: () => { kind = key; drawChips(); if (box.value.trim().length >= 2) search(); } })));
    async function search() {
      const words = box.value.trim();
      if (words.length < 2) { fill(results, el('p', { class: 'small dim', role: 'status', text: 'Type at least two letters.' })); box.focus(); return; }
      const mine = ++run, wanted = kind;
      const stop = searchProgress(results, words, { sites: false, what: 'Looking up titles' });
      try {
        const data = await api('GET', '/api/media/search?' + qs({ kind: wanted, q: words })).finally(stop);
        if (mine !== run) return;
        fill(results, data.results.length ? el('div', { class: 'shelf' }, data.results.map((result) => posterCard(Object.assign({ kind: wanted }, result, { id: null }),
          el('div', { class: 'stack gap6' }, result.overview ? el('div', { class: 'small dim', text: result.overview.length > 120 ? result.overview.slice(0, 120) + '…' : result.overview }) : null,
            result.in_library ? (result.arr_id ? el('a', { class: 'btn sm', href: '#/watch/' + wanted + '/' + result.arr_id, text: 'Already here: open it' })
              : el('div', { class: 'small ok', text: 'Already on the list' })) : requestControls(wanted, result)))))
          : el('p', { class: 'small dim', role: 'status', text: 'Nothing found. Try fewer words.' }));
      } catch (err) { if (!(err instanceof Stale) && mine === run) fill(results, el('p', { class: 'bad', role: 'status', text: err.message })); }
    }
    drawChips();
    setMain(topBar(), back('#/watch', 'Back to Watch'),
      el('section', { class: 'panel cut' }, head('Search for a film or show', el('span', { class: 'small dim', text: 'Asked for here, downloaded to the server' })),
        chips,
        el('form', { class: 'row end', role: 'search', onsubmit: (event) => { event.preventDefault(); search(); } },
          field('Title', box, 'grow'), el('button', { class: 'btn pri', type: 'submit', text: 'Search' })),
        el('p', { class: 'small dim', text: 'What you ask for shows under On the way until it is ready to watch.' }),
        results));
    box.focus();
  }

  // ---------------------------------------------------------------- the player

  // What this browser can play, for Jellyfin to decide between passing the file on and converting it.
  function canPlay() {
    const probe = document.createElement('video');
    const plays = (type) => !!probe.canPlayType(type).replace('no', '');
    const source = window.ManagedMediaSource || window.MediaSource;
    const pieces = (type) => !!(source && source.isTypeSupported && source.isTypeSupported(type));
    const HEVC = 'video/mp4; codecs="hvc1.2.4.L153.B0"';  // 10-bit, as 2160p films are
    const video = [['h264', plays('video/mp4; codecs="avc1.640029"')], ['hevc', plays(HEVC) && (apple() || pieces(HEVC))],
      ['av1', plays('video/mp4; codecs="av01.0.08M.08"')], ['vp9', plays('video/webm; codecs="vp9"')], ['vp8', plays('video/webm; codecs="vp8"')]];
    const audio = [['aac', plays('audio/mp4; codecs="mp4a.40.2"')], ['mp3', plays('audio/mpeg')], ['opus', plays('audio/webm; codecs="opus"') || plays('audio/mp4; codecs="opus"')],
      ['flac', plays('audio/flac') || plays('audio/mp4; codecs="flac"')], ['vorbis', plays('audio/webm; codecs="vorbis"')],
      ['ac3', plays('audio/mp4; codecs="ac-3"')], ['eac3', plays('audio/mp4; codecs="ec-3"')]];
    const names = (list) => list.filter(([, yes]) => yes).map(([name]) => name);
    return { video: names(video), audio: names(audio), containers: ['mp4'].concat(plays('video/webm; codecs="vp9"') || plays('video/webm; codecs="vp8"') ? ['webm'] : []) };
  }
  const apple = () => /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent);
  let hlsLoading = null;
  function loadHls() {
    if (window.Hls) return Promise.resolve();
    if (!hlsLoading) hlsLoading = new Promise((resolve, reject) => {
      const script = el('script', { src: '/static/vendor/hls.min.js' });
      script.onload = resolve;
      script.onerror = () => { hlsLoading = null; reject(new Error('The player could not be loaded.')); };
      document.head.append(script);
    });
    return hlsLoading;
  }

  async function pagePlay(kind, id, season, episode, fromStart) {
    const [item, where] = await Promise.all([api('GET', '/api/media/item/' + kind + '/' + id), api('GET', '/api/media/watch/' + kind + '/' + id)]);
    const titlePage = '#/watch/' + kind + '/' + id;
    let watchId = where.watch_id, heading = item.title, sub = '', next = null;
    if (kind === 'show') {
      const playable = episodesOf(where).filter((entry) => entry.on_server && entry.watch_id);
      const now = playable.find((entry) => entry.season === season && entry.number === episode);
      watchId = now ? now.watch_id : null;
      sub = now ? episodeName(season, episode) + (now.title ? ' · ' + now.title : '') : '';
      next = now ? playable[playable.indexOf(now) + 1] || null : null;
    }
    nameThePage(heading + (sub ? ' ' + sub.split(' · ')[0] : ''));
    if (!watchId) {
      setMain(topBar(), back(titlePage, 'Back to ' + item.title), el('section', { class: 'panel cut', role: 'status' }, head('Not ready to play'),
        el('p', { text: appState.media.jellyfin ? 'This is not ready to play yet. If it has only just been downloaded, it usually is within a few minutes.'
          : 'Playing here needs Jellyfin connected' + (isAdmin() ? ' under Admin, Hub, Connections.' : '. Ask an admin.') })));
      return;
    }

    const gen = routeGen;
    const video = el('video', { playsinline: true, preload: 'auto', 'aria-label': heading + (sub ? ', ' + sub : '') });
    const status = el('p', { class: 'wp-status', role: 'status' });
    const seek = el('input', { type: 'range', class: 'wp-seek', min: '0', max: '0', step: '1', value: '0', 'aria-label': 'Place in the ' + (kind === 'film' ? 'film' : 'episode') });
    const time = el('span', { class: 'wp-time', 'aria-hidden': 'true', text: '0:00' });
    const button = (name, label, key, onclick, more) => el('button', Object.assign({ class: 'wp-btn', type: 'button', 'aria-label': label, title: label + (key ? ' (' + key + ')' : ''), onclick }, more || {}), icon(ICON[name]));
    const playBtn = button('play', 'Play', 'Space', () => toggle());
    const muteBtn = button('sound', 'Mute', 'M', () => { video.muted = !video.muted; });
    const subsBtn = button('subs', 'Subtitles', 'C', () => openMenu(subsBtn, subsMenu), { 'aria-haspopup': 'true', 'aria-expanded': 'false', hidden: true });
    const soundBtn = el('button', { class: 'wp-btn words', type: 'button', 'aria-haspopup': 'true', 'aria-expanded': 'false', hidden: true, text: 'Language', onclick: () => openMenu(soundBtn, soundMenu) });
    const subsMenu = el('div', { class: 'wp-menu', role: 'menu', 'aria-label': 'Subtitles', hidden: true });
    const soundMenu = el('div', { class: 'wp-menu', role: 'menu', 'aria-label': 'Language', hidden: true });
    const nextBtn = next ? el('a', { class: 'wp-btn words', href: playLink('show', id, next.season, next.number, true), title: 'Next episode (N)' }, icon(ICON.next), 'Next episode') : null;
    const fullBtn = paintFull(el('button', { class: 'wp-btn', type: 'button', 'data-full': 'icon', onclick: () => toggleFull(video) }));
    const overlay = el('div', { class: 'wp-over', hidden: true });
    const controls = el('div', { class: 'wp-controls' },
      el('div', { class: 'wp-seekrow' }, seek),
      el('div', { class: 'wp-row' }, playBtn, button('back', 'Back ' + SKIP_BACK + ' seconds', '←', () => skip(-SKIP_BACK)), button('on', 'Forward ' + SKIP_ON + ' seconds', '→', () => skip(SKIP_ON)),
        time, el('span', { class: 'wp-gap' }), nextBtn, el('span', { class: 'wp-pop' }, subsBtn, subsMenu), el('span', { class: 'wp-pop' }, soundBtn, soundMenu), muteBtn, fullBtn));
    const stage = el('div', { class: 'wplayer', 'data-theme': 'dark' }, video,
      el('div', { class: 'wp-head' }, el('a', { class: 'wp-btn words', href: titlePage, 'aria-label': 'Back to ' + item.title }, icon(ICON.leave), 'Back'),
        el('div', { class: 'wp-name' }, el('strong', { text: heading }), sub ? el('span', { text: sub }) : null)),
      status, overlay, controls);

    let info = null, hls = null, length = 0, settled = false, ended = false, mended = false, quietTimer = null, keepTimer = null, countdown = null, stallTimer = null;
    let chosenAudio = null, closed = false;
    const total = () => (Number.isFinite(video.duration) && video.duration > 0 ? video.duration : length);
    const say = (words) => { status.textContent = words || ''; status.hidden = !words; };

    // A film that stays stuck (the server stopped converting, the network dropped) says so, with a way to try again,
    // rather than showing "getting ready" for ever. The player library retries by itself well within this time.
    function expectPicture() {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => { if (overlay.hidden && !video.paused) failed('The film stopped arriving from the family server.'); }, STALLED_AFTER);
    }
    const gotPicture = () => clearTimeout(stallTimer);

    function keepPlace(closing) {
      const all = total();
      // Once the player is being put away the video is emptied and reads 0:00; that must never be kept as the place.
      if (closed || !settled || !all || !info) return;
      tell('/api/media/play/' + watchId + '/place', { at: ended ? all : video.currentTime, length: all, kind, arr_id: id,
        season: kind === 'show' ? season : null, episode: kind === 'show' ? episode : null, title: item.title }, closing);
    }
    function dropStream(closing) {
      if (hls) { hls.destroy(); hls = null; }
      video.removeAttribute('src');
      for (const track of [...video.querySelectorAll('track')]) track.remove();
      video.load();
      if (info && info.kind === 'hls' && info.session) tell('/api/media/play/' + watchId + '/stop', { session: info.session }, closing);
    }

    // ---- starting, and starting again (another language, or after a problem)
    async function begin(audio, at) {
      overlay.hidden = true; ended = false; settled = false;
      say('Getting it ready…');
      expectPicture();
      if (info) dropStream();
      let got;
      try { got = await api('POST', '/api/media/play/' + watchId + '/start', { can: canPlay(), audio: audio === null ? undefined : audio }); }
      catch (err) { if (!(err instanceof Stale)) failed(err.message); return; }
      if (gen !== routeGen) return;
      info = got; length = got.length; chosenAudio = audio;
      const startAt = at !== undefined ? at : fromStart ? 0 : got.resume;
      seek.max = String(Math.floor(length)); draw();
      for (const entry of got.subtitles) video.append(el('track', { kind: 'subtitles', label: entry.label, srclang: entry.lang || 'en', src: entry.url }));
      drawMenus();
      if (got.kind === 'hls') {
        const own = video.canPlayType('application/vnd.apple.mpegurl');
        try { if (!(own && apple())) await loadHls(); } catch (err) { /* the browser's own player may still manage */ }
        if (gen !== routeGen) return;
        if (window.Hls && window.Hls.isSupported() && !(own && apple())) {
          hls = new window.Hls({ startPosition: startAt > 0 ? startAt : -1, maxBufferLength: 30, maxMaxBufferLength: 120 });
          hls.on(window.Hls.Events.ERROR, (event, data) => {
            if (!data.fatal) return;
            if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && !mended) { mended = true; hls.recoverMediaError(); }
            else failed('The film stopped arriving from the family server.');
          });
          hls.loadSource(got.url);
          hls.attachMedia(video);
        } else if (own) { video.src = got.url; jumpWhenLoaded(startAt); }
        else { failed('This browser cannot play films that need converting. Try another browser on this device.'); return; }
      } else { video.src = got.url; jumpWhenLoaded(startAt); }
      if (startAt >= WORTH_RESUMING) say('Carrying on from ' + clock(startAt));
      video.play().catch(() => { say(''); wake(); });  // a browser that will not start by itself: the Play button is right there
    }
    function jumpWhenLoaded(at) {
      video.addEventListener('loadedmetadata', () => { if (at > 0) video.currentTime = Math.min(at, Math.max(0, total() - 5)); settled = true; }, { once: true });
    }
    function failed(words) {
      say(''); gotPicture();
      video.pause();
      overlay.hidden = false;
      const at = settled ? video.currentTime : undefined;
      fill(overlay, el('div', { class: 'wp-card', role: 'alert' }, el('strong', { text: 'This could not be played' }), el('p', { text: words }),
        el('div', { class: 'row' }, el('button', { class: 'btn pri', type: 'button', text: 'Try again', onclick: () => begin(chosenAudio, at) }),
          el('a', { class: 'btn', href: titlePage, text: 'Back to ' + item.title }))));
      overlay.querySelector('button').focus();
      wake();
    }

    // ---- the controls
    function toggle() { if (video.paused || video.ended) video.play().catch(() => {}); else video.pause(); }
    function skip(by) {
      const all = total();
      if (!all) return;
      video.currentTime = Math.max(0, Math.min(all - 1, video.currentTime + by));
      draw(); wake();
    }
    let dragging = false;
    function draw() {
      const all = total(), at = dragging ? Number(seek.value) : video.currentTime || 0;
      if (all && Number(seek.max) !== Math.floor(all)) seek.max = String(Math.floor(all));
      if (!dragging) seek.value = String(Math.floor(at));
      let loaded = 0;
      for (let index = 0; index < video.buffered.length; index++) if (video.buffered.start(index) <= at + 1) loaded = Math.max(loaded, video.buffered.end(index));
      seek.style.setProperty('--played', (all ? 100 * at / all : 0) + '%');
      seek.style.setProperty('--loaded', (all ? 100 * Math.max(at, loaded) / all : 0) + '%');
      seek.setAttribute('aria-valuetext', spoken(at) + ' of ' + spoken(all));
      time.textContent = clock(at) + ' / ' + clock(all);
    }
    seek.addEventListener('input', () => { dragging = true; draw(); wake(); });
    seek.addEventListener('change', () => { video.currentTime = Number(seek.value); dragging = false; draw(); });
    function showPlaying() {
      const paused = video.paused || video.ended;
      playBtn.replaceChildren(icon(paused ? ICON.play : ICON.pause));
      playBtn.setAttribute('aria-label', paused ? 'Play' : 'Pause'); playBtn.title = (paused ? 'Play' : 'Pause') + ' (Space)';
      muteBtn.replaceChildren(icon(video.muted ? ICON.muted : ICON.sound));
      muteBtn.setAttribute('aria-label', video.muted ? 'Sound on' : 'Mute'); muteBtn.setAttribute('aria-pressed', String(video.muted));
    }
    // The controls stay while paused, while a menu is open, or while something is being said; otherwise they slip away.
    function wake() {
      stage.removeAttribute('data-quiet');
      clearTimeout(quietTimer);
      quietTimer = setTimeout(() => {
        if (video.paused || !overlay.hidden || !subsMenu.hidden || !soundMenu.hidden || dragging) return;
        stage.setAttribute('data-quiet', '');
      }, QUIET_AFTER);
    }
    const quiet = () => stage.hasAttribute('data-quiet');

    // ---- subtitles and languages
    function closeMenus() {
      for (const [opener, menu] of [[subsBtn, subsMenu], [soundBtn, soundMenu]]) { menu.hidden = true; opener.setAttribute('aria-expanded', 'false'); }
    }
    function openMenu(opener, menu) {
      const open = menu.hidden;
      closeMenus();
      if (!open) return;
      menu.hidden = false; opener.setAttribute('aria-expanded', 'true');
      (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button')).focus();
      wake();
    }
    const menuItem = (label, on, onclick) => el('button', { type: 'button', role: 'menuitemradio', 'aria-checked': String(on), text: label, onclick });
    function pickSubtitles(index, fromMenu) {
      [...video.textTracks].forEach((track, position) => { track.mode = position === index ? 'showing' : 'disabled'; });
      try { localStorage.setItem('watch-subtitles', index < 0 ? '' : (info.subtitles[index].lang || 'on')); } catch (err) { /* private browsing */ }
      drawMenus(); closeMenus();
      if (fromMenu) subsBtn.focus();
    }
    function drawMenus() {
      const showing = [...video.textTracks].findIndex((track) => track.mode === 'showing');
      subsBtn.hidden = !info.subtitles.length;
      fill(subsMenu, menuItem('Off', showing < 0, () => pickSubtitles(-1, true)), info.subtitles.map((entry, index) => menuItem(entry.label, showing === index, () => pickSubtitles(index, true))));
      soundBtn.hidden = info.audio.length < 2;
      fill(soundMenu, info.audio.map((entry) => menuItem(entry.label, entry.chosen, () => {
        closeMenus(); soundBtn.focus();
        if (!entry.chosen) begin(entry.index, video.currentTime);
      })));
    }
    // Subtitles stay as they were last time (off, or that language), on this device.
    video.addEventListener('loadedmetadata', () => {
      let wanted = '';
      try { wanted = localStorage.getItem('watch-subtitles') || ''; } catch (err) { /* private browsing */ }
      const index = wanted ? Math.max(info.subtitles.findIndex((entry) => entry.lang === wanted), wanted === 'on' ? 0 : -1) : -1;
      [...video.textTracks].forEach((track, position) => { track.mode = position === index ? 'showing' : 'disabled'; });
      drawMenus();
    });

    // ---- what the video says back
    video.addEventListener('timeupdate', () => { if (!dragging) draw(); });
    video.addEventListener('progress', draw);
    video.addEventListener('durationchange', draw);
    video.addEventListener('playing', () => { say(''); gotPicture(); showPlaying(); wake(); settled = true; });
    video.addEventListener('play', () => { showPlaying(); wake(); });
    video.addEventListener('pause', () => { gotPicture(); showPlaying(); wake(); if (!ended) keepPlace(); });
    video.addEventListener('volumechange', showPlaying);
    video.addEventListener('waiting', () => { if (!video.paused) { say('Getting the next part ready…'); expectPicture(); } });
    video.addEventListener('canplay', () => { gotPicture(); if (status.textContent.startsWith('Getting')) say(''); });
    video.addEventListener('error', () => { if (!hls && video.error) failed('The film stopped arriving from the family server, or this browser cannot play it.'); });
    video.addEventListener('ended', () => {
      // A stream that gives out part-way can look like the end to the browser. Only the real end counts as watched.
      if (length > 0 && video.currentTime < length * 0.9) { failed('The film stopped arriving from the family server.'); return; }
      ended = true; keepPlace(); showPlaying(); wake();
      if (!next) { location.hash = titlePage; return; }
      let remaining = UP_NEXT;
      const count = el('p', { 'aria-live': 'off' });
      const tick = () => { count.textContent = 'Starts in ' + plural(remaining, 'second') + '.'; };
      const stopCount = () => { clearInterval(countdown); countdown = null; };
      overlay.hidden = false;
      fill(overlay, el('div', { class: 'wp-card' }, el('span', { class: 'lab', text: 'Up next' }),
        el('strong', { text: episodeName(next.season, next.number) + (next.title ? ' · ' + next.title : '') }), count,
        el('div', { class: 'row' }, el('a', { class: 'btn pri', href: playLink('show', id, next.season, next.number, true), text: 'Play now' }),
          el('button', { class: 'btn', type: 'button', text: 'Not now', onclick: () => { stopCount(); location.hash = titlePage; } }))));
      overlay.querySelector('a').focus();
      tick();
      countdown = setInterval(() => { remaining -= 1; tick(); if (remaining <= 0) { stopCount(); location.hash = playLink('show', id, next.season, next.number, true); } }, 1000);
    });
    video.addEventListener('click', () => { toggle(); wake(); });
    video.addEventListener('dblclick', () => toggleFull(video));
    stage.addEventListener('pointermove', wake);
    stage.addEventListener('pointerdown', (event) => { if (!event.target.closest('.wp-pop')) closeMenus(); wake(); });
    stage.addEventListener('focusin', wake);

    // ---- keys: a keyboard, or a TV remote's arrows and OK
    function onKey(event) {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || document.querySelector('dialog[open]')) return;
      const target = event.target, key = event.key;
      const onControl = target.closest && target.closest('.wp-controls button, .wp-controls a, .wp-head a, .wp-over button, .wp-over a');
      const inMenu = target.closest && target.closest('.wp-menu');
      const wasQuiet = quiet();
      wake();
      if (key === 'Escape') {
        if (!subsMenu.hidden || !soundMenu.hidden) { const opener = subsMenu.hidden ? soundBtn : subsBtn; closeMenus(); opener.focus(); event.preventDefault(); }
        else if (!fullNow()) { event.preventDefault(); location.hash = titlePage; }
        return;
      }
      if (!overlay.hidden) { if (ARROWS[key] && step(ARROWS[key], overlay)) event.preventDefault(); return; }
      if (inMenu) {
        if (key === 'ArrowDown' || key === 'ArrowUp') { step(key === 'ArrowDown' ? 'down' : 'up', inMenu); event.preventDefault(); }
        return;
      }
      if (key === ' ' || key === 'Enter') {
        // OK on a remote with the controls away, or on the picture itself: play or pause. On a button, the button's own job.
        if (onControl && !wasQuiet) return;
        if (key === 'Enter' && target === seek) return;
        event.preventDefault(); toggle(); return;
      }
      const letter = key.length === 1 ? key.toLowerCase() : key;
      if (letter === 'k' || key === 'MediaPlayPause') { event.preventDefault(); toggle(); }
      else if (letter === 'f') { event.preventDefault(); toggleFull(video); }
      else if (letter === 'm') { event.preventDefault(); video.muted = !video.muted; }
      else if (letter === 'j') { event.preventDefault(); skip(-SKIP_BACK); }
      else if (letter === 'l') { event.preventDefault(); skip(SKIP_ON); }
      else if (letter === 'c' && info && info.subtitles.length) {
        event.preventDefault();
        const showing = [...video.textTracks].findIndex((track) => track.mode === 'showing');
        pickSubtitles(showing < 0 ? 0 : -1);
      }
      else if (letter === 'n' && nextBtn) { event.preventDefault(); location.hash = nextBtn.getAttribute('href'); }
      else if (key === 'Backspace' || key === 'BrowserBack') { event.preventDefault(); location.hash = titlePage; }
      else if (key === 'ArrowLeft' || key === 'ArrowRight') {
        // With the controls away, or on the picture or the seek bar: move through the film. On a button: along the row.
        event.preventDefault();
        if (onControl && !wasQuiet) step(key === 'ArrowLeft' ? 'left' : 'right', stage);
        else skip(key === 'ArrowLeft' ? -SKIP_BACK : SKIP_ON);
      }
      else if (key === 'ArrowDown' || key === 'ArrowUp') {
        event.preventDefault();
        if (!onControl && target !== seek) { (key === 'ArrowUp' ? seek : playBtn).focus(); return; }
        if (target === seek) { (key === 'ArrowDown' ? playBtn : stage.querySelector('.wp-head a')).focus(); return; }
        if (!step(key === 'ArrowUp' ? 'up' : 'down', stage) && key === 'ArrowUp') seek.focus();
      }
    }
    document.addEventListener('keydown', onKey);
    const onHide = () => { keepPlace(true); };
    window.addEventListener('pagehide', onHide);

    document.body.classList.add('wp-open');
    setMain(stage);
    showPlaying(); wake();
    playBtn.focus({ preventScroll: true });
    keepTimer = setInterval(() => { if (!video.paused && !ended) keepPlace(); }, KEEP_EVERY);
    leave = () => {
      leave = null;
      clearInterval(keepTimer); clearInterval(countdown); clearTimeout(quietTimer); clearTimeout(stallTimer);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('pagehide', onHide);
      if (!ended) keepPlace(true);
      closed = true;
      video.pause();
      dropStream(true);
      document.body.classList.remove('wp-open');
    };
    begin(null);
  }

  // ---------------------------------------------------------------- which screen

  // Any move to another screen puts the player away first. Leaving Watch mode altogether leaves full screen too.
  window.addEventListener('hashchange', () => {
    if (leave) leave();
    if (!location.hash.startsWith('#/watch') && fullNow()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  });

  async function page() {
    if (leave) leave();
    const parts = location.hash.replace(/^#\/?/, '').split('/');  // watch, then the rest
    const number = (text) => (/^\d{1,9}$/.test(text || '') ? Number(text) : null);
    if (parts[1] === 'search') return pageSearch();
    if ((parts[1] === 'film' || parts[1] === 'show') && number(parts[2]) !== null) return pageTitle(parts[1], number(parts[2]));
    if (parts[1] === 'play' && parts[2] === 'film' && number(parts[3]) !== null) return pagePlay('film', number(parts[3]), null, null, parts[4] === 'start');
    if (parts[1] === 'play' && parts[2] === 'show' && [parts[3], parts[4], parts[5]].every((part) => number(part) !== null)) {
      return pagePlay('show', number(parts[3]), number(parts[4]), number(parts[5]), parts[6] === 'start');
    }
    return pageHome();
  }

  return { page, playLink };
})();

const pageWatch = () => Watch.page();
