// Director: picks the robot's expressions from what is said.
//
// The voice state already sets the base mood (app.js). This layers the rest
// on top: flashes of emotion, gestures and arm poses cued by words in the
// user's message and in Jarvis's reply, plus small fidgets while idle so the
// robot never looks frozen. All cues are keyword matches -- cheap, instant,
// and they work the same for Gemini and the pipeline brain.

// What the user says. Checked against the whole turn so far, in priority
// order; each cue fires at most once per turn.
const USER_CUES = [
  { re: /\b(thanks|thank you|thx|shukriya|dhanyavaa?d)\b/, act: (d) => { d.flash("love", 2600); d.pose("hug", 2200); } },
  { re: /\b(love you|you('| a)re (the best|awesome|amazing|smart)|good (job|boy)|well done|great job)\b/, act: (d) => { d.flash("love", 2600); d.play("celebrate"); } },
  { re: /\b(awesome|amazing|yay|woohoo|brilliant|superb|mast|badhiya)\b/, act: (d) => { d.flash("happy", 2200); d.pose("bothUp", 1600); } },
  { re: /\b(hi|hello|namaste|namaskar|good (morning|afternoon|evening))\b/, act: (d) => { d.play("wave"); } },
  { re: /\b(stupid|idiot|useless|dumb|shut up|bekaar|pagal|bakwas)\b/, act: (d) => { d.flash("angry", 1800); d.pose("hips", 1800); } },
  { re: /\b(i('| a)m (sad|tired|sick|upset|lonely)|bad day|udaas|dukhi)\b/, act: (d) => { d.flash("sad", 2600); d.pose("hug", 2600); } },
  { re: /\b(dance|spin|twirl|naach|naacho|ghoom)\b/, act: (d) => { d.play("spin"); d.later(1700, () => d.flash("dizzy", 2200)); } },
  { re: /\b(sneeze|achoo|bless you)\b/, act: (d) => { d.play("sneeze"); } },
  { re: /\b(stretch|t ?pose)\b/, act: (d) => { d.pose("tPose", 2200); } },
  { re: /\b(wow|whoa|really\?|seriously\?|what\?!)/, act: (d) => { d.play("surprise"); } },
];

// What Jarvis says. Checked one sentence at a time as the reply streams in;
// the first matching cue wins, and cues are spaced out so the robot does not
// twitch through a long answer.
const REPLY_CUES = [
  { re: /\b(congratulations|congrats|you did it|well done|mubarak)\b/, act: (d) => { d.pose("victory", 2000); d.flash("happy", 2400); d.play("celebrate"); } },
  { re: /\b(hooray|yay|woohoo)\b/, act: (d) => { d.pose("bothUp", 1600); d.flash("happy", 2000); } },
  { re: /\b(you('| a)re (very |so |most )?welcome|my pleasure|anytime|happy to help|koi baat nahi)\b/, act: (d) => { d.pose("hug", 1800); d.flash("love", 2000); } },
  { re: /^\s*(hi|hello|hey|namaste)\b/, act: (d) => { d.pose("hi", 1600); } },
  { re: /\b(sorry|i('| a)m not sure|i don'?t know|i can'?t|i cannot|unable to|unfortunately|no idea|maaf)\b/, act: (d) => { d.pose("shrug", 1700); d.flash("confused", 1700); } },
  { re: /\b(oops|uh[- ]oh|whoops)\b/, act: (d) => { d.flash("confused", 1500); d.play("shake"); } },
  { re: /\b(warning|careful|danger|watch out|be safe|dhyan)\b/, act: (d) => { d.flash("alert", 1800); d.pose("pointUp", 1600); } },
  { re: /\b(let me think|hmm+|good question|interesting|let me see)\b/, act: (d) => { d.pose("chin", 2000); } },
  { re: /\b(important|remember|note that|tip|pro tip|idea|actually|first(ly)?)\b/, act: (d) => { d.pose("pointUp", 1500); } },
  { re: /\b(here('| i)s|here are|this is|look at|check out|over there|that one)\b/, act: (d) => { d.pose("point", 1500); } },
  { re: /\b(options|either|on one hand|both|welcome|together|everyone)\b/, act: (d) => { d.pose("reachOut", 1600); } },
  { re: /\b(come on|excuse me|well well)\b/, act: (d) => { d.pose("hips", 1500); } },
  { re: /\b(great|awesome|wonderful|glad|perfect|excellent|nice|fun|haha|lol)\b/, act: (d) => { d.flash("happy", 1800); } },
  { re: /\b(love|heart|sweet|cute|pyaar)\b/, act: (d) => { d.flash("love", 1800); } },
  { re: /\b(sad|sorry to hear|that'?s tough|condolences)\b/, act: (d) => { d.flash("sad", 2000); } },
  { re: /\b(dizzy|spinning|round and round)\b/, act: (d) => { d.flash("dizzy", 1800); } },
];

// While idle, now and then: weight, action.
const FIDGETS = [
  [4, (d) => d.bot.blink()],
  [2, (d) => d.bot.wink()],
  [3, (d) => d.play("scan")],
  [2, (d) => d.play("nod")],
  [1, (d) => d.pose("tPose", 1800)],       // a stretch
  [1, (d) => d.pose("hips", 1800)],
  [1, (d) => d.pose("chin", 2200)],
  [1, (d) => d.play("sneeze")],
  [0.5, (d) => { d.play("spin"); d.later(1700, () => d.flash("dizzy", 1800)); }],
  [0.5, (d) => d.play("wave")],
];

// While speaking: the hand and head moves people make as they talk. One of
// these plays on each beat, never the same one twice in a row. Keyword and
// punctuation cues take priority; beats only fill the gaps between them.
const TALK_MOVES = [
  [5, "nod", (d) => d.play("nod")],
  [4, "reachOut", (d) => d.pose("reachOut", 1200)],
  [3, "point", (d) => d.pose("point", 1100)],
  [3, "hi", (d) => d.pose("hi", 1000)],          // one open hand
  [2, "shrug", (d) => d.pose("shrug", 1100)],
  [2, "pointUp", (d) => d.pose("pointUp", 1100)],
  [1.5, "rest", (d) => d.pose("rest", 900)],     // a beat with the hands down
  [1.2, "chin", (d) => d.pose("chin", 1300)],
  [1, "hug", (d) => d.pose("hug", 1100)],
  [1, "hips", (d) => d.pose("hips", 1200)],
  [0.8, "scan", (d) => d.play("scan")],
  [0.6, "bothUp", (d) => d.pose("bothUp", 1000)],
  [0.6, "victory", (d) => d.pose("victory", 1100)],
  [0.5, "tPose", (d) => d.pose("tPose", 900)],
];
// Seconds between beats, per style.
const TALK_BEATS = { calm: [2.4, 4.2], lively: [0.8, 1.6] };

const REPLY_GAP_MS = 1400;   // minimum time between two reply cues
const IDLE_FIRST_S = 18;     // first fidget after this much quiet
const IDLE_EVERY_S = [14, 30];

const pickWeighted = (list, skip) => {
  const pool = list.filter((m) => m[1] !== skip);
  let r = Math.random() * pool.reduce((n, m) => n + m[0], 0);
  for (const m of pool) if ((r -= m[0]) <= 0) return m;
  return pool[0];
};
const pick = (arr) => arr[(Math.random() * arr.length) | 0];

export function createDirector(bot) {
  let poseTimer = 0;
  let userText = "", userFired = new Set();
  let replyBuf = "", lastReplyCue = 0, lastMoveAt = 0, lastMove = "";
  let state = "OFFLINE", quietSince = performance.now(), nextFidget = IDLE_FIRST_S;
  let nextBeat = 0;
  const timers = new Set();
  let fidgets = true;
  let talkStyle = "lively";   // off | calm | lively
  let armLock = null;         // a pose name while arms are in Manual mode

  const d = {
    bot,
    play: (g) => { bot.play(g); lastMoveAt = performance.now(); },
    flash: (m, ms) => bot.flash(m, ms),
    // A manual arm pose for a moment, then back to mood-driven arms. In
    // Manual mode the chosen pose is held and these are ignored.
    pose(name, ms = 1600) {
      lastMoveAt = performance.now();
      if (armLock) return;
      bot.setArmPose(name);
      clearTimeout(poseTimer);
      poseTimer = setTimeout(() => bot.setOptions({ armMode: "auto" }), ms);
    },
    later(ms, fn) {
      const id = setTimeout(() => { timers.delete(id); fn(); }, ms);
      timers.add(id);
    },
  };

  function quiet() { quietSince = performance.now(); nextFidget = IDLE_FIRST_S; }

  function autoArms() {
    clearTimeout(poseTimer);
    if (armLock) bot.setArmPose(armLock);
    else bot.setOptions({ armMode: "auto" });
  }

  // Sentence shape, when no keyword matched: questions open the hands,
  // exclamations lift them, "no" shakes the head, lists count on a finger.
  function shapeCue(s) {
    if (/\b(no|not|never|nope|nahi|nahin|mat)\b/.test(s)) { d.play("shake"); return true; }
    if (/\?\s*$/.test(s)) { d.pose(pick(["shrug", "reachOut"]), 1300); return true; }
    if (/!\s*$/.test(s)) { pick([() => d.pose("bothUp", 1100), () => d.play("surprise"), () => d.pose("victory", 1200)])(); return true; }
    if (/\b(\d+|one|two|three|first|second|third|next|then|finally|also)\b/.test(s)) { d.pose("pointUp", 1200); return true; }
    if (/\b(bye|goodbye|see you|good night|alvida)\b/.test(s)) { d.play("wave"); return true; }
    return false;
  }

  function sentenceCue(sentence) {
    const now = performance.now();
    if (now - lastReplyCue < REPLY_GAP_MS) return;
    const s = sentence.toLowerCase().trim();
    const cue = REPLY_CUES.find((c) => c.re.test(s));
    if (cue) { cue.act(d); lastReplyCue = now; return; }
    if (talkStyle !== "off" && shapeCue(s)) lastReplyCue = now;
  }

  function beat(now) {
    const [lo, hi] = TALK_BEATS[talkStyle];
    if (!nextBeat) { nextBeat = now + 400 + Math.random() * 500; return; }  // settle in first
    if (now < nextBeat) return;
    // Something else just moved the robot: let it finish first.
    if (now - lastMoveAt < lo * 700) { nextBeat = lastMoveAt + lo * 700; return; }
    const move = pickWeighted(TALK_MOVES, lastMove);
    move[2](d);
    lastMove = move[1];
    nextBeat = now + (lo + Math.random() * (hi - lo)) * 1000;
  }

  // Talk beats run fast enough to follow speech; fidgets are checked here too.
  const tick = setInterval(() => {
    const now = performance.now();
    if (state === "SPEAKING" && talkStyle !== "off") { beat(now); quiet(); return; }
    nextBeat = 0;
    const idle = fidgets && state === "IDLE";
    if (!idle) { quiet(); return; }
    const secs = (now - quietSince) / 1000;
    if (secs < nextFidget) return;
    const total = FIDGETS.reduce((n, [w]) => n + w, 0);
    let r = Math.random() * total;
    for (const [w, act] of FIDGETS) { if ((r -= w) <= 0) { act(d); break; } }
    const [flo, fhi] = IDLE_EVERY_S;
    nextFidget = secs + flo + Math.random() * (fhi - flo);
  }, 150);

  return {
    /** Voice state from the server (or OFFLINE / DISCONNECTED locally). */
    state(name) {
      if (name === state) return;
      const was = state;
      state = name;
      quiet();
      // Lost connection: angry, hands on hips until it's back. Reconnecting
      // is celebrated in app.js, with the green glow.
      if (name === "DISCONNECTED") {
        clearTimeout(poseTimer);
        if (!armLock) bot.setArmPose("hips");
      } else if (was === "DISCONNECTED" || was === "SPEAKING" || name === "OFFLINE") {
        autoArms();
      }
    },

    /** A new turn begins: forget the last one's cues. */
    newTurn() {
      userText = ""; userFired = new Set();
      replyBuf = ""; quiet();
    },

    /** User words -- a whole typed message, or one piece of a transcript.
     *  Gemini's pieces carry their own spacing and may split a word, so
     *  they are joined as-is. */
    user(text) {
      userText += text;
      // Only the strongest new match plays -- "thanks, you're awesome"
      // should be one clear reaction, not three fighting over the arms.
      const s = userText.toLowerCase();
      const hits = USER_CUES.map((c, i) => (!userFired.has(i) && c.re.test(s) ? i : -1)).filter((i) => i >= 0);
      if (hits.length) {
        USER_CUES[hits[0]].act(d);
        for (const i of hits) userFired.add(i);
      }
      quiet();
    },

    /** Streaming reply text: cue on each finished sentence. */
    reply(delta) {
      replyBuf += delta;
      const parts = replyBuf.split(/(?<=[.!?।])\s+/);
      replyBuf = parts.pop();
      for (const p of parts) sentenceCue(p);
      // A long run-on with no full stop yet still gets a chance.
      if (replyBuf.length > 160) { sentenceCue(replyBuf); replyBuf = ""; }
      quiet();
    },

    /** The reply is over: whatever is left counts as a sentence. */
    endReply() {
      if (replyBuf.trim()) sentenceCue(replyBuf);
      replyBuf = "";
    },

    /** Turn the idle fidgets on or off. */
    setFidgets(on) { fidgets = !!on; quiet(); },

    /** How much it gestures while talking: "off", "calm" or "lively". */
    setTalkStyle(style) { talkStyle = style === "off" || TALK_BEATS[style] ? style : "lively"; },

    /** Manual arms: hold this pose (a name) until called with null. */
    lockArms(pose) {
      armLock = pose || null;
      autoArms();
    },
    get armLock() { return armLock; },

    /** Buttons: play a gesture, show a mood, or strike a pose right now. */
    play: (g) => d.play(g),
    flash: (m, ms = 2500) => d.flash(m, ms),
    pose(name, ms = 2500) {
      if (armLock) { armLock = name; bot.setArmPose(name); lastMoveAt = performance.now(); return; }
      d.pose(name, ms);
    },

    /** Something happened that is not speech (tools, events). */
    poke() { quiet(); },

    dispose() {
      clearInterval(tick);
      clearTimeout(poseTimer);
      for (const id of timers) clearTimeout(id);
    },
  };
}
