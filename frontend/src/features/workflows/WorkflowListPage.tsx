import { Anchor, Button, Group, Pagination, Select, Table, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconPlus, IconSearch } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, unwrap } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate } from "../../lib/format";

const PAGE = 20;

export function WorkflowListPage() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [debounced] = useDebouncedValue(q, 300);
  const [status, setStatus] = useState<string | null>("active");
  const [page, setPage] = useState(1);

  const list = useQuery({
    queryKey: ["workflows", debounced, status, page],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/workflows", {
          params: {
            query: {
              q: debounced || undefined,
              status: (status as "active" | "archived" | null) ?? undefined,
              limit: PAGE,
              offset: (page - 1) * PAGE,
            },
          },
        }),
      ),
  });

  return (
    <>
      <PageHeader
        title="Flujos"
        description="Definiciones versionadas: se editan como borrador y se ejecuta una versión publicada."
        actions={
          can("workflows:write") && (
            <Button component={Link} to="/workflows/new" leftSection={<IconPlus size={16} />}>
              Nuevo flujo
            </Button>
          )
        }
      />
      <Group mb="md">
        <TextInput
          placeholder="Buscar por nombre"
          leftSection={<IconSearch size={16} />}
          value={q}
          onChange={(e) => {
            setQ(e.currentTarget.value);
            setPage(1);
          }}
          aria-label="Buscar flujos"
        />
        <Select
          data={[
            { value: "active", label: "Activos" },
            { value: "archived", label: "Archivados" },
          ]}
          value={status}
          onChange={(v) => {
            setStatus(v);
            setPage(1);
          }}
          clearable
          placeholder="Todos"
          aria-label="Estado"
        />
      </Group>
      <ErrorAlert error={list.error} />
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Nombre</Table.Th>
            <Table.Th>Estado</Table.Th>
            <Table.Th>Publicada</Table.Th>
            <Table.Th>Borrador</Table.Th>
            <Table.Th>Actualizado</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {list.data?.items.map((w) => (
            <Table.Tr key={w.id}>
              <Table.Td>
                <Anchor component={Link} to={`/workflows/${w.id}`}>
                  {w.name}
                </Anchor>
                {w.description && (
                  <Text size="xs" c="dimmed" lineClamp={1}>
                    {w.description}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                <StatusBadge status={w.status} />
              </Table.Td>
              <Table.Td>{w.published_version ? `v${w.published_version}` : "—"}</Table.Td>
              <Table.Td>{w.draft_version ? `v${w.draft_version}` : "—"}</Table.Td>
              <Table.Td>{formatDate(w.updated_at)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {list.data && list.data.items.length === 0 && (
        <Text c="dimmed" ta="center" py="xl">
          No hay flujos que coincidan.
        </Text>
      )}
      {list.data && list.data.total > PAGE && (
        <Pagination mt="md" total={Math.ceil(list.data.total / PAGE)} value={page} onChange={setPage} />
      )}
    </>
  );
}
