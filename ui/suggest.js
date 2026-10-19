// Suggested questions: every one is a recorded run (demos/energy-questions.yml),
// so clicking a card replays it instantly and free.
// Empty state: three cards that rotate one at a time. After an answer: three
// still "Ask next" cards from the same topic, unseen ones first.

const ROTATE_MS = 6000;
const FADE_MS = 280;
const LIMITS_TOPIC = "limits";

const suggest = {
  seen: new Set(), // question ids played this visit
  queue: [], // rotation order; refilled (shuffled) when empty, so no repeats until the pool is shown
  slots: [], // the three visible questions in the empty state
  timer: null,
  next: 0, // which slot rotates next
  paused: false,

  topicName(id) {
    if (id === LIMITS_TOPIC) return "Beyond the data";
    return state.topicNames[id] || id;
  },

  card(q, { rotating = false } = {}) {
    return el("button", { type: "button", class: "q-card" + (rotating ? " rotating" : ""), onclick: () => this.play(q) },
      el("span", { class: `q-topic t-${q.topic}`, text: this.topicName(q.topic) }),
      el("span", { class: "q-text", text: q.question }),
      el("span", { class: "q-go", "aria-hidden": "true", text: "▶" }));
  },

  play(q) {
    this.seen.add(q.id);
    playReplay(q);
  },

  // -- empty state: rotating cards -------------------------------------------------

  showStart() {
    this.stop();
    this.slots = [];
    for (let i = 0; i < 3; i++) {
      const q = this.take();
      if (q) this.slots.push(q);
    }
    if (!this.slots.length) return;
    const cards = el("div", { class: "q-cards" }, ...this.slots.map((q) => this.card(q, { rotating: true })));
    const box = el("div", { class: "q-block start" }, el("h3", { text: "Try asking" }), cards);
    box.addEventListener("mouseenter", () => { this.paused = true; });
    box.addEventListener("mouseleave", () => { this.paused = false; });
    box.addEventListener("focusin", () => { this.paused = true; });
    box.addEventListener("focusout", () => { this.paused = false; });
    $("suggestions").replaceChildren(box);
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      this.timer = setInterval(() => this.rotate(cards), ROTATE_MS);
    }
  },

  take() {
    const visible = new Set(this.slots.map((q) => q.id));
    if (!this.queue.length) {
      this.queue = shuffle(state.questions.filter((q) => !visible.has(q.id)));
    }
    return this.queue.shift() || null;
  },

  rotate(cards) {
    if (this.paused || document.hidden || !cards.isConnected) return;
    const i = this.next;
    this.next = (this.next + 1) % this.slots.length;
    const q = this.take();
    const old = cards.children[i];
    if (!q || !old) return;
    this.slots[i] = q;
    old.classList.add("fading");
    setTimeout(() => {
      if (!cards.isConnected) return;
      const fresh = this.card(q, { rotating: true });
      fresh.classList.add("fading");
      old.replaceWith(fresh);
      requestAnimationFrame(() => requestAnimationFrame(() => fresh.classList.remove("fading")));
    }, FADE_MS);
  },

  stop() {
    clearInterval(this.timer);
    this.timer = null;
    this.paused = false;
  },

  // -- after an answer: still, related cards -------------------------------------

  showNext(current) {
    this.stop();
    const pool = state.questions.filter((q) => q.id !== current?.id);
    const rank = (q) =>
      (current?.topic && q.topic === current.topic ? 0 : 2) + (this.seen.has(q.id) ? 1 : 0);
    const picks = shuffle(pool).sort((a, b) => rank(a) - rank(b)).slice(0, 3);
    if (!picks.length) return;
    $("suggestions").replaceChildren(
      el("div", { class: "q-block next" }, el("h3", { text: "Ask next" }), el("div", { class: "q-cards" }, ...picks.map((q) => this.card(q)))));
  },

  clear() {
    this.stop();
    $("suggestions").replaceChildren();
  },
};

function shuffle(items) {
  const a = [...items];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}
