# 7. Sharing the project on GitHub

The folder is already laid out as a GitHub project: a README, a licence, a
contributing guide, automated tests that run on every change, and a
`.gitignore` that keeps your private files out.

## What is public and what is private

The project is split so the code can be public while your household stays
private:

| | Where | On GitHub? |
|---|---|---|
| The code, the guides, the template `.env.example` | the project folder | Yes: anyone can use it |
| Every address, password, API key and setting | `.env` (one file) | **Never** |
| Profiles, passcodes, tags, ratings, reading history | `stack/bookhub` | **Never** |
| Each program's own setup (indexers, qBittorrent, Radarr, Sonarr, subtitles, Jellyfin, the library manager's accounts) | `stack/*-config` | **Never** |
| Books, films, chapters | `stack/library`, `DATA_ROOT`, `stack/series` | **Never** |

Someone new copies `.env.example` to `.env`, fills it in and starts. Someone
joining *your* household's setup gets the setup file instead (below), so
they start exactly where you are without redoing any of it.

## The setup file

**Admin → Hub → Setup file** packs your `.env`, the profiles and reading
history, and each program's own setup into one zip locked with a passphrase
(AES-256). No books or films, so it is small (a few MB). Keep it off GitHub:
it holds every password even though it is locked. Hand it over by USB stick,
email or a shared drive, and tell the passphrase separately (in person or by
another app).

To use it, on the computer that will run Family Hub:

1. Get the project folder: `git clone https://github.com/YOUR-NAME/YOUR-REPO.git`
   (or copy the folder).
