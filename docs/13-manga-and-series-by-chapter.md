# 13. Graphic novels, manga and series by chapter

The Library has two halves:

| Half | What is in it |
|---|---|
| **Books** | Fiction, non-fiction, tabletop and unsorted books |
| **Graphic novels** | Comics, graphic novels and whole manga volumes from the library, plus manga and manhwa followed **chapter by chapter** |

Family Hub remembers which half you had open, per profile and per device.

## Two ways a manga gets here

| | Whole volumes | Chapter by chapter |
|---|---|---|
| Good for | Finished series, collected editions | Series still coming out |
| Found with | **Discover** (the book search) | **Library → Graphic novels → Follow a new series** |
| Kept in | The book library, like any comic | The series folder, as one CBZ file per chapter |
| Read on | Any device, like a book | Tachimanga on an iPad, or any browser |

The book search cannot find single chapters. It looks through indexers,
which carry whole volumes and packs. Chapters come from reading sites.

## The two programs behind it

| Program | Its job | Its own page |
|---|---|---|
| **Suwayomi** (the series server) | Follows series on reading sites and downloads every chapter. It uses the same kind of sources as Tachimanga | Port 4567 |
| **Komga** | Hands the downloaded chapters to reading apps such as Tachimanga, with an account for each reader | Port 25600 |

A long series would be hundreds of "books" in the book library, so chapters
are kept apart from it. Family Hub does not touch the book library for any of
this. Komga only reads the chapters; it cannot change them.

### Switching it on

1. In the `.env` file, give both programs a password of your choosing:

   ```
   SUWAYOMI_AUTH_MODE=basic_auth
   SUWAYOMI_USER=bookhub
   SUWAYOMI_PASS=something-long
   KOMGA_USER=bookhub@bookhub.local
   KOMGA_PASS=something-else-long
   ```

2. In the project folder run:

   ```
   docker compose --profile series up -d
   ```

3. Open the series server's own page, `http://<the computer's address>:4567`,
   and sign in with the name and password from step 1.
4. Go to **Settings → Browse → Extension stores** and press **Add extension
   store**. Paste the address of a source list. This is the same address you
   added to Tachimanga as a repository.
5. Go to **Browse → Extensions** and install the sites you read on.
6. In Family Hub open **Admin → Hub → Connections → Series by chapter**
   and press **Test connection**.

The test says how many sources it found. It also sets the series server to
download every new chapter of every followed series as a CBZ file. Its own
defaults would skip a series while you still have unread chapters.

Family Hub never adds a source list or a source itself. Which sites you use is
your choice, and is made in the series server. If you read in Tachimanga, its
extension repository address (Tachimanga: **More → Settings → Browse →
Extension repos**) is the one to paste here; the same sites then work in
Family Hub without Tachimanga.

Without step 1 the series server's page is open to anyone on your home
network, and it shows everything that is followed.

### Languages

Many sources come in dozens of languages. Family Hub searches the ones listed
under **Languages to search** (short codes, separated by commas). It starts
as `en`. Add `ko` or `ja` to search Korean or Japanese sources too.

## Who can do what

| | Admins | Members who can find new books | Other members | Guests |
|---|---|---|---|---|
| See the series shelf and read | Yes | Yes | Yes | Never |
| Follow a new series, stop following, download more | Yes | Yes | No | Never |

An admin can switch the shelf off for one profile: **Admin → Hub → Profiles → Can see series by chapter**. That covers the
covers, pages and chapter files as well as the shelf.

Following is tied to **Can find new books** because following downloads a
whole series, which can be several GB.

## Following a series

1. Open **Library → Graphic novels**.
2. Under **Follow a new series** type a title and press **Search**. Results
   are grouped by site.
3. Press **Follow**.

Every chapter is queued to download straight away. New chapters are looked
for twice a day and downloaded as they appear. **Look for new chapters**
does it on demand.

Each series card shows how many chapters have been read, how many are new,
and how many are kept on the server so far.

## One comic file for the whole series

As soon as chapters are kept, Family Hub joins them into **one comic file (CBZ)** for the series, by
itself, checking every ten minutes. Inside, each chapter is a folder of its own ("0003 Chapter 3"), and
the file's ComicInfo.xml names the page each chapter starts on, so comic readers that read it show a
chapter list. A long series comes in parts of 50 chapters ("Part 2 (Chapter 51 – Chapter 100)").

- The series' page shows each part under **As comic files**: **Download CBZ** when it is ready, or
  that it is waiting or being put together.
- When new chapters arrive, only the last part is made again. While chapters are still downloading,
  only full parts are made, so the last one is not rebuilt for every chapter.
- The files are Family Hub's own, in `stack/bookhub/chapter-files`. The book library is not changed.
- On the same page, **My rating of the whole series** and the family and personal tags for the series
  work as for a series of volumes (guide 10).

## Reading in Tachimanga on an iPad

Tachimanga reads from Komga through its Komga source.

1. In Tachimanga open **Browse → Extensions**, find **Komga** and install it.
2. Open the Komga source's settings (the cog beside it).
3. Fill in:
   - **Address**: `http://<the computer's address>:25600`, with no slash at
     the end. On the home network that is the computer's local address, for
     example `http://192.168.0.73:25600`.
   - **Username** and **Password**: the reader's Komga account.
