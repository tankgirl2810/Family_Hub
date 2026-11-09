# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A household: two adults who are both admins, and a few other family members (no children's profiles). Guests can browse and download books without anything being tracked. They use Family Hub at home and away, on computers, phones and an iPad (comics and manga by chapter mostly on the iPad). Their jobs: find something to read or watch, keep track of what they are reading and have read, get books onto their devices, and ask for films, TV and books the household does not have yet. The admins also run the server through it: connections, profiles, tidying the library.

## Product Purpose

One private, family-friendly front door to the household's own books, comics, films and TV on its own server, in place of each person juggling the separate programs underneath (Calibre-Web, Jellyfin, Radarr, Sonarr, Prowlarr, qBittorrent and the rest), and without subscriptions. Books, comics, films and TV matter about equally. Success is a family member finding, reading or watching something without ever needing to know which program does what.

## Positioning

It belongs to one household: each profile keeps its own reading, ratings, tags, devices and requests, while the family shares one library and sees what everyone is into. It is built on the household's own server and files, not a service.

## Operating Context

- Runs in Docker on a home server; tested on a Windows PC first. Family members open it in a browser; reading apps (Readest, KOReader) and Kindles get books from it.
- Admins set things up under Admin (Hub, Books); family members never need those screens.
- A demo mode (`--demo`) builds a sample library with profiles You, Partner (admins) and Nan (member), passcode 1234.

## Capabilities and Constraints

- Front end is plain JavaScript and CSS served by the Flask app: no framework, no build step.
- The Calibre library is read-only by default. Family Hub writes to it only through the admin-confirmed tools (duplicates, fix book details, review queue), with a backup first.
- One look for the whole household, chosen by an admin (design, colour scheme, accent). Covers, posters and photos stay in full colour while the rest of the design keeps its one-ink look.
- The repository is public and must stay free of household details: no real names, places or private data, and screenshots come from the demo library.
- Films and TV, downloads, series by chapter and outside catalogues depend on services that may not be connected; every screen has to make sense when they are not.
- Photos and Files are placeholders ("Coming soon"); nothing is faked for them.

## Brand Commitments

- Name: Family Hub (the code package is still `bookhub`).
- Voice: plain, warm, Australian English ("colour", "Films and TV", "Ask an admin"). Tool names (qBittorrent, Radarr, VPN) appear only on admin screens; family members are told what is happening in ordinary words.
- Destructive actions explain what will happen and what safety net exists, in words, before they happen.

## Evidence on Hand

- The demo library (public-domain classics) for screenshots and testing.
- No testimonials, usage figures or external claims exist; none should be invented.

## Product Principles

1. The family's own things come first: books, reading and what is new lead; server plumbing stays with the admins.
2. Every screen works when a service is missing: say plainly what is not there yet and who can add it, never show broken parts.
3. Your data turned into household objects (a shelf of the year, a book's place in the year) beats generic charts and tiles.
4. Nothing irreversible without a clear, named confirmation and a backup.
5. Each person's view is theirs; the household's view is shared.

## Accessibility & Inclusion

No household-specific needs; keep to WCAG 2.2 AA throughout, with 44px touch targets on phones and reduced motion respected.
