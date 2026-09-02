"use strict";

/* ============================================================================
 * ENGINE — reads its content entirely from data.js. Do not hardcode flavor
 * text/cities/suspects here; add them to data.js instead.
 * ========================================================================== */

const ATTR_KEYS = ["gender", "hair", "build", "quirk", "hobby"];
const ATTR_LABELS = {
  gender: "Gender",
  hair: "Hair",
  build: "Build",
  quirk: "Distinguishing habit",
  hobby: "Hobby/interest",
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

function factToClue(fact) {
  const templates = [
    (f) => `A local told me the crook kept asking about a place ${f}.`,
    (f) => `Someone mentioned the crook seemed very interested in a place ${f}.`,
    (f) => `A witness overheard the crook mention heading somewhere ${f}.`,
    (f) => `The crook bought a guidebook to a place ${f}.`,
  ];
  return pick(templates)(fact);
}

function attrToClue(key, value) {
  const templates = {
    gender: (v) => `Word is the crook is ${v === "female" ? "a woman" : v === "male" ? "a man" : v}.`,
    hair: (v) => `Someone noticed the crook has ${v}.`,
    build: (v) => `A witness described the crook as ${v}.`,
    quirk: (v) => `I noticed the crook was ${v}.`,
    hobby: (v) => `Rumor has it the crook ${v}.`,
  };
  return templates[key](value);
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
  state.knownAttrs = {}; // key -> value, revealed so far
  state.hasWarrant = false;
  state.visitedWitnessesThisCity = new Set();
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

function witnessesForCurrentCity() {
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
  const unrevealed = ATTR_KEYS.filter((k) => !(k in state.knownAttrs));
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
  return `
    <div class="statusbar">
      <span>RANK: ${rank.title}</span>
      <span>CASE: ${state.rankIndex + 1} / ${RANKS.length}</span>
      <span>TIME LEFT: ${state.timeRemaining}h</span>
      <span>WARRANT: ${state.hasWarrant ? "ISSUED" : "none"}</span>
    </div>`;
}

function renderBriefing() {
  const kase = state.currentCase;
  const sceneCity = kase.trail[0];
  setScreen(`
    ${statusBarHtml()}
    <div class="panel">
      <h2>ACME CRIME ALERT</h2>
      <p>${kase.loot} has been stolen! Our only lead: the trail starts in
      <strong>${sceneCity.name}, ${sceneCity.country}</strong>.</p>
      <p>Suspect gender: <strong>${kase.suspect.gender}</strong> (confirmed by witnesses at the scene).</p>
      <p class="muted">Question witnesses to build a warrant profile and follow
      the trail. Time is limited — move quickly but carefully.</p>
      <button onclick="acceptBriefing()">Fly to ${sceneCity.name}</button>
    </div>
  `);
}

function acceptBriefing() {
  state.knownAttrs.gender = state.currentCase.suspect.gender;
  travelTo(state.currentCase.trail[0].id, true);
}

function renderCity() {
  const city = cityById(state.currentCityId);
  const witnesses = witnessesForCurrentCity();
  const atFinal = isAtFinalCity();
  const onTrail = isOnTrail();

  let html = `
    ${statusBarHtml()}
    <div class="panel">
      <h2>${city.name}, ${city.country}</h2>
      <p class="muted">${
        atFinal
          ? "This looks like the crook's current hideout."
          : onTrail
          ? "The trail is still warm here."
          : "Nobody here has heard of the crook — wrong city."
      }</p>
      <h3>Talk to witnesses</h3>
      <div class="btnlist">
        ${witnesses
          .map(
            (w) =>
              `<button onclick="questionWitness('${w.id}')" ${
                state.visitedWitnessesThisCity.has(w.id) ? "class=\"visited\"" : ""
              }>${w.name}${state.visitedWitnessesThisCity.has(w.id) ? " ✓" : ""}</button>`
          )
          .join("")}
      </div>
      <div id="witness-output" class="output"></div>
      <h3>Warrant profile so far</h3>
      <ul class="attrs">
        ${ATTR_KEYS.map(
          (k) =>
            `<li class="${k in state.knownAttrs ? "known" : "unknown"}">${ATTR_LABELS[k]}: ${
              k in state.knownAttrs ? state.knownAttrs[k] : "???"
            }</li>`
        ).join("")}
      </ul>
      <div class="actionrow">
        <button ${canGetWarrant() ? "" : "disabled"} onclick="issueWarrant()">
          ${state.hasWarrant ? "Warrant already issued" : "Request warrant from Interpol"}
        </button>
        ${
          atFinal
            ? `<button class="danger" onclick="attemptArrest()">Attempt arrest</button>`
            : ""
        }
        <button onclick="openTravel()">Travel to another city</button>
        <button onclick="openAlmanac()">Consult almanac</button>
      </div>
    </div>
  `;
  setScreen(html);
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
    state.knownAttrs[w.key] = value;
    line = attrToClue(w.key, value);
  } else if (w.type === "destination") {
    line = factToClue(w.fact);
  } else if (w.type === "final") {
    line = `"That's them! They're holed up right around here — go, go!"`;
  } else {
    line = pick(DEADEND_LINES);
  }

  byId("witness-output").innerHTML = `<p class="clue"><strong>${w.name} says:</strong> ${line}</p>`;
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
    <div class="panel">
      <h2>ACME Travel Bureau</h2>
      <p class="muted">Pick a destination based on the clues you've gathered. Wrong guesses cost time.</p>
      ${CONTINENTS.map(
        (cont) => `
        <h3>${cont}</h3>
        <div class="btnlist">
          ${grouped[cont]
            .map((c) => `<button onclick="travelTo('${c.id}')">${c.name}, ${c.country}</button>`)
            .join("")}
        </div>`
      ).join("")}
      <div class="actionrow">
        <button onclick="render()">Cancel</button>
      </div>
    </div>
  `);
}

function openAlmanac() {
  setScreen(`
    ${statusBarHtml()}
    <div class="panel">
      <h2>World Almanac</h2>
      <p class="muted">Reference facts for every city on file. Cross-check witness clues against these.</p>
      ${CITIES.map(
        (c) => `
        <div class="almanac-entry">
          <strong>${c.name}, ${c.country}</strong> (${c.continent})
          <ul>${c.facts.map((f) => `<li>${f}</li>`).join("")}</ul>
        </div>`
      ).join("")}
      <div class="actionrow">
        <button onclick="render()">Back</button>
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
    }
    // Otherwise this is a wrong-guess detour: trailIndex doesn't move, so
    // isOnTrail() will be false at this new city and witnesses go quiet.
  }

  state.currentCityId = cityId;
  state.visitedWitnessesThisCity = new Set();

  if (checkTimeOut()) return;
  render();
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
    byId("witness-output").innerHTML = `<p class="clue bad">Interpol won't let you make an arrest without a warrant!</p>`;
    return;
  }
  state.gameOver = true;
  state.win = true;
  state.solvedSuspectIds.push(state.currentCase.suspect.id);
  state.score += state.timeRemaining;
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

