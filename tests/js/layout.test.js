import assert from "node:assert/strict";
import { test } from "node:test";

import { restoreLayout } from "../../web/js/layout.js";

const DEFAULT = { swapped: false, enlarged: "", logFolded: false };

test("a remembered layout comes back", () => {
  const saved = { swapped: true, enlarged: "map", logFolded: true };
  assert.deepEqual(restoreLayout(saved), saved);
});

test("only what this version understands is taken from a remembered layout", () => {
  assert.deepEqual(restoreLayout({ swapped: "yes", enlarged: "radar", extra: 1 }), DEFAULT);
  for (const junk of [null, undefined, 5, "text", []]) {
    assert.deepEqual(restoreLayout(junk), DEFAULT);
  }
});
