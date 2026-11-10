import { Button, Code, CopyButton, Group, Modal, NumberInput, Stack, Switch, Table, Text, TextInput } from "@mantine/core";
import { IconRefresh, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ErrorAlert } from "../../components/ErrorAlert";
import { SecretOnce } from "../../components/SecretOnce";
import { formatDate } from "../../lib/format";

type Trigger = Schemas["TriggerView"];

export function triggersQuery(workflowId: string) {
  return {
    queryKey: ["workflow", workflowId, "triggers"],
    queryFn: () => unwrap(api.GET("/api/v1/workflows/{workflow_id}/triggers", { params: { path: { workflow_id: workflowId } } })),
  };
}

/** Webhooks entrantes de un flujo: cada uno tiene una URL propia y un secreto para firmar. */
export function TriggersTab({ workflowId }: { workflowId: string }) {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [limit, setLimit] = useState<number>(60);
  const [rotating, setRotating] = useState<Trigger | null>(null);
  // El secreto vive solo aquí. Las mutaciones lo entregan con setSecret y no lo devuelven,
  // para que no quede retenido en la caché de mutaciones de react-query.
  const [secret, setSecret] = useState<string | null>(null);

  const list = useQuery(triggersQuery(workflowId));
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["workflow", workflowId, "triggers"] });

  const create = useMutation({
    mutationFn: async () => {
      const created = await unwrap(
        api.POST("/api/v1/workflows/{workflow_id}/triggers", {
          params: { path: { workflow_id: workflowId } },
          body: { name: name.trim(), rate_limit_per_minute: limit },
        }),
      );
      setSecret(created.secret);
    },
    onSuccess: () => {
      setCreating(false);
      setName("");
      return refresh();
    },
  });
  const rotate = useMutation({
    mutationFn: async (id: string) => {
      const rotated = await unwrap(api.POST("/api/v1/triggers/{trigger_id}/rotate-secret", { params: { path: { trigger_id: id } } }));
      setSecret(rotated.secret);
    },
    onSuccess: () => setRotating(null),
  });
  const toggle = useMutation({
    mutationFn: (v: { id: string; is_active: boolean }) =>
      unwrap(api.PATCH("/api/v1/triggers/{trigger_id}", { params: { path: { trigger_id: v.id } }, body: { is_active: v.is_active } })),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/triggers/{trigger_id}", { params: { path: { trigger_id: id } } })),
    onSuccess: refresh,
  });

  return (
    <Stack>
      <ErrorAlert error={list.error ?? toggle.error ?? remove.error ?? rotate.error} />
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Un sistema externo lanza este flujo con un POST firmado (HMAC) a la URL del disparador. Los reenvíos del mismo evento se deduplican.
        </Text>
        <Button onClick={() => setCreating(true)}>Nuevo disparador</Button>
      </Group>
      {list.data?.length === 0 && <Text c="dimmed">Este flujo no tiene disparadores.</Text>}
      {!!list.data?.length && (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Nombre</Table.Th>
              <Table.Th>URL</Table.Th>
              <Table.Th>Límite</Table.Th>
              <Table.Th>Última recepción</Table.Th>
              <Table.Th>Activo</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {list.data.map((t) => {
              const url = window.location.origin + t.url_path;
              return (
                <Table.Tr key={t.id}>
                  <Table.Td>{t.name}</Table.Td>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Code style={{ overflowWrap: "anywhere" }}>{url}</Code>
                      <CopyButton value={url}>
                        {({ copied, copy }) => (
                          <Button size="compact-xs" variant="subtle" onClick={copy} aria-label={`Copiar URL de ${t.name}`}>
                            {copied ? "Copiada" : "Copiar"}
                          </Button>
                        )}
                      </CopyButton>
                    </Group>
                  </Table.Td>
                  <Table.Td>{t.rate_limit_per_minute}/min</Table.Td>
                  <Table.Td>{formatDate(t.last_received_at)}</Table.Td>
                  <Table.Td>
                    <Switch
                      aria-label={`Activar ${t.name}`}
                      checked={t.is_active}
                      disabled={toggle.isPending}
                      onChange={(e) => toggle.mutate({ id: t.id, is_active: e.currentTarget.checked })}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap" justify="flex-end">
                      <Button variant="subtle" size="xs" aria-label={`Rotar secreto de ${t.name}`} onClick={() => setRotating(t)}>
                        <IconRefresh size={16} />
                      </Button>
                      <Button
                        variant="subtle"
                        color="red"
                        size="xs"
                        aria-label={`Eliminar ${t.name}`}
                        loading={remove.isPending && remove.variables === t.id}
                        onClick={() => remove.mutate(t.id)}
                      >
                        <IconTrash size={16} />
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}

      <Modal opened={creating} onClose={() => setCreating(false)} title="Nuevo disparador" centered>
        <Stack>
          <TextInput label="Nombre" required maxLength={100} value={name} onChange={(e) => setName(e.currentTarget.value)} />
          <NumberInput
            label="Límite por minuto"
            description="Peticiones aceptadas por minuto antes de responder 429."
            min={1}
            max={6000}
            allowDecimal={false}
            value={limit}
            onChange={(v) => setLimit(typeof v === "number" ? v : 60)}
          />
          <ErrorAlert error={create.error} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setCreating(false)}>
              Cancelar
            </Button>
            <Button disabled={!name.trim()} loading={create.isPending} onClick={() => create.mutate()}>
              Crear
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={rotating !== null} onClose={() => setRotating(null)} title="Rotar secreto" centered>
        <Stack>
          <Text size="sm">
            El secreto actual de <strong>{rotating?.name}</strong> dejará de valer de inmediato: las peticiones firmadas con él recibirán 401 hasta que el sistema externo use el nuevo.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRotating(null)}>
              Cancelar
            </Button>
            <Button color="orange" loading={rotate.isPending} onClick={() => rotating && rotate.mutate(rotating.id)}>
              Rotar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <SecretOnce title="Secreto del disparador" secret={secret} onClose={() => setSecret(null)} />
    </Stack>
  );
}
