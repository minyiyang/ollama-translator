export type ErrorDescription = { message: string; details: string };

/** Turn anything a component threw into a readable message plus optional stack details. */
export function describeError(error: unknown): ErrorDescription {
  if (error instanceof Error) {
    return { message: error.message || error.name, details: error.stack ?? "" };
  }
  if (typeof error === "string") return { message: error, details: "" };
  try {
    return { message: JSON.stringify(error) ?? String(error), details: "" };
  } catch {
    return { message: String(error), details: "" }; // e.g. a circular object
  }
}
