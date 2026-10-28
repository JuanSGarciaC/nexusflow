import { Button, Code, Group, Table, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../../api/client";
import { RequirePermission } from "../../auth/RequirePermission";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PageHeader } from "../../components/PageHeader";
import { formatDate } from "../../lib/format";

export function AuditPage() {
  const [prefix, setPrefix] = useState("");
  const [debounced] = useDebouncedValue(prefix, 300);
  const pages = useInfiniteQuery({
    queryKey: ["audit", debounced],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/audit-logs", {
          params: { query: { action_prefix: debounced || undefined, before_id: pageParam ?? undefined, limit: 50 } },
        }),
      ),
    getNextPageParam: (last) => last.next_before_id,
  });
  const items = pages.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <RequirePermission permission="audit:read">
      <PageHeader title="Auditoría" description="Registro de acciones sensibles. Solo lectura." />
      <Group mb="md">
        <TextInput placeholder="Prefijo de acción (p. ej. auth.)" value={prefix} onChange={(e) => setPrefix(e.currentTarget.value)} aria-label="Filtrar por acción" />
      </Group>
      <ErrorAlert error={pages.error} />
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Fecha</Table.Th>
            <Table.Th>Acción</Table.Th>
            <Table.Th>Actor</Table.Th>
            <Table.Th>Recurso</Table.Th>
            <Table.Th>Detalles</Table.Th>
            <Table.Th>IP</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.map((e) => (
            <Table.Tr key={e.id}>
              <Table.Td>
                <Text size="xs">{formatDate(e.occurred_at)}</Text>
              </Table.Td>
              <Table.Td>
                <Code>{e.action}</Code>
              </Table.Td>
              <Table.Td>{e.actor_email ?? "—"}</Table.Td>
              <Table.Td>
                <Text size="xs">
                  {e.resource_type ?? ""} {e.resource_id ? e.resource_id.slice(0, 8) : ""}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="xs" ff="monospace" lineClamp={2}>
                  {Object.keys(e.details).length ? JSON.stringify(e.details) : ""}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="xs">{e.ip_address ?? "—"}</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {pages.hasNextPage && (
        <Group justify="center" mt="md">
          <Button variant="default" loading={pages.isFetchingNextPage} onClick={() => void pages.fetchNextPage()}>
            Cargar más
          </Button>
        </Group>
      )}
    </RequirePermission>
  );
}
