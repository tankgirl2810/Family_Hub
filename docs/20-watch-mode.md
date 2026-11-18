# 20. Watch mode: films and TV on the big screen

**Watch mode** is Films and TV laid out for the couch: big posters, a full-screen switch, and a
player inside Family Hub, so nobody has to open Jellyfin to watch. Open it with the **Watch mode**
button on **Films and TV → Watch**, or go straight to `…/#/watch` (handy as the bookmark on a TV's
browser).

Guests do not see it. Anyone with a profile who sees Films and TV does.

## Picking something

| Row | What is in it |
|---|---|
| **Carry on watching** | Films and episodes somebody started, with a ribbon showing how far in. Pressing one carries on from there |
| **New on the server** | The twelve newest things that are ready to watch |
| **Films**, **TV shows** | Everything that can be watched, A to Z |
| **On the way** | What has been asked for and is not on the server yet, with how the download is going |

A poster opens that film or show: **Play** (or **Carry on from 41:20** and **Start from the
beginning**), and for a show its seasons and episodes. Episodes that have been watched say so.

It works with a mouse, a finger, or the arrow keys, which is what a TV remote or a keyboard on the
couch sends: the arrows move to the nearest poster or button in that direction, **Enter** (OK on a
remote) presses it, and **Backspace** goes back.

## Full screen

Press **Full screen** in the bar at the top, or the **F** key. It stays on while you move between the
rows, a film's page and the player, and goes off when you press **Leave Watch mode** (or **Esc**).
On an iPhone only a video can fill the screen, so the button is in the player there.

## Asking for something that is not there

**Search and ask** is the same search as **Films and TV → Search**: find a film or show and ask for
it, and it is downloaded to the server. It needs **Can request films and TV** (guide 14). What you
asked for waits under **On the way** until it is ready.

## The player

| Button | Key | What it does |
|---|---|---|
| Play, Pause | **Space** or **K** (OK on a remote) | |
| Back 10 seconds, Forward 30 seconds | **←** **→** (or **J** **L**) | |
| The bar | drag it, or **↑** then **←** **→** | Moves through the film |
| Subtitles | **C** | Off, or any subtitles in words that came with the film or that Bazarr fetched. Your choice is remembered on that device |
| Language | | Shown when the film has more than one sound track |
| Mute | **M** | |
| Next episode | **N** | Shown when the next episode is on the server |
| Full screen | **F** | |
| Back | **Backspace** | Back to the film's page |

The buttons slip away after three seconds and come back when you move the mouse or press a key. At
the end of an episode the next one starts by itself after ten seconds; **Not now** stops that.

**The place is the household's.** Family Hub remembers how far in each film and episode is, for
everyone, because the whole household watches through one Jellyfin account. It also tells Jellyfin,
so Jellyfin's own apps (on a TV, say) carry on from the same place, and a place reached there is
picked up here. A film counts as watched once it is more than 92% through.

## What happens behind it

Jellyfin still keeps the films and does the work; Family Hub asks it how each one can be played in
the browser that is asking, and passes the video through. So the browser only ever talks to Family
Hub, and the Jellyfin key never leaves the server.

- A file the browser can play is passed on as it is.
- Anything else is converted by Jellyfin while it plays. That includes most 2160p films in browsers
  that cannot play HEVC, and films whose sound a browser has no decoder for. Converting a 2160p film
  is heavy work for the server: on a small one it may stutter. A browser that can play HEVC (Edge
  and Chrome on a recent computer, Safari) is sent the picture untouched, which is light.
- Choosing another **Language** always goes through Jellyfin's converter, because a browser cannot
  switch sound tracks inside a file.
- Subtitles drawn as pictures (from discs) are not offered here. Open the film in Jellyfin for those:
  the film's ordinary page under Films and TV still has **Open in Jellyfin**.

For any of it to work, Jellyfin must be connected under **Admin → Hub → Connections → Watching
(Jellyfin)** (guide 14) and must have been through its first-time setup, which makes the
administrator account Family Hub plays as. Until then Watch mode still shows what is on the server
and lets people ask for more; it says that playing is not connected yet.

## If something does not play

| What you see | What to try |
|---|---|
| "Not ready to play yet" on something just downloaded | Jellyfin has not listed it yet. It usually has within a few minutes |
| "This could not be played", **Try again** | The film stopped arriving, or Jellyfin could not start it. **Try again** carries on from the same place. If it keeps happening, an admin can look at Jellyfin's own page (its Dashboard shows what it is converting) |
| It plays but stutters | The server is converting it and cannot keep up. A browser that plays HEVC avoids the conversion; so does asking for a 1080p copy under **Pick a version yourself** on the film's ordinary page |
| No **Full screen** button | That browser does not allow it. On a phone, the button in the player fills the screen with the film |

## What has and has not been tested

Tested in Edge against a stand-in that answers like Jellyfin, with a test clip: the rows, arrow-key
moving, full screen, a file passed on as it is, a converted stream, subtitles, another language,
carrying on, the next episode, leaving the player every way, and a stream that stops. **Not yet
tried:** a real Jellyfin with real films (2160p conversion above all), Safari, an iPhone, and a
television's own browser.
