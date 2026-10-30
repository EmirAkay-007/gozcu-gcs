import assert from "node:assert/strict";
import { test } from "node:test";

import { RELOAD, RESTART, buildNotice } from "../../web/js/build.js";

test("a station older than this page, or not restarted after an update, must be restarted", () => {
  assert.equal(buildNotice({ type: "hello" }, null), RESTART); // from before stations said
  assert.equal(buildNotice({ build: "abc", updated: true }, null), RESTART);
  assert.equal(buildNotice({ build: "abc", updated: true }, "abc"), RESTART);
});

test("a station restarted with other files means this page is the old one", () => {
  assert.equal(buildNotice({ build: "abc", updated: false }, null), "");
  assert.equal(buildNotice({ build: "abc", updated: false }, "abc"), ""); // reconnected
  assert.equal(buildNotice({ build: "def", updated: false }, "abc"), RELOAD);
});
