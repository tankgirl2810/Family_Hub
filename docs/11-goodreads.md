# 11. Goodreads and books that are only on Kindle

Each person can link their Goodreads profile. Family Hub then knows what they
have read on a Kindle, and the family can see which books somebody already
owns there but the library lacks.

## What linking does

| | |
|---|---|
| Reading history | Books on the **Read** and **Currently reading** shelves go into that person's history, with the date read |
| Ratings | Goodreads star ratings are copied, unless the person has already rated the book here |
| Books the library has | Matched by ISBN, or by title and author. They get a **View on Goodreads** link, shown to that person only |
| Books the library lacks | Listed in the Library as **On Kindle only**, for everyone signed in |
| What readers think | Goodreads' average rating for each book on the shelves is shown on its card |

The **To read** shelf is not used.

Family Hub only reads from Goodreads. It never changes anything there.

## Linking your profile

1. On Goodreads, open your profile and copy the address. It looks like
   `https://www.goodreads.com/user/show/12345678-your-name`.
2. In Family Hub press your name at the top right, and under **Settings
   for** your name find **Goodreads**.
3. Paste the address and press **Save link**.

The shelves are read straight away (a long list takes a minute) and then
every six hours. **Read shelves now** does it on demand.

### If your Goodreads profile is private

The link only works while the profile is public. Family Hub says so when
Goodreads lists no books. Either:

- on Goodreads, open **Account settings → Settings → Privacy** and let
  anyone view your profile, or
- use an export file. On Goodreads open **My Books → Import and export →
  Export library**, then in Family Hub choose the file under **Or import a
  Goodreads export file** and press **Import file**. Repeat it whenever you
  want to bring Family Hub up to date.

## Who sees what

| | You | Rest of the family | Admins | Guest |
|---|---|---|---|---|
| Your Goodreads link | Yes | No | No. They see that a link is saved, and can replace or remove it | No |
| Your reading history and ratings | Yes | Ratings only, as for any book | On the Admin page, as for any book | No |
| "On Kindle only" books | Yes | Yes, **without your name** | Yes; the Admin page says whose they are | No |

The link is not in the book-data export or the backups. After restoring
Family Hub from a backup, paste it in again.

An admin sets up Goodreads for someone else under **Admin → Hub → Profiles →
Devices** next to that person's name.

## Books that are only on Kindle

A book on somebody's Goodreads shelves that the library does not hold is
shown in the Library with:

- the label **KINDLE** on its cover and the words **On Kindle only** where
  a library book has its download button;
- **Find the EPUB**, which opens the book search ([guide 5](05-finding-new-books.md))
  with the title and author filled in. People who may not find new books do
  not get this link;
- **View on Goodreads**.

"On Kindle only" (the Goodreads settings call them "Available on Kindle")
means *someone in the family has this on Goodreads*.
Goodreads' feeds do not say whether that was a Kindle book or a paperback, so
a few will be paper copies.

When the book is later added to the library, Family Hub joins the two up within
about ten minutes: the Kindle entry goes, and the reading history and rating
move to the library's copy.

**Admin → Books → On Kindle, not in the library** lists these books with
the names of the people who have them. That is the only place names appear.

## Removing the link

Press **Remove link**. Your reading history stays. The "On Kindle only"
books that came from your shelves are no longer listed for the family.

## Things to know

- Progress that came from a reading app, or that you typed in, is never
  overwritten by Goodreads. The one exception: a book you marked as reading
  becomes finished when Goodreads says you finished it.
- After the first read, only the hundred most recently changed books on each
  shelf are checked each time. **Read shelves now** checks everything.
- The Hardcover link under **Kindle and other reading** still works and can
  be used alongside this.
