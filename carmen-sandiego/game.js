"use strict";

/* ============================================================================
 * ENGINE — reads its content entirely from data.js. Do not hardcode flavor
 * text/cities/suspects here; add them to data.js instead.
 * ========================================================================== */

const ATTR_KEYS = ["gender", "height", "hair", "build", "quirk", "sport"];
const ATTR_LABELS = {
  gender: "Gender",
  height: "Height",
  hair: "Hair",
  build: "Build",
  quirk: "Distinguishing habit",
  sport: "Favorite sport",
};

let state = null;

function byId(id) {
  return document.getElementById(id);
}

function cityById(id) {
  return CITIES.find((c) => c.id === id);
}

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function travelCost(fromCity, toCity) {
  if (!fromCity) return 0;
  if (fromCity.id === toCity.id) return 6;
  return fromCity.continent === toCity.continent ? 24 : 54;
}

function cap(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Strips everything but letters/digits and lowercases, so a player's typed
// answer can match the source text regardless of punctuation, quotes, or
// capitalization differences.
function normalizeForCompare(s) {
  return (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function factToClue(fact) {
  const templates = [
    (f) => `A local told me the crook kept asking about a place ${f}.`,
    (f) => `Someone mentioned the crook seemed very interested in a place ${f}.`,
    (f) => `A witness overheard the crook mention heading somewhere ${f}.`,
    (f) => `The crook bought a guidebook to a place ${f}.`,
    (f) => `"They kept talking about a place ${f}," one local recalled.`,
    (f) => `Word is the crook was hunting for tickets to a place ${f}.`,
  ];
  return pick(templates)(fact);
}

function attrToClue(key, value) {
  const templates = {
    gender: [
      (v) => `Word is the crook is ${v === "female" ? "a woman" : v === "male" ? "a man" : v}.`,
      (v) => `Witnesses agree on this much: the crook is ${v === "female" ? "a woman" : v === "male" ? "a man" : v}.`,
    ],
    height: [
      (v) => `A witness pegged the crook's height at about ${v}.`,
      (v) => `Someone guessed the crook stands around ${v} tall.`,
      (v) => `"Not hard to spot in a crowd," one witness said. "About ${v}."`,
    ],
    hair: [
      (v) => `Someone noticed the crook has ${v}.`,
      (v) => `A witness recalled ${v} — hard to miss, they said.`,
      (v) => `"They had ${v}," one bystander remembered clearly.`,
    ],
    build: [
      (v) => `A witness described the crook as ${v}.`,
      (v) => `"Picture someone ${v}," said one onlooker.`,
      (v) => `Build-wise, more than one person called them ${v}.`,
    ],
    quirk: [
      (v) => `I noticed the crook was ${v}.`,
      (v) => `One odd detail stuck with a witness: the crook was ${v}.`,
      (v) => `"You'd remember them," said a witness. "${cap(v)}."`,
    ],
    sport: [
      (v) => `Rumor has it the crook ${v}.`,
      (v) => `Word around town is the crook ${v}.`,
      (v) => `A witness mentioned, almost in passing, that the crook ${v}.`,
    ],
  };
  return pick(templates[key])(value);
}

/* --------------------------------------------------------------------------
 * CASE GENERATION
 * ------------------------------------------------------------------------ */

function generateCase(rankIndex) {
  const rank = RANKS[rankIndex];
  const usedIds = state ? state.solvedSuspectIds : [];
  const isFinalCase = rankIndex === RANKS.length - 1;

  let suspect;
  if (isFinalCase) {
    suspect = CARMEN;
  } else {
    const pool = SUSPECTS.filter((s) => !usedIds.includes(s.id));
    suspect = pick(pool.length ? pool : SUSPECTS);
  }

  const authored = CASES[rankIndex];
  if (authored) {
    return {
      loot: authored.loot,
      suspect: SUSPECTS.find((s) => s.id === authored.suspectId) || suspect,
      trail: authored.trail.map(cityById),
      rankIndex,
    };
  }

  const trailCities = shuffle(CITIES).slice(0, rank.trailLength);
  const loot = pick([
    "a priceless painting",
    "a rare diamond necklace",
    "an ancient golden mask",
    "a one-of-a-kind manuscript",
    "a jeweled royal crown",
    "a legendary trophy",
    "a vintage sports car",
    "a chest of pirate gold",
  ]);

  return {
    loot,
    suspect,
    trail: trailCities,
    rankIndex,
  };
}

/* --------------------------------------------------------------------------
 * STATE
 * ------------------------------------------------------------------------ */

function newGame() {
  byId("titlecard").classList.add("hidden");
  state = {
    rankIndex: 0,
    solvedSuspectIds: [],
    score: 0,
    currentStreak: 0,
    stats: { bestStreak: 0, wrongTurns: 0, hintsUsed: 0, falseArrests: 0 },
  };
  startCase();
}

function startCase() {
  const kase = generateCase(state.rankIndex);
  const rank = RANKS[state.rankIndex];

  state.currentCase = kase;
  state.trailIndex = 0; // player is heading to kase.trail[0]
  state.currentCityId = null; // not arrived anywhere yet
  state.timeRemaining = rank.timeBudget;
  state.knownAttrs = {}; // key -> value, but only once the player has correctly picked it
  state.revealedAttrs = new Set(); // keys a witness has mentioned, whether or not picked yet
  state.attrOptions = {}; // key -> shuffled [correct value, ...decoys], cached per case
  state.hasWarrant = false;
  state.visitedWitnessesThisCity = new Set();
  state.cityWitnesses = null;
  state.lineup = null;
  state.gameOver = false;
  state.win = false;

  render();
}

// True once the player has actually arrived at the correct next stop on the
// trail (as opposed to a wrong-guess detour, which leaves them off-trail).
function isOnTrail() {
  const trail = state.currentCase.trail;
  return !!state.currentCityId && state.currentCityId === trail[state.trailIndex].id;
}

function isAtFinalCity() {
  return isOnTrail() && state.trailIndex === state.currentCase.trail.length - 1;
}

/* --------------------------------------------------------------------------
 * WITNESSES
 * ------------------------------------------------------------------------ */

// Odds that a description witness and a destination witness turn out to be
// the same chatty person, handing over both clues for the price of one.
const HOT_TIP_CHANCE = 0.3;

// The witness list for the current city is rolled once on arrival and then
// cached (state.cityWitnesses), so it can't reshuffle itself — including the
// rare "hot tip" merge below — between renders while the player is still
// deciding who to talk to.
function witnessesForCurrentCity() {
  if (!state.cityWitnesses) {
    state.cityWitnesses = generateWitnessesForCity();
  }
  return state.cityWitnesses;
}

function generateWitnessesForCity() {
  const kase = state.currentCase;

  // A wrong-guess detour: nobody here has ever heard of the crook.
  if (!isOnTrail()) {
    return [
      { id: "w-deadend-1", name: "A shopkeeper", type: "deadend" },
      { id: "w-deadend-2", name: "A passerby", type: "deadend" },
      { id: "w-deadend-3", name: "A hotel clerk", type: "deadend" },
    ];
  }

  const atFinal = isAtFinalCity();
  const nextCity = atFinal ? null : kase.trail[state.trailIndex + 1];

  const list = [];

  // Description clue witness (one unrevealed attribute), unless all revealed.
  const unrevealed = ATTR_KEYS.filter((k) => !state.revealedAttrs.has(k));
  if (unrevealed.length) {
    list.push({
      id: "w-desc",
      name: "A nervous shopkeeper",
      type: "description",
      key: unrevealed[0],
    });
  }

  if (!atFinal && nextCity) {
    const facts = shuffle(nextCity.facts).slice(0, 2);
    facts.forEach((f, i) => {
      list.push({
        id: "w-dest-" + i,
        name: i === 0 ? "A cab driver" : "A hotel clerk",
        type: "destination",
        fact: f,
      });
    });
  }

  // Rare "hot tip": merge the description witness and one destination
  // witness into a single chatty informant who hands over both clues.
  const descIdx = list.findIndex((w) => w.type === "description");
  const destIdx = list.findIndex((w) => w.type === "destination");
  if (descIdx !== -1 && destIdx !== -1 && Math.random() < HOT_TIP_CHANCE) {
    const desc = list[descIdx];
    const dest = list[destIdx];
    const merged = list.filter((_, i) => i !== descIdx && i !== destIdx);
    merged.push({
      id: "w-hottip",
      name: pick(PLAIN_WITNESS_NAMES),
      type: "combo",
      key: desc.key,
      fact: dest.fact,
    });
    list.length = 0;
    list.push(...merged);
  }

  if (atFinal) {
    list.push({
      id: "w-final",
      name: "A jittery bystander",
      type: "final",
    });
  }

  // A dead-end witness for flavor/misdirection, even on the right trail.
  list.push({
    id: "w-deadend-1",
    name: "A street vendor",
    type: "deadend",
  });

  return list;
}

const DEADEND_LINES = [
  "\"Sorry, never saw anyone like that.\"",
  "\"You should ask down at the market, not me.\"",
  "\"I've got nothing for you today.\"",
  "\"Can't help you — try someone else around here.\"",
  "\"Wish I could help, but you've got the wrong person.\"",
  "\"No idea what you're talking about, honestly.\"",
];

const HOT_TIP_INTROS = [
  "Lucky break —",
  "Turns out this one's a talker —",
  "Didn't expect this, but —",
];

// Deliberately mundane names, so a hot-tip witness looks exactly like any
// other witness in the list until the player actually questions them.
const PLAIN_WITNESS_NAMES = [
  "A local shopkeeper",
  "A curious bystander",
  "A market vendor",
  "A friendly local",
  "An off-duty guide",
];

/* --------------------------------------------------------------------------
 * RENDERING
 * ------------------------------------------------------------------------ */

function render() {
  if (!state) return;
  byId("app").classList.remove("hidden");

  if (state.gameOver) {
    renderGameOver();
    return;
  }

  if (!state.currentCityId) {
    renderBriefing();
    return;
  }

  renderCity();
}

function setScreen(html) {
  byId("screen").innerHTML = html;
}

function statusBarHtml() {
  const rank = RANKS[state.rankIndex];
  const timePct = Math.max(0, Math.min(100, Math.round((state.timeRemaining / rank.timeBudget) * 100)));
  return `
    <div class="statgrid">
      <div class="stat">
        <div class="stat-label">Rank</div>
        <div class="stat-value">${rank.title}</div>
      </div>
      <div class="stat">
        <div class="stat-label">Case</div>
        <div class="stat-value">${state.rankIndex + 1} of ${RANKS.length}</div>
      </div>
      <div class="stat">
        <div class="stat-label">Time left</div>
        <div class="stat-value">${state.timeRemaining}h</div>
        <div class="progress-track"><div class="progress-fill ${timePct <= 25 ? "low" : ""}" style="width:${timePct}%"></div></div>
      </div>
      <div class="stat">
        <div class="stat-label">Warrant</div>
        <div class="stat-value ${state.hasWarrant ? "badge-on" : "badge-off"}">${state.hasWarrant ? "Issued" : "None"}</div>
      </div>
      <div class="stat">
        <div class="stat-label">Streak</div>
        <div class="stat-value ${state.currentStreak > 0 ? "badge-on" : "badge-off"}">${
    state.currentStreak > 0 ? "🔥 " + state.currentStreak : "—"
  }</div>
      </div>
    </div>`;
}

function renderBriefing() {
  const kase = state.currentCase;
  const sceneCity = kase.trail[0];
  setScreen(`
    ${statusBarHtml()}
    <div class="card">
      <div class="card-title">ACME Crime Alert</div>
      <p>${kase.loot} has been stolen! Our only lead: the trail starts in
      <strong>${sceneCity.name}, ${sceneCity.country}</strong>.</p>
      <p>Suspect gender: <strong>${kase.suspect.gender}</strong> (confirmed by witnesses at the scene).</p>
      <p class="muted">Question witnesses to build a warrant profile and follow
      the trail. Time is limited — move quickly but carefully.</p>
      <div class="actionrow">
        <button class="btn btn-primary" onclick="acceptBriefing()">Fly to ${sceneCity.name}</button>
      </div>
    </div>
  `);
}

function acceptBriefing() {
  // Gender comes straight from the case briefing, not from an investigation
  // step, so it's the one attribute that's known immediately rather than
  // requiring the player to type it in.
  state.knownAttrs.gender = state.currentCase.suspect.gender;
  state.revealedAttrs.add("gender");
  travelTo(state.currentCase.trail[0].id, true);
}

function renderCity() {
  const city = cityById(state.currentCityId);
  const witnesses = witnessesForCurrentCity();
  const atFinal = isAtFinalCity();
  const onTrail = isOnTrail();

  let html = `
    ${statusBarHtml()}
    <div class="card">
      <div class="card-title">${city.name}, ${city.country}</div>
      <p class="card-subtitle">${
        atFinal
          ? "This looks like the crook's current hideout."
          : onTrail
          ? "The trail is still warm here."
          : "Nobody here has heard of the crook — wrong city."
      }</p>
      <div class="section-label">Talk to witnesses</div>
      <div class="chip-grid">
        ${witnesses
          .map(
            (w) =>
              `<button class="chip ${state.visitedWitnessesThisCity.has(w.id) ? "visited" : ""}" onclick="questionWitness('${w.id}')">${w.name}${
                state.visitedWitnessesThisCity.has(w.id) ? " ✓" : ""
              }</button>`
          )
          .join("")}
      </div>
      <div id="witness-output" class="clue-feed"></div>
      <div class="section-label">Warrant profile so far</div>
      <p class="muted" style="margin-top:-4px;">Pick what a witness told you to confirm it on the warrant.</p>
      <ul class="attr-grid">
        ${ATTR_KEYS.map((k) => attrRowHtml(k)).join("")}
      </ul>
      <div class="actionrow">
        <button class="btn ${canGetWarrant() ? "btn-primary" : ""}" ${canGetWarrant() ? "" : "disabled"} onclick="issueWarrant()">
          ${state.hasWarrant ? "Warrant already issued" : "Request warrant from Interpol"}
        </button>
        ${
          atFinal
            ? `<button class="btn btn-danger" onclick="attemptArrest()">Attempt arrest</button>`
            : ""
        }
        <button class="btn" onclick="openTravel()">Travel to another city</button>
        <button class="btn" onclick="openAlmanac()">Consult almanac</button>
        ${
          !atFinal && onTrail
            ? `<button class="btn" onclick="requestHint()">📡 Call ACME HQ (-20h)</button>`
            : ""
        }
      </div>
    </div>
  `;
  setScreen(html);
}

// A couple of decoy values pulled from other suspects' (and Carmen's) same
// attribute, so the dropdown isn't a giveaway of exactly one real option.
function buildAttrOptions(key, correctValue) {
  const pool = SUSPECTS.concat([CARMEN])
    .map((s) => s[key])
    .filter((v) => normalizeForCompare(v) !== normalizeForCompare(correctValue));
  const uniquePool = Array.from(new Map(pool.map((v) => [normalizeForCompare(v), v])).values());
  const decoys = shuffle(uniquePool).slice(0, 2);
  return shuffle([correctValue, ...decoys]);
}

function attrRowHtml(k) {
  if (k in state.knownAttrs) {
    return `<li class="attr-item known">
      <span class="attr-mark">✓</span>
      <span class="attr-label">${ATTR_LABELS[k]}:</span>
      <span class="attr-value">${state.knownAttrs[k]}</span>
    </li>`;
  }
  if (state.revealedAttrs.has(k)) {
    if (!state.attrOptions[k]) {
      state.attrOptions[k] = buildAttrOptions(k, state.currentCase.suspect[k]);
    }
    const options = state.attrOptions[k];
    return `<li class="attr-item pending">
      <div class="attr-item-top">
        <span class="attr-mark">?</span>
        <span class="attr-label">${ATTR_LABELS[k]}:</span>
      </div>
      <select id="attr-select-${k}" class="attr-select" onchange="submitAttr('${k}')">
        <option value="" disabled selected>What did the witness say?</option>
        ${options.map((v) => `<option value="${v.replace(/"/g, "&quot;")}">${v}</option>`).join("")}
      </select>
      <div class="attr-error-msg" id="attr-error-${k}"></div>
    </li>`;
  }
  return `<li class="attr-item unknown">
    <span class="attr-mark">?</span>
    <span class="attr-label">${ATTR_LABELS[k]}:</span>
    <span class="attr-value">unknown</span>
  </li>`;
}

function submitAttr(key) {
  const select = byId("attr-select-" + key);
  if (!select || !select.value) return;
  const guess = normalizeForCompare(select.value);
  const actual = normalizeForCompare(state.currentCase.suspect[key]);
  if (guess === actual) {
    state.knownAttrs[key] = state.currentCase.suspect[key];
    render_status_only();
  } else {
    select.value = "";
    const err = byId("attr-error-" + key);
    if (err) err.textContent = "That's not what the witness described — try again.";
  }
}

function canGetWarrant() {
  return (
    !state.hasWarrant &&
    ATTR_KEYS.filter((k) => k in state.knownAttrs).length >= WARRANT_ATTRIBUTES_REQUIRED
  );
}

function questionWitness(witnessId) {
  const witnesses = witnessesForCurrentCity();
  const w = witnesses.find((x) => x.id === witnessId);
  if (!w) return;
  state.visitedWitnessesThisCity.add(witnessId);
  spendTime(4);

  let line;
  if (w.type === "description") {
    const value = state.currentCase.suspect[w.key];
    state.revealedAttrs.add(w.key);
    line = attrToClue(w.key, value);
  } else if (w.type === "destination") {
    line = factToClue(w.fact);
  } else if (w.type === "combo") {
    const value = state.currentCase.suspect[w.key];
    state.revealedAttrs.add(w.key);
    line = `${pick(HOT_TIP_INTROS)} ${attrToClue(w.key, value)} And get this: ${factToClue(w.fact)}`;
  } else if (w.type === "final") {
    line = `"That's them! They're holed up right around here — go, go!"`;
  } else {
    line = pick(DEADEND_LINES);
  }

  byId("witness-output").innerHTML = `<div class="clue"><span class="clue-icon">💬</span><div class="clue-body"><strong>${w.name}</strong>${line}</div></div>`;
  checkTimeOut();
  render_status_only();
}

// Re-render just enough to update status bar / warrant list without wiping
// the witness-output line we just set.
function render_status_only() {
  const output = byId("witness-output") ? byId("witness-output").innerHTML : "";
  renderCity();
  const el = byId("witness-output");
  if (el) el.innerHTML = output;
}

function openTravel() {
  const grouped = {};
  CONTINENTS.forEach((c) => (grouped[c] = []));
  CITIES.forEach((c) => grouped[c.continent].push(c));

  setScreen(`
    ${statusBarHtml()}
    <div class="card">
      <div class="card-title">ACME Travel Bureau</div>
      <p class="card-subtitle">Pick a destination based on the clues you've gathered. Wrong guesses cost time.</p>
      ${CONTINENTS.map(
        (cont) => `
        <div class="continent-group">
          <div class="section-label">${cont}</div>
          <div class="chip-grid">
            ${grouped[cont]
              .map((c) => `<button class="chip" onclick="travelTo('${c.id}')">${c.name}, ${c.country}</button>`)
              .join("")}
          </div>
        </div>`
      ).join("")}
      <div class="actionrow">
        <button class="btn" onclick="render()">Cancel</button>
      </div>
    </div>
  `);
}

function openAlmanac() {
  setScreen(`
    ${statusBarHtml()}
    <div class="card">
      <div class="card-title">World Almanac</div>
      <p class="card-subtitle">Reference facts for every city on file. Cross-check witness clues against these.</p>
      ${CITIES.map(
        (c) => `
        <div class="almanac-entry">
          <span class="almanac-title">${c.name}, ${c.country}</span>
          <span class="almanac-country"> — ${c.continent}</span>
          <ul>${c.facts.map((f) => `<li>${f}</li>`).join("")}</ul>
        </div>`
      ).join("")}
      <div class="actionrow">
        <button class="btn" onclick="render()">Back</button>
      </div>
    </div>
  `);
}

function travelTo(cityId, isFirstTravel) {
  const dest = cityById(cityId);
  const from = state.currentCityId ? cityById(state.currentCityId) : null;
  const cost = isFirstTravel ? 12 : travelCost(from, dest);
  spendTime(cost);

  const trail = state.currentCase.trail;

  if (!isFirstTravel) {
    const nextOnTrail = state.trailIndex < trail.length - 1 ? trail[state.trailIndex + 1] : null;
    if (nextOnTrail && nextOnTrail.id === cityId) {
      state.trailIndex++;
      state.currentStreak++;
      state.stats.bestStreak = Math.max(state.stats.bestStreak, state.currentStreak);
    } else {
      // Wrong-guess detour: trailIndex doesn't move, so isOnTrail() will be
      // false at this new city and witnesses go quiet.
      state.currentStreak = 0;
      state.stats.wrongTurns++;
    }
  }

  state.currentCityId = cityId;
  state.visitedWitnessesThisCity = new Set();
  state.cityWitnesses = null;

  if (checkTimeOut()) return;
  render();
}

function requestHint() {
  if (isAtFinalCity() || !isOnTrail()) return;
  const nextCity = state.currentCase.trail[state.trailIndex + 1];
  spendTime(20);
  state.stats.hintsUsed++;
  if (checkTimeOut()) return;
  render();
  byId("witness-output").innerHTML = `<div class="clue hint"><span class="clue-icon">📡</span><div class="clue-body"><strong>ACME HQ</strong>Satellite traces show them heading toward ${nextCity.continent}.</div></div>`;
}

function issueWarrant() {
  if (!canGetWarrant()) return;
  spendTime(6);
  state.hasWarrant = true;
  if (checkTimeOut()) return;
  render();
}

function attemptArrest() {
  if (!isAtFinalCity()) return;
  if (!state.hasWarrant) {
    byId("witness-output").innerHTML = `<div class="clue bad"><span class="clue-icon">🚫</span><div class="clue-body">Interpol won't let you make an arrest without a warrant!</div></div>`;
    return;
  }
  renderLineup();
}

// The lineup: the real suspect plus a couple of decoys pulled from the rest
// of the roster, so the warrant profile actually has to be matched rather
// than the arrest being a formality.
function buildLineup() {
  const kase = state.currentCase;
  const pool = SUSPECTS.concat([CARMEN]).filter((s) => s.id !== kase.suspect.id);
  const decoys = shuffle(pool).slice(0, 2);
  return shuffle([kase.suspect, ...decoys]);
}

function renderLineup() {
  if (!state.lineup) state.lineup = buildLineup();
  setScreen(`
    ${statusBarHtml()}
    <div class="card">
      <div class="card-title">Lineup at the local precinct</div>
      <p class="card-subtitle">Match the warrant profile to the right suspect. Pick wrong and they'll slip away in the confusion.</p>
      <div class="lineup-grid">
        ${state.lineup
          .map(
            (s) => `
          <div class="lineup-card">
            <div class="lineup-name">${s.name}</div>
            <ul class="lineup-traits">
              ${ATTR_KEYS.map((k) => `<li>${ATTR_LABELS[k]}: ${s[k]}</li>`).join("")}
            </ul>
            <button class="btn btn-primary btn-full" onclick="confirmArrest('${s.id}')">This is them — arrest</button>
          </div>`
          )
          .join("")}
      </div>
      <div class="actionrow">
        <button class="btn" onclick="cancelLineup()">Back without arresting</button>
      </div>
    </div>
  `);
}

function cancelLineup() {
  state.lineup = null;
  render();
}

function confirmArrest(suspectId) {
  const kase = state.currentCase;
  state.lineup = null;
  if (suspectId === kase.suspect.id) {
    finalizeWin();
    return;
  }
  state.stats.falseArrests++;
  spendTime(12);
  if (checkTimeOut()) return;
  render();
  byId("witness-output").innerHTML = `<div class="clue bad"><span class="clue-icon">🙅</span><div class="clue-body">Wrong suspect! In the confusion, the real crook slips further away.</div></div>`;
}

function finalizeWin() {
  state.gameOver = true;
  state.win = true;
  state.solvedSuspectIds.push(state.currentCase.suspect.id);
  state.score += state.timeRemaining + state.currentStreak * 5;
  render();
}

function spendTime(hours) {
  state.timeRemaining = Math.max(0, state.timeRemaining - hours);
}

function checkTimeOut() {
  if (state.timeRemaining <= 0) {
    state.gameOver = true;
    state.win = false;
    render();
    return true;
  }
  return false;
}

// A title based on how the whole playthrough went, shown only at the true
// end of the game (final win or a loss), so it doesn't slow down the
// case-to-case pace.
function reportCardTitle(stats) {
  if (stats.wrongTurns === 0 && stats.hintsUsed === 0 && stats.falseArrests === 0) {
    return "Globetrotter Prodigy";
  }
  if (stats.falseArrests >= 2) return "Trigger-Happy Rookie";
  if (stats.hintsUsed >= 3) return "Persistent Investigator";
  if (stats.wrongTurns >= 3) return "Scenic Route Specialist";
  return "Solid Detective";
}

function reportCardHtml() {
  const s = state.stats;
  return `
    <div class="section-label">Case file report</div>
    <ul class="attr-grid">
      <li class="attr-item known"><span class="attr-mark">🏅</span><span class="attr-label">Title:</span><span class="attr-value">${reportCardTitle(s)}</span></li>
      <li class="attr-item known"><span class="attr-mark">🔥</span><span class="attr-label">Best streak:</span><span class="attr-value">${s.bestStreak}</span></li>
      <li class="attr-item ${s.wrongTurns ? "unknown" : "known"}"><span class="attr-mark">🧭</span><span class="attr-label">Wrong turns:</span><span class="attr-value">${s.wrongTurns}</span></li>
      <li class="attr-item ${s.hintsUsed ? "unknown" : "known"}"><span class="attr-mark">📡</span><span class="attr-label">Hints used:</span><span class="attr-value">${s.hintsUsed}</span></li>
      <li class="attr-item ${s.falseArrests ? "unknown" : "known"}"><span class="attr-mark">🙅</span><span class="attr-label">False arrests:</span><span class="attr-value">${s.falseArrests}</span></li>
    </ul>`;
}

function renderGameOver() {
  if (state.win) {
    const isFinal = state.rankIndex === RANKS.length - 1;
    setScreen(`
      <div class="card center">
        <div class="eyebrow" style="display:inline-block;font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--success);background:var(--success-soft);padding:4px 12px;border-radius:999px;margin-bottom:14px;">Case closed</div>
        <div class="card-title">You got them!</div>
        <p>You caught <strong>${state.currentCase.suspect.name}</strong> in
        ${cityById(state.currentCityId).name} with time to spare!</p>
        ${
          isFinal
            ? `<div class="clue" style="text-align:left;"><span class="clue-icon">🏆</span><div class="clue-body">You've caught Carmen Sandiego herself. Welcome to the ACME Hall of Fame!</div></div>
               <p>Final score: <strong>${state.score}</strong></p>
               <div style="text-align:left;">${reportCardHtml()}</div>
               <div class="actionrow" style="justify-content:center;"><button class="btn btn-primary" onclick="newGame()">Play again</button></div>`
            : `<p>Promoted to <strong>${
                RANKS[state.rankIndex + 1] ? RANKS[state.rankIndex + 1].title : RANKS[state.rankIndex].title
              }</strong>!</p>
               <div class="actionrow" style="justify-content:center;"><button class="btn btn-primary" onclick="nextCase()">Continue to next case</button></div>`
        }
      </div>
    `);
  } else {
    setScreen(`
      <div class="card center">
        <div class="card-title">Case gone cold</div>
        <p>You ran out of time. ${state.currentCase.suspect.name} got away.</p>
        <p class="muted">The suspect was: <strong>${state.currentCase.suspect.name}</strong>
        (${ATTR_KEYS.map((k) => state.currentCase.suspect[k]).join(", ")})</p>
        <div style="text-align:left;">${reportCardHtml()}</div>
        <div class="actionrow" style="justify-content:center;"><button class="btn btn-primary" onclick="newGame()">Start over</button></div>
      </div>
    `);
  }
}

function nextCase() {
  state.rankIndex++;
  startCase();
}

window.acceptBriefing = acceptBriefing;
window.questionWitness = questionWitness;
window.submitAttr = submitAttr;
window.openTravel = openTravel;
window.openAlmanac = openAlmanac;
window.travelTo = travelTo;
window.issueWarrant = issueWarrant;
window.attemptArrest = attemptArrest;
window.confirmArrest = confirmArrest;
window.cancelLineup = cancelLineup;
window.requestHint = requestHint;
window.newGame = newGame;
window.nextCase = nextCase;
window.render = render;
