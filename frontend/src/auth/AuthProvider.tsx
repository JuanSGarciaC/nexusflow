import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, unwrap, type Schemas } from "../api/client";
import { onTokenChange, refreshAccessToken, setAccessToken } from "../api/session";

export type Me = Schemas["MeResponse"];
export type Permission = string;

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: Me };

type AuthContextValue = {
  state: AuthState;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: Permission) => boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

async function loadMe(): Promise<Me> {
  return unwrap(api.GET("/api/v1/users/me"));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const queryClient = useQueryClient();

  // Al cargar la página: si la cookie de refresh sigue vigente, se recupera la sesión.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const token = await refreshAccessToken();
      if (cancelled) return;
      if (!token) {
        setState({ status: "anonymous" });
        return;
      }
      try {
        const user = await loadMe();
        if (!cancelled) setState({ status: "authenticated", user });
      } catch {
        if (!cancelled) setState({ status: "anonymous" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Si una renovación falla en mitad del uso (sesión revocada), se vuelve al login.
  useEffect(
    () =>
      onTokenChange((token) => {
        if (token === null) {
          setState((prev) => (prev.status === "authenticated" ? { status: "anonymous" } : prev));
          queryClient.clear();
        }
      }),
    [queryClient],
  );

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await unwrap(api.POST("/api/v1/auth/login", { body: { email, password } }));
    setAccessToken(tokens.access_token);
    const user = await loadMe();
    setState({ status: "authenticated", user });
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.POST("/api/v1/auth/logout");
    } finally {
      setAccessToken(null);
      queryClient.clear();
      setState({ status: "anonymous" });
    }
  }, [queryClient]);

  const can = useCallback(
    (permission: Permission) =>
      state.status === "authenticated" && state.user.permissions.includes(permission),
    [state],
  );

  const value = useMemo(() => ({ state, login, logout, can }), [state, login, logout, can]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  return value;
}
