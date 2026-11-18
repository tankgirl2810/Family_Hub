# 19. Books that put their own details right

A book often arrives as `PNW0397.pdf` by `elenak`: the file had no proper details, so the library took the
file name as the title and the user name of whoever made the PDF as the author. Family Hub now works out what
such books really are by itself, a couple of books a minute, and puts the clear cases right without anybody
having to.

## What it reads

For each book whose title is a file name or whose author is a user name, Family Hub reads:

- an **ISBN** in the library entry, the file name, an EPUB's own details or the first ten pages of a PDF;
- a **DOI** on the first pages of a PDF (most papers and technical books carry one);
- the **title and author the file gives itself**, leaving out what PDF programs put there on their own
  ("Microsoft Word - draft3.docx", "Adobe InDesign", "untitled");
- the **biggest words on a PDF's first page**, which on nearly every book is the title, and a **byline**
  with them ("The Asylum Stone By James L. Sutter");
- the title, tidied of file endings and release tags.

Then it asks **Open Library**, **Crossref** (for DOIs and non-fiction) and **Google Books** (when its key is
saved under Connections). What is sent is a title, an author, an ISBN or a DOI; nothing about the family.

## Clear, doubtful, unknown

- **Clear**: the ISBN or DOI inside the file matched a book whose title agrees with the file, or two
  catalogues and the file agree on the title and author, and no other book is nearly as likely. Family Hub
  copies the library's database, then changes the title and author (and adds a description, publisher and
  cover when the book has none) through Calibre-Web, the same way **Save to the library** does.
- **Doubtful**: there is a likely answer, but not a sure one. The book waits in the **review queue** with
  that answer already filled in and a line saying why ("Probably The Humane Interface by Jef Raskin, because
  its ISBN matched"). One press of **Confirm** says yes.
- **Unknown**: no catalogue knew it, or what came back did not agree with anything in the file (a loose search
  for "Heroes Handbook" finds Greek Mythology; that is never offered). It is asked about again in a month.

Game books and graphic novels are left alone here: the sorter (guide 8) and Comic Vine look after them.

## Seeing and undoing what changed

**Admin › Books › Books without real details** has a **Put right by itself** panel at the top:

- the switch **Put clear cases right by themselves** (on to begin with). Turned off, clear cases wait in the
  review queue like doubtful ones;
- how many books were put right, are waiting for you, were unknown, or are still to look up;
- every change, newest first, with what the book was called before, why Family Hub was sure, and an **Undo**.
  Undo puts the old title and author back and marks the book as right as it is, so it is not changed again.

Nothing is put right until Calibre-Web's sign-in is saved under **Admin › Hub › Connections**; until then,
clear cases wait in the review queue too.
