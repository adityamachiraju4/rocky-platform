import assert from "node:assert/strict";
import test from "node:test";

import { CONTACT_EMAILS, routes } from "../src/content.ts";

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

test("sensitive public pages expose the correct contact routes", () => {
  const contactsFor = (path: string) => routes
    .find((route) => route.path === path)
    ?.sections.flatMap((section) => section.contacts?.map(({ email }) => email) ?? []);

  assert.deepEqual(contactsFor("/privacy"), [CONTACT_EMAILS.privacy]);
  assert.deepEqual(contactsFor("/security"), [CONTACT_EMAILS.security]);
  assert.deepEqual(contactsFor("/account-deletion"), [
    CONTACT_EMAILS.privacy,
    CONTACT_EMAILS.support,
  ]);
  assert.deepEqual(contactsFor("/support"), [
    CONTACT_EMAILS.support,
    CONTACT_EMAILS.support,
  ]);
});
