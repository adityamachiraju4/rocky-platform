import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const config = JSON.parse(
  readFileSync(new URL("../vercel.json", import.meta.url), "utf8"),
) as { rewrites: Array<{ source: string; destination: string }> };

test("Vercel serves the SPA entrypoint for every direct application route", () => {
  assert.deepEqual(config.rewrites, [
    { source: "/(.*)", destination: "/index.html" },
  ]);

  const directRoutes = [
    "/login",
    "/register",
    "/forgot-password",
    "/reset-password",
    "/verify-email",
    "/mission-control",
    "/projects",
    "/tasks",
    "/activity",
    "/reminders",
    "/notes",
    "/lists",
    "/notifications",
    "/settings",
  ];
  const rewrite = new RegExp(`^${config.rewrites[0].source}$`);
  for (const route of directRoutes) assert.match(route, rewrite);
});
