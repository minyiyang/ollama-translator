// Read and edit a nested config mapping by dotted path without mutating it.
export type Values = Record<string, any>;

export function getPath(values: Values, path: string): any {
  return path.split(".").reduce<any>((node, key) => (node && typeof node === "object" ? node[key] : undefined), values);
}

export function hasPath(values: Values, path: string): boolean {
  const keys = path.split(".");
  const parent = getPath(values, keys.slice(0, -1).join("."));
  return keys.length === 1 ? keys[0] in values : !!parent && typeof parent === "object" && keys[keys.length - 1] in parent;
}

export function setPath(values: Values, path: string, value: unknown): Values {
  const [head, ...rest] = path.split(".");
  const child = values[head];
  return {
    ...values,
    [head]: rest.length ? setPath(child && typeof child === "object" ? child : {}, rest.join("."), value) : value,
  };
}

/** Remove a key; empty parent sections are dropped so the YAML stays minimal. */
export function unsetPath(values: Values, path: string): Values {
  const [head, ...rest] = path.split(".");
  if (!(head in values)) return values;
  const copy = { ...values };
  if (!rest.length) {
    delete copy[head];
    return copy;
  }
  const child = unsetPath(copy[head] ?? {}, rest.join("."));
  if (Object.keys(child).length) copy[head] = child;
  else delete copy[head];
  return copy;
}

export function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
