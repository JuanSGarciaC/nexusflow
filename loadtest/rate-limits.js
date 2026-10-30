// Los límites de uso medidos bajo carga (k6). Ver loadtest/README.md.
//
// Las pruebas de integración ya demuestran que el 429 llega. Esta comprueba lo que solo se ve
// con tráfico sostenido: que el límite se mantiene sin dejar pasar de más, que rechazar es
// barato (no degrada a la API), que Retry-After es utilizable y que la ventana se reabre.
//
// REQUISITO: la API con los límites POR DEFECTO (120 ejecuciones/min y 120 escrituras/min por
// usuario). No subas NEXUSFLOW_RUN_CREATE_RATE_LIMIT_PER_USER para esta prueba. Usa usuarios
// que no hayan hecho peticiones en el último minuto (ventana fija).
//
// Dos usuarios en paralelo, cada uno a 3 peticiones/s durante 75 s (225 intentos):
// - operador: crear ejecuciones (límite run_create_user, 120/min)
// - editor:   PATCH de un flujo (límite write_user, 120/min)
// Con ventana fija: 120 aceptadas en el primer minuto y las ~45 de los últimos 15 s, ya en
// la ventana siguiente, también aceptadas => entre 120 y 240 aceptadas, y 429 en medio.
import http from "k6/http";
import { fail } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const PASSWORD = __ENV.NEXUSFLOW_DEMO_PASSWORD;

const runsAccepted = new Counter("rl_runs_accepted");
const runsLimited = new Counter("rl_runs_limited");
const writesAccepted = new Counter("rl_writes_accepted");
const writesLimited = new Counter("rl_writes_limited");
const unexpected = new Counter("rl_unexpected_status");
const retryAfterValid = new Rate("rl_retry_after_valid");
const rejectedMs = new Trend("rl_rejected_ms", true);

const scenario = (exec) => ({
  executor: "constant-arrival-rate",
  rate: 3,
  timeUnit: "1s",
  duration: "75s",
  preAllocatedVUs: 10,
  maxVUs: 30,
  exec,
});

export const options = {
  scenarios: { runs: scenario("runsLimit"), writes: scenario("writesLimit") },
  thresholds: {
    // Nunca deja pasar más del doble (borde de la ventana fija) ni menos del límite.
    rl_runs_accepted: ["count>=120", "count<=240"],
    rl_writes_accepted: ["count>=110", "count<=240"], // setup ya gastó 2 escrituras del editor
    // El límite se aplica de verdad.
    rl_runs_limited: ["count>0"],
    rl_writes_limited: ["count>0"],
    // Solo 2xx del camino feliz y 429: ningún 5xx ni 4xx inesperado bajo presión.
    rl_unexpected_status: ["count==0"],
    // Retry-After siempre presente y dentro de la ventana (1-60 s).
    rl_retry_after_valid: ["rate==1"],
    // Rechazar es barato: no degrada a la API.
    rl_rejected_ms: ["p(95)<200"],
  },
};

function login(email) {
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (res.status !== 200) fail(`login de ${email} falló: ${res.status}`);
  return { Authorization: `Bearer ${res.json("access_token")}`, "Content-Type": "application/json" };
}

export function setup() {
  if (!PASSWORD) fail("Define NEXUSFLOW_DEMO_PASSWORD (la del seed-demo).");
  const editor = login("demo-editor@example.com");
  const operator = login("demo-operator@example.com");
  const definition = {
    inputs: [{ name: "amount", type: "number" }],
    start: "calc",
    steps: [{ id: "calc", type: "transform", config: { output: { total: "{{ input.amount }}" } } }],
  };
  const created = http.post(
    `${BASE_URL}/api/v1/workflows`,
    JSON.stringify({ name: `carga-limites-${Date.now()}`, definition }),
    { headers: editor },
  );
  if (created.status !== 201) fail(`no se pudo crear el flujo: ${created.status} ${created.body}`);
  const workflowId = created.json("id");
  const published = http.post(
    `${BASE_URL}/api/v1/workflows/${workflowId}/publish`,
    JSON.stringify({ expected_revision: 1 }),
    { headers: editor },
  );
  if (published.status !== 200 && published.status !== 201) fail(`no se pudo publicar: ${published.status}`);
  return { workflowId, editor, operator };
}

function classify(res, okStatus, accepted, limited) {
  if (res.status === okStatus) {
    accepted.add(1);
  } else if (res.status === 429) {
    limited.add(1);
    rejectedMs.add(res.timings.duration);
    const wait = Number(res.headers["Retry-After"]);
    retryAfterValid.add(Number.isInteger(wait) && wait >= 1 && wait <= 60);
  } else {
    unexpected.add(1);
    console.error(`estado inesperado ${res.status}: ${String(res.body).slice(0, 120)}`);
  }
}

export function runsLimit(data) {
  const res = http.post(
    `${BASE_URL}/api/v1/workflows/${data.workflowId}/runs`,
    JSON.stringify({ input: { amount: __ITER } }),
    { headers: data.operator, responseCallback: http.expectedStatuses(202, 429) },
  );
  classify(res, 202, runsAccepted, runsLimited);
}

export function writesLimit(data) {
  const res = http.patch(
    `${BASE_URL}/api/v1/workflows/${data.workflowId}`,
    JSON.stringify({ description: `k6 ${__ITER}` }),
    { headers: data.editor, responseCallback: http.expectedStatuses(200, 429) },
  );
  classify(res, 200, writesAccepted, writesLimited);
}
