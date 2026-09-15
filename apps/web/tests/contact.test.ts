import assert from "node:assert/strict";
import test from "node:test";

import { CONTACT_EMAILS } from "../src/contact.ts";

test("official Rocky contact addresses use the rockyos.in domain", () => {
  assert.deepEqual(CONTACT_EMAILS, {
    support: "support@rockyos.in",
    hello: "hello@rockyos.in",
    security: "security@rockyos.in",
    privacy: "privacy@rockyos.in",
    billing: "billing@rockyos.in",
    contact: "contact@rockyos.in",
  });
});