function renderGameOver() {
  if (state.win) {
    const isFinal = state.rankIndex === RANKS.length - 1;
    setScreen(`
      <div class="panel center">
        <h2>CASE CLOSED</h2>
        <p>You caught <strong>${state.currentCase.suspect.name}</strong> in
        ${cityById(state.currentCityId).name} with time to spare!</p>
        ${
          isFinal
            ? `<p class="clue">You've caught Carmen Sandiego herself. Welcome to the ACME Hall of Fame!</p>
               <p>Final score: ${state.score}</p>
               <button onclick="newGame()">Play again</button>`
            : `<p>Promoted to <strong>${
                RANKS[state.rankIndex + 1] ? RANKS[state.rankIndex + 1].title : RANKS[state.rankIndex].title
              }</strong>!</p>
               <button onclick="nextCase()">Continue to next case</button>`
        }
      </div>
    `);
  } else {
    setScreen(`
      <div class="panel center">
        <h2>CASE GONE COLD</h2>
        <p>You ran out of time. ${state.currentCase.suspect.name} got away.</p>
        <p>The suspect was: <strong>${state.currentCase.suspect.name}</strong>
        (${ATTR_KEYS.map((k) => state.currentCase.suspect[k]).join(", ")})</p>
        <button onclick="newGame()">Start over</button>
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
window.openTravel = openTravel;
window.openAlmanac = openAlmanac;
window.travelTo = travelTo;
window.issueWarrant = issueWarrant;
window.attemptArrest = attemptArrest;
window.newGame = newGame;
window.nextCase = nextCase;
window.render = render;
