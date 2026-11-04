import { t } from "./i18n.js";

/** @typedef {(command: string, args?: Record<string, string>) => Promise<unknown>} Invoke */

/**
 * What the status card says for each answer `local_status` gives. The words are
 * keys rather than sentences so the card speaks the page's language; `i18n.js`
 * holds the Chinese and the English lives here.
 */
const VIEWS = {
  no_docker: {
    title: "view.no_docker.title",
    message: "view.no_docker.message",
    actions: ["docker", "open-docker", "check"],
  },
  docker_stopped: {
    title: "view.docker_stopped.title",
    message: "view.docker_stopped.message",
    actions: ["open-docker", "check"],
  },
  running: {
    title: "view.running.title",
    message: "view.running.message",
    actions: ["use", "create"],
  },
  ready: {
    title: "view.ready.title",
    message: "view.ready.message",
    actions: ["create"],
  },
};

/**
 * Local setup keeps its status card mounted while probing and connecting.
 * @param {HTMLElement} root
 * @param {Invoke} invoke
 * @param {(message: unknown) => void} report
 * @returns {() => Promise<void>}
 */
export function localComputerStep(root, invoke, report) {
  const card = root.querySelector("[data-local-instance]");
  const title = root.querySelector("[data-local-title]");
  const message = root.querySelector("[data-local-message]");
  const actions = root.querySelector("[data-local-actions]");
  const form = root.querySelector("form");
  const list = root.querySelector("[data-instance-list]");
  const instanceError = root.querySelector("[data-instance-error]");
  const overview = root.querySelector("[data-local-overview]");
  const create = root.querySelector("[data-local-create]");
  const heading = root.querySelector("[data-local-heading]");
  const lead = root.querySelector("[data-local-lead]");
  let instances = [];
  function showOverview() {
    root.dataset.view = "overview";
    overview.hidden = false;
    create.hidden = true;
    form.hidden = true;
    heading.textContent = t("local.heading");
    lead.textContent = t("local.lead");
  }
  form
    .querySelector('[data-action="cancel-create"]')
    .addEventListener("click", () => {
      showOverview();
      actions
        .querySelector('[data-action="create"]')
        ?.focus({ preventScroll: true });
    });
  function showForm(instance) {
    root.dataset.view = "create";
    overview.hidden = true;
    create.hidden = false;
    form.hidden = false;
    heading.textContent = instance
      ? t("js.local.finishHeading")
      : t("js.local.createHeading");
    lead.textContent = t("js.local.createLead");
    form.querySelector("[name=instance-id]").value = instance?.id ?? "";
    form.querySelector("[name=instance-name]").value = instance?.name ?? "";
    form.querySelector("[name=instance-name]").focus();
  }
  async function refreshInstances(request) {
    try {
      const answer = await invoke("local_instances");
      if (request !== generation || root.hidden) return;
      if (
        !Array.isArray(answer) ||
        answer.some(
          (row) =>
            !row ||
            typeof row.id !== "string" ||
            typeof row.name !== "string" ||
            typeof row.url !== "string" ||
            typeof row.installed !== "boolean" ||
            typeof row.running !== "boolean",
        )
      )
        throw new Error(t("js.unreadableInstances"));
      instances = answer;
      list.replaceChildren();
      for (const instance of instances) {
        const row = document.createElement("div");
        row.className = "instance-row";
        const description = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = instance.name;
        const hint = document.createElement("p");
        const state = !instance.installed
          ? t("js.instance.unfinished")
          : instance.running
            ? t("js.instance.running")
            : t("js.instance.stopped");
        hint.textContent = `${instance.url} · ${state}`;
        description.append(name, hint);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "secondary";
        button.textContent = !instance.installed
          ? t("js.instance.finish")
          : instance.running
            ? t("js.connect")
            : t("js.instance.startConnect");
        button.dataset.instance = instance.id;
        row.append(description, button);
        list.append(row);
      }
      list.hidden = instances.length === 0;
      instanceError.hidden = true;
    } catch (error) {
      if (request !== generation || root.hidden) return;
      instanceError.hidden = false;
      instanceError.textContent = String(error);
    }
  }
  list.addEventListener("click", async (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("[data-instance]");
    const instance = instances.find(
      (item) => item.id === button?.dataset.instance,
    );
    if (!instance || connecting) return;
    if (!instance.installed) {
      showForm(instance);
      return;
    }
    connecting = true;
    root.inert = true;
    button.disabled = true;
    button.textContent = t("js.connecting");
    try {
      await invoke("use_local", { instanceId: instance.id });
    } catch (error) {
      instanceError.hidden = false;
      instanceError.textContent = String(error);
      connecting = false;
      root.inert = false;
      button.disabled = false;
      button.textContent = t("js.instance.retry");
    }
  });
  let generation = 0;
  let connecting = false;
  function setBusy(busy) {
    card.setAttribute("aria-busy", String(busy));
  }
  function showActions(visible) {
    actions.hidden = false;
    const primary = ["use", "create", "docker", "open-docker", "check"].find(
      (action) => visible.includes(action),
    );
    for (const button of actions.querySelectorAll("[data-action]")) {
      button.hidden = !visible.includes(button.dataset.action);
      button.classList.toggle("primary", button.dataset.action === primary);
      button.classList.toggle("secondary", button.dataset.action !== primary);
    }
  }
  async function refresh() {
    const request = ++generation;
    showOverview();
    card.dataset.state = "checking";
    setBusy(true);
    title.textContent = t("local.checking.title");
    message.textContent = t("local.checking.message");
    showActions([]);
    form.hidden = true;
    try {
      const status = await invoke("local_status");
      if (request !== generation || root.hidden) return;
      if (typeof status !== "string" || !Object.hasOwn(VIEWS, status))
        throw new Error(t("js.unreadableStatus"));
      void refreshInstances(request);
      const view = VIEWS[status];
      card.dataset.state = status;
      title.textContent = t(view.title);
      message.textContent = t(view.message);
      showActions(view.actions);
    } catch (error) {
      if (request !== generation || root.hidden) return;
      card.dataset.state = "error";
      title.textContent = t("js.check.failedTitle");
      message.textContent = String(error);
      showActions(["check"]);
    } finally {
      if (request === generation) setBusy(false);
    }
  }
  actions.addEventListener("click", async (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("[data-action]");
    const action = button?.dataset.action;
    if (action === "check") await refresh();
    if (action === "create") showForm(null);
    if (action === "docker") {
      button.disabled = true;
      message.textContent = t("js.docker.downloading");
      try {
        await invoke("download_docker");
        message.textContent = t("js.docker.finishInstall");
      } catch (error) {
        message.textContent = String(error);
        report(error);
      } finally {
        button.disabled = false;
      }
    }
    if (action === "open-docker") {
      try {
        await invoke("open_docker");
        message.textContent = t("js.docker.finishSetup");
      } catch (error) {
        message.textContent = String(error);
      }
    }
    if (action !== "use" || connecting) return;
    connecting = true;
    root.inert = true;
    button.disabled = true;
    root.querySelector("[data-go]").disabled = true;
    button.dataset.busy = "true";
    button.textContent = t("js.connecting");
    setBusy(true);
    try {
      await invoke("use_local");
    } catch (error) {
      message.textContent = String(error);
      connecting = false;
      root.inert = false;
      button.disabled = false;
      root.querySelector("[data-go]").disabled = false;
      delete button.dataset.busy;
      button.textContent = t("local.btn.use");
      setBusy(false);
    }
  });
  return refresh;
}
