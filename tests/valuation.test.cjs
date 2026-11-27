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

test('as-of review checks manual and indexed disclosures against the same inclusive cutoff', () => {
  const later=runInNewContext(source+'\nlaterDisclosures');
  assert.deepEqual(Array.from(later({asOf:'2024-06-03'})),[]);
  const issues=later({asOf:'2024-06-03',
    deals:[{name:'On date',disclosed:'2024-06-03'},{name:'Later deal',disclosed:'2024-06-04'}],
    disclosures:[{field:'Earlier terms',source_date:'2024-05-02'},{field:'Payment confirmed',source_date:'2026-04-30'}]});
  assert.equal(issues.length,2);
  assert.match(issues[0],/Later deal.*2024-06-04/);
  assert.match(issues[1],/Payment confirmed.*2026-04-30/);
});

test('unchanged cash flows distinguish scenario-only disclosures by archive identity', () => {
  const differences=runInNewContext(source+'\nscenarioDifferences');
  const cases=[['shared','old','old'],['new','shared']].map(disclosureIds=>({
    amount:10,year:0,probability:100,disclosureIds
  }));
  const outputs=cases.map(c=>price([c],0));
  const disclosures=['old','new'].map(id=>({id,field:'付款披露',source_date:'2024-06-03'}));
  const [row]=differences({flows:[{cases}],outputs,disclosures});
  assert.equal(row.delta,0);
  assert.deepEqual(Array.from(row.evidenceChanges),[
    '仅基准引用：付款披露 · 2024-06-03 · old','仅替代引用：付款披露 · 2024-06-03 · new'
  ]);
});
