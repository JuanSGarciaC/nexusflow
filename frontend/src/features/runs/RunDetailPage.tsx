import { Accordion, Alert, Anchor, Button, Card, Group, JsonInput, Modal, SimpleGrid, Skeleton, Stack, Table, Text, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconRefresh } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, unwrap, type Schemas } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorAlert } from "../../components/ErrorAlert";
import { JsonView } from "../../components/JsonView";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, formatDuration, shortId } from "../../lib/format";
import { LazyFlowCanvas } from "../canvas/LazyFlowCanvas";
import { parseJson } from "../workflows/templates";
import { versionQuery } from "../workflows/VersionViewer";

type Step = Schemas["StepView"];
type ResolveAction = Schemas["ResolveStep"]["action"];
const ACTIVE = new Set(["pending", "running"]);
const FINISHED = new Set(["succeeded", "failed", "cancelled", "timed_out"]);

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      <Text size="sm">{children}</Text>
    </div>
  );
}

function ResolveModal({ runId, step, onClose }: { runId: string; step: Step; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [action, setAction] = useState<ResolveAction | null>(null);
  const [output, setOutput] = useState("{}");
  const [note, setNote] = useState("");
  const parsed = parseJson(output);
  const resolve = useMutation({
    mutationFn: (chosen: ResolveAction) =>
      unwrap(
        api.POST("/api/v1/runs/{run_id}/steps/{step_id}/resolve", {
          params: { path: { run_id: runId, step_id: step.step_id } },
          body: {
            action: chosen,
            output: chosen === "mark_succeeded" && parsed.ok ? (parsed.value as Schemas["JsonValue"]) : null,
            note: note || null,
          },
        }),
      ),
    onSuccess: async () => {
      notifications.show({ color: "green", message: `Paso ${step.step_id} resuelto` });
      await queryClient.invalidateQueries({ queryKey: ["run", runId] });
      onClose();
    },
  });
  return (
    <Modal opened onClose={onClose} title={`Resolver «${step.step_id}»`} size="lg">
      <Stack>
        <Alert color="orange" icon={<IconAlertTriangle size={18} />}>
          El resultado de este paso es desconocido y no es idempotente: reintentarlo podría repetir el efecto en el sistema externo (por ejemplo, un cobro).
          Comprueba primero en ese sistema qué ocurrió.
        </Alert>
        <Text size="sm">
          Clave de idempotencia enviada: <Text span ff="monospace">{step.idempotency_key}</Text>
        </Text>
        <Textarea label="Nota (queda en la auditoría)" maxLength={500} value={note} onChange={(e) => setNote(e.currentTarget.value)} />
        {action === "mark_succeeded" && (
          <JsonInput label="Salida a registrar (JSON)" autosize minRows={3} value={output} onChange={setOutput} error={parsed.ok ? undefined : parsed.message} />
        )}
        <ErrorAlert error={resolve.error} />
        <Group>
          <Button variant="default" onClick={() => { setAction("retry"); resolve.mutate("retry"); }} loading={resolve.isPending && action === "retry"}>
            Reintentar (misma clave)
          </Button>
          {action === "mark_succeeded" ? (
            <Button color="green" disabled={!parsed.ok} loading={resolve.isPending} onClick={() => resolve.mutate("mark_succeeded")}>
              Confirmar como completado
            </Button>
          ) : (
            <Button color="green" variant="light" onClick={() => setAction("mark_succeeded")}>
              Marcar como completado…
            </Button>
          )}
          <Button color="red" variant="light" loading={resolve.isPending && action === "mark_failed"} onClick={() => { setAction("mark_failed"); resolve.mutate("mark_failed"); }}>
            Marcar como fallido
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function StepPanel({ step }: { step: Step }) {
  return (
    <Stack gap="sm">
      <Group gap="xl">
        <Field label="Tipo">{step.step_type}</Field>
        <Field label="Efecto">{step.side_effect}</Field>
        <Field label="Intentos">
          {step.attempt_count}/{step.max_attempts}
        </Field>
        <Field label="Próximo intento">{formatDate(step.next_attempt_at)}</Field>
      </Group>
      {step.error && (
        <Alert color="red" title={String(step.error.class ?? "Error")}>
          {String(step.error.message ?? "")}
          {step.error.http_status ? ` (HTTP ${String(step.error.http_status)})` : ""}
        </Alert>
      )}
      {step.attempts.length > 0 && (
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>#</Table.Th>
              <Table.Th>Inicio</Table.Th>
              <Table.Th>Duración</Table.Th>
              <Table.Th>Resultado</Table.Th>
              <Table.Th>Error</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {step.attempts.map((a) => (
              <Table.Tr key={a.attempt_number}>
                <Table.Td>{a.attempt_number}</Table.Td>
                <Table.Td>{formatDate(a.started_at)}</Table.Td>
                <Table.Td>{formatDuration(a.duration_ms)}</Table.Td>
                <Table.Td>{a.outcome ?? "—"}</Table.Td>
                <Table.Td>
                  {a.error_class && (
                    <Text size="xs" c="red">
                      {a.error_class}
                      {a.http_status ? ` · HTTP ${a.http_status}` : ""} {a.error_message ? `· ${a.error_message}` : ""}
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {step.output !== null && step.output !== undefined && <JsonView value={step.output} />}
    </Stack>
  );
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [resolving, setResolving] = useState<Step | null>(null);
  // Pasos abiertos del acordeón. null hasta que llega la ejecución; entonces se abren los que
  // no terminaron bien y ya no cambia solo: un paso que se está leyendo no se cierra al terminar.
  const [opened, setOpened] = useState<string[] | null>(null);
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => unwrap(api.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: runId } } })),
    // Mientras la ejecución avanza, se consulta cada 2 s; al terminar, se deja de consultar.
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 2_000 : false),
  });
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/runs/{run_id}/cancel", { params: { path: { run_id: runId } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", runId] }),
  });

  // El lienzo usa la definición de la versión que se ejecutó, no la actual del flujo.
  const version = useQuery({
    ...versionQuery(run.data?.workflow_id ?? "", run.data?.version ?? 0),
    enabled: run.data !== undefined,
    staleTime: Infinity,
  });
  const rerun = useMutation({
    mutationFn: (r: Schemas["RunDetail"]) =>
      unwrap(
        api.POST("/api/v1/workflows/{workflow_id}/runs", {
          params: { path: { workflow_id: r.workflow_id } },
          // Sin idempotency_key: reutilizar la original devolvería la misma ejecución.
          body: { input: r.input as Record<string, never>, version: r.version },
        }),
      ),
    onSuccess: (created) => navigate(`/runs/${created.id}`),
  });

  if (run.error) return <ErrorAlert error={run.error} />;
  if (!run.data) return <Skeleton h={400} />;
  const r = run.data;
  if (opened === null) setOpened(r.steps.filter((s) => s.status !== "succeeded").map((s) => s.id));
  const cancellable = ["pending", "running", "needs_review"].includes(r.status);
  const canExecute = can("workflows:execute");
  const openSteps = opened ?? [];
  const showStep = (stepId: string) => {
    const step = r.steps.find((s) => s.step_id === stepId);
    if (!step) return;
    setOpened([...new Set([...openSteps, step.id])]);
    document.getElementById(`step-${step.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <>
      <PageHeader
        title={`Ejecución ${shortId(r.id)}`}
        description={
          <Anchor component={Link} to={`/workflows/${r.workflow_id}`} size="sm">
            Ver flujo (v{r.version})
          </Anchor>
        }
        actions={
          <>
            <StatusBadge status={r.status} />
            {canExecute && cancellable && (
              <Button color="red" variant="light" size="xs" loading={cancel.isPending} onClick={() => cancel.mutate()}>
                Cancelar
              </Button>
            )}
            {canExecute && FINISHED.has(r.status) && (
              <Button variant="light" size="xs" leftSection={<IconRefresh size={14} />} loading={rerun.isPending} onClick={() => rerun.mutate(r)}>
                Reejecutar
              </Button>
            )}
          </>
        }
      />
      <ErrorAlert error={cancel.error ?? rerun.error} />
      <Stack>
        <Card withBorder>
          <SimpleGrid cols={{ base: 2, md: 5 }}>
            <Field label="Origen">{r.trigger_type}</Field>
            <Field label="Creada">{formatDate(r.created_at)}</Field>
            <Field label="Fin">{formatDate(r.finished_at)}</Field>
            <Field label="Duración">{formatDuration(r.duration_ms)}</Field>
            <Field label="Idempotencia">{r.idempotency_key ?? "—"}</Field>
          </SimpleGrid>
        </Card>
        {r.error && (
          <Alert color="red" title={`Error${r.error.step_id ? ` en «${String(r.error.step_id)}»` : ""}: ${String(r.error.class ?? "")}`} role="alert">
            {String(r.error.message ?? "")}
          </Alert>
        )}
        {version.data && <LazyFlowCanvas definition={version.data.definition} steps={r.steps} onSelectStep={showStep} height={360} />}
        {version.error && (
          <Text size="sm" c="dimmed">
            No se pudo cargar el lienzo de esta versión.
          </Text>
        )}
        <Title order={4}>Pasos</Title>
        <Accordion variant="separated" multiple value={openSteps} onChange={setOpened}>
          {r.steps.map((step) => (
            <Accordion.Item key={step.id} value={step.id} id={`step-${step.id}`}>
              <Accordion.Control>
                <Group justify="space-between" pr="md">
                  <Text fw={600} ff="monospace">
                    {step.sequence}. {step.step_id}
                  </Text>
                  <StatusBadge status={step.status} />
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                {/* La acción va en el panel y no en el encabezado: el encabezado del acordeón ya
                    es un botón, y un botón anidado es HTML inválido e inaccesible. */}
                {step.status === "needs_review" && canExecute && (
                  <Alert color="orange" mb="sm" title="Este paso necesita una decisión manual">
                    <Button size="xs" color="orange" mt="xs" onClick={() => setResolving(step)}>
                      Resolver
                    </Button>
                  </Alert>
                )}
                <StepPanel step={step} />
              </Accordion.Panel>
            </Accordion.Item>
          ))}
        </Accordion>
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          <div>
            <Title order={5} mb="xs">
              Entrada
            </Title>
            <JsonView value={r.input} />
          </div>
          <div>
            <Title order={5} mb="xs">
              Salida
            </Title>
            <JsonView value={r.output ?? null} />
          </div>
        </SimpleGrid>
      </Stack>
      {resolving && <ResolveModal runId={r.id} step={resolving} onClose={() => setResolving(null)} />}
    </>
  );
}
