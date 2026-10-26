# 9. Devices and "My books"

The Library shows every book the family has. **My books** is each person's
own list: the books they want on their devices.

- Reading apps (Readest, KOReader) fetch My books themselves from a private
  catalogue.
- A Kindle cannot do that, so Family Hub emails books to it.

> Sending to a Kindle and the catalogue have been tested with stand-ins, not
> yet with a real Kindle, Gmail account or Readest. Expect to adjust details
> the first time.

## My books

- On the Library page, press **Add to my books** under a book. The card
  then says **Already in my library**.
- To take a book off your list, open the book's page and press **In my
  books**.
- Your reading app's catalogue lists My books on their own.
- **Dashboard → My books** shows the whole list in one place: where each
  book stands on each Kindle (sent, sending, not sent, with the button
  that fits), a **Take off my list** button, then what you are reading now
  and what you have finished, the most recent first.

Taking a book off your list keeps your reading history, rating and tags.

**Family Hub cannot delete a book from a device.** When you take a book off
your list it stops being offered, and Family Hub tells you which Kindle still
has a copy. Delete it on the device itself (on a Kindle: press and hold the
book, then **Remove download** or **Delete permanently**).

## Add your devices

**Profiles → Settings → Devices and reading apps.** Add as many as you use.
Only you and admins can see your list.

| Kind | What Family Hub does for it |
|---|---|
| Kindle | Emails books to the Kindle's own address |
| Readest | Offers My books through a catalogue; tracks reading position |
| KOReader | The same as Readest |
| Kobo | Nothing automatic; download in the Kobo's browser, or install KOReader |
| Other | Just keeps the name and a link |

Each device can have a **link to open it** (for example your Kindle library
on Amazon). Kindle, Readest and Kobo have a usual link filled in for you;
type your own to replace it.

## Readest or KOReader: the catalogue

1. In Family Hub, set a **sync password** under **Profiles → Settings →
   Reading app sync**. One password covers the catalogue and progress sync.
2. In the app, add a catalogue. Apps call it an "OPDS catalogue"; in
   KOReader it is under **Search → OPDS catalog**, in Readest look for
   OPDS catalogues in the library's menu. Enter:
   - Address: `http://<server>:8090/opds`
   - User name: your profile name, exactly as Family Hub shows it
   - Password: the sync password
3. Open **My books** in the catalogue and download what you want. **The
   whole family library** and search cover everything else.

**Comic series are one book.** Graphic novels and manga kept as volumes (CBZ or CBR) are one entry in
the catalogue. It downloads as one EPUB running from the first volume to the last, with a contents list
entry for each volume, so the reading app's contents button jumps straight to any volume (the same pages
as one CBZ file are offered too). Choosing any volume for My books brings the whole series. A series
followed chapter by chapter comes the same way, with a contents entry per chapter, once you press **Add
to my books** on its page. When a new volume arrives, the series
comes again as a new, longer book; delete the shorter one in the reading app.

A long series takes a few minutes to put together the first time. Until it is ready, My books lists it
with "Being put together on the server" and nothing to download, and Readest's automatic download picks
it up on a later check. **My books → Series for your reading app** shows each one: being put together
(with how far along), waiting its turn, ready, or why it could not be made. The series card in the
Library carries the same as a badge.

Very large scans are shrunk to tablet size in this book. The files in the library are not changed. A
series that would still be over about 1 GB comes in parts, such as "The Ancient Magus' Bride · Part 2
(Volumes 5–9)", because a tablet's reading app cannot open a much bigger book. A volume is never split
between parts, and a new volume only changes the last part. Series by chapter come in parts of 50
chapters. A volume whose file is damaged is left out, and a whole copy of it is used if the library has
one.

**Progress in a joined book reaches its volumes.** Family Hub records every joined book it makes, so
when Readest reports your place, the volume you are in gets that progress, the ones before it count as
finished, and finishing the book finishes them all, with your reading stats counting each volume. For
a series by chapter, the chapters you have passed are marked read on the series server.

