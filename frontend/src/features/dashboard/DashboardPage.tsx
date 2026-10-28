import { BarChart } from "@mantine/charts";
import { Anchor, Card, Group, SegmentedControl, SimpleGrid, Skeleton, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, unwrap } from "../../api/client";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, formatDuration, formatPercent } from "../../lib/format";

const WINDOWS = [
  { label: "24 h", value: "24" },
  { label: "7 días", value: "168" },
  { label: "30 días", value: "720" },
];

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card withBorder padding="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      <Text size="xl" fw={700} mt={4} data-testid={`stat-${label}`}>
        {value}
      </Text>
      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </Card>
  );
}

export function DashboardPage() {
  const [windowHours, setWindowHours] = useState("24");
  const summary = useQuery({
    queryKey: ["metrics", windowHours],
    queryFn: () =>
      unwrap(api.GET("/api/v1/metrics/summary", { params: { query: { window_hours: Number(windowHours) } } })),
    refetchInterval: 15_000,
  });
  const m = summary.data;
  const hourly = m?.bucket === "hour";
  const chartData =
    m?.timeline.map((b) => ({
      t: new Date(b.start).toLocaleString("es", hourly ? { hour: "2-digit", minute: "2-digit" } : { day: "2-digit", month: "short" }),
      Completadas: b.succeeded,
      Fallidas: b.failed,
    })) ?? [];

  return (
    <>
      <PageHeader
        title="Panel"
        description="Volumen, tasa de éxito y tiempos de las ejecuciones."
        actions={<SegmentedControl data={WINDOWS} value={windowHours} onChange={setWindowHours} aria-label="Ventana de tiempo" />}
      />
      <ErrorAlert error={summary.error} />
      {!m ? (
        <Skeleton h={300} />
      ) : (
        <Stack gap="lg">
          <SimpleGrid cols={{ base: 2, md: 4 }}>
            <Stat label="Ejecuciones" value={String(m.total_runs)} />
            <Stat label="Tasa de éxito" value={formatPercent(m.success_rate)} hint="Sin contar canceladas" />
            <Stat label="Duración p95" value={formatDuration(m.run_duration.p95_ms)} hint={`p50 ${formatDuration(m.run_duration.p50_ms)}`} />
            <Stat label="Reintentos" value={String(m.retries)} hint={`Intentos totales: ${m.attempts}`} />
          </SimpleGrid>

          <Card withBorder>
            <Title order={4} mb="sm">
              Ejecuciones por {hourly ? "hora" : "día"}
            </Title>
            {/* Barras apiladas: cada barra es un intervalo y su altura el total. Un área apilada
                dibujaba la línea de "Fallidas" encima de "Completadas" y parecía que había fallos. */}
            <BarChart
              h={240}
              data={chartData}
              dataKey="t"
              type="stacked"
              withLegend
              tickLine="y"
              series={[
                { name: "Completadas", color: "teal.6" },
                { name: "Fallidas", color: "red.6" },
              ]}
            />
          </Card>

          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Card withBorder>
              <Title order={4} mb="sm">
                Últimos fallos
              </Title>
              {m.recent_failures.length === 0 ? (
                <Text c="dimmed" size="sm">
                  Sin fallos en la ventana.
                </Text>
              ) : (
                <Table striped>
                  <Table.Tbody>
                    {m.recent_failures.map((f) => (
                      <Table.Tr key={f.run_id}>
                        <Table.Td>
                          <Anchor component={Link} to={`/runs/${f.run_id}`} size="sm">
                            {f.workflow_name}
                          </Anchor>
                          <Text size="xs" c="dimmed">
                            {f.step_id ? `${f.step_id}: ` : ""}
                            {f.message ?? f.error_class ?? ""}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <StatusBadge status={f.status} />
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs">{formatDate(f.finished_at)}</Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Card>
            <Card withBorder>
              <Title order={4} mb="sm">
                Errores y tipos de paso
              </Title>
              <Group align="flex-start" grow>
                {m.error_classes.length === 0 ? (
                  <Text c="dimmed" size="sm">
                    Sin errores en la ventana.
                  </Text>
                ) : (
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Clase de error</Table.Th>
                      <Table.Th>Intentos</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {m.error_classes.map((e) => (
                      <Table.Tr key={e.error_class}>
                        <Table.Td>{e.error_class}</Table.Td>
                        <Table.Td>{e.count}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
                )}
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Paso</Table.Th>
                      <Table.Th>p95</Table.Th>
                      <Table.Th>Fallos</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {m.step_types.map((s) => (
                      <Table.Tr key={s.step_type}>
                        <Table.Td>{s.step_type}</Table.Td>
                        <Table.Td>{formatDuration(s.p95_ms)}</Table.Td>
                        <Table.Td>
                          {s.failed_attempts}/{s.attempts}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Group>
            </Card>
          </SimpleGrid>
        </Stack>
      )}
    </>
  );
}
