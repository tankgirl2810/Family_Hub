# 5. Finding and downloading new books

The Discover page searches for a book, sends your pick to your torrent
client, and the finished file is added to the library automatically.

```
Discover search ─▶ Prowlarr ─▶ your torrent client ─▶ finished-downloads folder
                                                              │ Family Hub copies ebook files
                                                              ▼
                                   library  ◀─ Calibre-Web Automated ◀─ import folder
```

Family Hub talks to **Prowlarr** for searching, and Prowlarr hands the
download to whichever client it is connected to. That is why any torrent
client Prowlarr supports will work.

> Tested so far only against a stand-in for Prowlarr (see the README).
> What you download is your responsibility: add only indexers you are
> entitled to use, and check what is legal where you live.

## A. Start Prowlarr

Skip this if the home server already runs Prowlarr; use that one.

Sites match whole words exactly, so Discover asks for a few spellings at once: as typed, without accents
and apostrophes ("Savior's Café" and "Saviors Cafe"), and for a long title without its first word or two.
Webtoons and manhwa are rarely on these sites at all; follow them chapter by chapter instead (guide 13).

```bash
docker compose --profile search up -d
```

Open <http://localhost:9696> and create its sign-in when asked.

## B. Add indexers

In Prowlarr: **Indexers → Add Indexer**. Add the ones you use. Family Hub
searches the **Books** categories (7000 and 7020), so pick indexers that
carry ebooks.

## C. Connect your torrent client to Prowlarr

In Prowlarr: **Settings → Download Clients → +** and choose your client.

- **Using the bundled qBittorrent:** start it with
  `docker compose --profile search --profile torrent up -d`. Its first
  password is printed in `docker logs qbittorrent`. In Prowlarr use host
  `qbittorrent`, port `8080`.
- **Using your own client:** enter its address and sign-in as usual.

Press **Test**, then **Save**.

## D. Tell Family Hub where finished downloads land

Family Hub watches one folder for finished ebook files (EPUB, PDF, MOBI,
AZW3 and a few others) and copies them into the library manager's import
folder. It copies rather than moves, so seeding continues.

1. In your torrent client set a **separate folder for unfinished
   downloads**. In qBittorrent: **Options → Downloads → Keep incomplete
   torrents in**, for example `/downloads/incomplete`, with the default save
   path `/downloads/complete`. Family Hub ignores folders named `incomplete`,
   `temp`, `tmp` or `downloading`, and files less than two minutes old.
2. In `.env` set `DOWNLOADS_PATH` to the folder the client saves into, as
   the server sees it. With the bundled qBittorrent, leave the default.

## E. Connect Family Hub to Prowlarr

1. In Prowlarr: **Settings → General → API Key**. Copy it.
2. In Family Hub, sign in as an admin and open **Admin → Hub → Connections → Book search (Prowlarr)**.
3. Enter the address and paste the key.
   Use `http://prowlarr:9696` when Prowlarr runs from this project's compose
   file; otherwise use its real address, for example
   `http://192.168.1.10:9696`.
4. Press **Test connection**. It tells you whether the address answered and
   whether the key was accepted. Then press **Save**. It takes effect
   straight away; nothing needs restarting.
5. Optional, for live progress bars: fill in **Download progress
   (qBittorrent)** the same way. With the bundled client the address is
   `http://qbittorrent:8080`.
6. Check **Admin → Hub → Server**: "Book search" should say Connected
   and the folders should say Found.

Keys and passwords saved here are kept on the server and are never shown
again; to change one, type the new one over it. **Remove** next to a field
forgets the saved value. They are not included in **Download book data** or
the daily backups.

The same settings can still be given in `.env` (`PROWLARR_URL`,
`PROWLARR_API_KEY`, `QBITTORRENT_URL` and so on). A value saved on the Admin
page wins over `.env`; removing it goes back to the `.env` value.

## F. Use it

1. **Discover → Search for a book.** EPUB results are listed first, then by
   seeders. Books you already own show **In library**.
2. Press **Download**. The button changes to **Sent**, and a small card in
   the bottom-right corner follows the book until it is in the library.
   **Downloads** (in the sidebar, or the tab in Books) shows every download
   with **Pause**, **Continue**, the order they download in and, for admins,
   **Delete**, which removes the download and its files (a book already in
   the library stays there; delete it from its page).
3. When the download finishes, Family Hub copies the file to the import folder
   (checked every minute) and the library manager adds it. It then appears
   in the Library for everyone. A file qBittorrent is still downloading is
   left alone, even when the torrent has stalled and the file looks full
   size, so a half-finished volume never reaches the library. A file the library
   already has, byte for byte, is not added again: it is noted in the import
   log (from the inbox it goes to the "needs attention" folder instead).

If series by chapter is switched on (guide 13), the same search also asks the
series server's reading sites, such as Webtoon. What they find appears under
**Series on reading sites**, above the torrent results. **Follow** keeps
every chapter on the server. Above the search box, **Books and novels** leaves the
reading sites out, and **Also manga and webcomic sites** asks them too. The
choice is remembered on that device for your profile.

Who can do this: admins and members. Restricted profiles cannot.

## PDFs

Two ways to turn a PDF into an EPUB:

- **On arrival (recommended):** in the library manager's own settings, turn
  on automatic conversion to EPUB for newly added books. Every PDF that
  comes in is converted there.
- **On demand in Family Hub:** build the image with the converter included,
  then use **Convert to EPUB** on a PDF book's page:

  ```bash
  docker compose build --build-arg INSTALL_CALIBRE=true bookhub
  docker compose up -d
  ```

  The converted copy is kept alongside Family Hub's data and offered as a
  second download; the original PDF is not changed.

Text-based PDFs convert reasonably. Scanned or heavily laid-out PDFs
usually come out badly; that is a limit of the format, not the setup.

## Following authors and tags

**Discover → Your watchlist.**

- **Author:** Family Hub searches your indexers for that author and lists
  results you do not already own under **New for you**. It suggests; you
  press **Find it** to download. (Automatic downloading is not built.)
- **Genre or tag:** lists unread books carrying that tag, from your library
  and from the community tag packs. This draws on tags you and others have
  added; it is not a feed of newly published books.

Suggestions are refreshed every 12 hours, or when you press **Check again**.

## Recommendations

**Discover → Similar to books you've read**, and **Similar books** on any
book's page. Matches are scored by shared tags (family and personal tags
count most, then community tags, then book file tags) plus the same author. The
more you tag, the better they get.