**Automatic downloads (Readest).** If the catalogue's automatic download is
switched on, Readest downloads only **My books** by itself: press **Add to
my books** in Family Hub and the book appears in Readest shortly after.
Everything else downloads only when you tap it in the catalogue. (An older
Family Hub pointed Readest at the whole library; if Readest already took
every book, delete the unwanted ones in Readest, then remove and add the
catalogue again so it starts fresh.)
4. Set up progress sync as in [guide 4](04-syncing.md).

On an iPad, the first time Readest contacts a server on your home network
iOS asks for permission to find devices on the local network. Allow it.

The catalogue signs in with a plain password, so use it at home or over
Tailscale ([guide 4](04-syncing.md), "Syncing away from home"), not over the
open internet.

## Kindle: sending by email

Every Kindle has its own email address. Anything a trusted sender emails to
it appears on the Kindle the next time it is on Wi-Fi, wherever it is.

### Once, for the whole family (an admin)

1. Create a Gmail address just for this, for example
   `ourfamily.books@gmail.com`. Do not use a personal one: Family Hub keeps
   its password.
2. In that Google account turn on **2-Step Verification**, then create an
   **app password** (Google Account → Security → 2-Step Verification → App
   passwords). It is 16 letters.
3. In Family Hub: **Admin → Hub → Connections → Sending books by
   email**. Enter the address and the app password. Leave the mail server
   and port as they are for Gmail.
4. Press **Test connection**, then **Save**.

To change the address or password later, type the new one over it and save.
The password is never shown again and is not included in backups.

Another mail service works too: enter its server name and port (587, or 465
for services that use it).

### For each Kindle

1. Find the Kindle's address: on Amazon, **Manage Your Content and Devices →
   Preferences → Personal Document Settings**. It ends in `@kindle.com`.
2. On the same Amazon page, under **Approved Personal Document E-mail
   List**, add the Gmail address from above. Amazon silently drops mail from
   anyone not on this list.
3. In Family Hub add the device: kind **Kindle**, a name, and the Kindle's
   address. Leave **Send books automatically** ticked to have every book
   added to My books sent straight away.

### Sending

- With **Send automatically** on, adding a book to My books sends it.
- Otherwise open the book and press **Send to** *device name*.
- The book's page shows **Sent to** *device* with the date, or the reason it
  was not sent, with **Try again**.

What cannot be sent:

- **Comics** (CBZ, CBR). Amazon does not accept them by email.
- **Books over 18 MB.** Gmail takes messages up to 25 MB and a book grows by
  about a third when attached. Amazon's own limit is 50 MB; if your mail
  service allows more, raise **Largest book to send** under Connections.
- Formats a Kindle cannot open by email. EPUB is sent when the book has one,
  then PDF. A PDF you converted in Family Hub is sent as the EPUB.

"Sent" means the mail service accepted the message. Amazon then delivers it,
usually within a few minutes. If it never arrives, check the approved-sender
list first.

## Looking after someone else's Kindle (Nan)

An admin can do all of this from their own profile.

1. **Admin → Hub → Profiles**, find Nan in the table and press **Devices**. Add
   her Kindle with its address and leave **Send books automatically** on.
2. Add the family Gmail address to the approved senders in *her* Amazon
   account (step 2 above). This is the one thing that needs her sign-in.
3. To give her a book, open it and under **Other people's books** press
   **Add to Nan's books**. It is emailed to her Kindle and shows **Sent to
   Nan's Kindle**. Her Kindle picks it up on her own Wi-Fi; she does not need
   Tailscale or to be on your network.
4. A Kindle does not report what she has read. When she tells you, set
   **Reading status for Nan** on the same page to Reading or Finished, so
   her history and stats stay right.
5. To tidy up, press **In Nan's books** to take it off her list. The copy on
   her Kindle stays until it is deleted there.

## What is kept where

- My books lists are part of the daily book-data backup
  ([guide 6](06-your-data-and-backups.md)).
- Devices, Kindle addresses and the mail password are kept in Family Hub's
  database only. They are in a copy of the `stack/bookhub` folder, not in
  the book-data file.
