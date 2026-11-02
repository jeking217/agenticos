import { getLocaleBadge, type Locale } from "@/i18n";

/**
 * The mark in front of a language's name, in the switcher and the preferences menu.
 *
 * It used to be a flag emoji, which was wrong the moment a third and fourth
 * language arrived: a flag is a country, and `zh-CN` and `zh-TW` are one script
 * written two ways. The character says which of them it is without answering a
 * question nobody asked.
 *
 * One component rather than the same four classes at each call site, because the
 * two menus already disagreed about the size of the emoji they drew.
 */
export function LocaleBadge({ locale, className }: { locale: Locale; className?: string }) {
  return (
    <span
      aria-hidden
      className={[
        // Outlined rather than filled: a filled chip in a menu row that highlights on
        // hover is a second background competing with the first, and the character
        // inside it is the thing being read.
        "border-foreground/25 text-muted-foreground",
        "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-[5px] border",
        "text-[10px] leading-none font-semibold",
        className ?? "",
      ].join(" ")}
    >
      {getLocaleBadge(locale)}
    </span>
  );
}
