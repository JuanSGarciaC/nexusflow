import { Alert, Badge, Button, Group, JsonInput, Skeleton, Stack, Table, Tabs, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";

import { api, isConflict, unwrap, type Schemas } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorAlert } from "../../components/ErrorAlert";
import { JsonView } from "../../components/JsonView";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate } from "../../lib/format";
import { RunTable } from "../runs/RunTable";
import { IssuesList } from "./IssuesList";
import { SchedulesTab } from "./SchedulesTab";
import { parseJson, pretty } from "./templates";

type DefinitionIn = Schemas["WorkflowDefinition-Input"];
type Detail = Schemas["WorkflowDetail"];

function DefinitionTab({ wf, canWrite }: { wf: Detail; canWrite: boolean }) {
  const queryClient = useQueryClient();
  const draft = wf.draft;
  const base = draft ?? wf.published;
  const [text, setText] = useState(base ? pretty(base.definition) : "");
  const [issues, setIssues] = useState<Schemas["ValidationIssue"][] | undefined>(draft?.issues);

  const parsed = parseJson(text);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["workflow", wf.id] });

  const save = useMutation({
    mutationFn: (definition: DefinitionIn) =>
      unwrap(
        api.PUT("/api/v1/workflows/{workflow_id}/draft", {
          params: { path: { workflow_id: wf.id } },
          body: { definition, expected_revision: draft?.revision ?? null },
        }),
      ),
    onSuccess: async (saved) => {
      setIssues(saved.issues);
      notifications.show({ color: "green", message: `Borrador v${saved.version} guardado (revisión ${saved.revision})` });
      await refresh();
    },
  });
  const publish = useMutation({
    mutationFn: (revision: number) =>
      unwrap(
        api.POST("/api/v1/workflows/{workflow_id}/publish", {
          params: { path: { workflow_id: wf.id } },
          body: { expected_revision: revision },
        }),
      ),
    onSuccess: async (version) => {
      notifications.show({ color: "green", message: `Versión ${version.version} publicada` });
      await refresh();
    },
  });
  const validate = useMutation({
    mutationFn: (definition: DefinitionIn) => unwrap(api.POST("/api/v1/workflows/validate", { body: { definition } })),
    onSuccess: (r) => setIssues(r.issues),
  });

  const dirty = base ? text !== pretty(base.definition) : true;
  const conflict = isConflict(save.error) || isConflict(publish.error);

  return (
    <Stack>
      <Group gap="xs">
        {draft ? (
          <Badge variant="light">Borrador v{draft.version} · revisión {draft.revision}</Badge>
        ) : (
          <Badge variant="light" color="green">
            Mostrando la versión publicada v{wf.published?.version}
          </Badge>
        )}
        {draft && dirty && <Badge color="yellow">Cambios sin guardar</Badge>}
      </Group>
      {conflict && (
        <Alert color="orange" title="Otra persona guardó cambios">
          Tu copia está desactualizada. Recarga para ver la revisión actual antes de volver a guardar.
          <Button size="xs" mt="xs" variant="light" onClick={() => void refresh()}>
            Recargar
          </Button>
        </Alert>
      )}
      <JsonInput
        aria-label="Definición del flujo"
        autosize
        minRows={16}
        maxRows={36}
        formatOnBlur
        readOnly={!canWrite}
        value={text}
        onChange={setText}
        error={parsed.ok ? undefined : parsed.message}
        styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
      />
      <IssuesList issues={issues} />
      {!conflict && <ErrorAlert error={save.error ?? publish.error ?? validate.error} />}
      {canWrite && (
        <Group>
          <Button variant="default" disabled={!parsed.ok} loading={validate.isPending} onClick={() => parsed.ok && validate.mutate(parsed.value as DefinitionIn)}>
            Validar
          </Button>
          <Button disabled={!parsed.ok || (!!draft && !dirty)} loading={save.isPending} onClick={() => parsed.ok && save.mutate(parsed.value as DefinitionIn)}>
            {draft ? "Guardar borrador" : "Crear nueva versión"}
          </Button>
          {draft && (
            <Button
              color="green"
              disabled={dirty || (draft.issues?.length ?? 0) > 0}
              loading={publish.isPending}
              onClick={() => publish.mutate(draft.revision)}
            >
              Publicar v{draft.version}
            </Button>
          )}
        </Group>
      )}
    </Stack>
  );
}

