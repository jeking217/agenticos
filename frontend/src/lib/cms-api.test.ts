import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api-client", () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import { apiClient } from "./api-client";
import {
  bindSite,
  discardSiteFiles,
  getCmsStatus,
  getDiff,
  getReleasePlan,
  getSiteFile,
  getSuggestions,
  listSites,
  pullSite,
  pushSite,
  releaseSite,
  rollbackSite,
  verifySite,
} from "./cms-api";

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe("the site endpoints", () => {
  it("asks whether the module may be opened", async () => {
    get.mockResolvedValue({ configured: true, platform_linked: false, can_use: false, reason: "请用 Unione 平台账号登录" });

    const status = await getCmsStatus();

    expect(get).toHaveBeenCalledWith("/cms/status");
    expect(status.can_use).toBe(false);
  });

  it("unwraps the site list rather than handing the page an envelope", async () => {
    get.mockResolvedValue({ sites: [{ id: 7 }] });

    await expect(listSites()).resolves.toEqual([{ id: 7 }]);
    expect(get).toHaveBeenCalledWith("/cms/sites");
  });

  it("binds a site with the id in both the path and the body", async () => {
    get.mockResolvedValue({ configured: true, platform_linked: true, can_use: true, reason: null });

    await bindSite({
      id: 7,
      title: "Main",
      ctx: "/cms/main",
      domain: null,
      cover_url: null,
      space_id: 11,
      space_vers: 3,
      editable: true,
      reason: null,
    });

    expect(post).toHaveBeenCalledWith("/cms/sites/7/bind", expect.objectContaining({ site_id: 7, space_id: 11 }));
  });
});

describe("the flow's endpoints", () => {
  it("names each step on its own route", async () => {
    post.mockResolvedValue({ ok: true });
    get.mockResolvedValue({ changes: [], skipped: [], counts: {} });

    await pullSite(7);
    await getDiff(7);
    await verifySite(7);
    await pushSite(7);

    expect(post.mock.calls.map(([path]) => path)).toEqual([
      "/cms/sites/7/pull",
      "/cms/sites/7/verify",
      "/cms/sites/7/push",
    ]);
    expect(get).toHaveBeenCalledWith("/cms/sites/7/diff");
  });

  it("discards the files it was given, on one route", async () => {
    post.mockResolvedValue({ ok: true, discarded: [], recalled: [], failed: [] });

    await discardSiteFiles(7, ["/views/index.html", "/static/app.css"]);

    expect(post).toHaveBeenCalledWith("/cms/sites/7/discard", {
      paths: ["/views/index.html", "/static/app.css"],
    });
  });

  it("encodes the path it asks a file by", async () => {
    get.mockResolvedValue({ path: "/views/index.html", text: "<h1>v1</h1>" });

    await expect(getSiteFile(7, "/views/index.html")).resolves.toEqual({
      path: "/views/index.html",
      text: "<h1>v1</h1>",
    });
    expect(get).toHaveBeenCalledWith("/cms/sites/7/file?path=%2Fviews%2Findex.html");
  });

  it("asks for the confirmation sheet before publishing", async () => {
    get.mockResolvedValue({ ok: false, siteId: 7, files: [], blockers: [], warnings: [], foreignDrafts: [], verifyFailures: [] });

    await getReleasePlan(7);

    expect(get).toHaveBeenCalledWith("/cms/sites/7/release/plan");
  });

  it("sends the person's confirmation, which is not something this module decides", async () => {
    post.mockResolvedValue({ ok: true, released: 2, files: [], tag: "cms-release-4", warnings: [], blockers: [] });

    await releaseSite(7, ["/views/about.html"]);

    expect(post).toHaveBeenCalledWith("/cms/sites/7/release", {
      confirmed: true,
      foreign_drafts: ["/views/about.html"],
    });
  });

  it("rolls back to the previous release when no tag is named", async () => {
    post.mockResolvedValue({ ok: true, restored: [], tag: null, released: 0 });

    await rollbackSite(7);

    expect(post).toHaveBeenCalledWith("/cms/sites/7/rollback", { to: null });
  });

  it("keeps the suggestion chips as the API shaped them", async () => {
    const chips = [{ action: "cms.publish", label: "确认并发布", detail: "", payload: {} }];
    get.mockResolvedValue(chips);

    await expect(getSuggestions(7)).resolves.toEqual(chips);
  });
});
