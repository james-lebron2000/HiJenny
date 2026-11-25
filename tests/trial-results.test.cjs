const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname,'../web/trial-results.js'),'utf8');
const {measureRows,safetySummary,registryValue} = runInNewContext(source+'\n({measureRows,safetySummary,registryValue})');

test('keep outcome-specific denominator scopes and original numeric precision',()=>{
  const denom = value=>[{units:'Participants',counts:[{groupId:'OG01',value}]}];
  const m={groups:[{id:'OG01',title:'Arm'}],unitOfMeasure:'%',denoms:denom('23'),
    classes:[{title:'Subset',denoms:denom('11'),categories:[{title:'Day 8',denoms:denom('8'),
      measurements:[{groupId:'OG01',value:'0.00',lowerLimit:'0',upperLimit:'N/A'}]}]}]};
  const {rows,denoms}=measureRows(m);
  assert.equal(denoms.length,3);
  assert.equal(denoms[2][0],'Subset / Day 8');
  assert.equal(denoms[2][2],'8');
  assert.equal(rows[0][1],'OG01 · Arm');
  assert.equal(rows[0][2],'0.00');
  assert.equal(rows[0][5],'0');
  assert.equal(rows[0][6],'N/A');
});

test('unreported safety counts remain missing while reported zero is displayed',()=>{
  const rows=safetySummary({eventGroups:[{id:'EG0',title:'Arm',seriousNumAffected:0,seriousNumAtRisk:3}]});
  assert.equal(rows.length,1);
  assert.equal(registryValue(rows[0][2]),'0');
  assert.equal(registryValue(undefined),'未提供');
});
