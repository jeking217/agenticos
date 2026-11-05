import { describe, expect, it } from "vitest";

import {
  buildTree,
  changedFiles,
  countsOf,
  dirtyFiles,
  editFile,
  initialWorkbench,
  isDirty,
  languageOf,
  openFile,
  recoveryFor,
  removeFile,
  resetFile,
  type FileChange,
} from "./cms-workspace";

const change = (path: string, status: string): FileChange => ({
  path,
  status,
  size: 10,
  resource_id: 1,
});

describe("buildTree", () => {
  it("nests files under their directories, directories first", () => {
    const tree = buildTree(["/views/index.html", "/static/app.css", "/views/partials/head.html"]);

    expect(tree.map((node) => node.name)).toEqual(["static", "views"]);
    const views = tree.find((node) => node.name === "views")!;
    expect(views.kind).toBe("dir");
    expect(views.children.map((node) => node.name)).toEqual(["partials", "index.html"]);
  });

  it("marks a file with the letter the diff gave it", () => {
    const tree = buildTree(["/views/index.html"], [change("/views/index.html", "M")]);

    expect(tree[0]!.children[0]!.status).toBe("M");
    expect(tree[0]!.status).toBeNull();
  });

  it("does not confuse a directory and a file of the same name", () => {
    const tree = buildTree(["/a/b.html", "/a/b/c.html"]);

    const a = tree[0]!;
    expect(a.children.map((node) => `${node.kind}:${node.name}`)).toEqual(["dir:b", "file:b.html"]);
  });

  it("keeps a leading-slash path and the change that belongs to it in step", () => {
    const tree = buildTree(["/index.html"], [change("/index.html", "A")]);

    expect(tree[0]!.path).toBe("/index.html");
    expect(tree[0]!.status).toBe("A");
  });
});

describe("the diff card's order", () => {
  it("lists additions, then modifications, then deletions", () => {
    const order = changedFiles([
      change("/z.html", "D"),
      change("/y.html", "M"),
      change("/x.html", "A"),
    ]).map((entry) => entry.path);

    expect(order).toEqual(["/x.html", "/y.html", "/z.html"]);
  });

  it("counts each kind", () => {
    expect(countsOf([change("/a", "A"), change("/b", "M"), change("/c", "M")])).toEqual({
      added: 1,
      modified: 2,
      deleted: 0,
    });
  });
});

describe("the editor's state", () => {
  it("opens a file and remembers what was on disk", () => {
    const state = openFile(initialWorkbench, "/views/index.html", "<h1>x</h1>");

    expect(state.openPath).toBe("/views/index.html");
    expect(isDirty(state, "/views/index.html")).toBe(false);
  });

  it("becomes dirty only after an edit", () => {
    const opened = openFile(initialWorkbench, "/views/index.html", "<h1>x</h1>");
    const edited = editFile(opened, "<h1>y</h1>");

    expect(isDirty(edited, "/views/index.html")).toBe(true);
    expect(dirtyFiles(edited)).toEqual(["/views/index.html"]);
  });

  it("keeps an unsaved draft when the person switches files", () => {
    const opened = openFile(initialWorkbench, "/a.html", "a");
    const edited = editFile(opened, "a2");
    const switched = openFile(edited, "/b.html", "b");

    expect(switched.drafts["/a.html"]).toBe("a2");
    expect(isDirty(switched, "/a.html")).toBe(true);
  });

  it("re-opening the same file does not discard its draft", () => {
    const edited = editFile(openFile(initialWorkbench, "/a.html", "a"), "a2");
    const again = openFile(edited, "/a.html", "a2");

    expect(again).toBe(edited);
  });

  it("editing with nothing open is a no-op rather than a crash", () => {
    expect(editFile(initialWorkbench, "text")).toBe(initialWorkbench);
  });

  it("resetting a file drops the draft and stops calling it edited", () => {
    const edited = editFile(openFile(initialWorkbench, "/a.html", "a"), "a2");

    const reset = resetFile(edited, "/a.html", "a");

    expect(reset.drafts["/a.html"]).toBe("a");
    expect(isDirty(reset, "/a.html")).toBe(false);
    expect(dirtyFiles(reset)).toEqual([]);
  });

  it("removing a file takes it out of the tree and closes it", () => {
    const state = { ...openFile(initialWorkbench, "/a.html", "a"), files: ["/a.html", "/b.html"] };

    const gone = removeFile(state, "/a.html");

    expect(gone.files).toEqual(["/b.html"]);
    expect(gone.openPath).toBeNull();
    expect("/a.html" in gone.drafts).toBe(false);
    expect(dirtyFiles(gone)).toEqual([]);
  });
});

describe("languageOf", () => {
  it("names the languages an editor shows", () => {
    expect(languageOf("/views/index.html")).toBe("HTML");
    expect(languageOf("/static/app.css")).toBe("CSS");
    expect(languageOf("/static/app.min.js")).toBe("JavaScript");
    expect(languageOf("/static/logo.png")).toBe("PNG");
    expect(languageOf("/LICENSE")).toBe("Text");
  });
});

describe("recoveryFor", () => {
  it("gives each refusal its own next step", () => {
    expect(recoveryFor("drafts_pending").action).toBe("push");
    expect(recoveryFor("cms_drift").action).toBe("pull");
    expect(recoveryFor("cms_token_expired").action).toBe("login");
    expect(recoveryFor("cms_verify_failed").action).toBe("retry");
    expect(recoveryFor("something_new").action).toBe("verify");
  });
});
