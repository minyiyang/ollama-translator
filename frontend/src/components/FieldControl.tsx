import { useEffect, useState } from "react";
import type { SchemaField } from "../lib/configCatalog";
import { Switch } from "./ui";

type Props = {
  field: SchemaField;
  value: any;
  onChange: (value: any) => void;
  installed?: string[] | null;
  isModel?: boolean;
};

function bounds(field: SchemaField) {
  const parts: string[] = [];
  if (field.minimum !== undefined) parts.push(`≥ ${field.minimum}`);
  if (field.exclusiveMinimum !== undefined) parts.push(`> ${field.exclusiveMinimum}`);
  if (field.maximum !== undefined) parts.push(`≤ ${field.maximum}`);
  if (field.exclusiveMaximum !== undefined) parts.push(`< ${field.exclusiveMaximum}`);
  return parts.join(", ");
}

function ModelStatus({ model, installed }: { model: string; installed?: string[] | null }) {
  if (!model || !installed) return null;
  const ok = installed.includes(model) || (!model.includes(":") && installed.includes(`${model}:latest`));
  return ok ? <span className="ok-mark" title="Installed">✓</span> : <span className="bad-mark" title="Not installed in Ollama">✗ not installed</span>;
}

function TagList({ items, onChange, isModel, installed }: { items: string[]; onChange: (items: string[]) => void; isModel?: boolean; installed?: string[] | null }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const value = draft.trim();
    if (value && !items.includes(value)) onChange([...items, value]);
    setDraft("");
  };
  return (
    <div className="taglist">
      {items.map((item) => (
        <span className="tag" key={item}>
          {item}
          {isModel && <ModelStatus model={item} installed={installed} />}
          <button type="button" aria-label={`Remove ${item}`} onClick={() => onChange(items.filter((x) => x !== item))}>×</button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        list={isModel ? "installed-models" : undefined}
        placeholder={isModel ? "add model, Enter" : "add item, Enter"}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
        onBlur={add}
      />
    </div>
  );
}

/** Key/value rows for mapping fields such as per-model context caps. */
function MapEditor({ field, value, onChange, isModelKey }: { field: SchemaField; value: any; onChange: (value: any) => void; isModelKey: boolean }) {
  const entries = Object.entries(value && typeof value === "object" ? value : {});
  const numeric = field.value_type === "integer" || field.value_type === "number";
  const [key, setKey] = useState("");
  const write = (next: [string, unknown][]) => onChange(Object.fromEntries(next));
  const coerce = (raw: string) => (numeric && raw !== "" && Number.isFinite(Number(raw)) ? Number(raw) : raw);
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {entries.map(([k, v]) => (
        <div className="row" style={{ margin: 0 }} key={k}>
          <span className="mono" style={{ minWidth: 160 }}>{k}</span>
          <input type={numeric ? "number" : "text"} value={String(v ?? "")} style={{ width: 140 }}
            onChange={(e) => write(entries.map(([ek, ev]) => [ek, ek === k ? coerce(e.target.value) : ev]))} />
          <button type="button" className="small" onClick={() => write(entries.filter(([ek]) => ek !== k))}>Remove</button>
        </div>
      ))}
      <div className="row" style={{ margin: 0 }}>
        <input type="text" value={key} placeholder={isModelKey ? "model name" : "key"} list={isModelKey ? "installed-models" : undefined} style={{ width: 200 }} onChange={(e) => setKey(e.target.value)} />
        <button type="button" className="small" disabled={!key.trim() || entries.some(([k]) => k === key.trim())}
          onClick={() => { write([...entries, [key.trim(), numeric ? 0 : ""]]); setKey(""); }}>Add</button>
      </div>
    </div>
  );
}

/** Keeps the typed string locally so partial input such as "0." survives re-renders. */
function NumberInput({ field, value, onChange }: { field: SchemaField; value: any; onChange: (value: any) => void }) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));
  const [focused, setFocused] = useState(false);
  useEffect(() => { if (!focused) setDraft(value == null ? "" : String(value)); }, [value, focused]);
  return (
    <input
      type="number"
      value={draft}
      step={field.type === "integer" ? 1 : "any"}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onChange={(e) => {
        const raw = e.target.value;
        setDraft(raw);
        if (raw === "") return onChange(field.nullable ? null : raw);
        const parsed = Number(raw);
        onChange(Number.isFinite(parsed) ? parsed : raw);
      }}
    />
  );
}

/** One schema-typed input. Values that fail to parse are passed through so validation can report them. */
export function FieldControl({ field, value, onChange, installed, isModel }: Props) {
  switch (field.type) {
    case "boolean":
      return <Switch checked={!!value} onChange={onChange} label={field.path} />;
    case "enum":
      return (
        <select value={value ?? ""} onChange={(e) => onChange(e.target.value === "" && field.nullable ? null : e.target.value)}>
          {field.nullable && <option value="">(none)</option>}
          {field.enum!.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      );
    case "integer":
    case "number":
      return (
        <>
          <NumberInput field={field} value={value} onChange={onChange} />
          {bounds(field) && <span className="default">{bounds(field)}</span>}
        </>
      );
    case "object":
      return <MapEditor field={field} value={value} onChange={onChange} isModelKey={field.key.startsWith("model")} />;
    case "array":
      return <TagList items={Array.isArray(value) ? value.map(String) : []} onChange={onChange} isModel={isModel} installed={installed} />;
    default:
      return (
        <>
          <input
            type="text"
            value={value ?? ""}
            list={isModel ? "installed-models" : undefined}
            placeholder={field.nullable ? "(not set)" : undefined}
            spellCheck={false}
            onChange={(e) => onChange(e.target.value === "" && field.nullable ? null : e.target.value)}
          />
          {isModel && <ModelStatus model={value ?? ""} installed={installed} />}
        </>
      );
  }
}
