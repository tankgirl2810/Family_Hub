# 3. Profiles: full and restricted

Every person gets a profile. Everyone sees the same family library; a
profile keeps its own reading history, ratings and personal tags.

## The kinds of profile

| | Admin | Member | Restricted | Guest |
|---|---|---|---|---|
| Browse and download books | Yes | Yes | Yes | Yes |
| Reading history, ratings, personal tags | Yes | Yes | Yes | No, nothing is tracked |
| Edit family tags | Yes | Yes (can be switched off) | Yes (can be switched off) | No |
| Search for and download new books | Yes | Yes | No | No |
| See films and TV, Downloads and the Watchlist | Yes | Yes | Yes | No |
| Request films and TV shows ([guide 14](14-films-tv-and-the-vpn.md)) | Yes | If an admin ticks it | If an admin ticks it | No |
| Admin dashboard, add and remove profiles | Yes | No | No | No |
| Own "My books" list and devices | Yes | Yes | Yes | No |
| Change someone else's My books, devices and reading status | Yes | No | No | No |
| Remove a film, show or book from the server | Yes | No | No | No |
| Choose the look of the whole hub | Yes | No | No | No |
| Hide or rename book file and community tags | Yes | No | No | No |
| See and manage copies of the same book | Yes | No | No | No |
| Change a book's cover | Yes | If allowed to edit family tags | If allowed to edit family tags | No |
| Link their own Goodreads profile | Yes | Yes | Yes | No |
| See books the family has only on Kindle | Yes | Yes | Yes | No |
| See whose Kindle books those are | Yes | No | No | No |
| Open another profile, reset passcodes | Yes | No | No | No |

## Who's here?

The whole hub (books, films and TV) has one front door, **Who's here?**:
press your profile and type its passcode. A profile is a person, not a
device: sign in to the same profile on your phone, tablet and laptop. The
hub's look is the same for everyone and only an admin changes it.

"Restricted" is a member with **Can find new books** switched off. It is the
right choice for someone who should just read, such as Nan. On **Who's
here?** and in the Admin table a restricted profile is shown as **Member**;
its door says "Reading only".

To switch profiles, or to sign out, press your name at the top right (it
says **Switch profile** under it). The same page has **Settings for** your
name below the profiles.

On a computer the sections (Home, Movies and TV Shows, Books, Downloads,
Watchlist and the rest) are listed down the left. On a phone, Home, Films &
TV, Books and Downloads are a bar along the bottom of the screen, and the
others open from **More** at the end of it.

## Create the first admin

The first time Family Hub opens it asks for a name and passcode. That profile
is an admin. Make your partner a second admin so either of you can help the
others.

## Add a full profile (for you or your partner)

1. Sign in as an admin.
2. **Admin → Hub → Profiles**.
3. Under the table, enter the name and a passcode, choose **Member** or
   **Admin**, and press **Add profile**.
4. Sign in as that person, press their name at the top right and go to
   **Settings for** their name:
   - **Passcode:** they can change their own passcode here.
   - **Reading app sync:** set a sync password, then follow
     [guide 4](04-syncing.md) on their devices.
   - **Devices and reading apps:** list what they read on, so books can be
     sent to a Kindle or fetched by a reading app
     ([guide 9](09-devices-and-my-books.md)).
   - **Goodreads:** for someone who reads on Kindle and wants it tracked
     automatically ([guide 11](11-goodreads.md)).
   - **Kindle and other reading (experimental):** the older Hardcover route.
     Also in guide 4.

## Add a restricted profile (Nan)

1. **Admin → Hub → Profiles**, add the profile with type **Restricted: read,
   rate and tag only**.
2. Give it a short passcode she will remember, or leave the passcode empty
   if only she uses her tablet. An empty passcode opens with one tap.
3. In the table you can also set **Can edit family tags** to No if you want
   her to rate and track only.
4. Set up her tablet once ([guide 4](04-syncing.md), "Someone who lives
   elsewhere"). After that she only ever taps her name. If she reads on a
   Kindle e-reader, press **Devices** next to her name in the table instead
   and send her books from your own profile
   ([guide 9](09-devices-and-my-books.md)).

What Nan sees: the same sections as everyone else except Admin. On
**Books → Discover** she gets the suggestions but no book search; the page
tells her to ask an admin. Films and shows she can watch, and request only
if an admin ticks **Can request films and TV**.

## Passcodes and admin override

- A passcode is asked for each time a profile is opened.
- Five wrong tries lock that profile for one minute.
- **Forgotten passcode:** on the passcode box press **Admin override**, pick
  an admin, enter the admin's own passcode and press **Open as admin**. The
  profile opens and the foot of the sidebar shows "Opened by an admin". To
  set a new passcode use **Admin → Hub → Profiles → Reset passcode**.
- A passcode keeps family members out of each other's profiles. It is not
  strong security, so do not put Family Hub on the open internet relying on it
  alone (see guide 4 for remote access).

## Guest

The Guest button lets a visitor browse and download without a profile.
Nothing they do is recorded.

- By default it only appears on the home network (`GUEST_LAN_ONLY=true`).
- Turn it off entirely with `GUEST_ENABLED=false` in `.env`.
- If you put Family Hub behind a reverse proxy, also set `TRUST_PROXY=true`,
  otherwise every visitor looks like they are at home.

## Removing a profile

**Admin → Hub → Profiles → Delete**. A backup is written first, so their ratings
and tags can be recovered from `stack/bookhub/backups`.
