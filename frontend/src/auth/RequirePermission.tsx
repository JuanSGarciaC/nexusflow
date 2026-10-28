import { Alert } from "@mantine/core";
import type { ReactNode } from "react";

import { useAuth, type Permission } from "./AuthProvider";

/**
 * Oculta una página si el usuario no tiene el permiso. Es solo experiencia de usuario: la
 * autorización real la aplica el backend en cada endpoint.
 */
export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(permission)) {
    return (
      <Alert color="yellow" title="Sin acceso">
        Tu rol no incluye el permiso <strong>{permission}</strong>. Pide acceso a un administrador.
      </Alert>
    );
  }
  return <>{children}</>;
}