2. Put the setup file in that folder and unpack it *into* the folder with
   the passphrase:
   - Windows: right-click it, **7-Zip → Extract Here** ([7-Zip](https://www.7-zip.org)
     is free; Windows' own zip tool cannot open locked zips), or run
     `scripts\restore-setup.bat family-hub-setup-....zip`.
   - Linux or macOS: `7z x family-hub-setup-....zip`, or
     `scripts/restore-setup.sh family-hub-setup-....zip` (needs only Docker).
   An `.env` already there is kept as `.env.before-restore`.
3. Open `.env` and check the paths suit this computer (`LIBRARY_PATH`,
   `DATA_ROOT`, `BACKUP_PATH`, `PUID`/`PGID` on Linux). On a test PC the
   defaults (`./stack/...`) are fine.
4. Start it as guide 1 (a PC) or guide 2 (the server) says. Everyone signs in
   with their own profile and passcode as before.

Make a fresh setup file whenever you have changed settings you want to pass
on. It is also a good thing to keep safe as a backup of the whole setup.

## Working on it together

A typical way for a second person to try it, add their own work and then
host it:

1. **Try it on their own computer.** Clone the repository, unpack the setup
   file into it (above), and start it. It has the household's settings and
   profiles but no books or films, so it is a safe place to experiment; the
   VPN, indexers and logins all work as on the original.
2. **Change things on a branch.** `git switch -c my-change`, edit, run the
   tests (`pytest -q`, see `CONTRIBUTING.md`), commit, and
   `git push -u origin my-change`. On GitHub open a **pull request**; the
   tests run by themselves and the other person can look before it is merged.
   A second admin on the repository (**Settings → Collaborators**) can push
   branches directly instead of forking.
3. **Host it on the home server.** Clone the repository there, unpack the
   latest setup file into it, set the server's paths in `.env` (guide 2),
   and start it. Later updates are `git pull` and
   `docker compose up -d --build`; the household's data in `stack/` and the
   `.env` are never touched by that.

Because the code holds nothing private, the repository can stay public and
others can use it too: they bring their own `.env`.

## Your name in the history

Every commit records the name and email address set in Git, and GitHub
shows them publicly. To keep your own email out of future commits, use the
private address GitHub gives you (**Settings → Emails → Keep my email
addresses private** shows it):

```bash
git config user.email "12345678+your-name@users.noreply.github.com"
```

Commits already pushed keep the address they were made with.

## Before the first upload

1. **Put your name in `LICENSE`** in place of `[YOUR NAME]`. The MIT licence
   lets anyone use, change and share the code as long as they keep the
   notice. If you would rather require that changed versions stay open,
   swap it for GPL-3.0 before publishing; changing later is harder.
2. **Check nothing private is included.** These must not be uploaded, and
   `.gitignore` already excludes them: `.env`, any `family-hub-setup-*.zip`,
   `stack/`, `demo/`, `backups/`, and in `themes/` the artwork folders
   (`skinart`, `check`) and the design zip. Your books, profiles, passwords,
   API keys and reading history all live in those. Also check the project
   folder for stray files such as screenshots.
3. Decide on a name. "Family Hub" is generic; search GitHub to see whether you
   want something more distinctive.

## Upload it

You need a free [GitHub](https://github.com) account and
[Git](https://git-scm.com) installed.

1. On GitHub press **New repository**. Name it, leave it **empty** (no
   README, no licence), choose Public, and create it.
2. In a terminal, inside the project folder:

   If the folder is already a Git repository (it has a `.git` folder), skip
   `git init` and `git remote add`, and push to the repository it already
   points at (`git remote -v` shows it).

   ```bash
   git init
   git add .
   git status          # read the list: no .env, no stack/, no demo/
   git commit -m "Family Hub 0.1"
   git branch -M main
   git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git
   git push -u origin main
   ```

3. Open the repository page. The **Actions** tab shows the tests running;
   a green tick means the upload works on a clean machine.

## Make it welcoming

- **About** (top right of the repository page): add a one-line description
  and topics such as `ebooks`, `self-hosted`, `calibre`, `reading-tracker`.
- **Settings → General → Features:** switch on **Issues** (bug reports) and
  **Discussions** (questions and ideas).
- **Who can push:** on GitHub only the owner and the people added under
  **Settings → Collaborators** can push to the repository; everyone else can
  only open issues, fork it, and offer pull requests that you choose whether
  to merge. Add the people who work on it with you there.
- **Settings → Rules → Rulesets:** protect `master` from being deleted or
  overwritten with a force-push (steps under "Protecting master" below).
- Add a screenshot or two to the README. People decide in seconds.
- Keep the "What has and has not been tested" table in the README honest
  and current. It is the most useful thing a new visitor can read.

## Protecting master

1. **Settings → Rules → Rulesets → New ruleset → New branch ruleset.**
2. Name it `Protect master`, set **Enforcement status** to **Active**.
3. Under **Target branches** press **Add target → Include default branch**.
4. Tick **Restrict deletions** and **Block force pushes**. Leave **Require a
   pull request before merging** off if the people working on it push
   directly; switch it on if every change should be looked at by the other
   person first.
5. Optional: **Require status checks to pass**, adding **test**, so a change
   that breaks the tests cannot land on `master`. With this on, changes go
   through a pull request (the tests have to run on it first).
6. Press **Create**.

To overwrite `master` on purpose later (rare), switch the ruleset to
**Disabled** for a moment, then back to **Active**.

## How other people contribute

- **Fork:** they press **Fork**, getting their own copy to change freely.
- **Pull request:** they offer a change back. You see exactly what changed,
  the tests run automatically, and you choose whether to merge it.
- **Tag packs:** the easiest contribution. Someone adds a JSON file to
  `tagpacks/` and everyone who updates gets those community tags.

`CONTRIBUTING.md` explains all this to contributors.

## Releasing a version

When it is in a good state: **Releases → Draft a new release**, create a tag
such as `v0.1.0`, and write two or three lines on what changed. People who
do not use Git can download the zip from there.

## Keeping your own install up to date

On the server, if the folder was cloned from your repository:

```bash
git pull
docker compose up -d --build
```

Your data in `stack/` is never touched by this.
