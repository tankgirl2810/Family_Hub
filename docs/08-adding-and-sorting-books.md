# 8. Adding your own files and sorting the library

## The inbox

Drop book files, folders or archives into `stack/inbox`. Family Hub looks at
the inbox every minute, tidies what it finds and hands the books to the
library manager.

```
stack/inbox ─▶ Family Hub tidies ─▶ stack/ingest ─▶ library manager ─▶ library
                    │
                    └─▶ stack/needs-attention   (anything it could not use)
```

| What you drop in | What happens |
|---|---|
| A book file (EPUB, PDF, MOBI, AZW3, CBZ, CBR, CHM, DJVU, DOCX, TXT and others) | Passed to the library |
| Folders inside folders | Every book inside is found; the folder names are remembered for tagging |
| A `.zip`, `.rar` or `.7z` with books inside | Unpacked, and the books are passed on |
| A `.zip`, `.rar` or `.7z` that is nearly all page images | Treated as a comic and renamed `.cbz`, `.cbr` or `.cb7` |
| A folder of three or more loose page images | Made into one `.cbz` comic named after the folder |
| A Kindle-format file (MOBI, AZW3 and similar) beside an EPUB with the same name | Kept in `needs-attention/_processed`. The library would only turn it into a second copy of the EPUB |
| Covers and details files (`cover.jpg`, `metadata.opf`, `.nfo`) | Kept in `needs-attention/_processed` |
| Anything else (`.doc`, `.ppt`, programs, broken archives) | Moved to `needs-attention` with a reason |

**Nothing is deleted.** Originals whose contents went into the library
(unpacked archives, loose comic pages) are kept in
`stack/needs-attention/_processed`. That folder is safe to empty whenever
you like.

**Admin → Books → Needs attention** lists what was set aside and why.
After fixing something (for example converting a `.doc` to `.docx`), put the
file back in the inbox, or press **Try these again**.

Use the inbox rather than `stack/ingest`. The library manager deletes
anything in `ingest` that it cannot read, without saying so.

### Why PDFs and comics are not converted

The library manager converts new books to EPUB as they arrive, except PDFs
and comics. Converting a large scanned PDF can take longer than the library
manager allows for one book, and while it tries, every book behind it waits.
PDFs and comics are added as they are. To convert one PDF, use **Convert to
EPUB** on its page ([guide 5](05-finding-new-books.md), "PDFs").

If you set up the library manager from scratch, make the same choice there:
**Settings → CWA Settings → Auto-Convert**, and tick PDF, CBZ, CBR, CB7, CBC
and DJVU under formats to leave alone.

### If books stop being added

The library manager only notices a file when it arrives, and if its importer
crashes part-way it leaves a lock behind that blocks every later book. Two
things now recover from that without anyone stepping in:

- A health check on the library manager removes a lock that no running
  importer owns (see `docker-compose.yml`).
- Family Hub re-announces any file that has sat in `stack/ingest` for more
  than five minutes.

**Admin → Hub → Server → Library import folder** shows how many files
are waiting, and says so if some have waited longer than usual. If that does
not clear, restart the library manager:

```bash
docker compose restart calibre-web-automated
```

## Books without real details

A book sometimes arrives as "PNW0397.pdf" by "elenak": the file carried no
proper details, so the library took its file name and the user name of
whoever made the PDF. **Admin → Books → Books without real details → Fix
them** lists every entry whose title looks like a file name or whose author
is missing or a user name (comics only for a bad title, since comic files
rarely name an author).

**Find details** gathers clues, most trusted first:

1. An ISBN: in the library entry, in the title or file name, among an
   EPUB's own details or on its copyright page, or printed on the first ten
   pages of a PDF.
2. The title and author the PDF or EPUB gives itself.
3. The title, tidied of file endings, underscores and release tags.

It looks each one up in Open Library (and Google Books, when its key is
saved under Admin, Hub, Connections) and lists the matches with their cover
and the clue that found them. **Use this** fills in the title and authors;
change them if needed, or type them in yourself, then **Save to the
library**. **It is right** leaves a book off the list for good.

