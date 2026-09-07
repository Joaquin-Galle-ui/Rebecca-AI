const tracking = $('If').item.json;
const teamId = Number(tracking.team_id);
const equipoSeguido = tracking.equipo || 'el equipo solicitado';
const staticData = $getWorkflowStaticData('global');
const payload = $input.first().json;
// A budget/cooldown/error result must never reach the paid language model.
if (payload.status === 'error' || payload.errors || payload.cache) return [];
const responseArray = Array.isArray(payload.response) ? payload.response : [];

const fixture = responseArray.find(f =>
  Number(f.teams.home.id) === teamId || Number(f.teams.away.id) === teamId
);
if (!fixture) return [];

const fixtureId = String(fixture.fixture.id);
const events = fixture.events || [];
const status = fixture.fixture.status.short;
const elapsed = Number(fixture.fixture.status.elapsed || 0);
const extra = Number(fixture.fixture.status.extra || 0);
const minuteLabel = extra > 0 ? `${elapsed}+${extra}` : String(elapsed);
const home = fixture.teams.home.name;
const away = fixture.teams.away.name;
const goalsHome = fixture.goals.home ?? 0;
const goalsAway = fixture.goals.away ?? 0;

const base = (tipo, detalle, minuto = minuteLabel) => ({
  json: {
    fixture_id: fixtureId,
    equipo_seguido: equipoSeguido,
    equipo_local: home,
    equipo_visitante: away,
    goles_local: goalsHome,
    goles_visitante: goalsAway,
    minuto,
    estado: status,
    tipo_evento: tipo,
    detalle_masticado: detalle,
  },
});

const eventKey = (event, index) => [
  fixtureId,
  event.time?.elapsed || 0,
  event.time?.extra || 0,
  event.type || '',
  event.detail || '',
  event.team?.id || 0,
  event.player?.id || event.player?.name || 0,
  event.assist?.id || event.assist?.name || 0,
  index,
].join('_');

const explainEvent = event => {
  const minute = event.time?.extra
    ? `${event.time.elapsed}+${event.time.extra}`
    : String(event.time?.elapsed || elapsed);
  const player = event.player?.name || 'jugador desconocido';
  const secondPlayer = event.assist?.name || '';
  const team = event.team?.name || 'un equipo';
  if (event.type === 'subst') {
    return { minute, text: `[Minuto ${minute}] Cambio en ${team}: entra ${secondPlayer || 'un jugador'} y sale ${player}.` };
  }
  if (event.type === 'Goal') {
    const assist = secondPlayer ? `, con asistencia de ${secondPlayer}` : '';
    return { minute, text: `[Minuto ${minute}] ¡GOL de ${team}! Anotó ${player}${assist}.` };
  }
  if (event.type === 'Card') {
    return { minute, text: `[Minuto ${minute}] ${event.detail || 'Tarjeta'} para ${player} (${team}).` };
  }
  if (event.type === 'Var') {
    return { minute, text: `[Minuto ${minute}] Revisión VAR en ${team}: ${event.detail || 'decisión arbitral'}.` };
  }
  return { minute, text: `[Minuto ${minute}] Evento de ${team}: ${event.detail || event.type} (${player}).` };
};

const currentKeys = events.map(eventKey);
let notifications = [];

if (staticData.bocaLiveFixtureId !== fixtureId) {
  staticData.bocaLiveFixtureId = fixtureId;
  staticData.bocaLiveSeenEvents = currentKeys;
  staticData.bocaLiveStatus = status;
  staticData.bocaLiveHeartbeatMinute = elapsed;
  notifications.push(base(
    'Inicio',
    `Seguimiento activado para ${equipoSeguido}. Así está ahora: ${home} ${goalsHome} - ${goalsAway} ${away}, minuto ${minuteLabel}.`,
  ));
  return notifications;
}

const statusMessages = {
  '1H': '¡Arrancó el primer tiempo!',
  'HT': 'Terminó el primer tiempo; estamos en el entretiempo.',
  '2H': '¡Arrancó el segundo tiempo!',
  'ET': '¡Arrancó el tiempo suplementario!',
  'BT': 'Pausa del tiempo suplementario.',
  'P': 'Comenzó la tanda de penales.',
  'PEN': `Final después de los penales: ${home} ${goalsHome} - ${goalsAway} ${away}.`,
  'FT': `Final del partido: ${home} ${goalsHome} - ${goalsAway} ${away}.`,
  'AET': `Final después del suplementario: ${home} ${goalsHome} - ${goalsAway} ${away}.`,
};

if (staticData.bocaLiveStatus !== status) {
  staticData.bocaLiveStatus = status;
  staticData.bocaLiveHeartbeatMinute = elapsed;
  if (statusMessages[status]) {
    notifications.push(base('Estado', statusMessages[status]));
  }
}

const seen = new Set(staticData.bocaLiveSeenEvents || []);
events.forEach((event, index) => {
  const key = eventKey(event, index);
  if (seen.has(key)) return;
  seen.add(key);
  const explained = explainEvent(event);
  notifications.push(base(event.type || 'Evento', explained.text, explained.minute));
});
staticData.bocaLiveSeenEvents = Array.from(seen).slice(-250);

const liveStatuses = new Set(['1H', '2H', 'ET', 'BT', 'P']);
const lastHeartbeat = Number(staticData.bocaLiveHeartbeatMinute ?? elapsed);
if (
  notifications.length === 0
  && liveStatuses.has(status)
  && elapsed > 0
  && elapsed - lastHeartbeat >= 5
) {
  staticData.bocaLiveHeartbeatMinute = elapsed;
  notifications.push(base(
    'Resumen periódico',
    `Parte del minuto ${minuteLabel}: ${home} ${goalsHome} - ${goalsAway} ${away}. No hubo otro evento decisivo desde el último aviso.`,
  ));
}

// Several events in one poll need one summary, not one paid AI call per event.
if (notifications.length <= 1) return notifications;
return [{ json: {
  ...notifications[notifications.length - 1].json,
  tipo_evento: 'Novedades del partido',
  detalle_masticado: notifications.map(item => item.json.detalle_masticado).join('\n'),
} }];
