/**
 * Helpers for secret `*_ref` fields.
 *
 * - Detect whether a value is already a `<scheme>:<...>` ref string.
 * - Sanitize free-form text into a single secret-store name segment.
 *
 * Name rules mirror the backend regex in ``openpoly/news/secret_store.py``
 * (`[A-Za-z0-9_-]` characters).
 */

// Schemes the backend resolver knows about.
const REF_SCHEME_RE = /^(env|local|vault|keychain):/

export function isRefFormatted(value: unknown): boolean {
  return typeof value === 'string' && REF_SCHEME_RE.test(value)
}

/**
 * Normalize free-form text into a secret-store name segment: runs of
 * disallowed characters collapse to a single `-`; leading and trailing
 * dashes are stripped.
 */
export function sanitizeNameSegment(s: string): string {
  return s
    .trim()
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
}
