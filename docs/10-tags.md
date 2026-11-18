# 10. Tags

Every book can carry four sorts of tag. The Library's search box finds books
by any of them (see "Searching the library" below).

| Sort | Where it comes from | Who can change it |
|---|---|---|
| Book file tags | The book file's own details | Admins can hide or rename them |
| Community tags | Public book catalogues and shared tag packs | Admins can hide or rename them |
| Family tags | Typed by the family; a few are added automatically for series such as "Star Trek" | Admins and anyone allowed to edit family tags |
| My tags | Typed by you | Only you; nobody else sees them |

## Searching the library

You build a search, then press **Search**. Until you do, the books shown stay
as they were, and a note says the search has changed.

Under the box are **Sort** and **Refine**. **Refine** opens the less-used
settings: **Look in**, **Add tags as**, the colour key and **How to type a
search**. Press it again (**Hide refine**) to fold them away; the choice is
remembered.

Plain words typed in the box are looked for in the places switched on under
**Look in**: **Title**, **Author**, **Series** and **Tag**. **Enter** keeps what you typed
as terms, shown as chips under the box (Search keeps them too).

Under the chips are the tags you can pick from the part of the library you
are in, the most used first (**All tags** shows the rest, and typing narrows
the list). First choose how a tag you click counts under **Add tags as**
(press **Refine** under the search box to show it):
**+ Must have**, **− Must not have** or **∨ Any of these**, then click tags to
add them. A tag already in the search shows pressed; clicking it
again takes it out. A tag of the same name from several sorts (a book file's
"Fiction" and the community's "Fiction") is one tag, found whichever sort
carries it. Coloured dots say what each tag or term is, one dot per sort; the
key under **Refine** explains them, and hovering shows the names:

| Dot | Colour |
|---|---|
| Title | olive |
| Author | blue |
| Series | indigo |
| Book file tags | brown |
| Community | coral |
| Family | green |
| Mine | plum |

You can type more than words (**How to type a search**, under **Refine**,
says the same):

- `cafe in science fiction`: "cafe" in the title (or wherever is switched on),
  tagged Science fiction. `in` takes the words up to the next keyword as a tag.
- `by Ursula Le Guin`: an author.
- `+word` must match, `-word` or `not word` must not.
- `dragon OR wyvern`: either will do.
- `"book cafe"`: these words together, in this order.
- `mag*`: any ending (magic, magician, Magus).

The sign at the front of a chip says how it counts. Click it to cycle through
**+** (must match), **−** (must not) and **∨ or** (one of the "or" chips will
do), and click **×** to take the chip away. All + chips must match: two
tags mean a book needs both. Two typed words of the same kind (two authors)
mean "either".

At the top of the page, beside **Books** and **Graphic novels**, are the tabs
for that part: All, Fiction, Non-fiction, Tabletop and Unsorted for Books, and
All, Series and One-shots for Graphic novels. They change the books straight
away, without pressing Search, and the search covers the tab you are on.

Under **Sort**, just below the search box, **Last added**, **Title A–Z** and **Author A–Z** set the
order (authors by surname; "The" and "A" are skipped in titles). A series card
counts as its newest volume. The choice is remembered on that device.

## Tags and a rating for a whole series

A series' own page has **My rating of the whole series**, and family tags
and personal tags for the series. A series followed chapter by chapter has
them too, on its page (guide 13). They are kept apart from each volume's own
tags and rating, but they count for every volume: a search for a series tag
finds every volume in it, and each volume's page shows them under **From the
whole series**, with the series rating where you have not rated the volume
itself. The series card shows your series rating and the average of your
volume ratings.

## Book file tags are tidied

Book files often carry clutter where the subject should be: ISBNs, dates,
the publisher, adverts for the program that made the file, whole sentences.
Family Hub leaves these out and shows what is left. It also:

- splits combined tags ("Fiction.Horror" becomes "Fiction" and "Horror");
- uses one spelling for common genres ("Thrillers" and "Thriller" are the
  same tag).

Nothing is changed in the files. With **Write tags to the library** on (see
below) the clutter is taken off the library entries too. To see what was left
out, open **Admin → Books → Tidy tags** and look under **Removed as
clutter**.

## Community tags from catalogues

Family Hub asks public catalogues what each book is about and adds the answers
as community tags.

| Catalogue | Needs | Notes |
|---|---|---|
| Open Library | Nothing | The main source. Tested with real books |
| Wikidata | Nothing | Adds genres for well-known books. It limits how often it can be asked, so it fills in slowly |
| Google Books | A free API key | **Not yet tested with a real key** |
| Comic Vine | A free API key | Comics only. **Not yet tested with a real key** |

How it works:

- New books are looked up by themselves, a few each minute. A library of
  300 books takes about an hour and a half the first time.
- A book is looked up by its ISBN if it has one, otherwise by title and
  author.
- A match is only accepted when the title **and** the author agree. A book
  whose title is a file name (`1592005934.pdf`) or whose author is a user
  name (`admin`) will not be recognised unless it has an ISBN. Correct its
  details in the library manager and ask again.
- Comics and game books are rarely in Open Library, Wikidata or Google
  Books. Comics are looked up on Comic Vine instead, once you enter its key
  (see below).
- Only the title, author and ISBN are sent. Nothing about your family or
  what anyone reads leaves the server.

### Looking up by hand

- **One book:** open it and press **Look up tags** under Community tags
  (**Look up again** once it has some).
- **Everything waiting:** Admin → Books → Tidy tags → **Look up waiting books now**.
- **After fixing book details:** Admin → Books → Tidy tags → **Ask again about
  unrecognised books**.

A book the catalogues did not know is asked about again after 30 days.

### Switching it off, or adding Google Books

**Admin → Hub → Connections → Community tags from book catalogues.**

- Set **Look up tags for new books** to Off to stop all lookups. Tags
  already found stay.
- To add Comic Vine, see **Comics and Comic Vine** below.
- To add Google Books: in the Google Cloud console create a project, enable
  the **Books API**, create an **API key**, and paste it here. Press **Test
  connection**.

### Comics and Comic Vine

A comic file is named after its series, such as `Star Trek (Gold Key) 07`,
and has no author to match on. Family Hub asks Comic Vine for the series by
name. The publisher or year in brackets settles which series is meant; if
Comic Vine has no series of that name from that publisher, the comic gets no
tags.

Each comic receives the publisher, and the characters and themes that appear
most often in the series. Every issue of one series gets the same tags.

To switch it on: make a free account at `comicvine.gamespot.com`, open
`comicvine.gamespot.com/api` to see your key, and paste it under
**Connections → Community tags from book catalogues → Comic Vine API key**.
Press **Test connection**. Only books of the kind **Graphic novel**, or in a
comic format (CBZ, CBR), are sent to it.

## Hiding and renaming tags

**Admin → Books → Tidy tags** lists every book file tag and community
tag with the number of books carrying it.

- **Hide** removes a tag from every book, the tag search and the stats.
- **Rename** changes it everywhere. Rename two spellings to the same name to
  merge them.
- **Put back**, under **Hidden or renamed by you**, undoes either.

These choices are saved in the daily book-data backup
([guide 6](06-your-data-and-backups.md)).

Your own tags are not on this page. Remove those, and single family tags, on
the book's page with the cross next to the tag.

## Tags inside tags

A tag can sit under another: "Pathfinder 1e" and "Pathfinder 2e" under
"Pathfinder", "Vampire: The Masquerade" under "World of Darkness". Picking
the outer tag in a search finds the books with any tag under it too, however
deep. In the tag list under the search box the inner tags sit in one box with
their outer tag, called by the part that differs: **Pathfinder** [**1e**]
[**2e**]. An outer tag's number counts the books of every tag under it.

The sorter nests game editions under their game by itself
([guide 8](08-adding-and-sorting-books.md)). To nest any other tag, or undo
one, go to **Admin → Books → Tidy tags → Nested tags**: type the tag, what it
goes under, and press **Nest**; the **×** beside a tag takes it back out. A tag
can't go inside itself, directly or further down. Nesting is saved in the
book-data backup.

## Hiding tags from your own library

Anyone with a profile can keep books out of their own library list: press
**Refine** under the search box, type a tag or a role (**Rulebook**,
**Adventure**, **Player aid**, **GM aid**) under **Hidden** and press
**Hide**. Hiding an outer tag hides everything under it, so "Pathfinder 1e"
hides one edition and "Pathfinder" the whole game. The **×** on a hidden tag
shows those books again. The count over the books says how many you hid.

It is only your own list: nobody else's changes. Hidden books still turn up
as soon as you search, and on the **Game books** page, which also has a
**Hide … from my library** button for the game or edition you are looking at.

## Merging tags

When one thing has two tags ("Yaoi" and "Boy love", "Sci-fi" and "Sci fi"),
merge them under **Admin → Books → Tidy tags → Merge tags**. One merge covers
every sort of tag at once:

| Sort | What a merge does |
|---|---|
| Family tags | Changed on every book. A book that had both keeps one. A tag added later under the old name, by a person, the sorter or an import, goes on under the new name |
| Book file and community tags | Shown under the new name, like **Rename**. The files and catalogues are not changed |
| My tags | Never touched |

If the old tag sat under another tag ("Pathfinder 2e" under "Pathfinder"),
or had tags under it, the kept name takes its place.

**These look like the same tag** lists tags Family Hub spotted by itself:
spellings that differ only by capitals, a plural, punctuation, spaces or "&"
for "and". The number on each is how many books carry it, and the ticked one
(the most used) is kept: click another to keep that instead. **Merge into …**
says how many books change and asks before it does anything. **Not the
same** stops the suggestion coming back. Words that mean the same but are
spelled differently (Yaoi and BL) are never suggested; merge those by hand.

**Merge by hand**: type a tag in **Tags to merge** (the list shows family,
book file and community tags) and press **Add**, as many as you like. Click
the one to keep, or type a new name under **Name to keep**. The line below
says what will change, for example "12 books change; 3 already have "Boy
love"".

**Merged so far** lists every merge with **Undo**. Undo puts the old name
back on the family tags that had it when you merged, and stops the renaming.
Tags added since then under the old name keep the new one, and nesting stays
as it is.

Merges, and the suggestions you said no to, are saved in the book-data
backup ([guide 6](06-your-data-and-backups.md)).

## Tags written into the library

So that Calibre-Web, KOReader and every reading app see the same tags as
Family Hub, the tags are written into the library itself. Each book gets:

- its own tags, as they were typed, less the clutter (ISBNs, web addresses,
  "Accessible book") and with your hidden and renamed tags applied;
- the best few community tags (up to six, the ones most catalogues agree on
  first);
- its family tags.

Personal tags never leave Family Hub. A tag somebody typed in Calibre-Web is
kept, even if Family Hub has never heard of it; a tag Family Hub wrote and no
longer wants (you took a family tag off, or hid a community tag) is taken off
again.

Ten books are written a minute, through Calibre-Web, after a copy of the
library's database. **Admin → Books → Tidy tags → Tags in the library** shows:

- the switch **Write tags to the library** (on to begin with);
- how many books are still to write, and the next few with what will be
  added (+) and taken off (−);
- every book written, with an **Undo**. Undo puts that book's tags back as
  they were and leaves them alone from then on; **Look after it again**
  hands them back.

Nothing is written until Calibre-Web's sign-in is saved under
**Admin → Hub → Connections**. If Calibre-Web takes none of a batch's changes
(its account may not edit books), writing stops by itself and says so.

## Kinds of book

When the catalogues add tags to a book that is still **Unsorted**, Family Hub
tries again to work out its kind ([guide 8](08-adding-and-sorting-books.md)).
A kind that a person chose is never changed.