function VersionsTab({ workflowId }: { workflowId: string }) {
  const versions = useQuery({
    queryKey: ["workflow", workflowId, "versions"],
    queryFn: () => unwrap(api.GET("/api/v1/workflows/{workflow_id}/versions", { params: { path: { workflow_id: workflowId } } })),
  });
  return (
    <>
      <ErrorAlert error={versions.error} />
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Versión</Table.Th>
            <Table.Th>Estado</Table.Th>
            <Table.Th>Revisión</Table.Th>
            <Table.Th>Publicada</Table.Th>
            <Table.Th>Hash</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {versions.data?.map((v) => (
            <Table.Tr key={v.id}>
              <Table.Td>v{v.version}</Table.Td>
              <Table.Td>
                <StatusBadge status={v.state} />
              </Table.Td>
              <Table.Td>{v.revision}</Table.Td>
              <Table.Td>{formatDate(v.published_at)}</Table.Td>
              <Table.Td>
                <Text ff="monospace" size="xs">
                  {v.definition_hash.slice(0, 12)}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </>
  );
}

function RunTab({ wf }: { wf: Detail }) {
  const navigate = useNavigate();
  const inputs = wf.published?.definition.inputs ?? [];
  const [text, setText] = useState(pretty(Object.fromEntries(inputs.map((i) => [i.name, i.type === "number" ? 0 : ""]))));
  const [key, setKey] = useState("");
  const parsed = parseJson(text);
  const run = useMutation({
    mutationFn: (input: Record<string, never>) =>
      unwrap(
        api.POST("/api/v1/workflows/{workflow_id}/runs", {
          params: { path: { workflow_id: wf.id } },
          body: { input, idempotency_key: key || null },
        }),
      ),
    onSuccess: (r) => navigate(`/runs/${r.id}`),
  });
  if (!wf.published) return <Alert color="yellow">Publica una versión para poder ejecutar el flujo.</Alert>;
  return (
    <Stack maw={700}>
      <Text size="sm">
        Se ejecutará la versión publicada <strong>v{wf.published.version}</strong>.
      </Text>
      <JsonInput label="Entrada (JSON)" autosize minRows={4} value={text} onChange={setText} error={parsed.ok ? undefined : parsed.message} formatOnBlur />
      <TextInput
        label="Clave de idempotencia (opcional)"
        description="Si repites la misma clave, se devuelve la misma ejecución en lugar de crear otra."
        value={key}
        onChange={(e) => setKey(e.currentTarget.value)}
        maxLength={128}
      />
      <ErrorAlert error={run.error} />
      <Group>
        <Button disabled={!parsed.ok} loading={run.isPending} onClick={() => parsed.ok && run.mutate(parsed.value as Record<string, never>)}>
          Ejecutar
        </Button>
      </Group>
    </Stack>
  );
}

export function WorkflowDetailPage() {
  const { workflowId = "" } = useParams();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const wf = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => unwrap(api.GET("/api/v1/workflows/{workflow_id}", { params: { path: { workflow_id: workflowId } } })),
  });
  const archive = useMutation({
    mutationFn: (status: "active" | "archived") =>
      unwrap(api.PATCH("/api/v1/workflows/{workflow_id}", { params: { path: { workflow_id: workflowId } }, body: { status } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] }),
  });

  if (wf.error) return <ErrorAlert error={wf.error} />;
  if (!wf.data) return <Skeleton h={400} />;
  const w = wf.data;
  const canWrite = can("workflows:write") && w.status === "active";

  return (
    <>
      <PageHeader
        title={w.name}
        description={w.description ?? undefined}
        actions={
          <>
            <StatusBadge status={w.status} />
            {can("workflows:write") && (
              <Button variant="default" size="xs" loading={archive.isPending} onClick={() => archive.mutate(w.status === "active" ? "archived" : "active")}>
                {w.status === "active" ? "Archivar" : "Restaurar"}
              </Button>
            )}
          </>
        }
      />
      <ErrorAlert error={archive.error} />
      <Tabs defaultValue="definition" keepMounted={false}>
        <Tabs.List mb="md">
          <Tabs.Tab value="definition">Definición</Tabs.Tab>
          <Tabs.Tab value="versions">Versiones</Tabs.Tab>
          {can("workflows:execute") && w.status === "active" && <Tabs.Tab value="run">Ejecutar</Tabs.Tab>}
          <Tabs.Tab value="runs">Ejecuciones</Tabs.Tab>
          {can("schedules:manage") && <Tabs.Tab value="schedules">Programaciones</Tabs.Tab>}
          {w.published && <Tabs.Tab value="published">Publicada</Tabs.Tab>}
        </Tabs.List>
        <Tabs.Panel value="definition">
          {/* La key remonta el editor cuando cambia la revisión del servidor (tras guardar o
              publicar): así no hace falta sincronizar estado con un efecto. */}
          <DefinitionTab key={`${w.draft?.id ?? "none"}:${w.draft?.revision ?? 0}:${w.published?.version ?? 0}`} wf={w} canWrite={canWrite} />
        </Tabs.Panel>
        <Tabs.Panel value="versions">
          <VersionsTab workflowId={w.id} />
        </Tabs.Panel>
        <Tabs.Panel value="run">
          <RunTab wf={w} />
        </Tabs.Panel>
        <Tabs.Panel value="runs">
          <RunTable workflowId={w.id} />
        </Tabs.Panel>
        <Tabs.Panel value="schedules">
          <SchedulesTab workflowId={w.id} canRun={w.status === "active"} />
        </Tabs.Panel>
        <Tabs.Panel value="published">{w.published && <JsonView value={w.published.definition} maxHeight={600} />}</Tabs.Panel>
      </Tabs>
    </>
  );
}
