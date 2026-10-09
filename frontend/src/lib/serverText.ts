// Text the server sends that the interface can say in its own language: errors
// and rerun warnings come with a code, and a stage's status message is known by
// its English wording. Anything else is shown as the server wrote it.
// The codes are in book_agent/web/messages.py; see docs/LOCALIZATION.md, 2.5.

import en from "../i18n/en.json";
import { hasMessage, t, type MessageKey, type Values } from "../i18n";

/** An error body's message: its code's translation, else the server's English text. */
export function serverError(body: { error?: string; code?: string; params?: Values } | null | undefined, fallback = ""): string {
  const key = `server.error.${body?.code ?? ""}`;
  return hasMessage(key) ? t(key, body?.params) : body?.error || fallback;
}

/** A rerun warning in the interface language. */
export function rerunWarning(warning: { code: string; message: string }): string {
  const key = `server.rerun.${warning.code}`;
  return hasMessage(key) ? t(key) : warning.message;
}

// Each stage message the catalog knows, as a pattern over its English text with a group per value.
const STAGE_MESSAGES = (Object.keys(en) as MessageKey[])
  .filter((key) => key.startsWith("server.stage."))
  .map((key) => {
    const parts = en[key].split(/\{(\w+)\}/);
    const pattern = parts.map((part, index) => (index % 2 ? `(?<${part}>\\d+)` : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))).join("");
    return [key, new RegExp(`^${pattern}$`)] as const;
  });

/** What a stage says of itself, in the interface language when it is a message the catalog knows. */
export function stageMessage(message: string): string {
  for (const [key, pattern] of STAGE_MESSAGES) {
    const match = pattern.exec(message);
    if (match) return t(key, match.groups);
  }
  return message;
}
