-- Planes de las consultas críticas (ver docs/performance.md)
\pset pager off
\echo '### Q1 barrido: ejecuciones vencidas'
EXPLAIN (ANALYZE, COSTS OFF, TIMING ON, SUMMARY ON) SELECT id FROM workflow_runs WHERE status IN ('pending','running','needs_review') AND deadline_at <= now() LIMIT 200;
\echo '### Q2 barrido: pasos con lease vencido'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT s.run_id, s.id FROM step_runs s JOIN workflow_runs r ON r.id = s.run_id WHERE s.status = 'running' AND s.lease_expires_at <= now() AND r.status = 'running' LIMIT 200;
\echo '### Q3 barrido: pasos listos para encolar'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT s.id FROM step_runs s JOIN workflow_runs r ON r.id = s.run_id WHERE s.status IN ('pending','retry_scheduled') AND (s.next_attempt_at IS NULL OR s.next_attempt_at <= now()) AND (s.last_dispatched_at IS NULL OR s.last_dispatched_at <= now() - interval '60 seconds') AND r.status IN ('pending','running') ORDER BY s.next_attempt_at NULLS FIRST LIMIT 200;
\echo '### Q4 historial global (página 1)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT r.*, v.version FROM workflow_runs r JOIN workflow_versions v ON v.id = r.version_id ORDER BY r.created_at DESC, r.id LIMIT 20;
\echo '### Q5 historial de un flujo'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT r.*, v.version FROM workflow_runs r JOIN workflow_versions v ON v.id = r.version_id WHERE r.workflow_id = '00000000-0000-4000-9000-000000000007' ORDER BY r.created_at DESC, r.id LIMIT 20;
\echo '### Q6 COUNT del historial global'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT count(*) FROM workflow_runs;
\echo '### Q7 métricas: percentiles 24h'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT count(*), percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at)) FROM workflow_runs WHERE finished_at >= now() - interval '24 hours' AND started_at IS NOT NULL AND status IN ('succeeded','failed','timed_out','cancelled');
\echo '### Q8c intentos por tipo 24h (columna desnormalizada, sin JOIN)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT step_type, count(*), count(*) FILTER (WHERE outcome='failed'), percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) FROM step_attempts WHERE started_at >= now() - interval '24 hours' GROUP BY step_type;
\echo '### Q9c 30 días (columna desnormalizada, sin JOIN)'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT step_type, count(*), count(*) FILTER (WHERE outcome='failed'), percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) FROM step_attempts WHERE started_at >= now() - interval '720 hours' GROUP BY step_type;
\echo '### Q10 auditoría por prefijo'
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON) SELECT a.*, u.email FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id WHERE a.action LIKE 'user.%' ORDER BY a.id DESC LIMIT 51;
