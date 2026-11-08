// Family Hub: Kindle export. Run on your Kindle library page (read.amazon.com.au/kindle-library), signed in:
// open the browser console (Ctrl+Shift+J in Chrome), paste all of this and press Enter. It saves
// kindle-reading.json; bring that file in under Dashboard, Kindle in Family Hub.
//
// It only reads. For each book it asks Amazon's web reader for the furthest place read (Amazon keeps that in
// step with the Kindle app on a phone or iPad); it does not open or turn any pages, so your place stays put.
// Amazon may count each book as looked at, which can change the order of your "Recent" list.
(async () => {
  const PAGE = 50;
  const say = (text) => console.log('%cFamily Hub%c ' + text, 'font-weight:bold', '');
  if (!/read\.amazon\./.test(location.host)) { say('Run this on your Kindle library page: https://read.amazon.com.au/kindle-library'); return; }

  // 1. every book in the library, 50 at a time
  const items = [];
  let token = '0';
  for (let pages = 0; token && pages < 200; pages++) {
    const reply = await fetch(location.origin + '/kindle-library/search?query=&libraryType=BOOKS&sortType=recency&querySize=' + PAGE
      + '&paginationToken=' + encodeURIComponent(token), { credentials: 'include' });
    if (!reply.ok) { say('Amazon would not list the library (' + reply.status + '). Are you signed in?'); return; }
    const data = await reply.json();
    items.push(...(data.itemsList || []));
    if (!data.paginationToken || data.paginationToken === token || !(data.itemsList || []).length) break;
    token = data.paginationToken;
  }
  if (!items.length) { say('No books found. Are you signed in?'); return; }
  say(items.length + ' books. Now asking for the place reached in each (about two seconds a book)…');

  // 2. the furthest place read, from the web reader's page for the book (the page itself, not the reader)
  const books = [];
  for (const [index, item] of items.entries()) {
    let position = null;
    try {
      const html = await (await fetch(item.webReaderUrl || (location.origin + '/?asin=' + item.asin), { credentials: 'include' })).text();
      // the page writes it as \x22mostRecentPositionRead\x22:2523, its quotes escaped (and "22" is not the place)
      const found = html.match(/mostRecentPositionRead(?:\\x22|\\"|")\s*:\s*(\d+)/);
      if (found) position = Number(found[1]);
    } catch (err) { /* left blank: Family Hub keeps the place it had */ }
    books.push({ asin: item.asin, title: item.title, authors: item.authors || [], origin: item.originType || '', position });
    if ((index + 1) % 10 === 0) say((index + 1) + ' of ' + items.length + '…');
    await new Promise((done) => setTimeout(done, 400));  // gently: one book at a time
  }

  // 3. the file for Family Hub
  const file = new Blob([JSON.stringify({ source: 'kindle-web', exported: new Date().toISOString(), books }, null, 1)], { type: 'application/json' });
  const link = Object.assign(document.createElement('a'), { href: URL.createObjectURL(file), download: 'kindle-reading.json' });
  document.body.append(link); link.click(); link.remove();
  const started = books.filter((book) => book.position > 1).length;
  say('Saved kindle-reading.json: ' + books.length + ' books, ' + started + ' started. Bring it in under Dashboard, Kindle in Family Hub.');
})();
