import { Alert, Button, Code, CopyButton, Group, Modal, Stack } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";

/**
 * Muestra un secreto recién creado o rotado. La API solo lo devuelve en esa respuesta, así
 * que quien lo abre debe guardarlo en estado local y soltarlo en `onClose`.
 */
export function SecretOnce({ title, secret, onClose }: { title: string; secret: string | null; onClose: () => void }) {
  return (
    // Sin cierre por clic fuera ni Escape: perder el secreto obliga a rotarlo.
    <Modal opened={secret !== null} onClose={onClose} title={title} centered closeOnClickOutside={false} closeOnEscape={false} withCloseButton={false}>
      <Stack>
        <Alert color="yellow" icon={<IconAlertTriangle size={18} />}>
          Copia este secreto ahora. No se volverá a mostrar.
        </Alert>
        <Code block style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
          {secret}
        </Code>
        <Group justify="flex-end">
          <CopyButton value={secret ?? ""}>
            {({ copied, copy }) => (
              <Button variant="default" onClick={copy}>
                {copied ? "Copiado" : "Copiar"}
              </Button>
            )}
          </CopyButton>
          <Button onClick={onClose}>Ya lo copié</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
