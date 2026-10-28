import { PageHeader } from "../../components/PageHeader";
import { RunTable } from "./RunTable";

export function RunListPage() {
  return (
    <>
      <PageHeader title="Ejecuciones" description="Historial de todas las ejecuciones, con su estado y errores." />
      <RunTable />
    </>
  );
}
