import { Badge, Button, Code, Group, Modal, MultiSelect, Stack, Switch, Table, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconHistory, IconRefresh, IconSend, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, describeError, unwrap, type Schemas } from "../../api/client";
import { RequirePermission } from "../../auth/RequirePermission";
import { ErrorAlert } from "../../components/ErrorAlert";
import { GlowCard } from "../../components/GlowCard";
import { PageHeader } from "../../components/PageHeader";
import { SecretOnce } from "../../components/SecretOnce";
import { DeliveriesDrawer } from "./DeliveriesDrawer";

type Subscription = Schemas["SubscriptionView"];

// Eventos que emite el backend (WebhookEvent). "ping" no se suscribe: se envía a petición.
const EVENTS = [
  { value: "run.succeeded", label: "Ejecución completada" },
  { value: "run.failed", label: "Ejecución fallida" },
  { value: "run.needs_review", label: "Necesita revisión manual" },
  { value: "run.timed_out", label: "Tiempo agotado" },
  { value: "run.cancelled", label: "Ejecución cancelada" },
];
const KEY = ["webhook-subscriptions"];

function Subscriptions() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState(["run.succeeded", "run.failed"]);
  const [rotating, setRotating] = useState<Subscription | null>(null);
  const [viewing, setViewing] = useState<Subscription | null>(null);
  // El secreto vive solo aquí: las mutaciones no lo devuelven (ver TriggersTab).
  const [secret, setSecret] = useState<string | null>(null);

  const list = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET("/api/v1/webhook-subscriptions")) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: KEY });

  const create = useMutation({
    mutationFn: async () => {
      const created = await unwrap(
        api.POST("/api/v1/webhook-subscriptions", { body: { name: name.trim(), url: url.trim(), events, workflow_id: null } }),
      );
      setSecret(created.secret);
    },
    onSuccess: () => {
      setCreating(false);
      setName("");
      setUrl("");
      return refresh();
    },
  });
  const rotate = useMutation({
    mutationFn: async (id: string) => {
      const rotated = await unwrap(
        api.POST("/api/v1/webhook-subscriptions/{subscription_id}/rotate-secret", { params: { path: { subscription_id: id } } }),
      );
      setSecret(rotated.secret);
    },
    onSuccess: () => setRotating(null),
  });
  const toggle = useMutation({
    mutationFn: (v: { id: string; is_active: boolean }) =>
      unwrap(
        api.PATCH("/api/v1/webhook-subscriptions/{subscription_id}", {
          params: { path: { subscription_id: v.id } },
          body: { is_active: v.is_active },
        }),
      ),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/webhook-subscriptions/{subscription_id}", { params: { path: { subscription_id: id } } })),
    onSuccess: refresh,
  });
  const ping = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/v1/webhook-subscriptions/{subscription_id}/ping", { params: { path: { subscription_id: id } } })),
    onSuccess: () => notifications.show({ color: "green", message: "Ping encolado. Revisa las entregas para ver el resultado." }),
    onError: (error) => notifications.show({ color: "red", title: "No se pudo enviar el ping", message: describeError(error) }),
  });

  return (
    <>
      <PageHeader
        title="Webhooks"
        description="Avisos firmados a otros sistemas cuando una ejecución termina. Si el destino falla, se reintenta con espera creciente."
        actions={<Button onClick={() => setCreating(true)}>Nueva suscripción</Button>}
      />
      <ErrorAlert error={list.error ?? toggle.error ?? remove.error ?? rotate.error} />
      <GlowCard spin={false} p={0}>
        {list.data?.length === 0 && (
          <Text c="dimmed" p="lg">
            Aún no hay suscripciones. Crea una para avisar a otro sistema cuando termine una ejecución.
          </Text>
        )}
        {!!list.data?.length && (
          <Table verticalSpacing="sm" horizontalSpacing="md">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Nombre</Table.Th>
                <Table.Th>Destino</Table.Th>
                <Table.Th>Eventos</Table.Th>
                <Table.Th>Activa</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {list.data.map((s) => (
                <Table.Tr key={s.id}>
                  <Table.Td>{s.name}</Table.Td>
                  <Table.Td>
                    <Code style={{ overflowWrap: "anywhere" }}>{s.url}</Code>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      {s.events.map((e) => (
                        <Badge key={e} variant="default" size="sm">
                          {e}
                        </Badge>
                      ))}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Switch
                      aria-label={`Activar ${s.name}`}
                      checked={s.is_active}
                      disabled={toggle.isPending}
                      onChange={(e) => toggle.mutate({ id: s.id, is_active: e.currentTarget.checked })}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap" justify="flex-end">
                      <Button variant="subtle" size="xs" aria-label={`Entregas de ${s.name}`} onClick={() => setViewing(s)}>
                        <IconHistory size={16} />
                      </Button>
                      <Button
                        variant="subtle"
                        size="xs"
                        aria-label={`Enviar ping a ${s.name}`}
                        loading={ping.isPending && ping.variables === s.id}
                        onClick={() => ping.mutate(s.id)}
                      >
                        <IconSend size={16} />
                      </Button>
                      <Button variant="subtle" size="xs" aria-label={`Rotar secreto de ${s.name}`} onClick={() => setRotating(s)}>
                        <IconRefresh size={16} />
                      </Button>
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
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </GlowCard>

      <Modal opened={creating} onClose={() => setCreating(false)} title="Nueva suscripción" centered>
        <Stack>
          <TextInput label="Nombre" required maxLength={100} value={name} onChange={(e) => setName(e.currentTarget.value)} />
          <TextInput
            label="URL de destino"
            description="Debe ser https y pública: el motor bloquea direcciones internas."
            required
            maxLength={2048}
            value={url}
            onChange={(e) => setUrl(e.currentTarget.value)}
          />
          <MultiSelect label="Eventos" data={EVENTS} value={events} onChange={setEvents} maxValues={5} />
          <ErrorAlert error={create.error} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setCreating(false)}>
              Cancelar
            </Button>
            <Button disabled={!name.trim() || url.trim().length < 8 || events.length === 0} loading={create.isPending} onClick={() => create.mutate()}>
              Crear
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={rotating !== null} onClose={() => setRotating(null)} title="Rotar secreto" centered>
        <Stack>
          <Text size="sm">
            Las entregas a <strong>{rotating?.name}</strong> se firmarán con el secreto nuevo desde ahora. El destino debe actualizarlo o rechazará las firmas.
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

      <SecretOnce title="Secreto de la suscripción" secret={secret} onClose={() => setSecret(null)} />
      <DeliveriesDrawer subscription={viewing} onClose={() => setViewing(null)} />
    </>
  );
}

export function WebhooksPage() {
  return (
    <RequirePermission permission="webhooks:manage">
      <Subscriptions />
    </RequirePermission>
  );
}
