import assert from "node:assert/strict";
import { test } from "node:test";

import { formatAge } from "../../web/js/fields.js";

test("an age reads in the largest whole unit", () => {
  assert.equal(formatAge(2.5), "2 s");
  assert.equal(formatAge(59.6), "59 s");
  assert.equal(formatAge(60), "1 min");
  assert.equal(formatAge(3599), "59 min");
  assert.equal(formatAge(7300), "2 h");
});
