# 6. Your data and backups

## Where each kind of information is kept

| Information | Kept in | Notes |
|---|---|---|
| Family tags | Family Hub | Shared by everyone |
| Personal tags | Family Hub | Private to each profile |
| Ratings | Family Hub | One per profile per book |
| Community tags | Family Hub, from public catalogues and `tagpacks/*.json` | Each tag records where it came from ([guide 10](10-tags.md)) |
| Tags you hid or renamed | Family Hub | In the backup file as `tag_rules` |
| Which same-named entries you joined or kept apart | Family Hub | In the backup file as `copy_choices` |
| Covers chosen in Family Hub | Family Hub, as pictures in `stack/bookhub/covers` | **Not** in the backup file, which holds no pictures. Copy the `stack/bookhub` folder to keep them |
| Goodreads links | Family Hub | **Not** in the backup file, to keep them private. Reading history taken from Goodreads is. Paste the link in again after a restore ([guide 11](11-goodreads.md)) |
| Book file tags, synopsis, covers | The Calibre library | Family Hub reads them and leaves out clutter; a snapshot of the tidied tags goes into every backup |
| Reading history and position | Family Hub | |
| "My books" lists | Family Hub | In the backup file as `shelves` |
| Devices and Kindle addresses | Family Hub | Not in the backup file; copy `stack/bookhub` to keep them |
| Series by chapter: the chapters, what is followed, what is read | The series server, in `stack/series` and `stack/suwayomi` | Not in the backup file. Copy both folders; see [guide 13](13-manga-and-series-by-chapter.md) |
| Connection settings (Prowlarr key, mail password) | Family Hub | Not in the backup file, never shown again once saved |
| Profiles and passcodes | Family Hub | Passcodes are stored scrambled, never as text |

"Family Hub" means the single file `stack/bookhub/bookhub.db`.

## Why the data survives a library rebuild

Family Hub does not file your tags under the library's internal book numbers,
which change if the library is rebuilt. It uses the book's ISBN, or its
title and first author when there is no ISBN. Delete a book and add it
again later and its tags, ratings and history reattach by themselves.

The one thing that breaks the link is renaming: if you change a book's
title or author in the library manager and it has no ISBN, Family Hub sees a
different book. Add ISBNs where you can.

## Automatic daily backup

Once a day, and after changes, Family Hub writes

```
stack/bookhub/backups/bookdata-YYYY-MM-DD.json
```

and keeps the last 30 (change with `BACKUP_KEEP`). The file is plain text
you can open and read. For each book it holds:

```json
"ta:pride-and-prejudice|jane-austen": {
  "title": "Pride and Prejudice",
  "author": "Jane Austen",
  "isbn": "",
  "pages": 432,
  "epub_tags": ["Classic", "Romance"],
  "community_tags": [{"tag": "Slow burn", "source": "pack:Starter classics"}],
  "family_tags": ["Class difference"],
  "personal_tags": {"You": ["Comfort re-read"]},
  "ratings": {"You": 4, "Partner": 5},
  "shelves": ["You"],
  "reading": {"You": {"status": "reading", "percent": 62.0, "started": "2026-10-01", "finished": null, "source": "device"}}
}
```

It does not contain passcodes, sync passwords, API keys, the mail password
or Kindle addresses.

## Doing it by hand

**Admin → Books → Book data backup**

- **Back up now** writes today's file immediately.
- **Download book data** saves the same file to your computer.
- **Import**, under **Restore from a book data file**, merges a file back in. It only adds: existing tags, ratings
  and history are never overwritten or removed, so importing twice is safe.

## The nightly copy to a second disk

Family Hub can copy everything to another disk every night. It is off until
you name a folder.

| What | How it is copied |
|---|---|
| Family Hub's data, and the settings of the library manager, the series server, Komga, Prowlarr and qBittorrent | A complete dated copy every night, in `settings/2026-10-11` and so on. None is ever removed. They are a few MB each |
| The book library and the manga chapters | A mirror: only new and changed files are copied. A file that changed or was removed at the source is moved to `removed/2026-10-11` on the copy first, so it is still there |

Family Hub never deletes anything on the second disk. Caches and logs are left
out, as is the library manager's `processed_books` folder (its own copies of
files it has already imported, several GB).

### Switching it on

1. Make an empty folder on the other disk.
2. Put it in the `.env` file:

   ```
   BACKUP_PATH=D:/BookHubCopy
   ```

   On Linux: `BACKUP_PATH=/mnt/disk2/bookhub`.
3. Run `docker compose up -d`.
4. Open **Admin → Hub**. The **Copies to a second disk** card, beside
   **Server** at the bottom, now has a **Copy now** button. Press it for the
   first copy; a large library takes a while the first time.

After that it runs once a day, at the first chance after 2 in the morning.
The same card shows when it last ran, how many files it copied and how much
room the disk has left. **Admin → Hub → Connections → Nightly copy to a
second disk** has the on/off switch (**Copy every night**) and a **Test
connection** button that checks the folder can be written to.

The folder has to be named in `.env` rather than on the Admin page because
Docker must be told about it before Family Hub starts.

If the manga chapters live outside the `stack` folder (`SERIES_PATH`), they
are still copied. The book library is copied from wherever `LIBRARY_PATH`
points.

## Backing up everything by hand

The JSON file covers book data. For a complete copy including profiles and
passcodes, copy the `stack/bookhub` folder:

```bash
scripts/backup.sh /path/to/backup/drive
```

Run it on a schedule if you like (for example weekly with `cron`). Keep at
least one copy on a different machine or drive.

Back up the book files themselves (`stack/library`) with whatever you use
for the rest of the server.

## Restoring

| Situation | What to do |
|---|---|
| Lost a few tags or ratings | Admin → Books → Import an older `bookdata-*.json` |
| Moving to a new machine | Guide 2 |
| The database is damaged | Stop Family Hub, replace `stack/bookhub` with your latest copy, start it |
| Starting clean but keeping history | Fresh install, create the admin, Import the latest `bookdata-*.json` |

## Sharing your tags with other people

Your family tags can become a tag pack for the community: see
[tagpacks/README.md](../tagpacks/README.md). Share tags only; leave ratings,
personal tags and reading history out.