4. Go back, open the Komga source and pick a series.

The iPad has to be able to reach the computer: on the same Wi-Fi, or through
Tailscale when away from home.

These steps follow Komga's guide for Mihon, the app Tachimanga is modelled
on. The names of the buttons in Tachimanga may differ a little. **This has
not yet been tried on a real iPad.**

## Reading in Readest, as one book

Press **Add to my books** on a series' page. Your reading app's catalogue (guide 9) then has the series
as **one book**: every chapter kept so far, in order, with a contents entry per chapter. The catalogue
offers the comic file above as a second download once it is made. Either is kept on the tablet, so it
reads offline. While the series
is ongoing, each new chapter makes the catalogue offer a longer copy, which Readest downloads by itself if
automatic download is on. The longer copy is a new book in Readest: open it from the contents list at the
chapter you were up to, and delete the shorter one.

## Reading in a browser

Press **Continue** on a series card in Family Hub, or open the series and pick
a chapter.

- The chapter opens with every page under the last, which is how manhwa are
  drawn and works for manga on a tablet.
- Reaching the end, or pressing **Next**, marks the chapter read.
- Each kept chapter has a **File** button that downloads it as a CBZ file,
  for any comic reader app.

## What is not built yet

These were agreed and are waiting on one thing: confirmation that Tachimanga
connects to Komga on a real iPad.

| Agreed | State |
|---|---|
| An account for each person, made by Family Hub and shown once in their profile settings | Not built. For now there is one trial account |
| An **Only me** switch that hides a series from everyone else, admins included | Not built. Everything followed is visible to everyone with the shelf |
| Read marks for each person | Not built. Family Hub's browser reader uses the series server's single set of marks; Tachimanga uses the Komga account's |

The way Komga would do each of these has been proved on a throwaway copy:
separate accounts, a series hidden from one account and visible to another,
and read marks that differ per account.

## Things to know

- **Stopping does not delete.** **Stop following** ends the search for new
  chapters. Chapters already kept stay on the disk.
- **Tachimanga's own list is not copied.** Series you follow inside
  Tachimanga from a reading site are not brought here; follow them again in
  Family Hub.
- **Sites break.** A source that stops working shows under "No answer from"
  in a search. Updating it is done in the series server: **Browse →
  Extensions**.
- **"VerifyError: Bad type on operand stack".** Some add-ons (Webtoon was
  one) are built in a way that makes Java turn them away. `docker-compose.yml`
  sets `JAVA_TOOL_OPTIONS` on the series server to skip that check for
  add-ons. If you see this error, check that the line is still there and
  restart the series server.
- **Disk space.** A chapter is usually 5 to 30 MB. A long series can pass
  several GB.
- **Komga notices new chapters on its own**, a little while after they are
  downloaded.

## Where it is kept

| Folder | What | Copy it when moving? |
|---|---|---|
| `stack/series` | The chapters, as CBZ files in a folder per site and series | Yes |
| `stack/bookhub/chapter-files` | Each followed series joined into comic files | No: made again from the chapters |
| `stack/suwayomi` | The series server's settings, sources and followed series | Yes |
| `stack/komga-config` | Komga's accounts and read marks | Yes |

None of this is in Family Hub's backup file. The nightly copy to a second disk
([guide 6](06-your-data-and-backups.md)) takes all three.

## What has been tested

Tested on the test PC against Suwayomi v2.4.2366 and Komga 1.28.1:

- connecting to the series server with and without a password;
- searching a real site, following a series, a chapter arriving as a CBZ
  file, the browser reader, read marks and the File download;
- Komga finding the chapters in the series folder and a reader account
  seeing them.

Not yet tested: Tachimanga on a real iPad, the sites you use in Tachimanga,
and a series with hundreds of chapters.
