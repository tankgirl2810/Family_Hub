// Picks one visual per day. Every device works out the same answer for the same date, with nothing to
// store, and the same visual never shows two days running: each day picks at random from every visual
// except yesterday's, starting from a fixed first day and walking forward to today.

const FIRST_DAY = Date.UTC(2026, 0, 1);   // the walk starts here; any past date works

// a fixed "random" number for each day number, so the result is the same everywhere
function dayRandom(n) {
  n = Math.imul(n ^ (n >>> 16), 0x45d9f3b);
  n = Math.imul(n ^ (n >>> 16), 0x45d9f3b);
  return (n ^ (n >>> 16)) >>> 0;
}

// the visual for a date (local calendar day, so it changes at your midnight)
function visualForDay(visuals, date = new Date()) {
  if (visuals.length < 2) return visuals[0];   // one visual: show it every day (there's nothing to swap to)
  const today = Math.floor((Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) - FIRST_DAY) / 86400000);
  let pick = -1;
  for (let day = 0; day <= Math.max(0, today); day++) {
    const choices = visuals.map((_, i) => i).filter(i => i !== pick);   // everything except yesterday's
    pick = choices[dayRandom(day + 1) % choices.length];
  }
  return visuals[pick];
}
