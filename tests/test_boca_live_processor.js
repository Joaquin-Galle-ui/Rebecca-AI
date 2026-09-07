const assert = require('node:assert/strict');
const fs = require('node:fs');

const source = fs.readFileSync('n8n-workflows/boca_live_processor.js', 'utf8');
const runNode = new Function('$', '$getWorkflowStaticData', '$input', source);
const state = {};
let fixture;

const run = () => runNode(
  name => {
    assert.equal(name, 'If');
    return { item: { json: { team_id: 435, equipo: 'River Plate' } } };
  },
  scope => {
    assert.equal(scope, 'global');
    return state;
  },
  { first: () => ({ json: { response: [fixture] } }) },
);

const makeFixture = (minute, events = [], status = '1H') => ({
  fixture: { id: 99, status: { short: status, elapsed: minute, extra: null } },
  teams: {
    home: { id: 435, name: 'River Plate' },
    away: { id: 10, name: 'Rival' },
  },
  goals: { home: 0, away: 0 },
  events,
});

fixture = makeFixture(1);
let output = run();
assert.equal(output.length, 1);
assert.equal(output[0].json.equipo_seguido, 'River Plate');
assert.equal(output[0].json.tipo_evento, 'Inicio');

fixture = makeFixture(4);
assert.equal(run().length, 0);

fixture = makeFixture(6);
output = run();
assert.equal(output.length, 1);
assert.equal(output[0].json.tipo_evento, 'Resumen periódico');

const goal = {
  time: { elapsed: 8, extra: null },
  team: { id: 435, name: 'River Plate' },
  player: { id: 7, name: 'Delantero' },
  assist: { id: 8, name: 'Asistente' },
  type: 'Goal',
  detail: 'Normal Goal',
};
const card = {
  time: { elapsed: 8, extra: null },
  team: { id: 10, name: 'Rival' },
  player: { id: 5, name: 'Defensor' },
  assist: {},
  type: 'Card',
  detail: 'Yellow Card',
};
fixture = makeFixture(8, [goal, card]);
output = run();
assert.equal(output.length, 1);
assert.match(output[0].json.detalle_masticado, /GOL/);
assert.match(output[0].json.detalle_masticado, /Yellow Card/);

assert.equal(run().length, 0);
fixture = makeFixture(120, [goal, card], 'PEN');
output = run();
assert.equal(output.length, 1);
assert.match(output[0].json.detalle_masticado, /Final después de los penales/);
assert.equal(run().length, 0);
for (const payload of [{status:'error'}, {errors:{requests:'quota'}}, {response:[],cache:true}, {response:null}]) {
  assert.deepEqual(runNode(() => ({item:{json:{team_id:435}}}), () => state,
    {first:() => ({json:payload})}), []);
}
console.log('Boca Juniors Live: seguimiento continuo verificado.');
