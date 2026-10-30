import assert from "node:assert/strict";
import { test } from "node:test";

import { proposalLine, threatBadge, trackCells } from "../../web/js/gozcu.js";

const value = (value, text = String(value), more = {}) => ({
  value,
  text,
  reason: null,
  age: 0.2,
  stale: false,
  level: null,
  ...more,
});

function heard(state, more = {}) {
  return { heard: true, present: true, fields: { state }, ...more };
}

test("the threat badge says the state, coloured by its meaning", () => {
  assert.deepEqual(threatBadge(heard(value(0, "CLEAR"))), { text: "CLEAR", level: "" });
  assert.deepEqual(threatBadge(heard(value(1, "ADVISORY"))), { text: "ADVISORY", level: "warn" });
  assert.deepEqual(threatBadge(heard(value(2, "AVOID"))), { text: "AVOID", level: "crit" });
  assert.equal(threatBadge(heard(value(3, "CLEAR OF CONFLICT"))).level, "ok");
});

test("without Gözcü, or with an old state, the badge says so", () => {
  assert.deepEqual(threatBadge(null), { text: "NO DATA", level: "" });
  assert.deepEqual(threatBadge(heard(value(2, "AVOID"), { present: false })), {
    text: "GÖZCÜ SILENT",
    level: "crit",
  });
  assert.equal(threatBadge(heard(value(2, "AVOID", { stale: true }))).text, "NO STATUS");
});

function proposal(decision, more = {}) {
  return {
    proposal: value(7),
    manoeuvre: value(1, "CLIMB"),
    decision: value(0, decision),
    reason: value(0, ""),
    time_left: value(4000, "4000", { age: 0.5 }),
    ...more,
  };
}

test("a proposal waiting for the operator counts down", () => {
  assert.deepEqual(proposalLine(proposal("WAITING")), {
    text: "#7 CLIMB: WAITING, 3.5 s left",
    level: "warn",
  });
});

test("a refused proposal says why", () => {
  const refused = proposal("REFUSED", { reason: value(2, "below minimum altitude") });
  assert.equal(proposalLine(refused).text, "#7 CLIMB: REFUSED (below minimum altitude)");
});

test("no proposal, or an old one, shows a dash", () => {
  assert.equal(proposalLine(proposal("WAITING", { proposal: value(0) })).text, "—");
  assert.equal(proposalLine(proposal("WAITING", { proposal: value(7, "7", { stale: true }) })).text, "—");
});

test("a track row shows every value, a dash where one is missing", () => {
  const track = { id: 3, class: "drone", conf: 0.87, az: -4.25, el: 2, rate: 0.4, px: 14, tau: null };
  assert.deepEqual(trackCells(track), ["3", "drone", "87 %", "−4.3/+2.0", "0.4", "14", "—"]);
  const empty = { id: null, class: null, conf: null, az: null, el: null, rate: null, px: null, tau: null };
  assert.deepEqual(trackCells(empty), ["—", "—", "—", "—/—", "—", "—", "—"]);
});
