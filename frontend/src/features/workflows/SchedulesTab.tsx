import { Alert, Button, Code, Group, JsonInput, Select, Stack, Switch, Table, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../../api/client";
import { ErrorAlert } from "../../components/ErrorAlert";
import { formatDate } from "../../lib/format";
import { parseJson } from "./templates";

const PRESETS = [
  { value: "*/15 * * * *", label: "Cada 15 minutos" },
  { value: "0 * * * *", label: "Cada hora" },
  { value: "0 9 * * 1-5", label: "Días laborables a las 09:00" },
  { value: "0 0 * * *", label: "Cada día a medianoche" },
  { value: "0 8 * * 1", label: "Cada lunes a las 08:00" },
];

const browserZone = (): string => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

function CreateSchedule({ workflowId, onDone }: { workflowId: string; onDone: () => void }) {
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 9 * * 1-5");
  const [timezone, setTimezone] = useState(browserZone());
  const [input, setInput] = useState("{}");
  const parsed = parseJson(input);
  const create = useMutation({
    mutationFn: (body: Record<string, never>) =>
      unwrap(
        api.POST("/api/v1/workflows/{workflow_id}/schedules", {
          params: { path: { workflow_id: workflowId } },
          body: { name, cron_expression: cron, timezone, input: body, is_active: true },
        }),
      ),
    onSuccess: () => {
      notifications.show({ color: "green", message: "Programación creada" });
      onDone();
    },
  });
  return (
    <Stack maw={620}>
      <TextInput label="Nombre" required value={name} onChange={(e) => setName(e.currentTarget.value)} maxLength={100} />
      <Select label="Plantilla" placeholder="Elige una frecuencia habitual" data={PRESETS} value={null} onChange={(v) => v && setCron(v)} clearable={false} />
      <TextInput
        label="Expresión cron"
        description="5 campos: minuto hora día-del-mes mes día-de-la-semana"
        required
        ff="monospace"
        value={cron}
        onChange={(e) => setCron(e.currentTarget.value)}
      />
      <TextInput label="Zona horaria" description="Nombre IANA, p. ej. Europe/Madrid" required value={timezone} onChange={(e) => setTimezone(e.currentTarget.value)} />
      <JsonInput label="Entrada (JSON)" autosize minRows={3} formatOnBlur value={input} onChange={setInput} error={parsed.ok ? undefined : parsed.message} />
      <ErrorAlert error={create.error} />
      <Group>
        <Button disabled={!name || !parsed.ok} loading={create.isPending} onClick={() => parsed.ok && create.mutate(parsed.value as Record<string, never>)}>
          Crear programación
        </Button>
        <Button variant="default" onClick={onDone}>
          Cancelar
        </Button>
      </Group>
    </Stack>
  );
}

export function SchedulesTab({ workflowId, canRun }: { workflowId: string; canRun: boolean }) {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const key = ["workflow", workflowId, "schedules"];
  const list = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET("/api/v1/workflows/{workflow_id}/schedules", { params: { path: { workflow_id: workflowId } } })),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });
  const toggle = useMutation({
    mutationFn: (v: { id: string; is_active: boolean }) =>
      unwrap(api.PATCH("/api/v1/schedules/{schedule_id}", { params: { path: { schedule_id: v.id } }, body: { is_active: v.is_active } })),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/schedules/{schedule_id}", { params: { path: { schedule_id: id } } })),
    onSuccess: refresh,
  });

  if (creating) {
    return (
      <CreateSchedule
        workflowId={workflowId}
        onDone={() => {
          setCreating(false);
          void refresh();
        }}
      />
    );
  }
  return (
    <Stack>
      <ErrorAlert error={list.error ?? toggle.error ?? remove.error} />
      {!canRun && <Alert color="yellow">El flujo está archivado: no se puede programar.</Alert>}
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Cada disparo ejecuta la última versión publicada. Si el servicio está caído, los disparos perdidos no se recuperan.
        </Text>
        {canRun && <Button onClick={() => setCreating(true)}>Nueva programación</Button>}
      </Group>
      {list.data?.length === 0 && <Text c="dimmed">Este flujo no tiene programaciones.</Text>}
      {!!list.data?.length && (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Nombre</Table.Th>
              <Table.Th>Horario</Table.Th>
              <Table.Th>Próximo disparo</Table.Th>
              <Table.Th>Último disparo</Table.Th>
              <Table.Th>Activa</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {list.data.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td>
                  {s.name}
                  {s.last_error && (
                    <Text size="xs" c="red">
                      {s.last_error}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Code>{s.cron_expression}</Code>
                  <Text size="xs" c="dimmed">
                    {s.timezone}
                  </Text>
                </Table.Td>
                <Table.Td>{formatDate(s.next_run_at)}</Table.Td>
                <Table.Td>{formatDate(s.last_run_at)}</Table.Td>
                <Table.Td>
                  <Switch
                    aria-label={`Activar ${s.name}`}
                    checked={s.is_active}
                    disabled={!canRun || toggle.isPending}
                    onChange={(e) => toggle.mutate({ id: s.id, is_active: e.currentTarget.checked })}
                  />
                </Table.Td>
                <Table.Td>
                  <Button
                    variant="subtle"
                    color="red"
                    size="xs"
                    aria-label={`Eliminar ${s.name}`}
                    loading={remove.isPending && remove.variables === s.id}
                    onClick={() => remove.mutate(s.id)}
                  >
                    <IconTrash size={16} />
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
