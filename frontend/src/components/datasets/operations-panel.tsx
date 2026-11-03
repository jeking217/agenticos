"use client";

import type { Dataset } from "@/types/datasets";

import { OperationsTable } from "./operations-table";

interface OperationsPanelProps {
  dataset: Dataset;
}

/**
 * The operation log as a section of the dataset page.
 *
 * The history itself lives in `OperationsTable`, which the drawer shares: a
 * person reading the log in the page and a person opening it over a row are
 * asking the same question.
 */
export function OperationsPanel({ dataset }: OperationsPanelProps) {
  return <OperationsTable dataset={dataset} />;
}
