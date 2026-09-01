/**
 * The API has no user resource -- a user is just an id string that documents
 * are scoped by (see app/api/v1/users.py: an unknown user is an empty page,
 * not a 404). So the roster of "known" users is a client-side convenience,
 * persisted in localStorage, not a server record.
 */

export interface UserProfile {
  /** The value sent as `user_id`. Must match the API's pattern. */
  id: string;
  /** Display name. Falls back to the id. */
  label: string;
  created_at: string;
}

/** Mirrors USER_ID_PATTERN in app/schemas/document.py. */
export const USER_ID_PATTERN = /^[A-Za-z0-9_.:@-]{1,64}$/;

const USERS_KEY = "docs-ai.users";
const ACTIVE_KEY = "docs-ai.active-user";

export const SEED_USERS: UserProfile[] = [
  { id: "user-1", label: "user-1", created_at: new Date(0).toISOString() },
];

function readJSON<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    // A corrupt or unavailable store is not a reason to fail to render.
    return null;
  }
}

function writeJSON(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Private mode / quota. The in-memory state stays correct for this session.
  }
}

export function loadUsers(): UserProfile[] {
  const stored = readJSON<UserProfile[]>(USERS_KEY);
  if (!Array.isArray(stored) || stored.length === 0) return SEED_USERS;
  return stored.filter((u) => u && typeof u.id === "string" && USER_ID_PATTERN.test(u.id));
}

export function saveUsers(users: UserProfile[]): void {
  writeJSON(USERS_KEY, users);
}

export function loadActiveUserId(): string | null {
  const stored = readJSON<string>(ACTIVE_KEY);
  return typeof stored === "string" ? stored : null;
}

export function saveActiveUserId(id: string): void {
  writeJSON(ACTIVE_KEY, id);
}

/** Two-letter monogram for the selector avatar. */
export function initials(user: UserProfile): string {
  const source = user.label.trim() || user.id;
  const parts = source.split(/[\s_.:@-]+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : source.slice(0, 2);
  return letters.toUpperCase();
}
