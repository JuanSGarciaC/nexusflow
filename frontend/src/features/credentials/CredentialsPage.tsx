import { Alert, Button, Group, Modal, PasswordInput, Select, Stack, Table, TagsInput, Text, TextInput, Textarea } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconKey, IconPencil, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { RequirePermission } from "../../auth/RequirePermission";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PageHeader } from "../../components/PageHeader";
import { formatDate } from "../../lib/format";

type CredentialType = Schemas["CredentialType"];
type Credential = Schemas["CredentialView"];
type Secret = Record<string, string>;

const KEY = ["credentials"];
const SECRET_FIELDS: Record<CredentialType, { key: string; label: string; secret: boolean }[]> = {
  bearer_token: [{ key: "token", label: "Token", secret: true }],
  api_key_header: [
    { key: "header_name", label: "Cabecera", secret: false },
    { key: "value", label: "Valor", secret: true },
  ],
  basic_auth: [
    { key: "username", label: "Usuario", secret: false },
    { key: "password", label: "Contraseña", secret: true },
  ],
};

/** Campos del secreto según el tipo de credencial. Los usan el alta y el reemplazo. */
function SecretFields({ type, secret, onChange }: { type: CredentialType; secret: Secret; onChange: (secret: Secret) => void }) {
  return SECRET_FIELDS[type].map((field) =>
    field.secret ? (
      <PasswordInput key={field.key} label={field.label} required autoComplete="off" value={secret[field.key] ?? ""} onChange={(e) => onChange({ ...secret, [field.key]: e.currentTarget.value })} />
    ) : (
      <TextInput key={field.key} label={field.label} required value={secret[field.key] ?? ""} onChange={(e) => onChange({ ...secret, [field.key]: e.currentTarget.value })} />
    ),
  );
}

