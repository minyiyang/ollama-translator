// Interface text belongs in the catalog (src/i18n): this fails on English
// written straight into a component, which no translation could reach.

import ts from "typescript";
import { describe, expect, it } from "vitest";

// Every component, by its path under src/; tests and test helpers are not interface.
const SOURCES = Object.entries(import.meta.glob<string>("../**/*.tsx", { query: "?raw", import: "default", eager: true }))
  .map(([path, text]) => [path.slice(3), text] as const)
  .filter(([path]) => !path.endsWith(".test.tsx") && !path.startsWith("test/"));
// Attributes whose value a person reads or hears.
const TEXT_ATTRIBUTES = new Set(["title", "aria-label", "placeholder", "alt", "label", "aria-description"]);
// Names and notation that read the same in every language, the example language codes, and config paths.
const NEUTRAL = /^(Ollama Translator|EPUB|HTML|Markdown|XLIFF|CSV|YAML|JSON|RTF|PDF|LLM|ID|OK|en|ja)$/;
const CONFIG_PATHS = /^[a-z_]+(\.[a-z_]+)+(, [a-z_]+(\.[a-z_]+)+)*$/;
const isText = (value: string) => /\p{L}{2,}/u.test(value) && !NEUTRAL.test(value.trim()) && !CONFIG_PATHS.test(value.trim());

/** Literal text a component would render: JSX text, and string literals in JSX children or text attributes. */
function hardcodedText(path: string, text: string): string[] {
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: string[] = [];
  const report = (node: ts.Node, value: string) => {
    const { line } = source.getLineAndCharacterOfPosition(node.getStart());
    found.push(`${line + 1}: ${value.trim().replace(/\s+/g, " ")}`);
  };
  // String literals an expression can evaluate to; a call's arguments (message keys, class names) are not text.
  const literals = (node: ts.Node) => {
    if (ts.isCallExpression(node) || ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node)) return;
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      if (isText(node.text)) report(node, node.text);
      return;
    }
    if (ts.isTemplateExpression(node)) {
      const fixed = [node.head.text, ...node.templateSpans.map((span) => span.literal.text)].join(" ");
      if (isText(fixed)) report(node, node.getText());
      return;
    }
    if (ts.isConditionalExpression(node)) {
      literals(node.whenTrue);
      literals(node.whenFalse);
      return;
    }
    if (ts.isBinaryExpression(node)) {
      const operator = node.operatorToken.kind;
      if (operator === ts.SyntaxKind.AmpersandAmpersandToken) literals(node.right);
      else if (
        operator === ts.SyntaxKind.BarBarToken ||
        operator === ts.SyntaxKind.QuestionQuestionToken ||
        operator === ts.SyntaxKind.PlusToken
      ) {
        literals(node.left);
        literals(node.right);
      }
      return;
    }
    if (ts.isParenthesizedExpression(node)) literals(node.expression);
  };
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node) && isText(node.text)) report(node, node.text);
    if (ts.isJsxExpression(node) && node.expression && !ts.isJsxAttribute(node.parent)) literals(node.expression);
    if (ts.isJsxAttribute(node) && TEXT_ATTRIBUTES.has(node.name.getText()) && node.initializer) {
      if (ts.isStringLiteral(node.initializer)) {
        if (isText(node.initializer.text)) report(node, node.initializer.text);
      } else if (ts.isJsxExpression(node.initializer) && node.initializer.expression) {
        literals(node.initializer.expression);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

describe("no interface text outside the catalog", () => {
  for (const [path, text] of SOURCES) it(path, () => expect(hardcodedText(path, text)).toEqual([]));
});
