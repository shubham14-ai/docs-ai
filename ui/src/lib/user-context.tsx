"use client";

/**
 * Active-user context. Everything user-scoped in the app reads `userId` from
 * here, so switching users re-queries in place -- no page reload, no prop
 * threading. `revision` bumps on every switch: components key their fetch
 * effects on it so an in-flight response for the previous user cannot land
 * after the switch.
 */

import * as React from "react";
import {
  loadActiveUserId,
  loadUsers,
  saveActiveUserId,
  saveUsers,
  SEED_USERS,
  type UserProfile,
} from "./users";

interface UserContextValue {
  users: UserProfile[];
  activeUser: UserProfile;
  userId: string;
  /** Increments on every user switch. Use as a fetch dependency. */
  revision: number;
  /** True until localStorage has been read on the client. */
  hydrated: boolean;
  selectUser: (id: string) => void;
  addUser: (id: string, label?: string) => UserProfile;
  hasUser: (id: string) => boolean;
}

const UserContext = React.createContext<UserContextValue | null>(null);

export function UserProvider({ children }: { children: React.ReactNode }) {
  // Seeded identically on server and client; the stored roster is applied in an
  // effect so the first client render matches the SSR output.
  const [users, setUsers] = React.useState<UserProfile[]>(SEED_USERS);
  const [activeId, setActiveId] = React.useState<string>(SEED_USERS[0].id);
  const [revision, setRevision] = React.useState(0);
  const [hydrated, setHydrated] = React.useState(false);

  React.useEffect(() => {
    const stored = loadUsers();
    const storedActive = loadActiveUserId();
    setUsers(stored);
    if (storedActive && stored.some((u) => u.id === storedActive)) {
      setActiveId(storedActive);
    } else {
      setActiveId(stored[0].id);
    }
    setHydrated(true);
  }, []);

  // A ref, not the state value, so the callback identity stays stable and the
  // guard never fires twice under StrictMode's double invocation.
  const activeIdRef = React.useRef(activeId);
  activeIdRef.current = activeId;

  const selectUser = React.useCallback((id: string) => {
    if (activeIdRef.current === id) return;
    activeIdRef.current = id;
    saveActiveUserId(id);
    setActiveId(id);
    setRevision((r) => r + 1);
  }, []);

  const addUser = React.useCallback((id: string, label?: string) => {
    const profile: UserProfile = {
      id,
      label: label?.trim() || id,
      created_at: new Date().toISOString(),
    };
    setUsers((current) => {
      const next = current.some((u) => u.id === id)
        ? current.map((u) => (u.id === id ? profile : u))
        : [...current, profile];
      saveUsers(next);
      return next;
    });
    return profile;
  }, []);

  const value = React.useMemo<UserContextValue>(() => {
    const activeUser =
      users.find((u) => u.id === activeId) ??
      ({ id: activeId, label: activeId, created_at: new Date(0).toISOString() } as UserProfile);
    return {
      users,
      activeUser,
      userId: activeUser.id,
      revision,
      hydrated,
      selectUser,
      addUser,
      hasUser: (id: string) => users.some((u) => u.id === id),
    };
  }, [users, activeId, revision, hydrated, selectUser, addUser]);

  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export function useUser(): UserContextValue {
  const ctx = React.useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used inside <UserProvider>");
  return ctx;
}
