import { Button, Drawer, Group, Pagination, Stack, Table, Text } from "@mantine/core";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { ErrorAlert } from "../../components/ErrorAlert";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate } from "../../lib/format";

const PAGE_SIZE = 20;

/** Historial de entregas de una suscripción. Una entrega agotada se puede reenviar. */
export function DeliveriesDrawer({ subscription, onClose }: { subscription: Schemas["SubscriptionView"] | null; onClose: () => void }) {
  return (
    <Drawer opened={subscription !== null} onClose={onClose} position="right" size="xl" title={subscription ? `Entregas de ${subscription.name}` : ""}>
      {/* La key reinicia la página al cambiar de suscripción. */}
      {subscription && <Deliveries key={subscription.id} subscriptionId={subscription.id} />}
    </Drawer>
  );
}

function Deliveries({ subscriptionId }: { subscriptionId: string }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const key = ["webhook-subscription", subscriptionId, "deliveries"];
  const list = useQuery({
    queryKey: [...key, page],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/webhook-subscriptions/{subscription_id}/deliveries", {
          params: { path: { subscription_id: subscriptionId }, query: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE } },
        }),
      ),
    placeholderData: keepPreviousData,
    // Las entregas avanzan solas (reintentos con backoff): se refresca mientras el cajón está abierto.
    refetchInterval: 5_000,
  });
  const redeliver = useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/webhook-deliveries/{delivery_id}/redeliver", { params: { path: { delivery_id: id } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });

  const pages = Math.ceil((list.data?.total ?? 0) / PAGE_SIZE);
  return (
    <Stack>
      <ErrorAlert error={list.error ?? redeliver.error} />
      {list.data?.total === 0 && <Text c="dimmed">Todavía no hay entregas.</Text>}
      {!!list.data?.items.length && (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Evento</Table.Th>
              <Table.Th>Estado</Table.Th>
              <Table.Th>Intentos</Table.Th>
              <Table.Th>Último resultado</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {list.data.items.map((d) => (
              <Table.Tr key={d.id}>
                <Table.Td>
                  {d.event_type}
                  <Text size="xs" c="dimmed">
                    {formatDate(d.created_at)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <StatusBadge status={d.status} />
                </Table.Td>
                <Table.Td>
                  {d.attempt_count} de {d.max_attempts}
                </Table.Td>
                <Table.Td>
                  {d.last_status_code ? `HTTP ${d.last_status_code}` : "—"}
                  {d.last_error && (
                    <Text size="xs" c="red" style={{ overflowWrap: "anywhere" }}>
                      {d.last_error}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Button
                    size="compact-xs"
                    variant="light"
                    disabled={d.status !== "dead"}
                    loading={redeliver.isPending && redeliver.variables === d.id}
                    onClick={() => redeliver.mutate(d.id)}
                  >
                    Reenviar
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {pages > 1 && (
        <Group justify="flex-end">
          <Pagination total={pages} value={page} onChange={setPage} size="sm" />
        </Group>
      )}
    </Stack>
  );
}
