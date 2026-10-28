import { Alert, Button, Group, Modal, PasswordInput, Select, Stack, Table, TagsInput, Text, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap, type Schemas } from "../../api/client";
import { RequirePermission } from "../../auth/RequirePermission";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PageHeader } from "../../components/PageHeader";
import { formatDate } from "../../lib/format";

type CredentialType = Schemas["CredentialType"];

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

function CreateCredential({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [type, setType] = useState<CredentialType>("bearer_token");
  const [hosts, setHosts] = useState<string[]>([]);
  const [secret, setSecret] = useState<Record<string, string>>({});
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/credentials", { body: { name, type, allowed_hosts: hosts, secret } })),
    onSuccess: async () => {
      // El secreto se descarta del estado del navegador en cuanto se guarda.
      setSecret({});
      await queryClient.invalidateQueries({ queryKey: ["credentials"] });
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
        {SECRET_FIELDS[type].map((field) =>
          field.secret ? (
            <PasswordInput key={field.key} label={field.label} required autoComplete="off" value={secret[field.key] ?? ""} onChange={(e) => setSecret({ ...secret, [field.key]: e.currentTarget.value })} />
          ) : (
            <TextInput key={field.key} label={field.label} required value={secret[field.key] ?? ""} onChange={(e) => setSecret({ ...secret, [field.key]: e.currentTarget.value })} />
          ),
        )}
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

export function CredentialsPage() {
  const [opened, { open, close }] = useDisclosure(false);
  const list = useQuery({ queryKey: ["credentials"], queryFn: () => unwrap(api.GET("/api/v1/credentials")) });
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
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {list.data?.map((c) => (
            <Table.Tr key={c.id}>
              <Table.Td ff="monospace">{c.name}</Table.Td>
              <Table.Td>{c.type}</Table.Td>
              <Table.Td>{c.allowed_hosts.join(", ")}</Table.Td>
              <Table.Td ff="monospace">{c.hint}</Table.Td>
              <Table.Td>{formatDate(c.last_used_at)}</Table.Td>
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
    </RequirePermission>
  );
}
