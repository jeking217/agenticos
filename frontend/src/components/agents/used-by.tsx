"use client";

import { useState } from "react";
import { Bot, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { AgentAvatar } from "./agent-avatar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { AgentUsage } from "@/types/agents";

const NAMED = 2;

/**
 * The control that takes a resource back off an agent (#2075).
 *
 * One agent is one click. Several would be a guess about which one the reader
 * meant, so the button opens the list and each name is its own button: a
 * removal names its agent in the toast, and there is no undo behind it.
 */
function RemoveControl({
  agents,
  onRemove,
  removing,
}: {
  agents: AgentUsage[];
  onRemove: (agent: AgentUsage) => void;
  removing?: string | null;
}) {
  const t = useTranslations("agents");
  const [open, setOpen] = useState(false);
  const busy = Boolean(removing);

  const control = (label: string, onClick?: () => void) => (
    <button
      type="button"
      disabled={busy}
      onClick={onClick}
      aria-label={label}
      title={t("removeFromAgentHint")}
      data-testid="used-by-remove"
      className="text-muted-foreground hover:text-foreground hover:bg-accent pointer-events-auto relative z-10 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded disabled:opacity-50"
    >
      <X className="h-3 w-3" aria-hidden />
    </button>
  );

  const only = agents.length === 1 ? agents[0] : undefined;
  if (only !== undefined) {
    return control(t("removeFromAgentNamed", { agent: only.name }), () => onRemove(only));
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{control(t("removeFromAgentPick"))}</PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-1">
        <p className="text-muted-foreground px-2 py-1.5 text-xs">{t("removeFromAgentPickHint")}</p>
        {agents.map((agent) => (
          <button
            key={agent.id}
            type="button"
            disabled={busy}
            aria-label={t("removeFromAgentNamed", { agent: agent.name })}
            onClick={() => {
              setOpen(false);
              onRemove(agent);
            }}
            className="hover:bg-accent flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm focus-visible:outline-offset-[-2px] disabled:opacity-50"
          >
            <AgentAvatar
              agentId={agent.id}
              slug={agent.slug ?? agent.id}
              hasAvatar={agent.has_avatar}
              colorSlot={agent.avatar_color}
              size="sm"
            />
            <span className="min-w-0 flex-1 truncate">{agent.name}</span>
            <X className="text-muted-foreground h-3.5 w-3.5 shrink-0" aria-hidden />
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
}

/**
 * Which agents use a skill, a context file or a knowledge base (#2075).
 *
 * "Not used by any agent yet" is the line worth reading: something written and
 * never given to an agent does nothing, and the card is where its author looks.
 * Nothing for a response that did not say, rather than a claim of "nowhere".
 *
 * Given `onRemove`, the line is also where a reader takes it back. Adding a base
 * from its card was one click, so removing it had to be reachable from the same
 * line - the Builder's Toolbox tab was the only way, and nobody found it.
 */
export function UsedBy({
  agents,
  className,
  onRemove,
  removing,
}: {
  agents?: AgentUsage[];
  className?: string;
  /** Passed only where the reader may edit agents; absent hides the control. */
  onRemove?: (agent: AgentUsage) => void;
  /** The agent whose removal is in flight. */
  removing?: string | null;
}) {
  const t = useTranslations("agents");
  if (agents === undefined) return null;
  const names = agents
    .slice(0, NAMED)
    .map((agent) => agent.name)
    .join(", ");
  return (
    <span
      className={cn("text-muted-foreground flex min-w-0 items-center gap-1 text-xs", className)}
      title={agents.map((agent) => agent.name).join(", ") || undefined}
    >
      {agents.length === 0 ? (
        <Bot className="h-3.5 w-3.5 shrink-0" aria-hidden />
      ) : (
        <span className="flex shrink-0 -space-x-1.5" aria-hidden>
          {agents.slice(0, NAMED).map((agent) => (
            <AgentAvatar
              key={agent.id}
              agentId={agent.id}
              slug={agent.slug ?? agent.id}
              hasAvatar={agent.has_avatar}
              colorSlot={agent.avatar_color}
              size="sm"
              unframed
            />
          ))}
        </span>
      )}
      <span className="truncate">
        {agents.length === 0
          ? t("usedByNobody")
          : t("usedBy", { names, more: Math.max(0, agents.length - NAMED) })}
      </span>
      {onRemove !== undefined && agents.length > 0 && (
        <RemoveControl agents={agents} onRemove={onRemove} removing={removing} />
      )}
    </span>
  );
}