Saving is done by the library manager, like removing spare copies, so its
sign-in must be saved under **Admin → Hub → Connections → Library
manager**. The library's database is copied to `stack/bookhub/library-backups`
first. A description, publisher and cover from the match are added only
when the book has none. Reading progress, tags, ratings and shelves stay
with the book under its new name; community tags are looked up again for
the real title. Afterwards, **Copies of the same book** may find more spare
copies, since books that only differed by a file name now share a real
title.

### Sorting suggestions

The same page opens with **Sorting suggestions**: books the sorter would
like to put right, in three tabs, **Tabletop**, **Books** and **Graphic
novels & manga**. A book waits here only when there is something to say:
a cleaner title ("PF 2E - Core Rulebook" becomes "Core Rulebook"), a real
author or publisher, a series and its number, game and edition tags, clues
that disagree (the folder says 1st edition, the first pages say second), or
a game Family Hub does not know yet. A book that is simply unsorted, with
nothing to go on, is left alone.

Each book shows what confirming would change, the new value over the old.

- **Confirm** writes the title, author, series and number, publisher,
  language and tags (the game, edition and role go on as tags) into the
  library through the library manager, after a copy of the library
  database, as above. Reading, ratings and tags follow the book to its new
  name. The kind stays in Family Hub only.
- **Edit** opens every detail as a box first. For a game book the author
  can be the publisher (Paizo, White Wolf, Wizards of the Coast); **Find the
  writers** reads the credits on the first pages of a PDF.
- **Not right** leaves the book as it is and never suggests the same thing
  for it again.
- **Skip** hides it until the page is opened again.
- **Confirm all like this** does the same for every book in the batch:
  books that arrived in the same folder, or are named alike when the folder
  is not known. Each keeps its own title and number.

Books that share a title and author come first. The library shows them as
copies of one book, so they share one set of tags until one is given its
own title. After them come the biggest batches.

When the top folder names a game nobody has told Family Hub about
("Shadowrun 6e/..."), the suggestion offers it as a new game: Shadowrun,
edition Shadowrun 6e. Confirming adds it to the games the sorter knows, and
later books in that folder sort themselves.

**Sort the whole library again** looks at every book once more, reading the
first pages of PDFs, which takes a while for a big library; the bar shows
how far it has got. Books whose kind someone chose are left as they are. On
the server it can also be run once from the command line:

```bash
docker exec -e BACKGROUND_JOBS=false bookhub python -m bookhub --resort-library
```

### Unsorted books and Edit details

Books the sorter could not place at all wait under the **Unsorted** tab,
whether or not it has a guess. The first one opens straight in the editor:
**the book on the left** (a PDF's first pages, an EPUB's opening, or a
comic's first pictures, which you can scroll through, with **Show 10 more
pages** for more) and **its details on the right**. Put in what it is and
press **Confirm**, or **Skip**; the next book opens.

Any book can be opened the same way: on the book's page an admin has
**Edit details**. It is the same editor, for books the sorter was happy
with too.

### Learned rules

Every confirm and every **Not right** is remembered as a rule, listed in
plain words under **Learned rules**, for example "Anything arriving in
Pathfinder/2nd Edition/Society → Pathfinder › Pathfinder 2e, Adventure".
Books arriving the same way, then and later, are sorted by the rule in
Family Hub straight away. Their library entries are not changed until you
say so: they wait at the top of the tab as **Ready to write**, one card per
rule, and **Write N books to the library** writes them all (after one copy
of the library database), so the details reach every reading app. Open the
list to see each book's changes, or **Edit** any one first. Each rule says how many confirmations it came from and which books it
matches; **Delete** removes it, and books like it wait in the queue again.

When several rules say nearly the same thing (nine Society seasons, each
its own rule), the page suggests one rule instead, with the books it would
cover and anything that would change. Rules are only combined when you
press **Combine them**. Rules and the games you added are in the book-data
file and its backups ([guide 6](06-your-data-and-backups.md)).

## The same book twice

The library manager makes a new entry for every file it is given. Give it a
book as both EPUB and MOBI and it converts the MOBI and lists the book twice.

Family Hub shows such entries as **one book**:

- Entries with the same title and author (or the same ISBN) are one book.
- The entry people see and download is the one with a cover, an EPUB and the
  larger file. A format that only another copy has (say a PDF) is offered on
  the same page.
- Ratings, tags and reading history already belonged to the book, not the
  entry, so nothing is lost or split.
- **Showing them as one removes nothing.** Every copy is still in the library until you remove the spares
  yourself; see "Removing spare copies" below.

