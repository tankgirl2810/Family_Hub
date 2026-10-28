# 18. Comics in Paperback

Graphic novels and series followed chapter by chapter can be read in **Paperback**, a free comic and manga
reader for iPhone and iPad, instead of in the book-reading app. Paperback reads them from **Komga**, the comic
server that starts together with the series server (guide 13).

What you get over reading them as books:

- A series is one thing with its volumes or chapters inside. A new chapter is added to it; nothing is
  replaced, and your place in each chapter is kept.
- Each series by chapter shows its state: **Ongoing**, **Hiatus**, **Ended** or **Abandoned**, and a line
  saying how many chapters are here and whether more are on the way ("108 of 112 chapters here; 4 more on
  the way", or "All 108 chapters are here. Waiting for the next one to come out").
- Unread chapters are counted, and Paperback's **Updates** tab lists new ones.

## What Family Hub does

Every ten minutes Family Hub:

- keeps a copy of the library's graphic novels laid out the way Komga wants them: a folder per series, one
  file per volume (`Cherry Magic!/Cherry Magic! - Volume 001.cbz`), and one-shots in `_oneshots`. The
  library is laid out by Calibre, a folder per book, which Komga would show as a series per volume. The copy
  is kept in `komga-comics` in the stack folder; new volumes are copied in and removed ones taken out. The
  library itself is never changed. It is a copy rather than a link because Docker on Windows cannot link
  files between two folders it has been given.
- asks Komga to look again when new graphic novels or chapters have arrived (by itself Komga only looks
  every six hours).
- writes each series by chapter's state and chapter line onto it in Komga.
- matches up each reader's history, both ways, keeping whichever side has read further (nothing ever goes
  backwards): volumes finished in the reading app before are marked read in Komga, and what is read in
  Paperback goes into Family Hub's reading history and stats. A chapter read on either side is marked read on
  the series server and in Komga. The series server keeps one "read" mark per chapter for the whole household,
  so a chapter read by one person counts for every Komga account listed.

The first time, the copy takes a few minutes (about 16 GB for 120 graphic novels).

## Seeing what's new: My books

Paperback is not good at saying what it has and hasn't got yet, so **Dashboard → My books** has a **For your
graphic novel app** section, using your own Komga login:

- **Added in the last 14 days**: the volumes and chapters Komga has taken in, by series, with how many are still
  unread. Series with something new since you last pressed **Mark all as seen** are marked **New**. Open those
  in Paperback, pull down to refresh, and download what you want to read offline.
- **On the way**: chapters still downloading on the series server, and graphic novels not copied into Komga yet.
- **Everything in Komga**: every series, its state (Ongoing, On hiatus, Ended), and how much you have read.

Each series' name opens it on Komga's own page, which works as a reader in the browser too.

## Switching it on

In the `.env` file:

```
KOMGA_USER=bookhub@bookhub.local
KOMGA_PASS=something-long
COMICS_APP=paperback
```

then in the project folder run:

```
docker compose --profile series up -d
```

With `COMICS_APP=paperback` only one app ever takes graphic novels in: the book-reading app's catalogue
(My books, the whole library and its search) leaves graphic novels and series by chapter out, and Komga only
ever has graphic novels and series by chapter, never other books. Copies the reading app downloaded before
stay on the tablet until you delete them there. Put `COMICS_APP=readest` to send them to the reading app again.

## An account for each reader

Family Hub signs in to Komga with the account in `.env`. Give each reader their own account, so their
progress is their own:

1. Open Komga's page, `http://<the computer's address>:25600`, and sign in with `KOMGA_USER` and `KOMGA_PASS`.
2. Go to **Server → Users** and press **Add user**. Use an email-like name (for example
   `name@familyhub.local`; nothing is ever sent to it) and a password.
3. Tick **Page streaming** and **File download**, and leave the libraries on **All libraries**.
4. Add the login to `KOMGA_ACCOUNTS` in `.env`, after the person's Family Hub profile name, so their reading
   history goes both ways, then run `docker compose up -d`:

   ```
   KOMGA_ACCOUNTS=Sam:sam@familyhub.local:their-password, Alex:alex@familyhub.local:their-password
   ```

## Setting up Paperback on an iPad or iPhone

The iPad has to be able to reach the computer: on the same Wi-Fi, or through Tailscale when away from home.

1. Install **Paperback** from the App Store.
2. Komga support comes with Paperback, as the source called **Paperback**. If it is not there, open
   **Settings → External Sources**, tap **Edit**, then **+**, and enter
   `https://paperback-ios.github.io/extensions-default/0.8/`. Then browse that repository and install
   **Paperback**.
3. In **Settings → External Sources**, tap the **Paperback** source and open **Server Settings**. Fill in:
   - **Server URL**: `http://<the computer's address>:25600`, for example `http://192.168.1.50:25600`
   - **Username** and **Password**: the reader's Komga account
4. Tap **Save**, then **Try Settings** to check it connects.
5. So that what you read is marked read in Komga too: open **Settings → External Trackers**, tap **Edit**,
   then **+**, and enter `https://paperback-ios.github.io/trackers-main/main/`. Then browse it and install
   the **Paperback** tracker.
6. Open the **Paperback** source to see the two libraries: **Graphic novels** and **Series** (the series by
   chapter). Add the series you read to your Paperback library to get them in **Updates**.

Only the Paperback source is needed; Family Hub never adds any others.

These steps follow [Komga's guide for Paperback](https://komga.org/docs/guides/paperback). Button names may
differ a little between Paperback versions.
