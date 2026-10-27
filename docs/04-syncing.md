# 4. Syncing devices and Kindle reading

There are two separate streams of reading, and they sync differently.

| What you read | How Family Hub hears about it | Detail you get |
|---|---|---|
| Books from the family library, read in Readest or KOReader | The app reports straight to Family Hub | Exact position, per device |
| Kindle books (including Kindle Unlimited) | Kindle → Goodreads → Family Hub | Started and finished only |
| Anything else | You add it by hand on the Dashboard | Whatever you enter |

> Device sync and the Hardcover import have **not yet been tested with real
> devices or accounts** (see the README). Treat this guide as the intended
> setup and expect to adjust it.

## Family library books: Readest or KOReader

Family Hub speaks the same sync language as a KOReader sync server. Both
[Readest](https://readest.com) (iPad, iPhone, Android, desktop) and
[KOReader](https://koreader.rocks) (Kobo, Kindle e-readers, Android) can use
a custom server.

**Once per profile, in Family Hub**

1. Sign in, press your name at the top right, and find **Reading app sync**
   under **Settings for** your name.
2. Type a **Sync password** and press **Save sync password**. This is
   separate from the passcode, because it is stored on each device.

**On each device**

*Readest*

1. Get the book onto the device: add your Family Hub catalogue in Readest
   and download from **My books** ([guide 9](09-devices-and-my-books.md)).
   Or open Family Hub in the browser, press **Download EPUB**, and open the
   file in Readest.
2. Open a book, then **Book menu → KOReader Sync**.
3. Server: `http://<server>:8090/kosync`
4. User name: the profile name, exactly as shown in Family Hub. Password: the
   sync password.

*KOReader*

1. Open a book, then **Tools → Progress sync → Custom sync server**:
   `http://<server>:8090/kosync`
2. **Register / Login** → **Login** with the profile name and sync password.
   (Registering from the device is switched off; profiles are made in Family
   Hub.)
3. Set **Document matching method** to **Binary**. Family Hub matches books by
   the file's fingerprint, so the file on the device must be the one from
   your library, unmodified.

After that, position is saved as you read and Family Hub shows the percentage
and the device name. A book reaching 99% or more is marked finished.

**Good to know**

- One sync server per app. If the app syncs to Family Hub it does not also
  sync to the library manager.
- A Kobo using Kobo's own software and the library manager's Kobo sync does
  **not** report to Family Hub. Install KOReader on the Kobo, or set your
  place by hand on the book's page.
- Books opened on a device that are not in the library are still synced
  between your devices; they just do not appear in your stats.

## Syncing away from home

"Syncs when I join the home network" works automatically: the app tries the
server each time, and succeeds when it can reach it. To sync from anywhere,
give devices a way to reach the server.

**Recommended: Tailscale.** It creates a private link between your devices
and the server, with nothing exposed to the internet.

1. Install [Tailscale](https://tailscale.com) on the server and sign in.
2. Install it on each phone or tablet and sign in to the same account (or
   invite family members to your network).
3. Use the server's Tailscale name or `100.x.x.x` address in place of
   `<server>` everywhere above. It works at home and away.

Do not use a device's IP address to identify people; home IP addresses
change. The server needs a stable address, the devices do not.

### Someone who lives elsewhere (Nan)

If she reads on a **Kindle e-reader**, none of the steps below are needed:
books are emailed to her Kindle and arrive over her own Wi-Fi. See
[guide 9](09-devices-and-my-books.md), "Looking after someone else's
Kindle". The steps below are for a tablet.

1. Install Tailscale on her tablet. Tailscale publishes
   [instructions for Amazon Fire devices](https://tailscale.com/kb/1394/install-amazon-fire).
   Sign in once and leave it on.
2. Bookmark `http://<server tailscale name>:8090` on her home screen.
3. Reading app: Readest may need sideloading on a Fire tablet (not
   confirmed either way). KOReader has an Android version that can be
   sideloaded. If neither suits, she can read in any EPUB app and mark books
   **Finished** in Family Hub herself.
4. Her Kindle books need none of this: that stream runs entirely online
   (next section).

Guest access should be refused over Tailscale when `GUEST_LAN_ONLY=true`, because
Tailscale addresses are not home-network addresses. Check it once from her tablet.

## Kindle and Kindle Unlimited

Amazon does not let other software read Kindle progress. The only data that
leaves is "started" and "finished", and only to Goodreads.

1. **Kindle → Goodreads.** In the Kindle app or on the e-reader, link the
   person's Goodreads account and switch on automatic updates. Starting a
   book shelves it as Currently Reading; finishing shelves it as Read.
2. **Goodreads → Family Hub.** In Family Hub, under **Settings for** your
   name, find **Goodreads**, paste the address of the Goodreads profile and
   press **Save link**. Family Hub reads the shelves straight away and then every six hours. The
   Goodreads profile has to be public; for a private one, import an export
   file instead. [Guide 11](11-goodreads.md) has the details.

**The older route through Hardcover still works.** Goodreads → Hardcover by
Hardcover's Goodreads import or a community sync tool from its
[showcase](https://docs.hardcover.app/showcase) (third-party, not tested for
this guide), then in Hardcover's account settings copy the **Hardcover API**
key and paste it under **Settings for** your name → **Kindle and other
reading (experimental)**.
It is only worth it if you already use Hardcover.

Each profile links its own accounts, so a different Kindle account per
person is fine. Someone without a Kindle skips this section.

**If the chain is more trouble than it is worth:** use **Dashboard → Add a
book read elsewhere**. Typing a title when you finish a Kindle book takes a
few seconds and gives the same stats.

## What "syncing together" covers

- Reading position: device ↔ Family Hub, as above.
- Tags, ratings, stats: these live in Family Hub only, so there is nothing to
  sync; every device sees the same thing in the browser.
- New books: appear in the library for everyone as soon as the library
  manager has added them.
- Books on devices: each person's **My books** list, fetched by reading
  apps or emailed to a Kindle ([guide 9](09-devices-and-my-books.md)).
