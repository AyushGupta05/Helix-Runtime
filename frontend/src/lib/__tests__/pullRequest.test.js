import { describe, expect, it } from "vitest";

import { missionPullRequestUrl, normalizePullRequest } from "../pullRequest";

describe("normalizePullRequest", () => {
  it("returns a direct pull request url when the payload is already normalized", () => {
    expect(
      normalizePullRequest({
        html_url: "https://github.com/example/repo/pull/42"
      })
    ).toMatchObject({
      html_url: "https://github.com/example/repo/pull/42"
    });
  });

  it("extracts a pull request url from governed action result text", () => {
    expect(
      normalizePullRequest({
        result: [
          {
            type: "text",
            text: "{\"url\":\"https://github.com/example/repo/pull/77\"}"
          }
        ]
      })
    ).toMatchObject({
      url: "https://github.com/example/repo/pull/77"
    });
  });
});

describe("missionPullRequestUrl", () => {
  it("finds the github publish pull request url from live skill outputs", () => {
    expect(
      missionPullRequestUrl({
        skill_outputs: {
          github_publish: {
            pull_request: {
              result: [
                {
                  type: "text",
                  text: "{\"url\":\"https://github.com/example/repo/pull/99\"}"
                }
              ]
            }
          }
        }
      })
    ).toBe("https://github.com/example/repo/pull/99");
  });
});