An exception: when the title looks like a file name (`scan_01`,
`1592005934.pdf`) or the author like a user name (`admin`), the name proves
nothing. Those entries are only joined when they hold exactly the same file.

**Admin → Books → Copies of the same book → See the copies** lists every set:

| Button | What it does |
|---|---|
| **Keep apart** | Lists the entries as separate books again |
| **Show as one** | Joins entries Family Hub was not sure about |
| **Let Family Hub decide** | Forgets your choice |
| **Open in library manager** | Opens that entry in the library manager |

The page also shows how much disk space the spare copies take. Admins see
the other copies on the book's own page too.

### Removing spare copies

The same page starts with **Copies to sort out**, a carousel you move
through yourself.

- Along the top is a strip of every set's cover, with how many copies each
  has. **Previous** and **Next**, the arrow keys (when you are not typing in
  a box), or a cover in the strip move to another set.
- The set's copies lie open side by side like the pages of a book: each
  copy's front page, large. **Show the first pages** turns them into each
  copy's first few pages, scrollable, with **Show 10 more pages**; press it
  again for the front pages. That's how two files with one name (three
  different cert sheets, say) are told apart.
- Under each page are that copy's own title, author, series and number, and
  tags, ready to change, then its format, size, pages, date added and the
  rest. Boxes and facts where the copies differ are marked. The copy picked
  to start with fills its empty boxes from whichever copy says more, and says
  so. **Open in the editor** opens one copy on its own.
- Press **Keep this one** under the copy you want. Under the others the page
  says what becomes of them.
- The save button says what will happen before you press it, for example
  **Keep copy 2 · the other copy is identical and will be deleted** or
  **... the other copy will be set aside in needs-attention**. One copy of the
  library's database is made first, then the spare goes by the rules below,
  then the kept copy's details are written. Its tags are replaced by the list
  in the box, so a tag you take off comes off. The carousel moves on to the
  next set.
- **They are different books · save each one** is for copies that are not
  the same book (three `Maps.pdf` by the same person, say). Every copy stays
  in the library with the title, author, series, number and tags written
  under it, and Family Hub never suggests them as copies again, even if they
  end up with the same name.

The sets only you can decide come first. The bulk buttons for the clear-cut
sets are still there, folded away under **Clear-cut sets, all at once**. That
card sorts every set into one of four kinds:

| Kind | How Family Hub tells | What the button does |
|---|---|---|
| The very same file twice | The files are identical, byte for byte | Removes the spare entry and its file |
| The same book, the same details | File sizes within 10%; neither entry says more than the other | Keeps the original over a converted copy, sets the spare aside, removes its entry |
| The same book, one entry says more | File sizes within 10%; one entry has details the other lacks | Keeps the entry with the better file, copies the missing description, tags, series or publisher onto it, sets the spare aside, removes its entry |
| For you to decide | Sizes more than 10% apart, an entry with no file, a doubtful name, or a detail that would be lost | Nothing, until you press **Keep this one** on an entry |

Each of the first three has a button for the whole kind and, under **See the
sets**, one for each set. Every button asks before it acts.

For the sets that need you, the queue above is the easier way: open each
file from its link if you want to compare, then keep one, or press **Not
duplicates**.

What keeps this safe:

- **Set aside, not deleted.** A spare is only deleted outright when the kept
  file is identical, whichever copy you keep. Every other spare has its files copied to
  `needs-attention/_duplicates` first, with its cover and details file.
  Empty that folder yourself when you are satisfied.
- **A copy of the library's database first.** One is written to
  `stack/bookhub/library-backups` before every batch. The last 20 are kept.
- **Checked afterwards.** If the library manager says yes but the entry is
  still there, the page says so and logs nothing as done.
- **Your reading history is untouched.** Ratings, tags and history belong to
  the book, not the entry.

This and **Deleting a book** (below) are the only places where Family Hub
changes the library, and neither writes to it directly. It signs in to the library manager and uses the
library manager's own merge and edit pages. For that it needs an admin
account there: **Admin → Hub → Connections → Library manager**. Until
that is filled in, the buttons are greyed out.

Two limits to know:

- The library manager can copy a description, tags, a series and a publisher
  from one entry to another. It cannot copy an ISBN, a publication date or a
  cover. A set where the better file's entry lacks one of those goes to
  **For you to decide**.
