/*
 * ============================================================================
 * GAME CONTENT FILE
 * ============================================================================
 * This is the ONLY file you need to edit to add real content to the game.
 * Everything below is placeholder/sample data so the engine is playable
 * end-to-end right now. Replace/expand it with real cities, real facts,
 * and real criminal identities and the game gets deeper without touching
 * any engine code in game.js.
 *
 * SCHEMA
 * ------
 * CITY:
 *   id        - unique lowercase slug, no spaces (e.g. "paris")
 *   name      - display name (e.g. "Paris")
 *   country   - display country name (e.g. "France")
 *   continent - one of CONTINENTS below
 *   facts     - array of short "flavor fact" strings about the city/country.
 *               These get turned into destination riddles for the player,
 *               e.g. "famous for the Eiffel Tower" becomes a witness clue
 *               like: "I heard them ask for directions to a great iron tower."
 *               Give each city at least 3 distinct, recognizable facts.
 *
 * SUSPECT:
 *   id       - unique slug
 *   name     - crook's full alias (e.g. "Sarah Nade")
 *   gender   - "male" | "female" | "unknown" (unknown = revealed as a clue)
 *   height   - height description (e.g. "5'11\"")
 *   hair     - hair color/description
 *   build    - build/physique description
 *   quirk    - a distinguishing habit/accessory
 *   sport    - a favorite sport, phrased as what the crook does
 *              (e.g. "plays pickup ice hockey every chance they get")
 *   These six traits (gender, height, hair, build, quirk, sport) are the
 *   "warrant attributes." The game reveals them one at a time as description
 *   clues.
 *
 * CASE (optional, for hand-authored cases instead of random ones):
 *   loot      - what was stolen
 *   sceneCityId - which city the crime happened in (first destination)
 *   suspectId - which SUSPECT committed it
 *   trail     - array of city ids, in order, starting with sceneCityId,
 *               ending with the final hideout where the arrest happens.
 * ============================================================================
 */

const CONTINENTS = [
  "Europe",
  "Asia",
  "Africa",
  "North America",
  "South America",
  "Oceania",
];

const CITIES = [
  {
    id: "paris",
    name: "Paris",
    country: "France",
    continent: "Europe",
    facts: [
      "home to a great iron tower built for a world's fair",
      "the river Seine winds through the middle of the city",
      "famous for croissants, baguettes, and fine pastry",
      "houses one of the world's most famous art museums, the Louvre",
    ],
  },
  {
    id: "cairo",
    name: "Cairo",
    country: "Egypt",
    continent: "Africa",
    facts: [
      "sits near ancient pyramids on the edge of the desert",
      "the long river Nile flows right through it",
      "known for open-air bazaars full of spices and lamps",
      "home to a museum full of pharaohs' golden treasures",
    ],
  },
  {
    id: "tokyo",
    name: "Tokyo",
    country: "Japan",
    continent: "Asia",
    facts: [
      "one of the busiest train and subway systems on Earth",
      "cherry blossoms bloom across the city every spring",
      "famous for sushi and ramen shops on every corner",
      "a giant snow-capped mountain is visible on clear days nearby",
    ],
  },
  {
    id: "rio",
    name: "Rio de Janeiro",
    country: "Brazil",
    continent: "South America",
    facts: [
      "a giant statue of a robed figure overlooks the city from a mountain",
      "famous for an enormous carnival parade every year",
      "sits beside beaches with white sand and green mountains",
      "samba music can be heard drifting from open windows",
    ],
  },
  {
    id: "sydney",
    name: "Sydney",
    country: "Australia",
    continent: "Oceania",
    facts: [
      "a harbor opera house shaped like sails sits on the water",
      "kangaroos and koalas are native to the surrounding country",
      "a great coral reef lies off the coast not far away",
      "famous for its harbor bridge, one of the largest steel arches",
    ],
  },
  {
    id: "nairobi",
    name: "Nairobi",
    country: "Kenya",
    continent: "Africa",
    facts: [
      "a national park full of lions and giraffes sits at the city's edge",
      "a major hub for coffee and tea exports",
      "sits at a high elevation with a cool climate for being near the equator",
      "gateway city for safaris across the savanna",
    ],
  },
  {
    id: "moscow",
    name: "Moscow",
    country: "Russia",
    continent: "Europe",
    facts: [
      "home to a red-bricked fortress and square at its center",
      "famous for onion-domed cathedrals in bright colors",
      "winters here are long, snowy, and famously cold",
      "the ballet and circus are beloved local traditions",
    ],
  },
  {
    id: "new-york",
    name: "New York",
    country: "United States",
    continent: "North America",
    facts: [
      "a green statue holding a torch stands on an island in the harbor",
      "famous for towering skyscrapers and a park shaped like a rectangle",
      "a hub for Broadway theater and yellow taxi cabs",
      "immigrants historically passed through an island gateway here",
    ],
  },
  {
    id: "chicago",
    name: "Chicago",
    country: "United States",
    continent: "North America",
    facts: [
      "famous for a deep-dish style of pizza baked in a pan",
      "a giant silver, bean-shaped sculpture reflects the skyline downtown",
      "known as the Windy City, sitting on the shore of a Great Lake",
      "home to one of the tallest skyscrapers in the country",
    ],
  },
  {
    id: "mexico-city",
    name: "Mexico City",
    country: "Mexico",
    continent: "North America",
    facts: [
      "built on the site of an ancient Aztec capital",
      "sits at a very high elevation surrounded by volcanoes",
      "famous for murals, mariachi music, and street tacos",
      "one of the largest and most populous cities in the world",
    ],
  },
  {
    id: "mumbai",
    name: "Mumbai",
    country: "India",
    continent: "Asia",
    facts: [
      "home to the world's largest film industry by number of movies made",
      "a famous stone archway gate faces the harbor",
      "monsoon rains soak the city every summer",
      "spices and street food fill the markets day and night",
    ],
  },
  {
    id: "jerusalem",
    name: "Jerusalem",
    country: "Israel",
    continent: "Asia",
    facts: [
      "an ancient walled Old City sits at its historic heart",
      "sacred sites for three major world religions stand within blocks of each other",
      "known for a bustling open-air market called a shuk",
      "one of the oldest continuously inhabited cities in the world",
    ],
  },
  {
    id: "cape-town",
    name: "Cape Town",
    country: "South Africa",
    continent: "Africa",
    facts: [
      "a flat-topped mountain looms over the whole city",
      "two oceans meet not far from here",
      "penguins live on beaches just outside the city",
      "vineyards cover the nearby valleys",
    ],
  },
  {
    id: "rome",
    name: "Rome",
    country: "Italy",
    continent: "Europe",
    facts: [
      "home to a giant ancient stone arena where gladiators once fought",
      "a tiny independent city-state sits inside its borders",
      "famous for pizza, pasta, and gelato",
      "legend says it was founded by twins raised by a wolf",
    ],
  },
];

