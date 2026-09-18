export type DiffPart = { kind: "same" | "del" | "ins"; text: string };

/** Character diff (LCS) trimmed to the changed middle so long passages stay cheap. */
export function diffChars(before: string, after: string): DiffPart[] {
  const a = [...before], b = [...after];
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p++;
  let s = 0;
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
  const x = a.slice(p, a.length - s), y = b.slice(p, b.length - s);
  const parts: DiffPart[] = [];
  const push = (kind: DiffPart["kind"], text: string) => {
    if (!text) return;
    const last = parts[parts.length - 1];
    if (last && last.kind === kind) last.text += text;
    else parts.push({ kind, text });
  };
  push("same", a.slice(0, p).join(""));
  if (x.length * y.length > 4e6) {
    push("del", x.join(""));
    push("ins", y.join(""));
  } else {
    const n = x.length, m = y.length, w = m + 1;
    const table = new Uint32Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--)
        table[i * w + j] = x[i] === y[j] ? table[(i + 1) * w + j + 1] + 1 : Math.max(table[(i + 1) * w + j], table[i * w + j + 1]);
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (x[i] === y[j]) { push("same", x[i]); i++; j++; }
      else if (table[(i + 1) * w + j] >= table[i * w + j + 1]) push("del", x[i++]);
      else push("ins", y[j++]);
    }
    while (i < n) push("del", x[i++]);
    while (j < m) push("ins", y[j++]);
  }
  push("same", a.slice(a.length - s).join(""));
  return parts;
}