- "Converted copy" is a judgement: the file has the layout the library
  manager's converter produces. A book that someone converted before it
  reached you looks the same.

To stop new duplicates, add books through the inbox: a Kindle-format file
beside an EPUB of the same name is held back there. Files put straight into
the library manager's own import folder are not checked.

### Deleting a book

Admins find **Delete this book** under **More** on a book's page. It removes the book and
every copy of it from the library, files included, after a confirmation
that names the title, its formats and how many copies go with it.

- A copy of the library's database is saved first, as above.
- It uses the library manager's own Delete, so it needs the same admin
  sign-in under **Admin → Hub → Connections → Library manager**, and that
  account must be allowed to delete books there.
- The family's ratings, tags and history for the book are kept, in case it
  comes back.
- If the nightly copy to a second disk is on, last night's copy still has
  the files.

## Changing a cover

Some books arrive with no cover, or with the first page of a PDF as one. On
the book's page, under the cover, press **More**, then **Change cover**. Admins can do this,
and so can anyone allowed to edit family tags.

| Choice | What it does |
|---|---|
| **Use this picture** | Uploads a picture from your device (JPEG, PNG or WebP, up to 15 MB) |
| **Look for covers** | Asks Open Library for covers of this book and its other editions. Click one to use it |
| **Make a title cover** | Makes a plain cover from the title and author |
| **Back to the library's cover** | Removes the cover you chose |

Things to know:

- The cover is kept in Family Hub. **The library and the book file are not
  changed**, so the library manager, and a book once it is open on a device,
  still show the old cover. Family Hub's pages and the catalogue that reading
  apps browse show the new one.
- **Look for covers** only finds a book whose title and author are right.
  Only the title, author and ISBN are sent, and pictures are only ever
  fetched from Open Library.
- Chosen covers are pictures in `stack/bookhub/covers`. They are not in the
  book-data backup file ([guide 6](06-your-data-and-backups.md)).

## What a book's card shows

The Library is one list of every book: the ones on the home server, the
ones you have read elsewhere and the ones the family has only on Kindle.
Search and tags narrow it down.

| On the card | Meaning |
|---|---|
| Faded cover with **Already read** across it | You have finished this book. Other people see it as usual |
| **Reading** and a percentage across the cover | You are part-way through it |
| **ELSEWHERE** on the cover, **Read outside the library** | A book you added under **Dashboard → Add a book read elsewhere**, or that came from Hardcover; the library has no copy |
| **Already in my library** | It is on your My books list ([guide 9](09-devices-and-my-books.md)) |
| **On Kindle only** | Someone in the family has it on Goodreads; the library has no copy ([guide 11](11-goodreads.md)) |
| **My rating** | The stars you gave it |
| **★ Readers** | The average rating from readers at large, and where it came from |

### Where the readers' rating comes from

- **Goodreads**, for any book on a linked Goodreads shelf. Goodreads' feeds
  give the average but not how many people rated.
- **Open Library**, for the rest of the library, with the number of
  ratings. Four books a minute are looked up, so a large library takes an
  hour or two the first time. Only the title, author and ISBN are sent.
- **Google Books** and **Hardcover** too, when their keys are saved under
  **Admin → Hub → Connections → Community tags from book catalogues**. Of
  these, the figure the most people stand behind is kept.

A Goodreads figure is used when one exists. An average from fewer than ten
ratings is not shown, since it says too little. Books nobody knows show no
readers' rating.

This is switched off together with tag lookups: **Admin → Hub → Connections → Community tags from book catalogues**.

## A series is one card

Books in the same series show as a single card in the Library, with the
number of volumes on it. Open it to see every volume in order. The series
comes from the library itself or, when a book has none, from its file name:
manga and comics downloaded as "Title v01 (2020) (Digital)" or
"Title Vol. 3" are put together as "Title", volume 1, 3 and so on. A
capital V on a book that is not a comic, as in "Vampire The Masquerade V5",
is read as an edition and left alone. Family Hub only shows them this way;
the library and its files are not changed.

A title that arrived garbled, such as "The Birdâ€™s Nest", is shown as it
should read ("The Bird’s Nest"), and so is a comic story whose apostrophe
was lost in its file name ("Child s Play"). An entry the library holds
without any book file says so on its card and its page; adding the file
again in the library manager fixes it.

