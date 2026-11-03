"use client";

import { useMemo, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, FileSpreadsheet, Upload } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Progress,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { DIALOG_BODY, DIALOG_BROAD, DIALOG_COLUMN } from "@/lib/dialog-sizes";
import { useCommitImport, useDatasets, useUploadWorkbook } from "@/hooks/use-datasets";
import { saveImportDraft } from "@/lib/datasets-api";
import { cn } from "@/lib/utils";
import {
  allReadyToPublish,
  checkedSheets,
  commitPayload,
  draftStepFor,
  FIELD_TYPES,
  initialChoices,
  keptColumns,
  mergeColumn,
  mergeSheet,
  WIZARD_STEPS,
  type ColumnChoice,
  type SheetChoice,
  type SheetChoices,
  type WizardStep,
} from "./import-wizard";
import type {
  Dataset,
  DatasetFieldType,
  ImportProgress,
  ImportSheetResult,
  SheetProposal,
} from "@/types/datasets";

const ACCEPTED = ".xlsx,.xlsm,.xls";

interface ImportWorkbookDialogProps {
  kbId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Offered as import targets, so a second file can extend a dataset. */
  datasets: Dataset[];
}

/**
 * Upload → sheets → columns → check → publish → report.
 *
 * The parser's proposal is a suggestion and is shown as one: every sheet can be
 * unchecked, renamed, pointed at an existing dataset or given a different
 * primary key, and every column can be renamed, retyped or left out. Nothing is
 * written until the publish step, which is the whole reason this is two
 * requests rather than one - and the publish itself runs on a worker, so the
 * fifth step is a progress bar rather than a spinner.
 *
 * The choices are saved against the import as they are made, so closing the
 * dialog at step four costs the decisions, not the upload. A save that fails is
 * not an error the person can act on and does not block the wizard: the draft is
 * a convenience, and the publish is the transaction.
 */
