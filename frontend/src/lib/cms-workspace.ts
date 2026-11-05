/**
 * The workbench's state, kept out of its components.
 *
 * A file tree, an editor and a diff card all describe the same thing - which
 * files exist, what changed, and which one is open - and the moment that lives
 * in three `useState` calls the tree stops agreeing with the editor. So it is
 * one reducer here, with tests, and the components read it.
 *
 * The paths are the CMS's own: `/views/index.html`. They are also the keys the
 * diff uses, which is why nothing in this module is allowed to normalise them -
 * a tree that trims a leading slash would silently stop finding the changes
 * that belong to its files.
 */

export interface FileChange {
  path: string;
  status: string;
  size: number;
  resource_id: number | null;
}

export interface TreeNode {
  /** The segment shown in the tree, not the whole path. */
  name: string;
  path: string;
  kind: "dir" | "file";
  children: TreeNode[];
  /** `A`, `M` or `D` when this file changed, else null. */
  status: string | null;
}

export interface WorkbenchState {
  /** Every file the workspace holds, as the CMS names it. */
  files: string[];
  changes: FileChange[];
  /** The file open in the editor, or null before anything is chosen. */
  openPath: string | null;
  /** The editor's text, per file. Kept even after switching files. */
  drafts: Record<string, string>;
  /** What was last read from disk, so "dirty" means "edited here". */
  saved: Record<string, string>;
}

export const initialWorkbench: WorkbenchState = {
  files: [],
  changes: [],
  openPath: null,
  drafts: {},
  saved: {},
};

/**
 * Build the directory tree the left pane draws.
 *
 * Directories sort before files and each group sorts by name, because that is
 * how every file browser a person has used behaves - and because a site's
 * `views/` folder is what they look for first, not `about.html`.
 */
export function buildTree(files: string[], changes: FileChange[] = []): TreeNode[] {
  const statusOf = new Map(changes.map((change) => [change.path, change.status]));
  const root: TreeNode = { name: "", path: "", kind: "dir", children: [], status: null };

  for (const path of [...files].sort()) {
    const segments = path.split("/").filter(Boolean);
    let cursor = root;
    segments.forEach((segment, index) => {
      const isLast = index === segments.length - 1;
      const full = `/${segments.slice(0, index + 1).join("/")}`;
      let next = cursor.children.find((child) => child.name === segment && child.kind === (isLast ? "file" : "dir"));
      if (!next) {
        next = {
          name: segment,
          path: full,
          kind: isLast ? "file" : "dir",
          children: [],
          status: isLast ? statusOf.get(full) ?? null : null,
        };
        cursor.children.push(next);
      }
      cursor = next;
    });
  }

  const sort = (nodes: TreeNode[]): TreeNode[] =>
    nodes
      .map((node) => ({ ...node, children: sort(node.children) }))
      .sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "dir" ? -1 : 1));

  return sort(root.children);
}

/** Files that differ, in the order the diff card lists them. */
export function changedFiles(changes: FileChange[]): FileChange[] {
  const rank: Record<string, number> = { A: 0, M: 1, D: 2 };
  return [...changes].sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || a.path.localeCompare(b.path));
}

export function countsOf(changes: FileChange[]): { added: number; modified: number; deleted: number } {
  return {
    added: changes.filter((change) => change.status === "A").length,
    modified: changes.filter((change) => change.status === "M").length,
    deleted: changes.filter((change) => change.status === "D").length,
  };
}

export function openFile(state: WorkbenchState, path: string, text: string): WorkbenchState {
  if (state.openPath === path) return state;
  const known = state.drafts[path] ?? text;
  return {
    ...state,
    openPath: path,
    drafts: { ...state.drafts, [path]: known },
    saved: { ...state.saved, [path]: text },
  };
}

export function editFile(state: WorkbenchState, text: string): WorkbenchState {
  if (!state.openPath) return state;
  return { ...state, drafts: { ...state.drafts, [state.openPath]: text } };
}

/**
 * Put a file back to what is on disk, dropping whatever draft it had.
 *
 * What a discard needs: the file on disk changed under the editor, so keeping
 * the in-memory copy would leave the pane showing text that no longer exists
 * anywhere - and `isDirty` would say "edited" about an edit the person just
 * threw away.
 */
export function resetFile(state: WorkbenchState, path: string, text: string): WorkbenchState {
  return {
    ...state,
    drafts: { ...state.drafts, [path]: text },
    saved: { ...state.saved, [path]: text },
  };
}

/**
 * Forget a file entirely: it was added locally and the person discarded it, so
 * the workspace no longer holds it and the tree must stop offering it.
 */
export function removeFile(state: WorkbenchState, path: string): WorkbenchState {
  const drafts = { ...state.drafts };
  const saved = { ...state.saved };
  delete drafts[path];
  delete saved[path];
  return {
    ...state,
    files: state.files.filter((file) => file !== path),
    drafts,
    saved,
    openPath: state.openPath === path ? null : state.openPath,
  };
}

export function isDirty(state: WorkbenchState, path: string): boolean {
  if (!(path in state.drafts) || !(path in state.saved)) return false;
  return state.drafts[path] !== state.saved[path];
}

export function dirtyFiles(state: WorkbenchState): string[] {
  return Object.keys(state.drafts).filter((path) => isDirty(state, path));
}

/** A short label for the editor's status bar. */
export function languageOf(path: string): string {
  // The last segment only: a path with no dot in it is a file with no
  // extension, and `split(".").pop()` would hand back the whole path.
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  const known: Record<string, string> = {
    html: "HTML",
    htm: "HTML",
    css: "CSS",
    js: "JavaScript",
    mjs: "JavaScript",
    json: "JSON",
    svg: "SVG",
    xml: "XML",
    txt: "Text",
  };
  return known[extension] ?? (extension ? extension.toUpperCase() : "Text");
}

/**
 * What the console offers for a refusal code.
 *
 * The API returns codes rather than sentences because each one has a *different
 * next step*, and this table is that difference: re-verify, save the draft
 * first, pull again, sign in. A generic "something went wrong" makes the person
 * guess at all four.
 */
export function recoveryFor(code: string): { label: string; action: "verify" | "push" | "pull" | "login" | "retry" } {
  switch (code) {
    case "cms_verify_failed":
      return { label: "回到编辑器修模板", action: "retry" };
    case "drafts_pending":
      return { label: "先保存草稿", action: "push" };
    case "nothing_to_publish":
      return { label: "重新加载变更", action: "pull" };
    case "cms_drift":
      return { label: "重新拉取站点", action: "pull" };
    case "cms_token_expired":
      return { label: "重新登录平台", action: "login" };
    default:
      return { label: "重新校验", action: "verify" };
  }
}
