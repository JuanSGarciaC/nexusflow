import { Select, Switch, Table } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, describeError, unwrap, type Schemas } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { RequirePermission } from "../../auth/RequirePermission";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PageHeader } from "../../components/PageHeader";
import { formatDate } from "../../lib/format";

type Role = Schemas["Role"];
const ROLES: Role[] = ["viewer", "operator", "editor", "admin"];

export function UsersPage() {
  const { state, can } = useAuth();
  const me = state.status === "authenticated" ? state.user.id : null;
  const queryClient = useQueryClient();
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => unwrap(api.GET("/api/v1/users", { params: { query: { limit: 100 } } })),
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["UserUpdateRequest"] }) =>
      unwrap(api.PATCH("/api/v1/users/{user_id}", { params: { path: { user_id: id } }, body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
    onError: (e) => notifications.show({ color: "red", message: describeError(e) }),
  });
  const canManage = can("users:manage");

  return (
    <RequirePermission permission="users:read">
      <PageHeader title="Usuarios" description="Roles y acceso. Los cambios tienen efecto inmediato y quedan auditados." />
      <ErrorAlert error={users.error} />
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Email</Table.Th>
            <Table.Th>Rol</Table.Th>
            <Table.Th>Activo</Table.Th>
            <Table.Th>Último acceso</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {users.data?.items.map((u) => {
            const self = u.id === me;
            return (
              <Table.Tr key={u.id}>
                <Table.Td>{u.email}</Table.Td>
                <Table.Td>
                  <Select
                    aria-label={`Rol de ${u.email}`}
                    w={140}
                    size="xs"
                    data={ROLES}
                    value={u.role}
                    allowDeselect={false}
                    disabled={!canManage || self}
                    onChange={(role) => role && update.mutate({ id: u.id, body: { role: role as Role } })}
                  />
                </Table.Td>
                <Table.Td>
                  <Switch
                    aria-label={`Activo ${u.email}`}
                    checked={u.is_active}
                    disabled={!canManage || self}
                    onChange={(e) => update.mutate({ id: u.id, body: { is_active: e.currentTarget.checked } })}
                  />
                </Table.Td>
                <Table.Td>{formatDate(u.last_login_at)}</Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </RequirePermission>
  );
}