Comics are gathered by franchise: every Star Trek comic, whichever run, is
on one **Star Trek** card. Its page shows each run (Gold Key, Deep Space
Nine (Malibu), ...) as its own section, biggest first. Comics that share a
title but have no number, such as "Star Trek (Book & Record Set)", become
one run, each part called by its story. A comic that is the only one of
its kind stays on its own until another arrives.

Comics named with a bare issue number ("Star Trek - Deep Space Nine
(Marvel) 05") are grouped too, with annuals and specials after the issues.
A publisher or year in brackets before the number keeps different runs
apart. Inside a series each part is called by its own title when it has
one, otherwise "Volume 3", "Issue 12" or "Chapter 40".

A series page has **The whole series in one file** (on a franchise page,
one button beside each run): comics (CBZ and CBR)
are joined into one CBZ that reads straight through in any comic reader;
other books come as one ZIP with each volume's own file inside. Each part
can still be downloaded on its own.

## Game books

Role-playing game books come in many small pieces: adventures in parts,
maps, pawns, character sheets. So that they don't fill the Library, the small
ones are put together on cards:

- An adventure series is one card: "Age of Ashes", "6 parts + Player's
  Guide + maps".
- Player aids and GM aids that belong to no series are one card for each game,
  edition and role: "Pathfinder 2e · GM aids", "34 GM aids".
- Rulebooks, and adventures that stand alone, keep a card each.

**Open** on such a card goes to the **Game books** page at those books. That
page (**Game books by game** at the top of the Library) shows every game
book by game, then edition, then product line: the folders the books arrived
in ("Adventure Paths › Age of Ashes", "Lost Omens"), else their series, else
a shelf for their role. Click a product line to open it. Inside, the core
books come first, then general player's guides, then other rulebooks; an
adventure path's parts are in order, each card saying "Part 3", followed by
its Player's Guide and maps. Novels and comics set in a game's world are
under the game too. **Rulebooks**, **Adventures**, **Player aids** and **GM
aids** show only those; pick none to see everything.

A book that is not in English has a small badge in the corner of its cover
("RU" for Russian), from the language the library has for it or the one its
title is written in.

Card decks and map packs used to be sorted as graphic novels. Once sorted to
Tabletop they leave the comic server's copy of the graphic novels at its
next update ([guide 18](18-comics-in-paperback.md)); the library's own files
are not touched. Reading apps get them one by one, never joined into one
comic, with their role (Adventure, GM aid...) as a category.

## Kinds of book

Every book has one kind: **Fiction**, **Non-fiction**, **Graphic novel**,
**Tabletop** or **Unsorted**. At the top of the Library, under **Books**,
the tabs **All**, **Fiction**, **Non-fiction**, **Tabletop** and
**Unsorted** show one kind at a time; graphic novels have their own half,
**Graphic novels** ([guide 13](13-manga-and-series-by-chapter.md)). The
tab you chose is remembered on that device for that profile.

Family Hub guesses the kind once, when a book first appears:

- Comic files (CBZ, CBR) are graphic novels.
- Titles and folder names that mention a tabletop game (Vampire: The
  Masquerade, Clanbook, D&D, sourcebook and similar) are tabletop.
- A book whose own tags say "Fiction", "Fantasy", "Romance" and so on is
  fiction; "History", "Science", "Biography" and so on is non-fiction.
- Anything else is unsorted. PDFs rarely carry tags, so most of them start
  here.

When catalogues later add community tags to an unsorted book
([guide 10](10-tags.md)), the guess is made again with those.

It also adds family tags for series and editions it recognises, such as
"Star Trek", "VTM", "V5" and "Revised". These are ordinary family tags; remove
any that are wrong on the book's page.

A kind somebody chooses is never changed by a later guess, and it is included
in the book-data backup.

## Sorting many books at once

**Admin → Books → Sort the library** (admins only).

1. Pick the kind to look through. It opens on **Unsorted**.
2. Tick the books that belong together, or tick the box in the heading to
   select all of them. Search narrows the list first.
3. Choose **Set kind to**, type a family tag if you want one added (for
   example a game edition), and press **Apply to selected**.

To change one book, use **Kind of book** on its page. Anyone who can edit
family tags can do that.