const SUSPECTS = [
  {
    id: "sarah-nade",
    name: "Sarah Nade",
    gender: "female",
    height: "5'11\"",
    hair: "fiery red hair",
    build: "tall and athletic",
    quirk: "always seen wearing mirrored sunglasses, even indoors",
    sport: "plays pickup ice hockey every chance she gets",
  },
  {
    id: "rob-yew-blind",
    name: "Rob Yu Blind",
    gender: "male",
    height: "5'5\"",
    hair: "slicked-back black hair",
    build: "short and stocky",
    quirk: "constantly chewing on an unlit cigar",
    sport: "never misses a table tennis tournament",
  },
  {
    id: "polly-ester",
    name: "Polly Ester",
    gender: "female",
    height: "5'6\"",
    hair: "bleached blonde bob",
    build: "average height, wiry",
    quirk: "wears a different loud patterned scarf every day",
    sport: "competes in roller derby under a stage name",
  },
  {
    id: "max-imum",
    name: "Max Imum",
    gender: "male",
    height: "6'4\"",
    hair: "bald, with a thin mustache",
    build: "large and broad-shouldered",
    quirk: "carries an oversized pocket calculator everywhere",
    sport: "arm-wrestles at every dive bar in town",
  },
  {
    id: "ivy-legue",
    name: "Ivy Legue",
    gender: "female",
    height: "5'1\"",
    hair: "silver-streaked black hair in a tight bun",
    build: "petite and precise in movement",
    quirk: "always sipping tea from a tiny porcelain cup",
    sport: "trains as a competitive figure skater",
  },
];

// The mastermind. Caught only on the final case of a playthrough.
const CARMEN = {
  id: "carmen-sandiego",
  name: "Carmen Sandiego",
  gender: "female",
  height: "5'10\"",
  hair: "raven black hair",
  build: "tall, moves like a fencer",
  quirk: "always wears a long red trench coat and matching fedora",
  sport: "fences competitively, épée specialist",
};

/*
 * HAND-AUTHORED CASES (optional)
 * If this array is non-empty, the game will play through these cases in
 * order (one per rank) instead of generating fully random ones. Leave it
 * empty to use pure random case generation from CITIES + SUSPECTS.
 *
 * Example of the shape expected once you supply real content:
 * {
 *   loot: "the Crown Jewels",
 *   sceneCityId: "moscow",
 *   suspectId: "sarah-nade",
 *   trail: ["moscow", "cairo", "rio", "sydney"],
 * }
 */
const CASES = [];

// Minimum number of warrant attributes (out of 6) the player must collect
// before a warrant can be issued for the current suspect. Kept at 4 (rather
// than a stricter 5) because the Rookie/Sleuth ranks only have a 3-city
// trail: gender (given at the briefing) plus one description witness per
// city stop caps out at 4 known attributes there, so a higher requirement
// would make the warrant impossible to obtain on the shorter early cases.
const WARRANT_ATTRIBUTES_REQUIRED = 4;

// Starting time budget, in in-game hours, per rank (index 0 = first case).
const RANKS = [
  { title: "Rookie", timeBudget: 240, trailLength: 3 },
  { title: "Sleuth", timeBudget: 216, trailLength: 3 },
  { title: "Private Eye", timeBudget: 192, trailLength: 4 },
  { title: "Investigator", timeBudget: 168, trailLength: 4 },
  { title: "Ace Detective", timeBudget: 144, trailLength: 5 },
];
