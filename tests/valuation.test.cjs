const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');

const source = readFileSync(join(__dirname, '../web/valuation.js'), 'utf8');
const price = runInNewContext(source + '\npriceScenario');

test('certain upfront and risk-adjusted later proceeds are additive', () => {
  const result = price([
    {year:0, amount:-10, probability:100},
    {year:1, amount:110, probability:50},
    {year:2, amount:-8, probability:0},
  ], 10);
  assert.ok(Math.abs(result.total - 40) < 1e-9);
  assert.equal(result.presentValues[0], -10);
});

test('zero discount uses the dated cash flows without extra discounting', () => {
  assert.equal(price([{year:8, amount:-20, probability:25}], 0).total, -5);
});

test('invalid probabilities, years, discount or non-finite amounts fail', () => {
  const flow = {year:1, amount:100, probability:30};
  for (const change of [{year:-1}, {year:1.5}, {probability:101}, {amount:Infinity}]) {
    assert.throws(() => price([{...flow,...change}], 0));
  }
  assert.throws(() => price([flow], NaN));
});

test('opposing scenario deltas reconcile even when the total change is zero', () => {
  const differences = runInNewContext(source + '\nscenarioDifferences');
  const flows = [[-10,-20],[0,10],[5,5]].map(pair=>({cases:pair.map(amount=>({amount,year:0,probability:100}))}));
  const outputs = [0,1].map(j=>price(flows.map(f=>f.cases[j]),0));
  const rows = differences({flows,outputs});
  assert.deepEqual(Array.from(rows,r=>r.delta), [-10,10,0]);
  assert.equal(rows.reduce((sum,row)=>sum+row.delta,0),outputs[1].total-outputs[0].total);
  assert.equal(rows[0].assumptions[0],'金额 -10 → -20');
  assert.equal(rows[2].assumptions.length,0);
});
