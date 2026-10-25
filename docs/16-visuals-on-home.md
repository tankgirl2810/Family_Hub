# 16. Visuals on Home

The animated picture at the top of Home is a **visual**: a small live drawing in the household's colours.
Family Hub comes with six:

| Visual | What it shows | Needs |
|---|---|---|
| Live sky | The real planets around the Sun; a journey from 3,000 years ago, or today | – |
| Earthquakes | This week's earthquakes (USGS) on a turning globe | Internet |
| Weather | Today's wind and temperature at home (Open-Meteo) | Internet, your home town |
| Molecule | A molecule of the day, with its formula and where it is found | – |
| Cells | Living cells dividing under a wandering microscope lens | – |
| Transit line | Mercury and Venus either side of the Sun over two months | – |

By default Home shows a different one each day (never the same as yesterday's), the same on every
device. **Admin → Hub → Appearance → Visual on Home** can pin one instead, and shows a preview.

## Your home town

Weather (and where the globe starts turning) uses the household's home town, set in `.env` or under
**Admin → Hub → Connections → Visuals on Home**:

```
HOME_PLACE=Your town
HOME_LAT=-33.87
HOME_LON=151.21
```

Find the numbers on any map (south of the equator and west of Greenwich are negative). They are only
sent to Open-Meteo for the forecast. Until they are set, Weather is left out of the daily choice.

## Adding your own from GitHub

Visuals can live in a GitHub repository of their own, so new ones arrive without touching Family Hub:

1. Make a public repository with a `visuals/` folder (and optionally `daily.js` and a `lib/` folder). The
   household's own is a good starting point; its `README.md` explains how a visual is written and how to
   try one on its test page.
2. In `.env` (or under Connections) set `VISUALS_REPO=your-name/repository-name`, and `VISUALS_BRANCH`
   if it is not the repository's default branch.
3. Family Hub checks the repository every hour and uses the newest `visuals/`, `lib/` and `daily.js`.
   **Admin → Hub → Appearance → Check for new visuals** checks straight away. Everyone gets the new ones
   the next time they open a page.

If the repository cannot be read, the visuals already in use stay, and Appearance says why. Clearing
`VISUALS_REPO` goes back to the built-in ones.

## Why visuals cannot see anything private

A visual is code, and code from GitHub can change whenever someone pushes to that repository. So each
visual runs in a **sealed frame** (a sandboxed iframe): it can draw, and fetch public data such as the
weather, but it cannot read Family Hub's pages, sign-ins, profiles or settings, or ask Family Hub to do
anything. The page sends it only the colours and the home town. Still, only point `VISUALS_REPO` at a
repository you (or people you trust) control.
