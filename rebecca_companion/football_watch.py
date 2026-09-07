"""Bounded football tracking. All API-Football traffic must pass this gate.

One Core process owns these files. The lock serializes its callers; atomic writes
make restarts safe. Counters are charged *before* HTTP, including failed requests.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"}
TERMINAL_STATUSES = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}


class FootballUnavailable(Exception):
    """Safe, credential-free failure to show to users."""


class FootballWatch:
    def __init__(self, root, api_key, *, clock=time.time, get=requests.get,
                 daily_limit=60, poll_seconds=120, session_seconds=3 * 3600):
        self.root = Path(root)
        self.state_path = self.root / "partido_estado.json"
        self.budget_path = self.root / "user_state" / "football_budget.json"
        self.api_key = api_key
        self.clock = clock
        self.get = get
        self.daily_limit = max(1, int(daily_limit))
        self.poll_seconds = max(120, int(poll_seconds))
        self.session_seconds = min(3 * 3600, max(60, int(session_seconds)))
        self.lock = threading.RLock()

    @staticmethod
    def _read(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {"corrupto": True}
        except FileNotFoundError:
            return {}
        except (ValueError, OSError):
            return {"corrupto": True}

    @staticmethod
    def _write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)

    def _budget(self):
        budget = self._read(self.budget_path)
        if budget.get("corrupto"):
            raise FootballUnavailable("Contador de fútbol ilegible; seguimiento pausado por seguridad.")
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        if budget.get("dia_utc") != day:
            budget = {"dia_utc": day, "usadas": 0}
        if not isinstance(budget.get("usadas"), int) or budget["usadas"] < 0:
            raise FootballUnavailable("Contador de fútbol inválido; no haré consultas.")
        return budget

    def _check_budget(self, budget):
        if not self.api_key:
            raise FootballUnavailable("Falta configurar la clave de la API de fútbol.")
        if budget.get("bloqueado") or budget["usadas"] >= self.daily_limit:
            raise FootballUnavailable("Fútbol pausado: cuota agotada o límite diario de seguridad alcanzado.")

    def _stop(self, state, reason):
        state.update(activo=False, motivo_fin=reason, terminado_en=self.clock())
        self._write(self.state_path, state)
        return state

    def stop(self, reason="manual"):
        with self.lock:
            return self._stop(self._read(self.state_path), reason)

    def _status(self):
        state = self._read(self.state_path)
        if state.get("activo"):
            expiry = state.get("vence_en")
            if not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
                return self._stop(state, "sesion_antigua_sin_vencimiento")
            if self.clock() >= expiry:
                return self._stop(state, "duracion_maxima")
            try:
                self._check_budget(self._budget())
            except FootballUnavailable:
                return self._stop(state, "cuota_o_configuracion")
        return state

    def status(self):
        with self.lock:
            state = self._status().copy()
            state.pop("ultimo_fixture", None)
            if state.get("pausa_hasta", 0) and self.clock() < state["pausa_hasta"]:
                state["activo"] = False
            return {"status": "success", "activo": False, **state}

    def start(self, team_id, name, fixture_id=None):
        with self.lock:
            self._check_budget(self._budget())
            state = self._status()
            # Repeated commands must not extend a running session indefinitely.
            if state.get("activo") and state.get("team_id") == int(team_id):
                return state
            now = self.clock()
            state = {"activo": True, "team_id": int(team_id), "equipo": name,
                     "iniciado_en": now, "vence_en": now + self.session_seconds,
                     "proxima_consulta": 0, "sin_partido": 0, "errores_seguidos": 0}
            if fixture_id:
                state["fixture_id"] = int(fixture_id)
            self._write(self.state_path, state)
            return state

    def pause(self):
        with self.lock:
            state = self._status()
            if not state.get("activo"):
                raise FootballUnavailable("No hay un partido activo para pausar.")
            state["pausa_hasta"] = self.clock() + 900
            self._write(self.state_path, state)

    def request(self, endpoint, params):
        """Shared persistent budget for live, daily fixtures and team lookup."""
        if endpoint not in {"teams", "fixtures"}:
            raise ValueError("Unsupported football endpoint")
        with self.lock:
            budget = self._budget()
            self._check_budget(budget)
            if self.clock() < budget.get("reintentar_en", 0):
                raise FootballUnavailable("La API de fútbol está en pausa por un error reciente.")
            budget["usadas"] += 1
            self._write(self.budget_path, budget)
            try:
                response = self.get("https://v3.football.api-sports.io/" + endpoint,
                                    headers={"x-apisports-key": self.api_key},
                                    params=params, timeout=10)
                if response.status_code in {401, 403, 429}:
                    budget["bloqueado"] = "cuota_o_credencial"
                    raise FootballUnavailable("La API de fútbol rechazó la cuota o credencial; queda pausada hoy.")
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("Invalid API response")
                errors = body.get("errors")
                if errors:
                    # API-Football also returns quota errors with HTTP 200.
                    budget["bloqueado"] = "error_proveedor"
                    raise FootballUnavailable("La API de fútbol informó un error; queda pausada hoy para proteger la cuota.")
                if not isinstance(body.get("response"), list):
                    raise ValueError("Invalid fixture list")
                budget["errores_seguidos"] = 0
                budget.pop("reintentar_en", None)
                return body["response"]
            except FootballUnavailable:
                raise
            except (requests.RequestException, ValueError, TypeError):
                budget["errores_seguidos"] = budget.get("errores_seguidos", 0) + 1
                budget["reintentar_en"] = self.clock() + 300
                if budget["errores_seguidos"] >= 3:
                    budget["bloqueado"] = "errores_repetidos"
                raise FootballUnavailable("La API de fútbol no respondió bien; pauso las consultas cinco minutos.") from None
            finally:
                self._write(self.budget_path, budget)

    @staticmethod
    def _matches(fixture, team_id):
        teams = fixture.get("teams") or {}
        return any((teams.get(side) or {}).get("id") == team_id for side in ("home", "away"))

    def poll(self, *, cached=False):
        with self.lock:
            state = self._status()
            now = self.clock()
            if not state.get("activo"):
                return {"response": [], "activo": False, "motivo": state.get("motivo_fin", "inactivo")}
            if now < max(state.get("proxima_consulta", 0), state.get("pausa_hasta") or 0):
                fixture = state.get("ultimo_fixture")
                return {"response": [fixture] if cached and fixture else [], "cache": True,
                        "consultado_en": state.get("consultado_en")}
            state["proxima_consulta"] = now + self.poll_seconds
            self._write(self.state_path, state)
            params = {"id": state["fixture_id"]} if state.get("fixture_id") else {"team": state["team_id"], "live": "all"}
            try:
                fixtures = self.request("fixtures", params)
            except FootballUnavailable as exc:
                state["errores_seguidos"] = state.get("errores_seguidos", 0) + 1
                state["proxima_consulta"] = now + 300
                budget = self._read(self.budget_path)
                if budget.get("bloqueado") or budget.get("usadas", 0) >= self.daily_limit or state["errores_seguidos"] >= 3:
                    self._stop(state, "cuota_o_errores")
                else:
                    self._write(self.state_path, state)
                return {"response": [], "status": "error", "message": str(exc)}
            state["errores_seguidos"] = 0
            fixture = next((f for f in fixtures if isinstance(f, dict) and self._matches(f, state["team_id"]) and
                            (not state.get("fixture_id") or (f.get("fixture") or {}).get("id") == state["fixture_id"])), None)
            info = (fixture or {}).get("fixture") or {}
            status = (info.get("status") or {}).get("short")
            if not fixture or status not in LIVE_STATUSES | TERMINAL_STATUSES:
                state["sin_partido"] += 1
                state["proxima_consulta"] = now + 300
                if state["sin_partido"] >= 3:
                    self._stop(state, "sin_partido_en_vivo")
                else:
                    self._write(self.state_path, state)
                return {"response": [], "motivo": "sin_partido_en_vivo"}
            state.update(fixture_id=info["id"], sin_partido=0, ultimo_fixture=fixture, consultado_en=now)
            if status in TERMINAL_STATUSES:
                self._stop(state, "partido_finalizado")
            else:
                self._write(self.state_path, state)
            return {"response": [fixture], "activo": state["activo"], "consultado_en": now}

    def daily_fixtures(self, team_id):
        """Daily discovery is informational, cached, and never arms the tracker."""
        with self.lock:
            budget = self._budget()
            day = datetime.fromtimestamp(self.clock()).date().isoformat()
            key = f"{day}:{team_id}"
            cached = budget.get("agenda", {}).get(key)
            if cached is not None:
                return cached
            fixtures = self.request("fixtures", {"team": team_id, "date": day})
            budget = self._budget()
            budget.setdefault("agenda", {})[key] = fixtures
            self._write(self.budget_path, budget)
            return fixtures

    def summary(self):
        # Reading the latest observation never steals events from the workflow
        # or makes an extra paid call. Always label its age, including halftime.
        with self.lock:
            state = self._status()
            fixture = state.get("ultimo_fixture")
            if not fixture:
                return {"status": "success", "message": "Todavía no tengo un marcador comprobado. Si activaste el seguimiento, esperá su próxima consulta; si no, pedime seguir un equipo."}
            age = max(0, int(self.clock() - state["consultado_en"]))
            home, away = fixture["teams"]["home"]["name"], fixture["teams"]["away"]["name"]
            goals = fixture.get("goals") or {}
            status = fixture["fixture"]["status"]
            return {"status": "success", "message": f"Último dato verificado, hace {age} segundos: {home} {goals.get('home', '?')} - {goals.get('away', '?')} {away}. Minuto {status.get('elapsed')}, estado {status.get('short')}. Seguimiento {'activo' if state.get('activo') else 'apagado'}. No es una consulta nueva."}
