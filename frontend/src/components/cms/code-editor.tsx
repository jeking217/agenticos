"use client";

import { useEffect, useRef } from "react";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { bracketMatching, indentOnInput } from "@codemirror/language";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";

/**
 * The editor pane.
 *
 * CodeMirror rather than a `textarea` for three reasons that all show up in the
 * first minute of use: HTML that is one long line is unreadable without syntax
 * colour, a template is edited by indenting a brace and not by retyping a
 * block, and Cmd+F has to find the file's own text rather than the browser's
 * whole page.
 *
 * The view is created once and then *told* about changes. Re-creating it per
 * keystroke would lose the cursor, the scroll position and the undo history -
 * which is the usual way an editor built into a form stops behaving like an
 * editor. Value, language and read-only each get their own effect, so typing
 * does not reconfigure the document and opening another file does not reset the
 * keymap.
 */

interface CodeEditorProps {
  /** The resource path, which decides the language. Null before one is open. */
  path: string | null;
  value: string;
  onChange: (text: string) => void;
  readOnly?: boolean;
  /** Accessible name; the pane's header already shows the path. */
  label: string;
}

function languageFor(path: string | null): Extension {
  if (!path) return [];
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  switch (extension) {
    case "html":
    case "htm":
    case "svg":
    case "xml":
      return html();
    case "css":
      return css();
    case "js":
    case "mjs":
      return javascript();
    case "json":
      return json();
    default:
      // A file whose type nobody parsed still has to be editable and legible;
      // plain text is the honest answer, not a guess.
      return [];
  }
}

const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "13px", backgroundColor: "transparent" },
  ".cm-scroller": {
    fontFamily:
      "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
    lineHeight: "1.6",
  },
  ".cm-content": { padding: "12px 0" },
  ".cm-gutters": {
    backgroundColor: "transparent",
    border: "none",
    color: "var(--muted-foreground)",
  },
  "&.cm-focused": { outline: "none" },
});

export function CodeEditor({ path, value, onChange, readOnly = false, label }: CodeEditorProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const view = useRef<EditorView | null>(null);
  // The listener below is installed once, so it must call the *current*
  // callback rather than the one captured on the first render.
  const listener = useRef(onChange);
  const language = useRef(new Compartment());
  const editable = useRef(new Compartment());

  useEffect(() => {
    listener.current = onChange;
  }, [onChange]);

  useEffect(() => {
    const parent = host.current;
    if (!parent) return;
    const instance = new EditorView({
      parent,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          history(),
          indentOnInput(),
          bracketMatching(),
          highlightActiveLine(),
          highlightSelectionMatches(),
          keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
          language.current.of(languageFor(path)),
          editable.current.of(EditorState.readOnly.of(readOnly)),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) listener.current(update.state.doc.toString());
          }),
          EditorView.contentAttributes.of({ "aria-label": label }),
          theme,
        ],
      }),
    });
    view.current = instance;
    return () => {
      instance.destroy();
      view.current = null;
    };
    // Mount only: everything below updates the view in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const instance = view.current;
    if (!instance) return;
    // Only when the document really differs - otherwise the echo of the
    // person's own keystroke would reset the cursor to the top of the file.
    if (instance.state.doc.toString() === value) return;
    instance.dispatch({
      changes: { from: 0, to: instance.state.doc.length, insert: value },
    });
  }, [value]);

  useEffect(() => {
    view.current?.dispatch({ effects: language.current.reconfigure(languageFor(path)) });
  }, [path]);

  useEffect(() => {
    view.current?.dispatch({
      effects: editable.current.reconfigure(EditorState.readOnly.of(readOnly)),
    });
  }, [readOnly]);

  return <div ref={host} className="min-h-0 flex-1 overflow-hidden" />;
}
