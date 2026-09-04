import assert from "node:assert/strict";
import test from "node:test";

import { ApiError } from "../src/apiErrors.ts";
import { expectArrayResponse } from "../src/apiResponse.ts";

test("project list responses accept the backend raw array shape", () => {
  const projects = [
    {
      id: "project-1",
      user_id: "user-1",
      name: "Rocky",
      description: null,
      status: "active",
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z",
    },
  ];
  assert.deepEqual(expectArrayResponse(projects, "/projects"), projects);
});

test("project list responses reject an enveloped projects shape", () => {
  assert.throws(
    () => expectArrayResponse({ projects: [] }, "/projects"),
    (error: unknown) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.message, "Malformed response from /projects: expected array, received object");
      assert.deepEqual(error.detail, { projects: [] });
      return true;
    },
  );
});
