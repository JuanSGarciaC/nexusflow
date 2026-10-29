from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexusflow.schedules.cron import InvalidScheduleError, next_fire
from nexusflow.schedules.schemas import ScheduleCreate


def utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def test_next_fire_is_strictly_after_the_reference() -> None:
    # Justo en un disparo: el siguiente es el del intervalo posterior, no el mismo instante.
    assert next_fire("*/15 * * * *", "UTC", utc(2026, 1, 1, 10, 15)) == utc(2026, 1, 1, 10, 30)
    assert next_fire("*/15 * * * *", "UTC", utc(2026, 1, 1, 10, 14, 59)) == utc(2026, 1, 1, 10, 15)


def test_weekday_expression_skips_the_weekend() -> None:
    # Viernes 2026-01-02 a las 10:00 → el siguiente 09:00 de lunes a viernes es el lunes 5.
    assert next_fire("0 9 * * 1-5", "UTC", utc(2026, 1, 2, 10, 0)) == utc(2026, 1, 5, 9, 0)


def test_expression_is_evaluated_in_its_timezone() -> None:
    # 09:00 en Madrid (CET, UTC+1) en invierno = 08:00 UTC; en verano (CEST) = 07:00 UTC.
    assert next_fire("0 9 * * *", "Europe/Madrid", utc(2026, 1, 10, 0, 0)) == utc(2026, 1, 10, 8)
    assert next_fire("0 9 * * *", "Europe/Madrid", utc(2026, 7, 10, 0, 0)) == utc(2026, 7, 10, 7)


def test_daylight_saving_gap_moves_the_fire_to_the_next_valid_time() -> None:
    # El 29-03-2026 en Madrid el reloj salta de 02:00 a 03:00: 02:30 no existe ese día,
    # así que se dispara a las 03:00 locales (01:00 UTC) en vez de saltarse el día.
    first = next_fire("30 2 * * *", "Europe/Madrid", utc(2026, 3, 28, 12, 0))
    assert first == utc(2026, 3, 29, 1, 0)
    # Al día siguiente vuelve a su hora normal (02:30 CEST = 00:30 UTC).
    assert next_fire("30 2 * * *", "Europe/Madrid", first) == utc(2026, 3, 30, 0, 30)


def test_daylight_saving_overlap_fires_once() -> None:
    # El 25-10-2026 las 02:30 ocurre dos veces en Madrid: solo se dispara la primera.
    first = next_fire("30 2 * * *", "Europe/Madrid", utc(2026, 10, 24, 12, 0))
    assert first == utc(2026, 10, 25, 0, 30)
    assert next_fire("30 2 * * *", "Europe/Madrid", first) == utc(2026, 10, 26, 1, 30)


@pytest.mark.parametrize(
    "expression",
    ["", "* * * *", "* * * * * *", "61 * * * *", "abc * * * *", "0 0 31 2 *", "x" * 200],
)
def test_invalid_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(InvalidScheduleError):
        next_fire(expression, "UTC", utc(2026, 1, 1))


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(InvalidScheduleError, match="Zona horaria"):
        next_fire("* * * * *", "Mars/Olympus_Mons", utc(2026, 1, 1))


def test_naive_reference_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="zona horaria"):
        next_fire("* * * * *", "UTC", datetime(2026, 1, 1))


def test_schema_validates_cron_and_timezone() -> None:
    assert ScheduleCreate(name="a", cron_expression="0 9 * * 1-5", timezone="Europe/Madrid")
    with pytest.raises(ValueError, match="cron"):
        ScheduleCreate(name="a", cron_expression="no es cron", timezone="UTC")
    with pytest.raises(ValueError, match="Zona horaria"):
        ScheduleCreate(name="a", cron_expression="* * * * *", timezone="Nowhere/City")
