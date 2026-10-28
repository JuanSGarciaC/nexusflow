import { Anchor, Pagination, Select, Table, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { ErrorAlert } from "../../components/ErrorAlert";
import { StatusBadge, statusLabel } from "../../components/StatusBadge";
import { formatDate, formatDuration, shortId } from "../../lib/format";

type RunStatus = Schemas["RunStatus"];
const STATUSES: RunStatus[] = ["pending", "running", "needs_review", "succeeded", "failed", "timed_out", "cancelled"];
const PAGE = 20;

export function RunTable({ workflowId }: { workflowId?: string }) {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [page, setPage] = useState(1);
  const runs = useQuery({
    queryKey: ["runs", workflowId, status, page],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/runs", {
          params: {
            query: { workflow_id: workflowId, status: status ?? undefined, limit: PAGE, offset: (page - 1) * PAGE },
          },
        }),
      ),
    refetchInterval: 5_000,
  });

  return (
    <>
      <Select
        mb="md"
        w={240}
        placeholder="Todos los estados"
        clearable
        aria-label="Filtrar por estado"
        data={STATUSES.map((s) => ({ value: s, label: statusLabel(s) }))}
        value={status}
        onChange={(v) => {
          setStatus(v as RunStatus | null);
          setPage(1);
        }}
      />
      <ErrorAlert error={runs.error} />
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Ejecución</Table.Th>
            <Table.Th>Estado</Table.Th>
            <Table.Th>Versión</Table.Th>
            <Table.Th>Origen</Table.Th>
            <Table.Th>Creada</Table.Th>
            <Table.Th>Duración</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {runs.data?.items.map((r) => (
            <Table.Tr key={r.id}>
              <Table.Td>
                <Anchor component={Link} to={`/runs/${r.id}`} ff="monospace">
                  {shortId(r.id)}
                </Anchor>
                {r.error && (
                  <Text size="xs" c="red" lineClamp={1}>
                    {String(r.error.message ?? r.error.class ?? "")}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                <StatusBadge status={r.status} />
              </Table.Td>
              <Table.Td>v{r.version}</Table.Td>
              <Table.Td>{r.trigger_type}</Table.Td>
              <Table.Td>{formatDate(r.created_at)}</Table.Td>
              <Table.Td>{formatDuration(r.duration_ms)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {runs.data && runs.data.items.length === 0 && (
        <Text c="dimmed" ta="center" py="xl">
          Sin ejecuciones.
        </Text>
      )}
      {runs.data && runs.data.total > PAGE && (
        <Pagination mt="md" total={Math.ceil(runs.data.total / PAGE)} value={page} onChange={setPage} />
      )}
    </>
  );
}
