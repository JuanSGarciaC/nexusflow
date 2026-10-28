import { Center, Loader } from "@mantine/core";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "./AuthProvider";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  const location = useLocation();
  if (state.status === "loading") {
    return (
      <Center mih="100vh">
        <Loader aria-label="Cargando sesión" />
      </Center>
    );
  }
  if (state.status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <>{children}</>;
}
