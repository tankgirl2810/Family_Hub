# 1. Try it on your PC

There are two levels of test. Do Level 1 first; it takes two minutes and
shows you every screen. Level 2 runs the real setup with your own books.

## Level 1: demo mode (no Docker)

**You need:** Python 3.10 or newer. On Windows, install it from
<https://www.python.org> and tick **Add Python to PATH**.

1. Unzip the project somewhere, for example `Documents\book-hub`.
2. Start it:
   - **Windows:** double-click `scripts\run-demo.bat`
   - **macOS / Linux:** run `scripts/run-demo.sh` in a terminal
3. Open <http://localhost:8090> in a browser.
4. Pick a profile. All three use passcode `1234`:
   - **You** and **Partner** are admins.
   - **Nan** is a restricted profile.
   - **Guest** needs no passcode.

The demo creates a `demo` folder holding twelve placeholder books and some
sample reading history. Delete that folder to start again from scratch.

Things worth trying:

- Library → choose **− Must not have**, click the tag `slow`, then press **Search**.
- Open a book → add a family tag, add a personal tag, give it stars.
- Dashboard → click a filter chip, then switch between Books and Pages.
- Admin → Books → **Back up now**, then **Download book data**.
- Profiles → sign out → open **Nan** using **Admin override**.

Stop it with `Ctrl+C` in the window that is running it.

## Level 2: the real setup, on your PC

This runs the same containers the server will run.

**You need:** [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(Windows or macOS) or Docker Engine (Linux).

> This setup has not been run yet (see the README). If a container fails to
> start, `docker compose logs <name>` shows why.

1. In the project folder, copy `.env.example` to `.env`.
2. Open `.env` in a text editor and check `TZ`. If you already have a Calibre
   library, set `LIBRARY_PATH` to that folder (the one containing
   `metadata.db`). Otherwise leave it and a new library is created.
3. Start Family Hub and the library manager:

   ```bash
   docker compose up -d --build
   ```

4. Open the library manager at <http://localhost:8083>. The default sign-in
   for Calibre-Web is `admin` / `admin123`; change it straight away.
5. Add a few books: drop them into `stack/inbox`. Family Hub passes them to
   the library manager, which adds them to the library within a minute or
   so. See [guide 8](08-adding-and-sorting-books.md) for what the inbox
   accepts.
6. Open Family Hub at <http://localhost:8090>. The first visit asks you to
   create the first admin profile.
7. Add the rest of the family: **Admin → Hub → Profiles**. See [guide 3](03-profiles.md).

Add book search when you are ready: [guide 5](05-finding-new-books.md).

### Checking it from another device

Find your PC's address on the home network (on Windows: `ipconfig`, look for
"IPv4 Address", something like `192.168.1.50`). On a phone or tablet on the
same Wi-Fi open `http://192.168.1.50:8090`. If it does not load, allow
Docker through the PC's firewall.

## When you are happy with it

Go to [guide 2](02-move-to-the-home-server.md). Everything you set up here
(profiles, tags, ratings, history) moves with it.