export function ImportWorkbookDialog({
  kbId,
  open,
  onOpenChange,
  datasets,
}: ImportWorkbookDialogProps) {
  const t = useTranslations("datasets");
  const inputRef = useRef<HTMLInputElement>(null);
  const upload = useUploadWorkbook(kbId);
  const commit = useCommitImport(kbId);
  const { datasets: known } = useDatasets(kbId);
  const targets = datasets.length > 0 ? datasets : known;

  const [step, setStep] = useState<WizardStep>("upload");
  const [dragging, setDragging] = useState(false);
  const [filename, setFilename] = useState<string | null>(null);
  const [importId, setImportId] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState(0);
  const [proposals, setProposals] = useState<SheetProposal[]>([]);
  const [choices, setChoices] = useState<SheetChoices>({});
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [results, setResults] = useState<ImportSheetResult[] | null>(null);

  const reset = () => {
    setStep("upload");
    setFilename(null);
    setImportId(null);
    setUploaded(0);
    setProposals([]);
    setChoices({});
    setProgress(null);
    setResults(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const onFile = async (file: File) => {
    setFilename(file.name);
    setResults(null);
    setUploaded(0);
    const record = await upload.mutateAsync({
      file,
      onProgress: (fraction) => setUploaded(fraction),
    });
    setImportId(record.id);
    setProposals(record.sheets);
    setChoices(initialChoices(record.sheets));
    setStep("sheets");
  };

  const selected = useMemo(() => checkedSheets(proposals, choices), [proposals, choices]);

  const canLeaveSheets = selected.length > 0;
  const canLeaveColumns = allReadyToPublish(proposals, choices);

  const patch = (sheetName: string, next: Partial<Omit<SheetChoice, "columns">>) =>
    setChoices((current) => mergeSheet(current, sheetName, next));

  const patchColumn = (sheetName: string, index: number, next: Partial<ColumnChoice>) =>
    setChoices((current) => mergeColumn(current, sheetName, index, next));

  const payload = () => commitPayload(proposals, choices);

  /**
   * Keep the choices made so far against the import.
   *
   * A save that fails is not an error the person can act on, so it does not
   * interrupt the wizard: a draft is a convenience, and the publish is the
   * transaction.
   */
  const saveDraft = () => {
    const draftStep = draftStepFor(step);
    if (!importId || !draftStep) return;
    void saveImportDraft(kbId, importId, { step: draftStep, sheets: payload() }).catch(
      () => undefined,
    );
  };

  const go = (next: WizardStep) => {
    saveDraft();
    setStep(next);
  };

  const publish = async () => {
    if (!importId) return;
    setStep("publish");
    setProgress({
      phase: "queued",
      total: selected.length,
      done: 0,
      failed: 0,
      current_sheet: null,
      percent: 0,
    });
    const result = await commit.mutateAsync({
      importId,
      sheets: payload(),
      onProgress: setProgress,
    });
    setResults(result.sheets);
    setStep("report");
  };

  const close = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const index = WIZARD_STEPS.indexOf(step);
  // The one number the bar and its label both show, so they cannot disagree.
  const uploadedPercent = Math.round(uploaded * 100);

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className={cn(DIALOG_COLUMN, DIALOG_BROAD)}>
        <DialogHeader>
          <DialogTitle>{t("importTitle")}</DialogTitle>
          <DialogDescription>{t("importDescription")}</DialogDescription>
        </DialogHeader>

        <ol className="flex flex-wrap items-center gap-x-2 gap-y-1" aria-label={t("importSteps")}>
          {WIZARD_STEPS.map((each, at) => (
            <li key={each} className="flex items-center gap-1 text-xs">
              <span
                className={cn(
                  "flex size-5 items-center justify-center rounded-full border text-[11px]",
                  at < index && "bg-brand text-brand-foreground border-transparent",
                  at === index && "border-brand-line text-brand",
                  at > index && "border-border text-muted-foreground",
                )}
                aria-hidden
              >
                {at < index ? <Check className="size-3" /> : at + 1}
              </span>
              <span className={cn(at === index ? "font-medium" : "text-muted-foreground")}>
                {t(`importStep.${each}`)}
              </span>
              {at < WIZARD_STEPS.length - 1 && (
                <ChevronRight className="text-muted-foreground size-3" aria-hidden />
              )}
            </li>
          ))}
        </ol>

        <div className={cn(DIALOG_BODY, "space-y-4 py-1")}>
          {step === "upload" && (
            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                const file = event.dataTransfer.files?.[0];
                if (file) void onFile(file);
              }}
              // A drop target is not a widget: dragging is a pointer-only
              // shortcut, and the button below is the keyboard path to the same
              // file picker. `presentation` says the rectangle adds no
              // semantics of its own - it decorates a control that is already
              // there, and the text inside it stays in the tree.
              role="presentation"
              className={cn(
                "rounded-card border-border flex flex-col items-center gap-3 border border-dashed p-8 text-center transition-colors",
                dragging && "border-brand-line bg-brand-subtle",
              )}
              data-testid="workbook-dropzone"
            >
              <FileSpreadsheet className="text-muted-foreground size-6" aria-hidden />
              <div className="space-y-1">
                <p className="text-sm font-medium">{t("dropWorkbook")}</p>
                <p className="text-muted-foreground text-xs">{t("acceptHint")}</p>
              </div>
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED}
                className="sr-only"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void onFile(file);
                }}
              />
              <Button
                variant="outline"
                onClick={() => inputRef.current?.click()}
                loading={upload.isPending}
              >
                <Upload className="size-4" aria-hidden />
                {t("chooseFile")}
              </Button>
              {upload.isPending && (
                <div className="w-full max-w-xs space-y-1">
                  {/* Both the bar's name and the line under it say the number:
                      the message carries `{percent}`, so a call without one is a
                      formatting error rather than a shorter label. */}
                  <Progress
                    value={uploadedPercent}
                    aria-label={t("uploading", { percent: uploadedPercent })}
                  />
                  <p className="text-muted-foreground text-xs">
                    {t("uploading", { percent: uploadedPercent })}
                  </p>
                </div>
              )}
              {filename && !upload.isPending && (
                <p className="text-muted-foreground truncate text-xs">{filename}</p>
              )}
            </div>
          )}

          {step === "sheets" && (
            <div className="space-y-2">
              <p className="text-sm font-medium">{t("sheetsFound", { count: proposals.length })}</p>
              <p className="text-muted-foreground text-xs">{t("sheetsHint")}</p>
              <div className="space-y-2">
                {proposals.map((sheet) => {
                  const choice = choices[sheet.sheet_name];
                  if (!choice) return null;
                  const disabled = sheet.kind !== "data";
                  return (
                    <div
                      key={sheet.sheet_name}
                      className={cn(
                        "rounded-card border-border flex flex-wrap items-center gap-3 border p-3",
                        disabled && "opacity-60",
                      )}
                    >
                      <Checkbox
                        checked={choice.checked}
                        disabled={disabled}
                        onCheckedChange={(checked) =>
                          patch(sheet.sheet_name, { checked: checked === true })
                        }
                        aria-label={sheet.sheet_name}
                      />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">
                        {sheet.sheet_name}
                      </span>
                      <Badge variant={disabled ? "outline" : "secondary"}>
                        {t(`sheetKind.${sheet.kind}`)}
                      </Badge>
                      <span className="text-muted-foreground text-xs">
                        {t("sheetShape", { rows: sheet.row_count, cols: sheet.column_count })}
                      </span>
                      {sheet.warnings.length > 0 && (
                        <Badge variant="outline">
                          {t("sheetWarnings", { count: sheet.warnings.length })}
                        </Badge>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {step === "columns" && (
            <div className="space-y-4">
              {selected.map((sheet) => {
                const choice = choices[sheet.sheet_name];
                if (!choice) return null;
                return (
                  <div key={sheet.sheet_name} className="space-y-2">
                    <div className="flex flex-wrap items-end gap-2">
                      <div className="min-w-0 flex-1 space-y-1">
                        <p className="text-sm font-medium">{sheet.sheet_name}</p>
                        <Input
                          value={choice.name}
                          onChange={(event) =>
                            patch(sheet.sheet_name, { name: event.target.value })
                          }
                          aria-label={t("datasetName")}
                          placeholder={t("datasetName")}
                        />
                      </div>
                      <Select
                        value={choice.target || "new"}
                        onValueChange={(value) =>
                          patch(sheet.sheet_name, { target: value === "new" ? "" : value })
                        }
                      >
                        <SelectTrigger className="w-56" aria-label={t("targetDataset")}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="new">{t("targetNew")}</SelectItem>
                          {targets.map((dataset) => (
                            <SelectItem key={dataset.id} value={dataset.id}>
                              {t("targetExisting", { name: dataset.name })}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    {choice.target ? (
                      <p className="text-muted-foreground text-xs">{t("existingColumnsHint")}</p>
                    ) : (
                      <div className="rounded-card border-border divide-border divide-y border">
                        {sheet.fields.map((field, at) => {
                          const column = choice.columns[at];
                          if (!column) return null;
                          return (
                            <div
                              key={`${sheet.sheet_name}-${field.name}`}
                              className="flex flex-wrap items-center gap-2 p-2"
                            >
                              <Checkbox
                                checked={column.keep}
                                onCheckedChange={(checked) =>
                                  patchColumn(sheet.sheet_name, at, {
                                    keep: checked === true,
                                  })
                                }
                                aria-label={t("columnKeep", { name: field.name })}
                              />
                              <Input
                                value={column.name}
                                onChange={(event) =>
                                  patchColumn(sheet.sheet_name, at, {
                                    name: event.target.value,
                                  })
                                }
                                disabled={!column.keep}
                                className="min-w-0 flex-1"
                                aria-label={t("fieldName")}
                              />
                              <Select
                                value={column.type}
                                onValueChange={(value) =>
                                  patchColumn(sheet.sheet_name, at, {
                                    type: value as DatasetFieldType,
                                  })
                                }
                              >
                                <SelectTrigger
                                  className="w-32"
                                  aria-label={t("fieldTypeLabel")}
                                  disabled={!column.keep}
                                >
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {FIELD_TYPES.map((type) => (
                                    <SelectItem key={type} value={type}>
                                      {t(`fieldType.${type}`)}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                              <Button
                                size="sm"
                                variant={column.isKey ? "secondary" : "ghost"}
                                disabled={!column.keep}
                                aria-pressed={column.isKey}
                                onClick={() => patchColumn(sheet.sheet_name, at, { isKey: true })}
                              >
                                {t("key")}
                              </Button>
                              <span className="text-muted-foreground w-24 text-xs">
                                {t("distinctRatio", {
                                  percent: Math.round(field.distinct_ratio * 100),
                                })}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {step === "validate" && (
            <div className="space-y-3">
              <p className="text-sm font-medium">{t("validateTitle")}</p>
              {selected.map((sheet) => {
                const choice = choices[sheet.sheet_name];
                if (!choice) return null;
                const kept = keptColumns(choice);
                return (
                  <div
                    key={sheet.sheet_name}
                    className="rounded-card border-border space-y-2 border p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">
                        {choice.target ? sheet.sheet_name : choice.name}
                      </span>
                      {choice.target ? (
                        <Badge variant="outline">{t("intoExisting")}</Badge>
                      ) : (
                        <Badge variant="secondary">
                          {t("newDatasetSummary", { columns: kept.length })}
                        </Badge>
                      )}
                      <span className="text-muted-foreground text-xs">
                        {t("sheetShape", { rows: sheet.row_count, cols: kept.length })}
                      </span>
                      <span className="text-muted-foreground text-xs">
                        {t("keyIs", { name: kept.find((column) => column.isKey)?.name ?? "" })}
                      </span>
                    </div>
                    {sheet.warnings.length > 0 && (
                      <ul className="text-muted-foreground space-y-1 text-xs">
                        {sheet.warnings.map((warning) => (
                          <li key={warning}>{warning}</li>
                        ))}
                      </ul>
                    )}
                    {sheet.preview.length > 0 && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead className="text-muted-foreground">
                            <tr>
                              {kept.map((column) => (
                                <th key={column.name} className="pr-3 font-medium">
                                  {column.name}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {sheet.preview.slice(0, 3).map((row, at) => (
                              <tr key={at}>
                                {kept.map((column) => (
                                  <td key={column.name} className="pr-3">
                                    {String(row[column.name] ?? "")}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {step === "publish" && (
            <div className="space-y-3" data-testid="import-progress">
              <p className="text-sm font-medium">{t("publishTitle")}</p>
              <Progress value={progress?.percent ?? 0} aria-label={t("publishTitle")} />
              <p className="text-muted-foreground text-xs" aria-live="polite">
                {progress?.current_sheet
                  ? t("publishCurrent", {
                      sheet: progress.current_sheet,
                      done: progress.done,
                      total: progress.total,
                    })
                  : t("publishQueued", { total: progress?.total ?? selected.length })}
              </p>
            </div>
          )}

          {step === "report" && results && (
            <div className="space-y-2" data-testid="import-report">
              <p className="text-sm font-medium">{t("importResults")}</p>
              <ul className="space-y-2">
                {results.map((result) => (
                  <li
                    key={result.sheet_name}
                    className="rounded-card border-border space-y-1 border p-3 text-sm"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={result.status === "committed" ? "secondary" : "destructive"}>
                        {result.status === "committed" ? t("committed") : t("failed")}
                      </Badge>
                      <span className="font-medium">{result.sheet_name}</span>
                      {result.status === "committed" ? (
                        <span className="text-muted-foreground">
                          {t("importedCounts", {
                            added: result.rows_added ?? 0,
                            changed: result.rows_changed ?? 0,
                            version: result.version_no ?? 0,
                          })}
                        </span>
                      ) : (
                        <span className="text-destructive">{result.error}</span>
                      )}
                      {(result.rows_withheld ?? 0) > 0 && (
                        <Badge variant="outline">
                          {t("rowsWithheld", { count: result.rows_withheld ?? 0 })}
                        </Badge>
                      )}
                    </div>
                    {(result.warnings?.length ?? 0) > 0 && (
                      <details className="text-muted-foreground text-xs">
                        <summary className="cursor-pointer">{t("drillDown")}</summary>
                        <ul className="mt-1 space-y-1">
                          {result.warnings?.map((warning) => (
                            <li key={warning}>{warning}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <DialogFooter>
          {step === "upload" && (
            <Button variant="ghost" onClick={() => close(false)}>
              {t("cancel")}
            </Button>
          )}
          {step === "sheets" && (
            <>
              <Button variant="ghost" onClick={() => setStep("upload")}>
                <ChevronLeft className="size-4" aria-hidden />
                {t("back")}
              </Button>
              <Button onClick={() => go("columns")} disabled={!canLeaveSheets}>
                {t("next")}
                <ChevronRight className="size-4" aria-hidden />
              </Button>
            </>
          )}
          {step === "columns" && (
            <>
              <Button variant="ghost" onClick={() => setStep("sheets")}>
                <ChevronLeft className="size-4" aria-hidden />
                {t("back")}
              </Button>
              <Button onClick={() => go("validate")} disabled={!canLeaveColumns}>
                {t("next")}
                <ChevronRight className="size-4" aria-hidden />
              </Button>
            </>
          )}
          {step === "validate" && (
            <>
              <Button variant="ghost" onClick={() => setStep("columns")}>
                <ChevronLeft className="size-4" aria-hidden />
                {t("back")}
              </Button>
              <Button onClick={publish} loading={commit.isPending}>
                {t("commitImport", { count: selected.length })}
              </Button>
            </>
          )}
          {step === "publish" && (
            <Button variant="ghost" onClick={() => close(false)} disabled>
              {t("publishing")}
            </Button>
          )}
          {step === "report" && <Button onClick={() => close(false)}>{t("done")}</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