function CreateCredential({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [type, setType] = useState<CredentialType>("bearer_token");
  const [hosts, setHosts] = useState<string[]>([]);
  const [secret, setSecret] = useState<Secret>({});
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/credentials", { body: { name, type, allowed_hosts: hosts, secret } })),
    onSuccess: async () => {
      // El secreto se descarta del estado del navegador en cuanto se guarda.
      setSecret({});
      await queryClient.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Modal opened onClose={onClose} title="Nueva credencial" size="lg">
      <Stack>
        <TextInput label="Nombre" description="Se usa en los flujos: config.credential" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <Select
          label="Tipo"
          data={[
            { value: "bearer_token", label: "Bearer token" },
            { value: "api_key_header", label: "API key en cabecera" },
            { value: "basic_auth", label: "Basic auth" },
          ]}
          value={type}
          onChange={(v) => {
            if (!v) return;
            setType(v as CredentialType);
            setSecret({});
          }}
        />
        <TagsInput
          label="Hosts permitidos"
          description="La credencial solo se enviará a estos hosts (Enter para añadir)"
          required
          value={hosts}
          onChange={setHosts}
        />
        <SecretFields type={type} secret={secret} onChange={setSecret} />
        <Alert color="blue">El secreto se cifra en el servidor y nunca se vuelve a mostrar.</Alert>
        <ErrorAlert error={create.error} />
        <Group justify="flex-end">
          <Button onClick={() => create.mutate()} loading={create.isPending} disabled={!name || hosts.length === 0}>
            Guardar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function EditCredential({ credential, onClose }: { credential: Credential; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [description, setDescription] = useState(credential.description ?? "");
  const [hosts, setHosts] = useState(credential.allowed_hosts);
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/credentials/{credential_id}", {
          params: { path: { credential_id: credential.id } },
          body: { description: description || null, allowed_hosts: hosts },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Modal opened onClose={onClose} title={`Editar ${credential.name}`} size="lg">
      <Stack>
        <Textarea label="Descripción" maxLength={500} value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
        <TagsInput label="Hosts permitidos" description="La credencial solo se enviará a estos hosts (Enter para añadir)" required value={hosts} onChange={setHosts} />
        <Text size="xs" c="dimmed">
          El nombre y el tipo no se cambian: los flujos la referencian por nombre.
        </Text>
        <ErrorAlert error={save.error} />
        <Group justify="flex-end">
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={hosts.length === 0}>
            Guardar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function ReplaceSecret({ credential, onClose }: { credential: Credential; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [secret, setSecret] = useState<Secret>({});
  const replace = useMutation({
    mutationFn: () =>
      unwrap(api.PUT("/api/v1/credentials/{credential_id}/secret", { params: { path: { credential_id: credential.id } }, body: { secret } })),
    onSuccess: async () => {
      setSecret({});
      await queryClient.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  const complete = SECRET_FIELDS[credential.type].every((field) => secret[field.key]);
  return (
    <Modal opened onClose={onClose} title={`Reemplazar secreto de ${credential.name}`} size="lg">
      <Stack>
        <Alert color="yellow">Las ejecuciones que empiecen a partir de ahora usarán el secreto nuevo. El anterior no se puede recuperar.</Alert>
        <SecretFields type={credential.type} secret={secret} onChange={setSecret} />
        <ErrorAlert error={replace.error} />
        <Group justify="flex-end">
          <Button onClick={() => replace.mutate()} loading={replace.isPending} disabled={!complete}>
            Reemplazar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function DeleteCredential({ credential, onClose }: { credential: Credential; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [typed, setTyped] = useState("");
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/credentials/{credential_id}", { params: { path: { credential_id: credential.id } } })),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Modal opened onClose={onClose} title={`Eliminar ${credential.name}`}>
      <Stack>
        <Text size="sm">Los flujos que la usen fallarán en su siguiente ejecución. Esta acción no se puede deshacer.</Text>
        <TextInput label={`Escribe ${credential.name} para confirmar`} value={typed} onChange={(e) => setTyped(e.currentTarget.value)} />
        <ErrorAlert error={remove.error} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancelar
          </Button>
          <Button color="red" onClick={() => remove.mutate()} loading={remove.isPending} disabled={typed !== credential.name}>
            Eliminar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

type Action = { kind: "edit" | "secret" | "delete"; credential: Credential };

export function CredentialsPage() {
  const [opened, { open, close }] = useDisclosure(false);
  const [action, setAction] = useState<Action | null>(null);
  const list = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET("/api/v1/credentials")) });
  const done = () => setAction(null);
  return (
    <RequirePermission permission="credentials:manage">
      <PageHeader
        title="Credenciales"
        description="Secretos cifrados para llamar a APIs externas. Solo se muestran metadatos."
        actions={
          <Button leftSection={<IconPlus size={16} />} onClick={open}>
            Nueva credencial
          </Button>
        }
      />
      <ErrorAlert error={list.error} />
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Nombre</Table.Th>
            <Table.Th>Tipo</Table.Th>
            <Table.Th>Hosts</Table.Th>
            <Table.Th>Pista</Table.Th>
            <Table.Th>Último uso</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {list.data?.map((c) => (
            <Table.Tr key={c.id}>
              <Table.Td ff="monospace">
                {c.name}
                {c.description && (
                  <Text size="xs" c="dimmed" ff="text">
                    {c.description}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>{c.type}</Table.Td>
              <Table.Td>{c.allowed_hosts.join(", ")}</Table.Td>
              <Table.Td ff="monospace">{c.hint}</Table.Td>
              <Table.Td>{formatDate(c.last_used_at)}</Table.Td>
              <Table.Td>
                <Group gap={4} wrap="nowrap" justify="flex-end">
                  <Button variant="subtle" size="xs" aria-label={`Editar ${c.name}`} onClick={() => setAction({ kind: "edit", credential: c })}>
                    <IconPencil size={16} />
                  </Button>
                  <Button variant="subtle" size="xs" aria-label={`Reemplazar secreto de ${c.name}`} onClick={() => setAction({ kind: "secret", credential: c })}>
                    <IconKey size={16} />
                  </Button>
                  <Button variant="subtle" color="red" size="xs" aria-label={`Eliminar ${c.name}`} onClick={() => setAction({ kind: "delete", credential: c })}>
                    <IconTrash size={16} />
                  </Button>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {list.data?.length === 0 && (
        <Text c="dimmed" ta="center" py="xl">
          Aún no hay credenciales.
        </Text>
      )}
      {opened && <CreateCredential onClose={close} />}
      {action?.kind === "edit" && <EditCredential credential={action.credential} onClose={done} />}
      {action?.kind === "secret" && <ReplaceSecret credential={action.credential} onClose={done} />}
      {action?.kind === "delete" && <DeleteCredential credential={action.credential} onClose={done} />}
    </RequirePermission>
  );
}
