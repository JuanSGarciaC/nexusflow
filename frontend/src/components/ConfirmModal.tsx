import { Button, Group, Modal, Stack } from "@mantine/core";
import type { ReactNode } from "react";

import { ErrorAlert } from "./ErrorAlert";

interface ConfirmModalProps {
  opened: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  color?: string;
  loading: boolean;
  error: unknown;
  onConfirm: () => void;
  onClose: () => void;
}

/** Confirmación de una acción que no se puede deshacer. El error se muestra dentro del diálogo. */
export function ConfirmModal({ opened, title, children, confirmLabel, color = "red", loading, error, onConfirm, onClose }: ConfirmModalProps) {
  return (
    // Mientras la petición está en vuelo no se puede cerrar: la respuesta puede traer un
    // secreto que solo se entrega una vez.
    <Modal opened={opened} onClose={() => !loading && onClose()} title={title} centered>
      <Stack>
        {children}
        <ErrorAlert error={error} />
        <Group justify="flex-end">
          <Button variant="default" disabled={loading} onClick={onClose}>
            Cancelar
          </Button>
          <Button color={color} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
